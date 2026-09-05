"""In-memory :class:`Provider` for unit tests and dry runs.

No reference source: written fresh for ahd (see docs/reuse/M0.md). AutoSaddler ships a
``FakeAgentProvider`` in-tree (src/autosaddler/v2/providers/fake.py, MIT) for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ahd.core.hashing import sha256_of
from ahd.llm.types import ChatRequest, ChatResponse, Usage


class FakeProvider:
    """Deterministic replies; records every request. Never touches a ledger or the network."""

    def __init__(self, reply: str | Callable[[ChatRequest], str] = "ok") -> None:
        self._reply = reply
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        content = self._reply(request) if callable(self._reply) else self._reply
        prompt_tokens = sum(len(m.content.split()) for m in request.messages)
        return ChatResponse(
            content=content,
            reasoning=None,
            finish_reason="stop",
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=len(content.split())),
            model=request.model,
            response_id=None,
            request_sha256=sha256_of({"provider": "fake", "request": request.cache_payload()}),
            latency_ms=0,
            cached=False,
            created_at=datetime.now(UTC),
        )
