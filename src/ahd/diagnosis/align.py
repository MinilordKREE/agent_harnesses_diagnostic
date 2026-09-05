"""Trajectory alignment: ordered divergence candidates between a failed and a reference run.

No reference source: written fresh for ahd (see docs/reuse/M3.md). HarnessEvolve §3.4 defines
"the first action divergence point t_i* (the earliest step at which the action in τ_i^-
deviates from that in τ_i^+)" and lets the optimiser LLM find it; ahd replaces that with the
deterministic class rules audited in docs/reuse/m3_audit.md section b plus replay validation
(owner decision 1): the output is an ORDERED CANDIDATE LIST, class divergences first
(``t_class``), then ``argument_variant`` steps; ``t_exact`` is recorded alongside. The oracle
step is chosen by replay among these candidates, never here.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value

type ActionClass = Literal["shell_ro", "shell_mut", "tool", "finish", "final"]
type DivergenceType = Literal[
    "different_action",
    "missing_mutation",
    "extra_mutation",
    "premature_finish",
    "late_finish",
    "no_tool_call",
    "error",
    "budget",
    "early",
    "argument_variant",
]

IDENTITY_KEYS: tuple[str, ...] = (
    "task_id",
    "event_id",
    "message_id",
    "email_id",
    "note_id",
    "file_id",
    "id",
    "path",
    "name",
)
_MUTATING_SHELL = re.compile(
    r"(?<![<])>{1,2}(?!&)|\b(cp|mv|rm|rmdir|mkdir|touch|tee|chmod|chown|ln|dd|truncate|unzip|tar|"
    r"git (?:add|commit|checkout|reset|apply)|pip install|npm install|apt(?:-get)?|"
    r"sed\s+-i|perl\s+-i|python3?\s+-c\s+.*\bopen\(.*['\"]w|curl\s+.*-o|wget)\b"
)
_READ_ONLY_TOOL = re.compile(r"^(?:[a-z0-9]+_)?(list|get|search|read|fetch|show|find|query)(?:_|$)")


def as_dict(value: object) -> dict[str, Any]:
    """``value`` when it is a dict, else an empty dict (trajectory entries are loosely typed)."""
    return value if isinstance(value, dict) else {}


class Action(StrictModel):
    step: int
    index: int
    name: str
    klass: ActionClass
    identity: str
    """Class-level key: command targets for mutating shell, identity args for tools."""
    exact: str
    """Exact normalised form (``t_exact`` comparisons)."""
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class StepActions(StrictModel):
    step: int
    actions: tuple[Action, ...]


class Candidate(StrictModel):
    step: int
    divergence: DivergenceType
    failed: tuple[Action, ...]
    reference: tuple[Action, ...]
    note: str


class Alignment(StrictModel):
    task_id: str
    failed_steps: int
    reference_steps: int
    t_exact: int | None
    t_class: int | None
    candidates: tuple[Candidate, ...]
    """Ordered: class divergences by step, then argument variants by step."""
    failed_exit_reason: str | None


# ---------------------------------------------------------------- normalisation


def normalise_command(command: str) -> str:
    text = re.sub(r"\s+", " ", command).strip()
    return re.sub(r"(\s*;\s*|\s*&&\s*true\s*)$", "", text)


def shell_targets(command: str) -> tuple[str, ...]:
    """Paths that a mutating command writes to (best effort: redirection targets and file args)."""
    targets: set[str] = set()
    for m in re.finditer(r">{1,2}\s*([^\s;&|]+)", command):
        targets.add(m.group(1))
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for i, part in enumerate(parts):
        if part in ("cp", "mv", "ln") and len(parts) > i + 2:
            targets.add(parts[-1] if parts[-1] not in ("&&", ";") else parts[i + 2])
        elif (
            part in ("rm", "rmdir", "mkdir", "touch", "chmod", "tee", "truncate")
            and len(parts) > i + 1
        ):
            for arg in parts[i + 1 :]:
                if arg.startswith("-") or arg in ("&&", ";", "||", "|"):
                    if arg in ("&&", ";", "||", "|"):
                        break
                    continue
                targets.add(arg)
    return tuple(sorted(targets))


def classify_shell(command: str) -> ActionClass:
    return "shell_mut" if _MUTATING_SHELL.search(command) else "shell_ro"


def _arguments(raw: Any) -> dict[str, JsonValue]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {"raw": raw}
    converted = to_json_value(raw if isinstance(raw, dict) else {"value": raw})
    return converted if isinstance(converted, dict) else {}


def _identity(args: dict[str, JsonValue]) -> str:
    keys = [k for k in IDENTITY_KEYS if k in args]
    return json.dumps({k: args[k] for k in keys}, sort_keys=True, ensure_ascii=False)


def actions_from_trajectory(trajectory: dict[str, Any]) -> tuple[StepActions, ...]:
    """Per-step actions from Evo-Bench's ``trajectory.json`` (assistant entries only)."""
    steps: list[StepActions] = []
    for entry in trajectory.get("trajectory", []):
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        step = int(entry.get("step", 0))
        message = as_dict(entry.get("message"))
        raw_calls = message.get("tool_calls")
        calls: list[Any] = raw_calls if isinstance(raw_calls, list) else []
        actions: list[Action] = []
        if not calls:
            content = re.sub(r"\s+", " ", str(message.get("content") or "")).strip().lower()
            actions.append(
                Action(
                    step=step,
                    index=0,
                    name="__final__",
                    klass="final",
                    identity="final",
                    exact=f"final:{content}",
                )
            )
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                continue
            function = as_dict(call.get("function"))
            name = str(function.get("name", ""))
            args = _arguments(function.get("arguments"))
            if name == "run_shell_command":
                command = normalise_command(str(args.get("command", "")))
                klass = classify_shell(command)
                identity = (
                    f"{klass}:{','.join(shell_targets(command))}" if klass == "shell_mut" else klass
                )
                exact = f"shell:{command}"
            elif name == "finish":
                answer = re.sub(r"\s+", " ", str(args.get("answer", ""))).strip().lower()
                klass, identity, exact = "finish", "finish", f"finish:{answer}"
            else:
                klass = "tool"
                identity = f"{name}:{_identity(args)}"
                exact = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            actions.append(
                Action(
                    step=step,
                    index=index,
                    name=name,
                    klass=klass,
                    identity=identity,
                    exact=exact,
                    arguments=args,
                )
            )
        steps.append(StepActions(step=step, actions=tuple(actions)))
    return tuple(steps)


