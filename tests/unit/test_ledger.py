from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahd.errors import BudgetExhausted, InfraError, TaskFailure
from ahd.llm.ledger import LEDGER_SCHEMA_VERSION, Ledger, read_ledger, summarize
from ahd.llm.types import Attribution, ChatMessage, ChatRequest

REQUIRED = {
    "schema_version",
    "ts",
    "run_id",
    "event",
    "arm",
    "unit_id",
    "seed",
    "model",
    "prompt_tokens",
    "completion_tokens",
    "cached",
    "latency_ms",
    "usd",
    "pricing_version",
}


def _request() -> ChatRequest:
    return ChatRequest(
        model="m",
        messages=(ChatMessage(role="user", content="x"),),
        seed=5,
        attribution=Attribution(arm="arm1", unit_id="u"),
    )


def test_rows_are_well_formed_and_counted_separately(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl", "r1")
    request = _request()
    ledger.record_infra_retry(
        request, attempt=1, kind="rate_limit", status_code=429, error="e", request_sha256="s"
    )
    ledger.record_infra_failure(
        request,
        InfraError("gave up", kind="server_error", status_code=500, attempts=3),
        request_sha256="s",
    )
    ledger.record_task_failure(
        request, BudgetExhausted("out of tokens", budget=10, spent=11, unit="tokens")
    )
    ledger.record_task_failure(request, TaskFailure("wrong answer", kind="verifier_failed"))
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for line in lines:
        row = json.loads(line)
        assert set(row) >= REQUIRED
        assert row["schema_version"] == LEDGER_SCHEMA_VERSION
        assert (row["run_id"], row["arm"], row["unit_id"], row["seed"]) == ("r1", "arm1", "u", 5)
    rows = read_ledger(ledger.path)
    assert [r.event for r in rows] == [
        "infra_retry",
        "infra_failure",
        "task_failure",
        "task_failure",
    ]
    assert rows[2].error_kind == "budget_exhausted"
    assert rows[3].error_kind == "verifier_failed"
    summary = summarize(rows)
    assert (summary.infra_retries, summary.infra_failures) == (1, 1)
    assert summary.task_failures == 1  # verifier_failed only
    assert summary.budget_exhausted == 1  # counted on its own, not inside task_failures
    assert summary.calls == 0


def test_corrupt_row_is_infra_error(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    with pytest.raises(InfraError) as info:
        read_ledger(path)
    assert info.value.kind == "corrupt_file"


def test_search_rows_are_priced_and_counted_apart(tmp_path: Path) -> None:
    from ahd.llm.pricing import SearchCost

    ledger = Ledger(tmp_path / "ledger.jsonl", "r2")
    cost = SearchCost(usd=0.001, provider="serper", pricing_version="s.1")
    ledger.record_search(
        arm="A", unit_id="u", seed=0, cost=cost, query_sha256="q" * 64, latency_ms=40
    )
    ledger.record_search(arm="A", unit_id="u", seed=0, cost=cost, query_sha256="r" * 64)
    rows = read_ledger(ledger.path)
    assert [r.event for r in rows] == ["search", "search"]
    assert rows[0].search_provider == "serper" and rows[0].model == "serper"
    assert rows[0].pricing_version == "s.1" and rows[0].usd == 0.001
    assert rows[0].schema_version == LEDGER_SCHEMA_VERSION == 2
    summary = summarize(rows)
    assert (summary.search_calls, summary.calls) == (2, 0)
    assert summary.search_usd == pytest.approx(0.002)
    assert summary.usd == 0.0  # LLM spend stays separate from search spend
