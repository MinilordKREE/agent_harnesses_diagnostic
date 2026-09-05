"""Step-level trajectory events in the M0 trace envelope, from Evo-Bench's files.

No reference source: written fresh for ahd (see docs/reuse/M2.md). Field names follow the
seed harness's ``trajectory.json`` (policy_harness_seed/agent/loop.py lines 84-91, 127-137) and
its ``rollout.log`` format (agent/state.py:76-78, agent/loop.py:92-97, agent/actions.py:71-75),
which is the crash-path fallback: when ``finalize`` never ran, the log is the only per-step
record, and events rebuilt from it carry ``partial: true``.

Event kinds: ``rollout_start``, ``model_call``, ``tool_call``, ``observation``, ``final``,
``rollout_end``. Every step event carries ``step``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.errors import InfraError
from ahd.llm.types import Usage

TRAJECTORY_KINDS: tuple[str, ...] = (
    "rollout_start",
    "model_call",
    "tool_call",
    "observation",
    "final",
    "rollout_end",
)

_LOG_LINE = re.compile(r"^\S+ \[(?P<level>\w+)\] \[\+\s*(?P<elapsed>[\d.]+)s\] (?P<msg>.*)$")
_LOG_MODEL = re.compile(
    r"^step (?P<step>\d+) - model returned in (?P<secs>[\d.]+)s, tokens=(?P<total>\d+) "
    r"\(prompt=(?P<prompt>\d+), completion=(?P<completion>\d+)\)$"
)
_LOG_SHELL = re.compile(
    r"^step (?P<step>\d+) - shell exit=(?P<exit>\S+) in (?P<secs>[\d.]+)s: (?P<cmd>.*)$"
)
_LOG_SHELL_TIMEOUT = re.compile(
    r"^step (?P<step>\d+) - SHELL TIMEOUT after (?P<secs>[\d.]+)s: (?P<cmd>.*)$"
)
_LOG_FINISH = re.compile(r"^step (?P<step>\d+) - finish called$")
_LOG_NO_TOOL = re.compile(r"^step (?P<step>\d+) - no tool calls, finishing with content answer$")
_LOG_MODEL_ERROR = re.compile(
    r"^step (?P<step>\d+) - MODEL CALL ERROR after (?P<secs>[\d.]+)s: (?P<detail>.*)$"
)
_LOG_END = re.compile(
    r"^rollout end exit_reason=(?P<exit>\S+) steps=(?P<steps>\d+) duration=(?P<secs>[\d.]+)s "
    r"total_tokens=(?P<total>\d+) errors=(?P<errors>\d+)$"
)


class TrajectoryEvent(StrictModel):
    kind: str
    payload: dict[str, JsonValue]


def _obj(value: object) -> dict[str, JsonValue]:
    converted = to_json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("payload must be a JSON object")
    return converted


def _usage_from_step(raw: Any) -> Usage:
    data = raw if isinstance(raw, dict) else {}
    prompt = int(data.get("prompt_tokens", 0) or 0)
    completion = int(data.get("completion_tokens", 0) or 0)
    cached = int(data.get("cached_tokens", 0) or 0)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_hit_prompt_tokens=min(cached, prompt),
    )


def events_from_trajectory(
    trajectory: dict[str, Any],
    metadata: dict[str, Any],
    *,
    context: dict[str, JsonValue],
) -> list[TrajectoryEvent]:
    """Rebuild step events from Evo-Bench's ``trajectory.json`` + ``metadata.json``."""
    entries = trajectory.get("trajectory")
    if not isinstance(entries, list):
        raise InfraError("trajectory.json has no `trajectory` list", kind="corrupt_file")
    events: list[TrajectoryEvent] = [
        TrajectoryEvent(
            kind="rollout_start",
            payload=_obj({**context, "rollout_id": trajectory.get("rollout_id"), "partial": False}),
        )
    ]
    for entry in entries:
        if not isinstance(entry, dict):
            raise InfraError("trajectory entry is not an object", kind="corrupt_file")
        step = int(entry.get("step", 0))
        role = entry.get("role")
        if role == "assistant":
            raw_message = entry.get("message")
            message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
            reasoning = message.get("reasoning_content")
            raw_calls = message.get("tool_calls")
            tool_calls: list[Any] = raw_calls if isinstance(raw_calls, list) else []
            events.append(
                TrajectoryEvent(
                    kind="model_call",
                    payload=_obj(
                        {
                            "step": step,
                            "message": message,
                            "reasoning_present": isinstance(reasoning, str)
                            and bool(reasoning.strip()),
                            "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
                            "tool_call_ids": [
                                c.get("id") for c in tool_calls if isinstance(c, dict)
                            ],
                            "usage": entry.get("token_usage"),
                            "model_call_seconds": entry.get("model_call_seconds"),
                            "completed_at": entry.get("completed_at"),
                        }
                    ),
                )
            )
        elif role == "tool":
            raw_call = entry.get("tool_call")
            call: dict[str, Any] = raw_call if isinstance(raw_call, dict) else {}
            raw_output = entry.get("tool_output")
            output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
            events.append(
                TrajectoryEvent(
                    kind="tool_call",
                    payload=_obj(
                        {
                            "step": step,
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "arguments": call.get("arguments"),
                        }
                    ),
                )
            )
            events.append(
                TrajectoryEvent(
                    kind="observation",
                    payload=_obj(
                        {
                            "step": step,
                            "id": call.get("id"),
                            "name": call.get("name"),
                            "output": output,
                            "exit_code": output.get("exit_code"),
                            "timeout": bool(output.get("timeout", False)),
                            "duration_seconds": output.get("duration_seconds"),
                            "completed_at": entry.get("completed_at"),
                        }
                    ),
                )
            )
        else:
            raise InfraError(f"trajectory entry has unknown role {role!r}", kind="corrupt_file")
    events.append(
        TrajectoryEvent(
            kind="final",
            payload=_obj(
                {
                    "exit_reason": metadata.get("exit_reason"),
                    "final_answer": metadata.get("final_answer", ""),
                    "steps": metadata.get("steps"),
                    "duration_seconds": metadata.get("duration_seconds"),
                    "runtime_errors": metadata.get("runtime_errors", []),
                }
            ),
        )
    )
    events.append(
        TrajectoryEvent(
            kind="rollout_end",
            payload=_obj({"token_usage": metadata.get("token_usage", {}), "partial": False}),
        )
    )
    return events


