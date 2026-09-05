"""Exception families for ahd.

No reference source: written fresh for ahd (see docs/reuse/M0.md). The split follows
VeRO's ``EvaluationInfrastructureError`` vs ``EvaluationTerminatedError`` distinction
(vero/src/vero/evaluation/exceptions.py) in spirit only; no code was copied.

Two runtime families that are never conflated and are counted separately in ledgers:

* :class:`InfraError` -- the infrastructure failed us: provider 429/5xx, network errors,
  timeouts, missing or corrupt files. Retryable or not is recorded on the instance.
* :class:`TaskFailure` -- the agent or harness failed the task. :class:`BudgetExhausted`
  is a task-level outcome (part of the estimand), not an infrastructure fault.

:class:`ConfigError` is raised before a run starts (bad config, dirty tree, invalid CLI
usage). It never fires mid-run and is not a ledger event.
"""

from __future__ import annotations


class AhdError(Exception):
    """Base class for all ahd exceptions."""


class InfraError(AhdError):
    """Infrastructure failure: provider errors, network, timeouts, missing files."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "infra",
        status_code: int | None = None,
        retryable: bool = False,
        attempts: int = 0,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retryable = retryable
        self.attempts = attempts

    def __str__(self) -> str:
        base = super().__str__()
        status = f", status={self.status_code}" if self.status_code is not None else ""
        return f"[{self.kind}{status}, attempts={self.attempts}] {base}"


class TaskFailure(AhdError):  # noqa: N818 - name mandated by docs/CONVENTIONS.md
    """The agent or harness failed the task. Never raised for infrastructure problems."""

    def __init__(self, message: str, *, kind: str = "task_failure") -> None:
        super().__init__(message)
        self.kind = kind


class BudgetExhausted(TaskFailure):
    """A task-level budget (tokens, usd, calls, steps) ran out before the task finished."""

    def __init__(self, message: str, *, budget: float, spent: float, unit: str) -> None:
        super().__init__(message, kind="budget_exhausted")
        self.budget = budget
        self.spent = spent
        self.unit = unit


class ConfigError(AhdError):
    """Invalid configuration or refused start (for example a dirty tree on a confirmatory run)."""
