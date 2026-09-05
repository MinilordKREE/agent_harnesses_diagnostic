"""Error signals: the REFERENCE arm (reference-guided) and the SYSTEM arm (failed run only).

No reference source: written fresh for ahd (see docs/reuse/M3.md). HarnessEvolve §3.4
"Trajectory Comparison" produces "(s_i, m_i, h_i)" from τ^- and τ^+ and, without a reference,
from τ^- alone. ahd renders the same fields plus a WHERE chosen among rule-table candidates
(``attribution.py``): the model never names a component outside the candidate set, and the
step is the divergence candidate under study (reference arm) or the model's own pick within
the failed run (system arm).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.diagnosis.align import Alignment, Candidate, actions_from_trajectory
from ahd.diagnosis.attribution import AttributionRule, attribute, system_rule
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.render import action_text, condensed
from ahd.diagnosis.schema import (
    CauseVocabulary,
    Diagnosis,
    FailureType,
    How,
    OracleBasis,
    Provenance,
    Severity,
    Where,
    Why,
)
from ahd.errors import TaskFailure
from ahd.harness.components import ComponentManifest
from ahd.tasks.models import Task

PROMPT_DIR = Path("configs/prompts/diagnosis")
FAILURE_TYPE_NOTES: dict[str, str] = {
    "deterministic": (
        "replacing the failed run's action at this step with the reference's action rescued "
        "the run, and re-sampling the policy from the same prefix did not: this step caused "
        "the failure"
    ),
    "stochastic": (
        "re-sampling the policy from the prefix before this step usually passes: the failure "
        "is a policy-level random event, and this is the step where it showed; diagnose what "
        "the harness allowed to happen here rather than why the policy chose this action"
    ),
    "unrepairable": "neither the reference action nor re-sampling rescued the run at any step",
    "unreplayable": "the prefix could not be re-executed faithfully; no counterfactual evidence",
    "unvalidated": "no replay validation ran; this is the first class-level divergence only",
}
_SEVERITIES: tuple[str, ...] = ("low", "medium", "high", "critical")


def load_prompts(directory: Path = PROMPT_DIR) -> dict[str, str]:
    return {
        name: read_text(directory / f"{name}.md") for name in ("reference_signal", "system_signal")
    }


def _severity(value: object) -> Severity:
    text = str(value or "").strip().lower()
    if text not in _SEVERITIES:
        raise TaskFailure(f"severity {value!r} not in {_SEVERITIES}", kind="malformed_model_output")
    return text  # type: ignore[return-value]


def _cause(value: object, vocabulary: CauseVocabulary) -> str:
    try:
        return vocabulary.normalise(value)
    except ValueError as exc:
        raise TaskFailure(str(exc), kind="malformed_model_output") from exc


def _component(value: object, rule: AttributionRule) -> str:
    text = str(value or "").strip()
    if text not in rule.candidates:
        raise TaskFailure(
            f"component {value!r} not among candidates {list(rule.candidates)} ({rule.rule_id})",
            kind="malformed_model_output",
        )
    return text


def _task_prompt(trajectory: dict[str, Any]) -> str:
    for message in trajectory.get("messages", []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _base_provenance(
    task: Task, *, replicate: str, attempt: int, harness_snapshot_id: str
) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "replicate": replicate,
        "attempt": attempt,
        "harness_snapshot_id": harness_snapshot_id,
    }


def reference_signal(
    task: Task,
    *,
    failed_trajectory: dict[str, Any],
    reference_trajectory: dict[str, Any],
    alignment: Alignment,
    candidate: Candidate,
    oracle_validated: bool,
    reference_run: str,
    replicate: str,
    attempt: int,
    harness_snapshot_id: str,
    manifest: ComponentManifest,
    llm: DiagnosisLLM,
    prompt_template: str,
    vocabulary: CauseVocabulary,
    failure_type: FailureType | None = None,
    oracle_step_basis: OracleBasis = "unvalidated",
) -> Diagnosis:
    """One REFERENCE-arm diagnosis for the validated oracle step (owner decision, M3.1: the
    prompt receives the validated step, the divergence type and the failure type)."""
    reference_actions = [
        a for s in actions_from_trajectory(reference_trajectory) for a in s.actions
    ]
    rule = attribute(
        candidate,
        failed_trajectory=failed_trajectory,
        reference_actions=reference_actions,
        failed_exit_reason=alignment.failed_exit_reason,
        manifest=manifest,
    )
    if not rule.candidates:
        raise TaskFailure(f"rule {rule.rule_id} has no eligible candidate", kind="no_candidate")
    prompt = (
        prompt_template.replace("{task_id}", task.id)
        .replace("{task_prompt}", _task_prompt(failed_trajectory))
        .replace("{step}", str(candidate.step))
        .replace("{divergence_type}", candidate.divergence)
        .replace("{divergence_note}", candidate.note)
        .replace("{failure_type}", failure_type or "unvalidated")
        .replace("{oracle_step_basis}", oracle_step_basis)
        .replace("{failure_type_note}", FAILURE_TYPE_NOTES[failure_type or "unvalidated"])
        .replace("{failed_action}", action_text(candidate.failed))
        .replace("{reference_action}", action_text(candidate.reference))
        .replace("{failed_trajectory}", condensed(failed_trajectory, keep_steps=(candidate.step,)))
        .replace(
            "{reference_trajectory}", condensed(reference_trajectory, keep_steps=(candidate.step,))
        )
        .replace("{rule_id}", rule.rule_id)
        .replace("{rule_note}", rule.note)
        .replace(
            "{candidates}", "\n".join(f"- {c}: {manifest.by_id(c).role}" for c in rule.candidates)
        )
        .replace("{cause_labels}", vocabulary.prompt_listing())
    )
    scope = "signal:reference:" + sha256_of(
        {
            "task": task.id,
            "failed": failed_trajectory,
            "reference": reference_trajectory,
            "step": candidate.step,
            "failure_type": failure_type,
            "basis": oracle_step_basis,
        }
    )

    answer = llm.ask_json(prompt, unit_id=task.id, cache_scope=scope)
    data = answer.data
    component = (
        _component(data.get("component"), rule) if len(rule.candidates) > 1 else rule.candidates[0]
    )
    return Diagnosis(
        where=Where(
            component=component,
            step=candidate.step,
            candidates=rule.candidates,
            rule=rule.rule_id,
            attribution="llm" if len(rule.candidates) > 1 else "rule",
        ),
        why=Why(
            cause_label=_cause(data.get("cause_label"), vocabulary),
            mechanism_sentence=str(data.get("mechanism", "")).strip(),
        ),
        how=How(fix_hint=str(data.get("fix_hint", "")).strip()),
        severity=_severity(data.get("severity")),
        source="reference",
        provenance=Provenance(
            **_base_provenance(
                task, replicate=replicate, attempt=attempt, harness_snapshot_id=harness_snapshot_id
            ),
            reference_run=reference_run,
            oracle_step=candidate.step,
            oracle_validated=oracle_validated,
            model=answer.response.model,
            prompt_sha256=answer.prompt_sha256,
            request_sha256=answer.response.request_sha256,
            failure_type=failure_type,
            oracle_step_basis=oracle_step_basis,
        ),
        extra={
            "divergence": candidate.divergence,
            "t_exact": alignment.t_exact,
            "t_class": alignment.t_class,
        },
    )


def system_signal(
    task: Task,
    *,
    failed_trajectory: dict[str, Any],
    exit_reason: str | None,
    score_reason: str,
    replicate: str,
    attempt: int,
    harness_snapshot_id: str,
    manifest: ComponentManifest,
    llm: DiagnosisLLM,
    prompt_template: str,
    vocabulary: CauseVocabulary,
) -> Diagnosis:
    """One SYSTEM-arm diagnosis from the failed run alone."""
    rule = system_rule(
        failed_trajectory, exit_reason=exit_reason, score_reason=score_reason, manifest=manifest
    )
    if not rule.candidates:
        raise TaskFailure(f"rule {rule.rule_id} has no eligible candidate", kind="no_candidate")
    prompt = (
        prompt_template.replace("{task_id}", task.id)
        .replace("{task_prompt}", _task_prompt(failed_trajectory))
        .replace("{failed_trajectory}", condensed(failed_trajectory))
        .replace("{score_reason}", score_reason)
        .replace("{rule_id}", rule.rule_id)
        .replace("{rule_note}", rule.note)
        .replace(
            "{candidates}", "\n".join(f"- {c}: {manifest.by_id(c).role}" for c in rule.candidates)
        )
        .replace("{cause_labels}", vocabulary.prompt_listing())
    )
    scope = "signal:system:" + sha256_of({"task": task.id, "failed": failed_trajectory})
    answer = llm.ask_json(prompt, unit_id=task.id, cache_scope=scope)
    data = answer.data
    component = (
        _component(data.get("component"), rule) if len(rule.candidates) > 1 else rule.candidates[0]
    )
    raw_step = data.get("step")
    step = (
        int(raw_step)
        if isinstance(raw_step, int | float) and not isinstance(raw_step, bool)
        else None
    )
    return Diagnosis(
        where=Where(
            component=component,
            step=step,
            candidates=rule.candidates,
            rule=rule.rule_id,
            attribution="llm" if len(rule.candidates) > 1 else "rule",
        ),
        why=Why(
            cause_label=_cause(data.get("cause_label"), vocabulary),
            mechanism_sentence=str(data.get("mechanism", "")).strip(),
        ),
        how=How(fix_hint=str(data.get("fix_hint", "")).strip()),
        severity=_severity(data.get("severity")),
        source="system",
        provenance=Provenance(
            **_base_provenance(
                task, replicate=replicate, attempt=attempt, harness_snapshot_id=harness_snapshot_id
            ),
            reference_run=None,
            oracle_step=None,
            oracle_validated=False,
            model=answer.response.model,
            prompt_sha256=answer.prompt_sha256,
            request_sha256=answer.response.request_sha256,
        ),
    )
