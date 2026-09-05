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
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue
from ahd.harness.components import LAYERS, ComponentManifest

type Severity = Literal["low", "medium", "high", "critical"]
type Source = Literal["reference", "system", "shuffled", "corrupted"]
type Corruption = Literal["none", "where", "why", "how", "all"]
type Attribution = Literal["rule", "llm"]
type Tier = Literal["near", "far", "any"]
type FailureType = Literal["deterministic", "stochastic", "unrepairable", "unreplayable"]
"""Replay-validation verdict on a failure (owner decision, M3.1): ``deterministic`` = some
candidate step is sufficient; ``stochastic`` = re-sampling from the prefix passes at every
tested candidate (a policy-level random event that the harness let through); ``unrepairable``
= neither the reference action nor re-sampling rescues the run; ``unreplayable`` = no arm could
be scored."""
type OracleBasis = Literal["sufficient", "manifestation", "unvalidated"]

OTHER_CAUSE = re.compile(r"^other:\s*[\w][\w \-/,'.]{2,60}$")
"""The escape hatch of the controlled vocabulary: ``other:<short text>``."""
CAUSES_PATH = Path("configs/prompts/diagnosis/causes.yaml")


class Cause(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str
    layers: tuple[str, ...] = ()


class CauseVocabulary(StrictModel):
    schema_version: Literal[1]
    causes: tuple[Cause, ...] = Field(min_length=1)

    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.causes)

    def prompt_listing(self) -> str:
        lines = [f"- {c.id}: {c.description}" for c in self.causes]
        lines.append("- other:<short text>: only when none of the above fits")
        return "\n".join(lines)

    def normalise(self, label: object) -> str:
        """The label the model returned, validated: an id, or ``other:<text>``."""
        text = str(label or "").strip()
        if text in self.ids():
            return text
        lowered = text.lower()
        if lowered in self.ids():
            return lowered
        if OTHER_CAUSE.match(text):
            return "other:" + text.split(":", 1)[1].strip().lower()
        raise ValueError(f"cause_label {label!r} is not in the vocabulary and not other:<text>")


def load_causes(path: Path = CAUSES_PATH) -> CauseVocabulary:
    from ahd.core.io import read_text

    try:
        raw = yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    return CauseVocabulary.model_validate(raw)


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
    failure_type: FailureType | None = None
    oracle_step_basis: OracleBasis | None = None
    """``sufficient``: earliest sufficient step; ``manifestation``: the last class candidate (where
    a stochastic failure showed); ``unvalidated``: no replay ran (only with --allow-unvalidated)."""


class Diagnosis(StrictModel):
    where: Where
    why: Why
    how: How
    severity: Severity
    source: Source
    corruption: Corruption = "none"
    provenance: Provenance
    extra: dict[str, JsonValue] = Field(default_factory=dict)


class FieldCaps(StrictModel):
    """Per-field character caps for one cluster (owner decision, M3.1): the longest arm's
    stripped text sets the cap, so nothing is padded and only longer texts are trimmed."""

    mechanism: int = Field(ge=20)
    fix_hint: int = Field(ge=20)


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


def _fit(text: str, cap: int) -> tuple[str, bool]:
    """Collapse whitespace; trim at a word boundary to ``cap``. Never pads."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= cap:
        return text, False
    cut = text[:cap]
    cut = cut[: cut.rfind(" ")] if " " in cut else cut
    return cut.rstrip(" ,;:") + ".", True


def stripped_lengths(diagnosis: Diagnosis, tokens: dict[str, str]) -> dict[str, int]:
    """Field lengths after identifier stripping and whitespace collapse (the cap inputs)."""
    mechanism, _ = strip_identifiers(diagnosis.why.mechanism_sentence, tokens)
    fix_hint, _ = strip_identifiers(diagnosis.how.fix_hint, tokens)
    return {
        "mechanism": len(re.sub(r"\s+", " ", mechanism).strip()),
        "fix_hint": len(re.sub(r"\s+", " ", fix_hint).strip()),
    }


def caps_for(diagnoses: Sequence[Diagnosis], tokens: dict[str, str]) -> FieldCaps:
    """The cap per field is the longest stripped text among the arms of one cluster."""
    lengths = [stripped_lengths(d, tokens) for d in diagnoses]
    return FieldCaps(
        mechanism=max([20, *(x["mechanism"] for x in lengths)]),
        fix_hint=max([20, *(x["fix_hint"] for x in lengths)]),
    )


def render(
    diagnosis: Diagnosis,
    template: str,
    *,
    tokens: dict[str, str],
    caps: FieldCaps,
) -> Rendered:
    """Fill the fixed template. WHERE is rendered verbatim; WHY/HOW are stripped and capped."""
    mechanism, counts_m = strip_identifiers(diagnosis.why.mechanism_sentence, tokens)
    fix_hint, counts_h = strip_identifiers(diagnosis.how.fix_hint, tokens)
    mechanism, trunc_m = _fit(mechanism, caps.mechanism)
    fix_hint, trunc_h = _fit(fix_hint, caps.fix_hint)
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
