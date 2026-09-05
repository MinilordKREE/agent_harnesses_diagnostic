"""Live M3: one real Claw failure through reference genuineness, alignment, replay (k=2) and a
corrupted diagnosis. Deselected by default (``make test-integration``); costs real money
(policy rollouts at the paper config plus judge and diagnosis calls).

Set ``AHD_DIAG_RUN`` and ``AHD_DIAG_REFERENCE_RUN`` (run directories produced earlier, e.g. by
``tests/integration/test_runner_live.py``) to skip the rollouts and diagnose existing runs.
``AHD_DIAG_EFFORT`` (default ``low``) sets the policy's reasoning effort for both runs: at the
paper's ``max`` the Claw task passes and there is nothing to diagnose (observed 2026-09-05).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ahd.core.config import load_run_config
from ahd.core.manifest import load_run_context
from ahd.core.trace import TraceWriter
from ahd.diagnosis import genuineness, leakage
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.pipeline import (
    align_failures,
    cluster_run,
    corrupt_run,
    instrument_snapshot,
    leakage_run,
    per_failure_cost,
    replay_failures,
    signal_failures,
    verify_references,
)
from ahd.diagnosis.signal import load_prompts
from ahd.harness.snapshot import SnapshotStore
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import DeepSeekClient, make_openai_transport
from ahd.llm.ledger import Ledger
from ahd.llm.pricing import load_pricing
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.settings import load_settings
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.scorer import Scorer
from tests.conftest import REPO_ROOT
from tests.integration.test_runner_live import CLAW_REPO, _prereqs, _run

pytestmark = pytest.mark.integration


HARD_CLAW_TASKS = (
    "claw-T007zh_todo_management",
    "claw-T148_system_health_check",
    "claw-T158_month_end_reconciliation",
    "claw-T156_onsite_support_dispatch",
    "claw-T161zh_automation_failure_recovery",
)


def _runs(tmp_path: Path) -> tuple[Path, Path]:
    env_run, env_ref = os.environ.get("AHD_DIAG_RUN"), os.environ.get("AHD_DIAG_REFERENCE_RUN")
    if env_run and env_ref:
        return Path(env_run), Path(env_ref)
    effort = os.environ.get("AHD_DIAG_EFFORT", "low")  # the paper config passes T007; low fails it
    task_ids = tuple(
        t for t in os.environ.get("AHD_DIAG_TASKS", ",".join(HARD_CLAW_TASKS)).split(",") if t
    )
    replicates = int(os.environ.get("AHD_DIAG_REPLICATES", "3"))
    result, _ledger, ctx = _run(
        tmp_path,
        mode="normal",
        replicates=replicates,
        run_id="live-diag-normal",
        effort=effort,
        task_ids=task_ids,
        workers=min(4, len(task_ids) * replicates),
    )
    failed = [f for f in result.failures if f.family != "infra"]
    seen = [(f.task_id, f.family, f.reason[:60]) for f in result.failures]
    print(f"\n[diag] normal run: failures={seen}")
    if not failed:
        # observed 2026-09-05: T007 and four hard tasks all pass at low effort (0.91 to 0.99)
        pytest.skip("every normal rollout passed; nothing to diagnose this time")
    target = failed[0].task_id
    ref_result, _l, ref_ctx = _run(
        tmp_path,
        mode="reference",
        replicates=1,
        run_id="live-diag-reference",
        max_attempts=3,
        effort=effort,
        task_ids=(target,),
    )
    if ref_result.references[0].passing_attempt is None:
        pytest.skip(f"no passing reference attempt for {target}")
    return ctx.out_dir, ref_ctx.out_dir


def test_claw_failure_reference_alignment_replay_and_corruption(tmp_path: Path) -> None:
    _prereqs()
    run_dir, ref_dir = _runs(tmp_path)
    settings = load_settings(REPO_ROOT / ".env")
    config = load_run_config(REPO_ROOT / "configs" / "runs" / "example.yaml")
    pricing = load_pricing(REPO_ROOT / config.pricing_path)
    ctx, manifest = load_run_context(run_dir)
    assert manifest.harness_snapshot_id is not None and manifest.run_spec is not None
    ledger = Ledger(run_dir / "ledger.jsonl", ctx.run_id)
    provider = DeepSeekClient(
        transport=make_openai_transport(
            api_key=settings.deepseek_api_key, base_url=config.llm.base_url, timeout_s=300
        ),
        ledger=ledger,
        pricing=pricing,
        retry=config.llm.retry,
        cache=ResponseCache(tmp_path / "cache", provider="deepseek"),
    )
    llm = DiagnosisLLM(provider, seed=0)
    taskset = EvoBenchLoader().load("validation")
    studied = SnapshotStore(run_dir / "harness").load(manifest.harness_snapshot_id)
    components = studied.resolved_manifest().manifest

    # 1. reference genuineness
    records = verify_references(
        ref_dir,
        taskset=taskset,
        llm=llm,
        prompt_template=genuineness.load_prompt(),
        claw_repo=CLAW_REPO,
    )
    assert records
    print(f"\n[diag] genuineness: {[(r.verdict, r.g1, r.g4, r.g2, r.g3) for r in records]}")
    if records[0].verdict != "genuine":
        pytest.skip(f"reference not genuine ({records[0].verdict}); no oracle available")

    # 2. alignment
    alignments = align_failures(run_dir, ref_dir)
    assert alignments and alignments[0].alignment.candidates
    a = alignments[0].alignment
    print(
        f"[diag] alignment: t_exact={a.t_exact} t_class={a.t_class} "
        f"candidates={[(c.step, c.divergence) for c in a.candidates]}"
    )

    # 3. replay validation, k=2, first 2 candidates
    spec = RunSpec.model_validate(manifest.run_spec)
    judge = AhdJudgeClient(provider, config=config.judge, api_base=config.llm.base_url, seed=0)
    scorer = Scorer(judge=judge, ledger=ledger, arm="replay", seed=0, claw_repo=CLAW_REPO)
    instrument = instrument_snapshot(run_dir, components)
    with TraceWriter(run_dir / "trace.jsonl", ctx.run_id) as trace:
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
        replays = replay_failures(
            run_dir,
            ref_dir,
            runner=runner,
            spec=spec,
            studied=studied,
            instrument=instrument,
            taskset=taskset,
            k=2,
            max_candidates=2,
            economize=True,
            only=(alignments[0].failure_key,),
        )
    assert replays
    r = replays[0]
    assert r.instrument_snapshot_id != r.studied_snapshot_id
    for c in r.candidates:
        print(
            f"[diag] replay c{c.step} {c.divergence}: status={c.status} "
            f"sub={[x.status for x in c.substitute.rollouts]} "
            f"ctl={[x.status for x in c.control.rollouts]} usd={c.usd:.4f}"
        )
    print(
        f"[diag] oracle_step={r.oracle_step} ({r.oracle_status}) "
        f"drift={json.dumps(r.drift_reports)[:400]}"
    )

    # 4. signals, clusters, one corrupted diagnosis per arm
    diagnoses = signal_failures(
        run_dir,
        ref_dir,
        taskset=taskset,
        manifest=components,
        harness_snapshot_id=studied.snapshot_id,
        llm=llm,
        prompts=load_prompts(REPO_ROOT / "configs" / "prompts" / "diagnosis"),
    )
    assert diagnoses.reference, diagnoses.errors
    clusters, _activity = cluster_run(
        run_dir,
        manifest=components,
        reference_run=ref_dir.name,
        instrument_snapshot_id=instrument.snapshot_id,
    )
    assert clusters.clusters
    for arm in ("reference", "system", "corrupt_where_near", "corrupt_where_far"):
        _table, rendered = corrupt_run(run_dir, arm=arm, seed=0, manifest=components)
        for item in rendered:
            text = item.rendered.text if item.rendered else f"IMPOSSIBLE: {item.impossible}"
            print(f"[diag] --- {arm} / {item.cluster_id} ---\n{text}")
    report = leakage_run(
        run_dir,
        manifest=components,
        llm=llm,
        prompt_template=leakage.load_prompt(
            REPO_ROOT / "configs" / "prompts" / "diagnosis" / "leakage.md"
        ),
    )
    print(f"[diag] leakage top1={report.top1_rate} top3={report.top3_rate}")
    print(f"[diag] cost: {json.dumps(per_failure_cost(run_dir))}")
