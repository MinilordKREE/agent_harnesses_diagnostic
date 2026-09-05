from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ahd.core.config import JudgeConfig
from ahd.errors import ConfigError, InfraError
from ahd.llm.fake import FakeProvider
from ahd.llm.ledger import Ledger, read_ledger, summarize
from ahd.tasks import scorer as scorer_module
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.models import Artifacts, Task, TaskSet
from ahd.tasks.scorer import REASON_RULES, ReasonRule, Scorer, artifact_sha256, classify_reason
from tests.evobench_fixtures import FAKE_REVISION


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


@pytest.fixture
def no_claw_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    @contextlib.contextmanager
    def _noop(judge: AhdJudgeClient) -> Iterator[None]:
        yield

    monkeypatch.setattr(scorer_module, "patched_claw_judge", _noop)


def _scorer(
    tmp_path: Path, reply: str = "CORRECT: yes\nREASON: fine"
) -> tuple[Scorer, Ledger, FakeProvider]:
    provider = FakeProvider(reply)
    ledger = Ledger(tmp_path / "ledger.jsonl", "run-s")
    judge = AhdJudgeClient(
        provider, config=JudgeConfig(), api_base="https://api.deepseek.com", seed=1
    )
    return Scorer(judge=judge, ledger=ledger, arm="A", seed=1), ledger, provider


def _workspace(tmp_path: Path, *, deliverable: bool = False) -> Path:
    ws = tmp_path / "ws"
    (ws / "outputs").mkdir(parents=True, exist_ok=True)
    if deliverable:
        (ws / "outputs" / "plan.txt").write_text("budget 12", encoding="utf-8")
    return ws


