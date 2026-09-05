from __future__ import annotations

import pytest

from ahd.core.config import RetryConfig
from ahd.errors import InfraError
from ahd.llm.deepseek import DeepSeekClient, build_wire_request
from ahd.llm.fake import FakeProvider
from ahd.llm.ledger import read_ledger, summarize
from ahd.llm.provider import Provider
from ahd.llm.types import Attribution, ChatMessage, ChatRequest
from tests.conftest import PEAK_TS, ClientFactory, make_completion, status_error


def _request(**overrides: object) -> ChatRequest:
    base: dict[str, object] = {
        "model": "deepseek-v4-flash",
        "messages": (ChatMessage(role="user", content="ping"),),
        "seed": 3,
        "max_tokens": 32,
        "attribution": Attribution(arm="A", unit_id="u1"),
    }
    base.update(overrides)
    return ChatRequest.model_validate(base)


def test_provider_protocol() -> None:
    assert isinstance(FakeProvider(), Provider)
    assert issubclass(DeepSeekClient, Provider)


def test_wire_request_shape() -> None:
    wire = build_wire_request(_request(temperature=0.3, timeout_s=9.0))
    assert wire["model"] == "deepseek-v4-flash"
    assert wire["messages"] == [{"role": "user", "content": "ping"}]
    assert wire["max_tokens"] == 32
    assert wire["temperature"] == 0.3
    assert wire["extra_body"] == {"thinking": {"type": "disabled"}}
    assert wire["timeout"] == 9.0
    assert wire["stream"] is False
    assert "seed" not in wire


def test_wire_request_thinking_omits_temperature() -> None:
    wire = build_wire_request(_request(thinking=True, reasoning_effort="high", temperature=0.9))
    assert "temperature" not in wire
    assert wire["extra_body"] == {"thinking": {"type": "enabled"}}
    assert wire["reasoning_effort"] == "high"  # top-level: the placement the server parses


def test_wire_request_no_effort_when_thinking_off() -> None:
    wire = build_wire_request(_request(thinking=False, reasoning_effort="high"))
    assert "reasoning_effort" not in wire


def test_happy_path_writes_priced_ledger_row(make_client: ClientFactory) -> None:
    bundle = make_client(
        [make_completion("pong", prompt_tokens=100, completion_tokens=10, cache_hit=40)]
    )
    response = bundle.client.complete(_request())
    assert response.content == "pong"
    assert response.cached is False
    assert response.usage.prompt_tokens == 100
    assert response.usage.cache_hit_prompt_tokens == 40
    assert response.usage.cache_miss_prompt_tokens == 60
    assert len(bundle.transport.calls) == 1
    rows = read_ledger(bundle.ledger.path)
    assert len(rows) == 1
    row = rows[0]
    assert row.event == "call"
    assert (row.run_id, row.arm, row.unit_id, row.seed) == ("run-test", "A", "u1", 3)
    assert row.model == "deepseek-v4-flash"
    assert (row.prompt_tokens, row.completion_tokens) == (100, 10)
    assert row.cached is False
    assert row.attempt == 1
    assert row.pricing_version == "test.1"
    assert row.pricing_tier == "off_peak"
    expected = (40 * 0.007 + 60 * 0.22 + 10 * 0.66) / 1_000_000
    assert row.usd == pytest.approx(expected)
    assert row.request_sha256 == response.request_sha256


def test_peak_tier_pricing(make_client: ClientFactory) -> None:
    bundle = make_client([make_completion(prompt_tokens=100, completion_tokens=10)], now=PEAK_TS)
    bundle.client.complete(_request())
    row = read_ledger(bundle.ledger.path)[0]
    assert row.pricing_tier == "peak"
    assert row.usd == pytest.approx((100 * 0.44 + 10 * 1.32) / 1_000_000)


def test_thinking_response_captures_reasoning(make_client: ClientFactory) -> None:
    bundle = make_client(
        [make_completion("42", reasoning="think", reasoning_tokens=7, completion_tokens=9)]
    )
    response = bundle.client.complete(_request(thinking=True))
    assert response.reasoning == "think"
    assert response.usage.reasoning_tokens == 7
    assert read_ledger(bundle.ledger.path)[0].reasoning_tokens == 7


