"""Versioned price table (``configs/pricing.yaml``) and usd computation.

No reference source: written fresh for ahd (see docs/reuse/M0.md). No surveyed repo ships a
pricing table; VeRO documents (vero/src/vero/gateway/inference.py lines 645-653) that a stale
public table over-estimated cost 3.1x, which is why every ledger row records the
``pricing_version`` it was priced with and the raw token counts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError

from ahd.core.config import StrictModel
from ahd.core.io import read_text
from ahd.errors import ConfigError
from ahd.llm.types import Usage

type PricingTier = Literal["peak", "off_peak"]


class TierRates(StrictModel):
    """USD per 1M tokens."""

    input_cache_hit: float = Field(ge=0.0)
    input_cache_miss: float = Field(ge=0.0)
    output: float = Field(ge=0.0)


class ModelPricing(StrictModel):
    peak: TierRates
    off_peak: TierRates


class PeakSchedule(StrictModel):
    description: str
    weekdays: tuple[int, ...]
    utc_windows: tuple[tuple[time, time], ...]

    def is_peak(self, ts: datetime) -> bool:
        """Start inclusive, end exclusive, evaluated in UTC. Naive datetimes are rejected."""
        if ts.tzinfo is None:
            raise ConfigError("pricing needs a timezone-aware timestamp")
        utc = ts.astimezone(UTC)
        if utc.weekday() not in self.weekdays:
            return False
        clock = utc.time()
        return any(start <= clock < end for start, end in self.utc_windows)


class CostBreakdown(StrictModel):
    usd: float
    tier: PricingTier
    pricing_version: str


class SearchPricing(StrictModel):
    """Per-query price of a web-search provider (an environment-interaction cost, not tokens)."""

    usd_per_query: float = Field(ge=0.0)
    credits_per_query: int = Field(default=1, ge=1)
    note: str = ""
    pricing_version: str = Field(min_length=1)
    as_of: date
    source: str


class SearchCost(StrictModel):
    usd: float
    provider: str
    pricing_version: str


class PricingTable(StrictModel):
    pricing_version: str = Field(min_length=1)
    as_of: date
    source: str
    currency: Literal["USD"]
    unit: Literal["per_1m_tokens"]
    peak: PeakSchedule
    models: dict[str, ModelPricing]
    search: dict[str, SearchPricing] = {}

    def tier_at(self, ts: datetime) -> PricingTier:
        return "peak" if self.peak.is_peak(ts) else "off_peak"

    def rates_for(self, model: str, ts: datetime) -> tuple[PricingTier, TierRates]:
        try:
            pricing = self.models[model]
        except KeyError:
            known = ", ".join(sorted(self.models))
            raise ConfigError(
                f"no pricing for model {model!r} (pricing_version {self.pricing_version}); "
                f"known: {known}"
            ) from None
        tier = self.tier_at(ts)
        return tier, pricing.peak if tier == "peak" else pricing.off_peak

    def cost(self, model: str, usage: Usage, ts: datetime) -> CostBreakdown:
        """Cache-hit and cache-miss prompt tokens are priced separately; reasoning tokens are
        part of ``completion_tokens`` and billed as output (DeepSeek convention)."""
        tier, rates = self.rates_for(model, ts)
        usd = (
            usage.cache_hit_prompt_tokens * rates.input_cache_hit
            + usage.cache_miss_prompt_tokens * rates.input_cache_miss
            + usage.completion_tokens * rates.output
        ) / 1_000_000
        return CostBreakdown(usd=usd, tier=tier, pricing_version=self.pricing_version)

    def search_cost(self, provider: str, *, queries: int = 1) -> SearchCost:
        """Cost of ``queries`` calls to a search provider; its own ``pricing_version``."""
        try:
            pricing = self.search[provider]
        except KeyError:
            known = ", ".join(sorted(self.search)) or "none"
            raise ConfigError(
                f"no search pricing for provider {provider!r}; known: {known}"
            ) from None
        if queries < 0:
            raise ConfigError("queries must be non-negative")
        return SearchCost(
            usd=queries * pricing.usd_per_query,
            provider=provider,
            pricing_version=pricing.pricing_version,
        )


def load_pricing(path: Path) -> PricingTable:
    text = read_text(path)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in pricing file {path}: {exc}") from exc
    try:
        return PricingTable.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid pricing file {path}:\n{exc}") from exc
