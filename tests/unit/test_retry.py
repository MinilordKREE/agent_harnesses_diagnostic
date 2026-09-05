from __future__ import annotations

import random

import pytest

from ahd.core.config import RetryConfig
from ahd.errors import InfraError
from ahd.llm.retry import RetryEvent, classify, run_with_retry
from tests.conftest import connection_error, status_error, timeout_error

CODES = (408, 409, 425, 429)


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (status_error(429), "rate_limit", True),
        (status_error(500), "server_error", True),
        (status_error(503), "server_error", True),
        (status_error(408), "transient", True),
        (status_error(425), "transient", True),
        (status_error(401), "auth", False),
        (status_error(403), "auth", False),
        (status_error(400), "bad_request", False),
        (status_error(404), "client_error", False),
        (connection_error(), "connection", True),
        (timeout_error(), "timeout", True),
        (TimeoutError("t"), "timeout", True),
        (ConnectionError("c"), "connection", True),
        (InfraError("x", kind="custom", retryable=True), "custom", True),
    ],
)
def test_classify(exc: Exception, kind: str, retryable: bool) -> None:
    info = classify(exc, retry_status_codes=CODES)
    assert info is not None
    assert info.kind == kind
    assert info.retryable is retryable


def test_classify_ignores_non_infra() -> None:
    assert classify(ValueError("bug"), retry_status_codes=CODES) is None


def test_succeeds_after_retries_and_reports_each() -> None:
    outcomes: list[Exception | str] = [status_error(429), status_error(500), "ok"]
    events: list[RetryEvent] = []
    sleeps: list[float] = []

    def fn() -> str:
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    policy = RetryConfig(max_attempts=5, initial_delay_s=1.0, multiplier=2.0, jitter_s=0.0)
    result = run_with_retry(fn, policy=policy, on_retry=events.append, sleep=sleeps.append)
    assert result == "ok"
    assert [e.attempt for e in events] == [1, 2]
    assert [e.kind for e in events] == ["rate_limit", "server_error"]
    assert sleeps == [1.0, 2.0]


def test_jitter_and_cap() -> None:
    sleeps: list[float] = []
    outcomes = [status_error(500)] * 3 + ["ok"]

    def fn() -> str:
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    policy = RetryConfig(max_attempts=5, initial_delay_s=10.0, max_delay_s=15.0, jitter_s=2.0)
    run_with_retry(fn, policy=policy, sleep=sleeps.append, rng=random.Random(0))
    assert len(sleeps) == 3
    assert all(10.0 <= s <= 17.0 for s in sleeps)
    assert sleeps[1] >= 15.0  # capped base delay plus jitter


def test_exhaustion_raises_infra_error() -> None:
    policy = RetryConfig(max_attempts=3, initial_delay_s=0.0, jitter_s=0.0)
    with pytest.raises(InfraError) as info:
        run_with_retry(
            lambda: (_ for _ in ()).throw(status_error(503)), policy=policy, sleep=lambda _: None
        )
    assert info.value.attempts == 3
    assert info.value.status_code == 503
    assert info.value.retryable is True


def test_non_retryable_raises_immediately() -> None:
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise status_error(401)

    policy = RetryConfig(max_attempts=5)
    with pytest.raises(InfraError) as info:
        run_with_retry(fn, policy=policy, sleep=lambda _: None)
    assert calls == 1
    assert info.value.attempts == 1
    assert info.value.retryable is False
    assert info.value.kind == "auth"


def test_non_infra_exception_propagates_unchanged() -> None:
    def fn() -> None:
        raise ValueError("bug in caller")

    with pytest.raises(ValueError, match="bug in caller"):
        run_with_retry(fn, policy=RetryConfig(), sleep=lambda _: None)


def test_total_timeout_cap() -> None:
    clock = iter([0.0, 100.0, 250.0, 400.0])
    policy = RetryConfig(max_attempts=10, initial_delay_s=5.0, jitter_s=0.0, total_timeout_s=300.0)

    def fn() -> None:
        raise status_error(500)

    with pytest.raises(InfraError, match="retry budget") as info:
        run_with_retry(fn, policy=policy, sleep=lambda _: None, clock=lambda: next(clock))
    assert info.value.attempts == 3