def test_retries_are_ledger_infra_events(make_client: ClientFactory) -> None:
    bundle = make_client([status_error(429), status_error(502), make_completion()])
    response = bundle.client.complete(_request())
    assert response.content == "pong"
    assert len(bundle.transport.calls) == 3
    assert bundle.sleeps == [0.5, 1.0]
    rows = read_ledger(bundle.ledger.path)
    assert [r.event for r in rows] == ["infra_retry", "infra_retry", "call"]
    assert [r.status_code for r in rows[:2]] == [429, 502]
    assert [r.error_kind for r in rows[:2]] == ["rate_limit", "server_error"]
    assert rows[2].attempt == 3
    summary = summarize(rows)
    assert (summary.calls, summary.infra_retries, summary.infra_failures) == (1, 2, 0)


def test_gives_up_with_infra_error(make_client: ClientFactory) -> None:
    bundle = make_client([status_error(500)] * 3)
    with pytest.raises(InfraError) as info:
        bundle.client.complete(_request())
    assert info.value.attempts == 3
    rows = read_ledger(bundle.ledger.path)
    assert [r.event for r in rows] == ["infra_retry", "infra_retry", "infra_failure"]
    assert rows[-1].status_code == 500
    assert rows[-1].attempt == 3
    assert summarize(rows).infra_failures == 1
    assert summarize(rows).task_failures == 0


def test_auth_error_not_retried(make_client: ClientFactory) -> None:
    bundle = make_client([status_error(401)])
    with pytest.raises(InfraError) as info:
        bundle.client.complete(_request())
    assert info.value.kind == "auth"
    assert info.value.retryable is False
    assert len(bundle.transport.calls) == 1
    rows = read_ledger(bundle.ledger.path)
    assert [r.event for r in rows] == ["infra_failure"]
    assert rows[0].status_code == 401


def test_cache_hit_skips_transport_and_costs_nothing(make_client: ClientFactory) -> None:
    bundle = make_client([make_completion("cached-answer", prompt_tokens=50, completion_tokens=5)])
    first = bundle.client.complete(_request(use_cache=True))
    second = bundle.client.complete(_request(use_cache=True))
    assert first.cached is False
    assert second.cached is True
    assert second.content == "cached-answer"
    assert len(bundle.transport.calls) == 1
    rows = read_ledger(bundle.ledger.path)
    assert [r.cached for r in rows] == [False, True]
    assert rows[1].usd == 0.0
    assert rows[1].prompt_tokens == 50
    assert rows[0].request_sha256 == rows[1].request_sha256
    assert summarize(rows).cached_calls == 1


def test_cache_key_differs_by_seed(make_client: ClientFactory) -> None:
    bundle = make_client([make_completion("a"), make_completion("b")])
    first = bundle.client.complete(_request(use_cache=True, seed=1))
    second = bundle.client.complete(_request(use_cache=True, seed=2))
    assert first.request_sha256 != second.request_sha256
    assert len(bundle.transport.calls) == 2


def test_use_cache_without_cache_is_error(make_client: ClientFactory) -> None:
    bundle = make_client([make_completion()], with_cache=False)
    with pytest.raises(InfraError) as info:
        bundle.client.complete(_request(use_cache=True))
    assert info.value.kind == "cache_unconfigured"


def test_insufficient_resource_is_retried(make_client: ClientFactory) -> None:
    bundle = make_client(
        [make_completion(finish_reason="insufficient_system_resource"), make_completion("ok")]
    )
    response = bundle.client.complete(_request())
    assert response.content == "ok"
    rows = read_ledger(bundle.ledger.path)
    assert rows[0].event == "infra_retry"
    assert rows[0].error_kind == "insufficient_system_resource"


def test_missing_usage_is_infra_error(make_client: ClientFactory) -> None:
    bundle = make_client(
        [make_completion(usage=False)] * 3,
        retry=RetryConfig(max_attempts=3, initial_delay_s=0.0, jitter_s=0.0),
    )
    with pytest.raises(InfraError) as info:
        bundle.client.complete(_request())
    assert info.value.kind == "missing_usage"
    assert len(bundle.transport.calls) == 1


def test_unknown_model_pricing_is_config_error(make_client: ClientFactory) -> None:
    from ahd.errors import ConfigError

    bundle = make_client([make_completion()])
    with pytest.raises(ConfigError, match="no pricing"):
        bundle.client.complete(_request(model="deepseek-v9"))
