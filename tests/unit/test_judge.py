from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from ahd.core.config import JudgeConfig
from ahd.llm.fake import FakeProvider
from ahd.llm.types import Attribution, ChatMessage, ChatRequest
from ahd.tasks.judge import (
    CLAW_JUDGE_MODULE,
    AhdJudgeClient,
    OpenAIShim,
    UnsupportedJudgeRequestError,
    patched_claw_judge,
    to_chat_completion,
)


def _judge(provider: FakeProvider | None = None, **cfg: Any) -> AhdJudgeClient:
    return AhdJudgeClient(
        provider or FakeProvider('{"score": 0.5, "reasoning": "ok"}'),
        config=JudgeConfig(**cfg),
        api_base="https://api.deepseek.com",
        seed=7,
    )


def test_create_returns_openai_shape_and_records_attribution() -> None:
    provider = FakeProvider("CORRECT: yes\nREASON: matches")
    judge = _judge(provider).bind(unit_id="bc-en-0001", cache_scope="artifact:abc")
    completion = judge.create(
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    assert completion.choices[0].message.content == "CORRECT: yes\nREASON: matches"
    assert completion.usage is not None and completion.usage.prompt_tokens >= 1
    request = provider.requests[0]
    assert request.attribution == Attribution(arm="judge", unit_id="bc-en-0001")
    assert request.cache_scope == "artifact:abc"
    assert request.temperature == 0.0
    assert request.use_cache is True
    assert request.model == "deepseek-v4-pro"
    assert request.seed == 7
    assert judge.config.model == "deepseek-v4-pro"
    assert judge.config.api_key_env == "DEEPSEEK_API_KEY"


def test_create_refuses_tools_multimodal_and_overrides() -> None:
    judge = _judge()
    with pytest.raises(UnsupportedJudgeRequestError, match="tool"):
        judge.create(messages=[{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    with pytest.raises(UnsupportedJudgeRequestError, match="multimodal"):
        judge.create(messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}])
    with pytest.raises(UnsupportedJudgeRequestError, match="model"):
        judge.create(messages=[{"role": "user", "content": "x"}], model="other")
    with pytest.raises(UnsupportedJudgeRequestError, match="temperature"):
        judge.create(messages=[{"role": "user", "content": "x"}], temperature=0.7)
    with pytest.raises(UnsupportedJudgeRequestError, match="role"):
        judge.create(messages=[{"role": "tool", "content": "x"}])
    # Claw's exact call shape is accepted
    judge.create(
        messages=[{"role": "user", "content": "x"}],
        model="deepseek-v4-pro",
        temperature=0.0,
        max_tokens=8192,
    )


def test_cache_scope_changes_cache_key() -> None:
    from ahd.llm.cache import ResponseCache

    cache = ResponseCache(Path("/tmp/unused"), provider="deepseek")
    base = ChatRequest(model="m", messages=(ChatMessage(role="user", content="x"),))
    scoped = base.model_copy(update={"cache_scope": "artifact:1"})
    other = base.model_copy(update={"cache_scope": "artifact:2"})
    assert cache.key(base) != cache.key(scoped) != cache.key(other)


def test_to_chat_completion_carries_usage() -> None:
    response = FakeProvider("hi").complete(
        ChatRequest(model="m", messages=(ChatMessage(role="user", content="a b c"),))
    )
    completion = to_chat_completion(response, model="m")
    assert completion.usage is not None
    assert completion.usage.prompt_tokens == 3
    assert completion.model == "m"


def test_openai_shim_matches_claw_call_shape() -> None:
    provider = FakeProvider('{"score": 1.0, "reasoning": "fine"}')
    shim = OpenAIShim(_judge(provider), api_key="ignored", base_url="ignored")
    resp = shim.chat.completions.create(
        model="deepseek-v4-pro",
        messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        temperature=0.0,
        max_tokens=8192,
    )
    assert resp.choices[0].message.content == '{"score": 1.0, "reasoning": "fine"}'
    assert provider.requests[0].max_tokens == 8192


def _install_fake_claw(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A stand-in for claw_eval.graders.llm_judge with the same constructor and call shape."""
    module = types.ModuleType(CLAW_JUDGE_MODULE)

    class _Sentinel:
        def __init__(self, **kwargs: Any) -> None:
            raise AssertionError("real OpenAI client must not be constructed")

    class LLMJudge:
        def __init__(
            self, model_id: str = "x", api_key: str | None = None, base_url: str = "y"
        ) -> None:
            self.client = module.OpenAI(api_key=api_key or "dummy", base_url=base_url)
            self.model_id = model_id

        def evaluate(self, prompt: str) -> str:
            resp = self.client.chat.completions.create(
                model=self.model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=8192,
            )
            content: str = resp.choices[0].message.content or ""
            return content

    setattr(module, "OpenAI", _Sentinel)  # noqa: B010 - dynamic module attribute
    setattr(module, "LLMJudge", LLMJudge)  # noqa: B010
    pkg = types.ModuleType("claw_eval")
    graders = types.ModuleType("claw_eval.graders")
    monkeypatch.setitem(sys.modules, "claw_eval", pkg)
    monkeypatch.setitem(sys.modules, "claw_eval.graders", graders)
    monkeypatch.setitem(sys.modules, CLAW_JUDGE_MODULE, module)
    return module


def test_patched_claw_judge_swaps_only_the_openai_name(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _install_fake_claw(monkeypatch)
    original = module.OpenAI
    provider = FakeProvider('{"score": 0.25, "reasoning": "meh"}')
    judge = _judge(provider).bind(unit_id="claw-1", cache_scope="artifact:z")
    with patched_claw_judge(judge):
        assert module.OpenAI is not original
        result = module.LLMJudge(model_id="deepseek-v4-pro", api_key=None, base_url="u").evaluate(
            "q"
        )
    assert result == '{"score": 0.25, "reasoning": "meh"}'
    assert module.OpenAI is original
    assert provider.requests[0].attribution.unit_id == "claw-1"


def test_patched_claw_judge_with_real_claw_eval() -> None:
    llm_judge = pytest.importorskip("claw_eval.graders.llm_judge")
    provider = FakeProvider('{"score": 0.5, "reasoning": "half"}')
    judge = _judge(provider).bind(unit_id="claw-real", cache_scope=None)
    with patched_claw_judge(judge):
        real = llm_judge.LLMJudge(model_id="deepseek-v4-pro", api_key=None, base_url="unused")
        result = real.evaluate("task", "conversation", "actions", "rubric")
    assert result.score == 0.5
    assert result.reasoning == "half"
    assert provider.requests[0].attribution.arm == "judge"
    assert provider.requests[0].max_tokens == 8192
