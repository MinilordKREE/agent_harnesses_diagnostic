"""Failure clustering (HarnessEvolve §3.4 "Error Clustering").

No reference source: written fresh for ahd (see docs/reuse/M3.md). The paper's three
principles, quoted from arXiv 2609.00829v1: "Groups are formed by error cause m_i rather than
coarse severity labels"; "Prioritize the first action divergence point t_i* between τ_i^+ and
τ_i^- as the root error cause"; "Preserve single-member clusters, ensuring rare but critical
failure patterns are not absorbed". ahd keys clusters by (cause label, WHERE component) so that
membership is deterministic, and hashes the membership into the run manifest.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_of
from ahd.diagnosis.schema import Diagnosis, Severity

SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def failure_key(diagnosis: Diagnosis) -> str:
    p = diagnosis.provenance
    return f"{p.task_id}/{p.replicate}/{p.attempt}"


class FailureCluster(StrictModel):
    id: str
    cause_label: str
    component: str
    members: tuple[str, ...]
    representative: str
    diagnosis_reference: Diagnosis
    oracle_validated_members: int
    max_severity: Severity
    failure_types: dict[str, int] = {}
    """Members by replay verdict (``deterministic`` / ``stochastic`` / ``unvalidated``)."""


class ClusterSet(StrictModel):
    clusters: tuple[FailureCluster, ...]
    membership_sha256: str
    failure_type_counts: dict[str, int] = {}
    """Reference-arm diagnoses by failure type; the E0 headline distribution."""
    unvalidated: tuple[str, ...]
    """Failure keys whose oracle step could not be validated (excluded from oracle arms)."""


def _type_counts(diagnoses: Sequence[Diagnosis]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in diagnoses:
        key = d.provenance.failure_type or "unvalidated"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _rank(d: Diagnosis) -> tuple[int, int, str]:
    step = d.where.step if d.where.step is not None else 10**6
    return (-SEVERITY_RANK[d.severity], step, failure_key(d))


def cluster(diagnoses: Sequence[Diagnosis]) -> ClusterSet:
    """Group by (cause_label, component); representative = highest severity, then earliest step."""
    groups: dict[tuple[str, str], list[Diagnosis]] = defaultdict(list)
    for d in diagnoses:
        groups[(d.why.cause_label, d.where.component)].append(d)
    clusters: list[FailureCluster] = []
    for (cause, component), members in sorted(groups.items()):
        members_sorted = sorted(members, key=_rank)
        keys = tuple(sorted(failure_key(d) for d in members_sorted))
        representative = members_sorted[0]
        cluster_id = "c" + sha256_of({"cause": cause, "component": component, "members": keys})[:8]
        clusters.append(
            FailureCluster(
                id=cluster_id,
                cause_label=cause,
                component=component,
                members=keys,
                representative=failure_key(representative),
                diagnosis_reference=representative,
                oracle_validated_members=sum(1 for d in members if d.provenance.oracle_validated),
                max_severity=representative.severity,
                failure_types=_type_counts(members),
            )
        )
    membership = sha256_of([{"id": c.id, "members": list(c.members)} for c in clusters])
    unvalidated = tuple(
        sorted(failure_key(d) for d in diagnoses if not d.provenance.oracle_validated)
    )
    return ClusterSet(
        clusters=tuple(clusters),
        membership_sha256=membership,
        failure_type_counts=_type_counts(list(diagnoses)),
        unvalidated=unvalidated,
    )
