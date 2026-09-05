from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ahd.runner.records import ErrorFamily, RolloutRecord
from ahd.runner.summary import aggregate_task, final_attempts, summarize_run
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.models import Score
from tests.evobench_fixtures import FAKE_REVISION


def _record(
    replicate: str, *, passed: bool | None, attempt: int = 1, family: ErrorFamily = "none"
) -> RolloutRecord:
    score = None
    if passed is not None:
        score = Score(
            passed=passed,
            value=1.0 if passed else 0.2,
            reason="r",
            scorer="claw_grader",
            artifact_sha256="a" * 64,
        )
    return RolloutRecord(
        task_id="claw-T000_synthetic",
        source_benchmark="claw_eval",
        replicate=replicate,
        attempt=attempt,
        mode="normal",
        rollout_id=None,
        rollout_dir=Path("/tmp/x"),
        workspace_dir=Path("/tmp/ws"),
        final_answer="",
        exit_reason="finished",
        steps=3,
        duration_seconds=1.0,
        started_at=datetime.now(UTC).isoformat(),
        usage=None,
        usd=0.01,
        pricing_tier="off_peak",
        partial=False,
        error_family=family,
        error_kind=None if family == "none" else family,
        error=None,
        score=score,
        serper_calls_approx=0,
        reasoning_steps=3,
    )


def test_claw_aggregation_is_strict_all_pass(fake_snapshot: Path) -> None:
    task = (
        EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot)
        .load("validation")
        .by_id("claw-T000_synthetic")
    )
    all_pass = aggregate_task(
        task,
        [_record("r1", passed=True), _record("r2", passed=True), _record("r3", passed=True)],
        k=3,
    )
    assert all_pass.pass_hat_k and all_pass.pass_at_k and all_pass.benchmark_aggregate_valid
    one_fail = aggregate_task(
        task,
        [_record("r1", passed=True), _record("r2", passed=False), _record("r3", passed=True)],
        k=3,
    )
    assert not one_fail.pass_hat_k and one_fail.pass_at_k
    assert (
        one_fail.mean_value is not None and abs(one_fail.mean_value - (1.0 + 0.2 + 1.0) / 3) < 1e-9
    )
    infra = aggregate_task(
        task, [_record("r1", passed=True), _record("r2", passed=None, family="infra")], k=2
    )
    assert not infra.pass_hat_k and infra.pass_at_k and infra.infra == 1 and infra.scored == 1
    assert infra.benchmark_aggregate_valid is False and infra.benchmark_k == 3
    short = aggregate_task(task, [_record("r1", passed=True)], k=2)
    assert not short.pass_hat_k  # a missing replicate cannot pass strict pass^k


def test_final_attempts_and_summary(fake_snapshot: Path) -> None:
    task = (
        EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot)
        .load("validation")
        .by_id("claw-T000_synthetic")
    )
    rollouts = [
        _record("r1", passed=False, attempt=1),
        _record("r1", passed=True, attempt=2),
        _record("r2", passed=True),
    ]
    finals = final_attempts(rollouts)
    assert [(r.replicate, r.attempt) for r in finals] == [("r1", 2), ("r2", 1)]
    result = aggregate_task(task, rollouts, k=2)
    assert result.pass_hat_k
    summary = summarize_run(
        run_id="r", harness_snapshot_id="s", mode="reference", tasks=[result], ledger_rows=[]
    )
    view = cast(Any, summary)
    assert view["per_source"]["claw_eval"]["pass_hat_k_rate"] == 1.0
    assert view["per_source"]["claw_eval"]["benchmark_aggregate_valid"] is False
    assert len(view["tasks"][0]["replicates"]) == 3
