from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ahd import __version__, cli
from ahd.core.manifest import read_manifest
from ahd.core.trace import read_trace
from ahd.llm.ledger import read_ledger
from ahd.settings import Settings
from tests.conftest import PRICING_YAML, REPO_ROOT, FakeTransport, make_completion


def _write_config(repo: Path, *, kind: str = "exploratory") -> Path:
    shutil.copy(REPO_ROOT / "configs" / "runs" / "example.yaml", repo / "run.yaml")
    text = (repo / "run.yaml").read_text(encoding="utf-8")
    text = text.replace("kind: exploratory", f"kind: {kind}")
    text = text.replace("pricing_path: configs/pricing.yaml", "pricing_path: pricing.yaml")
    (repo / "run.yaml").write_text(text, encoding="utf-8")
    (repo / "pricing.yaml").write_text(PRICING_YAML, encoding="utf-8")
    return repo / "run.yaml"


def test_version(
    capsys: pytest.CaptureFixture[str], git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(git_repo)
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert f"ahd {__version__}" in out
    assert "git " in out


def test_runs_new_creates_manifest_and_trace(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_repo)
    config_path = _write_config(git_repo)
    assert cli.main(["runs", "new", "--config", str(config_path), "--run-id", "r1"]) == 0
    out_dir = Path(capsys.readouterr().out.strip())
    assert out_dir == (git_repo / "runs" / "r1").resolve()
    manifest = read_manifest(out_dir / "manifest.json")
    assert len(manifest.git_sha) == 40
    assert manifest.git_dirty is True  # run.yaml is untracked
    assert (out_dir / "config.resolved.yaml").exists()
    events = read_trace(out_dir / "trace.jsonl", expected_run_id="r1")
    assert [e.kind for e in events] == ["run_created"]
    assert events[0].payload["config_sha256"] == manifest.config_sha256
    log_lines = (out_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log_lines[0])["run_id"] == "r1"


def test_runs_new_confirmatory_refuses_dirty_tree(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_repo)
    config_path = _write_config(git_repo, kind="confirmatory")
    assert cli.main(["runs", "new", "--config", str(config_path)]) == cli.EXIT_USAGE
    assert "dirty" in capsys.readouterr().err
    assert not (git_repo / "runs").exists()


def test_runs_new_missing_config_is_infra_exit(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_repo)
    assert cli.main(["runs", "new", "--config", "missing.yaml"]) == cli.EXIT_INFRA
    assert "missing_file" in capsys.readouterr().err


def test_llm_ping_end_to_end_with_fake_transport(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(git_repo)
    config_path = _write_config(git_repo)
    transport = FakeTransport([make_completion("pong", prompt_tokens=12, completion_tokens=1)])
    monkeypatch.setattr(cli, "make_transport", lambda settings, config: transport)
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings.model_validate({"deepseek_api_key": "x"})
    )
    assert cli.main(["llm", "ping", "--config", str(config_path), "--run-id", "p1"]) == 0
    out = capsys.readouterr().out
    assert "prompt_tokens:      12" in out
    assert "completion_tokens:  1" in out
    assert "usd:" in out and "pricing_tier:" in out
    assert transport.calls[0]["model"] == "deepseek-v4-flash"
    rows = read_ledger(git_repo / "runs" / "p1" / "ledger.jsonl")
    assert len(rows) == 1
    assert rows[0].arm == "ping"
    assert rows[0].pricing_version == "test.1"
    events = read_trace(git_repo / "runs" / "p1" / "trace.jsonl")
    assert [e.kind for e in events] == ["llm_ping_request", "llm_ping_response"]


def test_tasks_list_show_sample(fake_snapshot: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.evobench_fixtures import FAKE_REVISION

    common = ["--snapshot-dir", str(fake_snapshot), "--revision", FAKE_REVISION]
    assert cli.main(["tasks", "list", *common]) == 0
    out = capsys.readouterr().out
    assert "bc-en-0001" in out and "apex-" not in out and "-- 4 tasks" in out
    assert cli.main(["tasks", "list", *common, "--include-excluded", "--domain", "office"]) == 0
    out = capsys.readouterr().out
    assert "apex-" in out and "gdpval-" in out and "bc-en" not in out
    assert cli.main(["tasks", "show", "gdpval-00000000-0000-0000-0000-000000000001", *common]) == 0
    out = capsys.readouterr().out
    assert (
        "source_benchmark: gdpval" in out
        and "rubric_file_judge" in out
        and "Springfield" not in out
    )
    assert cli.main(["tasks", "show", "bc-en-0001", *common, "--show-gold"]) == 0
    assert "Springfield" in capsys.readouterr().out
    assert cli.main(["tasks", "sample", *common, "--n", "2", "--seed", "0"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["tasks", "sample", *common, "--n", "2", "--seed", "0"]) == 0
    assert capsys.readouterr().out == first
    assert "-- 2 of 4 tasks" in first
    assert cli.main(["tasks", "show", "missing", *common]) == cli.EXIT_USAGE
