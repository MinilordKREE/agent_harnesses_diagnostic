from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ahd.core.config import Budget
from ahd.harness.validate import SnapshotInvalidError, require_valid, validate_tree
from tests.conftest import REPO_ROOT

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
pytestmark = pytest.mark.skipif(
    not (SEED / "harness.py").is_file(), reason="submodule not checked out"
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    dest = tmp_path / "tree"
    shutil.copytree(SEED, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def _set_budget(tree: Path, **values: object) -> None:
    config = json.loads((tree / "harness.json").read_text())
    config.update(values)
    (tree / "harness.json").write_text(json.dumps(config, indent=2))


def test_seed_is_valid(tree: Path) -> None:
    report = require_valid(tree)
    assert report.ok
    assert sorted(report.tools_registered) == ["finish", "run_shell_command"]
    assert report.external_imports == ()


def test_budget_drift_is_refused(tree: Path) -> None:
    _set_budget(tree, max_steps=301)
    report = validate_tree(tree)
    assert not report.ok and any("max_steps is 301" in p for p in report.problems)
    with pytest.raises(SnapshotInvalidError):
        require_valid(tree)
    assert validate_tree(
        tree, budget=Budget(max_steps=301)
    ).ok  # the runner's budget is the reference


def test_tools_must_match_registry(tree: Path) -> None:
    _set_budget(tree, tools=["run_shell_command", "finish", "web_search"])
    report = validate_tree(tree)
    assert any("differs from TOOL_SCHEMAS" in p for p in report.problems)
    init = tree / "tools" / "__init__.py"
    init.write_text(
        init.read_text().replace(
            "TOOL_SCHEMAS = [RUN_SHELL_TOOL, FINISH_TOOL]", "TOOL_SCHEMAS = list(_dynamic())"
        )
    )
    assert any("list literal" in p for p in validate_tree(tree).problems)


def test_imports_are_restricted(tree: Path) -> None:
    (tree / "agent" / "memory.py").write_text(
        "import json\nfrom tools import finish_result\n", encoding="utf-8"
    )
    assert validate_tree(tree).ok  # stdlib + local are fine
    (tree / "agent" / "web.py").write_text("import requests\n", encoding="utf-8")
    report = validate_tree(tree)
    assert report.external_imports == ("requests",)
    assert any("disallowed import requests" in p for p in report.problems)
    (tree / "agent" / "web.py").write_text(
        "from evobench.evaluation import scorer\n", encoding="utf-8"
    )
    assert "evobench.evaluation.scorer" in validate_tree(tree).external_imports
