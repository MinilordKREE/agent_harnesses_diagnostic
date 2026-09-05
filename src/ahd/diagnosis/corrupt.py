"""Deterministic diagnosis corruption and the shuffled control (owner decisions 4, 5, 7).

No reference source: written fresh for ahd (see docs/reuse/M3.md).

Given the clusters of a run, an arm name and a seed, every cluster gets an assignment drawn
from ``random.Random(sha256(cluster_id:arm:seed))``:

* ``where``: another component c' (patchable, where_eligible, not the true one) at the
  requested tier (``near`` = same layer, ``far`` = different layer; empty pool falls back to
  the next tier with ``distance_fallback: true``) and a step t' at which c' is active and
  which is not in the failure's sufficient set;
* ``why``: another cluster's cause label and mechanism sentence (identifier-stripped at
  render time);
* ``how``: another cluster's fix hint;
* ``all``: another cluster's whole diagnosis (the shuffled control).

Coincidence exclusion: a corrupted value must differ from the true one; when no other cluster
or component can supply a different value the assignment is ``impossible`` and recorded as
such rather than silently reused. The assignment table is written to the run directory before
any proposer call.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import Literal

from ahd.core.config import StrictModel
from ahd.diagnosis.cluster import FailureCluster
from ahd.diagnosis.schema import Corruption, Diagnosis, DistanceMeta, Provenance, Tier, Where
from ahd.harness.components import ComponentManifest

ARM_CORRUPTION: dict[str, tuple[Corruption, Tier]] = {
    "reference": ("none", "any"),
    "system": ("none", "any"),
    "shuffled": ("all", "any"),
    "corrupt_where_near": ("where", "near"),
    "corrupt_where_far": ("where", "far"),
    "corrupt_why": ("why", "any"),
    "corrupt_how": ("how", "any"),
}


class Assignment(StrictModel):
    cluster_id: str
    arm: str
    seed: int
    corruption: Corruption
    where: Where | None = None
    origin_cluster: str | None = None
    impossible: str | None = None
    excluded: tuple[str, ...] = ()
    """Values ruled out by coincidence exclusion (components or cluster ids)."""
    rendered_lengths: dict[str, int] | None = None
    """Characters per rendered field (after stripping and capping); filled in after rendering
    and re-written to the table so length is available as a covariate (owner decision, M3.1)."""


class AssignmentTable(StrictModel):
    arm: str
    seed: int
    assignments: tuple[Assignment, ...]


def distance(manifest: ComponentManifest, a: str, b: str) -> tuple[bool, bool]:
    """(same_layer, same_file) between two components."""
    ca, cb = manifest.by_id(a), manifest.by_id(b)
    return ca.layer == cb.layer, bool(set(ca.files) & set(cb.files))


def _rng(cluster_id: str, arm: str, seed: int) -> random.Random:
    digest = hashlib.sha256(f"{cluster_id}:{arm}:{seed}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def where_tiers(
    manifest: ComponentManifest, true_component: str, tier: Tier
) -> list[tuple[Tier, list[str]]]:
    """Decoy component pools in the order to try them: the requested tier first."""
    eligible = [
        c.id
        for c in manifest.components
        if c.patchable and c.where_eligible and c.id != true_component
    ]
    true_layer = manifest.by_id(true_component).layer
    same = [c for c in eligible if manifest.by_id(c).layer == true_layer]
    other = [c for c in eligible if c not in same]
    if tier == "near":
        return [("near", same), ("far", other)]
    if tier == "far":
        return [("far", other), ("near", same)]
    return [("any", eligible)]


def where_pool(
    manifest: ComponentManifest, true_component: str, tier: Tier
) -> tuple[list[str], Tier, bool]:
    """Eligible decoy components at the requested tier; falls back and flags when empty."""
    tiers = where_tiers(manifest, true_component, tier)
    for index, (actual, pool) in enumerate(tiers):
        if pool:
            return pool, actual, index > 0
    return [], tier, True


def _where_choice(
    rng: random.Random,
    tiers: list[tuple[Tier, list[str]]],
    activity: dict[str, set[int]],
    sufficient: set[int],
) -> tuple[str, int, Tier, bool, tuple[str, ...]] | None:
    """(component, step, tier used, fallback, pool) with the component active at a step
    outside the sufficient set; tiers are tried in order."""
    for index, (tier, pool) in enumerate(tiers):
        viable = [c for c in sorted(pool) if activity.get(c, set()) - sufficient]
        if not viable:
            continue
        chosen = rng.choice(viable)
        step = rng.choice(sorted(activity[chosen] - sufficient))
        return chosen, step, tier, index > 0, tuple(sorted(pool))
    return None


def assign(
    clusters: Sequence[FailureCluster],
    *,
    arm: str,
    seed: int,
    manifest: ComponentManifest,
    activity: dict[str, dict[str, set[int]]],
    sufficient: dict[str, set[int]],
) -> AssignmentTable:
    """One assignment per cluster. ``activity[cluster_id][component]`` = steps where it acts;
    ``sufficient[cluster_id]`` = the representative failure's sufficient set."""
    if arm not in ARM_CORRUPTION:
        raise ValueError(f"unknown arm {arm!r}; known: {sorted(ARM_CORRUPTION)}")
    corruption, tier = ARM_CORRUPTION[arm]
    by_id = {c.id: c for c in clusters}
    assignments: list[Assignment] = []
    for cluster in clusters:
        rng = _rng(cluster.id, arm, seed)
        true = cluster.diagnosis_reference
        base = Assignment(cluster_id=cluster.id, arm=arm, seed=seed, corruption=corruption)
        if corruption == "none":
            assignments.append(base)
            continue
        if corruption == "where":
            tiers = where_tiers(manifest, true.where.component, tier)
            if not any(pool for _, pool in tiers):
                assignments.append(
                    base.model_copy(update={"impossible": "no eligible decoy component"})
                )
                continue
            choice = _where_choice(
                rng, tiers, activity.get(cluster.id, {}), sufficient.get(cluster.id, set())
            )
            if choice is None:
                assignments.append(
                    base.model_copy(
                        update={
                            "impossible": "no decoy component active outside the sufficient set",
                            "excluded": (true.where.component,),
                        }
                    )
                )
                continue
            chosen, step, _used_tier, fallback, pool = choice
            same_layer, same_file = distance(manifest, true.where.component, chosen)
            assignments.append(
                base.model_copy(
                    update={
                        "where": Where(
                            component=chosen,
                            step=step,
                            candidates=pool,
                            rule="corruption",
                            attribution="rule",
                            distance_meta=DistanceMeta(
                                same_layer=same_layer,
                                same_file=same_file,
                                requested_tier=tier,
                                distance_fallback=fallback,
                            ),
                        ),
                        "excluded": (true.where.component,),
                    }
                )
            )
            continue
        # why / how / all: another cluster with a different value
        others = [c for c in clusters if c.id != cluster.id]
        if corruption == "why":
            others = [
                c
                for c in others
                if c.cause_label != cluster.cause_label
                or c.diagnosis_reference.why.mechanism_sentence != true.why.mechanism_sentence
            ]
        elif corruption == "how":
            others = [c for c in others if c.diagnosis_reference.how.fix_hint != true.how.fix_hint]
        if not others:
            assignments.append(
                base.model_copy(
                    update={
                        "impossible": "no other cluster with a different value",
                        "excluded": (cluster.id,),
                    }
                )
            )
            continue
        origin = rng.choice(sorted(others, key=lambda c: c.id))
        assignments.append(
            base.model_copy(update={"origin_cluster": origin.id, "excluded": (cluster.id,)})
        )
    _ = by_id
    return AssignmentTable(arm=arm, seed=seed, assignments=tuple(assignments))


