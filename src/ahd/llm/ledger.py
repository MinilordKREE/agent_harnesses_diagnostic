"""Append-only JSONL cost ledger, one row per LLM call or infra/task event.

No reference source: written fresh for ahd (see docs/reuse/M0.md). Column names follow the
VeRO gateway request log (vero/src/vero/gateway/inference.py lines 885-905, MIT) and the
row-per-event shape of AutoSaddler ``PaidWorkLedger`` (src/autosaddler/v2/providers/fake.py,
MIT); no code was copied.

Event kinds are counted separately by :func:`summarize`: ``call`` (charged or cached),
``infra_retry`` (one per retry), ``infra_failure`` (gave up: ``InfraError`` raised),
``task_failure`` (``TaskFailure`` recorded by the caller), ``search`` (one web-search
provider call, priced per query; an environment-interaction cost that matched-compute
baselines must report). Infra and task rows are never merged.

Schema v2 (M1): added ``search`` event and the ``search_provider`` / ``search_query_sha256``
columns. Schema v3 (M2): added the ``policy`` event (one row per rollout, tokens summed from
the trajectory's per-step usage and priced at the rollout's start tier) and the ``replicate``,
``steps`` and ``approximate`` columns (``approximate`` marks Serper rows inferred from shell
commands rather than observed on the wire). Within ``task_failure`` rows,
``error_kind == "budget_exhausted"`` is counted on its
own as ``budget_exhausted`` and excluded from ``task_failures``, because budget exhaustion is
part of the estimand and must stay visible as its own column.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from ahd.core.config import StrictModel
from ahd.core.io import append_jsonl, read_jsonl
from ahd.errors import InfraError, TaskFailure
from ahd.llm.pricing import CostBreakdown, PricingTier, SearchCost
from ahd.llm.types import ChatRequest, ChatResponse, Usage

LEDGER_SCHEMA_VERSION = 3
LEDGER_FILENAME = "ledger.jsonl"

type LedgerEvent = Literal[
    "call", "policy", "infra_retry", "infra_failure", "task_failure", "search"
]


class LedgerRow(StrictModel):
    schema_version: int
    ts: datetime
    run_id: str
    event: LedgerEvent
    arm: str
    unit_id: str
    seed: int
    model: str
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cache_hit_prompt_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cached: bool = False
    latency_ms: int = Field(default=0, ge=0)
    usd: float = Field(default=0.0, ge=0.0)
    pricing_version: str | None = None
    pricing_tier: PricingTier | None = None
    attempt: int | None = None
    status_code: int | None = None
    error_kind: str | None = None
    error: str | None = None
    request_sha256: str | None = None
    search_provider: str | None = None
    search_query_sha256: str | None = None
    replicate: str | None = None
    steps: int | None = None
    approximate: bool = False


BUDGET_EXHAUSTED_KIND = "budget_exhausted"


class LedgerSummary(StrictModel):
    calls: int = 0
    cached_calls: int = 0
    infra_retries: int = 0
    infra_failures: int = 0
    task_failures: int = 0
    """``task_failure`` rows other than budget exhaustion."""
    budget_exhausted: int = 0
    """``task_failure`` rows with ``error_kind == "budget_exhausted"``; never folded into
    ``task_failures``."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    """LLM spend (``call`` rows)."""
    search_calls: int = 0
    search_usd: float = 0.0
    """Web-search spend (``search`` rows); reported next to ``usd``, never folded into it."""
    policy_rollouts: int = 0
    policy_prompt_tokens: int = 0
    policy_completion_tokens: int = 0
    policy_usd: float = 0.0
    """Policy-model spend (``policy`` rows); the rollouts under study, separate from ``usd``."""


class Ledger:
    """Writes rows for one run. Each append opens, writes one line and closes (crash-safe)."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id

    def append(self, row: LedgerRow) -> None:
        append_jsonl(self.path, row.model_dump(mode="json"))

    def _fields(
        self, *, event: LedgerEvent, arm: str, unit_id: str, seed: int, model: str
    ) -> dict[str, object]:
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "ts": datetime.now(UTC),
            "run_id": self.run_id,
            "event": event,
            "arm": arm,
            "unit_id": unit_id,
            "seed": seed,
            "model": model,
        }

    def _base(self, request: ChatRequest, event: LedgerEvent) -> dict[str, object]:
        return self._fields(
            event=event,
            arm=request.attribution.arm,
            unit_id=request.attribution.unit_id,
            seed=request.seed,
            model=request.model,
        )

    def record_call(
        self,
        request: ChatRequest,
        response: ChatResponse,
        *,
        cost: CostBreakdown | None,
        attempt: int,
    ) -> LedgerRow:
        """A completed call. ``cost`` is ``None`` for cache hits (nothing was charged)."""
        row = LedgerRow.model_validate(
            {
                **self._base(request, "call"),
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cache_hit_prompt_tokens": response.usage.cache_hit_prompt_tokens,
                "reasoning_tokens": response.usage.reasoning_tokens,
                "cached": response.cached,
                "latency_ms": response.latency_ms,
                "usd": cost.usd if cost is not None else 0.0,
                "pricing_version": cost.pricing_version if cost is not None else None,
                "pricing_tier": cost.tier if cost is not None else None,
                "attempt": attempt,
                "request_sha256": response.request_sha256,
            }
        )
        self.append(row)
        return row

    def record_infra_retry(
        self,
        request: ChatRequest,
        *,
        attempt: int,
        kind: str,
        status_code: int | None,
        error: str,
        request_sha256: str,
    ) -> LedgerRow:
        row = LedgerRow.model_validate(
            {
                **self._base(request, "infra_retry"),
                "attempt": attempt,
                "status_code": status_code,
                "error_kind": kind,
                "error": error,
                "request_sha256": request_sha256,
            }
        )
        self.append(row)
        return row

    def record_infra_failure(
        self, request: ChatRequest, exc: InfraError, *, request_sha256: str
    ) -> LedgerRow:
        row = LedgerRow.model_validate(
            {
                **self._base(request, "infra_failure"),
                "attempt": exc.attempts,
                "status_code": exc.status_code,
                "error_kind": exc.kind,
                "error": str(exc),
                "request_sha256": request_sha256,
            }
        )
        self.append(row)
        return row

    def record_task_failure(
        self, request: ChatRequest, exc: TaskFailure, *, request_sha256: str | None = None
    ) -> LedgerRow:
        return self.record_task_failure_event(
            arm=request.attribution.arm,
            unit_id=request.attribution.unit_id,
            seed=request.seed,
            model=request.model,
            exc=exc,
            request_sha256=request_sha256,
        )

    def record_task_failure_event(
        self,
        *,
        arm: str,
        unit_id: str,
        seed: int,
        model: str,
        exc: TaskFailure,
        request_sha256: str | None = None,
    ) -> LedgerRow:
        """A task-level failure not tied to one chat request (for example a scorer verdict)."""
        row = LedgerRow.model_validate(
            {
                **self._fields(
                    event="task_failure", arm=arm, unit_id=unit_id, seed=seed, model=model
                ),
                "error_kind": exc.kind,
                "error": str(exc),
                "request_sha256": request_sha256,
            }
        )
        self.append(row)
        return row

    def record_infra_failure_event(
        self,
        *,
        arm: str,
        unit_id: str,
        seed: int,
        model: str,
        exc: InfraError,
        request_sha256: str | None = None,
    ) -> LedgerRow:
        """An infrastructure failure not tied to one chat request (missing resource, grader)."""
        row = LedgerRow.model_validate(
            {
                **self._fields(
                    event="infra_failure", arm=arm, unit_id=unit_id, seed=seed, model=model
                ),
                "attempt": exc.attempts,
                "status_code": exc.status_code,
                "error_kind": exc.kind,
                "error": str(exc),
                "request_sha256": request_sha256,
            }
        )
        self.append(row)
        return row

    def record_search(
        self,
        *,
        arm: str,
        unit_id: str,
        seed: int,
        cost: SearchCost,
        query_sha256: str,
        latency_ms: int = 0,
        replicate: str | None = None,
        approximate: bool = False,
    ) -> LedgerRow:
        """One web-search provider call, priced per query from ``pricing.yaml``.

        ``approximate=True`` means the call was inferred from a shell command in the trajectory
        (M2), not observed on the wire.
        """
        row = LedgerRow.model_validate(
            {
                **self._fields(
                    event="search", arm=arm, unit_id=unit_id, seed=seed, model=cost.provider
                ),
                "usd": cost.usd,
                "pricing_version": cost.pricing_version,
                "latency_ms": latency_ms,
                "search_provider": cost.provider,
                "search_query_sha256": query_sha256,
                "replicate": replicate,
                "approximate": approximate,
            }
        )
        self.append(row)
        return row

    def record_policy_rollout(
        self,
        *,
        arm: str,
        unit_id: str,
        seed: int,
        model: str,
        replicate: str,
        usage: Usage,
        cost: CostBreakdown,
        steps: int,
        latency_ms: int,
        request_sha256: str | None = None,
    ) -> LedgerRow:
        """One policy rollout: tokens summed from per-step usage, priced at the start tier."""
        row = LedgerRow.model_validate(
            {
                **self._fields(event="policy", arm=arm, unit_id=unit_id, seed=seed, model=model),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cache_hit_prompt_tokens": usage.cache_hit_prompt_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "usd": cost.usd,
                "pricing_version": cost.pricing_version,
                "pricing_tier": cost.tier,
                "latency_ms": latency_ms,
                "replicate": replicate,
                "steps": steps,
                "request_sha256": request_sha256,
            }
        )
        self.append(row)
        return row


