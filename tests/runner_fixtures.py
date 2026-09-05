"""Synthetic Evo-Bench rollout artifacts for offline runner tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROLLOUT_LOG = """\
02:43:30 [INFO] [+    0.0s] rollout start id=rollout-abc task=t domain=general max_steps=40
02:44:07 [INFO] [+   36.5s] step 4 - model returned in 25.8s, tokens=6514 (prompt=3254, completion=3260)
02:44:07 [INFO] [+   36.5s] step 4 - shell exit=0 in 0.0s: for d in 2026-02-25; do echo $d; done
02:44:07 [INFO] [+   36.5s] step 5/40 - calling model (elapsed 36.5s)
02:44:32 [INFO] [+   61.5s] step 5 - model returned in 25.0s, tokens=9625 (prompt=6606, completion=3019)
02:44:34 [INFO] [+   62.8s] step 5 - no tool calls, finishing with content answer
02:44:34 [INFO] [+   62.8s] rollout end exit_reason=assistant_no_tool_call steps=5 duration=62.8s total_tokens=16139 errors=0
"""


def fake_trajectory(
    *,
    commands: list[str],
    final_answer: str,
    exit_reason: str = "finished",
    reasoning: bool = True,
    prompt_per_step: int = 100,
    completion_per_step: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A trajectory.json / metadata.json pair shaped like the seed harness writes them."""
    entries: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
    ]
    now = time.time()
    step = 0
    for step, command in enumerate(commands, start=1):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{step}",
                    "type": "function",
                    "function": {
                        "name": "run_shell_command",
                        "arguments": json.dumps({"command": command}),
                    },
                }
            ],
        }
        if reasoning:
            message["reasoning_content"] = f"thinking about step {step}"
        messages.append(message)
        entries.append(
            {
                "step": step,
                "role": "assistant",
                "message": message,
                "model_call_seconds": 1.5,
                "token_usage": {
                    "prompt_tokens": prompt_per_step,
                    "completion_tokens": completion_per_step,
                    "total_tokens": prompt_per_step + completion_per_step,
                    "cached_tokens": 0,
                },
                "completed_at": now + step,
            }
        )
        output = {"stdout": "ok", "stderr": "", "exit_code": 0, "duration_seconds": 0.1}
        messages.append(
            {"role": "tool", "tool_call_id": f"call_{step}", "content": json.dumps(output)}
        )
        entries.append(
            {
                "step": step,
                "role": "tool",
                "tool_call": {
                    "id": f"call_{step}",
                    "name": "run_shell_command",
                    "arguments": {"command": command},
                },
                "tool_output": {"content": json.dumps(output), **output},
                "completed_at": now + step + 0.5,
            }
        )
    step += 1
    finish_message: dict[str, Any] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{step}",
                "type": "function",
                "function": {"name": "finish", "arguments": json.dumps({"answer": final_answer})},
            }
        ],
    }
    if reasoning:
        finish_message["reasoning_content"] = "done"
    messages.append(finish_message)
    entries.append(
        {
            "step": step,
            "role": "assistant",
            "message": finish_message,
            "model_call_seconds": 1.0,
            "token_usage": {
                "prompt_tokens": prompt_per_step,
                "completion_tokens": completion_per_step,
                "total_tokens": prompt_per_step + completion_per_step,
                "cached_tokens": 0,
            },
            "completed_at": now + step,
        }
    )
    entries.append(
        {
            "step": step,
            "role": "tool",
            "tool_call": {
                "id": f"call_{step}",
                "name": "finish",
                "arguments": {"answer": final_answer},
            },
            "tool_output": {"content": json.dumps({"accepted": True, "answer": final_answer})},
            "completed_at": now + step + 0.5,
        }
    )
    steps = step
    trajectory = {"rollout_id": "rollout-fake", "messages": messages, "trajectory": entries}
    metadata = {
        "rollout_id": "rollout-fake",
        "task_id": "t",
        "task_domain": "search",
        "policy_harness_revision": "deadbeefdeadbeef",
        "model_config_id": "deepseek-v4-flash",
        "duration_seconds": 12.5,
        "exit_reason": exit_reason,
        "final_answer": final_answer,
        "artifact_path": "/ws",
        "runtime_errors": [],
        "token_usage": {
            "prompt_tokens": prompt_per_step * steps,
            "completion_tokens": completion_per_step * steps,
            "total_tokens": (prompt_per_step + completion_per_step) * steps,
            "cached_tokens": 0,
        },
        "log_path": "/x/rollout.log",
        "steps": steps,
    }
    return trajectory, metadata


def write_rollout_files(
    rollout_dir: Path, trajectory: dict[str, Any], metadata: dict[str, Any]
) -> None:
    rollout_dir.mkdir(parents=True, exist_ok=True)
    (rollout_dir / "trajectory.json").write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
    (rollout_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (rollout_dir / "rollout.log").write_text(
        "02:00:00 [INFO] [+    0.0s] rollout start\n", encoding="utf-8"
    )