def events_from_rollout_log(
    log_text: str, *, context: dict[str, JsonValue]
) -> list[TrajectoryEvent]:
    """Degraded reconstruction for crashed rollouts; every payload carries ``partial: true``."""
    events: list[TrajectoryEvent] = [
        TrajectoryEvent(kind="rollout_start", payload=_obj({**context, "partial": True}))
    ]
    exit_reason: str | None = None
    steps = 0
    duration: float | None = None
    total_tokens: int | None = None
    for raw in log_text.splitlines():
        line = _LOG_LINE.match(raw)
        if not line:
            continue
        msg = line.group("msg")
        elapsed = float(line.group("elapsed"))
        if m := _LOG_MODEL.match(msg):
            steps = max(steps, int(m.group("step")))
            events.append(
                TrajectoryEvent(
                    kind="model_call",
                    payload=_obj(
                        {
                            "step": int(m.group("step")),
                            "partial": True,
                            "usage": {
                                "prompt_tokens": int(m.group("prompt")),
                                "completion_tokens": int(m.group("completion")),
                                "total_tokens": int(m.group("total")),
                            },
                            "model_call_seconds": float(m.group("secs")),
                            "elapsed_seconds": elapsed,
                        }
                    ),
                )
            )
        elif m := _LOG_SHELL.match(msg):
            payload = {
                "step": int(m.group("step")),
                "partial": True,
                "name": "run_shell_command",
                "arguments": {"command": m.group("cmd")},
                "command_truncated": True,
            }
            events.append(TrajectoryEvent(kind="tool_call", payload=_obj(payload)))
            exit_code = m.group("exit")
            events.append(
                TrajectoryEvent(
                    kind="observation",
                    payload=_obj(
                        {
                            "step": int(m.group("step")),
                            "partial": True,
                            "name": "run_shell_command",
                            "exit_code": int(exit_code)
                            if exit_code.lstrip("-").isdigit()
                            else None,
                            "timeout": False,
                            "duration_seconds": float(m.group("secs")),
                            "elapsed_seconds": elapsed,
                        }
                    ),
                )
            )
        elif m := _LOG_SHELL_TIMEOUT.match(msg):
            events.append(
                TrajectoryEvent(
                    kind="tool_call",
                    payload=_obj(
                        {
                            "step": int(m.group("step")),
                            "partial": True,
                            "name": "run_shell_command",
                            "arguments": {"command": m.group("cmd")},
                            "command_truncated": True,
                        }
                    ),
                )
            )
            events.append(
                TrajectoryEvent(
                    kind="observation",
                    payload=_obj(
                        {
                            "step": int(m.group("step")),
                            "partial": True,
                            "name": "run_shell_command",
                            "exit_code": None,
                            "timeout": True,
                            "duration_seconds": float(m.group("secs")),
                            "elapsed_seconds": elapsed,
                        }
                    ),
                )
            )
        elif m := _LOG_FINISH.match(msg):
            events.append(
                TrajectoryEvent(
                    kind="tool_call",
                    payload=_obj({"step": int(m.group("step")), "partial": True, "name": "finish"}),
                )
            )
        elif m := _LOG_NO_TOOL.match(msg):
            exit_reason = exit_reason or "assistant_no_tool_call"
        elif m := _LOG_MODEL_ERROR.match(msg):
            exit_reason = "model_call_error"
            events.append(
                TrajectoryEvent(
                    kind="model_call",
                    payload=_obj(
                        {
                            "step": int(m.group("step")),
                            "partial": True,
                            "error": m.group("detail"),
                            "model_call_seconds": float(m.group("secs")),
                        }
                    ),
                )
            )
        elif m := _LOG_END.match(msg):
            exit_reason = m.group("exit")
            steps = int(m.group("steps"))
            duration = float(m.group("secs"))
            total_tokens = int(m.group("total"))
    events.append(
        TrajectoryEvent(
            kind="final",
            payload=_obj(
                {
                    "exit_reason": exit_reason,
                    "final_answer": None,
                    "steps": steps,
                    "duration_seconds": duration,
                    "partial": True,
                }
            ),
        )
    )
    events.append(
        TrajectoryEvent(
            kind="rollout_end",
            payload=_obj(
                {
                    "token_usage": {"total_tokens": total_tokens}
                    if total_tokens is not None
                    else {},
                    "partial": True,
                    "reconstructed_from": "rollout.log",
                }
            ),
        )
    )
    return events


