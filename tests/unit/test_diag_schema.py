"""Diagnosis schema: identifier stripping, rendering and length matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from ahd.diagnosis.schema import (
    RenderBudget,
    _fit,
    identifier_tokens,
    load_template,
    render,
    strip_identifiers,
)
from ahd.harness.components import ComponentManifest
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import diagnosis


@pytest.fixture(scope="module")
def tokens() -> dict[str, str]:
    manifest = ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")
    return identifier_tokens(manifest, tool_names=("todo_list_tasks", "todo_update_task"))


def test_worked_examples_from_the_audit(tokens: dict[str, str]) -> None:
    one = (
        "The `AppendOnlyContext` in `agent/components.py` hands the model every truncated "
        "observation, so by step 12 the `run_shell_command` output that mattered was cut"
    )
    out, counts = strip_identifiers(one, tokens)
    assert out == (
        "The [routine] in [path] hands the model every truncated observation, so by step 12 "
        "the [tool] output that mattered was cut"
    )
    assert counts == {"routine": 1, "path": 1, "tool": 1}
    two = (
        "`SeedCompletionPolicy.no_actions` treats any content-only reply as final after a "
        "`todo_list_tasks` call"
    )
    out2, _ = strip_identifiers(two, tokens)
    assert "[routine]" in out2 and "[tool] call" in out2 and "SeedCompletionPolicy" not in out2
    three = "Raise `max_steps` handling in the lifecycle layer: `run_policy_loop` should re-prompt"
    out3, counts3 = strip_identifiers(three, tokens)
    assert out3 == "Raise [setting] handling in the [layer] layer: [routine] should re-prompt"
    assert counts3["layer"] == 1


def test_component_ids_and_paths_are_stripped_only_on_boundaries(tokens: dict[str, str]) -> None:
    out, _ = strip_identifiers(
        "context_window truncates; my_context_window_x stays; loop.py vs agent/loop.py", tokens
    )
    assert out.startswith("[component] truncates; my_context_window_x stays;")
    assert "[path]" in out


def test_fit_trims_and_pads() -> None:
    long = "word " * 200
    text, truncated = _fit(long, 100, "filler.")
    assert truncated and len(text) <= 100 and text.endswith(".")
    short, truncated2 = _fit("tiny.", 100, "No further detail.")
    assert not truncated2 and len(short) >= 90 and short.startswith("tiny.")


def test_render_matches_lengths_across_arms(tokens: dict[str, str]) -> None:
    template = load_template(
        str(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "diagnosis_template.md")
    )
    budget = RenderBudget(mechanism=200, fix_hint=120)
    a = render(
        diagnosis(mechanism="The system_prompt tells the policy to stop early " * 8),
        template,
        tokens=tokens,
        budget=budget,
    )
    b = render(diagnosis(mechanism="short.", fix="tiny."), template, tokens=tokens, budget=budget)
    for r in (a, b):
        assert r.field_lengths["mechanism"] <= 200 and r.field_lengths["fix_hint"] <= 120
        assert r.field_lengths["mechanism"] >= 180 and r.field_lengths["fix_hint"] >= 108
    assert a.truncated["mechanism"] and not b.truncated["mechanism"]
    assert a.placeholder_counts.get("component", 0) >= 1 or a.truncated["mechanism"]
    c = render(
        diagnosis(mechanism="The system_prompt stops early."),
        template,
        tokens=tokens,
        budget=budget,
    )
    assert c.placeholder_counts == {"component": 1}
    assert "system_prompt" in a.text.split("WHY")[0]  # WHERE line keeps the id
    assert "[component]" in a.text.split("WHY")[1]


def test_template_validation(tmp_path: Path) -> None:
    bad = tmp_path / "t.md"
    bad.write_text("{component} only", encoding="utf-8")
    with pytest.raises(ValueError, match="lacks"):
        load_template(str(bad))
