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
import contextvars
import importlib
import logging
import threading
from collections.abc import Iterator
from typing import Any

from openai.types.chat import ChatCompletion

from ahd.core.config import JudgeConfig, StrictModel
from ahd.core.hashing import sha256_of, to_json_value
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
        arm: str = JUDGE_ARM,
    ) -> None:
        self._provider = provider
        self.arm = arm
        """Ledger arm: ``judge`` for the primary instrument, e.g. ``judge_vision`` for a
        secondary judge whose spend must stay separable."""
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
        self.responses: list[str] = []
        """Reply contents, in call order (shared with bound copies like ``requests``)."""
        self._repeats: dict[str, int] = {}
        """Per bound client: how often an identical request was already sent. A repeat is a
        caller-side retry (claw-eval's ``LLMJudge`` re-sends the same messages up to five
        times); it gets a ``|retry:<n>`` cache-scope salt so it is a real call, not a cache hit
        (owner decision, M3.1)."""
        self.max_retry_index = 0

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
            arm=self.arm,
        )
        bound.requests = self.requests
        bound.responses = self.responses
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
        chat_messages = tuple(
            _to_chat_message(m, multimodal=self._judge.multimodal) for m in messages
        )
        key = sha256_of(
            {"messages": [m.model_dump() for m in chat_messages], "max_tokens": max_tokens}
        )
        repeat = self._repeats.get(key, 0)
        self._repeats[key] = repeat + 1
        self.max_retry_index = max(self.max_retry_index, repeat)
        scope = self._cache_scope
        if repeat:
            scope = f"{scope or ''}|retry:{repeat}"
        request = ChatRequest(
            model=self.config.model,
            messages=chat_messages,
            temperature=self.config.temperature,
            seed=self._seed,
            max_tokens=max_tokens or self._judge.max_tokens,
            thinking=self._judge.thinking,
            timeout_s=self._judge.timeout_s,
            use_cache=self._judge.use_cache,
            attribution=Attribution(arm=self.arm, unit_id=self._unit_id),
            cache_scope=scope,
        )
        self.requests.append(request)
        response = self._provider.complete(request)
        self.responses.append(response.content)
        return to_chat_completion(response, model=self.config.model)


def _to_chat_message(message: dict[str, Any], *, multimodal: bool = False) -> ChatMessage:
    role = message.get("role")
    content = message.get("content")
    if role not in ("system", "user", "assistant"):
        raise UnsupportedJudgeRequestError(f"unsupported message role {role!r}")
    if isinstance(content, str):
        return ChatMessage(role=role, content=content)
    if not multimodal:
        raise UnsupportedJudgeRequestError(
            "judge accepts text content only; multimodal parts are not supported"
        )
    if not isinstance(content, list) or not content:
        raise UnsupportedJudgeRequestError("multimodal content must be a non-empty list")
    parts: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") not in ("text", "image_url"):
            raise UnsupportedJudgeRequestError(f"unsupported content part {part!r}"[:200])
        parts.append(dict(part))
    converted = to_json_value(parts)
    assert isinstance(converted, list)
    return ChatMessage(role=role, content=tuple(p for p in converted if isinstance(p, dict)))


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


_current_judge: contextvars.ContextVar[AhdJudgeClient | None] = contextvars.ContextVar(
    "ahd_claw_judge", default=None
)
_patch_lock = threading.Lock()
_patch_depth = 0
_patch_original: Any = None


def _factory(**kwargs: Any) -> OpenAIShim:
    judge = _current_judge.get()
    if judge is None:
        raise InfraError(
            "Claw-Eval judge constructed outside patched_claw_judge", kind="judge_unbound"
        )
    return OpenAIShim(judge, **kwargs)


@contextlib.contextmanager
def patched_claw_judge(judge: AhdJudgeClient) -> Iterator[None]:
    """Route Claw-Eval's ``LLMJudge`` through ``judge`` by replacing one module attribute.

    Patch point: ``claw_eval.graders.llm_judge.OpenAI`` (the name ``LLMJudge.__init__`` calls).
    Thread-safe (M2.1): the module attribute is installed once and refcounted, and the judge
    bound to the calling thread lives in a context variable, so concurrent scorings of
    different tasks each reach their own judge.
    """
    global _patch_depth, _patch_original
    try:
        module = importlib.import_module(CLAW_JUDGE_MODULE)
    except ImportError as exc:
        raise InfraError(
            "claw_eval is not importable; run `make setup-claw` (Evo-Bench's "
            "scripts/setup_claw_eval.sh)",
            kind="claw_eval_missing",
        ) from exc
    with _patch_lock:
        if _patch_depth == 0:
            _patch_original = getattr(module, CLAW_PATCH_TARGET)
            setattr(module, CLAW_PATCH_TARGET, _factory)
            logger.debug("patched %s.%s", CLAW_JUDGE_MODULE, CLAW_PATCH_TARGET)
        _patch_depth += 1
    token = _current_judge.set(judge)
    try:
        yield
    finally:
        _current_judge.reset(token)
        with _patch_lock:
            _patch_depth -= 1
            if _patch_depth == 0:
                setattr(module, CLAW_PATCH_TARGET, _patch_original)
                _patch_original = None
