"""Approximate Serper call counting from shell commands in a trajectory.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The seed policy has no
search tool; it can only reach Serper by running ``curl``/``python`` against
``google.serper.dev`` inside ``run_shell_command``. Until M2b's record/replay proxy observes
the wire, a command mentioning the Serper host counts as one call, labelled ``approximate``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_digest
from ahd.runner.trajectory import TrajectoryEvent

SERPER_HOST_RE = re.compile(r"google\.serper\.dev|serper\.dev/search", re.IGNORECASE)


class SerperCall(StrictModel):
    step: int
    command_sha256: str


def count_serper_calls(events: Iterable[TrajectoryEvent]) -> tuple[SerperCall, ...]:
    calls: list[SerperCall] = []
    for event in events:
        if event.kind != "tool_call" or event.payload.get("name") != "run_shell_command":
            continue
        arguments = event.payload.get("arguments")
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if isinstance(command, str) and SERPER_HOST_RE.search(command):
            step = event.payload.get("step")
            calls.append(
                SerperCall(
                    step=int(step) if isinstance(step, int) else 0,
                    command_sha256=sha256_digest(command),
                )
            )
    return tuple(calls)
