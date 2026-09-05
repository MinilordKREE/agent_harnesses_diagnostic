"""Blind localization probe: can WHERE be recovered from the stripped WHY alone?

No reference source: written fresh for ahd (see docs/reuse/M3.md). For each cluster the
identifier-stripped mechanism sentence and the component list go to a fresh model call; the
probe records whether the true component is its top-1 or in its top-3. Reported as a
covariate of the study, never used to edit diagnoses.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.diagnosis.cluster import FailureCluster
from ahd.diagnosis.llm import DiagnosisLLM, MalformedModelOutput
from ahd.diagnosis.schema import strip_identifiers
from ahd.harness.components import ComponentManifest


class LeakageProbe(StrictModel):
    cluster_id: str
    true_component: str
    stripped_mechanism: str
    top3: tuple[str, ...]
    hit_top1: bool
    hit_top3: bool
    error: str | None = None


class LeakageReport(StrictModel):
    probes: tuple[LeakageProbe, ...]
    n: int
    top1_rate: float | None
    top3_rate: float | None
    chance_top1: float
    """1 / number of where-eligible patchable components."""


def load_prompt(path: Path = Path("configs/prompts/diagnosis/leakage.md")) -> str:
    return read_text(path)


def probe(
    clusters: Sequence[FailureCluster],
    *,
    manifest: ComponentManifest,
    tokens: dict[str, str],
    llm: DiagnosisLLM,
    prompt_template: str,
) -> LeakageReport:
    eligible = [c for c in manifest.components if c.patchable and c.where_eligible]
    listing = "\n".join(f"- {c.id} ({c.layer}): {c.role}" for c in eligible)
    probes: list[LeakageProbe] = []
    for cluster in clusters:
        true = cluster.diagnosis_reference.where.component
        stripped, _ = strip_identifiers(cluster.diagnosis_reference.why.mechanism_sentence, tokens)
        prompt = prompt_template.replace("{components}", listing).replace("{mechanism}", stripped)
        scope = "leakage:" + sha256_of({"cluster": cluster.id, "mechanism": stripped})
        try:
            answer = llm.ask_json(prompt, unit_id=cluster.id, cache_scope=scope)
        except MalformedModelOutput as exc:
            probes.append(
                LeakageProbe(
                    cluster_id=cluster.id,
                    true_component=true,
                    stripped_mechanism=stripped,
                    top3=(),
                    hit_top1=False,
                    hit_top3=False,
                    error=str(exc),
                )
            )
            continue
        raw = answer.data.get("top3")
        top3 = tuple(str(x) for x in raw)[:3] if isinstance(raw, list) else ()
        probes.append(
            LeakageProbe(
                cluster_id=cluster.id,
                true_component=true,
                stripped_mechanism=stripped,
                top3=top3,
                hit_top1=bool(top3) and top3[0] == true,
                hit_top3=true in top3,
            )
        )
    scored = [p for p in probes if p.error is None]
    n = len(scored)
    return LeakageReport(
        probes=tuple(probes),
        n=n,
        top1_rate=(sum(p.hit_top1 for p in scored) / n) if n else None,
        top3_rate=(sum(p.hit_top3 for p in scored) / n) if n else None,
        chance_top1=1.0 / max(1, len(eligible)),
    )
