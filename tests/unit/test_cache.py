from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ahd.errors import InfraError
from ahd.llm.cache import CACHE_VERSION, ResponseCache
from ahd.llm.types import Attribution, ChatMessage, ChatRequest, ChatResponse, Usage


def _request(**overrides: object) -> ChatRequest:
    base: dict[str, object] = {
        "model": "deepseek-v4-flash",
        "messages": (ChatMessage(role="user", content="hi"),),
        "temperature": 0.0,
        "seed": 1,
        "max_tokens": 64,
        "thinking": False,
    }
    base.update(overrides)
    return ChatRequest.model_validate(base)


def _response() -> ChatResponse:
    return ChatResponse(
        content="hello",
        reasoning=None,
        finish_reason="stop",
        usage=Usage(prompt_tokens=3, completion_tokens=1),
        model="deepseek-v4-flash",
        response_id="id",
        request_sha256="x" * 64,
        latency_ms=12,
        cached=False,
        created_at=datetime(2026, 9, 4, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "change",
    [
        {"seed": 2},
        {"temperature": 0.5},
        {"model": "deepseek-v4-pro"},
        {"thinking": True},
        {"max_tokens": 65},
        {"reasoning_effort": "high"},
        {"messages": (ChatMessage(role="user", content="hi!"),)},
    ],
)
def test_key_changes_with_output_determining_fields(
    tmp_path: Path, change: dict[str, object]
) -> None:
    cache = ResponseCache(tmp_path, provider="deepseek")
    assert cache.key(_request()) != cache.key(_request(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"timeout_s": 5.0},
        {"use_cache": True},
        {"attribution": Attribution(arm="b", unit_id="u9")},
    ],
)
def test_key_ignores_bookkeeping_fields(tmp_path: Path, change: dict[str, object]) -> None:
    cache = ResponseCache(tmp_path, provider="deepseek")
    assert cache.key(_request()) == cache.key(_request(**change))


def test_key_includes_provider_and_version(tmp_path: Path) -> None:
    a = ResponseCache(tmp_path, provider="deepseek").key(_request())
    b = ResponseCache(tmp_path, provider="other").key(_request())
    assert a != b


def test_roundtrip_marks_cached_and_uses_fanout(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, provider="deepseek")
    request = _request()
    assert cache.get(request) is None
    assert cache.misses == 1
    path = cache.put(request, _response())
    key = cache.key(request)
    assert path == tmp_path / "deepseek" / key[:2] / f"{key}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["cache_version"] == CACHE_VERSION
    assert stored["request"]["seed"] == 1
    assert stored["response"]["cached"] is False
    hit = cache.get(request)
    assert hit is not None
    assert hit.cached is True
    assert hit.content == "hello"
    assert cache.hits == 1


def test_corrupt_entry_is_infra_error(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path, provider="deepseek")
    request = _request()
    path = cache.put(request, _response())
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InfraError) as info:
        cache.get(request)
    assert info.value.kind == "corrupt_file"
    stored = json.loads(cache.put(request, _response()).read_text(encoding="utf-8"))
    stored["key"] = "0" * 64
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(InfraError, match="does not match"):
        cache.get(request)
