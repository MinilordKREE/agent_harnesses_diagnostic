from __future__ import annotations

from ahd.errors import AhdError, BudgetExhausted, ConfigError, InfraError, TaskFailure


def test_families_are_disjoint() -> None:
    assert not issubclass(InfraError, TaskFailure)
    assert not issubclass(TaskFailure, InfraError)
    assert not issubclass(ConfigError, InfraError | TaskFailure)
    assert issubclass(BudgetExhausted, TaskFailure)
    assert all(issubclass(c, AhdError) for c in (InfraError, TaskFailure, ConfigError))


def test_infra_error_carries_diagnostics() -> None:
    exc = InfraError("boom", kind="rate_limit", status_code=429, retryable=True, attempts=4)
    assert exc.kind == "rate_limit"
    assert "rate_limit" in str(exc) and "429" in str(exc) and "attempts=4" in str(exc)


def test_budget_exhausted_is_task_level() -> None:
    exc = BudgetExhausted("spent", budget=1.0, spent=1.5, unit="usd")
    assert isinstance(exc, TaskFailure)
    assert not isinstance(exc, InfraError)
    assert exc.kind == "budget_exhausted"
    assert (exc.budget, exc.spent, exc.unit) == (1.0, 1.5, "usd")
