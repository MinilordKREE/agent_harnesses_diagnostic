"""Records produced by the runner: rollouts, failures (M3's input contract), tasks, runs.

No reference source: written fresh for ahd (see docs/reuse/M2.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ahd.core.config import StrictModel
from ahd.llm.types import Usage
from ahd.tasks.models import Score

type ErrorFamily = Literal["none", "infra", "budget"]
type FailureFamily = Literal["infra", "task", "budget"]


class RolloutRecord(StrictModel):
    task_id: str
    source_benchmark: str
    replicate: str
    attempt: int
    mode: Literal["normal", "reference"]
    rollout_id: str | None
    rollout_dir: Path
    workspace_dir: Path
    final_answer: str
    exit_reason: str | None
    steps: int
    duration_seconds: float
    started_at: str
    usage: Usage | None
    usd: float | None
    pricing_tier: str | None
    partial: bool
    error_family: ErrorFamily
    error_kind: str | None
    error: str | None
    score: Score | None
    serper_calls_approx: int
    reasoning_steps: int
    """Number of model calls whose assistant message carried ``reasoning_content``."""


class FailureRecord(StrictModel):
    """One failed rollout: the input contract for M3 diagnosis."""

    task_id: str
    source_benchmark: str
    replicate: str
    attempt: int
    mode: Literal["normal", "reference"]
    harness_snapshot_id: str
    trajectory_path: str
    partial: bool
    family: FailureFamily
    error_kind: str | None
    reason: str
    score_value: float | None
    passed: bool
    exit_reason: str | None
    verified: Literal["pending"] = "pending"
    """Set by M3's genuineness judge; always ``pending`` when written by the runner."""


class ReferenceRecord(StrictModel):
    task_id: str
    replicate: str
    attempts: int
    max_attempts: int
    passing_attempt: int | None
    verified: Literal["pending"] = "pending"


class TaskResult(StrictModel):
    task_id: str
    source_benchmark: str
    k: int
    benchmark_k: int
    benchmark_aggregate_valid: bool
    """``k == benchmark_k``: only then is ``pass_hat_k`` the benchmark's own Pass^k."""
    pass_hat_k: bool
    pass_at_k: bool
    mean_value: float | None
    scored: int
    passed: int
    infra: int
    budget: int
    task_failures: int
    rollouts: tuple[RolloutRecord, ...]


class RunResult(StrictModel):
    run_id: str
    harness_snapshot_id: str
    mode: Literal["normal", "reference"]
    tasks: tuple[TaskResult, ...]
    failures: tuple[FailureRecord, ...]
    references: tuple[ReferenceRecord, ...]
    summary_path: Path
