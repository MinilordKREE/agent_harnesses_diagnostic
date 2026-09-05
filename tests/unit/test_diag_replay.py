"""Replay validation with the worker mocked: prefix payloads, arms, sufficiency, statuses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahd.core.config import RunConfig, load_run_config
from ahd.core.context import create_run_context
from ahd.core.trace import TraceWriter
from ahd.diagnosis.align import Alignment, align
from ahd.diagnosis.pipeline import instrument_snapshot
from ahd.diagnosis.replay import Replayer, prefix_payload, reference_message_at
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, snapshot_from_dir
from ahd.llm.fake import FakeProvider
from ahd.llm.ledger import Ledger, read_ledger
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
TASK = "bc-en-0001"


def test_prefix_payload_boundary_and_flags() -> None:
    failed = trajectory(
        [[sh("ls")], [sh("echo x > a.txt")], [sh("cat a.txt")], [finish("wrong")]],
        outputs={
            1: {
                "stdout": "the quoted line of text here",
                "stderr": "",
                "exit_code": 0,
                "duration_seconds": 0.1,
            }
        },
        reasoning={3: "I saw: the quoted line of text here", 4: "irrelevant: step t is dropped"},
    )
    reference = trajectory(
        [[sh("ls")], [sh("echo x > a.txt")], [sh("cat a.txt")], [UPDATE], [finish("right")]]
    )
    payload = prefix_payload(
        failed,
        step=4,
        arm="substitute",
        substitute=reference_message_at(reference, 4),
        recorded_workspace="/ws/rec",
    )
    assert payload["resume_step"] == 4 and payload["arm"] == "substitute"
    assert [a["step"] for a in payload["prefix_actions"]] == [1, 2, 3]
    assert [a["mutating"] for a in payload["prefix_actions"]] == [False, True, False]
    assert [a["quoted"] for a in payload["prefix_actions"]] == [True, False, False]
    assert payload["prefix_actions"][1]["recorded_output"]["exit_code"] == 0
    # messages: system, user, then (assistant, tool) x 3 = 8, cut before the 4th assistant message
    assert len(payload["prefix_messages"]) == 8 and payload["prefix_messages"][-1]["role"] == "tool"
    assert all(int(e["step"]) < 4 for e in payload["prefix_trajectory"])
    assert payload["substitute"]["tool_calls"][0]["function"]["name"] == "todo_update_task"
    assert payload["masks"] == [["/ws/rec", "<workspace>"]]
    control = prefix_payload(
        failed, step=4, arm="control", substitute=None, recorded_workspace=None
    )
    assert control["substitute"] is None and control["masks"] == []
    assert reference_message_at(reference, 9) is None


class ReplayWorker:
    """Scripted worker: per (arm, index) decides pass/fail/unreplayable and writes the files."""

    def __init__(self) -> None:
        self.outcomes: dict[tuple[str, int], str] = {}
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
        replay = request["task"]["_ahd_replay"]
        index = int(rollout_dir.name[1:])
        kind = self.outcomes.get((replay["arm"], index), "pass")
        rollout_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "status": "unreplayable" if kind == "unreplayable" else "ok",
            "drifts": [{"step": 2}] if kind == "unreplayable" else [],
            "warnings": [],
        }
        (rollout_dir / "replay_report.json").write_text(json.dumps(report), encoding="utf-8")
        answer = "42" if kind == "pass" else "wrong"
        steps: list[Any] = [[sh("ls")], [sh("cat a")], [finish(answer)]]
        traj = trajectory(steps)
        exit_reason = "unreplayable" if kind == "unreplayable" else "finished"
        if kind == "unreplayable":
            traj["trajectory"] = []
        metadata = {
            "rollout_id": "rollout-replay",
            "task_id": request["task"]["id"],
            "exit_reason": exit_reason,
            "final_answer": answer,
            "duration_seconds": 1.0,
            "token_usage": {
                "prompt_tokens": 30 if kind != "unreplayable" else 0,
                "completion_tokens": 15 if kind != "unreplayable" else 0,
                "total_tokens": 45 if kind != "unreplayable" else 0,
            },
            "steps": 3,
            "runtime_errors": [],
        }
        (rollout_dir / "trajectory.json").write_text(json.dumps(traj), encoding="utf-8")
        (rollout_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (rollout_dir / "rollout.log").write_text(
            "02:00:00 [INFO] [+    0.0s] rollout start\n", encoding="utf-8"
        )
        return WorkerOutcome(
            ok=True,
            rollout={"rollout_id": "rollout-replay"},
            error=None,
            error_type=None,
            returncode=0,
            timed_out=False,
            elapsed_seconds=1.0,
            stdout_tail="",
            stderr_tail="",
        )


@pytest.fixture
def config(pricing_path: Path, git_repo: Path) -> RunConfig:
    base = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    return base.model_copy(update={"pricing_path": pricing_path, "runs_root": git_repo / "runs"})


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


class Setup:
    def __init__(
        self,
        config: RunConfig,
        git_repo: Path,
        pricing: PricingTable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self.worker = ReplayWorker()
        monkeypatch.setattr(runner_module, "invoke_worker", self.worker)

        def fake_score_task(
            task: dict[str, Any], workspace: Path, final_answer: str, judge_client: Any = None
        ) -> dict[str, Any]:
            passed = "42" in final_answer
            return {
                "passed": passed,
                "score": 1.0 if passed else 0.0,
                "reason": "llm_as_judge: ok" if passed else "llm_as_judge: wrong",
            }

        monkeypatch.setattr(scorer_module, "score_task", fake_score_task)
        self.ctx = create_run_context(
            config, runs_root=config.runs_root, run_id="run-replay", repo_dir=git_repo
        )
        self.ledger = Ledger(self.ctx.out_dir / "ledger.jsonl", self.ctx.run_id)
        judge = AhdJudgeClient(
            FakeProvider("ok"), config=config.judge, api_base="https://x", seed=0
        )
        scorer = Scorer(judge=judge, ledger=self.ledger, arm="replay", seed=0)
        self.trace = TraceWriter(self.ctx.out_dir / "trace.jsonl", self.ctx.run_id)
        self.runner = Runner(
            ctx=self.ctx,
            config=config,
            settings=Settings.model_validate({"deepseek_api_key": "k"}),
            pricing=pricing,
            ledger=self.ledger,
            scorer=scorer,
            trace=self.trace,
            claw_repo=None,
        )
        manifest = ComponentManifest.load(
            REPO_ROOT / "configs" / "harness" / "seed_components.yaml"
        )
        self.studied: HarnessSnapshot = snapshot_from_dir(
            SEED,
            store=SnapshotStore(self.ctx.out_dir / "harness"),
            manifest=manifest,
            provenance="seed",
        )
        self.instrument = instrument_snapshot(self.ctx.out_dir, manifest)
        self.spec = RunSpec.from_config(
            config, harness_snapshot_id=self.studied.snapshot_id, task_ids=(TASK,)
        )

    def replayer(self, **kwargs: Any) -> Replayer:
        return Replayer(
            runner=self.runner,
            spec=self.spec,
            studied=self.studied,
            instrument=self.instrument,
            out_dir=self.ctx.out_dir / "diagnosis",
            reference_run="ref-run",
            **kwargs,
        )


@pytest.fixture
def setup(
    config: RunConfig, git_repo: Path, pricing: PricingTable, monkeypatch: pytest.MonkeyPatch
) -> Setup:
    return Setup(config, git_repo, pricing, monkeypatch)


def _pair() -> tuple[dict[str, Any], dict[str, Any], Alignment]:
    failed = trajectory([[sh("ls")], [sh("cat a")], [finish("wrong")]])
    reference = trajectory(
        [[sh("ls")], [sh("cat a")], [sh("grep town a")], [finish("Springfield")]]
    )
    return failed, reference, align(failed, reference, task_id=TASK, failed_exit_reason="finished")


def test_instrument_is_hashed_and_differs_from_seed(setup: Setup) -> None:
    assert setup.instrument.meta.provenance == "instrument"
    assert setup.instrument.meta.sha256 != setup.studied.meta.sha256
    assert (
        (setup.instrument.tree / "agent" / "loop.py")
        .read_text(encoding="utf-8")
        .startswith("# Adapted from: RUCAIBox/Evo-Bench")
    )


def test_sufficient_candidate_and_bookkeeping(setup: Setup, taskset: TaskSet) -> None:
    failed, reference, alignment = _pair()
    w = setup.worker
    w.outcomes.update({("control", 1): "fail", ("control", 2): "fail", ("control", 3): "pass"})
    result = setup.replayer(k=3, max_candidates=5).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace="/old/ws",
    )
    setup.trace.close()
    assert (
        result.oracle_step == 3
        and result.oracle_status == "validated"
        and result.sufficient_set == (3,)
    )
    c = result.candidates[0]
    assert (
        c.status == "sufficient"
        and c.substitute.passed == 3
        and c.control.passed == 1
        and c.control.scored == 3
    )
    assert (
        result.instrument_snapshot_id == setup.instrument.snapshot_id
        and result.studied_snapshot_id == setup.studied.snapshot_id
    )
    assert result.usd > 0 and c.usd == result.usd
    # the worker saw the instrument tree and the replay block, never the studied tree
    for request in w.requests:
        assert request["harness_dir"] == str(setup.instrument.tree)
        assert request["task"]["_ahd_replay"]["resume_step"] == 3
    subs = [r for r in w.requests if r["task"]["_ahd_replay"]["arm"] == "substitute"]
    assert len(subs) == 3 and subs[0]["task"]["_ahd_replay"]["substitute"]["tool_calls"][0][
        "function"
    ]["arguments"] == json.dumps({"command": "grep town a"})
    assert (
        setup.ctx.out_dir / "diagnosis" / "replay" / f"{TASK}__r1__a1" / "replay.json"
    ).is_file()
    rows = read_ledger(setup.ledger.path)
    assert {r.arm for r in rows if r.event == "policy"} == {"replay"} and len(
        [r for r in rows if r.event == "policy"]
    ) == 6
    report = result.drift_reports["c3/substitute/k1"]
    assert isinstance(report, dict) and report["status"] == "ok"


def test_economize_skips_control_when_substitute_insufficient(
    setup: Setup, taskset: TaskSet
) -> None:
    failed, reference, alignment = _pair()
    setup.worker.outcomes.update({("substitute", 1): "fail", ("substitute", 2): "fail"})
    result = setup.replayer(k=3).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace=None,
    )
    setup.trace.close()
    c = result.candidates[0]
    assert c.status == "insufficient" and c.control.skipped and c.substitute.passed == 1
    assert result.oracle_status == "unvalidated" and result.oracle_step is None
    assert len(setup.worker.requests) == 3


def test_full_arms_and_control_failure(setup: Setup, taskset: TaskSet) -> None:
    failed, reference, alignment = _pair()
    setup.worker.outcomes.update(
        {("control", 1): "pass", ("control", 2): "pass", ("control", 3): "fail"}
    )
    result = setup.replayer(k=3, economize=False).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace=None,
    )
    setup.trace.close()
    c = result.candidates[0]
    assert c.status == "insufficient" and not c.control.skipped and c.control.passed == 2


def test_unreplayable_prefix(setup: Setup, taskset: TaskSet) -> None:
    failed, reference, alignment = _pair()
    setup.worker.outcomes.update({("substitute", i): "unreplayable" for i in (1, 2, 3)})
    result = setup.replayer(k=3).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace=None,
    )
    setup.trace.close()
    c = result.candidates[0]
    assert c.status == "unreplayable" and c.substitute.unreplayable == 3 and c.control.skipped
    assert result.oracle_status == "unvalidated"
    assert all(r.status == "unreplayable" for r in c.substitute.rollouts)
    rows = read_ledger(setup.ledger.path) if setup.ledger.path.exists() else []
    assert not [r for r in rows if r.event == "policy"]  # nothing to bill: no model call happened


def test_unscored_rollouts_count_against_sufficiency(setup: Setup, taskset: TaskSet) -> None:
    failed, reference, alignment = _pair()
    setup.worker.outcomes.update(
        {
            ("substitute", 3): "unreplayable",
            ("control", 1): "unreplayable",
            ("control", 2): "fail",
            ("control", 3): "fail",
        }
    )
    result = setup.replayer(k=3).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace=None,
    )
    setup.trace.close()
    c = result.candidates[0]
    assert c.substitute.pass_fraction == pytest.approx(2 / 3)
    assert c.control.conservative_pass_fraction == pytest.approx(1 / 3) and c.status == "sufficient"
