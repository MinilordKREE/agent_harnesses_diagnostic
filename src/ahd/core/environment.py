"""Environment facts recorded in every manifest so a run can be reproduced later.

No reference source: written fresh for ahd (see docs/reuse/M1.md). No surveyed repo records
its tool versions; Evo-Bench's GDPval judge depends on the local LibreOffice build and its
local mode on ``unshare``, so both are captured here.
"""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ahd.core.config import StrictModel

EVOBENCH_SUBMODULE = Path("third_party/evo-bench")
CLAW_EVAL_DEFAULT = Path("external/claw-eval")
CLAW_EVAL_MARKER = ".evobench-upstream-commit"


class EnvironmentInfo(StrictModel):
    python_version: str
    platform: str
    openai_version: str
    pydantic_version: str
    evobench_submodule_sha: str | None
    evobench_dataset_id: str | None
    evobench_snapshot_sha: str | None
    claw_eval_commit: str | None
    claw_eval_path: str | None
    libreoffice_version: str | None
    unshare_available: bool


def _run(args: list[str], *, cwd: Path | None = None) -> str | None:
    try:
        completed = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, check=False, timeout=60
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def libreoffice_version() -> str | None:
    """First line of ``soffice --version`` (``libreoffice`` as fallback), or ``None``."""
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if binary is None:
        return None
    out = _run([binary, "--headless", "--version"])
    return out.splitlines()[0].strip() if out else None


def submodule_sha(repo_dir: Path) -> str | None:
    path = repo_dir / EVOBENCH_SUBMODULE
    if not path.is_dir():
        return None
    return _run(["git", "rev-parse", "HEAD"], cwd=path)


def claw_eval_checkout(
    repo_dir: Path, explicit: Path | None = None
) -> tuple[str | None, str | None]:
    """(commit, path) of the Claw-Eval checkout prepared by Evo-Bench's setup script."""
    candidate = explicit or (repo_dir / CLAW_EVAL_DEFAULT)
    marker = candidate / CLAW_EVAL_MARKER
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip(), str(candidate.resolve())
    return None, None


def probe_environment(
    *,
    repo_dir: Path,
    evobench_dataset_id: str | None = None,
    evobench_snapshot_sha: str | None = None,
    claw_eval_dir: Path | None = None,
) -> EnvironmentInfo:
    claw_commit, claw_path = claw_eval_checkout(repo_dir, claw_eval_dir)
    return EnvironmentInfo(
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        openai_version=importlib.metadata.version("openai"),
        pydantic_version=importlib.metadata.version("pydantic"),
        evobench_submodule_sha=submodule_sha(repo_dir),
        evobench_dataset_id=evobench_dataset_id,
        evobench_snapshot_sha=evobench_snapshot_sha,
        claw_eval_commit=claw_commit,
        claw_eval_path=claw_path,
        libreoffice_version=libreoffice_version(),
        unshare_available=shutil.which("unshare") is not None,
    )
