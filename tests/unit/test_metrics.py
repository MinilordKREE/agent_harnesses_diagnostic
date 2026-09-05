from __future__ import annotations

from pathlib import Path

import pytest

from ahd.errors import ConfigError
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.metrics import MACRO_DISCLAIMER, summarize_scores
from ahd.tasks.models import Score
from tests.evobench_fixtures import FAKE_REVISION


def _score(passed: bool, value: float, failure: str | None = None) -> Score:
    return Score(
        passed=passed,
        value=value,
        reason="r",
        scorer="s",
        task_failure=failure,
        artifact_sha256="a" * 64,
    )


def test_per_source_and_labelled_macro(fake_snapshot: Path) -> None:
    tasks = (
        EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot)
        .load("validation")
        .select()
        .tasks
    )
    scores = {
        "bc-en-0001": _score(True, 1.0),
        "hle-0000000000000000000000ff": _score(False, 0.0),
        "gdpval-00000000-0000-0000-0000-000000000001": _score(False, 0.4, "no_deliverable"),
        "claw-T000_synthetic": _score(True, 0.9),
    }
    report = summarize_scores(tasks, scores)
    assert report.n_scored == 4
    assert report.per_source["browsecomp"].pass_rate == 1.0
    assert report.per_source["hle"].pass_rate == 0.0
    assert report.per_source["gdpval"].task_failures == 1
    assert report.per_source["claw_eval"].mean_value == pytest.approx(0.9)
    assert report.macro == pytest.approx((1.0 + 0.0 + 0.0 + 1.0) / 4)
    assert report.macro_label.startswith(MACRO_DISCLAIMER)
    assert "2:2:1" in report.macro_label
    weighted = summarize_scores(
        tasks, scores, weights={"browsecomp": 2, "hle": 2, "gdpval": 2, "claw_eval": 1}
    )
    assert weighted.macro == pytest.approx((2 * 1.0 + 2 * 0.0 + 2 * 0.0 + 1 * 1.0) / 7)
    with pytest.raises(ConfigError, match="missing"):
        summarize_scores(tasks, scores, weights={"browsecomp": 1})
    with pytest.raises(ConfigError, match="unknown"):
        summarize_scores(tasks, {"nope": _score(True, 1.0)})
    assert summarize_scores(tasks, {}).macro is None
