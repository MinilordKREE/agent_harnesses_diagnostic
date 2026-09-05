"""Deterministic stratified subsampling of a task set.

No reference source: written fresh for ahd (see docs/reuse/M1.md).

Strata are source benchmarks (finer than domains, so domain proportions follow). Allocation
is proportional with largest-remainder rounding; within a stratum, tasks are sorted by id and
shuffled with a stratum-specific seed derived from the run seed, so the sample for a given
``(seed, n)`` is the same on every machine and independent of input order.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict

from ahd.errors import ConfigError
from ahd.tasks.models import Task, TaskSet


def _stratum_seed(seed: int, stratum: str) -> int:
    digest = hashlib.sha256(f"{seed}:{stratum}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder proportional allocation of ``n`` across strata."""
    total = sum(counts.values())
    if n > total:
        raise ConfigError(f"cannot sample {n} tasks from {total}")
    if n < 0:
        raise ConfigError("sample size must be non-negative")
    quotas = {k: n * v / total for k, v in counts.items()}
    allocation = {k: math.floor(q) for k, q in quotas.items()}
    remainder = n - sum(allocation.values())
    by_fraction = sorted(counts, key=lambda k: (-(quotas[k] - allocation[k]), k))
    for key in by_fraction[:remainder]:
        allocation[key] += 1
    return allocation


def stratified_sample(taskset: TaskSet, *, n: int, seed: int) -> TaskSet:
    """Return a new ``TaskSet`` of ``n`` non-excluded tasks, stratified by source benchmark."""
    eligible = [t for t in taskset.tasks if not t.excluded]
    groups: dict[str, list[Task]] = defaultdict(list)
    for task in eligible:
        groups[task.source_benchmark].append(task)
    allocation = allocate({k: len(v) for k, v in groups.items()}, n)
    chosen: list[Task] = []
    for stratum in sorted(groups):
        ordered = sorted(groups[stratum], key=lambda t: t.id)
        random.Random(_stratum_seed(seed, stratum)).shuffle(ordered)
        chosen.extend(ordered[: allocation[stratum]])
    chosen.sort(key=lambda t: (t.source_benchmark, t.id))
    return taskset.model_copy(update={"tasks": tuple(chosen)})


def sample_per_source(taskset: TaskSet, *, per_source: int, seed: int) -> TaskSet:
    """``per_source`` non-excluded tasks from every source benchmark (E0: equal strata).

    Same shuffle as :func:`stratified_sample`, so the sample for ``per_source + m`` is a
    superset of the sample for ``per_source`` (E0 decision rule D5 grows 30 -> 45 in place).
    """
    eligible = [t for t in taskset.tasks if not t.excluded]
    groups: dict[str, list[Task]] = defaultdict(list)
    for task in eligible:
        groups[task.source_benchmark].append(task)
    chosen: list[Task] = []
    for stratum in sorted(groups):
        if per_source > len(groups[stratum]):
            raise ConfigError(
                f"cannot sample {per_source} tasks from {len(groups[stratum])} in {stratum}"
            )
        ordered = sorted(groups[stratum], key=lambda t: t.id)
        random.Random(_stratum_seed(seed, stratum)).shuffle(ordered)
        chosen.extend(ordered[:per_source])
    chosen.sort(key=lambda t: (t.source_benchmark, t.id))
    return taskset.model_copy(update={"tasks": tuple(chosen)})
