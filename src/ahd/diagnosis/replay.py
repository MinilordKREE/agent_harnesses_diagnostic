"""Replay validation of divergence candidates (owner decisions 2 and 3; docs/DEFINITIONS.md).

No reference source: written fresh for ahd (see docs/reuse/M3.md). The counterfactual logic
follows the contrastive resampling of CAR (``do_resample``) and Credit Without Ground Truth
as rules, not code (docs/reuse/survey.md). For a failed rollout and its ordered divergence
candidates (``align.py``), each candidate step t (bounded: the first ``max_candidates``) gets
two arms of ``k`` rollouts on the replay instrument:

* ``substitute``: prefix state re-executed, recorded context restored, the reference's
  assistant message at t injected, then the policy continues;
* ``control``: same prefix, the policy re-samples at t.

Adaptive schedule (M3.2, docs/DEFINITIONS.md): n in {3, 5, 8, 12} rollouts per arm, same
prefix. After each stage p_sub, p_ctl (unscored rollouts count against both arms) and
d = p_sub - p_ctl decide: validated-positive iff p_sub >= 0.6, p_ctl <= 0.34 and d >= 0.4;
validated-negative iff p_sub <= 0.4 or d <= 0.1; both confirmed only at n >= 5; otherwise the
stage escalates, and a candidate still undecided at n = 12 is ``unresolved``. Economize skips
the control arm only when p_sub <= 0.4 at n = 3 (then negative needs n = 5 of the substitute
arm alone). ``oracle_step`` = the earliest validated-positive candidate; none -> ``unvalidated``.
Pre-M3.2 results (fixed k) are read back with verdicts derived from the same thresholds and
``confirmed: false``. Every result records the studied snapshot hash and the instrument hash.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.core.io import atomic_write_text, read_json
from ahd.diagnosis.align import _READ_ONLY_TOOL, Alignment, Candidate, as_dict, classify_shell
from ahd.diagnosis.schema import FailureType, OracleBasis, Verdict
from ahd.harness.snapshot import HarnessSnapshot
from ahd.runner.records import RolloutRecord
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.tasks.models import Task

type ArmName = Literal["substitute", "control"]
type RolloutStatus = Literal["passed", "failed", "unreplayable", "infra", "inapplicable"]
type CandidateStatus = Literal["sufficient", "insufficient", "unreplayable", "skipped"]
type Threshold = Literal["positive", "negative", "undecided"]
type Decision = Literal["positive", "negative", "undecided", "control_skipped"]

REPLAY_ARM = "replay"
SUBSTITUTE_MIN = 2 / 3
"""Pre-M3.2 sufficiency threshold (fixed k = 3); ``status`` keeps reporting it."""
CONTROL_MAX = 1 / 3
"""Control pass fraction above which a candidate counts as 'the policy recovers' (stochastic)."""
SCHEDULE: tuple[int, ...] = (3, 5, 8, 12)
CONFIRM_N = 5
POSITIVE_SUB_MIN = 0.6
POSITIVE_CTL_MAX = 0.34
POSITIVE_D_MIN = 0.4
NEGATIVE_SUB_MAX = 0.4
NEGATIVE_D_MAX = 0.1
_EPS = 1e-9


def decide(p_sub: float, p_ctl: float) -> Threshold:
    """The M3.2 thresholds on one stage's fractions (confirmation by n is the caller's)."""
    d = p_sub - p_ctl
    if (
        p_sub + _EPS >= POSITIVE_SUB_MIN
        and p_ctl <= POSITIVE_CTL_MAX + _EPS
        and d + _EPS >= POSITIVE_D_MIN
    ):
        return "positive"
    if p_sub <= NEGATIVE_SUB_MAX + _EPS or d <= NEGATIVE_D_MAX + _EPS:
        return "negative"
    return "undecided"


class StageStat(StrictModel):
    n: int
    p_sub: float
    p_ctl: float | None
    d: float | None
    decision: Decision


class ReplayRollout(StrictModel):
    arm: ArmName
    index: int
    rollout_uid: str
    rollout_dir: str
    status: RolloutStatus
    exit_reason: str | None
    steps: int
    usd: float | None
    detail: str | None = None


class ArmResult(StrictModel):
    arm: ArmName
    k: int
    rollouts: tuple[ReplayRollout, ...] = ()
    passed: int = 0
    scored: int = 0
    unreplayable: int = 0
    infra: int = 0
    inapplicable: int = 0
    skipped: bool = False

    @property
    def pass_fraction(self) -> float:
        """Passes over k: unscored rollouts count as non-passes."""
        return self.passed / self.k if self.k else 0.0

    @property
    def conservative_pass_fraction(self) -> float:
        """(passes + unscored) over k: what the control arm is held to."""
        return (self.passed + (self.k - self.scored)) / self.k if self.k else 0.0


class CandidateReplay(StrictModel):
    step: int
    divergence: str
    substitute: ArmResult
    control: ArmResult
    status: CandidateStatus
    """Pre-M3.2 view: ``sufficient`` iff the verdict is positive."""
    usd: float
    classification_control: bool = False
    """The control arm ran only to classify the failure (economize would have skipped it)."""
    verdict: Verdict | None = None
    """M3.2 verdict; ``None`` on results written before the adaptive schedule (derive with
    ``verdict_of``)."""
    n: int | None = None
    confirmed: bool = False
    """True when the verdict was reached at n >= 5 (or unreplayable / unresolved at n = 12)."""
    stages: tuple[StageStat, ...] = ()
    control_skipped_by_economize: bool = False


class ReplayResult(StrictModel):
    task_id: str
    replicate: str
    attempt: int
    failure_key: str
    studied_snapshot_id: str
    studied_tree_sha256: str
    instrument_snapshot_id: str
    instrument_tree_sha256: str
    reference_run: str
    k: int
    max_candidates: int
    economize: bool
    candidates: tuple[CandidateReplay, ...]
    sufficient_set: tuple[int, ...]
    """Validated-positive candidate steps."""
    negative_set: tuple[int, ...] = ()
    unresolved_set: tuple[int, ...] = ()
    schedule: tuple[int, ...] = ()
    """The adaptive schedule used; empty on pre-M3.2 results (fixed ``k``)."""
    failure_type: FailureType
    manifestation_step: int | None
    """The last class candidate: where the failure showed (no_tool_call, premature_finish,
    error, budget, late_finish, or the last different action)."""
    oracle_step: int | None
    oracle_step_basis: OracleBasis
    oracle_status: Literal["validated", "unvalidated"]
    usd: float
    drift_reports: dict[str, JsonValue] = Field(default_factory=dict)


# ---------------------------------------------------------------- prefix construction


def _observation_lines(output: dict[str, Any]) -> list[str]:
    """Distinct lines of an observation (decoded stdout/stderr when present)."""
    parts: list[str] = []
    for key in ("stdout", "stderr"):
        if output.get(key):
            parts.append(str(output[key]))
    if not parts:
        content = str(output.get("content", ""))
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            parts.extend(str(v) for v in decoded.values() if isinstance(v, str))
        else:
            parts.append(content)
    return [ln.strip() for p in parts for ln in p.splitlines() if len(ln.strip()) >= 12]


def _quoted_later(output: dict[str, Any], later_texts: Sequence[str]) -> bool:
    return any(any(line in text for text in later_texts) for line in _observation_lines(output))


def prefix_payload(
    failed: dict[str, Any],
    *,
    step: int,
    arm: ArmName,
    substitute: dict[str, Any] | None,
    recorded_workspace: str | None,
    reference_workspace: str | None = None,
) -> dict[str, Any]:
    """The ``_ahd_replay`` block: prefix actions with recorded outputs, prefix context, the
    resume step and (substitute arm) the reference's assistant message. ``rewrite_paths`` are
    the absolute workspace paths of the failed run and of the reference run; the instrument
    replaces them by the replay workspace inside commands (policies write absolute paths)."""
    entries = [e for e in failed.get("trajectory", []) if isinstance(e, dict)]
    later_texts: dict[int, list[str]] = {}
    for e in entries:
        s = int(e.get("step", 0))
        if e.get("role") == "assistant":
            m = as_dict(e.get("message"))
            texts = [str(m.get("content") or ""), str(m.get("reasoning_content") or "")]
            for call in m.get("tool_calls") or []:
                if isinstance(call, dict):
                    fn = as_dict(call.get("function"))
                    texts.append(str(fn.get("arguments") or ""))
            later_texts.setdefault(s, []).extend(texts)
    prefix_actions: list[dict[str, Any]] = []
    for e in entries:
        s = int(e.get("step", 0))
        if e.get("role") != "tool" or s >= step:
            continue
        call = as_dict(e.get("tool_call"))
        raw_output = e.get("tool_output")
        output: dict[str, Any] = (
            raw_output if isinstance(raw_output, dict) else {"content": str(raw_output or "")}
        )
        name = str(call.get("name", ""))
        args = as_dict(call.get("arguments"))
        if name == "run_shell_command":
            klass = classify_shell(str(args.get("command", "")))
            mutating_prior: bool | None = None if klass == "shell_opaque" else klass == "shell_mut"
        elif name == "finish":
            mutating_prior = False
        else:
            mutating_prior = not _READ_ONLY_TOOL.match(name)
        after = [t for s2, ts in later_texts.items() if s < s2 < step for t in ts]
        content = str(output.get("content", ""))
        prefix_actions.append(
            {
                "step": s,
                "tool_call_id": str(call.get("id", "")),
                "name": name,
                "arguments": args,
                "recorded_output": {
                    "content": content,
                    "exit_code": output.get("exit_code"),
                    "timeout": output.get("timeout"),
                },
                "mutating_prior": mutating_prior,
                "quoted": _quoted_later(output, after),
            }
        )
    messages = [m for m in failed.get("messages", []) if isinstance(m, dict)]
    boundary = len(messages)
    seen = 0
    for index, m in enumerate(messages):
        if m.get("role") == "assistant":
            seen += 1
            if seen == step:
                boundary = index
                break
    prefix_messages = messages[:boundary]
    prefix_trajectory = [e for e in entries if int(e.get("step", 0)) < step]
    masks: list[list[str]] = []
    rewrite: list[str] = []
    for path in (recorded_workspace, reference_workspace):
        if path:
            masks.append([re.escape(path), "<workspace>"])
            rewrite.append(path)
    return {
        "rewrite_paths": rewrite,
        "arm": arm,
        "resume_step": step,
        "prefix_actions": prefix_actions,
        "prefix_messages": prefix_messages,
        "prefix_trajectory": prefix_trajectory,
        "substitute": substitute if arm == "substitute" else None,
        "masks": masks,
    }


def reference_message_at(reference: dict[str, Any], step: int) -> dict[str, Any] | None:
    for e in reference.get("trajectory", []):
        if isinstance(e, dict) and e.get("role") == "assistant" and int(e.get("step", 0)) == step:
            m = e.get("message")
            return dict(m) if isinstance(m, dict) else None
    return None


# ---------------------------------------------------------------- outcome classification


def _substituted_step_failed(trajectory: dict[str, Any], step: int) -> bool:
    """Inapplicable: every substituted tool call at ``step`` failed outright."""
    outputs = [
        e.get("tool_output")
        for e in trajectory.get("trajectory", [])
        if isinstance(e, dict) and e.get("role") == "tool" and int(e.get("step", 0)) == step
    ]
    if not outputs:
        return False
    failed = 0
    for o in outputs:
        if not isinstance(o, dict):
            continue
        if isinstance(o.get("exit_code"), int) and o["exit_code"] != 0:
            failed += 1
            continue
        content = str(o.get("content", ""))
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "error" in parsed and o.get("exit_code") is None:
            failed += 1
    return failed == len(outputs)


def manifestation_step(alignment: Alignment) -> int | None:
    classes = [c for c in alignment.candidates if c.divergence != "argument_variant"]
    pool = classes or list(alignment.candidates)
    return pool[-1].step if pool else None


def verdict_of(candidate: CandidateReplay) -> Verdict:
    """The candidate's verdict. Pre-M3.2 results carry none: it is derived from the recorded
    fractions with the M3.2 thresholds at n = k and stays ``confirmed: false``."""
    if candidate.verdict is not None:
        return candidate.verdict
    if candidate.status == "skipped":
        return "skipped"
    if candidate.status == "unreplayable":
        return "unreplayable"
    p_sub = candidate.substitute.pass_fraction
    if candidate.control.skipped:
        return "negative" if p_sub <= NEGATIVE_SUB_MAX + _EPS else "unresolved"
    decision = decide(p_sub, candidate.control.conservative_pass_fraction)
    return "unresolved" if decision == "undecided" else decision


def is_marginal(candidate: CandidateReplay) -> bool:
    """E0d-B re-opening rule (owner): a fixed-k verdict whose substitute arm passed 2 of 3, or
    whose control arm passed 1 of 3 with substitute >= 2 of 3, or that the M3.2 thresholds leave
    undecided. Candidates that already carry a schedule verdict are never marginal."""
    if candidate.verdict is not None or candidate.status in ("skipped", "unreplayable"):
        return False
    sub = candidate.substitute
    if sub.k != 3:
        return False
    if sub.passed == 2:
        return True
    if candidate.control.skipped:
        return False
    ctl = candidate.control
    held = ctl.passed + (ctl.k - ctl.scored)
    if ctl.k == 3 and held == 1 and sub.passed >= 2:
        return True
    return decide(sub.pass_fraction, ctl.conservative_pass_fraction) == "undecided"


def classify(candidates: Sequence[CandidateReplay]) -> FailureType:
    """Owner decisions (M3.1, M3.2): a validated-positive step -> deterministic; an unresolved
    step and no positive one -> unresolved; control passes at every tested step -> stochastic;
    both arms fail -> unrepairable; nothing scorable -> unreplayable."""
    replayed = [c for c in candidates if c.status != "skipped"]
    if not replayed or all(c.status == "unreplayable" for c in replayed):
        return "unreplayable"
    verdicts = [verdict_of(c) for c in replayed]
    if "positive" in verdicts:
        return "deterministic"
    if "unresolved" in verdicts:
        return "unresolved"
    controls = [c.control for c in replayed if not c.control.skipped and c.control.scored > 0]
    if not controls:
        return "unreplayable"
    if all(ctl.pass_fraction > CONTROL_MAX for ctl in controls):
        return "stochastic"
    return "unrepairable"


def arm_result(arm: ArmName, rollouts: Sequence[ReplayRollout], n: int) -> ArmResult:
    kept = tuple(rollouts[:n])
    return ArmResult(
        arm=arm,
        k=n,
        rollouts=kept,
        passed=sum(r.status == "passed" for r in kept),
        scored=sum(r.status in ("passed", "failed") for r in kept),
        unreplayable=sum(r.status == "unreplayable" for r in kept),
        infra=sum(r.status == "infra" for r in kept),
        inapplicable=sum(r.status == "inapplicable" for r in kept),
    )


class Replayer:
    def __init__(
        self,
        *,
        runner: Runner,
        spec: RunSpec,
        studied: HarnessSnapshot,
        instrument: HarnessSnapshot,
        out_dir: Path,
        reference_run: str,
        k: int = 3,
        max_candidates: int = 5,
        economize: bool = True,
        resume: bool = False,
        subdir: str = "replay",
        workers: int = 1,
    ) -> None:
        self._runner = runner
        self._spec = spec.model_copy(update={"arm": REPLAY_ARM, "keep_workspaces": True})
        self._studied = studied
        self._instrument = instrument
        self._out_dir = out_dir
        self._reference_run = reference_run
        self.k = k
        self.max_candidates = max_candidates
        self.economize = economize
        self.resume = resume
        self.subdir = subdir
        self.workers = max(1, workers)
        """Global bound on concurrent replay rollouts: candidates run in parallel and each
        arm's k rollouts run in parallel, all gated by one semaphore of this size. The
        economize decision stays per candidate (substitute arm before its control arm)."""
        self._slots = threading.Semaphore(self.workers)
        """``replay`` normally; E0 uses ``replay_full`` for the --full-arms headroom subset."""

    def _one(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        arm: ArmName,
        index: int,
        payload: dict[str, Any],
        drift_reports: dict[str, JsonValue],
    ) -> ReplayRollout:
        with self._slots:
            return self._one_unbounded(
                task,
                key=key,
                replicate=replicate,
                candidate=candidate,
                arm=arm,
                index=index,
                payload=payload,
                drift_reports=drift_reports,
            )

    def _one_unbounded(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        arm: ArmName,
        index: int,
        payload: dict[str, Any],
        drift_reports: dict[str, JsonValue],
    ) -> ReplayRollout:
        rollout_dir = self._out_dir / self.subdir / key / f"c{candidate.step}" / arm / f"k{index}"
        lane = f"{replicate}-replay-c{candidate.step}-{arm}-k{index}"
        record: RolloutRecord = self._runner.execute_rollout(
            task,
            lane,
            spec=self._spec,
            snapshot=self._instrument,
            rollout_dir=rollout_dir,
            task_extra={"_ahd_replay": payload},
            resume=self.resume,
        )
        report_path = rollout_dir / "replay_report.json"
        report = read_json(report_path) if report_path.is_file() else None
        if report is not None:
            drift_reports[f"c{candidate.step}/{arm}/k{index}"] = to_json_value(report)
        status: RolloutStatus
        detail: str | None = None
        if record.exit_reason == "unreplayable" or (
            isinstance(report, dict) and report.get("status") == "unreplayable"
        ):
            status = "unreplayable"
            drifts = report.get("drifts") if isinstance(report, dict) else None
            detail = f"{len(drifts) if isinstance(drifts, list) else '?'} drift(s)"
        elif record.error_family == "infra":
            status, detail = "infra", record.error
        else:
            trajectory_path = rollout_dir / "trajectory.json"
            fresh = read_json(trajectory_path) if trajectory_path.is_file() else {}
            if arm == "substitute" and _substituted_step_failed(fresh, candidate.step):
                status, detail = (
                    "inapplicable",
                    "the reference action failed against the prefix state",
                )
            else:
                scored = self._runner.score_record(task, record, resume=self.resume)
                if scored.error_family == "infra":
                    status, detail = "infra", scored.error
                else:
                    passed = bool(scored.score.passed) if scored.score is not None else False
                    status = "passed" if passed else "failed"
                    detail = scored.score.reason[:200] if scored.score is not None else None
                record = scored
        return ReplayRollout(
            arm=arm,
            index=index,
            rollout_uid=record.rollout_uid,
            rollout_dir=str(rollout_dir),
            status=status,
            exit_reason=record.exit_reason,
            steps=record.steps,
            usd=record.usd,
            detail=detail,
        )

    def _rollouts(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        arm: ArmName,
        indices: Sequence[int],
        payload: dict[str, Any],
        drift_reports: dict[str, JsonValue],
    ) -> list[ReplayRollout]:
        indices = list(indices)
        if not indices:
            return []
        if self.workers == 1 or len(indices) == 1:
            return [
                self._one(
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    arm=arm,
                    index=i,
                    payload=payload,
                    drift_reports=drift_reports,
                )
                for i in indices
            ]
        with ThreadPoolExecutor(max_workers=min(self.workers, len(indices))) as pool:
            futures = {
                i: pool.submit(
                    self._one,
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    arm=arm,
                    index=i,
                    payload=payload,
                    drift_reports=drift_reports,
                )
                for i in indices
            }
            return [futures[i].result() for i in indices]

    def _schedule(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        sub_payload: dict[str, Any],
        ctl_payload: dict[str, Any],
        drift_reports: dict[str, JsonValue],
        substitute: list[ReplayRollout],
        control: list[ReplayRollout],
    ) -> CandidateReplay:
        """Run the adaptive schedule for one candidate from the rollouts already in hand
        (empty for a fresh candidate; a pre-M3.2 arm's rollouts when re-opening it)."""
        stages: list[StageStat] = []
        verdict: Verdict | None = None
        confirmed = False
        skipped_by_economize = False
        for n in SCHEDULE:
            if len(substitute) < n:
                substitute.extend(
                    self._rollouts(
                        task,
                        key=key,
                        replicate=replicate,
                        candidate=candidate,
                        arm="substitute",
                        indices=range(len(substitute) + 1, n + 1),
                        payload=sub_payload,
                        drift_reports=drift_reports,
                    )
                )
            sub_res = arm_result("substitute", substitute, n)
            if n == SCHEDULE[0] and sub_res.unreplayable == n:
                ctl_res = (
                    arm_result("control", control, n)
                    if control
                    else ArmResult(arm="control", k=n, skipped=True)
                )
                return CandidateReplay(
                    step=candidate.step,
                    divergence=candidate.divergence,
                    substitute=sub_res,
                    control=ctl_res,
                    status="unreplayable",
                    usd=_usd(sub_res, ctl_res),
                    verdict="unreplayable",
                    n=n,
                    confirmed=True,
                    stages=tuple(stages),
                )
            p_sub = sub_res.pass_fraction
            if (
                self.economize
                and not control
                and n <= CONFIRM_N
                and p_sub <= NEGATIVE_SUB_MAX + _EPS
            ):
                stages.append(
                    StageStat(n=n, p_sub=p_sub, p_ctl=None, d=None, decision="control_skipped")
                )
                if n >= CONFIRM_N:
                    verdict, confirmed, skipped_by_economize = "negative", True, True
                    break
                continue
            if len(control) < n:
                control.extend(
                    self._rollouts(
                        task,
                        key=key,
                        replicate=replicate,
                        candidate=candidate,
                        arm="control",
                        indices=range(len(control) + 1, n + 1),
                        payload=ctl_payload,
                        drift_reports=drift_reports,
                    )
                )
            ctl_res = arm_result("control", control, n)
            p_ctl = ctl_res.conservative_pass_fraction
            decision = decide(p_sub, p_ctl)
            stages.append(
                StageStat(n=n, p_sub=p_sub, p_ctl=p_ctl, d=p_sub - p_ctl, decision=decision)
            )
            if n >= CONFIRM_N and decision != "undecided":
                verdict, confirmed = decision, True
                break
        if verdict is None:
            verdict, confirmed = "unresolved", True
        n_final = stages[-1].n
        sub_res = arm_result("substitute", substitute, n_final)
        ctl_res = (
            ArmResult(arm="control", k=n_final, skipped=True)
            if skipped_by_economize or not control
            else arm_result("control", control, n_final)
        )
        return CandidateReplay(
            step=candidate.step,
            divergence=candidate.divergence,
            substitute=sub_res,
            control=ctl_res,
            status="sufficient" if verdict == "positive" else "insufficient",
            usd=_usd(sub_res, ctl_res),
            verdict=verdict,
            n=n_final,
            confirmed=confirmed,
            stages=tuple(stages),
            control_skipped_by_economize=skipped_by_economize,
        )

    def _payloads(
        self,
        candidate: Candidate,
        *,
        failed_trajectory: dict[str, Any],
        reference_trajectory: dict[str, Any],
        recorded_workspace: str | None,
        reference_workspace: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        substitute_message = reference_message_at(reference_trajectory, candidate.step)
        if substitute_message is None:
            return None  # the reference had already finished: nothing to substitute
        sub_payload = prefix_payload(
            failed_trajectory,
            step=candidate.step,
            arm="substitute",
            substitute=substitute_message,
            recorded_workspace=recorded_workspace,
            reference_workspace=reference_workspace,
        )
        ctl_payload = prefix_payload(
            failed_trajectory,
            step=candidate.step,
            arm="control",
            substitute=None,
            recorded_workspace=recorded_workspace,
            reference_workspace=reference_workspace,
        )
        return sub_payload, ctl_payload

    def _candidate(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        failed_trajectory: dict[str, Any],
        reference_trajectory: dict[str, Any],
        recorded_workspace: str | None,
        drift_reports: dict[str, JsonValue],
        reference_workspace: str | None = None,
        existing: CandidateReplay | None = None,
    ) -> CandidateReplay:
        """One candidate through the schedule; ``existing`` re-opens a pre-M3.2 candidate with
        its rollouts kept (E0d-B)."""
        payloads = self._payloads(
            candidate,
            failed_trajectory=failed_trajectory,
            reference_trajectory=reference_trajectory,
            recorded_workspace=recorded_workspace,
            reference_workspace=reference_workspace,
        )
        if payloads is None:
            return CandidateReplay(
                step=candidate.step,
                divergence=candidate.divergence,
                substitute=ArmResult(arm="substitute", k=SCHEDULE[0], skipped=True),
                control=ArmResult(arm="control", k=SCHEDULE[0], skipped=True),
                status="skipped",
                usd=0.0,
                verdict="skipped",
                n=0,
                confirmed=True,
            )
        sub_payload, ctl_payload = payloads
        substitute = list(existing.substitute.rollouts) if existing is not None else []
        control = (
            list(existing.control.rollouts)
            if existing is not None and not existing.control.skipped
            else []
        )
        return self._schedule(
            task,
            key=key,
            replicate=replicate,
            candidate=candidate,
            sub_payload=sub_payload,
            ctl_payload=ctl_payload,
            drift_reports=drift_reports,
            substitute=substitute,
            control=control,
        )

    def _finish(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        attempt: int,
        alignment: Alignment,
        results: list[CandidateReplay],
        failed_trajectory: dict[str, Any],
        recorded_workspace: str | None,
        reference_workspace: str | None,
        drift_reports: dict[str, JsonValue],
        legacy_k: int | None = None,
    ) -> ReplayResult:
        if not any(verdict_of(c) == "positive" for c in results) and not any(
            not c.control.skipped and c.control.scored > 0 for c in results
        ):
            # economize skipped every control arm: run one (n = CONFIRM_N) so the failure can
            # be classified as stochastic or unrepairable
            for index, c in enumerate(results):
                if verdict_of(c) != "negative" or c.substitute.scored == 0:
                    continue
                candidate = next(x for x in alignment.candidates if x.step == c.step)
                ctl_payload = prefix_payload(
                    failed_trajectory,
                    step=c.step,
                    arm="control",
                    substitute=None,
                    recorded_workspace=recorded_workspace,
                    reference_workspace=reference_workspace,
                )
                rollouts = self._rollouts(
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    arm="control",
                    indices=range(1, CONFIRM_N + 1),
                    payload=ctl_payload,
                    drift_reports=drift_reports,
                )
                control = arm_result("control", rollouts, CONFIRM_N)
                results[index] = c.model_copy(
                    update={
                        "control": control,
                        "classification_control": True,
                        "control_skipped_by_economize": False,
                        "usd": c.usd + sum((r.usd or 0.0) for r in control.rollouts),
                    }
                )
                break
        verdicts = {c.step: verdict_of(c) for c in results}
        sufficient_set = tuple(s for s, v in verdicts.items() if v == "positive")
        negative_set = tuple(s for s, v in verdicts.items() if v == "negative")
        unresolved_set = tuple(s for s, v in verdicts.items() if v == "unresolved")
        failure_type = classify(results)
        manifestation = manifestation_step(alignment)
        oracle_step: int | None
        basis: OracleBasis
        if failure_type == "deterministic":
            oracle_step, basis = min(sufficient_set), "sufficient"
        elif failure_type == "stochastic" and manifestation is not None:
            oracle_step, basis = manifestation, "manifestation"
        else:
            oracle_step, basis = None, "unvalidated"
        result = ReplayResult(
            task_id=task.id,
            replicate=replicate,
            attempt=attempt,
            failure_key=key,
            studied_snapshot_id=self._studied.snapshot_id,
            studied_tree_sha256=self._studied.meta.sha256,
            instrument_snapshot_id=self._instrument.snapshot_id,
            instrument_tree_sha256=self._instrument.meta.sha256,
            reference_run=self._reference_run,
            k=legacy_k if legacy_k is not None else SCHEDULE[0],
            max_candidates=self.max_candidates,
            economize=self.economize,
            candidates=tuple(results),
            sufficient_set=sufficient_set,
            negative_set=negative_set,
            unresolved_set=unresolved_set,
            schedule=SCHEDULE,
            failure_type=failure_type,
            manifestation_step=manifestation,
            oracle_step=oracle_step,
            oracle_step_basis=basis,
            oracle_status="validated" if oracle_step is not None else "unvalidated",
            usd=sum(c.usd for c in results),
            drift_reports=drift_reports,
        )
        atomic_write_text(
            self._out_dir / self.subdir / key / "replay.json",
            result.model_dump_json(indent=2) + "\n",
        )
        return result

    def validate(
        self,
        task: Task,
        *,
        failed_trajectory: dict[str, Any],
        reference_trajectory: dict[str, Any],
        alignment: Alignment,
        replicate: str,
        attempt: int,
        recorded_workspace: str | None,
        reference_workspace: str | None = None,
    ) -> ReplayResult:
        key = f"{task.id}__{replicate}__a{attempt}"
        drift_reports: dict[str, JsonValue] = {}
        candidates = list(alignment.candidates[: self.max_candidates])
        if self.workers == 1 or len(candidates) <= 1:
            results = [
                self._candidate(
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    failed_trajectory=failed_trajectory,
                    reference_trajectory=reference_trajectory,
                    recorded_workspace=recorded_workspace,
                    reference_workspace=reference_workspace,
                    drift_reports=drift_reports,
                )
                for candidate in candidates
            ]
        else:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(candidates))) as pool:
                futures = [
                    pool.submit(
                        self._candidate,
                        task,
                        key=key,
                        replicate=replicate,
                        candidate=candidate,
                        failed_trajectory=failed_trajectory,
                        reference_trajectory=reference_trajectory,
                        recorded_workspace=recorded_workspace,
                        reference_workspace=reference_workspace,
                        drift_reports=drift_reports,
                    )
                    for candidate in candidates
                ]
                results = [f.result() for f in futures]
        return self._finish(
            task,
            key=key,
            replicate=replicate,
            attempt=attempt,
            alignment=alignment,
            results=results,
            failed_trajectory=failed_trajectory,
            recorded_workspace=recorded_workspace,
            reference_workspace=reference_workspace,
            drift_reports=drift_reports,
        )

    def escalate(
        self,
        task: Task,
        existing: ReplayResult,
        *,
        failed_trajectory: dict[str, Any],
        reference_trajectory: dict[str, Any],
        alignment: Alignment,
        recorded_workspace: str | None,
        reference_workspace: str | None = None,
    ) -> ReplayResult:
        """E0d-B: re-open the marginal candidates of a pre-M3.2 result through the schedule,
        keeping their existing rollouts; the other candidates keep their (derived, unconfirmed)
        verdicts. Rewrites ``replay.json`` in place."""
        key = existing.failure_key
        drift_reports: dict[str, JsonValue] = dict(existing.drift_reports)

        def reopen(c: CandidateReplay) -> CandidateReplay:
            candidate = next(x for x in alignment.candidates if x.step == c.step)
            return self._candidate(
                task,
                key=key,
                replicate=existing.replicate,
                candidate=candidate,
                failed_trajectory=failed_trajectory,
                reference_trajectory=reference_trajectory,
                recorded_workspace=recorded_workspace,
                reference_workspace=reference_workspace,
                drift_reports=drift_reports,
                existing=c,
            )

        marginal = [c for c in existing.candidates if is_marginal(c)]
        reopened: dict[int, CandidateReplay] = {}
        if self.workers == 1 or len(marginal) <= 1:
            reopened = {c.step: reopen(c) for c in marginal}
        elif marginal:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(marginal))) as pool:
                futures = {c.step: pool.submit(reopen, c) for c in marginal}
                reopened = {step: f.result() for step, f in futures.items()}
        results: list[CandidateReplay] = []
        for c in existing.candidates:
            if c.step in reopened:
                results.append(reopened[c.step])
            elif c.verdict is not None:
                results.append(c)
            else:
                results.append(c.model_copy(update={"verdict": verdict_of(c), "n": c.substitute.k}))
        return self._finish(
            task,
            key=key,
            replicate=existing.replicate,
            attempt=existing.attempt,
            alignment=alignment,
            results=results,
            failed_trajectory=failed_trajectory,
            recorded_workspace=recorded_workspace,
            reference_workspace=reference_workspace,
            drift_reports=drift_reports,
            legacy_k=existing.k,
        )


def _usd(*arms: ArmResult) -> float:
    return sum((r.usd or 0.0) for arm in arms for r in arm.rollouts)
