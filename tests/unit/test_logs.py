from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from ahd.logs import JsonFormatter, _AhdHandlerMarker, configure_logging


def test_json_formatter_emits_valid_json_with_extras() -> None:
    record = logging.LogRecord("ahd.test", logging.WARNING, "f.py", 1, "hello %s", ("x",), None)
    record.event = "infra_retry"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello x"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "ahd.test"
    assert payload["event"] == "infra_retry"
    assert payload["ts"].endswith("+00:00")


def test_configure_logging_is_idempotent_and_writes_file(tmp_path: Path) -> None:
    stream = io.StringIO()
    log_path = tmp_path / "log.jsonl"
    configure_logging(json_path=log_path, stream=stream)
    logger = configure_logging(json_path=log_path, stream=stream)
    ours = [h for h in logger.handlers if isinstance(h, _AhdHandlerMarker)]
    assert len(ours) == 2  # pytest adds its own capture handlers; count only ours
    logging.getLogger("ahd.unit").info("written", extra={"run_id": "r1"})
    for handler in ours:
        handler.flush()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["run_id"] == "r1"
    assert "written" in stream.getvalue()
