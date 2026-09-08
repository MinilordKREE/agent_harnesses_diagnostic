"""Privileged-information probe (M3.2): what a blind model recovers from a rendered diagnosis.

No reference source: written fresh for ahd (see docs/reuse/M3.md, M3.2). The probe receives
ONLY one rendered diagnosis, the mining pool's task ids with one-line descriptions, the
source's tool vocabulary and its answer categories; it guesses the task (top-1 / top-3), the
required tool calls and the answer category. Run per arm (REF, SELF = system, SHUF = shuffled,
COH-WRONG); recovery rates per arm and the REF - SELF gap per cluster are study covariates.
Cached, ledgered as arm ``probe``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.diagnosis.llm import DiagnosisLLM, MalformedModelOutput

PROBE_ARM = "probe"


class ProbeRecord(StrictModel):
    cluster_id: str
    arm: str
    assigned_task: str
    """The task of the cluster the proposer would work on."""
    origin_task: str
    """The task the rendered diagnosis was actually written about (differs for shuffled)."""
    task_top3: tuple[str, ...]
    hit_top1: bool
    hit_top3: bool
    origin_hit_top1: bool
    origin_hit_top3: bool
    guessed_tools: tuple[str, ...]
    required_tools: tuple[str, ...]
    tool_precision: float | None
    tool_recall: float | None
    guessed_category: str | None
    true_category: str | None
    category_hit: bool | None
    model: str | None = None
    request_sha256: str | None = None
    error: str | None = None


class ProbeReport(StrictModel):
    seed: int
    pool_size: int
    chance_top1: float
    chance_top3: float
    records: tuple[ProbeRecord, ...]


def load_prompt(path: Path = Path("configs/prompts/diagnosis/pi_probe.md")) -> str:
    return read_text(path)


def one_line(prompt: str, limit: int = 100) -> str:
    flat = " ".join(prompt.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def probe_one(
    rendered: str,
    *,
    cluster_id: str,
    arm: str,
    assigned_task: str,
    origin_task: str,
    pool: Sequence[tuple[str, str]],
    tools: Sequence[str],
    categories: Sequence[str],
    required_tools: Sequence[str],
    true_category: str | None,
    llm: DiagnosisLLM,
    prompt_template: str,
    seed: int,
) -> ProbeRecord:
    prompt = (
        prompt_template.replace("{diagnosis}", rendered.strip())
        .replace("{tasks}", "\n".join(f"- {tid}: {desc}" for tid, desc in pool))
        .replace("{tools}", "\n".join(f"- {t}" for t in tools))
        .replace("{categories}", "\n".join(f"- {c}" for c in categories))
    )
    scope = "probe:" + sha256_of(
        {"cluster": cluster_id, "arm": arm, "rendered": rendered, "pool": list(pool), "seed": seed}
    )
    base = {
        "cluster_id": cluster_id,
        "arm": arm,
        "assigned_task": assigned_task,
        "origin_task": origin_task,
        "required_tools": tuple(sorted(set(required_tools))),
        "true_category": true_category,
    }
    try:
        answer = llm.ask_json(prompt, unit_id=cluster_id, cache_scope=scope)
    except MalformedModelOutput as exc:
        return ProbeRecord(
            **base,
            task_top3=(),
            hit_top1=False,
            hit_top3=False,
            origin_hit_top1=False,
            origin_hit_top3=False,
            guessed_tools=(),
            tool_precision=None,
            tool_recall=None,
            guessed_category=None,
            category_hit=None,
            error=str(exc),
        )
    data = answer.data
    raw_tasks = data.get("task_top3")
    top3 = tuple(str(x) for x in raw_tasks)[:3] if isinstance(raw_tasks, list) else ()
    raw_tools = data.get("required_tools")
    guessed = (
        tuple(sorted({str(x) for x in raw_tools if str(x)})) if isinstance(raw_tools, list) else ()
    )
    truth = set(required_tools)
    hits = len(set(guessed) & truth)
    category = data.get("answer_category")
    guessed_category = str(category) if category is not None else None
    return ProbeRecord(
        **base,
        task_top3=top3,
        hit_top1=bool(top3) and top3[0] == assigned_task,
        hit_top3=assigned_task in top3,
        origin_hit_top1=bool(top3) and top3[0] == origin_task,
        origin_hit_top3=origin_task in top3,
        guessed_tools=guessed,
        tool_precision=(hits / len(guessed)) if guessed else None,
        tool_recall=(hits / len(truth)) if truth else None,
        guessed_category=guessed_category,
        category_hit=(
            guessed_category.strip().lower() == true_category.strip().lower()
            if guessed_category is not None and true_category is not None
            else None
        ),
        model=answer.response.model,
        request_sha256=answer.response.request_sha256,
    )
