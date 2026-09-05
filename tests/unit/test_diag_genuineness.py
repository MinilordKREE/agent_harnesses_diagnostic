"""Genuineness: deterministic checks and the judged verdict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahd.diagnosis.genuineness import claw_required_tools, deterministic_checks, verify
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.errors import InfraError
from ahd.llm.fake import FakeProvider
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import TaskSet
from tests.diag_fixtures import finish, sh, trajectory
from tests.evobench_fixtures import FAKE_REVISION

PROMPT = (
    Path(__file__).resolve().parents[2] / "configs" / "prompts" / "diagnosis" / "genuineness.md"
).read_text(encoding="utf-8")


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


def _claw_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "claw"
    task_dir = repo / "tasks" / "T000_synthetic"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text(
        "scoring_components:\n"
        "  - name: listing\n    check:\n      type: tool_called\n"
        "      tool_name: todo_list_tasks\n"
        "  - name: updates\n    check:\n      type: tool_called\n"
        "      tool_name: todo_update_task\n"
        "  - name: words\n    check:\n      type: keywords_present\n      keywords: [x]\n",
        encoding="utf-8",
    )
    return repo


def _dispatches(rollout_dir: Path, names: list[tuple[str, int]]) -> None:
    rollout_dir.mkdir(parents=True, exist_ok=True)
    with (rollout_dir / "claw_dispatches.jsonl").open("w", encoding="utf-8") as fh:
        for name, status in names:
            fh.write(
                json.dumps({"type": "tool_dispatch", "tool_name": name, "response_status": status})
                + "\n"
            )


def test_claw_required_tools_and_g1(tmp_path: Path, taskset: TaskSet) -> None:
    task = taskset.by_id("claw-T000_synthetic")
    repo = _claw_repo(tmp_path)
    assert claw_required_tools(task, claw_repo=repo) == ("todo_list_tasks", "todo_update_task")
    rollout = tmp_path / "r"
    _dispatches(rollout, [("todo_list_tasks", 200), ("todo_update_task", 500)])
    t = trajectory(
        [[("todo_list_tasks", {})], [("todo_update_task", {"task_id": "1"})], [finish("x")]]
    )
    g1, detail, g4 = deterministic_checks(task, t, rollout_dir=rollout, claw_repo=repo)
    assert not g1 and "todo_update_task" in detail and g4
    _dispatches(rollout, [("todo_list_tasks", 200), ("todo_update_task", 200)])
    assert deterministic_checks(task, t, rollout_dir=rollout, claw_repo=repo)[0]
    with pytest.raises(InfraError, match="Claw-Eval checkout"):
        deterministic_checks(task, t, rollout_dir=rollout, claw_repo=None)


def test_gdpval_search_and_hle_g1(tmp_path: Path, taskset: TaskSet) -> None:
    gdp = taskset.by_id("gdpval-00000000-0000-0000-0000-000000000001")
    rollout = tmp_path / "g"
    (rollout / "artifacts").mkdir(parents=True)
    t = trajectory([[sh("ls")], [finish("see outputs")]])
    assert deterministic_checks(gdp, t, rollout_dir=rollout, claw_repo=None)[0] is False
    (rollout / "artifacts" / "report.docx").write_bytes(b"x")
    assert deterministic_checks(gdp, t, rollout_dir=rollout, claw_repo=None)[0] is True
    bc = taskset.by_id("bc-en-0001")
    assert (
        deterministic_checks(
            bc, trajectory([[sh("ls")], [finish("x")]]), rollout_dir=rollout, claw_repo=None
        )[0]
        is False
    )
    assert (
        deterministic_checks(
            bc,
            trajectory([[sh("curl -s https://google.serper.dev/search")], [finish("x")]]),
            rollout_dir=rollout,
            claw_repo=None,
        )[0]
        is True
    )
    hle = taskset.by_id("hle-0000000000000000000000ff")
    g1, _, g4 = deterministic_checks(
        hle, trajectory([[finish("10")]]), rollout_dir=rollout, claw_repo=None
    )
    assert not g1 and not g4


def test_verify_verdicts(tmp_path: Path, taskset: TaskSet) -> None:
    bc = taskset.by_id("bc-en-0001")
    t = trajectory([[sh("curl -s https://example.com")], [finish("Springfield")]])
    rollout = tmp_path / "v"
    rollout.mkdir()

    def judged(g2: bool, g3: bool) -> str:
        return json.dumps({"g2": g2, "g3": g3, "explanation": "because"})

    llm = DiagnosisLLM(FakeProvider(judged(True, True)))
    record = verify(
        bc,
        t,
        replicate="r1",
        attempt=1,
        rollout_dir=rollout,
        claw_repo=None,
        llm=llm,
        prompt_template=PROMPT,
        task_prompt="Which town?",
    )
    assert record.verdict == "genuine" and record.g1 and record.g4 and record.g2 and record.g3
    assert record.request_sha256 and record.prompt_sha256 and record.model == "deepseek-v4-pro"
    request = llm.requests[-1]
    assert (
        request.attribution.arm == "diagnosis" and request.use_cache and request.temperature == 0.0
    )
    assert request.cache_scope is not None and request.cache_scope.startswith("genuineness:")
    assert (
        verify(
            bc,
            t,
            replicate="r1",
            attempt=1,
            rollout_dir=rollout,
            claw_repo=None,
            llm=DiagnosisLLM(FakeProvider(judged(False, True))),
            prompt_template=PROMPT,
            task_prompt="q",
        ).verdict
        == "shortcut"
    )
    assert (
        verify(
            bc,
            t,
            replicate="r1",
            attempt=1,
            rollout_dir=rollout,
            claw_repo=None,
            llm=DiagnosisLLM(FakeProvider("not json at all")),
            prompt_template=PROMPT,
            task_prompt="q",
        ).verdict
        == "undetermined"
    )
    unclear = verify(
        bc,
        t,
        replicate="r1",
        attempt=1,
        rollout_dir=rollout,
        claw_repo=None,
        llm=DiagnosisLLM(FakeProvider(json.dumps({"g2": "maybe"}))),
        prompt_template=PROMPT,
        task_prompt="q",
    )
    assert unclear.verdict == "undetermined" and unclear.g2 is None
    no_actions = verify(
        bc,
        trajectory([[finish("x")]]),
        replicate="r1",
        attempt=1,
        rollout_dir=rollout,
        claw_repo=None,
        llm=DiagnosisLLM(FakeProvider(judged(True, True))),
        prompt_template=PROMPT,
        task_prompt="q",
    )
    assert no_actions.verdict == "shortcut" and no_actions.g2 is None  # G1 failed: no judge call
