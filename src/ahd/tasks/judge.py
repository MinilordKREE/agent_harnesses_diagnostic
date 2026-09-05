"""The judge as a measurement instrument: every judge call goes through ``ahd.llm``.

No reference source: written fresh for ahd (see docs/reuse/M1.md).

Two duck-types are satisfied, both verified against the code that calls them:

* Evo-Bench's scorer calls ``judge_client.create(messages=...)`` and reads
  ``response.choices[0].message.content`` and ``response.usage`` through ``usage_to_dict``
  (``evobench/evaluation/scorer.py`` lines 256, 369, 602, 939); its Claw grader reads
  ``judge_client.config.model / api_base / api_key_env`` (line 1114).
* Claw-Eval's ``LLMJudge`` builds ``OpenAI(api_key=..., base_url=...)`` and calls
  ``client.chat.completions.create(model=..., messages=..., temperature=0.0, max_tokens=8192)``
  (``claw_eval/graders/llm_judge.py`` lines 67, 89-95). :func:`patched_claw_judge` replaces the
  single name ``claw_eval.graders.llm_judge.OpenAI`` with a shim around this client, leaving
  Claw's retry loop, parsing and call log untouched.

Multimodal content (a list of parts) is refused with :class:`UnsupportedJudgeRequestError`; the
GDPval rubric judge catches it and retries text-only, recording
``image_grading.used = false`` and the error (scorer.py lines 647-657).
"""

from __future__ import annotations

import contextlib
import importlib
import logging
from collections.abc import Iterator
from typing import Any

from openai.types.chat import ChatCompletion

from ahd.core.config import JudgeConfig, StrictModel
from ahd.errors import InfraError
from ahd.llm.provider import Provider
from ahd.llm.types import Attribution, ChatMessage, ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

JUDGE_ARM = "judge"
CLAW_JUDGE_MODULE = "claw_eval.graders.llm_judge"
CLAW_PATCH_TARGET = "OpenAI"


class UnsupportedJudgeRequestError(ValueError):
    """Tool calls or multimodal content: not something this text judge does."""


class JudgeConfigView(StrictModel):
    """The subset of Evo-Bench ``ModelConfig`` the scorer and Claw grader read."""

    provider: str = "openai-compatible"
    model: str
    api_base: str
    api_key_env: str
    temperature: float
    reasoning_effort: str | None = None
    max_output_tokens: int
    timeout_seconds: int


def to_chat_completion(response: ChatResponse, *, model: str) -> ChatCompletion:
    """Rebuild the OpenAI wire shape that downstream code reads (content and usage)."""
    usage = response.usage
    data: dict[str, Any] = {
        "id": response.response_id or "ahd-judge",
        "object": "chat.completion",
        "created": int(response.created_at.timestamp()),
        "model": response.model or model,
        "choices": [
            {
                "index": 0,
                "finish_reason": response.finish_reason,
                "message": {"role": "assistant", "content": response.content},
            }
        ],
        "usage": {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "prompt_cache_hit_tokens": usage.cache_hit_prompt_tokens,
            "prompt_cache_miss_tokens": usage.cache_miss_prompt_tokens,
            "completion_tokens_details": {"reasoning_tokens": usage.reasoning_tokens},
        },
    }
    return ChatCompletion.construct(**data)


