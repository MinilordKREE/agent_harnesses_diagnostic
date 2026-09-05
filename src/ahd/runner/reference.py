"""Reference mode: the frozen prompt block that makes the evaluator's reference visible.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The block template is
``configs/harness/reference_block.md``; the gold content per source follows the audit
(docs/reuse/evobench_runner.md section d): ``expected`` for BrowseComp/HLE, the rubric text for
GDPval, ``scoring_components`` + ``safety_checks`` from Claw-Eval's private ``task.yaml``.
The harness is never edited: the block is appended to the public task prompt by the runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ahd.core.io import read_text
from ahd.errors import ConfigError, InfraError
from ahd.tasks.models import Task

DEFAULT_TEMPLATE_PATH = Path("configs/harness/reference_block.md")
REFERENCE_MARKER = "=== REFERENCE (reference mode) ==="


def _gdpval_rubric_text(spec: dict[str, Any]) -> str:
    rubric = spec.get("rubric")
    if isinstance(rubric, str):
        try:
            rubric = json.loads(rubric)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"GDPval rubric is not valid JSON: {exc}") from exc
    if not isinstance(rubric, list):
        raise ConfigError("GDPval rubric must be a list")
    lines = [
        "Rubric (points, criterion); pass threshold "
        f"{spec.get('pass_threshold', 0.6)} of the positive points:"
    ]
    for item in rubric:
        if not isinstance(item, dict):
            continue
        lines.append(f"- ({item.get('score')}) {item.get('criterion')}")
    return "\n".join(lines)


def _claw_gold_text(spec: dict[str, Any], claw_repo: Path | None) -> str:
    if claw_repo is None:
        raise InfraError(
            "reference mode for Claw-Eval needs the Claw-Eval checkout", kind="claw_eval_missing"
        )
    task_dir = Path(str(spec.get("task_dir", "")))
    if not task_dir.is_absolute():
        task_dir = claw_repo / task_dir
    task_yaml = task_dir / "task.yaml"
    try:
        data = yaml.safe_load(read_text(task_yaml))
    except yaml.YAMLError as exc:
        raise InfraError(f"invalid task.yaml at {task_yaml}: {exc}", kind="corrupt_file") from exc
    if not isinstance(data, dict):
        raise InfraError(f"task.yaml at {task_yaml} is not a mapping", kind="corrupt_file")
    sections = {k: data.get(k) for k in ("scoring_components", "safety_checks") if k in data}
    if not sections:
        raise ConfigError(f"{task_yaml} has neither scoring_components nor safety_checks")
    return (
        "Evaluator checks (from the task definition):\n"
        + yaml.safe_dump(sections, sort_keys=False, allow_unicode=True).rstrip()
    )


def gold_text(task: Task, *, claw_repo: Path | None) -> str:
    spec = dict(task.evaluator.spec)
    match task.source_benchmark:
        case "browsecomp" | "hle":
            return f"Expected answer: {spec.get('expected')}"
        case "gdpval":
            return _gdpval_rubric_text(spec)
        case "claw_eval":
            return _claw_gold_text(spec, claw_repo)
        case _:
            raise ConfigError(f"no reference content for source {task.source_benchmark!r}")


def load_template(path: Path = DEFAULT_TEMPLATE_PATH) -> str:
    template = read_text(path)
    if "{gold}" not in template or REFERENCE_MARKER not in template:
        raise ConfigError(f"reference template {path} must contain {{gold}} and the marker line")
    return template


def render_reference_block(task: Task, *, template: str, claw_repo: Path | None) -> str:
    return template.replace("{gold}", gold_text(task, claw_repo=claw_repo)).rstrip() + "\n"


def with_reference(prompt: str, block: str) -> str:
    return prompt.rstrip() + "\n\n" + block
