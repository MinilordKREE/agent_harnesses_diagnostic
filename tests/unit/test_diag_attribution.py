"""Rule table R1 to R10 and component activity."""

from __future__ import annotations

from typing import Any

import pytest

from ahd.diagnosis.align import actions_from_trajectory, align
from ahd.diagnosis.attribution import RULES, AttributionRule, active_steps, attribute, system_rule
from ahd.harness.components import ComponentManifest
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import Step, finish, sh, trajectory

LIST: tuple[str, dict[str, Any]] = ("todo_list_tasks", {})
UPDATE: tuple[str, dict[str, Any]] = (
    "todo_update_task",
    {"task_id": "todo_001", "status": "completed"},
)


@pytest.fixture(scope="module")
def manifest() -> ComponentManifest:
    return ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")


def _rule(
    failed: list[Step],
    reference: list[Step],
    manifest: ComponentManifest,
    *,
    exit_reason: str = "finished",
    outputs: dict[int, dict[str, Any]] | None = None,
) -> AttributionRule:
    f = trajectory(failed, outputs=outputs)
    r = trajectory(reference)
    a = align(f, r, task_id="t", failed_exit_reason=exit_reason)
    assert a.candidates, "no candidate"
    ref_actions = [x for s in actions_from_trajectory(r) for x in s.actions]
    return attribute(
        a.candidates[0],
        failed_trajectory=f,
        reference_actions=ref_actions,
        failed_exit_reason=exit_reason,
        manifest=manifest,
    )


def test_every_rule_names_only_eligible_patchable_components(manifest: ComponentManifest) -> None:
    for rule, ids in RULES.items():
        for cid in ids:
            spec = manifest.by_id(cid)
            assert spec.patchable and spec.where_eligible, (rule, cid)


def test_r1_family(manifest: ComponentManifest) -> None:
    assert (
        _rule(
            [[LIST], [sh("cat a")], [finish("x")]], [[LIST], [UPDATE], [finish("x")]], manifest
        ).rule_id
        == "R9"
    )
    r1 = _rule(
        [[sh("ls")], [sh("cp a b")], [finish("x")]],
        [[sh("ls")], [sh("cp a c")], [finish("x")]],
        manifest,
    )
    assert r1.rule_id == "R1" and set(r1.candidates) == {
        "system_prompt",
        "task_prompt",
        "context_window",
        "planner",
    }
    bad = {1: {"stdout": "", "stderr": "boom", "exit_code": 2, "duration_seconds": 0.1}}
    r1a = _rule(
        [[sh("ls")], [sh("cp a b")], [finish("x")]],
        [[sh("ls")], [sh("cp a c")], [finish("x")]],
        manifest,
        outputs=bad,
    )
    assert r1a.rule_id == "R1a"
    trunc = {
        1: {
            "stdout": "x\n...[truncated 500 chars]...\ny",
            "stderr": "",
            "exit_code": 0,
            "duration_seconds": 0.1,
        }
    }
    r1b = _rule(
        [[sh("ls")], [sh("cp a b")], [finish("x")]],
        [[sh("ls")], [sh("cp a c")], [finish("x")]],
        manifest,
        outputs=trunc,
    )
    assert r1b.rule_id == "R1b" and r1b.candidates == ("observation_shaping",)


def test_r2_family_and_r8(manifest: ComponentManifest) -> None:
    other = ("todo_create_task", {"title": "x"})
    r2a = _rule([[LIST], [other], [finish("x")]], [[LIST], [UPDATE], [finish("x")]], manifest)
    assert r2a.rule_id == "R2a"  # the reference's todo_update_task never appears in the failed run
    r2 = _rule(
        [[LIST], [UPDATE], [other], [finish("x")]],
        [[LIST], [UPDATE], [("todo_create_task", {"title": "y"})], [finish("x")]],
        manifest,
    )
    assert r2.rule_id == "R2" and set(r2.candidates) == {
        "tool_router",
        "task_prompt",
        "context_window",
    }
    unknown = {2: {"content": '{"error": "unknown tool todo_bogus"}'}}
    r8 = _rule(
        [[LIST], [("todo_bogus", {})], [finish("x")]],
        [[LIST], [UPDATE], [finish("x")]],
        manifest,
        outputs=unknown,
    )
    assert r8.rule_id == "R8"


def test_r3_r4_r5_r6(manifest: ComponentManifest) -> None:
    r3 = _rule([[LIST], [finish("all good")]], [[LIST], [UPDATE], [finish("x")]], manifest)
    assert r3.rule_id == "R3"
    r3a = _rule(
        [[LIST], [finish("the answer is 12345")]], [[LIST], [UPDATE], [finish("x")]], manifest
    )
    assert r3a.rule_id == "R3a" and r3a.candidates == ("verifier", "observation_shaping")
    r4 = _rule(
        [[LIST], "content only"],
        [[LIST], [UPDATE], [finish("x")]],
        manifest,
        exit_reason="assistant_no_tool_call",
    )
    assert r4.rule_id == "R4"
    r5 = _rule(
        [[LIST]], [[LIST], [UPDATE], [finish("x")]], manifest, exit_reason="model_call_error"
    )
    assert r5.rule_id == "R5"
    r6 = _rule(
        [[LIST], [LIST]],
        [[LIST], [LIST], [UPDATE], [finish("x")]],
        manifest,
        exit_reason="max_steps",
    )
    assert r6.rule_id == "R6"


def test_r7_shell_timeout(manifest: ComponentManifest) -> None:
    timeout = {
        2: {
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "timeout": True,
            "duration_seconds": 120.0,
        }
    }
    r7 = _rule(
        [[sh("ls")], [sh("sleep 999")], [finish("x")]],
        [[sh("ls")], [sh("cp a c")], [finish("x")]],
        manifest,
        outputs=timeout,
    )
    assert r7.rule_id == "R7" and set(r7.candidates) == {"budget", "tool_shell", "error_handling"}


def test_r10_early(manifest: ComponentManifest) -> None:
    r10 = _rule([[LIST], [finish("x")]], [[sh("ls")], [finish("x")]], manifest)
    assert r10.rule_id == "R10" and r10.candidates == ("task_prompt", "system_prompt")


def test_system_rule_uses_exit_reason(manifest: ComponentManifest) -> None:
    t = trajectory([[LIST], "answer"])
    assert (
        system_rule(
            t, exit_reason="assistant_no_tool_call", score_reason="", manifest=manifest
        ).rule_id
        == "R4"
    )
    assert (
        system_rule(t, exit_reason="max_steps", score_reason="", manifest=manifest).rule_id == "R6"
    )
    t2 = trajectory([[LIST], [finish("value 999")]])
    assert (
        system_rule(t2, exit_reason="finished", score_reason="", manifest=manifest).rule_id == "R3a"
    )


def test_active_steps() -> None:
    t = trajectory(
        [[LIST], [sh("ls")], [finish("x")]],
        outputs={2: {"stdout": "", "stderr": "e", "exit_code": 1, "duration_seconds": 0.1}},
    )
    assert active_steps("system_prompt", t) == {1, 2, 3}
    assert active_steps("tool_shell", t) == {2}
    assert active_steps("tool_router", t) == {1, 2, 3}
    assert active_steps("completion_policy", t) == {3}
    assert active_steps("error_handling", t) == {2}
