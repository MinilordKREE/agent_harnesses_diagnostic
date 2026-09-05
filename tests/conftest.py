"""Shared fixtures. Every unit test runs offline: the provider is a fake transport."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from ahd.core.config import LLMConfig, RetryConfig, RunConfig
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import DeepSeekClient
from ahd.llm.ledger import Ledger
from ahd.llm.pricing import PricingTable, load_pricing

REPO_ROOT = Path(__file__).resolve().parents[1]

PRICING_YAML = """\
pricing_version: "test.1"
as_of: 2026-09-04
source: test
currency: USD
unit: per_1m_tokens
peak:
  description: "Mon-Fri 01:00-04:00 and 06:00-10:00 UTC"
  weekdays: [0, 1, 2, 3, 4]
  utc_windows: [["01:00", "04:00"], ["06:00", "10:00"]]
models:
  deepseek-v4-flash:
    peak:     {input_cache_hit: 0.014, input_cache_miss: 0.44, output: 1.32}
    off_peak: {input_cache_hit: 0.007, input_cache_miss: 0.22, output: 0.66}
  fake-model:
    peak:     {input_cache_hit: 2.0, input_cache_miss: 4.0, output: 8.0}
    off_peak: {input_cache_hit: 1.0, input_cache_miss: 2.0, output: 4.0}
search:
  serper:
    usd_per_query: 0.001
    pricing_version: "search-test.1"
    as_of: 2026-09-05
    source: test
"""

# A Wednesday at noon UTC: off-peak in the schedule above.
OFF_PEAK_TS = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
# A Wednesday at 02:00 UTC: peak.
PEAK_TS = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)


def make_completion(
    content: str = "pong",
    *,
    prompt_tokens: int = 10,
    completion_tokens: int = 2,
    cache_hit: int = 0,
    reasoning: str | None = None,
    reasoning_tokens: int = 0,
    finish_reason: str = "stop",
    model: str = "deepseek-v4-flash",
    usage: bool = True,
) -> ChatCompletion:
    """Build a ChatCompletion the way the SDK does for a real response (lenient construct)."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    data: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
    }
    if usage:
        data["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_cache_hit_tokens": cache_hit,
            "prompt_cache_miss_tokens": prompt_tokens - cache_hit,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        }
    return ChatCompletion.construct(**data)


def _http(status: int) -> httpx2.Response:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    return httpx2.Response(status, request=request)


def status_error(status: int, message: str = "boom") -> openai.APIStatusError:
    if status == 429:
        return openai.RateLimitError(message, response=_http(status), body=None)
    return openai.APIStatusError(message, response=_http(status), body=None)


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "https://api.deepseek.com"))


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx2.Request("POST", "https://api.deepseek.com"))


class FakeTransport:
    """Scripted ``chat.completions.create``: returns or raises each outcome in order."""

    def __init__(self, outcomes: list[ChatCompletion | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("FakeTransport has no scripted outcomes left")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def pricing_path(tmp_path: Path) -> Path:
    path = tmp_path / "pricing.yaml"
    path.write_text(PRICING_YAML, encoding="utf-8")
    return path


@pytest.fixture
def pricing(pricing_path: Path) -> PricingTable:
    return load_pricing(pricing_path)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A fresh repository with one commit and a clean tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("test\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def run_config(pricing_path: Path, tmp_path: Path) -> RunConfig:
    return RunConfig(
        schema_version=1,
        name="unit",
        kind="exploratory",
        seed=7,
        require_clean_tree=False,
        llm=LLMConfig(
            model="deepseek-v4-flash",
            cache_dir=tmp_path / "cache",
            retry=RetryConfig(max_attempts=3, initial_delay_s=0.0, jitter_s=0.0),
        ),
        pricing_path=pricing_path,
        runs_root=tmp_path / "runs",
    )


class ClientBundle:
    def __init__(
        self, client: DeepSeekClient, transport: FakeTransport, ledger: Ledger, cache: ResponseCache
    ) -> None:
        self.client = client
        self.transport = transport
        self.ledger = ledger
        self.cache = cache
        self.sleeps: list[float] = []


type ClientFactory = Callable[..., ClientBundle]


@pytest.fixture
def make_client(tmp_path: Path, pricing: PricingTable) -> ClientFactory:
    def factory(
        outcomes: list[ChatCompletion | Exception],
        *,
        with_cache: bool = True,
        retry: RetryConfig | None = None,
        now: datetime = OFF_PEAK_TS,
    ) -> ClientBundle:
        transport = FakeTransport(outcomes)
        ledger = Ledger(tmp_path / "ledger.jsonl", "run-test")
        cache = ResponseCache(tmp_path / "cache", provider="deepseek")
        sleeps: list[float] = []
        client = DeepSeekClient(
            transport=transport,
            ledger=ledger,
            pricing=pricing,
            retry=retry or RetryConfig(max_attempts=3, initial_delay_s=0.5, jitter_s=0.0),
            cache=cache if with_cache else None,
            now=lambda: now,
            sleep=sleeps.append,
        )
        bundle = ClientBundle(client, transport, ledger, cache)
        bundle.sleeps = sleeps
        return bundle

    return factory


@pytest.fixture
def fake_snapshot(tmp_path: Path) -> Path:
    from tests.evobench_fixtures import make_fake_snapshot

    return make_fake_snapshot(tmp_path / "snapshot")
