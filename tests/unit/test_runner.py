from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from ahd.core.config import RunConfig, load_run_config
from ahd.core.context import create_run_context
from ahd.core.trace import TraceWriter, read_trace
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, snapshot_from_dir
from ahd.llm.fake import FakeProvider
from ahd.llm.ledger import Ledger, read_ledger, summarize
from ahd.llm.pricing import PricingTable
from ahd.runner import runner as runner_module
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.runner.worker import WorkerOutcome
from ahd.settings import Settings
from ahd.tasks import scorer as scorer_module
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.models import TaskSet
from ahd.tasks.scorer import Scorer
from tests.conftest import REPO_ROOT
from tests.evobench_fixtures import FAKE_REVISION
from tests.runner_fixtures import ROLLOUT_LOG, fake_trajectory, write_rollout_files

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
pytestmark = pytest.mark.skipif(
    not (SEED / "harness.py").is_file(), reason="submodule not checked out"
)

type Scenario = Callable[[dict[str, Any], Path, Path], WorkerOutcome]


class FakeWorker:
    """Scripted stand-in for ``invoke_worker``: writes Evo-Bench-shaped files per rollout."""

    def __init__(self) -> None:
        self.scenarios: dict[tuple[str, str, int], Scenario] = {}
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        request: dict[str, Any],
        rollout_dir: Path,
        env: dict[str, str],
        timeout_s: int,
        python: str | None = None,
    ) -> WorkerOutcome:
        self.requests.append(request)
        key = (
            request["task"]["id"],
            rollout_dir.name
            if not rollout_dir.name.startswith("attempt_")
            else rollout_dir.parent.name,
            _attempt_of(rollout_dir),
        )
        scenario = self.scenarios[key]
        workspace = Path(request["task_workspace"])
        return scenario(request, rollout_dir, workspace)


def _attempt_of(rollout_dir: Path) -> int:
    return int(rollout_dir.name.split("_")[1]) if rollout_dir.name.startswith("attempt_") else 1


def finished(
    final_answer: str,
    *,
    commands: list[str] | None = None,
    exit_reason: str = "finished",
    deliverable: bool = False,
    usage_mismatch: bool = False,
) -> Scenario:
    def scenario(request: dict[str, Any], rollout_dir: Path, workspace: Path) -> WorkerOutcome:
        trajectory, metadata = fake_trajectory(
            commands=commands or ["ls"], final_answer=final_answer, exit_reason=exit_reason
        )
        if usage_mismatch:
            metadata["token_usage"]["prompt_tokens"] += 1
        write_rollout_files(rollout_dir, trajectory, metadata)
        if deliverable:
            (workspace / "outputs" / "plan.txt").write_text("budget 12", encoding="utf-8")
        return WorkerOutcome(
            ok=True,
            rollout={
                "rollout_id": "rollout-fake",
                "final_answer": final_answer,
                "exit_reason": exit_reason,
            },
            error=None,
            error_type=None,
            returncode=0,
            timed_out=False,
            elapsed_seconds=1.0,
            stdout_tail="",
            stderr_tail="",
        )

    return scenario


def crashed() -> Scenario:
    def scenario(request: dict[str, Any], rollout_dir: Path, workspace: Path) -> WorkerOutcome:
        rollout_dir.mkdir(parents=True, exist_ok=True)
        (rollout_dir / "rollout.log").write_text(ROLLOUT_LOG, encoding="utf-8")
        return WorkerOutcome(
            ok=False,
            rollout=None,
            error="policy worker timed out",
            error_type="policy_worker_timeout",
            returncode=124,
            timed_out=True,
            elapsed_seconds=5.0,
            stdout_tail="",
            stderr_tail="killed",
        )

    return scenario


@pytest.fixture
def config(pricing_path: Path, git_repo: Path) -> RunConfig:
    base = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    return base.model_copy(update={"pricing_path": pricing_path, "runs_root": git_repo / "runs"})


