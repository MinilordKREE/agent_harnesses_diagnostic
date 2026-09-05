from __future__ import annotations

from typing import Any, cast

import pytest

from ahd.core.hashing import JsonValue
from ahd.errors import InfraError
from ahd.llm.types import Usage
from ahd.runner.serper import count_serper_calls
from ahd.runner.trajectory import (
    events_from_rollout_log,
    events_from_trajectory,
    reconcile_usage,
    usage_from_events,
)
from tests.runner_fixtures import ROLLOUT_LOG, fake_trajectory


def j(value: object) -> Any:
    return cast(Any, value)


CTX: dict[str, JsonValue] = {"task_id": "t", "replicate": "r1", "attempt": 1, "mode": "normal"}


def test_events_from_trajectory_align_by_step() -> None:
    trajectory, metadata = fake_trajectory(
        commands=["ls", "curl https://google.serper.dev/search -d q=x"], final_answer="42"
    )
    events = events_from_trajectory(trajectory, metadata, context=CTX)
    kinds = [e.kind for e in events]
    assert kinds[0] == "rollout_start" and kinds[-2:] == ["final", "rollout_end"]
    assert (
        kinds.count("model_call") == 3
        and kinds.count("tool_call") == 3
        and kinds.count("observation") == 3
    )
    steps = [
        e.payload["step"] for e in events if e.kind in ("model_call", "tool_call", "observation")
    ]
    assert steps == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    first = next(e for e in events if e.kind == "model_call")
    assert first.payload["reasoning_present"] is True and first.payload["tool_call_ids"] == [
        "call_1"
    ]
    obs = next(e for e in events if e.kind == "observation")
    assert obs.payload["exit_code"] == 0 and j(obs.payload["output"])["stdout"] == "ok"
    usage = usage_from_events(events)
    assert usage.steps == 3 and usage.reasoning_steps == 3
    assert usage.usage.prompt_tokens == 300 and usage.usage.completion_tokens == 30
    reconcile_usage(usage.usage, metadata["token_usage"])
    with pytest.raises(InfraError) as info:
        reconcile_usage(Usage(prompt_tokens=1, completion_tokens=30), metadata["token_usage"])
    assert info.value.kind == "usage_mismatch"
    assert [c.step for c in count_serper_calls(events)] == [2]


def test_events_from_rollout_log_are_partial() -> None:
    events = events_from_rollout_log(ROLLOUT_LOG, context=CTX)
    assert events[0].payload["partial"] is True
    model_calls = [e for e in events if e.kind == "model_call"]
    assert [e.payload["step"] for e in model_calls] == [4, 5]
    assert model_calls[0].payload["usage"] == {
        "prompt_tokens": 3254,
        "completion_tokens": 3260,
        "total_tokens": 6514,
    }
    tools = [e for e in events if e.kind == "tool_call"]
    assert j(tools[0].payload["arguments"])["command"].startswith("for d in")
    final = next(e for e in events if e.kind == "final")
    assert final.payload["exit_reason"] == "assistant_no_tool_call" and final.payload["steps"] == 5
    end = events[-1]
    assert end.payload["reconstructed_from"] == "rollout.log" and end.payload["token_usage"] == {
        "total_tokens": 16139
    }
    assert usage_from_events(events).usage.prompt_tokens == 3254 + 6606