def read_ledger(path: Path) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    for index, raw in enumerate(read_jsonl(path), start=1):
        try:
            rows.append(LedgerRow.model_validate(raw))
        except ValidationError as exc:
            raise InfraError(
                f"invalid ledger row at line {index} in {path}:\n{exc}", kind="corrupt_file"
            ) from exc
    return rows


def summarize(rows: list[LedgerRow]) -> LedgerSummary:
    summary = LedgerSummary()
    counts = summary.model_dump()
    for row in rows:
        match row.event:
            case "call":
                counts["calls"] += 1
                if row.cached:
                    counts["cached_calls"] += 1
                counts["prompt_tokens"] += row.prompt_tokens
                counts["completion_tokens"] += row.completion_tokens
                counts["usd"] += row.usd
            case "infra_retry":
                counts["infra_retries"] += 1
            case "infra_failure":
                counts["infra_failures"] += 1
            case "task_failure":
                if row.error_kind == BUDGET_EXHAUSTED_KIND:
                    counts["budget_exhausted"] += 1
                else:
                    counts["task_failures"] += 1
            case "search":
                counts["search_calls"] += 1
                counts["search_usd"] += row.usd
            case "policy":
                counts["policy_rollouts"] += 1
                counts["policy_prompt_tokens"] += row.prompt_tokens
                counts["policy_completion_tokens"] += row.completion_tokens
                counts["policy_usd"] += row.usd
    return LedgerSummary.model_validate(counts)
