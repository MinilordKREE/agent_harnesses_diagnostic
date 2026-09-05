"""Reference genuineness (HarnessEvolve §3.3 "Reference Verification"; owner decision 6).

No reference source: written fresh for ahd (see docs/reuse/M3.md). Rubric G1 to G4 from
docs/reuse/m3_audit.md section e: G1 required actions and G4 effort are deterministic; G2
answer derivation and G3 reference dependence are one judge call. Verdicts: ``genuine`` (all
four), ``shortcut`` (G1 fails, or the judge finds G2 or G3 violated), otherwise
``undetermined``. Only ``genuine`` references are oracles; ``undetermined`` never is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.diagnosis.align import actions_from_trajectory, classify_shell
from ahd.diagnosis.llm import DiagnosisLLM, MalformedModelOutput
from ahd.diagnosis.render import condensed
from ahd.errors import InfraError
from ahd.tasks.models import Task

type Verdict = Literal["genuine", "shortcut", "undetermined"]
_SEARCH_ACTION = re.compile(r"\b(curl|wget|serper|search|http[s]?://)\b", re.IGNORECASE)


class GenuinenessRecord(StrictModel):
    task_id: str
    replicate: str
    attempt: int
    g1: bool
    g1_detail: str
    g4: bool
    g2: bool | None
    g3: bool | None
    verdict: Verdict
    explanation: str | None
    prompt_sha256: str | None
    request_sha256: str | None
    model: str | None
    error: str | None = None


def claw_required_tools(task: Task, *, claw_repo: Path | None) -> tuple[str, ...]:
    """Tool names of every ``tool_called`` check in the task's scoring components."""
    if claw_repo is None:
        raise InfraError(
            "genuineness for Claw-Eval needs the Claw-Eval checkout", kind="claw_eval_missing"
        )
    task_dir = Path(str(task.evaluator.spec.get("task_dir", "")))
    if not task_dir.is_absolute():
        task_dir = claw_repo / task_dir
    try:
        data = yaml.safe_load(read_text(task_dir / "task.yaml"))
    except yaml.YAMLError as exc:
        raise InfraError(f"invalid task.yaml under {task_dir}: {exc}", kind="corrupt_file") from exc
    names: list[str] = []
    for component in (data or {}).get("scoring_components", []) or []:
        if not isinstance(component, dict):
            continue
        checks: list[Any] = []
        if isinstance(component.get("check"), dict):
            checks.append(component["check"])
        if isinstance(component.get("checks"), list):
            checks.extend(component["checks"])
        for check in checks:
            if isinstance(check, dict) and check.get("type") == "tool_called":
                name = check.get("tool_name") or check.get("tool") or check.get("name")
                if isinstance(name, str):
                    names.append(name)
    return tuple(sorted(set(names)))


def successful_dispatches(rollout_dir: Path) -> set[str]:
    path = rollout_dir / "claw_dispatches.jsonl"
    names: set[str] = set()
    if not path.is_file():
        return names
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        status = row.get("response_status")
        if isinstance(status, int) and status < 400 and isinstance(row.get("tool_name"), str):
            names.add(row["tool_name"])
    return names


def deterministic_checks(
    task: Task, trajectory: dict[str, Any], *, rollout_dir: Path, claw_repo: Path | None
) -> tuple[bool, str, bool]:
    """(G1, detail, G4) from the trajectory, dispatch log and artifacts."""
    steps = actions_from_trajectory(trajectory)
    actions = [a for s in steps for a in s.actions]
    observations = sum(
        1
        for e in trajectory.get("trajectory", [])
        if isinstance(e, dict) and e.get("role") == "tool"
    )
    g4 = len(steps) >= 2 and observations >= 1
    match task.source_benchmark:
        case "claw_eval":
            required = claw_required_tools(task, claw_repo=claw_repo)
            done = successful_dispatches(rollout_dir)
            missing = [t for t in required if t not in done]
            g1 = not missing
            detail = f"required tools {list(required)}; missing {missing}"
        case "gdpval":
            artifacts = rollout_dir / "artifacts"
            files = [p for p in artifacts.rglob("*") if p.is_file()] if artifacts.is_dir() else []
            g1 = bool(files)
            detail = f"{len(files)} deliverable file(s) under artifacts/"
        case "browsecomp":
            gathering = [
                a
                for a in actions
                if a.klass in ("shell_ro", "shell_mut", "shell_opaque")
                and _SEARCH_ACTION.search(str(a.arguments.get("command", "")))
            ]
            g1 = bool(gathering)
            detail = f"{len(gathering)} information-gathering command(s)"
        case _:
            # hle: closed-book questions have no required action; any executed tool call counts
            executed = [a for a in actions if a.klass != "finish" and a.klass != "final"]
            g1 = bool(executed)
            detail = (
                f"{len(executed)} executed action(s) "
                f"(no required action for {task.source_benchmark})"
            )
    _ = classify_shell
    return g1, detail, g4


def load_prompt(path: Path = Path("configs/prompts/diagnosis/genuineness.md")) -> str:
    return read_text(path)


def verify(
    task: Task,
    trajectory: dict[str, Any],
    *,
    replicate: str,
    attempt: int,
    rollout_dir: Path,
    claw_repo: Path | None,
    llm: DiagnosisLLM,
    prompt_template: str,
    task_prompt: str,
) -> GenuinenessRecord:
    g1, detail, g4 = deterministic_checks(
        task, trajectory, rollout_dir=rollout_dir, claw_repo=claw_repo
    )
    base = {
        "task_id": task.id,
        "replicate": replicate,
        "attempt": attempt,
        "g1": g1,
        "g1_detail": detail,
        "g4": g4,
    }
    if not g1:
        return GenuinenessRecord(
            **base,
            g2=None,
            g3=None,
            verdict="shortcut",
            explanation="G1 failed: " + detail,
            prompt_sha256=None,
            request_sha256=None,
            model=None,
        )
    prompt = (
        prompt_template.replace("{task_prompt}", task_prompt)
        .replace("{trajectory}", condensed(trajectory))
        .replace("{g1}", f"{'pass' if g1 else 'fail'} ({detail})")
        .replace("{g4}", "pass" if g4 else "fail")
    )
    scope = "genuineness:" + sha256_of({"task": task.id, "trajectory": trajectory})
    try:
        answer = llm.ask_json(prompt, unit_id=task.id, cache_scope=scope)
    except MalformedModelOutput as exc:
        return GenuinenessRecord(
            **base,
            g2=None,
            g3=None,
            verdict="undetermined",
            explanation=None,
            prompt_sha256=None,
            request_sha256=None,
            model=llm.config.model,
            error=str(exc),
        )
    g2 = answer.data.get("g2")
    g3 = answer.data.get("g3")
    g2b = g2 if isinstance(g2, bool) else None
    g3b = g3 if isinstance(g3, bool) else None
    verdict: Verdict
    if g2b is False or g3b is False:
        verdict = "shortcut"
    elif g1 and g4 and g2b and g3b:
        verdict = "genuine"
    else:
        verdict = "undetermined"
    return GenuinenessRecord(
        **base,
        g2=g2b,
        g3=g3b,
        verdict=verdict,
        explanation=str(answer.data.get("explanation", ""))[:1000] or None,
        prompt_sha256=answer.prompt_sha256,
        request_sha256=answer.response.request_sha256,
        model=answer.response.model,
    )
