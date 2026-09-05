from __future__ import annotations

from pathlib import Path

import pytest

from ahd.errors import ConfigError
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import Task, TaskSet
from ahd.tasks.sampling import allocate, stratified_sample
from tests.evobench_fixtures import FAKE_REVISION


def _synthetic_set(counts: dict[str, int]) -> TaskSet:
    from ahd.tasks.kinds import DOMAIN_BY_SOURCE
    from ahd.tasks.models import EvaluatorSpec, TaskResources

    tasks = []
    for source, n in counts.items():
        for i in range(n):
            tasks.append(
                Task(
                    id=f"{source}-{i:03d}",
                    domain=DOMAIN_BY_SOURCE[source],
                    split="validation",
                    source_benchmark=source,
                    prompt="p",
                    evaluator=EvaluatorSpec(type="x", judge_required=False, spec={}),
                    resources=TaskResources(assets_dir=None),
                    metadata={},
                    raw={"id": f"{source}-{i:03d}"},
                )
            )
    return TaskSet(
        dataset_id="d", revision="r", split="validation", suite_name="s", tasks=tuple(tasks)
    )


def test_allocation_is_proportional_with_largest_remainder() -> None:
    assert allocate({"a": 32, "b": 32, "c": 32, "d": 32}, 20) == {"a": 5, "b": 5, "c": 5, "d": 5}
    assert allocate({"a": 128, "b": 128, "c": 64, "d": 64}, 12) == {"a": 4, "b": 4, "c": 2, "d": 2}
    assert allocate({"a": 3, "b": 3, "c": 3}, 4) == {"a": 2, "b": 1, "c": 1}  # tie broken by name
    with pytest.raises(ConfigError):
        allocate({"a": 2}, 3)


def test_sample_is_deterministic_and_seed_sensitive() -> None:
    taskset = _synthetic_set({"browsecomp": 32, "hle": 32, "gdpval": 32, "claw_eval": 32})
    first = stratified_sample(taskset, n=20, seed=0)
    second = stratified_sample(taskset, n=20, seed=0)
    other = stratified_sample(taskset, n=20, seed=1)
    assert first.ids() == second.ids()
    assert first.ids() != other.ids()
    assert first.counts_by_source() == {"browsecomp": 5, "claw_eval": 5, "gdpval": 5, "hle": 5}
    assert len(set(first.ids())) == 20


def test_sample_ignores_input_order() -> None:
    taskset = _synthetic_set({"browsecomp": 10, "hle": 10})
    reversed_set = taskset.model_copy(update={"tasks": tuple(reversed(taskset.tasks))})
    assert (
        stratified_sample(taskset, n=6, seed=3).ids()
        == stratified_sample(reversed_set, n=6, seed=3).ids()
    )


def test_excluded_tasks_are_never_sampled(fake_snapshot: Path) -> None:
    taskset = EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")
    sample = stratified_sample(taskset, n=4, seed=0)
    assert all(not t.excluded for t in sample.tasks)
    assert set(sample.counts_by_source()) == {"browsecomp", "claw_eval", "gdpval", "hle"}
    with pytest.raises(ConfigError, match="cannot sample 5"):
        stratified_sample(taskset, n=5, seed=0)