def apply(
    diagnosis: Diagnosis, assignment: Assignment, clusters: Sequence[FailureCluster]
) -> Diagnosis:
    """The diagnosis a proposer in ``assignment.arm`` receives for this cluster."""
    if assignment.impossible is not None:
        raise ValueError(
            f"assignment for {assignment.cluster_id} is impossible: {assignment.impossible}"
        )
    source: Literal["reference", "system", "shuffled", "corrupted"]
    if assignment.corruption == "none":
        return diagnosis
    provenance = diagnosis.provenance.model_copy(
        update={"origin_cluster": assignment.origin_cluster}
    )
    if assignment.corruption == "where":
        assert assignment.where is not None
        return diagnosis.model_copy(
            update={
                "where": assignment.where,
                "source": "corrupted",
                "corruption": "where",
                "provenance": provenance,
            }
        )
    origin = next(c for c in clusters if c.id == assignment.origin_cluster).diagnosis_reference
    if assignment.corruption == "why":
        return diagnosis.model_copy(
            update={
                "why": origin.why,
                "source": "corrupted",
                "corruption": "why",
                "provenance": provenance,
            }
        )
    if assignment.corruption == "how":
        return diagnosis.model_copy(
            update={
                "how": origin.how,
                "source": "corrupted",
                "corruption": "how",
                "provenance": provenance,
            }
        )
    source = "shuffled"
    return origin.model_copy(
        update={
            "source": source,
            "corruption": "all",
            "provenance": Provenance(
                **{**diagnosis.provenance.model_dump(), "origin_cluster": assignment.origin_cluster}
            ),
        }
    )