@pytest.fixture
def seed_snapshot(tmp_path: Path) -> HarnessSnapshot:
    manifest = ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")
    return snapshot_from_dir(
        SEED, store=SnapshotStore(tmp_path / "store"), manifest=manifest, provenance="seed"
    )


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


class Harness:
    def __init__(
        self,
        *,
        config: RunConfig,
        git_repo: Path,
        pricing: PricingTable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.worker = FakeWorker()
        monkeypatch.setattr(runner_module, "invoke_worker", self.worker)
        self.score_calls: list[tuple[str, str]] = []

        def fake_score_task(
            task: dict[str, Any], workspace: Path, final_answer: str, judge_client: Any = None
        ) -> dict[str, Any]:
            self.score_calls.append((task["id"], final_answer))
            passed = "42" in final_answer or (workspace / "outputs" / "plan.txt").exists()
            return {
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "reason": "llm_as_judge: ok" if passed else "llm_as_judge: wrong",
            }

        monkeypatch.setattr(scorer_module, "score_task", fake_score_task)
        self.ctx = create_run_context(
            config, runs_root=config.runs_root, run_id="run-unit", repo_dir=git_repo
        )
        self.ledger = Ledger(self.ctx.out_dir / "ledger.jsonl", self.ctx.run_id)
        judge = AhdJudgeClient(
            FakeProvider("ok"), config=config.judge, api_base="https://x", seed=0
        )
        self.scorer = Scorer(judge=judge, ledger=self.ledger, arm="seed", seed=0)
        self.trace = TraceWriter(self.ctx.out_dir / "trace.jsonl", self.ctx.run_id)
        self.runner = Runner(
            ctx=self.ctx,
            config=config,
            settings=Settings.model_validate({"deepseek_api_key": "k"}),
            pricing=pricing,
            ledger=self.ledger,
            scorer=self.scorer,
            trace=self.trace,
            claw_repo=None,
            reference_template=(
                "=== REFERENCE (reference mode) ===\n{gold}\n=== END REFERENCE ===\n"
            ),
        )


@pytest.fixture
def harness(
    config: RunConfig, git_repo: Path, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> Harness:
    return Harness(config=config, git_repo=git_repo, pricing=pricing, monkeypatch=monkeypatch)


def test_normal_run_records_everything(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    spec = RunSpec.from_config(
        config,
        harness_snapshot_id=seed_snapshot.snapshot_id,
        task_ids=("bc-en-0001", "gdpval-00000000-0000-0000-0000-000000000001"),
        replicates=2,
    )
    w = harness.worker
    w.scenarios[("bc-en-0001", "r1", 1)] = finished(
        "42", commands=["curl -s https://google.serper.dev/search -d q=town", "ls"]
    )
    w.scenarios[("bc-en-0001", "r2", 1)] = finished("", exit_reason="max_steps")
    w.scenarios[("gdpval-00000000-0000-0000-0000-000000000001", "r1", 1)] = finished(
        "see outputs", deliverable=True
    )
    w.scenarios[("gdpval-00000000-0000-0000-0000-000000000001", "r2", 1)] = crashed()
    tasks = [taskset.by_id(t) for t in spec.task_ids]
    result = harness.runner.run(spec, tasks, snapshot=seed_snapshot)
    harness.trace.close()

    bc, gdp = result.tasks
    assert bc.pass_at_k and not bc.pass_hat_k and bc.budget == 1
    assert gdp.infra == 1 and gdp.passed == 1
    r1 = bc.rollouts[0]
    assert r1.serper_calls_approx == 1 and r1.reasoning_steps == r1.steps == 3
    assert r1.score is not None and r1.score.passed and r1.usd is not None and r1.usd > 0
    assert r1.error_family == "none"
    r2 = bc.rollouts[1]
    assert r2.error_family == "budget" and r2.error_kind == "budget_exhausted"
    assert r2.score is not None and r2.score.task_failure == "empty_answer"
    crashed_record = gdp.rollouts[1]
    assert (
        crashed_record.error_family == "infra"
        and crashed_record.partial
        and crashed_record.score is None
    )
    assert crashed_record.error_kind == "policy_worker_timeout"

    out = harness.ctx.out_dir
    events = read_trace(
        out / "rollouts" / "bc-en-0001" / "r1" / "trajectory.jsonl", expected_run_id="run-unit"
    )
    kinds = [e.kind for e in events]
    assert (
        kinds[0] == "rollout_start"
        and kinds[-1] == "rollout_end"
        and "model_call" in kinds
        and "observation" in kinds
    )
    assert (
        events[0].payload["mode"] == "normal"
        and events[0].payload["harness_snapshot_id"] == seed_snapshot.snapshot_id
    )
    partial = read_trace(
        out / "rollouts" / "gdpval-00000000-0000-0000-0000-000000000001" / "r2" / "trajectory.jsonl"
    )
    assert (
        partial[-1].payload["partial"] is True
        and (
            out
            / "rollouts"
            / "gdpval-00000000-0000-0000-0000-000000000001"
            / "r2"
            / "worker_failure.json"
        ).exists()
    )
    assert (out / "rollouts" / "bc-en-0001" / "r1" / "score.json").exists()
    assert not (
        out / "rollouts" / "gdpval-00000000-0000-0000-0000-000000000001" / "r2" / "score.json"
    ).exists()
    assert (
        out
        / "rollouts"
        / "gdpval-00000000-0000-0000-0000-000000000001"
        / "r1"
        / "artifacts"
        / "plan.txt"
    ).read_text() == "budget 12"
    assert (out / "harness" / seed_snapshot.snapshot_id / "snapshot.json").exists()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["per_source"]["browsecomp"]["pass_at_k_rate"] == 1.0
    failures = json.loads((out / "failures.json").read_text())
    assert sorted((f["family"], f["replicate"]) for f in failures) == [
        ("budget", "r2"),
        ("infra", "r2"),
    ]
    assert all(f["verified"] == "pending" for f in failures)

    rows = read_ledger(harness.ledger.path)
    s = summarize(rows)
    # the crashed rollout still yields per-step usage reconstructed from rollout.log
    assert s.policy_rollouts == 4
    assert s.search_calls == 1 and rows[[r.event for r in rows].index("search")].approximate is True
    assert s.budget_exhausted == 1 and s.infra_failures == 1
    policy_rows = [r for r in rows if r.event == "policy"]
    assert {r.replicate for r in policy_rows} == {"r1", "r2"} and all(
        r.usd > 0 for r in policy_rows
    )
    # scores are written only after all replicates of a task ran
    request_order = [r["task"]["id"] for r in harness.worker.requests]
    assert request_order == [
        "bc-en-0001",
        "bc-en-0001",
        "gdpval-00000000-0000-0000-0000-000000000001",
        "gdpval-00000000-0000-0000-0000-000000000001",
    ]
    # the empty-answer replicate is decided by the precheck and never reaches Evo-Bench's scorer
    assert harness.score_calls[0][0] == "bc-en-0001" and len(harness.score_calls) == 2
    assert not (out / "workspaces" / "bc-en-0001").exists()  # cleaned up


def test_usage_mismatch_is_infra(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    spec = RunSpec.from_config(
        config, harness_snapshot_id=seed_snapshot.snapshot_id, task_ids=("bc-en-0001",)
    )
    harness.worker.scenarios[("bc-en-0001", "r1", 1)] = finished("42", usage_mismatch=True)
    result = harness.runner.run(spec, [taskset.by_id("bc-en-0001")], snapshot=seed_snapshot)
    record = result.tasks[0].rollouts[0]
    assert (
        record.error_family == "infra"
        and record.error_kind == "usage_mismatch"
        and record.score is None
    )
    assert result.failures[0].family == "infra"


def test_reference_mode_stops_at_first_pass(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    spec = RunSpec.from_config(
        config,
        harness_snapshot_id=seed_snapshot.snapshot_id,
        task_ids=("bc-en-0001",),
        mode="reference",
    ).model_copy(update={"reference_max_attempts": 3})
    w = harness.worker
    w.scenarios[("bc-en-0001", "r1", 1)] = finished("no idea")
    w.scenarios[("bc-en-0001", "r1", 2)] = finished("42")
    w.scenarios[("bc-en-0001", "r1", 3)] = finished("never used")
    result = harness.runner.run(spec, [taskset.by_id("bc-en-0001")], snapshot=seed_snapshot)
    assert [r.attempt for r in result.tasks[0].rollouts] == [1, 2]
    assert result.references[0].passing_attempt == 2 and result.references[0].verified == "pending"
    assert result.tasks[0].pass_hat_k
    prompt = harness.worker.requests[0]["task"]["prompt"]
    assert "=== REFERENCE (reference mode) ===" in prompt and "Springfield" in prompt
    assert "scorer" not in harness.worker.requests[0]["task"]
    assert (
        harness.ctx.out_dir / "rollouts" / "bc-en-0001" / "r1" / "attempt_2" / "score.json"
    ).exists()
    assert (harness.ctx.out_dir / "references.json").exists()


def test_mock_today_injected_only_when_null(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    spec = RunSpec.from_config(
        config, harness_snapshot_id=seed_snapshot.snapshot_id, task_ids=("claw-T000_synthetic",)
    ).model_copy(update={"mock_today": "2026-03-01"})
    harness.worker.scenarios[("claw-T000_synthetic", "r1", 1)] = finished("done")
    harness.runner.run(spec, [taskset.by_id("claw-T000_synthetic")], snapshot=seed_snapshot)
    claw_public = harness.worker.requests[0]["task"]["claw_public"]
    assert claw_public["mock_today"] == "2026-03-01"
    # a task that carries its own date keeps it
    task = taskset.by_id("claw-T000_synthetic")
    own = task.model_copy(
        update={
            "raw": {
                **task.raw,
                "claw_public": {
                    **cast(dict[str, Any], task.raw["claw_public"]),
                    "mock_today": "2025-12-31",
                },
            }
        }
    )
    harness.worker.scenarios[("claw-T000_synthetic", "r2", 1)] = finished("done")
    harness.runner.run(
        spec.model_copy(update={"replicate_ids": ("r2",)}), [own], snapshot=seed_snapshot
    )
    assert harness.worker.requests[-1]["task"]["claw_public"]["mock_today"] == "2025-12-31"
    assert harness.worker.requests[0]["model_config"]["api_base"] == config.policy.api_base
    assert harness.worker.requests[0]["harness_revision"] == seed_snapshot.meta.evobench_revision


def test_invalid_snapshot_is_refused(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    from ahd.harness.validate import SnapshotInvalidError

    spec = RunSpec.from_config(
        config, harness_snapshot_id=seed_snapshot.snapshot_id, task_ids=("bc-en-0001",)
    ).model_copy(update={"budget": config.budget.model_copy(update={"max_steps": 10})})
    with pytest.raises(SnapshotInvalidError):
        harness.runner.run(spec, [taskset.by_id("bc-en-0001")], snapshot=seed_snapshot)


def test_parallel_lanes_match_sequential(
    harness: Harness, taskset: TaskSet, seed_snapshot: HarnessSnapshot, config: RunConfig
) -> None:
    ids = ("bc-en-0001", "gdpval-00000000-0000-0000-0000-000000000001")
    spec = RunSpec.from_config(
        config, harness_snapshot_id=seed_snapshot.snapshot_id, task_ids=ids, replicates=2, workers=3
    )
    w = harness.worker
    for task_id in ids:
        for rep in ("r1", "r2"):
            w.scenarios[(task_id, rep, 1)] = finished("42", deliverable=True)
    result = harness.runner.run(spec, [taskset.by_id(t) for t in ids], snapshot=seed_snapshot)
    assert all(t.pass_hat_k for t in result.tasks)
    records = [r for t in result.tasks for r in t.rollouts]
    assert len(records) == 4 and len({r.rollout_uid for r in records}) == 4
    for record in records:
        marker = record.rollout_dir / "done.json"
        assert marker.is_file() and (record.rollout_dir / "score.json").is_file()
        assert json.loads(marker.read_text())["rollout_uid"] == record.rollout_uid
    rows = read_ledger(harness.ledger.path)
    assert {r.rollout_uid for r in rows if r.event == "policy"} == {r.rollout_uid for r in records}
    summary = json.loads((harness.ctx.out_dir / "summary.json").read_text())
    assert summary["ledger"]["policy_rollouts"] == 4
    assert summary["per_source"]["gdpval"]["pass_hat_k_rate"] == 1.0


def test_resume_reruns_only_unfinished_lanes(
    harness: Harness,
    taskset: TaskSet,
    seed_snapshot: HarnessSnapshot,
    config: RunConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = ("bc-en-0001", "gdpval-00000000-0000-0000-0000-000000000001")
    spec = RunSpec.from_config(
        config, harness_snapshot_id=seed_snapshot.snapshot_id, task_ids=ids, replicates=2, workers=2
    )
    w = harness.worker
    for task_id in ids:
        for rep in ("r1", "r2"):
            w.scenarios[(task_id, rep, 1)] = finished("42", deliverable=True)
    # Crash lane (gdpval, r2) after its ledger rows were written but before its done marker.
    original_marker = Runner._write_done_marker

    def crashing_marker(self: Runner, record: Any) -> None:
        if record.task_id == ids[1] and record.replicate == "r2":
            raise RuntimeError("simulated crash before done marker")
        original_marker(self, record)

    monkeypatch.setattr(Runner, "_write_done_marker", crashing_marker)
    first = harness.runner.run(spec, [taskset.by_id(t) for t in ids], snapshot=seed_snapshot)
    crashed = [r for t in first.tasks for r in t.rollouts if r.error_kind == "runner_exception"]
    assert len(crashed) == 1 and crashed[0].replicate == "r2" and crashed[0].partial
    assert not (crashed[0].rollout_dir / "done.json").exists()
    assert (crashed[0].rollout_dir / "runner_exception.txt").exists()
    first_summary = json.loads((harness.ctx.out_dir / "summary.json").read_text())
    assert first_summary["ledger"]["policy_rollouts"] == 3  # the crashed lane's rows are excluded
    requests_before = len(w.requests)

    monkeypatch.setattr(Runner, "_write_done_marker", original_marker)
    harness.score_calls.clear()
    second = harness.runner.run(
        spec, [taskset.by_id(t) for t in ids], snapshot=seed_snapshot, resume=True
    )
    assert len(w.requests) == requests_before + 1  # only the crashed lane ran again
    assert w.requests[-1]["task"]["id"] == ids[1]
    assert all(t.pass_hat_k for t in second.tasks) and all(t.infra == 0 for t in second.tasks)
    assert [c[0] for c in harness.score_calls] == [
        ids[1]
    ]  # already-scored rollouts were not re-scored
    second_summary = json.loads((harness.ctx.out_dir / "summary.json").read_text())
    assert (
        second_summary["ledger"]["policy_rollouts"] == 4
    )  # the orphaned first-attempt row stays excluded
    rows = read_ledger(harness.ledger.path)
    assert sum(1 for r in rows if r.event == "policy") == 5  # 4 kept + 1 orphaned
    events = read_trace(harness.ctx.out_dir / "trace.jsonl")
    assert [e.kind for e in events].count("rollout_reused") == 3
