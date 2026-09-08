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
from ahd.diagnosis.replay import (
    SCHEDULE,
    ArmResult,
    CandidateReplay,
    Replayer,
    ReplayResult,
    ReplayRollout,
    classify,
    is_marginal,
    manifestation_step,
    prefix_payload,
    reference_message_at,
    verdict_of,
)
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
UPDATE_VARIANT: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "priority": "high"},
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
    assert [a["mutating_prior"] for a in payload["prefix_actions"]] == [False, True, False]
    assert [a["quoted"] for a in payload["prefix_actions"]] == [True, False, False]
    assert payload["prefix_actions"][1]["recorded_output"]["exit_code"] == 0
    # messages: system, user, then (assistant, tool) x 3 = 8, cut before the 4th assistant message
    assert len(payload["prefix_messages"]) == 8 and payload["prefix_messages"][-1]["role"] == "tool"
    assert all(int(e["step"]) < 4 for e in payload["prefix_trajectory"])
    assert payload["substitute"]["tool_calls"][0]["function"]["name"] == "todo_update_task"
    assert payload["masks"] == [["/ws/rec", "<workspace>"]]
    assert payload["rewrite_paths"] == ["/ws/rec"]
    both = prefix_payload(
        failed,
        step=4,
        arm="substitute",
        substitute=reference_message_at(reference, 4),
        recorded_workspace="/ws/rec",
        reference_workspace="/ws/ref",
    )
    assert both["rewrite_paths"] == ["/ws/rec", "/ws/ref"] and len(both["masks"]) == 2
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


def _validate(setup: Setup, taskset: TaskSet, **kwargs: Any) -> ReplayResult:
    failed, reference, alignment = _pair()
    result = setup.replayer(**kwargs).validate(
        taskset.by_id(TASK),
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        replicate="r1",
        attempt=1,
        recorded_workspace="/old/ws",
    )
    setup.trace.close()
    return result


def _policy_requests(setup: Setup) -> int:
    return len([r for r in setup.worker.requests])


def test_positive_is_confirmed_at_five_and_bookkeeping(setup: Setup, taskset: TaskSet) -> None:
    w = setup.worker
    w.outcomes.update({("control", i): "fail" for i in range(1, 13)})
    result = _validate(setup, taskset, max_candidates=5)
    assert (
        result.oracle_step == 3
        and result.oracle_status == "validated"
        and result.sufficient_set == (3,)
        and result.failure_type == "deterministic"
        and result.schedule == SCHEDULE
    )
    c = result.candidates[0]
    assert c.status == "sufficient" and c.verdict == "positive" and c.confirmed and c.n == 5
    assert [(st.n, st.decision) for st in c.stages] == [(3, "positive"), (5, "positive")]
    assert c.substitute.k == 5 and c.substitute.passed == 5 and c.control.passed == 0
    assert _policy_requests(setup) == 10  # 5 + 5: n = 3 is never final
    assert (
        result.instrument_snapshot_id == setup.instrument.snapshot_id
        and result.studied_snapshot_id == setup.studied.snapshot_id
    )
    assert result.usd > 0 and c.usd == result.usd
    for request in w.requests:
        assert request["harness_dir"] == str(setup.instrument.tree)
        assert request["task"]["_ahd_replay"]["resume_step"] == 3
    subs = [r for r in w.requests if r["task"]["_ahd_replay"]["arm"] == "substitute"]
    assert len(subs) == 5 and subs[0]["task"]["_ahd_replay"]["substitute"]["tool_calls"][0][
        "function"
    ]["arguments"] == json.dumps({"command": "grep town a"})
    assert (
        setup.ctx.out_dir / "diagnosis" / "replay" / f"{TASK}__r1__a1" / "replay.json"
    ).is_file()
    rows = read_ledger(setup.ledger.path)
    assert {r.arm for r in rows if r.event == "policy"} == {"replay"} and len(
        [r for r in rows if r.event == "policy"]
    ) == 10
    report = result.drift_reports["c3/substitute/k1"]
    assert isinstance(report, dict) and report["status"] == "ok"


