"""Per-task aggregation (pass^k strict and pass@k) and the run summary.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The strict all-pass rule
follows Evo-Bench ``evaluation/runner.py:490-494`` (``pass_hat_k = all(passes) and
len(passes) == trials``, ``pass_at_k = any(passes)``, ``score = mean``) as a rule, not code.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ahd.core.hashing import JsonValue, to_json_value
from ahd.llm.ledger import LedgerRow, summarize
from ahd.runner.records import RolloutRecord, TaskResult
from ahd.runner.spec import BENCHMARK_TRIALS_BY_SOURCE
from ahd.tasks.models import Task


def final_attempts(rollouts: Sequence[RolloutRecord]) -> tuple[RolloutRecord, ...]:
    """The last attempt per replicate (reference mode retries; normal mode has one)."""
    last: dict[str, RolloutRecord] = {}
    for rollout in rollouts:
        current = last.get(rollout.replicate)
        if current is None or rollout.attempt > current.attempt:
            last[rollout.replicate] = rollout
    return tuple(last[key] for key in sorted(last))


def aggregate_task(task: Task, rollouts: Sequence[RolloutRecord], *, k: int) -> TaskResult:
    finals = final_attempts(rollouts)
    passes = [bool(r.score.passed) if r.score is not None else False for r in finals]
    values = [r.score.value for r in finals if r.score is not None]
    benchmark_k = BENCHMARK_TRIALS_BY_SOURCE.get(task.source_benchmark, 1)
    return TaskResult(
        task_id=task.id,
        source_benchmark=task.source_benchmark,
        k=k,
        benchmark_k=benchmark_k,
        benchmark_aggregate_valid=(k == benchmark_k),
        pass_hat_k=bool(passes) and all(passes) and len(passes) == k,
        pass_at_k=any(passes),
        mean_value=(sum(values) / len(values)) if values else None,
        scored=len(values),
        passed=sum(1 for p in passes if p),
        infra=sum(1 for r in finals if r.error_family == "infra"),
        budget=sum(1 for r in finals if r.error_family == "budget"),
        task_failures=sum(
            1 for r in finals if r.score is not None and r.score.task_failure is not None
        ),
        rollouts=tuple(rollouts),
    )


def summarize_run(
    *,
    run_id: str,
    harness_snapshot_id: str,
    mode: str,
    tasks: Sequence[TaskResult],
    ledger_rows: Sequence[LedgerRow],
) -> dict[str, JsonValue]:
    by_source: dict[str, list[TaskResult]] = defaultdict(list)
    for result in tasks:
        by_source[result.source_benchmark].append(result)
    per_source: dict[str, object] = {}
    for source in sorted(by_source):
        items = by_source[source]
        per_source[source] = {
            "tasks": len(items),
            "k": sorted({t.k for t in items}),
            "benchmark_k": items[0].benchmark_k,
            "benchmark_aggregate_valid": all(t.benchmark_aggregate_valid for t in items),
            "pass_hat_k_rate": sum(1 for t in items if t.pass_hat_k) / len(items),
            "pass_at_k_rate": sum(1 for t in items if t.pass_at_k) / len(items),
            "rollout_pass_rate": (
                sum(t.passed for t in items) / max(1, sum(t.scored for t in items))
            ),
            "mean_value": (
                sum(t.mean_value for t in items if t.mean_value is not None)
                / max(1, sum(1 for t in items if t.mean_value is not None))
            ),
            "infra_rollouts": sum(t.infra for t in items),
            "budget_rollouts": sum(t.budget for t in items),
            "task_failure_rollouts": sum(t.task_failures for t in items),
        }
    ledger = summarize(list(ledger_rows))
    summary = to_json_value(
        {
            "run_id": run_id,
            "harness_snapshot_id": harness_snapshot_id,
            "mode": mode,
            "tasks_total": len(tasks),
            "per_source": per_source,
            "note": (
                "pass_hat_k = all replicates pass (Evo-Bench's Pass^k); pass_at_k = any; "
                "the benchmark aggregate is only valid when k equals benchmark_k"
            ),
            "ledger": ledger.model_dump(),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "source": t.source_benchmark,
                    "k": t.k,
                    "pass_hat_k": t.pass_hat_k,
                    "pass_at_k": t.pass_at_k,
                    "mean_value": t.mean_value,
                    "scored": t.scored,
                    "passed": t.passed,
                    "infra": t.infra,
                    "budget": t.budget,
                    "replicates": [
                        {
                            "replicate": r.replicate,
                            "attempt": r.attempt,
                            "exit_reason": r.exit_reason,
                            "steps": r.steps,
                            "passed": r.score.passed if r.score else None,
                            "value": r.score.value if r.score else None,
                            "error_family": r.error_family,
                            "error_kind": r.error_kind,
                            "usd": r.usd,
                            "serper_calls_approx": r.serper_calls_approx,
                            "reasoning_steps": r.reasoning_steps,
                            "partial": r.partial,
                        }
                        for r in t.rollouts
                    ],
                }
                for t in tasks
            ],
        }
    )
    if not isinstance(summary, dict):  # pragma: no cover
        raise TypeError("summary must be an object")
    return summary
