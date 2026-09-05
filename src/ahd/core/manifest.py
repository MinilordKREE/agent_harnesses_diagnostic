"""``manifest.json`` and ``config.resolved.yaml`` for a run directory.

No reference source: written fresh for ahd (see docs/reuse/M0.md). The field set is the
union of Evo-Bench ``run_manifest.json`` (run_id, created_at, seed, framework_version),
VeRO ``SessionManifest`` (schema_version, config digest) and AutoSaddler ``manifest.json``
(schema_version, resolved config copy); no code was copied.

Schema v2 (M1): added ``environment`` (tool and dependency versions, see
:mod:`ahd.core.environment`) and the reserved ``web_snapshot_id``.
Schema v3 (M2): added ``harness_snapshot_id`` and ``run_spec`` (mode, replicates, frozen
budget, policy model, mock_today); ``web_snapshot_id`` stays reserved until M2b.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

import yaml
from pydantic import ValidationError

from ahd.core.config import RunConfig, StrictModel
from ahd.core.context import RunContext
from ahd.core.environment import EnvironmentInfo, probe_environment
from ahd.core.hashing import JsonValue
from ahd.core.io import atomic_write_text, read_json
from ahd.errors import InfraError

MANIFEST_SCHEMA_VERSION: Final = 3
MANIFEST_FILENAME = "manifest.json"
RESOLVED_CONFIG_FILENAME = "config.resolved.yaml"


class Manifest(StrictModel):
    schema_version: Literal[3]
    run_id: str
    created_at: datetime
    seed: int
    config_sha256: str
    config_schema_version: int
    config_path: str | None
    git_sha: str
    git_dirty: bool
    ahd_version: str
    python_version: str
    platform: str
    out_dir: str
    environment: EnvironmentInfo
    web_snapshot_id: str | None = None
    """Reserved: id of the frozen web snapshot (Serper JSON and fetched pages) shared by an
    experiment family. Set by M2b's record/replay proxy; ``None`` means no web freezing."""
    harness_snapshot_id: str | None = None
    run_spec: dict[str, JsonValue] | None = None
    """The ``RunSpec`` the run executed (mode, replicate ids, budget, policy model, mock_today)."""


def build_manifest(
    ctx: RunContext,
    *,
    config_path: Path | None = None,
    environment: EnvironmentInfo | None = None,
    web_snapshot_id: str | None = None,
    harness_snapshot_id: str | None = None,
    run_spec: dict[str, JsonValue] | None = None,
) -> Manifest:
    env = environment or probe_environment(repo_dir=Path.cwd())
    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        run_id=ctx.run_id,
        created_at=ctx.created_at,
        seed=ctx.seed,
        config_sha256=ctx.config_sha256,
        config_schema_version=ctx.config_schema_version,
        config_path=str(config_path) if config_path is not None else None,
        git_sha=ctx.git_sha,
        git_dirty=ctx.git_dirty,
        ahd_version=ctx.ahd_version,
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        out_dir=str(ctx.out_dir),
        environment=env,
        web_snapshot_id=web_snapshot_id,
        harness_snapshot_id=harness_snapshot_id,
        run_spec=run_spec,
    )


def write_manifest(
    ctx: RunContext,
    config: RunConfig,
    *,
    config_path: Path | None = None,
    environment: EnvironmentInfo | None = None,
    harness_snapshot_id: str | None = None,
    run_spec: dict[str, JsonValue] | None = None,
) -> Path:
    """Write ``manifest.json`` and ``config.resolved.yaml`` atomically; return the manifest path."""
    manifest = build_manifest(
        ctx,
        config_path=config_path,
        environment=environment,
        harness_snapshot_id=harness_snapshot_id,
        run_spec=run_spec,
    )
    manifest_path = ctx.out_dir / MANIFEST_FILENAME
    atomic_write_text(manifest_path, manifest.model_dump_json(indent=2) + "\n")
    resolved = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True, allow_unicode=True)
    atomic_write_text(ctx.out_dir / RESOLVED_CONFIG_FILENAME, resolved)
    return manifest_path


def read_manifest(path: Path) -> Manifest:
    raw = read_json(path)
    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise InfraError(f"invalid manifest {path}:\n{exc}", kind="corrupt_file") from exc
