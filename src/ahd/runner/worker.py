"""Invoke Evo-Bench's policy worker as a subprocess, exactly as its local adapter does.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The request and output
files, the ``python -m evobench.policy.worker IN OUT`` invocation and the deadline formula
mirror ``evobench/policy/adapter.py`` lines 814-845 and 898-919 (Apache-2.0); the code is not
copied, the protocol is (``worker.py:34-53, 131-146``).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ahd.core.config import StrictModel
from ahd.core.io import atomic_write_text

INPUT_FILENAME = "_policy_worker_input.json"
OUTPUT_FILENAME = "_policy_worker_output.json"
_TAIL = 12_000


class WorkerOutcome(StrictModel):
    ok: bool
    rollout: dict[str, Any] | None
    error: str | None
    error_type: str | None
    returncode: int
    timed_out: bool
    elapsed_seconds: float
    stdout_tail: str
    stderr_tail: str


def build_request(
    *,
    harness_dir: Path,
    task: Mapping[str, Any],
    task_workspace: Path,
    output_dir: Path,
    harness_revision: str,
    model_config_id: str,
    model_config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "harness_dir": str(harness_dir),
        "task": dict(task),
        "task_workspace": str(task_workspace),
        "output_dir": str(output_dir),
        "harness_revision": harness_revision,
        "model_config_id": model_config_id,
        "model_config": dict(model_config),
    }


def invoke_worker(
    *,
    request: Mapping[str, Any],
    rollout_dir: Path,
    env: Mapping[str, str],
    timeout_s: int,
    python: str | None = None,
) -> WorkerOutcome:
    rollout_dir.mkdir(parents=True, exist_ok=True)
    input_path = rollout_dir / INPUT_FILENAME
    output_path = rollout_dir / OUTPUT_FILENAME
    atomic_write_text(
        input_path, json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            [
                python or sys.executable,
                "-m",
                "evobench.policy.worker",
                str(input_path),
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=dict(env),
            check=False,
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout = (
            exc.stdout.decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", "replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "policy worker timed out")
        )
    elapsed = time.time() - started
    rollout: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    ok = False
    if output_path.is_file():
        try:
            data = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            error, error_type = f"malformed worker output: {exc}", "policy_worker_malformed_output"
        else:
            if isinstance(data, dict) and data.get("ok") and isinstance(data.get("rollout"), dict):
                ok, rollout = True, data["rollout"]
            else:
                error = (
                    str(data.get("error", "policy_worker_failed"))
                    if isinstance(data, dict)
                    else "policy_worker_failed"
                )
                error_type = (
                    str(data.get("error_type", "policy_worker_failed"))
                    if isinstance(data, dict)
                    else "policy_worker_failed"
                )
    else:
        error = "policy worker timed out" if timed_out else "policy worker produced no output"
        error_type = "policy_worker_timeout" if timed_out else "policy_worker_missing_output"
    return WorkerOutcome(
        ok=ok,
        rollout=rollout,
        error=error,
        error_type=error_type,
        returncode=returncode,
        timed_out=timed_out,
        elapsed_seconds=elapsed,
        stdout_tail=(stdout or "")[-_TAIL:],
        stderr_tail=(stderr or "")[-_TAIL:],
    )