class StepUsage(StrictModel):
    steps: int
    usage: Usage
    reasoning_steps: int


def usage_from_events(events: Iterable[TrajectoryEvent]) -> StepUsage:
    """Sum per-step usage over ``model_call`` events (the post-hoc policy ledger source)."""
    prompt = completion = cached = 0
    steps = reasoning = 0
    for event in events:
        if event.kind != "model_call":
            continue
        steps += 1
        usage = _usage_from_step(event.payload.get("usage"))
        prompt += usage.prompt_tokens
        completion += usage.completion_tokens
        cached += usage.cache_hit_prompt_tokens
        if event.payload.get("reasoning_present") is True:
            reasoning += 1
    return StepUsage(
        steps=steps,
        usage=Usage(
            prompt_tokens=prompt, completion_tokens=completion, cache_hit_prompt_tokens=cached
        ),
        reasoning_steps=reasoning,
    )


def reconcile_usage(step_usage: Usage, metadata_usage: dict[str, Any]) -> None:
    """Per-step sums must equal ``metadata.json`` sums; a mismatch is an ``InfraError``."""
    expected_prompt = int(metadata_usage.get("prompt_tokens", 0) or 0)
    expected_completion = int(metadata_usage.get("completion_tokens", 0) or 0)
    if (step_usage.prompt_tokens, step_usage.completion_tokens) != (
        expected_prompt,
        expected_completion,
    ):
        raise InfraError(
            "policy usage mismatch: per-step trajectory sums "
            f"(prompt={step_usage.prompt_tokens}, completion={step_usage.completion_tokens}) "
            f"differ from metadata.json (prompt={expected_prompt}, "
            f"completion={expected_completion})",
            kind="usage_mismatch",
        )
