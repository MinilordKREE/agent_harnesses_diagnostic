"""Reference and system error signals, candidate enforcement, leakage probe, JSON parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahd.diagnosis.align import align
from ahd.diagnosis.cluster import cluster
from ahd.diagnosis.leakage import probe
from ahd.diagnosis.llm import DiagnosisLLM, MalformedModelOutput, parse_json_object
from ahd.diagnosis.schema import identifier_tokens, load_causes
from ahd.diagnosis.signal import load_prompts, reference_signal, system_signal
from ahd.errors import TaskFailure
from ahd.harness.components import ComponentManifest
from ahd.llm.fake import FakeProvider
from ahd.llm.types import ChatRequest
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import TaskSet
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import diagnosis, finish, trajectory
from tests.evobench_fixtures import FAKE_REVISION

VOCAB = load_causes(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "causes.yaml")
LIST: tuple[str, dict[str, Any]] = ("todo_list_tasks", {})
UPDATE: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "status": "completed"},
)


@pytest.fixture(scope="module")
def manifest() -> ComponentManifest:
    return ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


def _answer(component: str, **extra: object) -> str:
    return json.dumps(
        {
            "severity": "high",
            "cause_label": "premature_termination",
            "mechanism": "The system_prompt lets the policy stop after listing.",
            "fix_hint": "Tell the policy to update before finishing.",
            "component": component,
            **extra,
        }
    )


def test_parse_json_object_variants() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure: {"a": [1, 2]} done') == {"a": [1, 2]}
    with pytest.raises(MalformedModelOutput):
        parse_json_object("[1, 2]")
    with pytest.raises(MalformedModelOutput):
        parse_json_object("nothing here")


def test_reference_signal_uses_candidates_and_records_provenance(
    manifest: ComponentManifest, taskset: TaskSet
) -> None:
    task = taskset.by_id("claw-T000_synthetic")
    failed = trajectory([[LIST], [finish("done")]])
    reference = trajectory([[LIST], [UPDATE], [finish("done")]])
    alignment = align(failed, reference, task_id=task.id, failed_exit_reason="finished")
    prompts = load_prompts(REPO_ROOT / "configs" / "prompts" / "diagnosis")
    captured: list[ChatRequest] = []

    def reply(request: ChatRequest) -> str:
        captured.append(request)
        return _answer("completion_policy")

    d = reference_signal(
        task,
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        candidate=alignment.candidates[0],
        oracle_validated=True,
        reference_run="ref:r1/1",
        replicate="r1",
        attempt=1,
        harness_snapshot_id="snap",
        manifest=manifest,
        llm=DiagnosisLLM(FakeProvider(reply)),
        prompt_template=prompts["reference_signal"],
        vocabulary=VOCAB,
    )
    assert d.where.component == "completion_policy" and d.where.step == 2 and d.where.rule == "R3"
    assert (
        d.where.candidates == ("completion_policy", "verifier", "system_prompt")
        and d.where.attribution == "llm"
    )
    assert (
        d.source == "reference" and d.provenance.oracle_validated and d.provenance.oracle_step == 2
    )
    assert d.why.cause_label == "premature_termination" and d.severity == "high"
    prompt = captured[0].messages[-1].content
    assert (
        "Divergence step: 2" in prompt
        and "- completion_policy:" in prompt
        and "todo_update_task" in prompt
    )
    scope = captured[0].cache_scope
    assert captured[0].attribution.arm == "diagnosis"
    assert scope is not None and scope.startswith("signal:reference:")
    with pytest.raises(TaskFailure, match="not among candidates"):
        reference_signal(
            task,
            failed_trajectory=failed,
            reference_trajectory=reference,
            alignment=alignment,
            candidate=alignment.candidates[0],
            oracle_validated=False,
            reference_run="ref",
            replicate="r1",
            attempt=1,
            harness_snapshot_id="snap",
            manifest=manifest,
            llm=DiagnosisLLM(FakeProvider(_answer("tool_shell"))),
            prompt_template=prompts["reference_signal"],
            vocabulary=VOCAB,
        )
    with pytest.raises(TaskFailure, match="not in the vocabulary"):
        reference_signal(
            task,
            failed_trajectory=failed,
            reference_trajectory=reference,
            alignment=alignment,
            candidate=alignment.candidates[0],
            oracle_validated=False,
            reference_run="ref",
            replicate="r1",
            attempt=1,
            harness_snapshot_id="snap",
            manifest=manifest,
            llm=DiagnosisLLM(FakeProvider(_answer("verifier", cause_label="made_up"))),
            prompt_template=prompts["reference_signal"],
            vocabulary=VOCAB,
        )


def test_system_signal(manifest: ComponentManifest, taskset: TaskSet) -> None:
    task = taskset.by_id("claw-T000_synthetic")
    failed = trajectory([[LIST], "all done"])
    prompts = load_prompts(REPO_ROOT / "configs" / "prompts" / "diagnosis")
    d = system_signal(
        task,
        failed_trajectory=failed,
        exit_reason="assistant_no_tool_call",
        score_reason="claw_grader: 0.3",
        replicate="r1",
        attempt=1,
        harness_snapshot_id="snap",
        manifest=manifest,
        llm=DiagnosisLLM(FakeProvider(_answer("completion_policy", step=2))),
        prompt_template=prompts["system_signal"],
        vocabulary=VOCAB,
    )
    assert (
        d.source == "system"
        and d.where.rule == "R4"
        and d.where.step == 2
        and d.provenance.reference_run is None
    )
    assert not d.provenance.oracle_validated


def test_leakage_probe(manifest: ComponentManifest) -> None:
    cs = cluster(
        [
            diagnosis(component="system_prompt", mechanism="The system_prompt says stop early."),
            diagnosis(
                task_id="b",
                component="verifier",
                cause="missing_verification",
                mechanism="Nothing checks the finish answer.",
            ),
        ]
    )
    tokens = identifier_tokens(manifest)
    seen: list[str] = []

    def reply(request: ChatRequest) -> str:
        seen.append(request.messages[-1].text())
        return json.dumps({"top3": ["system_prompt", "task_prompt", "loop"]})

    report = probe(
        cs.clusters,
        manifest=manifest,
        tokens=tokens,
        llm=DiagnosisLLM(FakeProvider(reply)),
        prompt_template=(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "leakage.md").read_text(
            encoding="utf-8"
        ),
    )
    assert report.n == 2 and report.top1_rate == 0.5 and report.top3_rate == 0.5
    assert all(
        "system_prompt" not in p.split("A diagnosis")[1].split("Which component")[0] for p in seen
    )
    eligible = [c for c in manifest.components if c.patchable and c.where_eligible]
    assert report.chance_top1 == pytest.approx(1 / len(eligible))


def test_other_cause_label_is_accepted(manifest: ComponentManifest, taskset: TaskSet) -> None:
    task = taskset.by_id("claw-T000_synthetic")
    failed = trajectory([[LIST], "all done"])
    prompts = load_prompts(REPO_ROOT / "configs" / "prompts" / "diagnosis")
    reply = _answer("completion_policy", cause_label="other: judge misread the rubric", step=2)
    d = system_signal(
        task,
        failed_trajectory=failed,
        exit_reason="assistant_no_tool_call",
        score_reason="x",
        replicate="r1",
        attempt=1,
        harness_snapshot_id="snap",
        manifest=manifest,
        llm=DiagnosisLLM(FakeProvider(reply)),
        prompt_template=prompts["system_signal"],
        vocabulary=VOCAB,
    )
    assert d.why.cause_label == "other:judge misread the rubric"