# ---------------------------------------------------------------- alignment


def _class_key(actions: Sequence[Action]) -> tuple[str, ...]:
    return tuple(sorted(a.identity for a in actions))


def _exact_key(actions: Sequence[Action]) -> tuple[str, ...]:
    return tuple(a.exact for a in actions)


def _has_mutation(actions: Sequence[Action]) -> bool:
    return any(
        a.klass == "shell_mut" or (a.klass == "tool" and not _READ_ONLY_TOOL.match(a.name))
        for a in actions
    )


def _divergence_type(
    failed: Sequence[Action], reference: Sequence[Action], step: int
) -> tuple[DivergenceType, str]:
    if any(a.klass == "final" for a in failed) and any(a.klass == "finish" for a in reference):
        return (
            "no_tool_call",
            "the failed run ended with a content-only reply where the reference called finish",
        )
    f_final = any(a.klass in ("finish", "final") for a in failed)
    r_final = any(a.klass in ("finish", "final") for a in reference)
    if f_final and not r_final:
        kind: DivergenceType = (
            "no_tool_call" if any(a.klass == "final" for a in failed) else "premature_finish"
        )
        return kind, "the failed run stopped while the reference continued"
    if r_final and not f_final:
        return "late_finish", "the reference finished here; the failed run kept acting"
    if _has_mutation(reference) and not _has_mutation(failed):
        return "missing_mutation", "the reference changed state here; the failed run only inspected"
    if _has_mutation(failed) and not _has_mutation(reference):
        return "extra_mutation", "the failed run changed state where the reference only inspected"
    if step == 1:
        return "early", "the very first actions differ"
    return "different_action", "same kind of step, different target"


def align(
    failed: dict[str, Any],
    reference: dict[str, Any],
    *,
    task_id: str,
    failed_exit_reason: str | None,
) -> Alignment:
    """Ordered divergence candidates between a failed and a reference ``trajectory.json``."""
    f_steps = {s.step: s.actions for s in actions_from_trajectory(failed)}
    r_steps = {s.step: s.actions for s in actions_from_trajectory(reference)}
    f_len, r_len = (max(f_steps) if f_steps else 0), (max(r_steps) if r_steps else 0)
    t_exact: int | None = None
    t_class: int | None = None
    class_candidates: list[Candidate] = []
    variant_candidates: list[Candidate] = []
    last = max(f_len, r_len)
    for step in range(1, last + 1):
        f_actions = f_steps.get(step, ())
        r_actions = r_steps.get(step, ())
        if t_exact is None and _exact_key(f_actions) != _exact_key(r_actions):
            t_exact = step
        if not f_actions and not r_actions:
            continue
        if not f_actions:
            # the failed run ended before the reference did without a final action
            kind: DivergenceType = "error" if failed_exit_reason == "model_call_error" else "budget"
            if failed_exit_reason in ("finished", "assistant_no_tool_call"):
                break
            class_candidates.append(
                Candidate(
                    step=step,
                    divergence=kind,
                    failed=(),
                    reference=r_actions,
                    note=f"failed run ended with {failed_exit_reason} before step {step}",
                )
            )
            if t_class is None:
                t_class = step
            break
        if not r_actions:
            if any(a.klass in ("finish", "final") for a in f_actions):
                break
            class_candidates.append(
                Candidate(
                    step=step,
                    divergence="late_finish",
                    failed=f_actions,
                    reference=(),
                    note="the reference had already finished",
                )
            )
            if t_class is None:
                t_class = step
            break
        if _class_key(f_actions) != _class_key(r_actions):
            kind, note = _divergence_type(f_actions, r_actions, step)
            class_candidates.append(
                Candidate(
                    step=step, divergence=kind, failed=f_actions, reference=r_actions, note=note
                )
            )
            if t_class is None:
                t_class = step
            if kind in ("premature_finish", "no_tool_call", "late_finish"):
                break
        elif _exact_key(f_actions) != _exact_key(r_actions) and any(
            a.klass == "tool" for a in r_actions
        ):
            variant_candidates.append(
                Candidate(
                    step=step,
                    divergence="argument_variant",
                    failed=f_actions,
                    reference=r_actions,
                    note="same tools and identities, different non-identity arguments",
                )
            )
    return Alignment(
        task_id=task_id,
        failed_steps=f_len,
        reference_steps=r_len,
        t_exact=t_exact,
        t_class=t_class,
        candidates=tuple(class_candidates + variant_candidates),
        failed_exit_reason=failed_exit_reason,
    )
