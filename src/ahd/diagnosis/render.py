"""Condensed trajectory text for diagnosis prompts.

No reference source: written fresh for ahd (see docs/reuse/M3.md). One block per step:
reasoning head, assistant content head, tool calls with arguments, observation heads. A
character budget trims the middle steps first so that the first divergence and the end of the
run survive.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ahd.diagnosis.align import as_dict

_WS = re.compile(r"\s+")


def _head(text: object, limit: int) -> str:
    flat = _WS.sub(" ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _observation_text(output: Any) -> str:
    if isinstance(output, dict):
        if "stdout" in output or "stderr" in output:
            parts = []
            if output.get("exit_code") is not None:
                parts.append(f"exit={output.get('exit_code')}")
            if output.get("timeout"):
                parts.append("TIMEOUT")
            if output.get("stdout"):
                parts.append(f"stdout: {output['stdout']}")
            if output.get("stderr"):
                parts.append(f"stderr: {output['stderr']}")
            return " | ".join(parts)
        return str(output.get("content", ""))
    return str(output or "")


def step_blocks(
    trajectory: dict[str, Any],
    *,
    reasoning_chars: int = 240,
    content_chars: int = 300,
    argument_chars: int = 300,
    observation_chars: int = 240,
) -> list[tuple[int, str]]:
    blocks: dict[int, list[str]] = {}
    for entry in trajectory.get("trajectory", []):
        if not isinstance(entry, dict):
            continue
        step = int(entry.get("step", 0))
        lines = blocks.setdefault(step, [])
        if entry.get("role") == "assistant":
            message = as_dict(entry.get("message"))
            if message.get("reasoning_content"):
                lines.append(f"  reasoning: {_head(message['reasoning_content'], reasoning_chars)}")
            if message.get("content"):
                lines.append(f"  says: {_head(message['content'], content_chars)}")
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = as_dict(call.get("function"))
                args = fn.get("arguments", "")
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                lines.append(f"  call {fn.get('name', '?')}({_head(args, argument_chars)})")
            if entry.get("substituted"):
                lines.append("  [substituted reference action]")
        elif entry.get("role") == "tool":
            call = as_dict(entry.get("tool_call"))
            lines.append(
                f"  observation[{call.get('name', '?')}]: "
                f"{_head(_observation_text(entry.get('tool_output')), observation_chars)}"
            )
    return [(step, "\n".join([f"step {step}:", *lines])) for step, lines in sorted(blocks.items())]


def condensed(
    trajectory: dict[str, Any], *, budget_chars: int = 20_000, keep_steps: tuple[int, ...] = ()
) -> str:
    """Join step blocks; when over budget, drop middle steps (never the first two, the last
    two, or ``keep_steps``) and mark the gap."""
    blocks = step_blocks(trajectory)
    if not blocks:
        return "(no steps)"
    text = "\n".join(b for _, b in blocks)
    if len(text) <= budget_chars:
        return text
    protected = set(keep_steps) | {
        blocks[0][0],
        blocks[min(1, len(blocks) - 1)][0],
        blocks[-1][0],
        blocks[max(0, len(blocks) - 2)][0],
    }
    for s in list(keep_steps):
        protected.update({s - 1, s + 1})
    kept: list[tuple[int, str]] = list(blocks)
    droppable = [i for i, (s, _) in enumerate(blocks) if s not in protected]
    # drop from the middle outwards
    droppable.sort(key=lambda i: abs(i - len(blocks) / 2))
    dropped: set[int] = set()
    while (
        len("\n".join(b for i, (_, b) in enumerate(kept) if i not in dropped)) > budget_chars
        and droppable
    ):
        dropped.add(droppable.pop(0))
    out: list[str] = []
    gap = 0
    for i, (_, b) in enumerate(kept):
        if i in dropped:
            gap += 1
            continue
        if gap:
            out.append(f"  … {gap} step(s) omitted …")
            gap = 0
        out.append(b)
    if gap:
        out.append(f"  … {gap} step(s) omitted …")
    return "\n".join(out)


def action_text(actions: Any) -> str:
    parts = []
    for a in actions or ():
        args = json.dumps(a.arguments, ensure_ascii=False, sort_keys=True)
        parts.append(f"{a.name}({_head(args, 400)})")
    return "; ".join(parts) if parts else "(none: the run ended)"
