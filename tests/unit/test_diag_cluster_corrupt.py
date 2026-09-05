"""Clustering determinism; corruption determinism, exclusion, distance covariates, fallback."""

from __future__ import annotations

import pytest

from ahd.diagnosis import corrupt
from ahd.diagnosis.cluster import ClusterSet, cluster
from ahd.diagnosis.schema import Diagnosis
from ahd.harness.components import ComponentManifest
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import diagnosis


@pytest.fixture(scope="module")
def manifest() -> ComponentManifest:
    return ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")


def _diagnoses() -> list[Diagnosis]:
    return [
        diagnosis(
            task_id="a",
            replicate="r1",
            component="system_prompt",
            cause="premature_termination",
            severity="medium",
            step=4,
        ),
        diagnosis(
            task_id="a",
            replicate="r2",
            component="system_prompt",
            cause="premature_termination",
            severity="high",
            step=6,
        ),
        diagnosis(
            task_id="b",
            replicate="r1",
            component="verifier",
            cause="missing_verification",
            severity="low",
            step=2,
            mechanism="No check of the answer.",
            fix="Verify the answer against the output.",
        ),
        diagnosis(
            task_id="c",
            replicate="r1",
            component="budget",
            cause="budget_exhaustion",
            severity="critical",
            step=9,
            mechanism="Ran out of steps.",
            fix="Raise the step limit.",
            validated=False,
        ),
    ]


def test_cluster_grouping_representative_and_hash() -> None:
    cs = cluster(_diagnoses())
    assert len(cs.clusters) == 3
    by_component = {c.component: c for c in cs.clusters}
    sp = by_component["system_prompt"]
    assert (
        sp.members == ("a/r1/1", "a/r2/1") and sp.representative == "a/r2/1"
    )  # highest severity first
    assert sp.oracle_validated_members == 2
    assert cs.unvalidated == ("c/r1/1",)
    reordered = cluster(list(reversed(_diagnoses())))
    assert reordered.membership_sha256 == cs.membership_sha256
    assert [c.id for c in reordered.clusters] == [c.id for c in cs.clusters]


def _activity(cs: ClusterSet, manifest: ComponentManifest) -> dict[str, dict[str, set[int]]]:
    return {
        c.id: {spec.id: {1, 2, 3, 4, 5, 6} for spec in manifest.components} for c in cs.clusters
    }


def test_where_corruption_near_far_exclusion_and_determinism(manifest: ComponentManifest) -> None:
    cs = cluster(_diagnoses())
    activity = _activity(cs, manifest)
    sufficient: dict[str, set[int]] = {
        c.id: {s for s in (c.diagnosis_reference.where.step,) if s is not None} for c in cs.clusters
    }
    near = corrupt.assign(
        cs.clusters,
        arm="corrupt_where_near",
        seed=1,
        manifest=manifest,
        activity=activity,
        sufficient=sufficient,
    )
    again = corrupt.assign(
        cs.clusters,
        arm="corrupt_where_near",
        seed=1,
        manifest=manifest,
        activity=activity,
        sufficient=sufficient,
    )
    assert near == again
    for a, c in zip(near.assignments, cs.clusters, strict=True):
        assert a.where is not None and a.where.component != c.diagnosis_reference.where.component
        assert a.where.step not in sufficient[c.id]
        meta = a.where.distance_meta
        assert meta is not None and meta.requested_tier == "near"
        if c.component == "verifier":
            assert meta.distance_fallback and not meta.same_layer  # verifier is alone in its layer
        else:
            assert meta.same_layer and not meta.distance_fallback
    far = corrupt.assign(
        cs.clusters,
        arm="corrupt_where_far",
        seed=1,
        manifest=manifest,
        activity=activity,
        sufficient=sufficient,
    )
    for a in far.assignments:
        assert a.where is not None and a.where.distance_meta is not None
        assert not a.where.distance_meta.same_layer and not a.where.distance_meta.distance_fallback
    other_seed = corrupt.assign(
        cs.clusters,
        arm="corrupt_where_far",
        seed=2,
        manifest=manifest,
        activity=activity,
        sufficient=sufficient,
    )
    assert other_seed != far


def test_where_corruption_needs_an_active_step(manifest: ComponentManifest) -> None:
    cs = cluster(_diagnoses()[:1])
    activity = {cs.clusters[0].id: {spec.id: {4} for spec in manifest.components}}
    table = corrupt.assign(
        cs.clusters,
        arm="corrupt_where_far",
        seed=0,
        manifest=manifest,
        activity=activity,
        sufficient={cs.clusters[0].id: {4}},
    )
    assert table.assignments[0].impossible == "no decoy component active outside the sufficient set"


def test_why_how_all_take_another_cluster_or_are_impossible(manifest: ComponentManifest) -> None:
    cs = cluster(_diagnoses())
    for arm in ("corrupt_why", "corrupt_how", "shuffled"):
        table = corrupt.assign(
            cs.clusters, arm=arm, seed=3, manifest=manifest, activity={}, sufficient={}
        )
        for a in table.assignments:
            assert (
                a.origin_cluster is not None
                and a.origin_cluster != a.cluster_id
                and a.impossible is None
            )
            applied = corrupt.apply(
                next(c for c in cs.clusters if c.id == a.cluster_id).diagnosis_reference,
                a,
                cs.clusters,
            )
            origin = next(c for c in cs.clusters if c.id == a.origin_cluster).diagnosis_reference
            assert applied.provenance.origin_cluster == a.origin_cluster
            if arm == "corrupt_why":
                assert (
                    applied.why == origin.why
                    and applied.how != origin.how
                    and applied.corruption == "why"
                )
            elif arm == "corrupt_how":
                assert applied.how == origin.how and applied.why != origin.why
            else:
                assert (
                    applied.where == origin.where
                    and applied.why == origin.why
                    and applied.source == "shuffled"
                )
                assert applied.provenance.task_id == a.cluster_id.replace(
                    a.cluster_id, applied.provenance.task_id
                )
    single = cluster(_diagnoses()[:1])
    table = corrupt.assign(
        single.clusters, arm="corrupt_why", seed=0, manifest=manifest, activity={}, sufficient={}
    )
    assert table.assignments[0].impossible is not None
    with pytest.raises(ValueError, match="impossible"):
        corrupt.apply(single.clusters[0].diagnosis_reference, table.assignments[0], single.clusters)


def test_reference_and_system_arms_are_untouched(manifest: ComponentManifest) -> None:
    cs = cluster(_diagnoses())
    for arm in ("reference", "system"):
        table = corrupt.assign(
            cs.clusters, arm=arm, seed=0, manifest=manifest, activity={}, sufficient={}
        )
        assert all(a.corruption == "none" and a.where is None for a in table.assignments)
        d = cs.clusters[0].diagnosis_reference
        assert corrupt.apply(d, table.assignments[0], cs.clusters) == d
    with pytest.raises(ValueError, match="unknown arm"):
        corrupt.assign(
            cs.clusters, arm="bogus", seed=0, manifest=manifest, activity={}, sufficient={}
        )


def test_distance_covariates(manifest: ComponentManifest) -> None:
    assert corrupt.distance(manifest, "system_prompt", "task_prompt") == (True, False)
    assert corrupt.distance(manifest, "loop", "error_handling") == (True, True)
    assert corrupt.distance(manifest, "budget", "verifier") == (False, False)
