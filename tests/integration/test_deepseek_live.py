"""Real-provider smoke test. Deselected by default; run with ``make test-integration``."""

from __future__ import annotations

from pathlib import Path

import pytest

from ahd.core.config import load_run_config
from ahd.errors import ConfigError
from ahd.llm.deepseek import DeepSeekClient, make_openai_transport
from ahd.llm.ledger import Ledger, read_ledger
from ahd.llm.pricing import load_pricing
from ahd.llm.types import Attribution, ChatMessage, ChatRequest
from ahd.settings import load_settings
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration


def _live_client(tmp_path: Path) -> tuple[DeepSeekClient, Ledger, str]:
    try:
        settings = load_settings(REPO_ROOT / ".env")
    except ConfigError:
        pytest.skip("DEEPSEEK_API_KEY not configured")
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    pricing = load_pricing(REPO_ROOT / config.pricing_path)
    ledger = Ledger(tmp_path / "ledger.jsonl", "integration")
    client = DeepSeekClient(
        transport=make_openai_transport(
            api_key=settings.deepseek_api_key,
            base_url=config.llm.base_url,
            timeout_s=config.llm.timeout_s,
        ),
        ledger=ledger,
        pricing=pricing,
        retry=config.llm.retry,
    )
    return client, ledger, config.llm.model


def test_live_ping(tmp_path: Path) -> None:
    client, ledger, model = _live_client(tmp_path)
    request = ChatRequest(
        model=model,
        messages=(ChatMessage(role="user", content="Reply with the single word: pong"),),
        max_tokens=16,
        seed=0,
        attribution=Attribution(arm="integration", unit_id="ping"),
    )
    response = client.complete(request)
    assert response.usage.prompt_tokens > 0
    assert response.usage.completion_tokens > 0
    assert response.finish_reason in {"stop", "length"}
    row = read_ledger(ledger.path)[0]
    assert row.usd > 0.0
    assert row.pricing_tier in {"peak", "off_peak"}


def test_live_thinking(tmp_path: Path) -> None:
    """Thinking on with top-level reasoning_effort: the placement the server parses.

    If this starts failing with a 400 naming ``reasoning_effort``, DeepSeek changed the
    contract; re-run the placement probe described in docs/CONVENTIONS.md before editing.
    """
    client, ledger, model = _live_client(tmp_path)
    request = ChatRequest(
        model=model,
        messages=(ChatMessage(role="user", content="What is 17*23? Reply with just the number."),),
        max_tokens=256,
        seed=0,
        thinking=True,
        reasoning_effort="low",
        attribution=Attribution(arm="integration", unit_id="thinking"),
    )
    response = client.complete(request)
    assert response.finish_reason == "stop"
    assert "391" in response.content
    assert response.reasoning, "thinking mode should return reasoning_content"
    assert response.usage.reasoning_tokens > 0
    row = read_ledger(ledger.path)[0]
    assert row.reasoning_tokens == response.usage.reasoning_tokens
    assert row.usd > 0.0
