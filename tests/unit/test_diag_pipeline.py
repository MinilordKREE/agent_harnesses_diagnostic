"""Offline end-to-end: reference genuineness -> align -> signal -> cluster -> corrupt -> leakage
over hand-built run directories, plus the ``ahd diag`` parser and cost command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahd.cli import build_parser, main
from ahd.core.config import RunConfig, load_run_config
from ahd.core.context import create_run_context
from ahd.core.io import read_json
from ahd.core.manifest import read_manifest, write_manifest
from ahd.diagnosis import genuineness, leakage
from ahd.diagnosis.corrupt import ARM_CORRUPTION
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.pipeline import (
    align_failures,
    cluster_run,
    corrupt_run,
    leakage_run,
    load_clusters,
    per_failure_cost,
    signal_failures,
    verify_references,
)
from ahd.diagnosis.signal import load_prompts
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import SnapshotStore, snapshot_from_dir
from ahd.llm.fake import FakeProvider
from ahd.llm.types import ChatRequest
from ahd.runner.records import FailureRecord, ReferenceRecord, RolloutRecord
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import TaskSet
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import finish, sh, trajectory
from tests.evobench_fixtures import FAKE_REVISION

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
pytestmark = pytest.mark.skipif(
    not (SEED / "harness.py").is_file(), reason="submodule not checked out"
)
LIST: tuple[str, dict[str, Any]] = ("todo_list_tasks", {})
UPDATE: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "status": "completed"},
)
CLAW = "claw-T000_synthetic"
BC = "bc-en-0001"


@pytest.fixture
def config(pricing_path: Path, git_repo: Path) -> RunConfig:
    base = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    return base.model_copy(
        update={
            "pricing_path": pricing_path,
            "runs_root": git_repo / "runs",
            "require_clean_tree": False,
        }
    )


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


def _write_rollout(
    run_dir: Path,
    task_id: str,
    replicate: str,
    traj: dict[str, Any],
    *,
    attempt: int = 1,
    exit_reason: str = "finished",
    passed: bool = False,
) -> Path:
    suffix = "" if attempt == 1 else f"/attempt_{attempt}"
    directory = run_dir / "rollouts" / task_id / f"{replicate}{suffix}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "trajectory.json").write_text(json.dumps(traj), encoding="utf-8")
    (directory / "trajectory.jsonl").write_text("", encoding="utf-8")
    record = RolloutRecord(
        rollout_uid="u" + replicate,
        task_id=task_id,
        source_benchmark="claw_eval" if task_id.startswith("claw") else "browsecomp",
        replicate=replicate,
        attempt=attempt,
        mode="normal",
        rollout_id="rollout-x",
        rollout_dir=directory,
        workspace_dir=run_dir / "workspaces" / task_id / replicate,
        final_answer="",
        exit_reason=exit_reason,
        steps=3,
        duration_seconds=1.0,
        started_at="2026-09-05T00:00:00+00:00",
        usage=None,
        usd=0.01,
        pricing_tier="off_peak",
        partial=False,
        error_family="none",
        error_kind=None,
        error=None,
        score=None,
        serper_calls_approx=0,
        reasoning_steps=3,
    )
    (directory / "done.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")
    if task_id.startswith("claw"):
        with (directory / "claw_dispatches.jsonl").open("w", encoding="utf-8") as fh:
            for name in ("todo_list_tasks", "todo_update_task"):
                fh.write(json.dumps({"tool_name": name, "response_status": 200}) + "\n")
    return directory


def _claw_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "claw"
    task_dir = repo / "tasks" / "T000_synthetic"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        "scoring_components:\n  - name: a\n    check:\n      type: tool_called\n"
        "      tool_name: todo_update_task\n",
        encoding="utf-8",
    )
    return repo


def _make_runs(
    config: RunConfig, git_repo: Path, manifest: ComponentManifest
) -> tuple[Path, Path, str]:
    snapshot = snapshot_from_dir(
        SEED, store=SnapshotStore(git_repo / "store"), manifest=manifest, provenance="seed"
    )
    run_ctx = create_run_context(
        config, runs_root=config.runs_root, run_id="study", repo_dir=git_repo
    )
    write_manifest(
        run_ctx,
        config,
        harness_snapshot_id=snapshot.snapshot_id,
        run_spec={"harness_snapshot_id": snapshot.snapshot_id},
    )
    from ahd.harness.snapshot import copy_snapshot

    copy_snapshot(snapshot, SnapshotStore(run_ctx.out_dir / "harness"))
    ref_ctx = create_run_context(
        config, runs_root=config.runs_root, run_id="ref", repo_dir=git_repo
    )
    write_manifest(ref_ctx, config, harness_snapshot_id=snapshot.snapshot_id)
    copy_snapshot(snapshot, SnapshotStore(ref_ctx.out_dir / "harness"))
    run, ref = run_ctx.out_dir, ref_ctx.out_dir

    # failures: two premature finishes on the claw task, one content-only reply on bc
    f1 = trajectory([[LIST], [finish("done")]])
    f2 = trajectory([[LIST], [LIST], [finish("done")]])
    f3 = trajectory([[sh("curl -s https://example.com")], "the answer is Shelbyville"])
    _write_rollout(run, CLAW, "r1", f1)
    _write_rollout(run, CLAW, "r2", f2)
    _write_rollout(run, BC, "r1", f3, exit_reason="assistant_no_tool_call")
    failures = [
        FailureRecord(
            task_id=CLAW,
            source_benchmark="claw_eval",
            replicate="r1",
            attempt=1,
            mode="normal",
            harness_snapshot_id=snapshot.snapshot_id,
            trajectory_path=str(run / "rollouts" / CLAW / "r1" / "trajectory.jsonl"),
            partial=False,
            family="task",
            error_kind=None,
            reason="claw_grader: 0.2",
            score_value=0.2,
            passed=False,
            exit_reason="finished",
        ),
        FailureRecord(
            task_id=CLAW,
            source_benchmark="claw_eval",
            replicate="r2",
            attempt=1,
            mode="normal",
            harness_snapshot_id=snapshot.snapshot_id,
            trajectory_path=str(run / "rollouts" / CLAW / "r2" / "trajectory.jsonl"),
            partial=False,
            family="task",
            error_kind=None,
            reason="claw_grader: 0.3",
            score_value=0.3,
            passed=False,
            exit_reason="finished",
        ),
        FailureRecord(
            task_id=BC,
            source_benchmark="browsecomp",
            replicate="r1",
            attempt=1,
            mode="normal",
            harness_snapshot_id=snapshot.snapshot_id,
            trajectory_path=str(run / "rollouts" / BC / "r1" / "trajectory.jsonl"),
            partial=False,
            family="task",
            error_kind=None,
            reason="llm_as_judge: wrong",
            score_value=0.0,
            passed=False,
            exit_reason="assistant_no_tool_call",
        ),
        FailureRecord(
            task_id=BC,
            source_benchmark="browsecomp",
            replicate="r2",
            attempt=1,
            mode="normal",
            harness_snapshot_id=snapshot.snapshot_id,
            trajectory_path=str(run / "rollouts" / BC / "r2" / "trajectory.jsonl"),
            partial=True,
            family="infra",
            error_kind="policy_worker_timeout",
            reason="timeout",
            score_value=None,
            passed=False,
            exit_reason=None,
        ),
    ]
    (run / "failures.json").write_text(
        json.dumps([f.model_dump(mode="json") for f in failures]), encoding="utf-8"
    )
    # references: claw passes on attempt 2, bc on attempt 1
    _write_rollout(ref, CLAW, "r1", trajectory([[LIST], [finish("nope")]]), attempt=1)
    _write_rollout(
        ref, CLAW, "r1", trajectory([[LIST], [UPDATE], [finish("done")]]), attempt=2, passed=True
    )
    _write_rollout(
        ref,
        BC,
        "r1",
        trajectory([[sh("curl -s https://example.com")], [finish("Springfield")]]),
        passed=True,
    )
    references = [
        ReferenceRecord(
            task_id=CLAW, replicate="r1", attempts=2, max_attempts=5, passing_attempt=2
        ),
        ReferenceRecord(task_id=BC, replicate="r1", attempts=1, max_attempts=5, passing_attempt=1),
    ]
    (ref / "references.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in references]), encoding="utf-8"
    )
    return run, ref, snapshot.snapshot_id


def _reply(request: ChatRequest) -> str:
    prompt = request.messages[-1].content
    if (
        "Decide whether the run actually executed" in prompt
        or "reference material from the evaluator" in prompt
    ):
        return json.dumps({"g2": True, "g3": True, "explanation": "executed"})
    if "Which component does this diagnosis" in prompt:
        return json.dumps({"top3": ["completion_policy", "system_prompt", "verifier"]})
    # error signal: pick the first candidate offered
    candidates_block = prompt.split("Candidate harness components")[1]
    first = candidates_block.split("\n- ")[1].split(":")[0].strip()
    system_arm = "there is no reference run" in prompt
    return json.dumps(
        {
            "severity": "high",
            "cause_label": "premature_termination" if "todo" in prompt else "contract_violation",
            "mechanism": f"The {first} accepts an early stop after run_shell_command output.",
            "fix_hint": f"Make {first} continue until the state changed.",
            "component": first,
            **({"step": 2} if system_arm else {}),
        }
    )


def test_pipeline_end_to_end(
    config: RunConfig,
    git_repo: Path,
    taskset: TaskSet,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")
    run, ref, snapshot_id = _make_runs(config, git_repo, manifest)
    llm = DiagnosisLLM(FakeProvider(_reply))
    monkeypatch.chdir(REPO_ROOT)

    records = verify_references(
        ref,
        taskset=taskset,
        llm=llm,
        prompt_template=genuineness.load_prompt(),
        claw_repo=_claw_repo(tmp_path),
    )
    assert {r.task_id: r.verdict for r in records} == {CLAW: "genuine", BC: "genuine"}
    assert records[0].attempt == 2

    alignments = align_failures(run, ref)
    assert [a.failure_key for a in alignments] == [
        f"{CLAW}/r1/1",
        f"{CLAW}/r2/1",
        f"{BC}/r1/1",
    ]  # infra failure skipped
    assert alignments[0].alignment.candidates[0].divergence == "premature_finish"
    assert alignments[2].alignment.candidates[0].divergence == "no_tool_call"

    refused = signal_failures(
        run,
        ref,
        taskset=taskset,
        manifest=manifest,
        harness_snapshot_id=snapshot_id,
        llm=llm,
        prompts=load_prompts(),
    )
    assert not refused.reference and len(refused.errors) == 3  # no replay verdict, not allowed
    diagnoses = signal_failures(
        run,
        ref,
        taskset=taskset,
        manifest=manifest,
        harness_snapshot_id=snapshot_id,
        llm=llm,
        prompts=load_prompts(),
        allow_unvalidated=True,
    )
    assert len(diagnoses.reference) == 3 and len(diagnoses.system) == 3 and not diagnoses.errors
    assert set(diagnoses.failure_types.values()) == {"unvalidated"} and not diagnoses.excluded
    assert all(d.provenance.oracle_step_basis == "unvalidated" for d in diagnoses.reference)
    assert all(not d.provenance.oracle_validated for d in diagnoses.reference)  # no replay ran
    assert (
        diagnoses.reference[0].where.component == "completion_policy"
        and diagnoses.reference[0].where.rule == "R3"
    )
    assert diagnoses.reference[2].where.rule == "R4" and diagnoses.system[2].where.step == 2

    clusters, activity = cluster_run(
        run, manifest=manifest, reference_run="ref", instrument_snapshot_id=None
    )
    # claw r1 (premature finish, R3) and r2 (missing mutation, R9) land in different clusters
    assert len(clusters.clusters) == 3 and all(len(c.members) == 1 for c in clusters.clusters)
    block = read_manifest(run / "manifest.json").diagnosis
    assert block is not None and block["clusters_sha256"] == clusters.membership_sha256
    assert read_manifest(run / "manifest.json").schema_version == 5
    assert activity.tool_names == ("todo_list_tasks",)
    loaded, _ = load_clusters(run)
    assert loaded == clusters

    results = corrupt_run(run, seed=7, manifest=manifest)
    assert set(results) == set(ARM_CORRUPTION)
    for arm, (table, rendered) in results.items():
        assert (run / "diagnosis" / "assignments" / f"{arm}-s7.json").is_file()
        assert len(rendered) == 3
        for item, assignment in zip(rendered, table.assignments, strict=True):
            assert item.impossible is None, (arm, item.impossible)
            assert item.rendered is not None and item.diagnosis is not None
            assert "No further detail" not in item.rendered.text
            assert "run_shell_command" not in item.rendered.text.split("WHY")[1]
            assert assignment.rendered_lengths == item.rendered.field_lengths
            if arm.startswith("corrupt_where"):
                meta = item.diagnosis.where.distance_meta
                assert meta is not None
                assert (meta.same_layer == (arm == "corrupt_where_near")) or meta.distance_fallback
            if arm == "system":
                assert item.diagnosis.source == "system"
    caps = read_json(run / "diagnosis" / "rendered" / "reference-s7" / "caps.json")
    assert isinstance(caps, dict) and len(caps) == 3
    # every arm's field length is within the cluster cap
    for _table, rendered in results.values():
        for item in rendered:
            assert item.rendered is not None
            cap = caps[item.cluster_id]
            assert isinstance(cap, dict)
            assert item.rendered.field_lengths["mechanism"] <= int(str(cap["mechanism"]))
    only_far = corrupt_run(run, seed=7, manifest=manifest, arms=("corrupt_where_far",))
    assert list(only_far) == ["corrupt_where_far"]
    first = read_json(run / "diagnosis" / "assignments" / "corrupt_where_far-s7.json")
    assert first == only_far["corrupt_where_far"][0].model_dump(mode="json")

    report = leakage_run(run, manifest=manifest, llm=llm, prompt_template=leakage.load_prompt())
    assert report.n == 3 and report.top1_rate == pytest.approx(
        2 / 3
    )  # two completion_policy clusters

    cost = per_failure_cost(run)
    assert (
        cost["by_arm"] == {} and cost["replay_per_failure"] == {}
    )  # FakeProvider writes no ledger rows


def test_diag_parser_and_cost_command(
    config: RunConfig, git_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "diag",
            "replay",
            "study",
            "--reference-run",
            "ref",
            "--k",
            "2",
            "--max-candidates",
            "1",
            "--full-arms",
        ]
    )
    assert (
        args.k == 2 and args.max_candidates == 1 and args.full_arms and args.reference_run == "ref"
    )
    args = parser.parse_args(
        ["diag", "corrupt", "study", "--arm", "corrupt_where_near", "--seed", "3"]
    )
    assert args.arm == ["corrupt_where_near"] and args.seed == 3
    with pytest.raises(SystemExit):
        parser.parse_args(["diag", "corrupt", "study", "--arm", "bogus", "--seed", "3"])
    manifest = ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")
    _make_runs(config, git_repo, manifest)
    config_path = tmp_path / "c.yaml"
    import yaml

    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    assert main(["diag", "cost", "study", "--config", str(config_path)]) == 0
    out = capsys.readouterr().out
    assert '"by_arm"' in out
