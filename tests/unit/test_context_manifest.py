from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from ahd import __version__
from ahd.core.config import RunConfig, config_sha256
from ahd.core.context import create_run_context, git_state, new_run_id
from ahd.core.manifest import read_manifest, write_manifest
from ahd.errors import ConfigError, InfraError


def test_git_state_clean_then_dirty(git_repo: Path) -> None:
    clean = git_state(git_repo)
    assert re.fullmatch(r"[0-9a-f]{40}", clean.sha)
    assert clean.dirty is False
    (git_repo / "untracked.txt").write_text("x", encoding="utf-8")
    assert git_state(git_repo).dirty is True


def test_git_state_outside_repo_is_infra_error(tmp_path: Path) -> None:
    with pytest.raises(InfraError) as info:
        git_state(tmp_path)
    assert info.value.kind == "git"


def test_new_run_id_shape() -> None:
    run_id = new_run_id(datetime(2026, 9, 4, 1, 2, 3, tzinfo=UTC), prefix="ping-")
    assert re.fullmatch(r"ping-20260904T010203Z-[0-9a-f]{6}", run_id)


def test_create_run_context_records_git_and_config(git_repo: Path, run_config: RunConfig) -> None:
    ctx = create_run_context(run_config, runs_root=git_repo / "runs", repo_dir=git_repo)
    assert ctx.out_dir.is_dir()
    assert ctx.git_sha == git_state(git_repo).sha
    assert ctx.git_dirty is False
    assert ctx.config_sha256 == config_sha256(run_config)
    assert ctx.seed == run_config.seed
    assert ctx.ahd_version == __version__
    assert ctx.created_at.tzinfo is not None


def test_confirmatory_refuses_dirty_tree(git_repo: Path, run_config: RunConfig) -> None:
    (git_repo / "wip.txt").write_text("x", encoding="utf-8")
    confirmatory = run_config.model_copy(
        update={"kind": "confirmatory", "require_clean_tree": True}
    )
    with pytest.raises(ConfigError, match="dirty"):
        create_run_context(confirmatory, runs_root=git_repo / "runs", repo_dir=git_repo)
    exploratory = create_run_context(run_config, runs_root=git_repo / "runs", repo_dir=git_repo)
    assert exploratory.git_dirty is True


def test_refuses_existing_dir_and_bad_id(git_repo: Path, run_config: RunConfig) -> None:
    create_run_context(run_config, runs_root=git_repo / "runs", run_id="fixed", repo_dir=git_repo)
    with pytest.raises(ConfigError, match="already exists"):
        create_run_context(
            run_config, runs_root=git_repo / "runs", run_id="fixed", repo_dir=git_repo
        )
    with pytest.raises(ConfigError, match="invalid run id"):
        create_run_context(
            run_config, runs_root=git_repo / "runs", run_id="../escape", repo_dir=git_repo
        )


def test_manifest_roundtrip(git_repo: Path, run_config: RunConfig, tmp_path: Path) -> None:
    ctx = create_run_context(run_config, runs_root=git_repo / "runs", repo_dir=git_repo)
    config_path = tmp_path / "c.yaml"
    manifest_path = write_manifest(ctx, run_config, config_path=config_path)
    manifest = read_manifest(manifest_path)
    assert manifest.schema_version == 3
    assert manifest.harness_snapshot_id is None and manifest.run_spec is None
    assert manifest.environment.unshare_available in (True, False)
    assert manifest.web_snapshot_id is None
    assert manifest.git_sha == ctx.git_sha
    assert manifest.git_dirty is False
    assert manifest.config_sha256 == config_sha256(run_config)
    assert manifest.config_path == str(config_path)
    assert manifest.run_id == ctx.run_id
    resolved = yaml.safe_load((ctx.out_dir / "config.resolved.yaml").read_text(encoding="utf-8"))
    assert RunConfig.model_validate(resolved) == run_config
    assert config_sha256(RunConfig.model_validate(resolved)) == manifest.config_sha256


def test_read_manifest_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    with pytest.raises(InfraError) as info:
        read_manifest(path)
    assert info.value.kind == "corrupt_file"
