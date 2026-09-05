from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahd.errors import ConfigError, InfraError
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.kinds import APEX_EXCLUSION_REASON
from tests.evobench_fixtures import FAKE_REVISION


def _loader(snapshot: Path) -> EvoBenchLoader:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=snapshot)


def test_loads_five_tasks_with_typed_fields(fake_snapshot: Path) -> None:
    taskset = _loader(fake_snapshot).load("validation")
    assert len(taskset) == 5
    assert taskset.split == "validation"
    assert taskset.counts_by_source() == {
        "apex": 1,
        "browsecomp": 1,
        "claw_eval": 1,
        "gdpval": 1,
        "hle": 1,
    }
    assert taskset.counts_by_domain() == {"general": 1, "office": 2, "search": 2}
    bc = taskset.by_id("bc-en-0001")
    assert (bc.domain, bc.source_benchmark, bc.evaluator.type) == (
        "search",
        "browsecomp",
        "llm_as_judge",
    )
    assert bc.evaluator.judge_required is True
    assert bc.evaluator.spec["expected"] == "Springfield"
    assert bc.excluded is False
    hle = taskset.by_id("hle-0000000000000000000000ff")
    assert hle.metadata["answer_type"] == "exactMatch"


def test_apex_is_loaded_but_excluded(fake_snapshot: Path) -> None:
    taskset = _loader(fake_snapshot).load("validation")
    apex = taskset.by_id("apex-0000000000000000000000000000ab")
    assert apex.excluded is True
    assert apex.exclusion_reason == APEX_EXCLUSION_REASON
    assert apex.source_benchmark == "apex"
    assert apex.resources.apex_public is not None
    assert [t.id for t in taskset.select().tasks] == [
        "bc-en-0001",
        "claw-T000_synthetic",
        "gdpval-00000000-0000-0000-0000-000000000001",
        "hle-0000000000000000000000ff",
    ]
    assert len(taskset.select(include_excluded=True)) == 5


def test_gdpval_resources_resolve_against_snapshot(fake_snapshot: Path) -> None:
    task = (
        _loader(fake_snapshot)
        .load("validation")
        .by_id("gdpval-00000000-0000-0000-0000-000000000001")
    )
    assert task.resources.assets_dir == (fake_snapshot / "assets" / "gdpval").resolve()
    assert task.resources.asset_files == {
        "inputs/notes.txt": "abc123abc123abc123abc123abc123ab/notes.txt"
    }
    assert task.resources.public_files == ("inputs/notes.txt",)
    assert "_asset_files_abs" in task.raw  # Evo-Bench's own loader resolved it


def test_raw_record_is_verbatim_and_never_mutated(fake_snapshot: Path) -> None:
    suite_path = fake_snapshot / "suites" / "evobench_validation.json"
    before = suite_path.read_bytes()
    taskset = _loader(fake_snapshot).load("validation")
    released = json.loads(before)["validation"]
    for record in released:
        task = taskset.by_id(record["id"])
        raw = task.to_evobench_dict()
        for key, value in record.items():
            assert raw[key] == value
        raw["prompt"] = "mutated"  # a copy, not the stored record
        assert task.raw["prompt"] == record["prompt"]
    assert suite_path.read_bytes() == before
    view = taskset.by_id("bc-en-0001").public_view()
    assert "scorer" not in view


def test_filters_by_domain_and_source(fake_snapshot: Path) -> None:
    taskset = _loader(fake_snapshot).load("validation")
    assert [t.id for t in taskset.select(domains=["search"]).tasks] == [
        "bc-en-0001",
        "hle-0000000000000000000000ff",
    ]
    assert [t.source_benchmark for t in taskset.select(sources=["gdpval"]).tasks] == ["gdpval"]
    assert taskset.select(domains=["office"]).counts_by_source() == {"gdpval": 1}


def test_evaluation_split(fake_snapshot: Path) -> None:
    taskset = _loader(fake_snapshot).load("evaluation")
    assert len(taskset) == 2
    assert taskset.tasks[0].split == "evaluation"


def test_missing_snapshot_is_infra_error(tmp_path: Path) -> None:
    with pytest.raises(InfraError) as info:
        EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=tmp_path).load("validation")
    assert info.value.kind == "missing_file"


def test_unknown_split_and_id(fake_snapshot: Path) -> None:
    loader = _loader(fake_snapshot)
    with pytest.raises(ConfigError):
        loader.suite_path("test")
    with pytest.raises(ConfigError, match="no task"):
        loader.load("validation").by_id("nope")
