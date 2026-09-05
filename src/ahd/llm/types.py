"""Typed chat request/response records shared by every provider.

No reference source: written fresh for ahd (see docs/reuse/M0.md). The usage field names
follow Evo-Bench ``usage_to_dict`` (evobench/models/client.py lines 456-543, Apache-2.0),
which already normalises DeepSeek's ``prompt_cache_hit_tokens``; no code was copied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ahd.core.config import ReasoningEffort, StrictModel
from ahd.core.hashing import JsonValue, to_json_value

type Role = Literal["system", "user", "assistant"]


class ChatMessage(StrictModel):
    role: Role
    content: str


class Attribution(StrictModel):
    """Who is paying for a call in the experiment's terms. Excluded from the cache key."""

    arm: str = "none"
    unit_id: str = "none"


class ChatRequest(StrictModel):
    """Everything that determines a completion, plus per-call bookkeeping flags.

    ``seed`` is a replicate identifier. DeepSeek's chat API does not accept a ``seed``
    parameter (verified against the API reference on 2026-09-04), so it is never sent on the
    wire; it is part of the cache key and every ledger row so that distinct seeds yield
    distinct samples and can be told apart afterwards.
    """

    model: str
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int = 0
    max_tokens: int = Field(default=4096, ge=1)
    thinking: bool = False
    reasoning_effort: ReasoningEffort | None = None
    timeout_s: float = Field(default=120.0, gt=0.0)
    use_cache: bool = False
    attribution: Attribution = Attribution()
    cache_scope: str | None = None
    """Opaque salt folded into the cache key. Judge calls set ``artifact:<sha256>`` so a cached
    verdict is bound to the exact artifact it judged, even if two artifacts render to the same
    judge prompt."""

    def cache_payload(self) -> dict[str, JsonValue]:
        """The fields that determine the output, plus ``cache_scope``. Timeout, cache flag and
        attribution are not included."""
        body = to_json_value(
            {
                "model": self.model,
                "messages": [m.model_dump() for m in self.messages],
                "temperature": self.temperature,
                "seed": self.seed,
                "max_tokens": self.max_tokens,
                "thinking": self.thinking,
                "reasoning_effort": self.reasoning_effort,
                "cache_scope": self.cache_scope,
            }
        )
        if not isinstance(body, dict):  # pragma: no cover - to_json_value of a dict is a dict
            raise TypeError("cache payload must be a JSON object")
        return body


class Usage(StrictModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cache_hit_prompt_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)

    @property
    def cache_miss_prompt_tokens(self) -> int:
        return self.prompt_tokens - self.cache_hit_prompt_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ChatResponse(StrictModel):
    content: str
    reasoning: str | None
    finish_reason: str
    usage: Usage
    model: str
    response_id: str | None
    request_sha256: str
    latency_ms: int
    cached: bool
    created_at: datetime
