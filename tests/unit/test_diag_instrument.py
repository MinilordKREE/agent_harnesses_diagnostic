"""The replay instrument's drift comparison: masks and the comparable part of a result."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ahd.diagnosis.pipeline import INSTRUMENT_DIR
from ahd.harness.validate import validate_tree

pytestmark = pytest.mark.skipif(
    not (INSTRUMENT_DIR / "harness.py").is_file(), reason="instrument missing"
)


@pytest.fixture(scope="module")
def loop() -> Any:
    pytest.importorskip("evobench")
    saved = {
        k: v
        for k, v in sys.modules.items()
        if k == "agent" or k.startswith("agent.") or k == "tools" or k.startswith("tools.")
    }
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(INSTRUMENT_DIR))
    try:
        module = importlib.import_module("agent.loop")
    finally:
        sys.path.remove(str(INSTRUMENT_DIR))
    yield module
    for k in [
        k
        for k in sys.modules
        if k == "agent" or k.startswith("agent.") or k == "tools" or k.startswith("tools.")
    ]:
        del sys.modules[k]
    sys.modules.update(saved)


def test_instrument_tree_is_valid_and_headed() -> None:
    assert validate_tree(INSTRUMENT_DIR).ok
    head = (INSTRUMENT_DIR / "agent" / "loop.py").read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("# Adapted from: RUCAIBox/Evo-Bench @ e1dc9386")
    seed = Path("third_party/evo-bench/policy_harness_seed")
    if (seed / "harness.py").is_file():
        for rel in (
            "agent/state.py",
            "agent/actions.py",
            "agent/components.py",
            "tools/shell.py",
            "system_prompt.md",
            "harness.json",
        ):
            assert (INSTRUMENT_DIR / rel).read_bytes() == (seed / rel).read_bytes(), rel


def test_comparable_ignores_duration_and_masks_volatile_text(loop: Any) -> None:
    shell_a: dict[str, Any] = {
        "stdout": "total 12\n-rw-r--r-- 1 u u 0 Sep  5 04:29 a.txt\n",
        "stderr": "",
        "exit_code": 0,
        "duration_seconds": 0.0123,
    }
    shell_a["content"] = json.dumps(shell_a)
    shell_b = dict(shell_a, duration_seconds=0.9)
    shell_b["stdout"] = shell_a["stdout"].replace("04:29", "11:02")
    shell_b["content"] = json.dumps({k: v for k, v in shell_b.items() if k != "content"})
    recorded = {"content": shell_a["content"], "exit_code": 0, "timeout": None}
    assert loop._mask(loop._comparable(shell_b), []) == loop._mask(loop._comparable(recorded), [])
    injected = {
        "content": json.dumps({"task_id": "todo_009", "created_at": "2026-09-05T10:11:12Z"})
    }
    injected2 = {
        "content": json.dumps({"task_id": "todo_009", "created_at": "2026-09-06T00:00:00Z"})
    }
    assert loop._mask(loop._comparable(injected), []) == loop._mask(loop._comparable(injected2), [])
    changed = {"content": json.dumps({"task_id": "todo_010", "created_at": "2026-09-05T10:11:12Z"})}
    assert loop._mask(loop._comparable(injected), []) != loop._mask(loop._comparable(changed), [])
    ws = "/home/u/runs/x/workspaces/t/r1"
    assert (
        loop._mask(f"cwd is {ws}/inputs and port localhost:9202 pid=4411", [], ws)
        == "cwd is <workspace>/inputs and port <endpoint> pid=<pid>"
    )
    assert (
        loop._mask("see /tmp/tmpab12/x and 1788600547.43", [["secret-\\d+", "<s>"]])
        == "see <tmp> and <epoch>"
    )


def test_tree_hash_tracks_workspace_mutation(loop: Any, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "inputs").mkdir(parents=True)
    (ws / "inputs" / "a.txt").write_text("a", encoding="utf-8")
    before = loop._tree_hash(ws)
    assert before == loop._tree_hash(ws)  # deterministic
    (ws / "outputs").mkdir()
    assert loop._tree_hash(ws) == before  # an empty directory is not a file change
    (ws / "outputs" / "report.md").write_text("x", encoding="utf-8")
    assert loop._tree_hash(ws) != before
    assert loop._tree_hash(tmp_path / "missing") == loop._tree_hash(tmp_path / "missing2")
