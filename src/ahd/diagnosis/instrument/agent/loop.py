# Adapted from: RUCAIBox/Evo-Bench @ e1dc9386a193cab1ee8630824c085e5e26d0c730
# Original path: policy_harness_seed/agent/loop.py
# License: Apache-2.0, Copyright 2026 Evo-Bench Authors -- see THIRD_PARTY_NOTICES.md
# Changes (ahd M3 replay instrument): when the task dict carries ``_ahd_replay``, the loop
#   (1) re-executes the recorded prefix actions for state only, comparing fresh and recorded
#       outputs under the drift rule (docs/DEFINITIONS.md) and writing replay_report.json;
#   (2) restores the recorded context and trajectory up to step t*-1;
#   (3) at t* uses the supplied assistant message ("substitute" arm) or calls the model
#       ("control" arm), then continues unmodified.
#   Without ``_ahd_replay`` the loop is the seed loop. This file is a measurement instrument
#   and is never part of an experimental arm. Only stdlib imports are added.
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from evobench.models.client import message_to_dict

from .components import HarnessComponents
from .state import PolicyRolloutResult, RolloutState


def _stop_for_wall_clock(
    state: RolloutState, step: int, elapsed: float, limit: float, *, before_tool: bool
) -> None:
    location = " before tool call" if before_tool else ""
    message = (
        f"wall-clock limit reached{location} at step {step}: "
        f"{elapsed:.1f}s >= {limit}s"
    )
    state.exit_reason = "rollout_wall_clock_timeout"
    state.runtime_errors.append(message)
    state.log(f"STOP {message}", logging.WARNING)


_MASKS = [
    (r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?", "<ts>"),
    (r"\b\d{4}-\d{2}-\d{2}\b", "<date>"),
    (r"\b[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}\b", "<mtime>"),
    (r"\b\d{2}:\d{2}:\d{2}\b", "<clock>"),
    (r"\b1[6-9]\d{8}(?:\.\d+)?\b", "<epoch>"),
    (r"localhost:\d{4,5}|127\.0\.0\.1:\d{4,5}", "<endpoint>"),
    (r"/tmp/[\w./-]+", "<tmp>"),
    (r"\bpid[=: ]+\d+\b", "pid=<pid>"),
]


def _comparable(output: dict) -> str:
    """The part of a tool result that can be compared across runs: stdout+stderr for shell
    results (never ``duration_seconds``), the content string for everything else."""
    payload = output
    if "stdout" not in output and "stderr" not in output:
        try:
            decoded = json.loads(str(output.get("content", "")))
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict) and ("stdout" in decoded or "stderr" in decoded):
            payload = decoded
        else:
            return str(output.get("content", ""))
    return f"{payload.get('stdout', '')}\n--stderr--\n{payload.get('stderr', '')}"


def _mask(text: str, extra_masks: list, workspace: str = "") -> str:
    out = str(text)
    if workspace:
        out = out.replace(workspace, "<workspace>")
    for pattern, replacement in [tuple(m) for m in extra_masks] + _MASKS:
        out = re.sub(pattern, replacement, out)
    return re.sub(r"\s+", " ", out).strip()


