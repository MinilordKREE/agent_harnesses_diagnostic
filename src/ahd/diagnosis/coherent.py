"""COH-WRONG (M3.2): coherent-wrong diagnoses and the plausibility-parity judge.

No reference source: written fresh for ahd (see docs/reuse/M3.md, M3.2). A coherent-wrong
diagnosis names a decoy component c' and step s' (``corrupt.assign``, arm ``coherent_wrong``)
and carries a mechanism and fix hint written *for* that decoy by the ``forced_where`` diagnoser
variant, which sees the cluster's failed trajectories only (never the reference). A blind judge
then scores the rendered REF and COH-WRONG diagnoses for plausibility (order randomised); the
arm is accepted per source when the paired mean difference is within +-0.3 and a two-sided
paired sign test is not significant at alpha = 0.10. On violation the texts are regenerated
with a new variant seed (the judge's scores are never shown to the generator), at most
``MAX_REGENERATIONS`` times; a persistent violation is recorded, never hidden.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from math import comb
from pathlib import Path
from typing import Any, Literal

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.diagnosis.cluster import FailureCluster
from ahd.diagnosis.corrupt import CoherentWrong
from ahd.diagnosis.llm import DiagnosisLLM, MalformedModelOutput
from ahd.diagnosis.render import condensed
from ahd.diagnosis.schema import CauseVocabulary, How, StepBasis, Why
from ahd.diagnosis.signal import _cause, _severity
from ahd.errors import TaskFailure
from ahd.harness.components import ComponentManifest

PROMPT_DIR = Path("configs/prompts/diagnosis")
MAX_MEMBER_TRAJECTORIES = 3
PARITY_MEAN_TOLERANCE = 0.3
PARITY_ALPHA = 0.10
MAX_REGENERATIONS = 3
_EPS = 1e-9

type Member = tuple[str, str, dict[str, Any]]
"""(failure key, task prompt as the failed run saw it, failed trajectory.json)."""


def load_prompts(directory: Path = PROMPT_DIR) -> dict[str, str]:
    return {name: read_text(directory / f"{name}.md") for name in ("forced_where", "plausibility")}


def _member_blocks(members: Sequence[Member], *, keep_step: int | None) -> str:
    keep = (keep_step,) if keep_step is not None else ()
    return "\n\n".join(
        f"[failed run {key}]\nTask prompt: {prompt}\n{condensed(trajectory, keep_steps=keep)}"
        for key, prompt, trajectory in members
    )


def generate(
    cluster: FailureCluster,
    *,
    members: Sequence[Member],
    component: str,
    step: int,
    step_basis: StepBasis,
    manifest: ComponentManifest,
    llm: DiagnosisLLM,
    prompt_template: str,
    vocabulary: CauseVocabulary,
    seed: int,
    generation_seed: int,
) -> CoherentWrong:
    """One COH-WRONG text for a cluster. The variant seed shuffles the member order and the
    vocabulary listing and is printed in the prompt, so a regeneration is a different request
    at temperature 0 without changing the instruction."""
    rng = random.Random(f"coherent:{cluster.id}:{seed}:{generation_seed}")
    chosen = list(members)[:MAX_MEMBER_TRAJECTORIES]
    rng.shuffle(chosen)
    causes = list(vocabulary.causes)
    rng.shuffle(causes)
    listing = "\n".join(f"- {c.id}: {c.description}" for c in causes)
    listing += "\n- other:<short text>: only when none of the above fits"
    spec = manifest.by_id(component)
    prompt = (
        prompt_template.replace("{component}", component)
        .replace("{component_role}", spec.role)
        .replace("{layer}", spec.layer)
        .replace("{step}", str(step))
        .replace("{failed_trajectories}", _member_blocks(chosen, keep_step=step))
        .replace("{cause_labels}", listing)
        .replace("{variant}", str(generation_seed))
    )
    scope = "coherent:" + sha256_of(
        {
            "cluster": cluster.id,
            "members": [k for k, _, _ in chosen],
            "trajectories": [t for _, _, t in chosen],
            "component": component,
            "step": step,
            "generation_seed": generation_seed,
        }
    )
    answer = llm.ask_json(prompt, unit_id=cluster.id, cache_scope=scope)
    data = answer.data
    return CoherentWrong(
        cluster_id=cluster.id,
        seed=seed,
        generation_seed=generation_seed,
        component=component,
        step=step,
        step_basis=step_basis,
        why=Why(
            cause_label=_cause(data.get("cause_label"), vocabulary),
            mechanism_sentence=str(data.get("mechanism", "")).strip(),
        ),
        how=How(fix_hint=str(data.get("fix_hint", "")).strip()),
        severity=_severity(data.get("severity")),
        model=answer.response.model,
        prompt_sha256=answer.prompt_sha256,
        request_sha256=answer.response.request_sha256,
    )


# ---------------------------------------------------------------- plausibility parity


class PlausibilityScore(StrictModel):
    cluster_id: str
    generation_seed: int
    reference_score: int | None
    coherent_score: int | None
    reference_position: Literal["A", "B"]
    model: str | None = None
    request_sha256: str | None = None
    error: str | None = None


class ParityResult(StrictModel):
    n: int
    mean_difference: float | None
    """Mean of (reference score - coherent score) over scored pairs."""
    reference_higher: int
    coherent_higher: int
    ties: int
    sign_test_p: float | None
    ok: bool
    reasons: tuple[str, ...] = ()


def _score(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TaskFailure(
            f"plausibility score {value!r} is not a number", kind="malformed_model_output"
        )
    score = round(float(value))
    if not 1 <= score <= 5:
        raise TaskFailure(
            f"plausibility score {value!r} not in 1..5", kind="malformed_model_output"
        )
    return score


def judge_plausibility(
    cluster: FailureCluster,
    *,
    members: Sequence[Member],
    rendered_reference: str,
    rendered_coherent: str,
    llm: DiagnosisLLM,
    prompt_template: str,
    seed: int,
    generation_seed: int,
) -> PlausibilityScore:
    """Blind judge: failed trajectories plus the two rendered diagnoses in a seeded random
    order; never the reference trajectory, never which diagnosis is which."""
    rng = random.Random(f"parity:{cluster.id}:{seed}:{generation_seed}")
    reference_first = rng.random() < 0.5
    a, b = (
        (rendered_reference, rendered_coherent)
        if reference_first
        else (rendered_coherent, rendered_reference)
    )
    chosen = list(members)[:MAX_MEMBER_TRAJECTORIES]
    prompt = (
        prompt_template.replace("{failed_trajectories}", _member_blocks(chosen, keep_step=None))
        .replace("{diagnosis_a}", a.strip())
        .replace("{diagnosis_b}", b.strip())
    )
    scope = "parity:" + sha256_of(
        {
            "cluster": cluster.id,
            "members": [k for k, _, _ in chosen],
            "a": a,
            "b": b,
            "generation_seed": generation_seed,
        }
    )
    position: Literal["A", "B"] = "A" if reference_first else "B"
    try:
        answer = llm.ask_json(prompt, unit_id=cluster.id, cache_scope=scope)
        scores = {"A": _score(answer.data.get("A")), "B": _score(answer.data.get("B"))}
    except (MalformedModelOutput, TaskFailure) as exc:
        return PlausibilityScore(
            cluster_id=cluster.id,
            generation_seed=generation_seed,
            reference_score=None,
            coherent_score=None,
            reference_position=position,
            error=str(exc),
        )
    other: Literal["A", "B"] = "B" if reference_first else "A"
    return PlausibilityScore(
        cluster_id=cluster.id,
        generation_seed=generation_seed,
        reference_score=scores[position],
        coherent_score=scores[other],
        reference_position=position,
        model=answer.response.model,
        request_sha256=answer.response.request_sha256,
    )


def sign_test_p(positive: int, negative: int) -> float | None:
    """Two-sided exact binomial sign test on the non-tied pairs (p = 1/2)."""
    m = positive + negative
    if m == 0:
        return None
    k = min(positive, negative)
    tail = sum(comb(m, i) for i in range(k + 1)) / (1 << m)
    return min(1.0, 2.0 * tail)


def parity(scores: Sequence[PlausibilityScore]) -> ParityResult:
    """The parity requirement (owner, M3.2): paired mean difference within +-0.3 and no
    sign-test significance at alpha = 0.10; computed over the scored pairs."""
    diffs = [
        s.reference_score - s.coherent_score
        for s in scores
        if s.error is None and s.reference_score is not None and s.coherent_score is not None
    ]
    if not diffs:
        return ParityResult(
            n=0,
            mean_difference=None,
            reference_higher=0,
            coherent_higher=0,
            ties=0,
            sign_test_p=None,
            ok=False,
            reasons=("no scored pairs",),
        )
    mean = statistics.fmean(diffs)
    positive = sum(1 for d in diffs if d > 0)
    negative = sum(1 for d in diffs if d < 0)
    p = sign_test_p(positive, negative)
    reasons: list[str] = []
    if abs(mean) > PARITY_MEAN_TOLERANCE + _EPS:
        reasons.append(f"mean difference {mean:+.2f} outside +-{PARITY_MEAN_TOLERANCE}")
    if p is not None and p < PARITY_ALPHA:
        reasons.append(f"sign test p={p:.3f} < {PARITY_ALPHA}")
    return ParityResult(
        n=len(diffs),
        mean_difference=mean,
        reference_higher=positive,
        coherent_higher=negative,
        ties=len(diffs) - positive - negative,
        sign_test_p=p,
        ok=not reasons,
        reasons=tuple(reasons),
    )
