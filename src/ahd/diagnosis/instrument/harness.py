# Adapted from: RUCAIBox/Evo-Bench @ e1dc9386a193cab1ee8630824c085e5e26d0c730
# Original path: policy_harness_seed/harness.py (verbatim; see README.md in this directory)
# License: Apache-2.0, Copyright 2026 Evo-Bench Authors -- see THIRD_PARTY_NOTICES.md
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evobench.models.client import ModelConfig, OpenAICompatibleClient

from agent import PolicyRolloutResult, build_default_components, run_policy_loop


class PolicyHarness:
    """Componentized, bash-only CodeAct seed harness."""

    def __init__(self, harness_dir: str | Path, model_config: ModelConfig) -> None:
        self.harness_dir = Path(harness_dir).resolve()
        self.config_path = self.harness_dir / "harness.json"
        self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.system_prompt = (
            self.harness_dir / self.config["system_prompt"]
        ).read_text(encoding="utf-8")
        self.max_steps = int(self.config.get("max_steps", 12))
        self.rollout_wall_clock_seconds = float(
            self.config.get("rollout_wall_clock_seconds", 0) or 0
        )
        self.model = OpenAICompatibleClient(model_config)
        self.components = build_default_components(self.model)

    def run_task(
        self,
        *,
        task: dict[str, Any],
        task_workspace: str | Path,
        output_dir: str | Path,
        harness_revision: str,
        model_config_id: str,
        command_timeout_seconds: int = 120,
    ) -> PolicyRolloutResult:
        return run_policy_loop(
            components=self.components,
            system_prompt=self.system_prompt,
            task=task,
            task_workspace=task_workspace,
            output_dir=output_dir,
            harness_revision=harness_revision,
            model_config_id=model_config_id,
            max_steps=self.max_steps,
            wall_clock_seconds=self.rollout_wall_clock_seconds,
            command_timeout_seconds=command_timeout_seconds,
        )
