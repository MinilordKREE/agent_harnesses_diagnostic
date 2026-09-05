"""Live Evo-Bench integration: judge shape under openai 3.x, GDPval scoring, Claw end to end.

Deselected by default; ``make test-integration``. Each test skips when its prerequisite
(key, dataset snapshot, LibreOffice, Claw-Eval checkout) is absent, and prints the measured
token counts so they can replace the estimates in docs/reuse/evobench.md.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import openai
import pytest

from ahd.core.config import JudgeConfig, RetryConfig, load_run_config
from ahd.errors import ConfigError
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import DeepSeekClient, make_openai_transport
from ahd.llm.ledger import Ledger, read_ledger, summarize
from ahd.llm.pricing import load_pricing
from ahd.settings import Settings, load_settings
from ahd.tasks.evobench import EvoBenchLoader, cached_snapshot_dir
from ahd.tasks.judge import AhdJudgeClient, patched_claw_judge
from ahd.tasks.kinds import EVOBENCH_DATASET_ID, EVOBENCH_PINNED_REVISION
from ahd.tasks.models import Artifacts
from ahd.tasks.scorer import Scorer
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

CLAW_REPO = REPO_ROOT / "external" / "claw-eval"


def _settings() -> Settings:
    try:
        return load_settings(REPO_ROOT / ".env")
    except ConfigError:
        pytest.skip("DEEPSEEK_API_KEY not configured")


def _snapshot() -> Path:
    path = cached_snapshot_dir(EVOBENCH_DATASET_ID, EVOBENCH_PINNED_REVISION)
    if path is None:
        pytest.skip("Evo-Bench snapshot not cached; run `ahd tasks fetch`")
    return path


def _judge(settings: Settings, tmp_path: Path, ledger: Ledger) -> AhdJudgeClient:
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    pricing = load_pricing(REPO_ROOT / config.pricing_path)
    provider = DeepSeekClient(
        transport=make_openai_transport(
            api_key=settings.deepseek_api_key, base_url=config.llm.base_url, timeout_s=300
        ),
        ledger=ledger,
        pricing=pricing,
        retry=RetryConfig(max_attempts=3),
        cache=ResponseCache(tmp_path / "cache", provider="deepseek"),
    )
    return AhdJudgeClient(provider, config=JudgeConfig(), api_base=config.llm.base_url, seed=0)


def test_evobench_client_shape_under_openai3(monkeypatch: pytest.MonkeyPatch) -> None:
    """One judge call through Evo-Bench's own client; assert the fields its scorer reads."""
    from evobench.models.client import ModelConfig, OpenAICompatibleClient, usage_to_dict

    settings = _settings()
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    monkeypatch.setenv("AHD_JUDGE_API_KEY", settings.deepseek_api_key.get_secret_value())
    cfg = ModelConfig(
        provider="openai-compatible",
        api_base=config.llm.base_url,
        api_key_env="AHD_JUDGE_API_KEY",
        model="deepseek-v4-pro",
        temperature=0.0,
        max_output_tokens=32,
        timeout_seconds=120,
    )
    response = OpenAICompatibleClient(cfg).create(
        messages=[{"role": "user", "content": "Reply with the single word: pong"}]
    )
    assert openai.__version__.startswith("3.")
    content = response.choices[0].message.content
    assert isinstance(content, str) and content.strip()
    usage = usage_to_dict(response.usage)
    assert usage["prompt_tokens"] > 0 and usage["completion_tokens"] > 0
    assert isinstance(usage.get("cached_tokens", 0), int)
    print(f"\n[judge-shape] openai={openai.__version__} usage={usage}")


def test_gdpval_scoring_text_only_fallback(tmp_path: Path) -> None:
    """Score a synthetic deliverable for a real GDPval task: LibreOffice render + text judge."""
    if shutil.which("soffice") is None and shutil.which("libreoffice") is None:
        pytest.skip("LibreOffice not installed")
    docx = pytest.importorskip("docx")
    from evobench.evaluation.tasks import prepare_task_workspace

    settings = _settings()
    snapshot = _snapshot()
    taskset = EvoBenchLoader(snapshot_dir=snapshot).load("validation").select(sources=["gdpval"])
    task = next(t for t in taskset.tasks if t.resources.asset_files)
    workspace = Path(prepare_task_workspace(task.to_evobench_dict(), tmp_path / "ws"))
    document = docx.Document()
    document.add_heading("Response", level=1)
    document.add_paragraph("This synthetic deliverable exists to exercise the scoring path.")
    document.add_paragraph(task.prompt[:200])
    document.save(workspace / "outputs" / "response.docx")

    ledger = Ledger(tmp_path / "ledger.jsonl", "integration-gdpval")
    scorer = Scorer(
        judge=_judge(settings, tmp_path, ledger), ledger=ledger, arm="integration", seed=0
    )
    score = scorer.score(
        task, Artifacts(workspace=workspace, final_answer="see outputs/response.docx")
    )
    assert score.scorer == "rubric_file_judge"
    detail = score.judge_meta["judge_detail"]
    assert isinstance(detail, dict)
    grading = detail["image_grading"]
    assert isinstance(grading, dict) and grading["used"] is False
    rows = read_ledger(ledger.path)
    judge_rows = [r for r in rows if r.event == "call" and r.arm == "judge"]
    assert judge_rows and judge_rows[-1].unit_id == task.id and judge_rows[-1].usd > 0
    print(
        f"\n[gdpval] task={task.id} score={score.value:.3f} passed={score.passed} "
        f"judge_usage={summarize(rows).model_dump()}"
    )


