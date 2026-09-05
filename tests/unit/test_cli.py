from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

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


def test_harness_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
    if not (seed / "harness.py").is_file():
        pytest.skip("submodule not checked out")
    store = tmp_path / "store"
    assert cli.main(["harness", "snapshot", "--from", str(seed), "--store", str(store)]) == 0
    out = capsys.readouterr().out
    snapshot_id = next(
        line.split()[1] for line in out.splitlines() if line.startswith("snapshot_id:")
    )
    assert "valid:              True" in out
    assert cli.main(["harness", "components", snapshot_id, "--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "model_client" in out and "0 unresolved" in out
    import shutil

    edited = tmp_path / "edited"
    shutil.copytree(seed, edited, ignore=shutil.ignore_patterns("__pycache__"))
    (edited / "system_prompt.md").write_text("changed prompt\n", encoding="utf-8")
    assert (
        cli.main(
            [
                "harness",
                "snapshot",
                "--from",
                str(edited),
                "--store",
                str(store),
                "--provenance",
                "manual",
            ]
        )
        == 0
    )
    other = next(
        line.split()[1]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("snapshot_id:")
    )
    assert cli.main(["harness", "diff", snapshot_id, other, "--store", str(store)]) == 0
    out = capsys.readouterr().out
    assert "+changed prompt" in out and "-> system_prompt" in out


def test_run_summarize_reads_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "runs" / "r9").mkdir(parents=True)
    (tmp_path / "runs" / "r9" / "summary.json").write_text('{"run_id": "r9"}\n', encoding="utf-8")
    assert cli.main(["run", "summarize", "r9", "--runs-root", str(tmp_path / "runs")]) == 0
    assert '"run_id": "r9"' in capsys.readouterr().out
    assert (
        cli.main(["run", "summarize", "nope", "--runs-root", str(tmp_path / "runs")])
        == cli.EXIT_INFRA
    )


def test_run_start_and_resume_cli(
    git_repo: Path,
    fake_snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ahd.runner import runner as runner_module
    from ahd.runner.worker import WorkerOutcome
    from ahd.tasks import scorer as scorer_module
    from ahd.tasks.evobench import EvoBenchLoader
    from tests.evobench_fixtures import FAKE_REVISION
    from tests.runner_fixtures import fake_trajectory, write_rollout_files

    seed = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
    if not (seed / "harness.py").is_file():
        pytest.skip("submodule not checked out")
    monkeypatch.chdir(git_repo)
    config_path = _write_config(git_repo)
    text = config_path.read_text(encoding="utf-8").replace(
        "  sources: [browsecomp, hle, gdpval, claw_eval]   # apex is loaded but excluded\n",
        "  sources: [browsecomp]\n",
    )
    config_path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "EvoBenchLoader",
        lambda **kw: EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot),
    )
    monkeypatch.setattr(cli, "make_transport", lambda settings, config: FakeTransport([]))
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings.model_validate({"deepseek_api_key": "x"})
    )
    monkeypatch.setattr(
        scorer_module,
        "score_task",
        lambda task, workspace, final_answer, judge_client=None: {
            "passed": True,
            "score": 1.0,
            "reason": "llm_as_judge: ok",
        },
    )
    calls: list[str] = []

    def fake_invoke(
        *,
        request: dict[str, Any],
        rollout_dir: Path,
        env: dict[str, str],
        timeout_s: int,
        python: str | None = None,
    ) -> WorkerOutcome:
        calls.append(rollout_dir.name)
        if len(calls) == 2:
            raise RuntimeError("boom")  # second lane crashes on the first run
        trajectory, metadata = fake_trajectory(commands=["ls"], final_answer="42")
        write_rollout_files(rollout_dir, trajectory, metadata)
        return WorkerOutcome(
            ok=True,
            rollout={},
            error=None,
            error_type=None,
            returncode=0,
            timed_out=False,
            elapsed_seconds=1.0,
            stdout_tail="",
            stderr_tail="",
        )

    monkeypatch.setattr(runner_module, "invoke_worker", fake_invoke)
    manifest_path = REPO_ROOT / "configs" / "harness" / "seed_components.yaml"
    assert (
        cli.main(
            [
                "run",
                "--config",
                str(config_path),
                "--harness",
                str(seed),
                "--manifest",
                str(manifest_path),
                "--tasks",
                "bc-en-0001",
                "--replicates",
                "2",
                "--workers",
                "1",
                "--run-id",
                "cli-run",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "failures: 1" in out
    manifest = json.loads((git_repo / "runs" / "cli-run" / "manifest.json").read_text())
    assert manifest["run_spec"]["workers"] == 1 and manifest["harness_snapshot_id"]
    assert (
        cli.main(["run", "resume", "cli-run", "--config", str(config_path), "--workers", "2"]) == 0
    )
    assert "failures: 0" in capsys.readouterr().out
    assert calls == ["r1", "r2", "r2"]
    summary = json.loads((git_repo / "runs" / "cli-run" / "summary.json").read_text())
    assert summary["tasks"][0]["pass_hat_k"] is True
