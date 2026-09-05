from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from ahd.core.hashing import (
    canonical_json,
    sha256_dir,
    sha256_file,
    sha256_of,
    to_json_value,
)


class _Model(BaseModel):
    b: int
    a: str


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 1, "a": {"y": 2, "x": 1}}) == canonical_json(
        {"a": {"x": 1, "y": 2}, "b": 1}
    )
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_to_json_value_handles_project_types() -> None:
    value = to_json_value(
        {
            "path": Path("x/y"),
            "when": datetime(2026, 9, 4, tzinfo=UTC),
            "model": _Model(b=1, a="z"),
            "set": {3, 1, 2},
            "tuple": (1, "two"),
        }
    )
    assert value == {
        "path": "x/y",
        "when": "2026-09-04T00:00:00+00:00",
        "model": {"a": "z", "b": 1},
        "set": [1, 2, 3],
        "tuple": [1, "two"],
    }


def test_to_json_value_rejects_non_finite_and_unknown() -> None:
    with pytest.raises(ValueError):
        to_json_value(float("nan"))
    with pytest.raises(TypeError):
        to_json_value(object())


def test_sha256_of_matches_reordered_input() -> None:
    assert sha256_of({"a": 1, "b": [1, 2]}) == sha256_of({"b": [1, 2], "a": 1})
    assert sha256_of({"a": 1}) != sha256_of({"a": 2})
    assert len(sha256_of({})) == 64


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello" * 1000)
    assert sha256_file(path) == hashlib.sha256(b"hello" * 1000).hexdigest()


def test_sha256_dir_ignores_caches_and_tracks_content(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("2", encoding="utf-8")
    baseline = sha256_dir(tmp_path)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.pyc").write_bytes(b"x")
    assert sha256_dir(tmp_path) == baseline
    (tmp_path / "sub" / "b.txt").write_text("3", encoding="utf-8")
    assert sha256_dir(tmp_path) != baseline