def test_economize_negative_by_substitute_alone_then_classifies(
    setup: Setup, taskset: TaskSet
) -> None:
    setup.worker.outcomes.update({("substitute", i): "fail" for i in range(1, 13)})
    result = _validate(setup, taskset)
    c = result.candidates[0]
    assert c.verdict == "negative" and c.confirmed and c.n == 5 and c.status == "insufficient"
    assert [st.decision for st in c.stages] == ["control_skipped", "control_skipped"]
    # the control arm was skipped by economize (p_sub <= 0.4 at n = 3, negative at n = 5 of the
    # substitute arm alone); with no positive step one control arm runs so the failure can be
    # classified (it passes 5/5 here -> stochastic)
    assert c.classification_control and not c.control.skipped and c.control.passed == 5
    assert result.failure_type == "stochastic" and result.negative_set == (3,)
    assert result.oracle_step == 3 and result.oracle_step_basis == "manifestation"
    assert _policy_requests(setup) == 10


def test_economize_runs_control_when_the_substitute_recovers_at_five(
    setup: Setup, taskset: TaskSet
) -> None:
    setup.worker.outcomes.update(
        {("substitute", 2): "fail", ("substitute", 3): "fail"}
        | {("control", i): "fail" for i in range(1, 13)}
    )
    result = _validate(setup, taskset)
    c = result.candidates[0]
    assert [(st.n, st.decision) for st in c.stages] == [(3, "control_skipped"), (5, "positive")]
    assert c.verdict == "positive" and c.confirmed and not c.control_skipped_by_economize
    assert c.substitute.passed == 3 and c.substitute.k == 5 and c.control.k == 5
    assert result.failure_type == "deterministic" and _policy_requests(setup) == 10


def test_negative_by_small_difference_is_stochastic(setup: Setup, taskset: TaskSet) -> None:
    result = _validate(setup, taskset)  # both arms pass everything: d = 0
    c = result.candidates[0]
    assert [(st.n, st.decision) for st in c.stages] == [(3, "negative"), (5, "negative")]
    assert c.verdict == "negative" and c.confirmed and c.n == 5
    assert result.failure_type == "stochastic" and result.oracle_step_basis == "manifestation"
    assert _policy_requests(setup) == 10


def test_undecided_at_five_escalates_to_eight(setup: Setup, taskset: TaskSet) -> None:
    setup.worker.outcomes.update(
        {("substitute", 4): "fail", ("substitute", 5): "fail"}
        | {("control", i): "fail" for i in (1, 3, 4, 6, 7, 8)}
    )
    result = _validate(setup, taskset)
    c = result.candidates[0]
    assert [(st.n, st.decision) for st in c.stages] == [
        (3, "positive"),
        (5, "undecided"),
        (8, "positive"),
    ]
    assert c.stages[1].p_sub == pytest.approx(0.6) and c.stages[1].p_ctl == pytest.approx(0.4)
    assert c.verdict == "positive" and c.n == 8 and result.failure_type == "deterministic"
    assert _policy_requests(setup) == 16


def test_still_undecided_at_twelve_is_unresolved(setup: Setup, taskset: TaskSet) -> None:
    sub_pass = {1, 2, 3, 6, 9, 12}
    ctl_pass = {2, 4, 7, 10}
    setup.worker.outcomes.update(
        {("substitute", i): "pass" if i in sub_pass else "fail" for i in range(1, 13)}
        | {("control", i): "pass" if i in ctl_pass else "fail" for i in range(1, 13)}
    )
    result = _validate(setup, taskset)
    c = result.candidates[0]
    assert [st.n for st in c.stages] == [3, 5, 8, 12]
    assert [st.decision for st in c.stages][1:] == ["undecided", "undecided", "undecided"]
    assert c.verdict == "unresolved" and c.confirmed and c.n == 12 and c.status == "insufficient"
    assert result.failure_type == "unresolved" and result.unresolved_set == (3,)
    assert result.oracle_step is None and result.oracle_status == "unvalidated"
    assert _policy_requests(setup) == 24


