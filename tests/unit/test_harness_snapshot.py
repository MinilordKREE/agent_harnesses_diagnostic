from __future__ import annotations

from pathlib import Path

import pytest

from ahd.core.hashing import sha256_dir
from ahd.errors import InfraError
from ahd.harness.components import ComponentManifest
from ahd.harness.patch import PatchApplyError
from ahd.harness.snapshot import (
    SnapshotStore,
    apply_patch,
    copy_snapshot,
    diff_snapshots,
    snapshot_from_dir,
)
from tests.conftest import REPO_ROOT

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
MANIFEST = REPO_ROOT / "configs" / "harness" / "seed_components.yaml"

pytestmark = pytest.mark.skipif(
    not (SEED / "harness.py").is_file(), reason="submodule not checked out"
)


@pytest.fixture
def manifest() -> ComponentManifest:
    return ComponentManifest.load(MANIFEST)


def test_seed_snapshot_hashes_like_evobench(tmp_path: Path, manifest: ComponentManifest) -> None:
    from evobench.evaluation.runner import hash_harness

    store = SnapshotStore(tmp_path / "store")
    snapshot = snapshot_from_dir(SEED, store=store, manifest=manifest, provenance="seed")
    assert len(snapshot.snapshot_id) == 12
    assert snapshot.meta.sha256 == sha256_dir(snapshot.tree)
    assert snapshot.meta.evobench_revision == hash_harness(snapshot.tree)
    assert snapshot.meta.provenance == "seed" and snapshot.meta.parent_snapshot_id is None
    assert "harness.py" in snapshot.meta.files and "agent/loop.py" in snapshot.meta.files
    assert not any("__pycache__" in f for f in snapshot.meta.files)
    again = snapshot_from_dir(SEED, store=store, manifest=manifest, provenance="seed")
    assert again.snapshot_id == snapshot.snapshot_id  # idempotent
    assert store.ids() == (snapshot.snapshot_id,)
    resolved = snapshot.resolved_manifest()
    assert resolved.tree_sha256 == snapshot.meta.sha256
    assert resolved.unresolved() == ()


def test_apply_patch_creates_child_with_diff(tmp_path: Path, manifest: ComponentManifest) -> None:
    store = SnapshotStore(tmp_path / "store")
    seed = snapshot_from_dir(SEED, store=store, manifest=manifest, provenance="seed")
    prompt = (seed.tree / "system_prompt.md").read_text().splitlines()
    diff = (
        "--- a/system_prompt.md\n+++ b/system_prompt.md\n@@ -1 +1,2 @@\n"
        f" {prompt[0]}\n+Always think twice before running a command.\n"
    )
    child = apply_patch(seed, diff, store=store, manifest=manifest)
    assert child.snapshot_id != seed.snapshot_id
    assert child.meta.parent_snapshot_id == seed.snapshot_id
    assert child.meta.provenance == "proposer"
    assert child.diff() == diff
    assert "think twice" in (child.tree / "system_prompt.md").read_text()
    assert "think twice" not in (seed.tree / "system_prompt.md").read_text()
    regenerated = diff_snapshots(seed, child)
    assert "+Always think twice" in regenerated
    with pytest.raises(PatchApplyError):
        apply_patch(
            seed, diff.replace(prompt[0], "not the real first line"), store=store, manifest=manifest
        )
    assert store.load(child.snapshot_id).meta.sha256 == child.meta.sha256


def test_store_detects_in_place_edits(tmp_path: Path, manifest: ComponentManifest) -> None:
    store = SnapshotStore(tmp_path / "store")
    seed = snapshot_from_dir(SEED, store=store, manifest=manifest, provenance="seed")
    (seed.tree / "system_prompt.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(InfraError) as info:
        store.load(seed.snapshot_id)
    assert info.value.kind == "snapshot_tampered"
    with pytest.raises(InfraError) as missing:
        store.load("000000000000")
    assert missing.value.kind == "missing_snapshot"


def test_copy_snapshot_into_run(tmp_path: Path, manifest: ComponentManifest) -> None:
    store = SnapshotStore(tmp_path / "store")
    seed = snapshot_from_dir(SEED, store=store, manifest=manifest, provenance="seed")
    run_store = SnapshotStore(tmp_path / "run" / "harness")
    copied = copy_snapshot(seed, run_store)
    assert copied.snapshot_id == seed.snapshot_id and copied.dir != seed.dir
    assert (copied.dir / "components.json").is_file()
