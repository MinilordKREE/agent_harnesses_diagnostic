"""Live runner tests: one Claw task through the seed harness under the PAPER policy config.

Deselected by default; ``make test-integration``. These make real DeepSeek calls (policy at
``reasoning_effort: max``, judge V4 Pro) and can take up to the frozen wall clock per rollout.
They print per-rollout cost for the review packet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from ahd.core.config import load_run_config
from ahd.core.context import create_run_context
from ahd.core.environment import probe_environment
from ahd.core.manifest import write_manifest
from ahd.core.trace import TraceWriter, read_trace
from ahd.errors import ConfigError
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import SnapshotStore, snapshot_from_dir
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import DeepSeekClient, make_openai_transport
from ahd.llm.ledger import Ledger, read_ledger, summarize
from ahd.llm.pricing import load_pricing
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.settings import load_settings
from ahd.tasks.evobench import EvoBenchLoader, cached_snapshot_dir
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.kinds import EVOBENCH_DATASET_ID, EVOBENCH_PINNED_REVISION
from ahd.tasks.scorer import Scorer
from tests.conftest import REPO_ROOT

pytestmark = pytest.mark.integration

SEED = REPO_ROOT / "third_party" / "evo-bench" / "policy_harness_seed"
CLAW_REPO = REPO_ROOT / "external" / "claw-eval"
TASK_ID = "claw-T007zh_todo_management"


def _prereqs() -> None:
    pytest.importorskip("claw_eval")
    if not (CLAW_REPO / ".evobench-upstream-commit").is_file():
        pytest.skip("Claw-Eval checkout missing; run `make setup-claw`")
    if cached_snapshot_dir(EVOBENCH_DATASET_ID, EVOBENCH_PINNED_REVISION) is None:
        pytest.skip("Evo-Bench snapshot not cached")
    try:
        load_settings(REPO_ROOT / ".env")
    except ConfigError:
        pytest.skip("DEEPSEEK_API_KEY not configured")


def _run(
    tmp_path: Path,
    *,
    mode: Literal["normal", "reference"],
    replicates: int,
    run_id: str,
    max_attempts: int = 5,
    effort: str | None = None,
    task_ids: tuple[str, ...] = (TASK_ID,),
    workers: int = 1,
) -> tuple[Any, Ledger, Any]:
    """``effort`` overrides the paper's ``reasoning_effort: max`` (and caps output tokens) for
    cheaper runs that are meant to fail; the paper config is asserted only when it is None."""
    settings = load_settings(REPO_ROOT / ".env")
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml").model_copy(
        update={"runs_root": tmp_path / "runs"}
    )
    pricing = load_pricing(REPO_ROOT / config.pricing_path)
    manifest = ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")
    snapshot = snapshot_from_dir(
        SEED, store=SnapshotStore(tmp_path / "store"), manifest=manifest, provenance="seed"
    )
    taskset = EvoBenchLoader().load("validation")
    tasks = [taskset.by_id(task_id) for task_id in task_ids]
    spec = RunSpec.from_config(
        config,
        harness_snapshot_id=snapshot.snapshot_id,
        task_ids=task_ids,
        mode=mode,
        replicates=replicates,
        workers=workers,
    ).model_copy(update={"reference_max_attempts": max_attempts, "mock_today": "2026-03-02"})
    if effort is not None:
        policy = spec.policy.model_copy(
            update={"reasoning_effort": effort, "max_output_tokens": 8192}
        )
        spec = spec.model_copy(update={"policy": policy})
    else:
        assert spec.policy.reasoning_effort == "max" and spec.policy.temperature == 1.0
    ctx = create_run_context(config, runs_root=config.runs_root, run_id=run_id, repo_dir=REPO_ROOT)
    write_manifest(
        ctx,
        config,
        environment=probe_environment(
            repo_dir=REPO_ROOT, evobench_snapshot_sha=EVOBENCH_PINNED_REVISION
        ),
        harness_snapshot_id=snapshot.snapshot_id,
        run_spec=spec.manifest_view(),
    )
    ledger = Ledger(ctx.out_dir / "ledger.jsonl", ctx.run_id)
    provider = DeepSeekClient(
        transport=make_openai_transport(
            api_key=settings.deepseek_api_key, base_url=config.llm.base_url, timeout_s=300
        ),
        ledger=ledger,
        pricing=pricing,
        retry=config.llm.retry,
        cache=ResponseCache(tmp_path / "cache", provider="deepseek"),
    )
    judge = AhdJudgeClient(provider, config=config.judge, api_base=config.llm.base_url, seed=0)
    scorer = Scorer(judge=judge, ledger=ledger, arm=spec.arm, seed=0, claw_repo=CLAW_REPO)
    with TraceWriter(ctx.out_dir / "trace.jsonl", ctx.run_id) as trace:
        runner = Runner(
            ctx=ctx,
            config=config,
            settings=settings,
            pricing=pricing,
            ledger=ledger,
            scorer=scorer,
            trace=trace,
            claw_repo=CLAW_REPO,
        )
        result = runner.run(spec, tasks, snapshot=snapshot)
    return result, ledger, ctx


def test_claw_two_replicates_paper_config(tmp_path: Path) -> None:
    _prereqs()
    result, ledger, ctx = _run(tmp_path, mode="normal", replicates=2, run_id="live-claw-normal")
    task = result.tasks[0]
    assert task.k == 2 and task.benchmark_k == 3 and task.benchmark_aggregate_valid is False
    assert len(task.rollouts) == 2
    for record in task.rollouts:
        assert record.error_family != "infra", record.error
        # DeepSeek at max effort omits reasoning_content on an occasional step (observed 19 of
        # 20 in a CLI smoke run), so the invariant is "most steps", not "every step".
        assert record.steps > 0 and record.reasoning_steps >= 1
        assert record.reasoning_steps / record.steps >= 0.8, (
            f"reasoning on {record.reasoning_steps}/{record.steps} steps"
        )
        events = read_trace(record.rollout_dir / "trajectory.jsonl", expected_run_id=ctx.run_id)
        for event in events:
            if event.kind == "model_call":
                usage = event.payload["usage"]
                assert isinstance(usage, dict)
                completion = usage["completion_tokens"]
                assert isinstance(completion, int) and completion > 0
        assert record.usd is not None and record.usd > 0
        score = record.score
        usage_rec = record.usage
        print(
            f"\n[live-claw] replicate={record.replicate} exit={record.exit_reason} "
            f"steps={record.steps} passed={score.passed if score else None} "
            f"value={score.value if score else None} "
            f"prompt={usage_rec.prompt_tokens if usage_rec else None} "
            f"cached={usage_rec.cache_hit_prompt_tokens if usage_rec else None} "
            f"completion={usage_rec.completion_tokens if usage_rec else None} "
            f"usd={record.usd:.4f} tier={record.pricing_tier} "
            f"duration={record.duration_seconds:.0f}s serper_approx={record.serper_calls_approx}"
        )
    summary = summarize(read_ledger(ledger.path))
    print(
        f"[live-claw] ledger: policy_rollouts={summary.policy_rollouts} "
        f"policy_usd={summary.policy_usd:.4f} judge_calls={summary.calls} "
        f"judge_usd={summary.usd:.4f}"
    )
    assert summary.policy_rollouts == 2


def test_claw_reference_mode(tmp_path: Path) -> None:
    _prereqs()
    result, _ledger, _ctx = _run(
        tmp_path, mode="reference", replicates=1, run_id="live-claw-reference", max_attempts=3
    )
    reference = result.references[0]
    print(
        f"\n[live-ref] attempts={reference.attempts} "
        f"passing_attempt={reference.passing_attempt} verified={reference.verified}"
    )
    for record in result.tasks[0].rollouts:
        events = read_trace(record.rollout_dir / "trajectory.jsonl")
        assert events[0].payload["mode"] == "reference"
        print(
            f"[live-ref] attempt={record.attempt} exit={record.exit_reason} "
            f"steps={record.steps} passed={record.score.passed if record.score else None} "
            f"usd={record.usd}"
        )
    assert result.tasks[0].rollouts[0].mode == "reference"
    assert reference.verified == "pending"
