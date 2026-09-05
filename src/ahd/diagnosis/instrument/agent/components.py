from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .actions import Action, ToolRouter
from .state import RolloutState


@dataclass(frozen=True)
class Verification:
    accepted: bool
    result: dict[str, Any]


@dataclass(frozen=True)
class Completion:
    stop: bool
    exit_reason: str
    final_answer: str = ""


class TaskAnalyzer:
    def build_prompt(self, task: dict[str, Any], workspace: Path) -> str:
        parts = [
            f"Task id: {task['id']}",
            f"Task workspace (your working directory): {workspace}",
            "",
            task["prompt"],
        ]
        if task.get("public_files"):
            parts.extend(["", "Public files:", *[
                f"- {path}" for path in task["public_files"]
            ]])
        return "\n".join(parts)


class PassivePlanner:
    def observe(self, state: RolloutState, event: str, value: Any = None) -> None:
        pass


class AppendOnlyContext:
    def initialize(self, state: RolloutState, system: str, user: str) -> None:
        state.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def model_messages(self, state: RolloutState) -> list[dict[str, Any]]:
        return state.messages

    def assistant(self, state: RolloutState, message: dict[str, Any]) -> None:
        state.messages.append(message)

    def tool(self, state: RolloutState, action: Action, result: dict[str, Any]) -> None:
        state.messages.append({
            "role": "tool",
            "tool_call_id": action.tool_call_id,
            "content": result["content"],
        })


class IdentityMiddleware:
    def before(self, action: Action, state: RolloutState) -> Action:
        return action

    def after(
        self, action: Action, result: dict[str, Any], state: RolloutState
    ) -> dict[str, Any]:
        return result


class AcceptAllVerifier:
    def verify(
        self, action: Action, result: dict[str, Any], state: RolloutState
    ) -> Verification:
        return Verification(True, result)


class SeedCompletionPolicy:
    def no_actions(self, assistant: dict[str, Any]) -> Completion:
        return Completion(True, "assistant_no_tool_call", assistant.get("content") or "")

    def after_action(
        self, action: Action, verification: Verification, state: RolloutState
    ) -> Completion | None:
        if action.name == "finish" and verification.accepted:
            return Completion(True, "finished", str(action.arguments.get("answer", "")))
        return None


@dataclass
class HarnessComponents:
    model: Any
    task_analyzer: TaskAnalyzer
    planner: PassivePlanner
    context: AppendOnlyContext
    router: ToolRouter
    middleware: IdentityMiddleware
    verifier: AcceptAllVerifier
    completion: SeedCompletionPolicy


def build_default_components(model: Any) -> HarnessComponents:
    return HarnessComponents(
        model=model,
        task_analyzer=TaskAnalyzer(),
        planner=PassivePlanner(),
        context=AppendOnlyContext(),
        router=ToolRouter(),
        middleware=IdentityMiddleware(),
        verifier=AcceptAllVerifier(),
        completion=SeedCompletionPolicy(),
    )