def test_unrepairable_when_both_arms_fail(setup: Setup, taskset: TaskSet) -> None:
    setup.worker.outcomes.update(
        {("substitute", i): "fail" for i in range(1, 13)}
        | {("control", i): "fail" for i in range(1, 13)}
    )
    result = _validate(setup, taskset)
    assert result.failure_type == "unrepairable"
    assert result.oracle_step is None and result.oracle_step_basis == "unvalidated"
    assert result.candidates[0].classification_control and _policy_requests(setup) == 10


def test_full_arms_run_both_arms_at_every_stage(setup: Setup, taskset: TaskSet) -> None:
    setup.worker.outcomes.update({("substitute", i): "fail" for i in range(1, 13)})
    result = _validate(setup, taskset, economize=False)
    c = result.candidates[0]
    assert [(st.n, st.decision) for st in c.stages] == [(3, "negative"), (5, "negative")]
    assert not c.control.skipped and c.control.passed == 5 and not c.classification_control
    assert result.failure_type == "stochastic" and _policy_requests(setup) == 10


def test_unreplayable_prefix(setup: Setup, taskset: TaskSet) -> None:
    setup.worker.outcomes.update({("substitute", i): "unreplayable" for i in (1, 2, 3)})
    result = _validate(setup, taskset)
    c = result.candidates[0]
    assert c.status == "unreplayable" and c.substitute.unreplayable == 3 and c.control.skipped
    assert c.verdict == "unreplayable" and c.confirmed and c.n == 3
    assert result.oracle_status == "unvalidated" and result.failure_type == "unreplayable"
    assert all(r.status == "unreplayable" for r in c.substitute.rollouts)
    rows = read_ledger(setup.ledger.path) if setup.ledger.path.exists() else []
    assert not [r for r in rows if r.event == "policy"]  # nothing to bill: no model call happened


def test_unscored_rollouts_count_against_both_arms(setup: Setup, taskset: TaskSet) -> None:
    setup.worker.outcomes.update(
        {("substitute", 3): "unreplayable", ("control", 1): "unreplayable"}
        | {("control", i): "fail" for i in range(2, 13)}
    )
    result = _validate(setup, taskset)
    c = result.candidates[0]
    first = c.stages[0]
    assert first.p_sub == pytest.approx(2 / 3) and first.p_ctl == pytest.approx(1 / 3)
    assert first.decision == "undecided"  # d = 1/3 < 0.4
    assert c.stages[1].decision == "positive" and c.verdict == "positive" and c.n == 5


def _legacy(sub_passed: int, ctl_passed: int | None, *, unscored_ctl: int = 0) -> CandidateReplay:
    substitute = ArmResult(arm="substitute", k=3, passed=sub_passed, scored=3)
    control = (
        ArmResult(arm="control", k=3, skipped=True)
        if ctl_passed is None
        else ArmResult(arm="control", k=3, passed=ctl_passed, scored=3 - unscored_ctl)
    )
    sufficient = ctl_passed is not None and sub_passed >= 2 and ctl_passed <= 1
    return CandidateReplay(
        step=3,
        divergence="premature_finish",
        substitute=substitute,
        control=control,
        status="sufficient" if sufficient else "insufficient",
        usd=0.0,
    )


def test_legacy_verdicts_and_the_marginal_rule() -> None:
    cases = [
        (_legacy(3, 0), "positive", False),
        (_legacy(2, 0), "positive", True),  # substitute 2/3
        (_legacy(2, 1), "unresolved", True),  # d = 1/3
        (_legacy(3, 1), "positive", True),  # control 1/3 with substitute >= 2/3
        (_legacy(3, 2), "unresolved", True),  # undecided under the M3.2 thresholds
        (_legacy(1, None), "negative", False),
        (_legacy(2, None), "unresolved", True),
        (_legacy(0, 0), "negative", False),
        (_legacy(0, 3), "negative", False),
    ]
    for candidate, verdict, marginal in cases:
        assert verdict_of(candidate) == verdict, candidate
        assert is_marginal(candidate) == marginal, candidate
        assert not candidate.confirmed
    settled = _legacy(2, 0).model_copy(update={"verdict": "positive", "n": 5, "confirmed": True})
    assert not is_marginal(settled)
    assert classify([_legacy(3, 0)]) == "deterministic"
    assert classify([_legacy(3, 2)]) == "unresolved"
    assert classify([_legacy(0, 3)]) == "stochastic" and classify([_legacy(0, 0)]) == "unrepairable"


