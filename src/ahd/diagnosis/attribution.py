"""Component attribution rule table (docs/reuse/m3_audit.md section c, rules R1 to R10).

No reference source: written fresh for ahd (see docs/reuse/M3.md). The rules produce a
deterministic candidate set from ``configs/harness/seed_components.yaml``; the LLM (in
``signal.py``) may only choose among the candidates. Components with ``where_eligible: false``
never appear.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from ahd.core.config import StrictModel
from ahd.diagnosis.align import Action, Candidate, actions_from_trajectory, as_dict
from ahd.harness.components import ComponentManifest


class AttributionRule(StrictModel):
    rule_id: str
    candidates: tuple[str, ...]
    note: str


RULES: dict[str, tuple[str, ...]] = {
    "R1": ("system_prompt", "task_prompt", "context_window", "planner"),
    "R1a": ("observation_shaping", "verifier", "error_handling"),
    "R1b": ("observation_shaping",),
    "R2": ("tool_router", "task_prompt", "context_window"),
    "R2a": ("tool_registry", "system_prompt"),
    "R3": ("completion_policy", "verifier", "system_prompt"),
    "R3a": ("verifier", "observation_shaping"),
    "R4": ("completion_policy", "system_prompt"),
    "R5": ("error_handling", "model_client"),
    "R6": ("budget", "loop", "completion_policy", "context_window"),
    "R7": ("budget", "tool_shell", "error_handling"),
    "R8": ("tool_router", "tool_registry", "system_prompt"),
    "R9": ("planner", "system_prompt", "context_window"),
    "R10": ("task_prompt", "system_prompt"),
}
NOTES: dict[str, str] = {
    "R1": "shell command differs from the reference",
    "R1a": "a prior tool result failed or timed out and the run did not react",
    "R1b": "the observation before the divergence was truncated",
    "R2": "injected tool differs in name or identity arguments",
    "R2a": "the failed run never called a tool the reference relies on",
    "R3": "premature finish while the reference continues",
    "R3a": "finish answer not supported by any observation",
    "R4": "content-only message ended the rollout",
    "R5": "model call error",
    "R6": "budget stop (max steps or wall clock)",
    "R7": "shell timeout at the divergence",
    "R8": "unknown tool name",
    "R9": "reference mutates state here, failed run only inspects",
    "R10": "divergence at the first step",
}


def _tool_entries(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        e
        for e in trajectory.get("trajectory", [])
        if isinstance(e, dict) and e.get("role") == "tool"
    ]


def _observations_before(trajectory: dict[str, Any], step: int) -> list[dict[str, Any]]:
    return [
        as_dict(e.get("tool_output"))
        for e in _tool_entries(trajectory)
        if int(e.get("step", 0)) < step and isinstance(e.get("tool_output"), dict)
    ]


def _unknown_tool_at(trajectory: dict[str, Any], step: int) -> bool:
    """The harness answered a tool call at ``step`` with its unknown-tool error."""
    return any(
        int(e.get("step", 0)) == step
        and "unknown tool" in str(as_dict(e.get("tool_output")).get("content", "")).lower()
        for e in _tool_entries(trajectory)
    )


def _timeout_at(trajectory: dict[str, Any], step: int) -> bool:
    return any(
        int(e.get("step", 0)) == step and bool(as_dict(e.get("tool_output")).get("timeout"))
        for e in _tool_entries(trajectory)
    )


def _all_observation_text(trajectory: dict[str, Any]) -> str:
    """Every non-finish observation (the finish tool merely echoes the answer)."""
    parts: list[str] = []
    for e in _tool_entries(trajectory):
        if as_dict(e.get("tool_call")).get("name") == "finish":
            continue
        parts.append(str(as_dict(e.get("tool_output")).get("content", "")))
    return "\n".join(parts)


def _actions_of(trajectory: dict[str, Any]) -> list[Action]:
    return [a for s in actions_from_trajectory(trajectory) for a in s.actions]


def _finish_unsupported(failed: Sequence[Action], trajectory: dict[str, Any]) -> bool:
    answer = next((str(a.arguments.get("answer", "")) for a in failed if a.klass == "finish"), "")
    digits = re.findall(r"\d[\d.,]*", answer)
    text = _all_observation_text(trajectory)
    return bool(digits) and not any(d in text for d in digits)


def _eligible(rule: str, manifest: ComponentManifest) -> AttributionRule:
    candidates = tuple(
        c for c in RULES[rule] if manifest.by_id(c).where_eligible and manifest.by_id(c).patchable
    )
    return AttributionRule(rule_id=rule, candidates=candidates, note=NOTES[rule])


def attribute(
    candidate: Candidate,
    *,
    failed_trajectory: dict[str, Any],
    reference_actions: Sequence[Action],
    failed_exit_reason: str | None,
    manifest: ComponentManifest,
) -> AttributionRule:
    """Pick the rule for a divergence candidate and return its eligible candidate set."""
    step = candidate.step
    failed = candidate.failed
    kind = candidate.divergence
    prior = _observations_before(failed_trajectory, step)
    rule: str
    if kind == "error" or (failed_exit_reason == "model_call_error" and not failed):
        rule = "R5"
    elif kind == "budget":
        rule = "R6"
    elif _timeout_at(failed_trajectory, step):
        rule = "R7"
    elif kind == "no_tool_call":
        rule = "R4"
    elif kind == "premature_finish":
        rule = "R3a" if _finish_unsupported(failed, failed_trajectory) else "R3"
    elif _unknown_tool_at(failed_trajectory, step):
        rule = "R8"
    elif kind == "early":
        rule = "R10"
    elif kind == "missing_mutation":
        rule = "R9"
    elif any(a.klass == "tool" for a in failed):
        reference_tools = {a.name for a in reference_actions if a.klass == "tool"}
        failed_all = {a.name for a in _actions_of(failed_trajectory)}
        rule = "R2a" if reference_tools - failed_all else "R2"
    else:
        last = prior[-1] if prior else {}
        if last and (
            last.get("timeout")
            or (isinstance(last.get("exit_code"), int) and last.get("exit_code") != 0)
        ):
            rule = "R1a"
        elif "[truncated" in str(last.get("stdout", "")) + str(last.get("stderr", "")):
            rule = "R1b"
        else:
            rule = "R1"
    return _eligible(rule, manifest)


def system_rule(
    trajectory: dict[str, Any],
    *,
    exit_reason: str | None,
    score_reason: str,
    manifest: ComponentManifest,
) -> AttributionRule:
    """Rule for the SYSTEM arm (no reference): the failed run's own evidence only."""
    steps = actions_from_trajectory(trajectory)
    last = steps[-1].actions if steps else ()
    rule: str
    if exit_reason == "model_call_error":
        rule = "R5"
    elif exit_reason in ("max_steps", "rollout_wall_clock_timeout"):
        rule = "R6"
    elif exit_reason == "assistant_no_tool_call":
        rule = "R4"
    elif any(a.klass == "finish" for a in last):
        rule = "R3a" if _finish_unsupported(last, trajectory) else "R3"
    else:
        prior = _observations_before(trajectory, 10**6)
        failed_results = [
            o
            for o in prior
            if o.get("timeout") or (isinstance(o.get("exit_code"), int) and o.get("exit_code") != 0)
        ]
        if failed_results:
            rule = "R1a"
        elif any("[truncated" in str(o.get("stdout", "")) for o in prior):
            rule = "R1b"
        else:
            rule = "R1"
    _ = score_reason
    return _eligible(rule, manifest)


