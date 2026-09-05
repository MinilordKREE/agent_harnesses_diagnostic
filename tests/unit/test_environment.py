from __future__ import annotations

from pathlib import Path

from ahd.core.environment import EnvironmentInfo, probe_environment
from tests.conftest import REPO_ROOT


def test_probe_reports_versions_and_tools(tmp_path: Path) -> None:
    info = probe_environment(
        repo_dir=tmp_path, evobench_dataset_id="d", evobench_snapshot_sha="s" * 40
    )
    assert isinstance(info, EnvironmentInfo)
    assert info.openai_version.split(".")[0].isdigit()
    assert info.evobench_submodule_sha is None  # no submodule under tmp_path
    assert info.claw_eval_commit is None
    assert info.evobench_snapshot_sha == "s" * 40
    assert (
        info.libreoffice_version is None
        or "LibreOffice" in info.libreoffice_version
        or info.libreoffice_version
    )
    assert isinstance(info.unshare_available, bool)


def test_probe_finds_submodule_and_claw_marker(tmp_path: Path) -> None:
    claw = tmp_path / "external" / "claw-eval"
    claw.mkdir(parents=True)
    (claw / ".evobench-upstream-commit").write_text("d3f02d49\n", encoding="utf-8")
    info = probe_environment(repo_dir=tmp_path)
    assert info.claw_eval_commit == "d3f02d49"
    assert info.claw_eval_path == str(claw.resolve())
    real = probe_environment(repo_dir=REPO_ROOT)
    if (REPO_ROOT / "third_party" / "evo-bench" / ".git").exists() or (
        REPO_ROOT / "third_party" / "evo-bench" / "evobench"
    ).exists():
        assert real.evobench_submodule_sha is None or len(real.evobench_submodule_sha) == 40