class AhdJudgeClient:
    """Judge calls as ledgered, cached ``ChatRequest``s with ``arm="judge"``."""

    def __init__(
        self,
        provider: Provider,
        *,
        config: JudgeConfig,
        api_base: str,
        seed: int = 0,
        api_key_env: str = "DEEPSEEK_API_KEY",
        unit_id: str = "unbound",
        cache_scope: str | None = None,
    ) -> None:
        self._provider = provider
        self._judge = config
        self._seed = seed
        self._unit_id = unit_id
        self._cache_scope = cache_scope
        self.config = JudgeConfigView(
            model=config.model,
            api_base=api_base,
            api_key_env=api_key_env,
            temperature=config.temperature,
            max_output_tokens=config.max_tokens,
            timeout_seconds=int(config.timeout_s),
        )
        self.requests: list[ChatRequest] = []

    def bind(self, *, unit_id: str, cache_scope: str | None) -> AhdJudgeClient:
        """A copy attributed to one task and one artifact hash. Shares the provider."""
        bound = AhdJudgeClient(
            self._provider,
            config=self._judge,
            api_base=self.config.api_base,
            seed=self._seed,
            api_key_env=self.config.api_key_env,
            unit_id=unit_id,
            cache_scope=cache_scope,
        )
        bound.requests = self.requests
        return bound

    @property
    def unit_id(self) -> str:
        return self._unit_id

    @property
    def cache_scope(self) -> str | None:
        return self._cache_scope

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        max_tokens: int | None = None,
        model: str | None = None,
        temperature: float | None = None,
        **unsupported: Any,
    ) -> ChatCompletion:
        if tools:
            raise UnsupportedJudgeRequestError("judge does not accept tool definitions")
        if unsupported:
            raise UnsupportedJudgeRequestError(f"unsupported judge kwargs: {sorted(unsupported)}")
        if model is not None and model != self.config.model:
            raise UnsupportedJudgeRequestError(
                f"caller asked for model {model!r}; judge is fixed to {self.config.model!r}"
            )
        if temperature is not None and temperature != self.config.temperature:
            raise UnsupportedJudgeRequestError(
                f"caller asked for temperature {temperature}; judge is fixed to "
                f"{self.config.temperature}"
            )
        request = ChatRequest(
            model=self.config.model,
            messages=tuple(_to_chat_message(m) for m in messages),
            temperature=self.config.temperature,
            seed=self._seed,
            max_tokens=max_tokens or self._judge.max_tokens,
            thinking=self._judge.thinking,
            timeout_s=self._judge.timeout_s,
            use_cache=self._judge.use_cache,
            attribution=Attribution(arm=JUDGE_ARM, unit_id=self._unit_id),
            cache_scope=self._cache_scope,
        )
        self.requests.append(request)
        response = self._provider.complete(request)
        return to_chat_completion(response, model=self.config.model)


def _to_chat_message(message: dict[str, Any]) -> ChatMessage:
    role = message.get("role")
    content = message.get("content")
    if role not in ("system", "user", "assistant"):
        raise UnsupportedJudgeRequestError(f"unsupported message role {role!r}")
    if not isinstance(content, str):
        raise UnsupportedJudgeRequestError(
            "judge accepts text content only; multimodal parts are not supported"
        )
    return ChatMessage(role=role, content=content)


class _Completions:
    def __init__(self, judge: AhdJudgeClient) -> None:
        self._judge = judge

    def create(self, **kwargs: Any) -> ChatCompletion:
        return self._judge.create(**kwargs)


class _Chat:
    def __init__(self, judge: AhdJudgeClient) -> None:
        self.completions = _Completions(judge)


class OpenAIShim:
    """Looks like ``openai.OpenAI(...)`` to Claw-Eval's ``LLMJudge``; routes to the ahd judge."""

    def __init__(self, judge: AhdJudgeClient, **_ignored: Any) -> None:
        self.chat = _Chat(judge)


@contextlib.contextmanager
def patched_claw_judge(judge: AhdJudgeClient) -> Iterator[None]:
    """Route Claw-Eval's ``LLMJudge`` through ``judge`` by replacing one module attribute.

    Patch point: ``claw_eval.graders.llm_judge.OpenAI`` (the name ``LLMJudge.__init__`` calls).
    """
    try:
        module = importlib.import_module(CLAW_JUDGE_MODULE)
    except ImportError as exc:
        raise InfraError(
            "claw_eval is not importable; run `make setup-claw` (Evo-Bench's "
            "scripts/setup_claw_eval.sh)",
            kind="claw_eval_missing",
        ) from exc
    original = getattr(module, CLAW_PATCH_TARGET)

    def factory(**kwargs: Any) -> OpenAIShim:
        return OpenAIShim(judge, **kwargs)

    setattr(module, CLAW_PATCH_TARGET, factory)
    logger.debug("patched %s.%s", CLAW_JUDGE_MODULE, CLAW_PATCH_TARGET)
    try:
        yield
    finally:
        setattr(module, CLAW_PATCH_TARGET, original)