def _rollout(arm: str, index: int, status: str) -> ReplayRollout:
    return ReplayRollout(
        arm=arm,
        index=index,
        rollout_uid=f"legacy-{arm}-{index}",
        rollout_dir=f"/legacy/{arm}/k{index}",
        status=status,
        exit_reason="finished",
        steps=3,
        usd=0.01,
    )


def test_escalate_reopens_marginal_candidates_and_keeps_their_rollouts(
    setup: Setup, taskset: TaskSet
) -> None:
    failed, reference, alignment = _pair()
    subs = [_rollout("substitute", i, s) for i, s in enumerate(("passed", "passed", "failed"), 1)]
    ctls = [_rollout("control", i, "failed") for i in (1, 2, 3)]
    legacy = CandidateReplay(
        step=3,
        divergence=alignment.candidates[0].divergence,
        substitute=ArmResult(arm="substitute", k=3, rollouts=tuple(subs), passed=2, scored=3),
        control=ArmResult(arm="control", k=3, rollouts=tuple(ctls), passed=0, scored=3),
        status="sufficient",
        usd=0.06,
    )
    existing = ReplayResult(
        task_id=TASK,
        replicate="r1",
        attempt=1,
        failure_key=f"{TASK}__r1__a1",
        studied_snapshot_id="s",
        studied_tree_sha256="t",
        instrument_snapshot_id="i",
        instrument_tree_sha256="u",
        reference_run="ref-run",
        k=3,
        max_candidates=5,
        economize=True,
        candidates=(legacy,),
        sufficient_set=(3,),
        failure_type="deterministic",
        manifestation_step=3,
        oracle_step=3,
        oracle_step_basis="sufficient",
        oracle_status="validated",
        usd=0.06,
    )
    assert is_marginal(legacy)
    setup.worker.outcomes.update({("control", 4): "fail", ("control", 5): "fail"})
    result = setup.replayer().escalate(
        taskset.by_id(TASK),
        existing,
        failed_trajectory=failed,
        reference_trajectory=reference,
        alignment=alignment,
        recorded_workspace="/old/ws",
    )
    setup.trace.close()
    c = result.candidates[0]
    assert c.verdict == "positive" and c.confirmed and c.n == 5
    assert [r.rollout_uid for r in c.substitute.rollouts[:3]] == [r.rollout_uid for r in subs]
    assert c.substitute.passed == 4 and c.control.passed == 0
    assert _policy_requests(setup) == 4  # k4, k5 of each arm only
    assert result.k == 3 and result.schedule == SCHEDULE and result.failure_type == "deterministic"
    assert (
        setup.ctx.out_dir / "diagnosis" / "replay" / f"{TASK}__r1__a1" / "replay.json"
    ).is_file()


def test_manifestation_step_is_the_last_class_candidate() -> None:
    failed = trajectory([[LIST], [UPDATE_VARIANT], [LIST], [finish("x")]])
    reference = trajectory([[LIST], [UPDATE], [UPDATE], [finish("x")]])
    alignment = align(failed, reference, task_id=TASK, failed_exit_reason="finished")
    assert [c.divergence for c in alignment.candidates] == ["missing_mutation", "argument_variant"]
    assert manifestation_step(alignment) == 3  # not the argument variant at step 2
    empty = align(failed, failed, task_id=TASK, failed_exit_reason="finished")
    assert manifestation_step(empty) is None
    assert classify(()) == "unreplayable"