def active_steps(component_id: str, trajectory: dict[str, Any]) -> set[int]:
    """Steps at which a component is active (docs/reuse/m3_audit.md section d)."""
    every: set[int] = set()
    tool_steps: set[int] = set()
    shell_steps: set[int] = set()
    failed_result_steps: set[int] = set()
    last_step = 0
    for entry in trajectory.get("trajectory", []):
        if not isinstance(entry, dict):
            continue
        step = int(entry.get("step", 0))
        last_step = max(last_step, step)
        if entry.get("role") == "assistant":
            every.add(step)
        elif entry.get("role") == "tool":
            tool_steps.add(step)
            if as_dict(entry.get("tool_call")).get("name") == "run_shell_command":
                shell_steps.add(step)
            output = as_dict(entry.get("tool_output"))
            if output.get("timeout") or (
                isinstance(output.get("exit_code"), int) and output.get("exit_code") != 0
            ):
                failed_result_steps.add(step)
    match component_id:
        case "tool_shell":
            return shell_steps
        case "tool_router" | "tool_registry" | "tool_finish":
            return tool_steps
        case "completion_policy":
            return {last_step} if last_step else set()
        case "verifier" | "middleware" | "observation_shaping":
            return tool_steps
        case "error_handling":
            return failed_result_steps or every
        case _:
            return every
