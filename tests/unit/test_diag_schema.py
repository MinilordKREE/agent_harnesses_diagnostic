"""Diagnosis schema: identifier stripping, rendering and length matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from ahd.diagnosis.schema import (
    FieldCaps,
    _fit,
    caps_for,
    identifier_tokens,
    load_causes,
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


def test_fit_trims_and_never_pads() -> None:
    long = "word " * 200
    text, truncated = _fit(long, 100)
    assert truncated and len(text) <= 100 and text.endswith(".")
    short, truncated2 = _fit("tiny.", 100)
    assert not truncated2 and short == "tiny."


def test_render_caps_come_from_the_longest_arm(tokens: dict[str, str]) -> None:
    template = load_template(
        str(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "diagnosis_template.md")
    )
    long = diagnosis(mechanism="The system_prompt tells the policy to stop early " * 8)
    short = diagnosis(mechanism="short.", fix="tiny.")
    caps = caps_for([long, short], tokens)
    assert caps.mechanism == len(("The [component] tells the policy to stop early " * 8).strip())
    a = render(long, template, tokens=tokens, caps=caps)
    b = render(short, template, tokens=tokens, caps=caps)
    assert not a.truncated["mechanism"] and a.field_lengths["mechanism"] == caps.mechanism
    assert b.field_lengths["mechanism"] == len("short.") and "No further detail" not in b.text
    tight = FieldCaps(mechanism=60, fix_hint=40)
    c = render(long, template, tokens=tokens, caps=tight)
    assert c.truncated["mechanism"] and c.field_lengths["mechanism"] <= 60
    assert a.placeholder_counts == {"component": 8}
    assert "system_prompt" in a.text.split("WHY")[0]  # WHERE line keeps the id
    assert "[component]" in a.text.split("WHY")[1]


def test_cause_vocabulary() -> None:
    vocab = load_causes(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "causes.yaml")
    assert 12 <= len(vocab.causes) <= 15 and len(set(vocab.ids())) == len(vocab.ids())
    assert {"premature_termination", "contract_violation", "error_recovery"} <= set(vocab.ids())
    assert vocab.normalise("Premature_Termination") == "premature_termination"
    assert vocab.normalise("other: judge misread the rubric") == "other:judge misread the rubric"
    with pytest.raises(ValueError, match="not in the vocabulary"):
        vocab.normalise("made_up_label")
    with pytest.raises(ValueError):
        vocab.normalise("other:")
    listing = vocab.prompt_listing()
    assert listing.count("\n") == len(vocab.causes) and "other:<short text>" in listing


def test_template_validation(tmp_path: Path) -> None:
    bad = tmp_path / "t.md"
    bad.write_text("{component} only", encoding="utf-8")
    with pytest.raises(ValueError, match="lacks"):
        load_template(str(bad))
