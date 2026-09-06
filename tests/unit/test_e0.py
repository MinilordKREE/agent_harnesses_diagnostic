"""E0: per-source sampling, spec/config contract, report determinism and decision rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahd.core.config import load_run_config
from ahd.errors import ConfigError
from ahd.experiments import e0 as e0_module
from ahd.experiments.e0 import _check_spec_matches_config, load_spec
from ahd.experiments.report import SourceCalibration, build_report, decisions, write_csv
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import TaskSet
from ahd.tasks.sampling import sample_per_source
from tests.conftest import REPO_ROOT
from tests.evobench_fixtures import FAKE_REVISION

SPEC = REPO_ROOT / "experiments" / "E0" / "spec.yaml"


@pytest.fixture
def taskset(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("evaluation")


def _widen(taskset: TaskSet, copies: int) -> TaskSet:
    """Synthetic strata: ``copies`` id-variants of every task (the fixtures hold one per source)."""
    tasks = [
        t.model_copy(update={"id": f"{t.id}-{i}"}) for t in taskset.tasks for i in range(copies)
    ]
    return taskset.model_copy(update={"tasks": tuple(tasks)})


def test_sample_per_source_is_equal_and_nested(taskset: TaskSet) -> None:
    wide = _widen(taskset, 3)
    one = sample_per_source(wide, per_source=1, seed=0)
    counts: dict[str, int] = {}
    for t in one.tasks:
        counts[t.source_benchmark] = counts.get(t.source_benchmark, 0) + 1
    assert set(counts.values()) == {1} and "apex" not in counts  # excluded tasks never sampled
    two = sample_per_source(wide, per_source=2, seed=0)
    assert {t.id for t in one.tasks} <= {t.id for t in two.tasks}  # D5 superset
    assert sample_per_source(wide, per_source=2, seed=0).ids() == two.ids()
    assert sample_per_source(wide, per_source=2, seed=1).ids() != two.ids()
    with pytest.raises(ConfigError, match="cannot sample"):
        sample_per_source(wide, per_source=4, seed=0)


def test_spec_loads_and_matches_the_config() -> None:
    spec = load_spec(SPEC)
    assert spec.replay.k == 3 and spec.replay.max_candidates == 5 and spec.replay.economize
    assert spec.reference_max_attempts == 5 and spec.workers == 4
    assert {"D1", "D1prime", "D2", "D3", "D4", "D5", "D6", "D7"} == set(spec.decision_rules)
    assert spec.owner_budget_usd == 600.0  # D4 (owner: suggested 600)
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "e0.yaml")
    _check_spec_matches_config(spec, config)
    broken = config.model_copy(update={"run": config.run.model_copy(update={"workers": 2})})
    with pytest.raises(ConfigError, match="workers"):
        _check_spec_matches_config(spec, broken)


def test_report_is_deterministic_and_handles_missing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_path = tmp_path / "spec.yaml"
    text = SPEC.read_text(encoding="utf-8").replace(
        "runs_root: runs/E0", f"runs_root: {tmp_path / 'runs'}"
    )
    spec_path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(REPO_ROOT)
    data = tmp_path / "data"
    report = tmp_path / "E0_REPORT.md"
    first = build_report(spec_path=spec_path, data_dir=data, report_path=report)
    snapshot = {p: p.read_bytes() for p in first}
    second = build_report(spec_path=spec_path, data_dir=data, report_path=report)
    assert first == second and all(p.read_bytes() == snapshot[p] for p in second)
    decisions_csv = (data / "decisions.csv").read_text(encoding="utf-8")
    assert "not evaluable" in decisions_csv and decisions_csv.startswith("rule,observed,decision\n")
    assert "E0a has not run" in report.read_text(encoding="utf-8")


def test_decision_rules() -> None:
    spec = load_spec(SPEC)
    calib = {
        "browsecomp": SourceCalibration(
            source="browsecomp",
            pass_rate=0.5,
            aa_delta_points=12.0,
            aa_ci=(3.0, 20.0),
            heldout_delta_points=7.0,
            clusters_with_two=8,
            primary_clusters=4,
        ),
        "hle": SourceCalibration(
            source="hle",
            pass_rate=0.95,
            aa_delta_points=1.0,
            aa_ci=(0.0, 3.0),
            heldout_delta_points=1.0,
            clusters_with_two=2,
            primary_clusters=1,
        ),
        "gdpval": SourceCalibration(
            source="gdpval",
            pass_rate=0.4,
            aa_delta_points=3.0,
            aa_ci=(0.0, 6.0),
            heldout_delta_points=2.0,
            clusters_with_two=7,
            primary_clusters=5,
        ),
        "claw_eval": SourceCalibration(
            source="claw_eval",
            pass_rate=0.93,
            aa_delta_points=4.0,
            aa_ci=(0.0, 8.0),
            heldout_delta_points=None,
            clusters_with_two=1,
            primary_clusters=0,
        ),
    }
    rows = decisions(
        spec,
        calib,
        {"judge_self_consistency": 0.85, "judge_flash_agreement": 0.7},
        cost_per_rollout=0.05,
    )
    by_rule = {str(r[0]): str(r[2]) for r in rows}
    assert by_rule["D1:gdpval"] == "enters E2"
    assert by_rule["D1:browsecomp"] == "excluded"  # A/A band 12 points
    assert by_rule["D1:hle"] == "excluded"  # pass rate above 0.90
    assert by_rule["D1:claw_eval"] == "excluded"
    assert by_rule["D2:gdpval"].startswith("5 primary")
    assert by_rule["D3:browsecomp"] == "descriptive only"
    assert by_rule["D4"] == "k=3"  # 8 arms x 10 primary x 3 x 0.05 = 12 USD <= 600
    unset = spec.model_copy(update={"owner_budget_usd": None})
    rows0 = {str(r[0]): str(r[2]) for r in decisions(unset, calib, {}, cost_per_rollout=0.05)}
    assert rows0["D4"] == "not evaluable: owner_budget_usd not set"
    assert by_rule["D5:browsecomp"] == "45/source" and by_rule["D5:gdpval"] == "30/source"
    assert by_rule["D5:claw_eval"] == "not evaluable"
    assert by_rule["D6"] == "2-of-3 judge vote"  # the Flash re-judge clause was replaced by P1
    budgeted = spec.model_copy(update={"owner_budget_usd": 100.0})
    rows2 = {str(r[0]): str(r[2]) for r in decisions(budgeted, calib, {}, cost_per_rollout=0.05)}
    assert rows2["D4"] == "k=3"  # 8 arms x 10 primary x 3 x 0.05 = 12 USD <= 100
    tiny = spec.model_copy(update={"owner_budget_usd": 5.0})
    rows3 = {str(r[0]): str(r[2]) for r in decisions(tiny, calib, {}, cost_per_rollout=0.05)}
    assert rows3["D4"] == "k=2"
    assert {str(r[0]) for r in decisions(spec, {}, {}, cost_per_rollout=None)} == set(
        spec.decision_rules
    )


def test_write_csv_is_stable(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "t.csv", ["a", "b"], [[1, None], ["x", 2.5]])
    assert path.read_text(encoding="utf-8") == "a,b\n1,\nx,2.5\n"


def test_full_arms_subset_is_deterministic(tmp_path: Path) -> None:
    from ahd.runner.records import FailureRecord

    def make_run(name: str, source: str, n: int) -> Path:
        run = tmp_path / name
        run.mkdir()
        failures = [
            FailureRecord(
                task_id=f"{source}-{i}",
                source_benchmark=source,
                replicate="r1",
                attempt=1,
                mode="normal",
                harness_snapshot_id="s",
                trajectory_path="x",
                partial=False,
                family="task",
                error_kind=None,
                reason="r",
                score_value=0.0,
                passed=False,
                exit_reason="finished",
            )
            for i in range(n)
        ]
        (run / "failures.json").write_text(
            json.dumps([f.model_dump(mode="json") for f in failures]), encoding="utf-8"
        )
        return run

    runs = [
        make_run("a", "browsecomp", 20),
        make_run("b", "gdpval", 20),
        make_run("c", "claw_eval", 3),
    ]

    class Ctx:
        spec = load_spec(SPEC)

    first = e0_module.full_arms_subset(Ctx(), runs)  # type: ignore[arg-type]
    second = e0_module.full_arms_subset(Ctx(), runs)  # type: ignore[arg-type]
    assert first == second
    total = sum(len(v) for v in first.values())
    assert total == 30 and len(first["c"]) == 3  # small sources contribute all their failures
