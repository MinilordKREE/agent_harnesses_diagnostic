"""Unified-diff parsing and application on a directory tree, and tree-to-tree diffs.

No reference source: written fresh for ahd (see docs/reuse/M2.md). Exact-context application
only (no fuzz, no offset search): a proposer's patch either applies cleanly or is rejected with
the file and hunk named. Supports create (``--- /dev/null``) and delete (``+++ /dev/null``).
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Literal

from ahd.core.config import StrictModel
from ahd.errors import AhdError

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_TREE_IGNORE = {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


class PatchApplyError(AhdError):
    """The diff does not apply to the tree (context mismatch, missing file, unsafe path)."""


class Hunk(StrictModel):
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: tuple[str, ...]
    """Body lines with their one-character prefix (' ', '-', '+'); no trailing newline."""


class FileDiff(StrictModel):
    old_path: str | None
    new_path: str | None
    hunks: tuple[Hunk, ...]

    @property
    def status(self) -> Literal["created", "deleted", "modified"]:
        if self.old_path is None:
            return "created"
        if self.new_path is None:
            return "deleted"
        return "modified"

    @property
    def path(self) -> str:
        path = self.new_path if self.new_path is not None else self.old_path
        if path is None:  # pragma: no cover - a diff always names one side
            raise PatchApplyError("file diff names neither side")
        return path


def _strip_prefix(raw: str) -> str | None:
    token = raw.split("\t", 1)[0].strip()
    if token == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if token.startswith(prefix):
            return token[len(prefix) :]
    return token


def parse_unified_diff(text: str) -> tuple[FileDiff, ...]:
    """Parse ``git diff``/``difflib`` style unified diffs into file diffs and hunks."""
    files: list[FileDiff] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise PatchApplyError(f"malformed diff header at line {i + 1}")
        old_path = _strip_prefix(line[4:])
        new_path = _strip_prefix(lines[i + 1][4:])
        i += 2
        hunks: list[Hunk] = []
        while i < len(lines) and (m := _HUNK_RE.match(lines[i])):
            old_start, old_len = int(m.group(1)), int(m.group(2) or "1")
            new_start, new_len = int(m.group(3)), int(m.group(4) or "1")
            i += 1
            body: list[str] = []
            seen_old = seen_new = 0
            while i < len(lines) and (seen_old < old_len or seen_new < new_len):
                item = lines[i]
                if item.startswith("\\"):
                    i += 1
                    continue
                prefix = item[:1] if item else " "
                if prefix not in (" ", "-", "+"):
                    raise PatchApplyError(f"unexpected diff line {i + 1}: {item[:60]!r}")
                body.append(item if item else " ")
                if prefix in (" ", "-"):
                    seen_old += 1
                if prefix in (" ", "+"):
                    seen_new += 1
                i += 1
            while i < len(lines) and lines[i].startswith("\\"):
                i += 1
            if seen_old != old_len or seen_new != new_len:
                raise PatchApplyError(
                    f"hunk @@ -{old_start},{old_len} +{new_start},{new_len} @@ is truncated"
                )
            hunks.append(
                Hunk(
                    old_start=old_start,
                    old_lines=old_len,
                    new_start=new_start,
                    new_lines=new_len,
                    lines=tuple(body),
                )
            )
        files.append(FileDiff(old_path=old_path, new_path=new_path, hunks=tuple(hunks)))
    return tuple(files)


def _safe_path(tree: Path, relative: str) -> Path:
    candidate = (tree / relative).resolve()
    if not candidate.is_relative_to(tree.resolve()):
        raise PatchApplyError(f"path escapes the tree: {relative}")
    return candidate


def _apply_hunks(original: list[str], hunks: tuple[Hunk, ...], *, path: str) -> list[str]:
    result: list[str] = []
    cursor = 0  # index into original
    for hunk in hunks:
        start = hunk.old_start - 1 if hunk.old_lines > 0 else hunk.old_start
        if start < cursor or start > len(original):
            raise PatchApplyError(f"{path}: hunk at old line {hunk.old_start} is out of order")
        result.extend(original[cursor:start])
        cursor = start
        for item in hunk.lines:
            prefix, body = item[:1], item[1:]
            if prefix in (" ", "-"):
                if cursor >= len(original) or original[cursor] != body:
                    found = original[cursor] if cursor < len(original) else "<eof>"
                    raise PatchApplyError(
                        f"{path}: context mismatch at line {cursor + 1}: "
                        f"expected {body!r}, found {found!r}"
                    )
                if prefix == " ":
                    result.append(body)
                cursor += 1
            else:
                result.append(body)
    result.extend(original[cursor:])
    return result


def apply_unified_diff(tree: Path, text: str) -> tuple[str, ...]:
    """Apply ``text`` to ``tree`` in place; return the relative paths touched."""
    touched: list[str] = []
    for file_diff in parse_unified_diff(text):
        relative = file_diff.path
        target = _safe_path(tree, relative)
        if file_diff.status == "created":
            if target.exists():
                raise PatchApplyError(f"{relative}: cannot create, already exists")
            body = [item[1:] for hunk in file_diff.hunks for item in hunk.lines if item[:1] == "+"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(body) + ("\n" if body else ""), encoding="utf-8")
        elif file_diff.status == "deleted":
            if not target.is_file():
                raise PatchApplyError(f"{relative}: cannot delete, missing")
            original = target.read_text(encoding="utf-8").splitlines()
            remaining = _apply_hunks(original, file_diff.hunks, path=relative)
            if remaining:
                raise PatchApplyError(f"{relative}: delete hunk does not cover the whole file")
            target.unlink()
        else:
            if not target.is_file():
                raise PatchApplyError(f"{relative}: missing")
            original = target.read_text(encoding="utf-8").splitlines()
            updated = _apply_hunks(original, file_diff.hunks, path=relative)
            target.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
        touched.append(relative)
    return tuple(touched)


def tree_files(root: Path) -> tuple[str, ...]:
    """Relative POSIX paths of the files in a tree, sorted, ignoring caches and VCS dirs."""
    return tuple(
        sorted(
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and not any(part in _TREE_IGNORE for part in p.parts)
        )
    )


def unified_diff_trees(old: Path, new: Path) -> str:
    """``a/`` ``b/`` prefixed unified diff of every text file that differs between two trees."""
    old_files = set(tree_files(old))
    new_files = set(tree_files(new))
    chunks: list[str] = []
    for relative in sorted(old_files | new_files):
        old_text = (old / relative).read_text(encoding="utf-8") if relative in old_files else None
        new_text = (new / relative).read_text(encoding="utf-8") if relative in new_files else None
        if old_text == new_text:
            continue
        diff = difflib.unified_diff(
            old_text.splitlines() if old_text is not None else [],
            new_text.splitlines() if new_text is not None else [],
            fromfile="/dev/null" if old_text is None else f"a/{relative}",
            tofile="/dev/null" if new_text is None else f"b/{relative}",
            lineterm="",
        )
        chunks.append("\n".join(diff) + "\n")
    return "".join(chunks)
