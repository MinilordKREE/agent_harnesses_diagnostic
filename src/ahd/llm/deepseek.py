"""DeepSeek client over the OpenAI-compatible chat completions endpoint.

No reference source: written fresh for ahd (see docs/reuse/M0.md). The ``max_retries=0``
choice follows VeRO (vero/src/vero/interpret/labeling/client.py line 34, MIT) so the SDK never
retries silently; the thin-wrapper shape follows AgentDebug ``engines/openai.py`` (MIT) and
Evo-Bench ``OpenAICompatibleClient`` (Apache-2.0) as patterns only.

Wire format, verified live on 2026-09-04 (see docs/CONVENTIONS.md, "Thinking mode"):
thinking is toggled with ``extra_body={"thinking": {"type": "enabled"|"disabled"}}``;
``reasoning_effort`` is a **top-level** request parameter (the guide's placement). The API
reference's nested ``thinking.reasoning_effort`` is silently ignored by the server, so it is
not sent. The reasoning text comes back as ``message.reasoning_content``; usage carries
``prompt_cache_hit_tokens`` and ``completion_tokens_details.reasoning_tokens``; ``seed`` is
not a supported parameter and is not sent; ``temperature`` has no effect in thinking mode and
is not sent then.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import openai
from openai.types.chat import ChatCompletion
from pydantic import SecretStr

from ahd.core.config import RetryConfig
from ahd.core.hashing import sha256_of
from ahd.errors import InfraError
from ahd.llm.cache import ResponseCache
from ahd.llm.ledger import Ledger
from ahd.llm.pricing import PricingTable
from ahd.llm.retry import RetryEvent, run_with_retry
from ahd.llm.types import ChatMessage, ChatRequest, ChatResponse, Usage

logger = logging.getLogger(__name__)

PROVIDER_NAME = "deepseek"
INSUFFICIENT_RESOURCE = "insufficient_system_resource"

type Transport = Callable[..., ChatCompletion]
"""``chat.completions.create``-shaped callable returning a non-streamed completion."""


def make_openai_transport(*, api_key: SecretStr, base_url: str, timeout_s: float) -> Transport:
    client = openai.OpenAI(
        api_key=api_key.get_secret_value(), base_url=base_url, max_retries=0, timeout=timeout_s
    )

    def create(**kwargs: Any) -> ChatCompletion:
        result = client.chat.completions.create(**kwargs)
        if not isinstance(result, ChatCompletion):
            raise InfraError("provider returned a streaming response", kind="protocol")
        return result

    return create


def build_wire_request(request: ChatRequest) -> dict[str, Any]:
    """Translate a :class:`ChatRequest` into ``chat.completions.create`` keyword arguments."""
    body: dict[str, Any] = {
        "model": request.model,
        "messages": [_wire_message(message) for message in request.messages],
        "max_tokens": request.max_tokens,
        "stream": False,
        "extra_body": {"thinking": {"type": "enabled" if request.thinking else "disabled"}},
        "timeout": request.timeout_s,
    }
    if request.thinking:
        if request.reasoning_effort is not None:
            body["reasoning_effort"] = request.reasoning_effort
    else:
        body["temperature"] = request.temperature
    return body


def _int_attr(obj: object, name: str) -> int:
    value = getattr(obj, name, None)
    return value if isinstance(value, int) else 0


def parse_completion(
    completion: ChatCompletion, *, request_sha256: str, latency_ms: int, created_at: datetime
) -> ChatResponse:
    if not completion.choices:
        raise InfraError("provider returned no choices", kind="empty_response")
    choice = completion.choices[0]
    if completion.usage is None:
        raise InfraError("provider returned no usage block", kind="missing_usage")
    usage_raw = completion.usage
    details = usage_raw.completion_tokens_details
    usage = Usage(
        prompt_tokens=usage_raw.prompt_tokens,
        completion_tokens=usage_raw.completion_tokens,
        cache_hit_prompt_tokens=_int_attr(usage_raw, "prompt_cache_hit_tokens"),
        reasoning_tokens=_int_attr(details, "reasoning_tokens") if details is not None else 0,
    )
    reasoning = getattr(choice.message, "reasoning_content", None)
    return ChatResponse(
        content=choice.message.content or "",
        reasoning=reasoning if isinstance(reasoning, str) else None,
        finish_reason=choice.finish_reason,
        usage=usage,
        model=completion.model,
        response_id=completion.id or None,
        request_sha256=request_sha256,
        latency_ms=latency_ms,
        cached=False,
        created_at=created_at,
    )


class DeepSeekClient:
    """Implements :class:`ahd.llm.provider.Provider` with retry, opt-in cache and ledger."""

    def __init__(
        self,
        *,
        transport: Transport,
        ledger: Ledger,
        pricing: PricingTable,
        retry: RetryConfig,
        cache: ResponseCache | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport
        self._ledger = ledger
        self._pricing = pricing
        self._retry = retry
        self._cache = cache
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep

    def request_sha256(self, request: ChatRequest) -> str:
        """The key a request would have in the cache (or the equivalent digest without one)."""
        if self._cache is not None:
            return self._cache.key(request)
        return sha256_of({"provider": PROVIDER_NAME, "request": request.cache_payload()})

    def _call_once(self, wire: dict[str, Any]) -> ChatCompletion:
        completion = self._transport(**wire)
        if completion.choices and completion.choices[0].finish_reason == INSUFFICIENT_RESOURCE:
            raise InfraError(
                "provider reported insufficient system resources",
                kind=INSUFFICIENT_RESOURCE,
                retryable=True,
            )
        return completion

    def complete(self, request: ChatRequest) -> ChatResponse:
        request_sha = self.request_sha256(request)
        if request.use_cache:
            if self._cache is None:
                raise InfraError(
                    "request asked for the cache but the client has none configured",
                    kind="cache_unconfigured",
                )
            hit = self._cache.get(request)
            if hit is not None:
                logger.info("cache hit", extra={"request_sha256": request_sha})
                self._ledger.record_call(request, hit, cost=None, attempt=0)
                return hit

        retries = 0

        def on_retry(event: RetryEvent) -> None:
            nonlocal retries
            retries += 1
            self._ledger.record_infra_retry(
                request,
                attempt=event.attempt,
                kind=event.kind,
                status_code=event.status_code,
                error=event.error,
                request_sha256=request_sha,
            )

        wire = build_wire_request(request)
        started_at = self._now()
        t0 = time.perf_counter()
        try:
            completion = run_with_retry(
                lambda: self._call_once(wire),
                policy=self._retry,
                on_retry=on_retry,
                sleep=self._sleep,
            )
        except InfraError as exc:
            self._ledger.record_infra_failure(request, exc, request_sha256=request_sha)
            logger.error("infra failure: %s", exc, extra={"error_kind": exc.kind})
            raise
        latency_ms = int((time.perf_counter() - t0) * 1000)
        response = parse_completion(
            completion, request_sha256=request_sha, latency_ms=latency_ms, created_at=started_at
        )
        cost = self._pricing.cost(request.model, response.usage, started_at)
        self._ledger.record_call(request, response, cost=cost, attempt=retries + 1)
        if request.use_cache and self._cache is not None:
            self._cache.put(request, response)
        return response


def _wire_message(message: ChatMessage) -> dict[str, object]:
    """OpenAI wire form: text stays a string, multimodal parts become a JSON list."""
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    return {"role": message.role, "content": [dict(part) for part in message.content]}
