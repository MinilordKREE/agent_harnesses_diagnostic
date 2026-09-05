"""RunSpec: everything a run needs, frozen and recorded in the manifest.

No reference source: written fresh for ahd (see docs/reuse/M2.md).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ahd.core.config import Budget, PolicyModelSpec, RunConfig, StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.errors import ConfigError
from ahd.tasks.kinds import Split

type RunMode = Literal["normal", "reference"]

BENCHMARK_TRIALS_BY_SOURCE: dict[str, int] = {"claw_eval": 3}
"""Evo-Bench's per-domain trial counts (README:225, runner.py:36-43): Claw 3, everything else 1."""


class RunSpec(StrictModel):
    harness_snapshot_id: str
    split: Split
    task_ids: tuple[str, ...] = Field(min_length=1)
    mode: RunMode = "normal"
    replicate_ids: tuple[str, ...] = Field(default=("r1",), min_length=1)
    budget: Budget = Budget()
    policy: PolicyModelSpec = PolicyModelSpec()
    web_snapshot_id: str | None = None
    mock_today: str | None = None
    reference_max_attempts: int = Field(default=5, ge=1)
    keep_workspaces: bool = False
    arm: str = "seed"
    workers: int = Field(default=1, ge=1)

    @classmethod
    def from_config(
        cls,
        config: RunConfig,
        *,
        harness_snapshot_id: str,
        task_ids: tuple[str, ...],
        mode: RunMode | None = None,
        replicates: int | None = None,
        arm: str | None = None,
        workers: int | None = None,
    ) -> RunSpec:
        if config.tasks is None:
            raise ConfigError("run config has no `tasks` section")
        count = replicates or config.run.replicates
        return cls(
            harness_snapshot_id=harness_snapshot_id,
            split=config.tasks.split,
            task_ids=task_ids,
            mode=mode or config.run.mode,
            replicate_ids=tuple(f"r{k}" for k in range(1, count + 1)),
            budget=config.budget,
            policy=config.policy,
            mock_today=config.run.mock_today,
            reference_max_attempts=config.run.reference_max_attempts,
            keep_workspaces=config.run.keep_workspaces,
            arm=arm or config.run.arm,
            workers=workers or config.run.workers,
        )

    def manifest_view(self) -> dict[str, JsonValue]:
        view = to_json_value(self.model_dump(mode="json"))
        if not isinstance(view, dict):  # pragma: no cover
            raise TypeError("run spec must dump to an object")
        return view
