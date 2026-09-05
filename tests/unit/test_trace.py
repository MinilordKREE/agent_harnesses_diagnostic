from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahd.core.trace import TRACE_SCHEMA_VERSION, TraceWriter, read_trace
from ahd.errors import InfraError

ENVELOPE = {"schema_version", "seq", "ts", "run_id", "kind", "payload"}


def test_envelope_and_sequence(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path, "run-1") as writer:
        writer.write("a", {"x": 1})
        writer.write("b", {"y": [1, 2]})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert set(json.loads(lines[0])) == ENVELOPE
    events = read_trace(path, expected_run_id="run-1")
    assert [e.seq for e in events] == [1, 2]
    assert [e.kind for e in events] == ["a", "b"]
    assert all(e.schema_version == TRACE_SCHEMA_VERSION for e in events)
    assert events[1].payload == {"y": [1, 2]}


def test_resume_continues_sequence(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path, "run-1") as writer:
        writer.write("a", {})
    with TraceWriter(path, "run-1") as writer:
        assert writer.seq == 1
        writer.write("b", {})
    assert [e.seq for e in read_trace(path)] == [1, 2]


def test_reader_detects_gap_schema_and_run_id(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with TraceWriter(path, "run-1") as writer:
        writer.write("a", {})
        writer.write("b", {})
    lines = path.read_text(encoding="utf-8").splitlines()
    gap = json.loads(lines[1])
    gap["seq"] = 5
    path.write_text(lines[0] + "\n" + json.dumps(gap) + "\n", encoding="utf-8")
    with pytest.raises(InfraError, match="expected seq 2"):
        read_trace(path)

    other = json.loads(lines[1])
    other["run_id"] = "run-2"
    path.write_text(lines[0] + "\n" + json.dumps(other) + "\n", encoding="utf-8")
    with pytest.raises(InfraError, match="run_id changed"):
        read_trace(path)

    old = json.loads(lines[0])
    old["schema_version"] = 99
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    with pytest.raises(InfraError) as info:
        read_trace(path)
    assert info.value.kind == "trace_schema"


def test_payload_must_be_json(tmp_path: Path) -> None:
    with TraceWriter(tmp_path / "t.jsonl", "r") as writer, pytest.raises(TypeError):
        writer.write("bad", {"obj": object()})