def _ledger_summary(ledger: Ledger) -> str:
    if not ledger.path.exists():
        return "no judge rows"
    return str(summarize(read_ledger(ledger.path)).model_dump())


def test_claw_task_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One Claw-Eval task through Evo-Bench's own runner in local mode, judged via ahd."""
    pytest.importorskip("claw_eval")
    if not (CLAW_REPO / ".evobench-upstream-commit").is_file():
        pytest.skip("Claw-Eval checkout missing; run `make setup-claw`")
    if shutil.which("unshare") is None:
        pytest.skip("unshare not available")
    from evobench.evaluation import runner as evo_runner

    settings = _settings()
    snapshot = _snapshot()
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    taskset = EvoBenchLoader(snapshot_dir=snapshot).load("validation").select(sources=["claw_eval"])
    ids = taskset.ids()
    task = (
        taskset.by_id("claw-T007zh_todo_management")
        if "claw-T007zh_todo_management" in ids
        else taskset.tasks[0]
    )

    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "name": "ahd-claw-smoke",
                "description": "one task",
                "assets_dir": str(snapshot / "assets" / "gdpval"),
                "validation": [task.to_evobench_dict()],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    harness_dir = tmp_path / "harness"
    shutil.copytree(REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed", harness_dir)
    harness_json = harness_dir / "harness.json"
    harness_cfg = json.loads(harness_json.read_text(encoding="utf-8"))
    harness_cfg["max_steps"] = 40  # bound the smoke run; the paper uses 300
    harness_cfg["rollout_wall_clock_seconds"] = 900
    harness_json.write_text(json.dumps(harness_cfg, indent=2), encoding="utf-8")

    monkeypatch.setenv("AHD_POLICY_API_BASE", config.llm.base_url)
    monkeypatch.setenv("AHD_POLICY_API_KEY", settings.deepseek_api_key.get_secret_value())
    monkeypatch.setenv("EVOBENCH_EXECUTION_MODE", "local")
    monkeypatch.setenv("EVOBENCH_CLAW_REPO", str(CLAW_REPO))
    policy_cfg = tmp_path / "policy.json"
    policy_cfg.write_text(
        json.dumps(
            {
                "provider": "openai-compatible",
                "api_base_env": "AHD_POLICY_API_BASE",
                "api_key_env": "AHD_POLICY_API_KEY",
                "model": "deepseek-v4-flash",
                "temperature": 1.0,
                "reasoning_effort": "low",
                "max_output_tokens": 4096,
                "timeout_seconds": 300,
                "require_api_key": True,
                "context_window_tokens": 256000,
            }
        ),
        encoding="utf-8",
    )
    ledger = Ledger(tmp_path / "ledger.jsonl", "integration-claw")
    judge = _judge(settings, tmp_path, ledger)
    monkeypatch.setattr(evo_runner, "make_judge_client", lambda _cfg: judge)
    with patched_claw_judge(judge):
        result = evo_runner.evaluate_split(
            suite_path=suite_path,
            split="validation",
            policy_harness_dir=harness_dir,
            policy_model_config=policy_cfg,
            output_dir=tmp_path / "eval",
            judge_model_config=policy_cfg,
            rollout_concurrency=1,
            trials=1,
        )
    records = result["tasks"]
    assert len(records) == 1 and records[0]["task_id"] == task.id
    record = records[0]
    assert "score" in record and "passed" in record
    assert record["exit_reason"] not in ("policy_worker_error", "eval_pipeline_error"), record.get(
        "runtime_errors"
    )
    usage = record.get("token_usage") or {}
    print(
        f"\n[claw] task={task.id} exit={record['exit_reason']} steps={record.get('steps')} "
        f"passed={record['passed']} score={record['score']} "
        f"reason={record['score_reason'][:120]!r} "
        f"policy_token_usage={usage} totals={result.get('token_usage_totals')} "
        f"judge_ledger={_ledger_summary(ledger)}"
    )
    assert os.environ.get("EVOBENCH_EXECUTION_MODE") == "local"
