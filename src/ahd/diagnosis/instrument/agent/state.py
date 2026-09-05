from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evobench.models.client import usage_to_dict


@dataclass
class PolicyRolloutResult:
    rollout_id: str
    task_id: str
    task_domain: str
    trajectory_path: Path
    metadata_path: Path
    final_answer: str
    exit_reason: str
    steps: int
    duration_seconds: float
    token_usage: dict[str, int]
    runtime_errors: list[str]


@dataclass
class RolloutState:
    task: dict[str, Any]
    workspace: Path
    output_dir: Path
    harness_revision: str
    model_config_id: str
    rollout_id: str = field(
        default_factory=lambda: f"rollout-{uuid.uuid4().hex[:12]}"
    )
    started_at: float = field(default_factory=time.time)
    messages: list[dict[str, Any]] = field(default_factory=list)
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    runtime_errors: list[str] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    })
    final_answer: str = ""
    exit_reason: str = "max_steps"
    current_step: int = 0
    _logger: logging.Logger | None = field(default=None, init=False, repr=False)
    _handler: logging.FileHandler | None = field(default=None, init=False, repr=False)

    @classmethod
    def create(cls, **kwargs: Any) -> "RolloutState":
        state = cls(**kwargs)
        state.output_dir.mkdir(parents=True, exist_ok=True)
        state._logger = logging.getLogger(f"policy_rollout.{state.rollout_id}")
        state._logger.setLevel(logging.INFO)
        state._logger.propagate = False
        state._handler = logging.FileHandler(
            state.output_dir / "rollout.log", mode="w", encoding="utf-8"
        )
        state._handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        state._logger.addHandler(state._handler)
        return state

    @property
    def elapsed(self) -> float:
        return time.time() - self.started_at

    @property
    def steps(self) -> int:
        return sum(1 for item in self.trajectory if item.get("role") == "assistant")

    def log(self, message: str, level: int = logging.INFO) -> None:
        assert self._logger is not None
        self._logger.log(level, f"[+{self.elapsed:7.1f}s] {message}")

    def add_usage(self, usage: Any) -> dict[str, int]:
        current = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        current.update(usage_to_dict(usage))
        for key, value in current.items():
            if isinstance(value, int):
                self.token_usage[key] = self.token_usage.get(key, 0) + value
        return current

    def finalize(self) -> PolicyRolloutResult:
        duration = self.elapsed
        self.log(
            f"rollout end exit_reason={self.exit_reason} steps={self.steps} "
            f"duration={duration:.1f}s total_tokens={self.token_usage.get('total_tokens', 0)} "
            f"errors={len(self.runtime_errors)}"
        )
        assert self._logger is not None and self._handler is not None
        self._handler.close()
        self._logger.removeHandler(self._handler)
        trajectory_path = self.output_dir / "trajectory.json"
        metadata_path = self.output_dir / "metadata.json"
        self._write(trajectory_path, {
            "rollout_id": self.rollout_id,
            "messages": self.messages,
            "trajectory": self.trajectory,
        })
        self._write(metadata_path, {
            "rollout_id": self.rollout_id,
            "task_id": self.task["id"],
            "task_domain": self.task.get("domain", "unknown"),
            "policy_harness_revision": self.harness_revision,
            "model_config_id": self.model_config_id,
            "duration_seconds": duration,
            "exit_reason": self.exit_reason,
            "final_answer": self.final_answer,
            "artifact_path": str(self.workspace),
            "runtime_errors": self.runtime_errors,
            "token_usage": self.token_usage,
            "log_path": str(self.output_dir / "rollout.log"),
            "steps": self.steps,
        })
        return PolicyRolloutResult(
            rollout_id=self.rollout_id,
            task_id=self.task["id"],
            task_domain=self.task.get("domain", "unknown"),
            trajectory_path=trajectory_path,
            metadata_path=metadata_path,
            final_answer=self.final_answer,
            exit_reason=self.exit_reason,
            steps=self.steps,
            duration_seconds=duration,
            token_usage=self.token_usage,
            runtime_errors=self.runtime_errors,
        )

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
