"""Diagnosis records, rendering, identifier stripping and length matching.

No reference source: written fresh for ahd (see docs/reuse/M3.md). The error-signal fields
follow HarnessEvolve §3.4 "Trajectory Comparison" (arXiv 2609.00829v1): "a structured error
signal F_i = (s_i, m_i, h_i), where s_i is the severity, m_i is the error cause (e.g., tool
hallucination, argument omission, premature termination), and h_i is a natural-language fix
hint". ahd adds WHERE (a harness component and a step) and strips identifiers from WHY/HOW so
that WHERE cannot leak through prose (docs/DEFINITIONS.md).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue
from ahd.harness.components import LAYERS, ComponentManifest

type Severity = Literal["low", "medium", "high", "critical"]
type Source = Literal["reference", "system", "shuffled", "corrupted"]
type Corruption = Literal["none", "where", "why", "how", "all"]
type Attribution = Literal["rule", "llm"]
type Tier = Literal["near", "far", "any"]

CAUSE_LABELS: tuple[str, ...] = (
    "tool_hallucination",
    "argument_error",
    "premature_termination",
    "missing_verification",
    "context_loss",
    "wrong_target",
    "ignored_error",
    "over_exploration",
    "budget_exhaustion",
    "instruction_misread",
    "other",
)
"""Closed cause taxonomy so that clustering is deterministic; the first three are the paper's
own examples."""

PLACEHOLDERS: dict[str, str] = {
    "path": "[path]",
    "routine": "[routine]",
    "tool": "[tool]",
    "component": "[component]",
    "setting": "[setting]",
    "layer": "[layer]",
}


class DistanceMeta(StrictModel):
    """Covariates between the true WHERE and a corrupted WHERE (owner decision 5)."""

    same_layer: bool
    same_file: bool
    requested_tier: Tier
    distance_fallback: bool = False


class Where(StrictModel):
    component: str
    step: int | None
    candidates: tuple[str, ...] = ()
    rule: str | None = None
    attribution: Attribution = "rule"
    distance_meta: DistanceMeta | None = None


class Why(StrictModel):
    cause_label: str
    mechanism_sentence: str


class How(StrictModel):
    fix_hint: str


class Provenance(StrictModel):
    task_id: str
    replicate: str
    attempt: int
    harness_snapshot_id: str
    reference_run: str | None
    oracle_step: int | None
    oracle_validated: bool
    model: str | None
    prompt_sha256: str | None
    request_sha256: str | None
    origin_cluster: str | None = None
    """For corrupted/shuffled diagnoses: the cluster the WHY/HOW/all was taken from."""


class Diagnosis(StrictModel):
    where: Where
    why: Why
    how: How
    severity: Severity
    source: Source
    corruption: Corruption = "none"
    provenance: Provenance
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class RenderBudget(StrictModel):
    """Character budget per rendered field; identical across arms for the same cluster."""

    mechanism: int = Field(default=400, ge=40)
    fix_hint: int = Field(default=300, ge=40)
    filler: str = "No further detail is available for this field."


class Rendered(StrictModel):
    text: str
    field_lengths: dict[str, int]
    placeholder_counts: dict[str, int]
    truncated: dict[str, bool]


# ---------------------------------------------------------------- identifier stripping


def identifier_tokens(
    manifest: ComponentManifest, *, tool_names: Iterable[str] = ()
) -> dict[str, str]:
    """Token -> placeholder class, derived from the manifest (docs/reuse/m3_audit.md section f)."""
    tokens: dict[str, str] = {}
    for component in manifest.components:
        for file in component.files:
            tokens[file] = "path"
        for symbol in component.symbols:
            path, _, rest = symbol.partition(":")
            tokens[path] = "path"
            qual = rest.split("@", 1)[0]
            if not qual:
                continue
            if path.endswith(".json"):
                tokens[qual] = "setting"
            else:
                for part in qual.split("."):
                    if part and part != "__init__":
                        tokens[part] = "routine"
    for name in tool_names:
        tokens[name] = "tool"
    for name in ("run_shell_command", "finish"):
        tokens[name] = "tool"
    for name in ("evobench", "OpenAICompatibleClient", "injected_tools", "harness.json"):
        tokens.setdefault(name, "routine" if name[0].isupper() else "path")
    for layer in LAYERS:
        tokens[layer] = "layer"
    for component in manifest.components:
        tokens[component.id] = "component"  # the WHERE vocabulary wins over same-named keys
    return tokens


def strip_identifiers(text: str, tokens: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Replace every identifier token (word-boundary, case-sensitive) by its class placeholder."""
    counts: dict[str, int] = {}
    ordered = sorted(tokens.items(), key=lambda item: -len(item[0]))
    out = text
    for token, klass in ordered:
        if "/" in token or "." in token:
            pattern = re.compile(r"(?<![\w/])" + re.escape(token) + r"(?![\w/])")
        else:
            pattern = re.compile(r"(?<!\w)" + re.escape(token) + r"(?!\w)")
        out, n = pattern.subn(PLACEHOLDERS[klass], out)
        if n:
            counts[klass] = counts.get(klass, 0) + n
    out = re.sub(r"`\[(\w+)\]`", r"[\1]", out)
    return out, counts


