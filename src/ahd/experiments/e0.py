"""E0 calibration: stage orchestration over the M2 runner and the M3 diagnosis pipeline.

No reference source: written fresh for ahd. The spec is ``experiments/E0/spec.yaml``; every
run this module starts carries ``manifest.experiment = {role, stage, spec_path, spec_sha256}``
and is resumable: a finished run (``summary.json``) is left alone, an unfinished one resumes on
its done markers, and every diagnosis step is skipped when its output file exists.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError

from ahd.core.config import JudgeConfig, RunConfig, StrictModel, load_run_config
from ahd.core.context import RunContext, create_run_context, git_state
from ahd.core.environment import probe_environment
from ahd.core.hashing import JsonValue, sha256_file, to_json_value
from ahd.core.io import atomic_write_text, read_json, read_text
from ahd.core.manifest import load_run_context, write_manifest
from ahd.core.trace import TRACE_FILENAME, TraceWriter
from ahd.diagnosis import genuineness, leakage
from ahd.diagnosis.llm import DiagnosisLLM, DiagnosisModelConfig
from ahd.diagnosis.pipeline import (
    align_failures,
    cluster_run,
    corrupt_run,
    diagnosis_dir,
    instrument_snapshot,
    leakage_run,
    load_alignments,
    replay_failures,
    signal_failures,
    verify_references,
)
from ahd.diagnosis.signal import load_prompts
from ahd.errors import ConfigError, InfraError
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, snapshot_from_dir
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import PROVIDER_NAME, DeepSeekClient, make_openai_transport
from ahd.llm.ledger import LEDGER_FILENAME, Ledger
from ahd.llm.pricing import PricingTable, load_pricing
from ahd.logs import configure_logging
from ahd.runner.records import FailureRecord, RolloutRecord
from ahd.runner.runner import Runner
from ahd.runner.spec import BENCHMARK_TRIALS_BY_SOURCE, RunSpec
from ahd.settings import Settings, load_settings
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.kinds import Split
from ahd.tasks.models import Artifacts, Task, TaskSet
from ahd.tasks.sampling import sample_per_source
from ahd.tasks.scorer import Scorer

logger = logging.getLogger(__name__)

SEED_HARNESS = Path("third_party/evo-bench/policy_harness_seed")
COMPONENTS_YAML = Path("configs/harness/seed_components.yaml")
CLAW_REPO_DEFAULT = Path("external/claw-eval")
HELDOUT_PATH = Path("experiments/heldout_v1.json")
type Stage = Literal["E0a", "E0b"]


# ---------------------------------------------------------------- spec


class ReplaySpec(StrictModel):
    k: int = Field(ge=1)
    max_candidates: int = Field(ge=1)
    economize: bool


class PilotSpec(StrictModel):
    per_source: int = Field(ge=1)
    seed: int
    replicates: Literal["benchmark"]
    diagnosis_on_failures: tuple[str, ...]


class E0Spec(StrictModel):
    schema_version: Literal[1]
    experiment: Literal["E0"]
    role: Literal["calibration"]
    policy: dict[str, JsonValue]
    judge: dict[str, JsonValue]
    mock_today: str
    replay: ReplaySpec
    reference_max_attempts: int = Field(ge=1)
    workers: int = Field(ge=1)
    config: str
    runs_root: str
    owner_budget_usd: float | None
    sources: tuple[str, ...]
    E0a: PilotSpec
    E0b: dict[str, JsonValue]
    decision_rules: dict[str, str]
    thresholds: dict[str, float]


def load_spec(path: Path = Path("experiments/E0/spec.yaml")) -> E0Spec:
    try:
        raw = yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    try:
        return E0Spec.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid E0 spec {path}:\n{exc}") from exc


def _check_spec_matches_config(spec: E0Spec, config: RunConfig) -> None:
    """The spec is the contract; the run config must implement it."""
    problems: list[str] = []
    if config.policy.model != spec.policy.get("model"):
        problems.append("policy.model")
    if config.policy.temperature != spec.policy.get("temperature"):
        problems.append("policy.temperature")
    if config.policy.reasoning_effort != spec.policy.get("reasoning_effort"):
        problems.append("policy.reasoning_effort")
    if config.judge.model != spec.judge.get("model"):
        problems.append("judge.model")
    if config.judge.temperature != spec.judge.get("temperature"):
        problems.append("judge.temperature")
    if config.judge.use_cache != spec.judge.get("cached"):
        problems.append("judge.cached")
    if config.run.mock_today != spec.mock_today:
        problems.append("mock_today")
    if config.run.reference_max_attempts != spec.reference_max_attempts:
        problems.append("reference_max_attempts")
    if config.run.workers != spec.workers:
        problems.append("workers")
    if str(config.runs_root) != spec.runs_root:
        problems.append("runs_root")
    if not config.require_clean_tree:
        problems.append("require_clean_tree must be true")
    if problems:
        raise ConfigError(f"configs/runs/e0.yaml disagrees with the E0 spec on: {problems}")


# ---------------------------------------------------------------- context


class E0Context:
    def __init__(
        self,
        spec_path: Path = Path("experiments/E0/spec.yaml"),
        *,
        repo_dir: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repo_dir = (repo_dir or Path.cwd()).resolve()
        self.spec_path = spec_path
        self.spec = load_spec(spec_path)
        self.spec_sha256 = sha256_file(spec_path)
        self.config = load_run_config(Path(self.spec.config))
        _check_spec_matches_config(self.spec, self.config)
        self.settings = settings or load_settings()
        self.pricing: PricingTable = load_pricing(self.config.pricing_path)
        self.runs_root = Path(self.spec.runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.components = ComponentManifest.load(COMPONENTS_YAML)
        self.claw_repo = CLAW_REPO_DEFAULT.resolve() if CLAW_REPO_DEFAULT.is_dir() else None
        self._tasksets: dict[str, TaskSet] = {}
        configure_logging(json_path=self.runs_root / "e0.log.jsonl")

    # -- tasks
    def taskset(self, split: Split) -> TaskSet:
        if split not in self._tasksets:
            assert self.config.tasks is not None
            loader = EvoBenchLoader(
                dataset_id=self.config.tasks.dataset_id, revision=self.config.tasks.revision
            )
            self._tasksets[split] = loader.load(split)
        return self._tasksets[split]

    # -- snapshot
    def seed_snapshot(self) -> HarnessSnapshot:
        store = SnapshotStore(self.runs_root / "harness_store")
        for snapshot_id in store.ids():
            snapshot = store.load(snapshot_id)
            if snapshot.meta.provenance == "seed":
                return snapshot
        return snapshot_from_dir(
            SEED_HARNESS, store=store, manifest=self.components, provenance="seed"
        )

    # -- model clients
    def provider(self, ledger: Ledger) -> DeepSeekClient:
        return DeepSeekClient(
            transport=make_openai_transport(
                api_key=self.settings.deepseek_api_key,
                base_url=self.config.llm.base_url,
                timeout_s=self.config.llm.timeout_s,
            ),
            ledger=ledger,
            pricing=self.pricing,
            retry=self.config.llm.retry,
            cache=ResponseCache(self.config.llm.cache_dir, provider=PROVIDER_NAME),
        )

    def judge(self, provider: DeepSeekClient, config: JudgeConfig | None = None) -> AhdJudgeClient:
        return AhdJudgeClient(
            provider,
            config=config or self.config.judge,
            api_base=self.config.llm.base_url,
            seed=self.config.seed,
        )

    def experiment_block(self, stage: Stage) -> dict[str, JsonValue]:
        return {
            "role": self.spec.role,
            "stage": stage,
            "spec_path": str(self.spec_path),
            "spec_sha256": self.spec_sha256,
        }

    # -- stage log
    def log_stage(self, stage: str, run_id: str, event: str, **extra: JsonValue) -> None:
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "stage": stage,
            "run_id": run_id,
            "event": event,
            **extra,
        }
        with (self.runs_root / "stages.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- runs


def trials_for(source: str) -> int:
    return BENCHMARK_TRIALS_BY_SOURCE.get(source, 1)


def run_or_resume(
    ctx: E0Context,
    *,
    run_id: str,
    tasks: Sequence[Task],
    mode: Literal["normal", "reference"],
    replicates: int,
    stage: Stage,
    arm: str = "seed",
) -> Path:
    """Start, resume or skip one run; returns its directory."""
    run_dir = ctx.runs_root / run_id
    if (run_dir / "summary.json").is_file():
        logger.info("run finished earlier", extra={"run_id": run_id})
        return run_dir
    snapshot = ctx.seed_snapshot()
    task_ids = tuple(t.id for t in tasks)
    if (run_dir / "manifest.json").is_file():
        run_ctx, manifest = load_run_context(run_dir)
        if manifest.run_spec is None:
            raise ConfigError(f"{run_id} has no run_spec; cannot resume")
        spec = RunSpec.model_validate(manifest.run_spec)
        resume = True
    else:
        state = git_state(ctx.repo_dir)
        if state.dirty:
            raise ConfigError(
                f"refusing to start {run_id}: working tree is dirty (E0 runs need a clean sha)"
            )
        spec = RunSpec.from_config(
            ctx.config,
            harness_snapshot_id=snapshot.snapshot_id,
            task_ids=task_ids,
            mode=mode,
            replicates=replicates,
            arm=arm,
            workers=ctx.spec.workers,
        )
        run_ctx = create_run_context(
            ctx.config, runs_root=ctx.runs_root, run_id=run_id, repo_dir=ctx.repo_dir
        )
        assert ctx.config.tasks is not None
        write_manifest(
            run_ctx,
            ctx.config,
            config_path=Path(ctx.spec.config),
            environment=probe_environment(
                repo_dir=ctx.repo_dir,
                evobench_dataset_id=ctx.config.tasks.dataset_id,
                evobench_snapshot_sha=ctx.config.tasks.revision,
            ),
            harness_snapshot_id=snapshot.snapshot_id,
            run_spec=spec.manifest_view(),
            experiment=ctx.experiment_block(stage),
        )
        resume = False
    ctx.log_stage(stage, run_id, "run_start", resume=resume, tasks=len(task_ids), mode=mode)
    ledger = Ledger(run_dir / LEDGER_FILENAME, run_ctx.run_id)
    provider = ctx.provider(ledger)
    scorer = Scorer(
        judge=ctx.judge(provider),
        ledger=ledger,
        arm=spec.arm,
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    with TraceWriter(run_dir / TRACE_FILENAME, run_ctx.run_id) as trace:
        runner = Runner(
            ctx=run_ctx,
            config=ctx.config,
            settings=ctx.settings,
            pricing=ctx.pricing,
            ledger=ledger,
            scorer=scorer,
            trace=trace,
            claw_repo=ctx.claw_repo,
        )
        result = runner.run(spec, list(tasks), snapshot=snapshot, resume=resume)
    ctx.log_stage(stage, run_id, "run_done", failures=len(result.failures), tasks=len(result.tasks))
    return run_dir


def load_failures(run_dir: Path) -> list[FailureRecord]:
    path = run_dir / "failures.json"
    if not path.is_file():
        return []
    raw = read_json(path)
    return [FailureRecord.model_validate(x) for x in raw] if isinstance(raw, list) else []


def task_failures(run_dir: Path) -> list[FailureRecord]:
    """Harness failures only (task and budget families); infra is not the harness's fault."""
    return [f for f in load_failures(run_dir) if f.family != "infra"]


