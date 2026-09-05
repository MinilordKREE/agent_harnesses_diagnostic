"""Synthetic trajectories and diagnoses for the M3 unit tests (offline)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ahd.diagnosis.schema import Diagnosis, How, Provenance, Severity, Where, Why

type Step = list[tuple[str, dict[str, Any]]] | str
"""A step is a list of (tool name, arguments) calls, or a plain string = content-only reply."""


def trajectory(
    steps: Sequence[Any],
    *,
    outputs: dict[int, dict[str, Any]] | None = None,
    reasoning: dict[int, str] | None = None,
    prompt: str = "task",
) -> dict[str, Any]:
    """``trajectory.json`` shaped like the seed harness writes it."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": prompt},
    ]
    entries: list[dict[str, Any]] = []
    outputs = outputs or {}
    reasoning = reasoning or {}
    for step, spec in enumerate(steps, start=1):
        if isinstance(spec, str):
            message: dict[str, Any] = {"role": "assistant", "content": spec, "tool_calls": None}
            if step in reasoning:
                message["reasoning_content"] = reasoning[step]
            messages.append(message)
            entries.append(
                {
                    "step": step,
                    "role": "assistant",
                    "message": message,
                    "model_call_seconds": 1.0,
                    "token_usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                    "completed_at": 1000.0 + step,
                }
            )
            continue
        calls = [
            {
                "id": f"call_{step}_{index}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
            }
            for index, (name, args) in enumerate(spec)
        ]
        message = {"role": "assistant", "content": "", "tool_calls": calls}
        if step in reasoning:
            message["reasoning_content"] = reasoning[step]
        messages.append(message)
        entries.append(
            {
                "step": step,
                "role": "assistant",
                "message": message,
                "model_call_seconds": 1.0,
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "completed_at": 1000.0 + step,
            }
        )
        for index, (name, args) in enumerate(spec):
            default: dict[str, Any]
            if name == "run_shell_command":
                default = {
                    "stdout": f"out{step}",
                    "stderr": "",
                    "exit_code": 0,
                    "duration_seconds": 0.1,
                }
            elif name == "finish":
                default = {"answer": args.get("answer", "")}
            else:
                default = {"result": f"{name} ok"}
            output = dict(outputs.get(step, default)) if index == 0 else dict(default)
            output_full = {"content": json.dumps(output, ensure_ascii=False), **output}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{step}_{index}",
                    "content": output_full["content"],
                }
            )
            entries.append(
                {
                    "step": step,
                    "role": "tool",
                    "tool_call": {"id": f"call_{step}_{index}", "name": name, "arguments": args},
                    "tool_output": output_full,
                    "completed_at": 1000.5 + step,
                }
            )
    return {"rollout_id": "rollout-synthetic", "messages": messages, "trajectory": entries}


def sh(command: str) -> tuple[str, dict[str, Any]]:
    return "run_shell_command", {"command": command}


def finish(answer: str) -> tuple[str, dict[str, Any]]:
    return "finish", {"answer": answer}


def diagnosis(
    *,
    task_id: str = "t1",
    replicate: str = "r1",
    attempt: int = 1,
    component: str = "system_prompt",
    step: int | None = 3,
    cause: str = "premature_termination",
    mechanism: str = "The harness stops at the first content-only reply.",
    fix: str = "Re-prompt once before accepting a content-only reply.",
    severity: Severity = "high",
    validated: bool = True,
) -> Diagnosis:
    return Diagnosis(
        where=Where(component=component, step=step, candidates=(component,), rule="R4"),
        why=Why(cause_label=cause, mechanism_sentence=mechanism),
        how=How(fix_hint=fix),
        severity=severity,
        source="reference",
        provenance=Provenance(
            task_id=task_id,
            replicate=replicate,
            attempt=attempt,
            harness_snapshot_id="abc",
            reference_run="ref:r1/1",
            oracle_step=step,
            oracle_validated=validated,
            model="fake",
            prompt_sha256=None,
            request_sha256=None,
        ),
    )
