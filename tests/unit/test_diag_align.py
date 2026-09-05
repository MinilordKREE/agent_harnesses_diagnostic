"""Alignment: every divergence type on synthetic failed/reference pairs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ahd.diagnosis.align import (
    Alignment,
    actions_from_trajectory,
    align,
    classify_shell,
    normalise_command,
    shell_targets,
)
from tests.diag_fixtures import finish, sh, trajectory

LIST: tuple[str, dict[str, Any]] = ("todo_list_tasks", {})
UPDATE: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "status": "completed"},
)
UPDATE_VARIANT: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "priority": "high"},
)
UPDATE_OTHER: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_002", "status": "completed"},
)


def _align(
    failed: Sequence[Any], reference: Sequence[Any], exit_reason: str = "finished"
) -> Alignment:
    return align(
        trajectory(failed), trajectory(reference), task_id="t", failed_exit_reason=exit_reason
    )


def test_shell_classification_and_targets() -> None:
    assert classify_shell("ls -la && cat a.txt") == "shell_ro"
    assert classify_shell("echo hi > out.txt") == "shell_mut"
    assert classify_shell("cat a 2>&1") == "shell_ro"
    assert classify_shell("sed -i 's/a/b/' f.py") == "shell_mut"
    assert shell_targets("cp a.txt b.txt") == ("b.txt",)
    assert shell_targets("echo x > outputs/report.md; ls") == ("outputs/report.md",)
    assert shell_targets("mkdir -p outputs && touch outputs/a outputs/b") == (
        "outputs",
        "outputs/a",
        "outputs/b",
    )
    assert normalise_command("  ls   -la ;") == "ls -la"


def test_identical_runs_have_no_candidates() -> None:
    steps = [[sh("ls")], [LIST], [finish("done")]]
    a = _align(steps, steps)
    assert a.t_exact is None and a.t_class is None and a.candidates == ()


def test_exact_divergence_is_recorded_but_not_a_candidate() -> None:
    a = _align(
        [[sh("ls")], [sh("cat a")], [finish("x")]], [[sh("pwd")], [sh("cat b")], [finish("x")]]
    )
    assert a.t_exact == 1 and a.t_class is None and a.candidates == ()


def test_premature_finish() -> None:
    a = _align([[sh("ls")], [finish("x")]], [[sh("ls")], [UPDATE], [finish("x")]])
    assert a.t_class == 2 and a.candidates[0].divergence == "premature_finish"
    assert a.failed_steps == 2 and a.reference_steps == 3


def test_no_tool_call_content_only() -> None:
    a = _align(
        [[sh("ls")], "here is my answer"],
        [[sh("ls")], [UPDATE], [finish("x")]],
        "assistant_no_tool_call",
    )
    assert a.candidates[0].divergence == "no_tool_call" and a.t_class == 2


def test_missing_and_extra_mutation() -> None:
    a = _align([[LIST], [LIST], [finish("x")]], [[LIST], [UPDATE], [finish("x")]])
    assert a.candidates[0].divergence == "missing_mutation" and a.candidates[0].step == 2
    b = _align([[LIST], [UPDATE], [finish("x")]], [[LIST], [LIST], [finish("x")]])
    assert b.candidates[0].divergence == "extra_mutation"


def test_early_divergence_at_step_one() -> None:
    a = _align([[finish("x")]], [[LIST], [finish("x")]])
    assert a.t_class == 1 and a.candidates[0].divergence == "premature_finish"
    b = _align([[sh("echo hi > a.txt")], [finish("x")]], [[LIST], [finish("x")]])
    assert b.candidates[0].step == 1 and b.candidates[0].divergence == "extra_mutation"
    c = _align([[LIST], [finish("x")]], [[sh("ls")], [finish("x")]])
    assert c.candidates[0].divergence == "early"


def test_different_action_same_kind() -> None:
    a = _align([[sh("cp a.txt x.txt")], [finish("x")]], [[sh("cp a.txt y.txt")], [finish("x")]])
    assert a.candidates[0].divergence == "early"
    b = _align(
        [[LIST], [sh("cp a.txt x.txt")], [finish("x")]],
        [[LIST], [sh("cp a.txt y.txt")], [finish("x")]],
    )
    assert b.candidates[0].divergence == "different_action" and b.candidates[0].step == 2


def test_late_finish() -> None:
    a = _align([[LIST], [LIST], [LIST], [finish("x")]], [[LIST], [finish("x")]])
    assert a.candidates[0].divergence == "late_finish" and a.candidates[0].step == 2


def test_budget_and_error_endings() -> None:
    a = _align([[LIST], [LIST]], [[LIST], [LIST], [UPDATE], [finish("x")]], "max_steps")
    assert a.candidates[0].divergence == "budget" and a.candidates[0].step == 3
    b = _align([[LIST]], [[LIST], [UPDATE], [finish("x")]], "model_call_error")
    assert b.candidates[0].divergence == "error" and b.candidates[0].step == 2


def test_argument_variant_after_class_candidates() -> None:
    failed = [[LIST], [UPDATE_VARIANT], [LIST], [finish("x")]]
    reference = [[LIST], [UPDATE], [UPDATE_OTHER], [finish("x")]]
    a = _align(failed, reference)
    kinds = [(c.step, c.divergence) for c in a.candidates]
    assert kinds == [(3, "missing_mutation"), (2, "argument_variant")]
    assert a.t_class == 3 and a.t_exact == 2


def test_actions_from_trajectory_identity_keys() -> None:
    steps = actions_from_trajectory(trajectory([[UPDATE, LIST], "final words"]))
    first = steps[0].actions
    assert [a.klass for a in first] == ["tool", "tool"]
    assert first[0].identity == 'todo_update_task:{"task_id": "todo_001"}'
    assert steps[1].actions[0].klass == "final"


def test_opaque_shell_class() -> None:
    assert classify_shell("python3 build.py --out outputs/report.xlsx") == "shell_opaque"
    assert classify_shell("python3 -m openpyxl_tool") == "shell_opaque"
    assert classify_shell("bash run.sh") == "shell_opaque"
    assert classify_shell("./scripts/make.sh") == "shell_opaque"
    assert (
        classify_shell("cat data.csv | python3 -c 'import sys; print(sys.stdin.read())'")
        == "shell_opaque"
    )
    assert classify_shell("soffice --headless --convert-to pdf report.docx") == "shell_opaque"
    assert classify_shell("python3 x.py > out.txt") == "shell_mut"  # explicit redirection wins
    assert classify_shell("ls -la && grep -n foo notes.txt") == "shell_ro"
    assert classify_shell("shred file") == "shell_ro"  # `sh` must match as a word, not a prefix


def test_opaque_alignment_compares_normalised_commands() -> None:
    same = [[LIST], [sh("python3 build.py")], [finish("x")]]
    assert _align(same, same).candidates == ()
    other = [[LIST], [sh("python3 build.py --fast")], [finish("x")]]
    a = _align(same, other)
    assert a.candidates[0].step == 2 and a.candidates[0].divergence == "different_action"
    # opaque on one side suppresses the mutation rules: no missing/extra_mutation verdicts
    b = _align(
        [[LIST], [sh("python3 inspect.py")], [finish("x")]], [[LIST], [UPDATE], [finish("x")]]
    )
    assert b.candidates[0].divergence == "different_action"