# ---------------------------------------------------------------- diagnosis chain


def diagnose_run(
    ctx: E0Context,
    run_dir: Path,
    *,
    stage: Stage,
    taskset: TaskSet,
    full_arms_keys: Sequence[str] = (),
    corruption_seed: int = 0,
) -> Path | None:
    """Reference run -> genuineness -> alignment -> replay -> signal -> cluster -> corrupt ->
    leakage for one normal run; every step skipped when its output exists. Returns the
    reference run dir, or None when the run had no harness failure."""
    failures = task_failures(run_dir)
    if not failures:
        ctx.log_stage(stage, run_dir.name, "no_failures")
        return None
    failed_ids = tuple(sorted({f.task_id for f in failures}))
    ref_dir = run_or_resume(
        ctx,
        run_id=f"{run_dir.name}-ref",
        tasks=[taskset.by_id(t) for t in failed_ids],
        mode="reference",
        replicates=1,
        stage=stage,
        arm="reference",
    )
    run_ctx, manifest = load_run_context(run_dir)
    ledger = Ledger(run_dir / LEDGER_FILENAME, run_ctx.run_id)
    provider = ctx.provider(ledger)
    llm = DiagnosisLLM(
        provider,
        config=DiagnosisModelConfig(model=ctx.config.judge.model),
        seed=ctx.config.seed,
    )
    out = diagnosis_dir(run_dir)
    if not (diagnosis_dir(ref_dir) / "genuineness.json").is_file():
        ctx.log_stage(stage, run_dir.name, "genuineness_start")
        ref_ledger = Ledger(ref_dir / LEDGER_FILENAME, ref_dir.name)
        ref_llm = DiagnosisLLM(
            ctx.provider(ref_ledger),
            config=DiagnosisModelConfig(model=ctx.config.judge.model),
            seed=ctx.config.seed,
        )
        verify_references(
            ref_dir,
            taskset=taskset,
            llm=ref_llm,
            prompt_template=genuineness.load_prompt(),
            claw_repo=ctx.claw_repo,
        )
    if not (out / "alignments.json").is_file():
        align_failures(run_dir, ref_dir)
    alignments = load_alignments(run_dir)
    if not alignments:
        ctx.log_stage(stage, run_dir.name, "no_genuine_reference")
        return ref_dir
    assert manifest.harness_snapshot_id is not None and manifest.run_spec is not None
    spec = RunSpec.model_validate(manifest.run_spec)
    studied = SnapshotStore(run_dir / "harness").load(manifest.harness_snapshot_id)
    instrument = instrument_snapshot(run_dir, ctx.components)
    scorer = Scorer(
        judge=ctx.judge(provider),
        ledger=ledger,
        arm="replay",
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    with TraceWriter(run_dir / TRACE_FILENAME, run_ctx.run_id) as trace:
        runner = Runner(
            ctx=run_ctx,
            config=ctx.config,
            settings=ctx.settings,
            pricing=ctx.pricing,
            ledger=ledger,
            scorer=scorer,
            trace=trace,
            claw_repo=ctx.claw_repo,
        )
        if not (out / "replays.json").is_file():
            ctx.log_stage(stage, run_dir.name, "replay_start", failures=len(alignments))
            replay_failures(
                run_dir,
                ref_dir,
                runner=runner,
                spec=spec,
                studied=studied,
                instrument=instrument,
                taskset=taskset,
                k=ctx.spec.replay.k,
                max_candidates=ctx.spec.replay.max_candidates,
                economize=ctx.spec.replay.economize,
                resume=True,
                workers=ctx.spec.workers,
            )
            ctx.log_stage(stage, run_dir.name, "replay_done")
        if full_arms_keys and not (out / "replays_replay_full.json").is_file():
            ctx.log_stage(stage, run_dir.name, "replay_full_start", failures=len(full_arms_keys))
            replay_failures(
                run_dir,
                ref_dir,
                runner=runner,
                spec=spec,
                studied=studied,
                instrument=instrument,
                taskset=taskset,
                k=ctx.spec.replay.k,
                max_candidates=ctx.spec.replay.max_candidates,
                economize=False,
                only=full_arms_keys,
                resume=True,
                subdir="replay_full",
                workers=ctx.spec.workers,
            )
    if not (out / "diagnoses.json").is_file():
        signal_failures(
            run_dir,
            ref_dir,
            taskset=taskset,
            manifest=ctx.components,
            harness_snapshot_id=studied.snapshot_id,
            llm=llm,
            prompts=load_prompts(),
        )
    if not (out / "clusters.json").is_file():
        cluster_run(
            run_dir,
            manifest=ctx.components,
            reference_run=ref_dir.name,
            instrument_snapshot_id=instrument.snapshot_id,
        )
    if not (out / "rendered" / f"reference-s{corruption_seed}" / "rendered.json").is_file():
        corrupt_run(run_dir, seed=corruption_seed, manifest=ctx.components)
    if not (out / "leakage.json").is_file():
        leakage_run(
            run_dir, manifest=ctx.components, llm=llm, prompt_template=leakage.load_prompt()
        )
    ctx.log_stage(stage, run_dir.name, "diagnosis_done")
    return ref_dir


# ---------------------------------------------------------------- E0a


def pilot_tasks(ctx: E0Context) -> TaskSet:
    validation = ctx.taskset("validation").select(sources=list(ctx.spec.sources))
    return sample_per_source(validation, per_source=ctx.spec.E0a.per_source, seed=ctx.spec.E0a.seed)


def pilot(ctx: E0Context) -> dict[str, Path]:
    """E0a: sample, run per source (benchmark trials), diagnose every harness failure."""
    sample = pilot_tasks(ctx)
    atomic_write_text(
        ctx.runs_root / "e0a_tasks.json",
        json.dumps(
            {
                s: sorted(t.id for t in sample.tasks if t.source_benchmark == s)
                for s in ctx.spec.sources
            },
            indent=2,
        )
        + "\n",
    )
    run_dirs: dict[str, Path] = {}
    for source in ctx.spec.sources:
        tasks = [t for t in sample.tasks if t.source_benchmark == source]
        run_dirs[source] = run_or_resume(
            ctx,
            run_id=f"e0a-{source}",
            tasks=tasks,
            mode="normal",
            replicates=trials_for(source),
            stage="E0a",
        )
    for run_dir in run_dirs.values():
        diagnose_run(ctx, run_dir, stage="E0a", taskset=ctx.taskset("validation"))
    return run_dirs


# ---------------------------------------------------------------- E0b


def _int(value: object, default: int) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else default


def b1_baseline(ctx: E0Context) -> dict[str, list[Path]]:
    validation = ctx.taskset("validation")
    passes = _int(_block(ctx, "B1_baseline").get("passes"), 2)
    dirs: dict[str, list[Path]] = {}
    for source in ctx.spec.sources:
        tasks = [t for t in validation.tasks if t.source_benchmark == source and not t.excluded]
        dirs[source] = [
            run_or_resume(
                ctx,
                run_id=f"e0b-b1-{source}-p{p}",
                tasks=tasks,
                mode="normal",
                replicates=trials_for(source),
                stage="E0b",
            )
            for p in range(1, passes + 1)
        ]
    return dirs


def _block(ctx: E0Context, name: str) -> dict[str, Any]:
    block = ctx.spec.E0b.get(name)
    return dict(block) if isinstance(block, dict) else {}


def heldout(ctx: E0Context, *, per_source: int | None = None) -> TaskSet:
    """The frozen held-out sample (experiments/heldout_v1.json); written once, verified after."""
    block = _block(ctx, "B2_heldout")
    n = per_source or _int(block.get("per_source"), 30)
    seed = _int(block.get("seed"), 0)
    evaluation = ctx.taskset("evaluation").select(sources=list(ctx.spec.sources))
    sample = sample_per_source(evaluation, per_source=n, seed=seed)
    new_ids: list[str] = sorted(t.id for t in sample.tasks)
    frozen: dict[str, object] = {
        "split": "evaluation",
        "seed": seed,
        "per_source": n,
        "task_ids": new_ids,
    }
    path = Path(str(block.get("frozen", HELDOUT_PATH)))
    if path.is_file():
        existing = read_json(path)
        if not isinstance(existing, dict):
            raise ConfigError(f"{path} is not an object")
        raw_ids = existing.get("task_ids")
        old_ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else []
        if existing.get("per_source") == n:
            if old_ids != new_ids:
                raise ConfigError(
                    f"{path} disagrees with the deterministic sample; not overwriting"
                )
        elif set(old_ids) <= set(new_ids):
            atomic_write_text(path, json.dumps(frozen, indent=2) + "\n")  # D5 superset growth
        else:
            raise ConfigError(f"{path} is not a subset of the new sample; not overwriting")
    else:
        atomic_write_text(path, json.dumps(frozen, indent=2) + "\n")
    return sample


def b2_heldout(ctx: E0Context, *, per_source: int | None = None) -> dict[str, list[Path]]:
    sample = heldout(ctx, per_source=per_source)
    passes = _int(_block(ctx, "B2_heldout").get("passes"), 2)
    dirs: dict[str, list[Path]] = {}
    for source in ctx.spec.sources:
        tasks = [t for t in sample.tasks if t.source_benchmark == source]
        dirs[source] = [
            run_or_resume(
                ctx,
                run_id=f"e0b-b2-{source}-p{p}",
                tasks=tasks,
                mode="normal",
                replicates=trials_for(source),
                stage="E0b",
            )
            for p in range(1, passes + 1)
        ]
    return dirs


def full_arms_subset(ctx: E0Context, run_dirs: Sequence[Path]) -> dict[str, tuple[str, ...]]:
    """30 failures, seed 0, stratified by source (as equal as the counts allow); keys per run."""
    block = _block(ctx, "B5_replay")
    raw_subset = block.get("full_arms_subset")
    subset: dict[str, Any] = raw_subset if isinstance(raw_subset, dict) else {}
    n = _int(subset.get("n"), 30)
    seed = _int(subset.get("seed"), 0)
    per_source: dict[str, list[tuple[Path, str]]] = {s: [] for s in ctx.spec.sources}
    for run_dir in run_dirs:
        for f in task_failures(run_dir):
            per_source.setdefault(f.source_benchmark, []).append(
                (run_dir, f"{f.task_id}__{f.replicate}__a{f.attempt}")
            )
    chosen: dict[str, list[str]] = {}
    quota = n // max(1, len([s for s in per_source if per_source[s]]))
    leftovers: list[tuple[Path, str]] = []
    for source in sorted(per_source):
        items = sorted(per_source[source], key=lambda x: (x[0].name, x[1]))
        random.Random(f"{seed}:{source}".__hash__() & 0xFFFFFFFF).shuffle(items)
        for run_dir, key in items[:quota]:
            chosen.setdefault(run_dir.name, []).append(key)
        leftovers.extend(items[quota:])
    taken = sum(len(v) for v in chosen.values())
    for run_dir, key in sorted(leftovers)[: max(0, n - taken)]:
        chosen.setdefault(run_dir.name, []).append(key)
    return {k: tuple(sorted(v)) for k, v in chosen.items()}


def b3_to_b6(ctx: E0Context, b1_dirs: dict[str, list[Path]]) -> None:
    """Failure mining, references, alignment + replay (+ full-arms subset), clusters,
    corruption feasibility, leakage over every B1 run."""
    all_dirs = [d for dirs in b1_dirs.values() for d in dirs]
    subset = full_arms_subset(ctx, all_dirs)
    for run_dir in all_dirs:
        diagnose_run(
            ctx,
            run_dir,
            stage="E0b",
            taskset=ctx.taskset("validation"),
            full_arms_keys=subset.get(run_dir.name, ()),
        )


class JudgeCalibrationRow(StrictModel):
    run_id: str
    task_id: str
    source: str
    replicate: str
    attempt: int
    original_passed: bool
    original_value: float
    pro_bypass_passed: bool | None
    pro_bypass_value: float | None
    flash_passed: bool | None
    flash_value: float | None
    error: str | None = None


def b7_judge_calibration(ctx: E0Context, run_dirs: Sequence[Path]) -> Path:
    """Re-judge 50 scored artifacts (a) with the cache bypassed (self-consistency) and (b) with
    deepseek-v4-flash; released per-trajectory judge labels do not exist in the Evo-Bench
    snapshot (only expected answers and rubrics), which the report states."""
    block = _block(ctx, "B7_judge_calibration")
    n = _int(block.get("n_artifacts"), 50)
    cross_model = str(block.get("cross_judge", "deepseek-v4-flash"))
    candidates: list[tuple[Path, RolloutRecord]] = []
    for run_dir in run_dirs:
        for marker in sorted(run_dir.glob("rollouts/*/*/done.json")) + sorted(
            run_dir.glob("rollouts/*/*/attempt_*/done.json")
        ):
            record = RolloutRecord.model_validate(read_json(marker))
            if (marker.parent / "score.json").is_file() and record.error_family != "infra":
                candidates.append((run_dir, record))
    by_source: dict[str, list[tuple[Path, RolloutRecord]]] = {}
    for item in candidates:
        by_source.setdefault(item[1].source_benchmark, []).append(item)
    chosen: list[tuple[Path, RolloutRecord]] = []
    quota = max(1, n // max(1, len(by_source)))
    for source in sorted(by_source):
        items = sorted(by_source[source], key=lambda x: (x[0].name, x[1].task_id, x[1].replicate))
        random.Random(f"e0-b7:{source}").shuffle(items)
        chosen.extend(items[:quota])
    chosen = chosen[:n]
    out = ctx.runs_root / "judge_calibration.json"
    done: dict[str, JudgeCalibrationRow] = {}
    if out.is_file():
        raw = read_json(out)
        if isinstance(raw, list):
            for x in raw:
                row = JudgeCalibrationRow.model_validate(x)
                done[f"{row.run_id}/{row.task_id}/{row.replicate}/{row.attempt}"] = row
    ledger = Ledger(ctx.runs_root / "judge_calibration.ledger.jsonl", "e0-b7")
    provider = ctx.provider(ledger)
    bypass = Scorer(
        judge=ctx.judge(provider, ctx.config.judge.model_copy(update={"use_cache": False})),
        ledger=ledger,
        arm="judge_calibration_bypass",
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    flash = Scorer(
        judge=ctx.judge(provider, ctx.config.judge.model_copy(update={"model": cross_model})),
        ledger=ledger,
        arm="judge_calibration_flash",
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    taskset = ctx.taskset("validation")
    for run_dir, record in chosen:
        key = f"{run_dir.name}/{record.task_id}/{record.replicate}/{record.attempt}"
        if key in done:
            continue
        task = taskset.by_id(record.task_id)
        original = read_json(record.rollout_dir / "score.json")
        assert isinstance(original, dict)
        artifacts = Artifacts(
            workspace=record.workspace_dir,
            final_answer=record.final_answer,
            trajectory_path=record.rollout_dir / "trajectory.json",
            rollout_id=record.rollout_id,
        )
        row = JudgeCalibrationRow(
            run_id=run_dir.name,
            task_id=record.task_id,
            source=record.source_benchmark,
            replicate=record.replicate,
            attempt=record.attempt,
            original_passed=bool(original["passed"]),
            original_value=float(str(original["value"])),
            pro_bypass_passed=None,
            pro_bypass_value=None,
            flash_passed=None,
            flash_value=None,
        )
        if not record.workspace_dir.is_dir():
            row = row.model_copy(update={"error": "workspace not kept; cannot re-judge"})
        else:
            try:
                a = bypass.score(task, artifacts)
                b = flash.score(task, artifacts)
                row = row.model_copy(
                    update={
                        "pro_bypass_passed": a.passed,
                        "pro_bypass_value": a.value,
                        "flash_passed": b.passed,
                        "flash_value": b.value,
                    }
                )
            except InfraError as exc:
                row = row.model_copy(update={"error": f"{exc.kind}: {exc}"})
        done[key] = row
        atomic_write_text(
            out,
            json.dumps([r.model_dump(mode="json") for r in done.values()], indent=2) + "\n",
        )
    return out


def e0b(ctx: E0Context, *, stages: Sequence[str] = ("B1", "B2", "B3-6", "B7")) -> None:
    b1_dirs = b1_baseline(ctx) if "B1" in stages else {}
    if "B2" in stages:
        b2_heldout(ctx)
    if "B3-6" in stages:
        if not b1_dirs:
            b1_dirs = {s: sorted(ctx.runs_root.glob(f"e0b-b1-{s}-p*")) for s in ctx.spec.sources}
        b3_to_b6(ctx, b1_dirs)
    if "B7" in stages:
        dirs = [d for dirs in b1_dirs.values() for d in dirs] or sorted(
            ctx.runs_root.glob("e0b-b1-*-p1")
        )
        b7_judge_calibration(ctx, dirs)


__all__ = [
    "E0Context",
    "E0Spec",
    "RunContext",
    "b1_baseline",
    "b2_heldout",
    "b3_to_b6",
    "b7_judge_calibration",
    "diagnose_run",
    "e0b",
    "full_arms_subset",
    "heldout",
    "load_spec",
    "pilot",
    "pilot_tasks",
    "run_or_resume",
    "task_failures",
    "to_json_value",
]