def _stub(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_score_task(
        task: dict[str, Any], workspace: Path, final_answer: str, judge_client: Any = None
    ) -> dict[str, Any]:
        calls.append(
            {
                "task": task,
                "workspace": workspace,
                "final_answer": final_answer,
                "judge": judge_client,
            }
        )
        return dict(result)

    monkeypatch.setattr(scorer_module, "score_task", fake_score_task)
    return calls


@pytest.mark.parametrize("rule", REASON_RULES, ids=[r.kind + ":" + r.family for r in REASON_RULES])
def test_every_reason_string_is_classified(rule: ReasonRule) -> None:
    assert classify_reason(rule.example) is rule


@pytest.mark.parametrize(
    "rule", [r for r in REASON_RULES if r.family == "infra"], ids=lambda r: r.example[:24]
)
def test_infra_reasons_raise_and_are_ledgered(
    rule: ReasonRule,
    taskset: TaskSet,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_claw_patch: None,
) -> None:
    _stub(monkeypatch, {"passed": False, "score": 0.0, "reason": rule.example})
    scorer, ledger, _ = _scorer(tmp_path)
    task = taskset.by_id("claw-T000_synthetic")
    with pytest.raises(InfraError) as info:
        scorer.score(task, Artifacts(workspace=_workspace(tmp_path), final_answer="x"))
    assert info.value.kind == rule.kind
    rows = read_ledger(ledger.path)
    assert [r.event for r in rows] == ["infra_failure"]
    assert rows[0].error_kind == rule.kind
    assert rows[0].arm == "A" and rows[0].unit_id == task.id


@pytest.mark.parametrize(
    "rule", [r for r in REASON_RULES if r.family == "task"], ids=lambda r: r.kind
)
def test_task_reasons_become_task_failures(
    rule: ReasonRule, taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, {"passed": False, "score": 0.0, "reason": rule.example})
    scorer, ledger, _ = _scorer(tmp_path)
    score = scorer.score(
        taskset.by_id("bc-en-0001"), Artifacts(workspace=_workspace(tmp_path), final_answer="x")
    )
    assert score.passed is False and score.task_failure == rule.kind
    summary = summarize(read_ledger(ledger.path))
    assert (summary.task_failures, summary.infra_failures) == (1, 0)


@pytest.mark.parametrize(
    "rule", [r for r in REASON_RULES if r.family == "judged"], ids=lambda r: r.kind
)
def test_judged_reasons_return_scores(
    rule: ReasonRule,
    taskset: TaskSet,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_claw_patch: None,
) -> None:
    _stub(
        monkeypatch,
        {"passed": True, "score": 1.0, "reason": rule.example, "judge_detail": {"k": 1}},
    )
    scorer, ledger, _ = _scorer(tmp_path)
    task = taskset.by_id("claw-T000_synthetic")
    score = scorer.score(task, Artifacts(workspace=_workspace(tmp_path), final_answer="x"))
    assert score.passed is True and score.value == 1.0
    assert score.scorer == rule.kind and score.task_failure is None
    assert score.judge_meta["judge_detail"] == {"k": 1}
    assert not ledger.path.exists()  # a judged verdict is not a ledger event by itself


def test_unknown_reason_is_infra_error(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, {"passed": False, "score": 0.0, "reason": "something new"})
    scorer, _, _ = _scorer(tmp_path)
    with pytest.raises(InfraError) as info:
        scorer.score(
            taskset.by_id("bc-en-0001"), Artifacts(workspace=_workspace(tmp_path), final_answer="x")
        )
    assert info.value.kind == "unknown_reason"


def test_judge_is_bound_to_task_and_artifact(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub(monkeypatch, {"passed": True, "score": 1.0, "reason": "llm_as_judge: ok"})
    scorer, _, _ = _scorer(tmp_path)
    ws = _workspace(tmp_path)
    artifacts = Artifacts(workspace=ws, final_answer="Springfield")
    score = scorer.score(taskset.by_id("bc-en-0001"), artifacts)
    judge = calls[0]["judge"]
    assert isinstance(judge, AhdJudgeClient)
    assert judge.unit_id == "bc-en-0001"
    assert judge.cache_scope == f"artifact:{score.artifact_sha256}"
    assert score.artifact_sha256 == artifact_sha256(artifacts)
    assert calls[0]["final_answer"] == "Springfield"
    assert "scorer" in calls[0]["task"]  # the scorer gets the protected record
    assert artifact_sha256(Artifacts(workspace=ws, final_answer="other")) != score.artifact_sha256


def test_trajectory_path_is_passed_as_private_key(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_claw_patch: None
) -> None:
    calls = _stub(
        monkeypatch, {"passed": True, "score": 1.0, "reason": "claw_grader: C=1.00 R=1.00"}
    )
    scorer, _, _ = _scorer(tmp_path)
    traj = tmp_path / "trajectory.json"
    traj.write_text("{}", encoding="utf-8")
    scorer.score(
        taskset.by_id("claw-T000_synthetic"),
        Artifacts(workspace=_workspace(tmp_path), final_answer="", trajectory_path=traj),
    )
    assert calls[0]["task"]["_trajectory_path"] == str(traj)


def test_prechecks_skip_the_judge(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub(monkeypatch, {"passed": True, "score": 1.0, "reason": "llm_as_judge"})
    scorer, ledger, _ = _scorer(tmp_path)
    empty = scorer.score(
        taskset.by_id("bc-en-0001"), Artifacts(workspace=_workspace(tmp_path), final_answer="   ")
    )
    assert empty.task_failure == "empty_answer" and empty.passed is False
    no_files = scorer.score(
        taskset.by_id("gdpval-00000000-0000-0000-0000-000000000001"),
        Artifacts(workspace=_workspace(tmp_path), final_answer="done"),
    )
    assert no_files.task_failure == "no_deliverable"
    assert calls == []
    rows = read_ledger(ledger.path)
    assert [r.error_kind for r in rows] == ["empty_answer", "no_deliverable"]


def test_gdpval_with_deliverable_reaches_the_judge(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub(
        monkeypatch,
        {
            "passed": False,
            "score": 0.3,
            "reason": "rubric_file_judge(text-only): 1.0/3 pts, 1 file(s), thr=0.6",
            "judge_detail": {"image_grading": {"used": False}},
        },
    )
    scorer, _, _ = _scorer(tmp_path)
    score = scorer.score(
        taskset.by_id("gdpval-00000000-0000-0000-0000-000000000001"),
        Artifacts(workspace=_workspace(tmp_path, deliverable=True), final_answer=""),
    )
    assert len(calls) == 1 and score.value == 0.3 and score.task_failure is None


def test_excluded_task_and_missing_workspace(taskset: TaskSet, tmp_path: Path) -> None:
    scorer, _, _ = _scorer(tmp_path)
    with pytest.raises(ConfigError, match="excluded"):
        scorer.score(
            taskset.by_id("apex-0000000000000000000000000000ab"),
            Artifacts(workspace=tmp_path, final_answer="x"),
        )
    with pytest.raises(InfraError) as info:
        scorer.score(
            taskset.by_id("bc-en-0001"), Artifacts(workspace=tmp_path / "nope", final_answer="x")
        )
    assert info.value.kind == "missing_file"


def test_claw_scoring_uses_the_patch(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[str] = []

    @contextlib.contextmanager
    def fake_patch(judge: AhdJudgeClient) -> Iterator[None]:
        entered.append(judge.unit_id)
        yield

    monkeypatch.setattr(scorer_module, "patched_claw_judge", fake_patch)
    _stub(monkeypatch, {"passed": True, "score": 1.0, "reason": "claw_grader: C=1.00 R=1.00"})
    scorer, _, _ = _scorer(tmp_path)
    scorer.score(
        taskset.by_id("claw-T000_synthetic"),
        Artifacts(workspace=_workspace(tmp_path), final_answer=""),
    )
    scorer.score(
        taskset.by_id("bc-en-0001"), Artifacts(workspace=_workspace(tmp_path), final_answer="x")
    )
    assert entered == ["claw-T000_synthetic"]


def test_model_task_type_is_pydantic(taskset: TaskSet) -> None:
    assert isinstance(taskset.tasks[0], Task)


def test_claw_scoring_sets_repo_env_for_the_call(
    taskset: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_claw_patch: None
) -> None:
    import os

    seen: list[str | None] = []

    def fake_score_task(
        task: dict[str, Any], workspace: Path, final_answer: str, judge_client: Any = None
    ) -> dict[str, Any]:
        seen.append(os.environ.get("EVOBENCH_CLAW_REPO"))
        return {"passed": True, "score": 1.0, "reason": "claw_grader: C=1.00 R=1.00"}

    monkeypatch.setattr(scorer_module, "score_task", fake_score_task)
    monkeypatch.delenv("EVOBENCH_CLAW_REPO", raising=False)
    provider = FakeProvider("ok")
    ledger = Ledger(tmp_path / "ledger.jsonl", "run-s")
    judge = AhdJudgeClient(provider, config=JudgeConfig(), api_base="https://x", seed=1)
    scorer = Scorer(judge=judge, ledger=ledger, arm="A", seed=1, claw_repo=tmp_path / "claw")
    scorer.score(
        taskset.by_id("claw-T000_synthetic"),
        Artifacts(workspace=_workspace(tmp_path), final_answer=""),
    )
    scorer.score(
        taskset.by_id("bc-en-0001"), Artifacts(workspace=_workspace(tmp_path), final_answer="x")
    )
    assert seen == [str(tmp_path / "claw"), None]  # set for Claw only, restored afterwards
    assert "EVOBENCH_CLAW_REPO" not in os.environ
