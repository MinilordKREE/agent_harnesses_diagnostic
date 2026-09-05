"""Typed task records. The released Evo-Bench record is kept verbatim in ``Task.raw``.

No reference source: written fresh for ahd (see docs/reuse/M1.md). Field meanings follow
Evo-Bench ``evobench/evaluation/tasks.py`` (Apache-2.0), which is imported, not copied.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue
from ahd.errors import ConfigError
from ahd.tasks.kinds import Domain, SourceBenchmark, Split

JUDGE_SCORER_TYPES: frozenset[str] = frozenset(
    {
        "llm_as_judge",
        "hle_judge",
        "rubric_file_judge",
        "pairwise_vs_seed",
        "claw_grader",
        "apex_grader",
    }
)


class EvaluatorSpec(StrictModel):
    """The released ``scorer`` block. Contains gold data; never shown to a policy."""

    type: str
    judge_required: bool
    spec: dict[str, JsonValue]


class TaskResources(StrictModel):
    assets_dir: Path | None
    asset_files: dict[str, str] = {}
    """Workspace path (``inputs/<name>``) to assets-relative path (``<md5>/<name>``)."""
    public_files: tuple[str, ...] = ()
    claw_public: dict[str, JsonValue] | None = None
    apex_public: dict[str, JsonValue] | None = None


class Task(StrictModel):
    id: str
    domain: Domain
    split: Split
    source_benchmark: SourceBenchmark
    prompt: str
    evaluator: EvaluatorSpec
    resources: TaskResources
    metadata: dict[str, JsonValue]
    excluded: bool = False
    exclusion_reason: str | None = None
    web_snapshot_id: str | None = None
    """Reserved for M2: the frozen web snapshot a rollout of this task must read from."""
    raw: dict[str, JsonValue]
    """The released record exactly as loaded (plus Evo-Bench's own ``_asset_files_abs``)."""

    def to_evobench_dict(self) -> dict[str, Any]:
        """A deep copy of the released record, for Evo-Bench's own scorer and workspace code."""
        return copy.deepcopy(self.raw)

    def public_view(self) -> dict[str, Any]:
        """What a policy may see: Evo-Bench's ``public_task_view`` (strips ``scorer``)."""
        from evobench.evaluation.tasks import public_task_view

        view: dict[str, Any] = public_task_view(self.to_evobench_dict())
        return view


class TaskSet(StrictModel):
    dataset_id: str
    revision: str
    split: Split
    suite_name: str
    tasks: tuple[Task, ...]

    def __len__(self) -> int:
        return len(self.tasks)

    def ids(self) -> tuple[str, ...]:
        return tuple(t.id for t in self.tasks)

    def by_id(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise ConfigError(f"no task {task_id!r} in {self.suite_name} ({self.split})")

    def counts_by_source(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.source_benchmark] = counts.get(task.source_benchmark, 0) + 1
        return dict(sorted(counts.items()))

    def counts_by_domain(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.domain] = counts.get(task.domain, 0) + 1
        return dict(sorted(counts.items()))

    def select(
        self,
        *,
        domains: Iterable[str] | None = None,
        sources: Iterable[str] | None = None,
        include_excluded: bool = False,
    ) -> TaskSet:
        """Filter without reordering or mutating; excluded tasks are dropped unless asked for."""
        domain_set = set(domains) if domains is not None else None
        source_set = set(sources) if sources is not None else None
        kept = tuple(
            t
            for t in self.tasks
            if (domain_set is None or t.domain in domain_set)
            and (source_set is None or t.source_benchmark in source_set)
            and (include_excluded or not t.excluded)
        )
        return self.model_copy(update={"tasks": kept})


class Artifacts(StrictModel):
    """What a rollout produced, as the scorer needs it."""

    workspace: Path
    final_answer: str = ""
    trajectory_path: Path | None = None
    rollout_id: str | None = None


class Score(StrictModel):
    passed: bool
    value: float
    reason: str
    scorer: str
    judge_meta: dict[str, JsonValue] = Field(default_factory=dict)
    task_failure: str | None = None
    """``None`` for a judged verdict; otherwise the task-failure kind (``empty_answer``,
    ``no_deliverable``, ``hackle_violation``) that was also written to the ledger."""
    artifact_sha256: str
