"""Canonical JSON and SHA-256 helpers.

Adapted from: microsoft/AutoSaddler @ 30e20ce004486c58e7ee97c66182a8d0d41ec90e
Original path: src/autosaddler/v2/core/domain.py (``to_json_value``, ``canonical_json``,
``sha256_digest``; lines 19-47)
License: MIT, Copyright (c) 2026 (AutoSaddler authors) -- see THIRD_PARTY_NOTICES.md

Also adapted from: RUCAIBox/Evo-Bench @ e1dc9386a193cab1ee8630824c085e5e26d0c730
Original path: evobench/evaluation/runner.py (``hash_harness``; lines 562-574)
License: Apache-2.0, Copyright 2026 Evo-Bench Authors -- see THIRD_PARTY_NOTICES.md

Changes: ``to_json_value`` additionally handles pydantic models, ``datetime``/``date`` and
``Enum``; ``sha256_digest`` returns bare hex (upstream prefixes ``sha256:``); ``sha256_dir``
returns the full digest (upstream truncates to 16 hex chars) and takes the ignore set as a
parameter.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

DEFAULT_IGNORED_PARTS: frozenset[str] = frozenset(
    {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", ".venv"}
)


def to_json_value(value: object) -> JsonValue:
    """Convert ``value`` to JSON-compatible data without lossy ``repr`` fallbacks."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON records cannot contain non-finite floats")
        return value
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return to_json_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        converted = [to_json_value(item) for item in value]
        return sorted(
            converted, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True)
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [to_json_value(item) for item in value]
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Deterministic JSON: sorted keys, compact separators, ASCII only."""
    return json.dumps(
        to_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def sha256_digest(data: bytes | str) -> str:
    payload = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


def sha256_of(value: object) -> str:
    """SHA-256 of the canonical JSON form of ``value``."""
    return sha256_digest(canonical_json(value))


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_dir(root: Path, *, ignored_parts: frozenset[str] = DEFAULT_IGNORED_PARTS) -> str:
    """Content hash of a directory tree: sorted relative paths and bytes, NUL-separated."""
    digest = hashlib.sha256()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not any(part in ignored_parts for part in p.parts)
    )
    for item in files:
        digest.update(item.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
