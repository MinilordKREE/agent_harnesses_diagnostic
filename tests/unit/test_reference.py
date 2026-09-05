from __future__ import annotations

from pathlib import Path

import pytest

from ahd.errors import ConfigError, InfraError
from ahd.runner.reference import (
    REFERENCE_MARKER,
    gold_text,
    load_template,
    render_reference_block,
    with_reference,
)
from ahd.tasks.evobench import EvoBenchLoader
from tests.conftest import REPO_ROOT
from tests.evobench_fixtures import FAKE_REVISION


def test_template_and_gold_per_source(fake_snapshot: Path, tmp_path: Path) -> None:
    template = load_template(REPO_ROOT / "configs" / "harness" / "reference_block.md")
    taskset = EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")
    bc = taskset.by_id("bc-en-0001")
    assert gold_text(bc, claw_repo=None) == "Expected answer: Springfield"
    gdp = taskset.by_id("gdpval-00000000-0000-0000-0000-000000000001")
    rubric = gold_text(gdp, claw_repo=None)
    assert "(2) Delivers a .docx" in rubric and "threshold 0.6" in rubric
    claw_repo = tmp_path / "claw"
    (claw_repo / "tasks" / "T000_synthetic").mkdir(parents=True)
    (claw_repo / "tasks" / "T000_synthetic" / "task.yaml").write_text(
        "task_id: T000_synthetic\nscoring_components:\n  - name: listing\n    weight: 0.5\n"
        "safety_checks:\n  - name: no_delete\nreference_solution: SECRET\n",
        encoding="utf-8",
    )
    claw = taskset.by_id("claw-T000_synthetic")
    text = gold_text(claw, claw_repo=claw_repo)
    assert "scoring_components" in text and "no_delete" in text and "SECRET" not in text
    with pytest.raises(InfraError):
        gold_text(claw, claw_repo=None)
    apex = taskset.by_id("apex-0000000000000000000000000000ab")
    with pytest.raises(ConfigError):
        gold_text(apex, claw_repo=None)
    block = render_reference_block(bc, template=template, claw_repo=None)
    assert block.startswith(REFERENCE_MARKER) and "must still execute the task" in block
    prompt = with_reference(bc.prompt, block)
    assert prompt.startswith(bc.prompt) and prompt.rstrip().endswith("=== END REFERENCE ===")


def test_template_must_have_placeholder(tmp_path: Path) -> None:
    bad = tmp_path / "t.md"
    bad.write_text("no placeholder\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_template(bad)
