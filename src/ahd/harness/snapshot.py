"""Harness snapshots: an immutable copy of a harness tree with hash, parent, diff, components.

No reference source: written fresh for ahd (see docs/reuse/M2.md). The tree hash is
``sha256_dir`` (adapted from Evo-Bench ``hash_harness``, see ``ahd.core.hashing``); its first
16 hex chars equal Evo-Bench's own ``harness_revision`` for the same tree, recorded as
``evobench_revision`` for cross-reference.

Layout of one snapshot directory ``<store>/<snapshot_id>/``:
``snapshot.json`` (meta), ``tree/`` (the harness), ``components.json`` (resolved manifest),
``diff.patch`` (unified diff against the parent, when there is one).
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_dir
from ahd.core.io import atomic_write_text, read_json
from ahd.errors import ConfigError, InfraError
from ahd.harness.components import ComponentManifest, ResolvedManifest, resolve_spans
from ahd.harness.patch import apply_unified_diff, tree_files, unified_diff_trees

SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_ID_LEN = 12
EVOBENCH_REVISION_LEN = 16
TREE_DIRNAME = "tree"
META_FILENAME = "snapshot.json"
COMPONENTS_FILENAME = "components.json"
DIFF_FILENAME = "diff.patch"
_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"
)

type Provenance = Literal["seed", "proposer", "manual", "instrument"]
"""``instrument``: the M3 replay instrument (``ahd/diagnosis/instrument``), hashed like a snapshot
but never an experimental arm."""


class SnapshotMeta(StrictModel):
    schema_version: Literal[1]
    snapshot_id: str
    sha256: str
    evobench_revision: str
    parent_snapshot_id: str | None
    provenance: Provenance
    created_at: datetime
    source: str
    diff_file: str | None
    file_count: int
    files: tuple[str, ...]


class HarnessSnapshot(StrictModel):
    dir: Path
    meta: SnapshotMeta

    @property
    def snapshot_id(self) -> str:
        return self.meta.snapshot_id

    @property
    def tree(self) -> Path:
        return self.dir / TREE_DIRNAME

    @property
    def components_path(self) -> Path:
        return self.dir / COMPONENTS_FILENAME

    @property
    def diff_path(self) -> Path | None:
        return self.dir / self.meta.diff_file if self.meta.diff_file else None

    def resolved_manifest(self) -> ResolvedManifest:
        try:
            return ResolvedManifest.model_validate(read_json(self.components_path))
        except ValidationError as exc:
            raise InfraError(
                f"corrupt components.json in {self.dir}:\n{exc}", kind="corrupt_file"
            ) from exc

    def diff(self) -> str:
        path = self.diff_path
        if path is None:
            return ""
        return path.read_text(encoding="utf-8")


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, snapshot_id: str) -> Path:
        return self.root / snapshot_id

    def exists(self, snapshot_id: str) -> bool:
        return (self.path(snapshot_id) / META_FILENAME).is_file()

    def ids(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(sorted(p.name for p in self.root.iterdir() if (p / META_FILENAME).is_file()))

    def load(self, snapshot_id: str) -> HarnessSnapshot:
        directory = self.path(snapshot_id)
        if not (directory / META_FILENAME).is_file():
            raise InfraError(
                f"harness snapshot {snapshot_id!r} not found under {self.root}",
                kind="missing_snapshot",
            )
        try:
            meta = SnapshotMeta.model_validate(read_json(directory / META_FILENAME))
        except ValidationError as exc:
            raise InfraError(
                f"corrupt {META_FILENAME} in {directory}:\n{exc}", kind="corrupt_file"
            ) from exc
        snapshot = HarnessSnapshot(dir=directory, meta=meta)
        actual = sha256_dir(snapshot.tree)
        if actual != meta.sha256:
            raise InfraError(
                f"snapshot {snapshot_id} tree hash {actual[:12]} differs from recorded "
                f"{meta.sha256[:12]}; the tree was edited in place",
                kind="snapshot_tampered",
            )
        return snapshot


def _write_snapshot(
    staged_tree: Path,
    *,
    store: SnapshotStore,
    manifest: ComponentManifest,
    provenance: Provenance,
    parent: HarnessSnapshot | None,
    source: str,
    diff: str | None,
) -> HarnessSnapshot:
    digest = sha256_dir(staged_tree)
    snapshot_id = digest[:SNAPSHOT_ID_LEN]
    if store.exists(snapshot_id):
        existing = store.load(snapshot_id)
        if existing.meta.sha256 != digest:  # pragma: no cover - 48-bit collision
            raise ConfigError(f"snapshot id collision for {snapshot_id}")
        return existing
    store.root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_id}.", dir=store.root))
    try:
        shutil.copytree(staged_tree, staging / TREE_DIRNAME, ignore=_IGNORE)
        resolved = resolve_spans(manifest, staging / TREE_DIRNAME)
        atomic_write_text(staging / COMPONENTS_FILENAME, resolved.model_dump_json(indent=2) + "\n")
        diff_file: str | None = None
        if parent is not None:
            text = (
                diff
                if diff is not None
                else unified_diff_trees(parent.tree, staging / TREE_DIRNAME)
            )
            atomic_write_text(staging / DIFF_FILENAME, text)
            diff_file = DIFF_FILENAME
        files = tree_files(staging / TREE_DIRNAME)
        meta = SnapshotMeta(
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            snapshot_id=snapshot_id,
            sha256=digest,
            evobench_revision=digest[:EVOBENCH_REVISION_LEN],
            parent_snapshot_id=parent.snapshot_id if parent is not None else None,
            provenance=provenance,
            created_at=datetime.now(UTC),
            source=source,
            diff_file=diff_file,
            file_count=len(files),
            files=files,
        )
        atomic_write_text(staging / META_FILENAME, meta.model_dump_json(indent=2) + "\n")
        staging.rename(store.path(snapshot_id))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return HarnessSnapshot(dir=store.path(snapshot_id), meta=meta)


def snapshot_from_dir(
    src: Path,
    *,
    store: SnapshotStore,
    manifest: ComponentManifest,
    provenance: Provenance,
    parent: HarnessSnapshot | None = None,
    source: str | None = None,
) -> HarnessSnapshot:
    """Copy ``src`` verbatim into the store (caches and VCS dirs excluded) and hash it."""
    if not (src / "harness.py").is_file():
        raise ConfigError(f"{src} is not a harness directory (no harness.py)")
    return _write_snapshot(
        src,
        store=store,
        manifest=manifest,
        provenance=provenance,
        parent=parent,
        source=source or str(src.resolve()),
        diff=None,
    )


def apply_patch(
    snapshot: HarnessSnapshot,
    diff: str,
    *,
    store: SnapshotStore,
    manifest: ComponentManifest,
    provenance: Provenance = "proposer",
) -> HarnessSnapshot:
    """A new snapshot = ``snapshot`` + ``diff``; the parent is never modified."""
    with tempfile.TemporaryDirectory(prefix="ahd-patch-") as scratch:
        work = Path(scratch) / TREE_DIRNAME
        shutil.copytree(snapshot.tree, work, ignore=_IGNORE)
        apply_unified_diff(work, diff)
        return _write_snapshot(
            work,
            store=store,
            manifest=manifest,
            provenance=provenance,
            parent=snapshot,
            source=f"patch of {snapshot.snapshot_id}",
            diff=diff,
        )


def diff_snapshots(old: HarnessSnapshot, new: HarnessSnapshot) -> str:
    return unified_diff_trees(old.tree, new.tree)


def copy_snapshot(snapshot: HarnessSnapshot, dest: SnapshotStore) -> HarnessSnapshot:
    """Copy a whole snapshot directory into another store (a run's ``harness/``)."""
    if dest.exists(snapshot.snapshot_id):
        return dest.load(snapshot.snapshot_id)
    dest.root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot.dir, dest.path(snapshot.snapshot_id))
    return dest.load(snapshot.snapshot_id)
