"""The run_shell_command tool: its function schema + execution.

A tool module owns both halves of one tool — the schema the model sees and the
code that runs when the model calls it — so adding/removing a tool is a single
self-contained file. Imports its helpers with a package-relative import
(``from ._util import ...``) so the package can be renamed or moved without
touching this file.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ._util import decode, truncate


RUN_SHELL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_shell_command",
        "description": (
            "Run a bash command inside the task workspace. Use it to inspect, "
            "edit, test, and create files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Exact bash command to execute."},
                "description": {"type": "string", "description": "Short reason for running this command."},
                "timeout_seconds": {"type": "integer", "description": "Optional timeout in seconds."},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
}


def run_shell_command(*, command: str, workspace: Path, timeout_seconds: int) -> dict[str, Any]:
    """Run a shell command in the task workspace (plain subprocess, no unshare).

    Inside a E2B sandbox the container boundary provides isolation; nested
    unshare is unavailable (max_user_namespaces=0).
    """
    if not command.strip():
        return {"content": json.dumps({"error": "empty command"}, ensure_ascii=False)}
    started = time.time()
    # NOTE: capture raw bytes (no text=True) and decode ourselves with
    # errors="replace". Task commands routinely fetch non-UTF-8 content
    # (e.g. GBK Chinese web pages); text=True decodes strictly inside
    # subprocess.communicate() and would raise UnicodeDecodeError before we
    # ever see the output, failing the whole rollout. TimeoutExpired also
    # carries raw bytes regardless of text=, which json.dumps cannot
    # serialize -- decoding here covers both paths.
    proc = subprocess.Popen(
        command,
        cwd=workspace,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
        result = {
            "stdout": truncate(decode(stdout)),
            "stderr": truncate(decode(stderr)),
            "exit_code": proc.returncode,
            "duration_seconds": time.time() - started,
        }
    except subprocess.TimeoutExpired:
        killed_process_group = False
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            killed_process_group = True
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        result = {
            "stdout": truncate(decode(stdout)),
            "stderr": truncate(decode(stderr)),
            "exit_code": None,
            "timeout": True,
            "killed_process_group": killed_process_group,
            "duration_seconds": time.time() - started,
        }
    return {"content": json.dumps(result, ensure_ascii=False), **result}