# ---------------------------------------------------------------- rendering


def _fit(text: str, budget: int, filler: str) -> tuple[str, bool]:
    """Trim at a word boundary to ``budget``; pad with a neutral filler up to 90% of it."""
    text = re.sub(r"\s+", " ", text).strip()
    truncated = False
    if len(text) > budget:
        cut = text[:budget]
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
        text, truncated = cut.rstrip(" ,;:") + ".", True
    target = int(budget * 0.9)
    while len(text) < target:
        room = budget - len(text) - 1
        if room < 3:
            break
        chunk = filler if len(filler) <= room else filler[:room]
        if len(filler) > room and " " in chunk:
            chunk = chunk.rsplit(" ", 1)[0]
        if not chunk.strip():
            break
        text = f"{text} {chunk}"
    return text, truncated


def render(
    diagnosis: Diagnosis,
    template: str,
    *,
    tokens: dict[str, str],
    budget: RenderBudget | None = None,
) -> Rendered:
    """Fill the fixed template. WHERE is rendered verbatim; WHY/HOW are stripped and fitted."""
    budget = budget or RenderBudget()
    mechanism, counts_m = strip_identifiers(diagnosis.why.mechanism_sentence, tokens)
    fix_hint, counts_h = strip_identifiers(diagnosis.how.fix_hint, tokens)
    mechanism, trunc_m = _fit(mechanism, budget.mechanism, budget.filler)
    fix_hint, trunc_h = _fit(fix_hint, budget.fix_hint, budget.filler)
    step = "unknown" if diagnosis.where.step is None else str(diagnosis.where.step)
    text = (
        template.replace("{component}", diagnosis.where.component)
        .replace("{step}", step)
        .replace("{severity}", diagnosis.severity)
        .replace("{cause_label}", diagnosis.why.cause_label)
        .replace("{mechanism}", mechanism)
        .replace("{fix_hint}", fix_hint)
    )
    counts: dict[str, int] = {}
    for part in (counts_m, counts_h):
        for key, value in part.items():
            counts[key] = counts.get(key, 0) + value
    return Rendered(
        text=text,
        field_lengths={"mechanism": len(mechanism), "fix_hint": len(fix_hint)},
        placeholder_counts=counts,
        truncated={"mechanism": trunc_m, "fix_hint": trunc_h},
    )


def shared_budget(
    diagnoses: Sequence[Diagnosis], *, base: RenderBudget | None = None
) -> RenderBudget:
    """One budget for a set of arms: the base budget, never larger than the base."""
    _ = diagnoses
    return base or RenderBudget()


def load_template(path: str | None = None) -> str:
    from pathlib import Path

    from ahd.core.io import read_text

    template = read_text(Path(path or "configs/prompts/diagnosis/diagnosis_template.md"))
    for key in (
        "{component}",
        "{step}",
        "{severity}",
        "{cause_label}",
        "{mechanism}",
        "{fix_hint}",
    ):
        if key not in template:
            raise ValueError(f"diagnosis template lacks {key}")
    return template
