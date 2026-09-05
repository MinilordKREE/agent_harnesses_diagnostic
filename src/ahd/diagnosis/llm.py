"""The diagnosis model: one fixed, cached, ledgered JSON-answering client (arm ``diagnosis``).

No reference source: written fresh for ahd (see docs/reuse/M3.md). Every diagnosis-side model
call (error signal, genuineness judge, leakage probe) goes through ``ask_json``: temperature
0, ``deepseek-v4-pro`` by default, response cache on, ``cache_scope`` bound to the evidence
hash so a cached verdict is tied to the exact trajectory it judged.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.errors import TaskFailure
from ahd.llm.provider import Provider
from ahd.llm.types import Attribution, ChatMessage, ChatRequest, ChatResponse

DIAGNOSIS_ARM = "diagnosis"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class DiagnosisModelConfig(StrictModel):
    model: str = "deepseek-v4-pro"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=64)
    timeout_s: float = Field(default=300.0, gt=0)
    thinking: bool = False
    use_cache: bool = True


class JsonAnswer(StrictModel):
    data: dict[str, Any]
    response: ChatResponse
    prompt_sha256: str


class MalformedModelOutput(TaskFailure):
    """The diagnosis model did not return the requested JSON object. Counted as a task
    failure of the diagnosis instrument, never as an infra error."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="malformed_model_output")


def parse_json_object(text: str) -> dict[str, Any]:
    body = _FENCE.sub("", text.strip()).strip()
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            raise MalformedModelOutput(f"no JSON object in model output: {text[:200]!r}") from None
        try:
            value = json.loads(body[start : end + 1])
        except json.JSONDecodeError as exc:
            raise MalformedModelOutput(f"invalid JSON in model output: {exc}") from exc
    if not isinstance(value, dict):
        raise MalformedModelOutput("model output is not a JSON object")
    return value


class DiagnosisLLM:
    def __init__(
        self, provider: Provider, *, config: DiagnosisModelConfig | None = None, seed: int = 0
    ) -> None:
        self._provider = provider
        self.config = config or DiagnosisModelConfig()
        self._seed = seed
        self.requests: list[ChatRequest] = []

    def ask_json(
        self, prompt: str, *, unit_id: str, cache_scope: str, system: str | None = None
    ) -> JsonAnswer:
        messages: list[ChatMessage] = []
        if system:
            messages.append(ChatMessage(role="system", content=system))
        messages.append(ChatMessage(role="user", content=prompt))
        request = ChatRequest(
            model=self.config.model,
            messages=tuple(messages),
            temperature=self.config.temperature,
            seed=self._seed,
            max_tokens=self.config.max_tokens,
            thinking=self.config.thinking,
            timeout_s=self.config.timeout_s,
            use_cache=self.config.use_cache,
            attribution=Attribution(arm=DIAGNOSIS_ARM, unit_id=unit_id),
            cache_scope=cache_scope,
        )
        self.requests.append(request)
        response = self._provider.complete(request)
        return JsonAnswer(
            data=parse_json_object(response.content),
            response=response,
            prompt_sha256=sha256_of({"system": system, "prompt": prompt}),
        )
