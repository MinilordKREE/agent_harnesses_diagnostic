"""Synthetic Evo-Bench-shaped records for offline tests. No released data is copied."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FAKE_REVISION = "0" * 40

GDPVAL_RUBRIC = json.dumps(
    [
        {"score": 2, "criterion": "Delivers a .docx", "required": None, "rubric_item_id": "r1"},
        {"score": 1, "criterion": "Mentions the budget", "required": None, "rubric_item_id": "r2"},
    ]
)


def validation_records() -> list[dict[str, Any]]:
    return [
        {
            "id": "apex-0000000000000000000000000000ab",
            "domain": "office",
            "prompt": "Is the notice valid?",
            "metadata": {"canary": "apex", "apex_domain": "Law", "has_input_files": True},
            "apex_public": {
                "task_id": "task_ab",
                "world_id": "w1",
                "world_name": "Law World",
                "apps": ["Mail"],
            },
            "scorer": {
                "type": "apex_grader",
                "rubric": [{"verifier_id": "v1", "criteria": "States yes"}],
                "gold_response": "yes",
                "gold_response_type": "text",
            },
        },
        {
            "id": "bc-en-0001",
            "domain": "search",
            "prompt": "Which town? (synthetic)",
            "metadata": {
                "problem_topic": "History",
                "dataset": "browsecomp",
                "canary": "browsecomp",
            },
            "scorer": {"type": "llm_as_judge", "expected": "Springfield"},
        },
        {
            "id": "claw-T000_synthetic",
            "domain": "general",
            "prompt": "Tidy the todo list (synthetic)",
            "metadata": {
                "canary": "claw",
                "category": "productivity",
                "difficulty": "easy",
                "language": "en",
            },
            "claw_public": {
                "task_id": "T000_synthetic",
                "tool_schemas": [],
                "endpoints": {},
                "services": [],
                "mock_today": "2026-01-01",
            },
            "scorer": {"type": "claw_grader", "task_dir": "tasks/T000_synthetic"},
        },
        {
            "id": "gdpval-00000000-0000-0000-0000-000000000001",
            "domain": "office",
            "prompt": "Write a short plan (synthetic)",
            "metadata": {
                "canary": "gdpval",
                "sector": "Health",
                "occupation": "Manager",
                "skipped_references": [],
            },
            "asset_files": {"inputs/notes.txt": "abc123abc123abc123abc123abc123ab/notes.txt"},
            "public_files": ["inputs/notes.txt"],
            "scorer": {"type": "rubric_file_judge", "rubric": GDPVAL_RUBRIC, "pass_threshold": 0.6},
        },
        {
            "id": "hle-0000000000000000000000ff",
            "domain": "search",
            "prompt": "What is 2+8? (synthetic)",
            "metadata": {
                "answer_type": "exactMatch",
                "category": "Math",
                "raw_subject": "Arithmetic",
                "dataset": "hle",
                "canary": "hle",
            },
            "scorer": {"type": "hle_judge", "expected": "10"},
        },
    ]


def evaluation_records() -> list[dict[str, Any]]:
    return [
        {
            "id": "bc-en-9001",
            "domain": "search",
            "prompt": "Which river? (synthetic)",
            "metadata": {"dataset": "browsecomp", "canary": "browsecomp"},
            "scorer": {"type": "llm_as_judge", "expected": "Nile"},
        },
        {
            "id": "gdpval-00000000-0000-0000-0000-000000000002",
            "domain": "office",
            "prompt": "Write a memo (synthetic)",
            "metadata": {"canary": "gdpval"},
            "scorer": {"type": "rubric_file_judge", "rubric": GDPVAL_RUBRIC, "pass_threshold": 0.6},
        },
    ]


def make_fake_snapshot(root: Path) -> Path:
    """Lay out ``suites/`` and ``assets/gdpval/`` the way the HF snapshot does."""
    (root / "suites").mkdir(parents=True)
    asset_dir = root / "assets" / "gdpval" / "abc123abc123abc123abc123abc123ab"
    asset_dir.mkdir(parents=True)
    (asset_dir / "notes.txt").write_text("budget: 12\n", encoding="utf-8")
    for split, records in (
        ("validation", validation_records()),
        ("evaluation", evaluation_records()),
    ):
        suite = {
            "name": f"fake_{split}",
            "description": "synthetic",
            "assets_dir": "../assets/gdpval",
            split: records,
        }
        (root / "suites" / f"evobench_{split}.json").write_text(
            json.dumps(suite, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return root
