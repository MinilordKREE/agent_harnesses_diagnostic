"""Retry loop with exponential backoff, jitter and a total wall-clock cap.

Adapted from: scaleapi/vero @ 0b0e86764d836c456aee5b8dff80d765fdbba77c
Original path: vero/src/vero/evaluation/models.py (``RetryPolicy`` fields; lines 130-145) and
vero/src/vero/interpret/labeling/client.py (jittered ``2**attempt + random``; lines 74-81)
License: MIT, Copyright (c) 2026 Scale AI -- see THIRD_PARTY_NOTICES.md

Also adapted from: RUCAIBox/Evo-Bench @ e1dc9386a193cab1ee8630824c085e5e26d0c730
Original path: evobench/evolution/lib/dissector.py (``_error_detail`` transient status set
``{408, 409, 425, 429}`` or ``>= 500``; lines 410-433)
License: Apache-2.0, Copyright 2026 Evo-Bench Authors -- see THIRD_PARTY_NOTICES.md

Changes: classification is by ``openai`` exception type and status code, never by message
substring; 400/401/403 are never retried; a total-duration cap is enforced; every retry is
reported through ``on_retry`` so the caller can write a ledger row; exhaustion raises
:class:`InfraError` with attempt count and status; non-infra exceptions propagate untouched.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

import openai

from ahd.core.config import RetryConfig
from ahd.errors import InfraError

logger = logging.getLogger(__name__)

NEVER_RETRY_STATUS: frozenset[int] = frozenset({400, 401, 403})


@dataclass(frozen=True, slots=True)
class Classification:
    kind: str
    status_code: int | None
    retryable: bool


@dataclass(frozen=True, slots=True)
class RetryEvent:
    attempt: int
    max_attempts: int
    delay_s: float
    kind: str
    status_code: int | None
    error: str


def classify(exc: BaseException, *, retry_status_codes: Collection[int]) -> Classification | None:
    """Map an exception to an infra classification, or ``None`` if it is not an infra error."""
    if isinstance(exc, InfraError):
        return Classification(exc.kind, exc.status_code, exc.retryable)
    if isinstance(exc, openai.APITimeoutError):
        return Classification("timeout", None, True)
    if isinstance(exc, openai.APIConnectionError):
        return Classification("connection", None, True)
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        if status in NEVER_RETRY_STATUS:
            kind = "auth" if status in (401, 403) else "bad_request"
            return Classification(kind, status, False)
        if status == 429:
            return Classification("rate_limit", status, True)
        if status >= 500:
            return Classification("server_error", status, True)
        if status in retry_status_codes:
            return Classification("transient", status, True)
        return Classification("client_error", status, False)
    if isinstance(exc, TimeoutError):
        return Classification("timeout", None, True)
    if isinstance(exc, ConnectionError):
        return Classification("connection", None, True)
    return None


def run_with_retry[T](
    fn: Callable[[], T],
    *,
    policy: RetryConfig,
    on_retry: Callable[[RetryEvent], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
) -> T:
    """Call ``fn`` until it succeeds, a non-retryable error occurs, or the policy is exhausted."""
    randomizer = rng or random.Random()
    started = clock()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            info = classify(exc, retry_status_codes=policy.retry_status_codes)
            if info is None:
                raise
            if not info.retryable:
                raise InfraError(
                    f"non-retryable provider error: {exc}",
                    kind=info.kind,
                    status_code=info.status_code,
                    retryable=False,
                    attempts=attempt,
                ) from exc
            if attempt >= policy.max_attempts:
                raise InfraError(
                    f"gave up after {attempt} attempt(s): {exc}",
                    kind=info.kind,
                    status_code=info.status_code,
                    retryable=True,
                    attempts=attempt,
                ) from exc
            delay = min(
                policy.max_delay_s, policy.initial_delay_s * policy.multiplier ** (attempt - 1)
            ) + randomizer.uniform(0.0, policy.jitter_s)
            elapsed = clock() - started
            if elapsed + delay > policy.total_timeout_s:
                raise InfraError(
                    f"retry budget of {policy.total_timeout_s}s exhausted after {attempt} "
                    f"attempt(s) ({elapsed:.1f}s elapsed): {exc}",
                    kind=info.kind,
                    status_code=info.status_code,
                    retryable=True,
                    attempts=attempt,
                ) from exc
            event = RetryEvent(
                attempt=attempt,
                max_attempts=policy.max_attempts,
                delay_s=delay,
                kind=info.kind,
                status_code=info.status_code,
                error=str(exc),
            )
            logger.warning(
                "infra retry %d/%d in %.2fs: %s",
                event.attempt,
                event.max_attempts,
                event.delay_s,
                event.error,
                extra={
                    "event": "infra_retry",
                    "error_kind": event.kind,
                    "status_code": event.status_code,
                },
            )
            if on_retry is not None:
                on_retry(event)
            sleep(delay)
