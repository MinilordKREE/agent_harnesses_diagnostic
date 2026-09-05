from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from evobench.policy import injected_tools

import tools

from .state import RolloutState


@dataclass(frozen=True)
class Action:
    tool_call_id: str
    name: str
    arguments: dict[str, Any]


class ToolRouter:
    def schemas(self) -> list[dict[str, Any]]:
        return tools.TOOL_SCHEMAS + injected_tools.schemas()

    def parse(self, assistant_message: Any) -> list[Action]:
        return [
            Action(
                tool_call_id=str(call.id),
                name=str(call.function.name),
                arguments=self._parse_args(call.function.arguments),
            )
            for call in (getattr(assistant_message, "tool_calls", None) or [])
        ]

    def execute(
        self,
        action: Action,
        state: RolloutState,
        *,
        command_timeout: int,
        remaining_seconds: float | None,
    ) -> dict[str, Any]:
        if action.name == "run_shell_command":
            requested = int(action.arguments.get("timeout_seconds") or command_timeout)
            timeout = requested
            if remaining_seconds is not None:
                timeout = min(requested, max(1, int(remaining_seconds)))
            if timeout != requested:
                state.log(
                    f"step {state.current_step} - shell timeout clamped from "
                    f"{requested}s to {timeout}s by remaining rollout wall-clock budget"
                )
            result = tools.run_shell_command(
                command=str(action.arguments.get("command", "")),
                workspace=state.workspace,
                timeout_seconds=timeout,
            )
            duration = result.get("duration_seconds")
            command = str(action.arguments.get("command", ""))
            if result.get("timeout"):
                state.runtime_errors.append(
                    f"shell timeout step {state.current_step} after "
                    f"{duration:.1f}s: {command[:120]}"
                )
                state.log(
                    f"step {state.current_step} - SHELL TIMEOUT after "
                    f"{duration:.1f}s: {command[:200]}",
                    logging.WARNING,
                )
            else:
                state.log(
                    f"step {state.current_step} - shell exit={result.get('exit_code')} "
                    f"in {duration:.1f}s: {command[:200]}"
                )
            return result
        if action.name == "finish":
            state.log(f"step {state.current_step} - finish called")
            return tools.finish_result(str(action.arguments.get("answer", "")))

        result = injected_tools.dispatch(
            action.name, action.arguments, tool_call_id=action.tool_call_id
        )
        if result is None:
            result = {"content": json.dumps(
                {"error": f"unknown tool {action.name}"}, ensure_ascii=False
            )}
            state.runtime_errors.append(f"unknown tool: {action.name}")
            state.log(
                f"step {state.current_step} - UNKNOWN TOOL: {action.name}",
                logging.WARNING,
            )
        else:
            state.log(f"step {state.current_step} - injected tool: {action.name}")
        return result

    @staticmethod
    def _parse_args(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