def _replay_prefix(replay: dict, state: RolloutState, components: HarnessComponents,
                   command_timeout_seconds: int) -> dict:
    """Re-execute the prefix actions for state; compare with the recorded outputs."""
    from .actions import Action

    drifts, warnings = [], []
    extra_masks = replay.get("masks", [])
    for item in replay.get("prefix_actions", []):
        action = Action(tool_call_id=str(item.get("tool_call_id", "")), name=str(item["name"]),
                        arguments=dict(item.get("arguments") or {}))
        result = components.router.execute(action, state, command_timeout=command_timeout_seconds,
                                           remaining_seconds=None)
        recorded = item.get("recorded_output") or {}
        fresh_exit, recorded_exit = result.get("exit_code"), recorded.get("exit_code")
        fresh_text = _mask(_comparable(result), extra_masks, str(state.workspace))
        recorded_text = _mask(_comparable(recorded), extra_masks, str(state.workspace))
        entry = {"step": item.get("step"), "name": action.name, "mutating": bool(item.get("mutating")),
                 "quoted": bool(item.get("quoted")), "exit_code": [recorded_exit, fresh_exit],
                 "output_equal": fresh_text == recorded_text}
        if action.name == "run_shell_command" and fresh_exit != recorded_exit:
            entry["reason"] = "exit_code_differs"
            drifts.append(entry)
        elif fresh_text != recorded_text and (entry["mutating"] or entry["quoted"]):
            entry["reason"] = "mutating_or_quoted_output_differs"
            drifts.append(entry)
        elif fresh_text != recorded_text:
            warnings.append(entry)
    status = "ok" if not drifts else "unreplayable"
    report = {"status": status, "drifts": drifts, "warnings": warnings,
              "prefix_actions": len(replay.get("prefix_actions", [])),
              "resume_step": replay.get("resume_step"), "arm": replay.get("arm")}
    (state.output_dir / "replay_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report



class _SubstituteMessage:
    """Duck-types the SDK message for ToolRouter.parse (tool_calls with .id/.function)."""

    class _Fn:
        def __init__(self, data):
            self.name = data.get("name", "")
            self.arguments = data.get("arguments", "{}")

    class _Call:
        def __init__(self, data):
            self.id = data.get("id", "")
            self.function = _SubstituteMessage._Fn(data.get("function") or {})

    def __init__(self, data):
        self.content = data.get("content")
        self.tool_calls = [self._Call(c) for c in (data.get("tool_calls") or [])]


def run_policy_loop(
    *,
    components: HarnessComponents,
    system_prompt: str,
    task: dict[str, Any],
    task_workspace: str | Path,
    output_dir: str | Path,
    harness_revision: str,
    model_config_id: str,
    max_steps: int,
    wall_clock_seconds: float,
    command_timeout_seconds: int,
) -> PolicyRolloutResult:
    replay = task.pop("_ahd_replay", None) if isinstance(task, dict) else None
    state = RolloutState.create(
        task=task,
        workspace=Path(task_workspace).resolve(),
        output_dir=Path(output_dir),
        harness_revision=harness_revision,
        model_config_id=model_config_id,
    )
    state.log(
        f"rollout start id={state.rollout_id} task={task.get('id')} "
        f"domain={task.get('domain')} max_steps={max_steps} "
        f"wall_clock_limit={wall_clock_seconds or 'none'}s"
    )
    user_prompt = components.task_analyzer.build_prompt(task, state.workspace)
    components.context.initialize(state, system_prompt, user_prompt)
    components.planner.observe(state, "initialized")

    # --- ahd replay instrument -------------------------------------------------
    start_step = 1
    substitute = None
    if replay:
        report = _replay_prefix(replay, state, components, command_timeout_seconds)
        if report["status"] != "ok":
            state.exit_reason = "unreplayable"
            state.runtime_errors.append(f"unreplayable prefix: {len(report['drifts'])} drift(s)")
            state.log("STOP unreplayable prefix", logging.WARNING)
            return state.finalize()
        state.messages = [dict(m) for m in replay["prefix_messages"]]
        state.trajectory = []
        for entry in replay.get("prefix_trajectory", []):
            restored = dict(entry)
            restored["replayed"] = True
            if restored.get("role") == "assistant":
                # usage was billed to the recorded run; the replay's own ledger must not
                # count it again (metadata.token_usage only counts fresh calls)
                restored["token_usage"] = {"prompt_tokens": 0, "completion_tokens": 0,
                                           "total_tokens": 0}
                restored["model_call_seconds"] = 0.0
            state.trajectory.append(restored)
        start_step = int(replay["resume_step"])
        substitute = replay.get("substitute")
        state.log(f"replay prefix restored: {len(state.messages)} messages, resume at step "
                  f"{start_step}, arm={replay.get('arm')}")
    # ---------------------------------------------------------------------------

    for step in range(start_step, max_steps + 1):
        state.current_step = step
        elapsed = state.elapsed
        if wall_clock_seconds and elapsed >= wall_clock_seconds:
            _stop_for_wall_clock(state, step, elapsed, wall_clock_seconds, before_tool=False)
            break
        if substitute is not None and step == start_step:
            state.log(f"step {step}/{max_steps} - SUBSTITUTED reference action (no model call)")
            assistant_dict = dict(substitute)
            call_elapsed = 0.0
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            assistant = _SubstituteMessage(assistant_dict)
            components.context.assistant(state, assistant_dict)
            state.trajectory.append({
                "step": step,
                "role": "assistant",
                "message": assistant_dict,
                "model_call_seconds": 0.0,
                "token_usage": usage,
                "completed_at": time.time(),
                "substituted": True,
            })
        else:
            state.log(f"step {step}/{max_steps} - calling model (elapsed {elapsed:.1f}s)")
            call_started = time.time()
            try:
                response = components.model.create(
                    messages=components.context.model_messages(state),
                    tools=components.router.schemas(),
                )
            except Exception as exc:
                call_elapsed = time.time() - call_started
                detail = f"{type(exc).__name__}: {exc}"
                state.runtime_errors.append(f"model_call_error step {step}: {detail}")
                state.exit_reason = "model_call_error"
                state.log(
                    f"step {step} - MODEL CALL ERROR after {call_elapsed:.1f}s: {detail}",
                    logging.ERROR,
                )
                break
            call_elapsed = time.time() - call_started
            assistant = response.choices[0].message
            assistant_dict = message_to_dict(assistant)
            components.context.assistant(state, assistant_dict)
            usage = state.add_usage(getattr(response, "usage", None))
            state.trajectory.append({
                "step": step,
                "role": "assistant",
                "message": assistant_dict,
                "model_call_seconds": call_elapsed,
                "token_usage": usage,
                "completed_at": time.time(),
            })
        state.log(
            f"step {step} - model returned in {call_elapsed:.1f}s, "
            f"tokens={usage.get('total_tokens', 0)} "
            f"(prompt={usage.get('prompt_tokens', 0)}, "
            f"completion={usage.get('completion_tokens', 0)})"
        )
        components.planner.observe(state, "assistant", assistant_dict)
        action_list = components.router.parse(assistant)
        if not action_list:
            done = components.completion.no_actions(assistant_dict)
            state.final_answer = done.final_answer
            state.exit_reason = done.exit_reason
            state.log(f"step {step} - no tool calls, finishing with content answer")
            break

        stop = False
        for action in action_list:
            elapsed = state.elapsed
            remaining = wall_clock_seconds - elapsed if wall_clock_seconds else None
            if remaining is not None and remaining <= 0:
                _stop_for_wall_clock(
                    state, step, elapsed, wall_clock_seconds, before_tool=True
                )
                stop = True
                break
            action = components.middleware.before(action, state)
            result = components.router.execute(
                action,
                state,
                command_timeout=command_timeout_seconds,
                remaining_seconds=remaining,
            )
            result = components.middleware.after(action, result, state)
            verified = components.verifier.verify(action, result, state)
            components.context.tool(state, action, verified.result)
            state.trajectory.append({
                "step": step,
                "role": "tool",
                "tool_call": {
                    "id": action.tool_call_id,
                    "name": action.name,
                    "arguments": action.arguments,
                },
                "tool_output": verified.result,
                "completed_at": time.time(),
            })
            components.planner.observe(state, "action", action)
            done = components.completion.after_action(action, verified, state)
            if done and done.stop:
                state.final_answer = done.final_answer
                state.exit_reason = done.exit_reason
                stop = True
                break
        if stop:
            break
    return state.finalize()
