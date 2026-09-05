"""JSONL trace writer with a versioned envelope.

Adapted from: microsoft/AutoSaddler @ 30e20ce004486c58e7ee97c66182a8d0d41ec90e
Original path: src/autosaddler/v2/core/events.py (``RunEvent`` envelope and
``EVENT_SCHEMA_VERSION``; lines 11, 54-62) and src/autosaddler/v2/storage/local.py
(sequence validation on read; lines 129-149)
License: MIT, Copyright (c) 2026 (AutoSaddler authors) -- see THIRD_PARTY_NOTICES.md

Changes: envelope reduced to ``{schema_version, seq, ts, run_id, kind, payload}`` (no
``operation_id``); pydantic instead of dataclasses; writer keeps an open handle with a lock
and can resume an existing file; reader raises :class:`InfraError` on schema, sequence or
run-id mismatches.

Schema bump rule (docs/CONVENTIONS.md): any field added to or removed from the envelope
bumps ``TRACE_SCHEMA_VERSION``; payload contents are free-form per ``kind``.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.core.io import read_jsonl
from ahd.errors import InfraError

TRACE_SCHEMA_VERSION = 1
TRACE_FILENAME = "trace.jsonl"


class TraceEvent(StrictModel):
    schema_version: int
    seq: int
    ts: datetime
    run_id: str
    kind: str
    payload: dict[str, JsonValue]


class TraceWriter:
    """Append-only, thread-safe JSONL writer. ``seq`` is contiguous from 1 within a file."""

    def __init__(self, path: Path, run_id: str, *, fsync: bool = False) -> None:
        self.path = path
        self.run_id = run_id
        self._fsync = fsync
        self._lock = threading.Lock()
        self._seq = self._last_seq(path) if path.exists() else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("a", encoding="utf-8")

    @staticmethod
    def _last_seq(path: Path) -> int:
        events = read_trace(path)
        return events[-1].seq if events else 0

    @property
    def seq(self) -> int:
        return self._seq

    def write(self, kind: str, payload: Mapping[str, object]) -> TraceEvent:
        body = to_json_value(dict(payload))
        if not isinstance(body, dict):
            raise TypeError("trace payload must convert to a JSON object")
        with self._lock:
            self._seq += 1
            event = TraceEvent(
                schema_version=TRACE_SCHEMA_VERSION,
                seq=self._seq,
                ts=datetime.now(UTC),
                run_id=self.run_id,
                kind=kind,
                payload=body,
            )
            self._handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n")
            self._handle.flush()
            if self._fsync:
                os.fsync(self._handle.fileno())
        return event

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def read_trace(path: Path, *, expected_run_id: str | None = None) -> list[TraceEvent]:
    """Parse a trace file, validating schema version, contiguous ``seq`` and a single run id."""
    events: list[TraceEvent] = []
    run_id = expected_run_id
    for index, raw in enumerate(read_jsonl(path), start=1):
        try:
            event = TraceEvent.model_validate(raw)
        except ValidationError as exc:
            raise InfraError(
                f"invalid trace event at line {index} in {path}:\n{exc}", kind="corrupt_file"
            ) from exc
        if event.schema_version != TRACE_SCHEMA_VERSION:
            raise InfraError(
                f"trace {path} has schema_version {event.schema_version}, "
                f"expected {TRACE_SCHEMA_VERSION}",
                kind="trace_schema",
            )
        if event.seq != index:
            raise InfraError(
                f"trace {path}: expected seq {index} at line {index}, found {event.seq}",
                kind="corrupt_file",
            )
        if run_id is None:
            run_id = event.run_id
        elif event.run_id != run_id:
            raise InfraError(
                f"trace {path}: run_id changed from {run_id} to {event.run_id} at seq {event.seq}",
                kind="corrupt_file",
            )
        events.append(event)
    return events
