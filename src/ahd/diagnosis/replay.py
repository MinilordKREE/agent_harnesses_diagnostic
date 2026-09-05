"""Replay validation of divergence candidates (owner decisions 2 and 3; docs/DEFINITIONS.md).

No reference source: written fresh for ahd (see docs/reuse/M3.md). The counterfactual logic
follows the contrastive resampling of CAR (``do_resample``) and Credit Without Ground Truth
as rules, not code (docs/reuse/survey.md). For a failed rollout and its ordered divergence
candidates (``align.py``), each candidate step t (bounded: the first ``max_candidates``) gets
two arms of ``k`` rollouts on the replay instrument:

* ``substitute``: prefix state re-executed, recorded context restored, the reference's
  assistant message at t injected, then the policy continues;
* ``control``: same prefix, the policy re-samples at t.

``sufficient`` iff substitute pass fraction >= 2/3 and control pass fraction <= 1/3, with
unscored rollouts (unreplayable, infra, inapplicable) counted against sufficiency in both
arms. ``oracle_step`` = the earliest sufficient candidate; none -> ``unvalidated``. Every
result records the studied snapshot hash and the instrument hash.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.core.io import atomic_write_text, read_json
from ahd.diagnosis.align import _READ_ONLY_TOOL, Alignment, Candidate, as_dict, classify_shell
from ahd.diagnosis.schema import FailureType, OracleBasis
from ahd.harness.snapshot import HarnessSnapshot
from ahd.runner.records import RolloutRecord
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.tasks.models import Task

type ArmName = Literal["substitute", "control"]
type RolloutStatus = Literal["passed", "failed", "unreplayable", "infra", "inapplicable"]
type CandidateStatus = Literal["sufficient", "insufficient", "unreplayable", "skipped"]

REPLAY_ARM = "replay"
SUBSTITUTE_MIN = 2 / 3
CONTROL_MAX = 1 / 3


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
    usd: float
    classification_control: bool = False
    """The control arm ran only to classify the failure (economize would have skipped it)."""


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
) -> dict[str, Any]:
    """The ``_ahd_replay`` block: prefix actions with recorded outputs, prefix context, the
    resume step and (substitute arm) the reference's assistant message."""
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
    if recorded_workspace:
        masks.append([re.escape(recorded_workspace), "<workspace>"])
    return {
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


def classify(candidates: Sequence[CandidateReplay]) -> FailureType:
    """Owner decision (M3.1): substitute passes and control fails at some step ->
    deterministic; control passes at every tested step -> stochastic; both arms fail ->
    unrepairable; nothing scorable -> unreplayable."""
    replayed = [c for c in candidates if c.status != "skipped"]
    if not replayed or all(c.status == "unreplayable" for c in replayed):
        return "unreplayable"
    if any(c.status == "sufficient" for c in replayed):
        return "deterministic"
    controls = [c.control for c in replayed if not c.control.skipped and c.control.scored > 0]
    if not controls:
        return "unreplayable"
    if all(ctl.pass_fraction > CONTROL_MAX for ctl in controls):
        return "stochastic"
    return "unrepairable"


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
        rollout_dir = self._out_dir / "replay" / key / f"c{candidate.step}" / arm / f"k{index}"
        lane = f"{replicate}-replay-c{candidate.step}-{arm}-k{index}"
        record: RolloutRecord = self._runner.execute_rollout(
            task,
            lane,
            spec=self._spec,
            snapshot=self._instrument,
            rollout_dir=rollout_dir,
            task_extra={"_ahd_replay": payload},
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
                scored = self._runner.score_record(task, record)
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

    def _arm(
        self,
        task: Task,
        *,
        key: str,
        replicate: str,
        candidate: Candidate,
        arm: ArmName,
        payload: dict[str, Any],
        drift_reports: dict[str, JsonValue],
    ) -> ArmResult:
        rollouts = [
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
            for i in range(1, self.k + 1)
        ]
        return ArmResult(
            arm=arm,
            k=self.k,
            rollouts=tuple(rollouts),
            passed=sum(r.status == "passed" for r in rollouts),
            scored=sum(r.status in ("passed", "failed") for r in rollouts),
            unreplayable=sum(r.status == "unreplayable" for r in rollouts),
            infra=sum(r.status == "infra" for r in rollouts),
            inapplicable=sum(r.status == "inapplicable" for r in rollouts),
        )

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
    ) -> ReplayResult:
        key = f"{task.id}__{replicate}__a{attempt}"
        drift_reports: dict[str, JsonValue] = {}
        results: list[CandidateReplay] = []
        for candidate in alignment.candidates[: self.max_candidates]:
            substitute_message = reference_message_at(reference_trajectory, candidate.step)
            if substitute_message is None:
                # the reference had already finished: nothing to substitute
                results.append(
                    CandidateReplay(
                        step=candidate.step,
                        divergence=candidate.divergence,
                        substitute=ArmResult(arm="substitute", k=self.k, skipped=True),
                        control=ArmResult(arm="control", k=self.k, skipped=True),
                        status="skipped",
                        usd=0.0,
                    )
                )
                continue
            sub_payload = prefix_payload(
                failed_trajectory,
                step=candidate.step,
                arm="substitute",
                substitute=substitute_message,
                recorded_workspace=recorded_workspace,
            )
            substitute = self._arm(
                task,
                key=key,
                replicate=replicate,
                candidate=candidate,
                arm="substitute",
                payload=sub_payload,
                drift_reports=drift_reports,
            )
            status: CandidateStatus
            if substitute.unreplayable == self.k:
                control = ArmResult(arm="control", k=self.k, skipped=True)
                status = "unreplayable"
            elif self.economize and substitute.pass_fraction < SUBSTITUTE_MIN:
                control = ArmResult(arm="control", k=self.k, skipped=True)
                status = "insufficient"
            else:
                ctl_payload = prefix_payload(
                    failed_trajectory,
                    step=candidate.step,
                    arm="control",
                    substitute=None,
                    recorded_workspace=recorded_workspace,
                )
                control = self._arm(
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    arm="control",
                    payload=ctl_payload,
                    drift_reports=drift_reports,
                )
                sufficient = (
                    substitute.pass_fraction >= SUBSTITUTE_MIN
                    and control.conservative_pass_fraction <= CONTROL_MAX
                )
                status = "sufficient" if sufficient else "insufficient"
            usd = sum((r.usd or 0.0) for r in substitute.rollouts) + sum(
                (r.usd or 0.0) for r in control.rollouts
            )
            results.append(
                CandidateReplay(
                    step=candidate.step,
                    divergence=candidate.divergence,
                    substitute=substitute,
                    control=control,
                    status=status,
                    usd=usd,
                )
            )
        sufficient_set = tuple(c.step for c in results if c.status == "sufficient")
        if not sufficient_set and not any(
            not c.control.skipped and c.control.scored > 0 for c in results
        ):
            # economize skipped every control arm: run one so the failure can be classified
            for index, c in enumerate(results):
                if c.status != "insufficient" or c.substitute.scored == 0:
                    continue
                candidate = next(x for x in alignment.candidates if x.step == c.step)
                ctl_payload = prefix_payload(
                    failed_trajectory,
                    step=c.step,
                    arm="control",
                    substitute=None,
                    recorded_workspace=recorded_workspace,
                )
                control = self._arm(
                    task,
                    key=key,
                    replicate=replicate,
                    candidate=candidate,
                    arm="control",
                    payload=ctl_payload,
                    drift_reports=drift_reports,
                )
                results[index] = c.model_copy(
                    update={
                        "control": control,
                        "classification_control": True,
                        "usd": c.usd + sum((r.usd or 0.0) for r in control.rollouts),
                    }
                )
                break
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
            k=self.k,
            max_candidates=self.max_candidates,
            economize=self.economize,
            candidates=tuple(results),
            sufficient_set=sufficient_set,
            failure_type=failure_type,
            manifestation_step=manifestation,
            oracle_step=oracle_step,
            oracle_step_basis=basis,
            oracle_status="validated" if oracle_step is not None else "unvalidated",
            usd=sum(c.usd for c in results),
            drift_reports=drift_reports,
        )
        atomic_write_text(
            self._out_dir / "replay" / key / "replay.json", result.model_dump_json(indent=2) + "\n"
        )
        return result
