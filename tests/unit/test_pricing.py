from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ahd.errors import ConfigError
from ahd.llm.pricing import PricingTable, load_pricing
from ahd.llm.types import Usage
from tests.conftest import REPO_ROOT

BEIJING = timezone(timedelta(hours=8))


@pytest.mark.parametrize(
    ("ts", "tier"),
    [
        (datetime(2026, 9, 2, 2, 0, tzinfo=UTC), "peak"),  # Wednesday 02:00 UTC
        (datetime(2026, 9, 2, 1, 0, tzinfo=UTC), "peak"),  # window start inclusive
        (datetime(2026, 9, 2, 4, 0, tzinfo=UTC), "off_peak"),  # window end exclusive
        (datetime(2026, 9, 2, 7, 30, tzinfo=UTC), "peak"),
        (datetime(2026, 9, 2, 12, 0, tzinfo=UTC), "off_peak"),
        (datetime(2026, 9, 5, 2, 0, tzinfo=UTC), "off_peak"),  # Saturday
        (datetime(2026, 9, 2, 10, 0, tzinfo=BEIJING), "peak"),  # 02:00 UTC
    ],
)
def test_tier_schedule(pricing: PricingTable, ts: datetime, tier: str) -> None:
    assert datetime(2026, 9, 2, tzinfo=UTC).weekday() == 2
    assert datetime(2026, 9, 5, tzinfo=UTC).weekday() == 5
    assert pricing.tier_at(ts) == tier


def test_naive_timestamp_rejected(pricing: PricingTable) -> None:
    with pytest.raises(ConfigError, match="timezone"):
        pricing.tier_at(datetime(2026, 9, 2, 2, 0))


def test_cost_splits_cache_hit_and_miss(pricing: PricingTable) -> None:
    usage = Usage(
        prompt_tokens=1_000_000, completion_tokens=500_000, cache_hit_prompt_tokens=250_000
    )
    cost = pricing.cost("fake-model", usage, datetime(2026, 9, 2, 12, 0, tzinfo=UTC))
    assert cost.tier == "off_peak"
    assert cost.usd == pytest.approx(0.25 * 1.0 + 0.75 * 2.0 + 0.5 * 4.0)
    assert cost.pricing_version == "test.1"


def test_unknown_model_is_config_error(pricing: PricingTable) -> None:
    with pytest.raises(ConfigError, match="no pricing for model"):
        pricing.cost("nope", Usage(prompt_tokens=1, completion_tokens=1), datetime.now(UTC))


def test_shipped_pricing_file_validates() -> None:
    table = load_pricing(REPO_ROOT / "configs" / "pricing.yaml")
    assert {"deepseek-v4-flash", "deepseek-v4-pro"} <= set(table.models)
    flash = table.models["deepseek-v4-flash"]
    assert flash.off_peak.output == pytest.approx(flash.peak.output / 2)
    assert table.pricing_version


def test_search_pricing_has_its_own_version() -> None:
    table = load_pricing(REPO_ROOT / "configs" / "pricing.yaml")
    cost = table.search_cost("serper", queries=3)
    assert cost.usd == pytest.approx(0.003)
    assert cost.provider == "serper"
    assert cost.pricing_version != table.pricing_version
    with pytest.raises(ConfigError, match="no search pricing"):
        table.search_cost("bing")
