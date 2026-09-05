from __future__ import annotations

from pathlib import Path

import pytest

from ahd.harness.patch import (
    PatchApplyError,
    apply_unified_diff,
    parse_unified_diff,
    tree_files,
    unified_diff_trees,
)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_round_trip_modify_create_delete(tmp_path: Path) -> None:
    old = _tree(
        tmp_path / "old", {"a.py": "x = 1\ny = 2\n", "gone.txt": "bye\n", "sub/k.md": "k\n"}
    )
    new = _tree(
        tmp_path / "new", {"a.py": "x = 1\ny = 3\nz = 4\n", "sub/k.md": "k\n", "born.txt": "hi\n"}
    )
    diff = unified_diff_trees(old, new)
    files = parse_unified_diff(diff)
    assert [(f.path, f.status) for f in files] == [
        ("a.py", "modified"),
        ("born.txt", "created"),
        ("gone.txt", "deleted"),
    ]
    touched = apply_unified_diff(old, diff)
    assert set(touched) == {"a.py", "born.txt", "gone.txt"}
    assert tree_files(old) == tree_files(new)
    for name in tree_files(new):
        assert (old / name).read_text() == (new / name).read_text()
    assert unified_diff_trees(old, new) == ""


def test_context_mismatch_and_unsafe_paths(tmp_path: Path) -> None:
    old = _tree(tmp_path / "old", {"a.py": "x = 1\ny = 2\n"})
    bad = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n x = 1\n-y = 9\n+y = 3\n"
    with pytest.raises(PatchApplyError, match="context mismatch"):
        apply_unified_diff(old, bad)
    assert (old / "a.py").read_text() == "x = 1\ny = 2\n"
    escape = "--- /dev/null\n+++ b/../evil.py\n@@ -0,0 +1 @@\n+boom\n"
    with pytest.raises(PatchApplyError, match="escapes"):
        apply_unified_diff(old, escape)
    with pytest.raises(PatchApplyError, match="missing"):
        apply_unified_diff(old, "--- a/nope.py\n+++ b/nope.py\n@@ -1 +1 @@\n-a\n+b\n")
    with pytest.raises(PatchApplyError, match="truncated"):
        parse_unified_diff("--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n x = 1\n")
