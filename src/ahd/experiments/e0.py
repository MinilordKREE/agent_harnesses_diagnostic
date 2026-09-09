"""E0 calibration: stage orchestration over the M2 runner and the M3 diagnosis pipeline.

No reference source: written fresh for ahd. The spec is ``experiments/E0/spec.yaml``; every
run this module starts carries ``manifest.experiment = {role, stage, spec_path, spec_sha256,
splits_sha256}`` and ``manifest.judges`` (judge model per source), and is resumable: a
finished run (``summary.json``) is left alone, an unfinished one resumes on its done markers,
and every diagnosis step is skipped when its output file exists. E0b enforces the owner's hard
spend cap between units of work (``BudgetExhausted``); nothing in flight is interrupted.
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError

from ahd.core.config import JudgeConfig, RunConfig, StrictModel, load_run_config
from ahd.core.context import create_run_context, git_state
from ahd.core.environment import probe_environment
from ahd.core.hashing import JsonValue, sha256_file, to_json_value
from ahd.core.io import atomic_write_text, lock_tree, read_json, read_text
from ahd.core.manifest import load_run_context, read_manifest, write_manifest
from ahd.core.trace import TRACE_FILENAME, TraceWriter
from ahd.diagnosis import coherent, genuineness, leakage
from ahd.diagnosis import probe as probe_module
from ahd.diagnosis.coherent import PlausibilityScore, parity
from ahd.diagnosis.llm import DiagnosisLLM, DiagnosisModelConfig
from ahd.diagnosis.pipeline import (
    PROBE_ARMS,
    AlignmentRecord,
    EscalationRow,
    align_failures,
    cluster_run,
    coherent_round,
    corrupt_run,
    diagnosis_dir,
    escalate_replays,
    finalize_coherent,
    instrument_snapshot,
    leakage_run,
    load_alignments,
    load_clusters,
    load_coherent_set,
    load_replays,
    probe_run,
    replay_failures,
    safe_key,
    signal_failures,
    verify_references,
)
from ahd.diagnosis.replay import SCHEDULE, CandidateReplay, unconfirmed
from ahd.diagnosis.schema import load_causes
from ahd.diagnosis.signal import load_prompts
from ahd.errors import BudgetExhausted, ConfigError, InfraError
from ahd.experiments.splits import (
    SPLITS_PATH,
    Splits,
    build_splits,
    freeze,
    load_splits,
    with_eval_rest,
)
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, snapshot_from_dir
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import PROVIDER_NAME, DeepSeekClient, make_openai_transport
from ahd.llm.ledger import LEDGER_FILENAME, Ledger, read_ledger
from ahd.llm.pricing import PricingTable, load_pricing
from ahd.llm.types import Attribution, ChatMessage, ChatRequest
from ahd.logs import configure_logging
from ahd.runner.records import FailureRecord, RolloutRecord
from ahd.runner.runner import Runner
from ahd.runner.spec import BENCHMARK_TRIALS_BY_SOURCE, RunSpec
from ahd.settings import Settings, load_settings
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import JUDGE_ARM, AhdJudgeClient
from ahd.tasks.kinds import Split
from ahd.tasks.models import Artifacts, Task, TaskSet
from ahd.tasks.sampling import sample_per_source
from ahd.tasks.scorer import Scorer

logger = logging.getLogger(__name__)

SEED_HARNESS = Path("third_party/evo-bench/policy_harness_seed")
COMPONENTS_YAML = Path("configs/harness/seed_components.yaml")
CLAW_REPO_DEFAULT = Path("external/claw-eval")
VISION_JUDGE_ARM = "judge_vision"
type Stage = Literal["E0a", "E0b", "E0c", "E0d"]

# a 1x1 white PNG for the vision probe (no benchmark data leaves the machine)
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


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


class JudgesSpec(StrictModel):
    primary: dict[str, str]
    secondary: dict[str, str] = {}


class E0Spec(StrictModel):
    schema_version: Literal[1]
    experiment: Literal["E0"]
    role: Literal["calibration"]
    policy: dict[str, JsonValue]
    judge: dict[str, JsonValue]
    judges: JudgesSpec
    mock_today: str
    replay: ReplaySpec
    reference_max_attempts: int = Field(ge=1)
    workers: int = Field(ge=1)
    config: str
    runs_root: str
    owner_budget_usd: float | None
    hard_cap_usd: float | None = None
    sources: tuple[str, ...]
    E0a: PilotSpec
    E0b: dict[str, JsonValue]
    E0c: dict[str, JsonValue] = {}
    """The D1' pivot (i) condition (policy reasoning_effort low). In spec.yaml ``status``
    records whether it ran before E0d; in spec_e0c.yaml it is the E0c plan itself."""
    E0d: dict[str, JsonValue] = {}
    """The M3.2 calibration addendum (oracle validity, corruption validity, power)."""
    decision_rules: dict[str, str]
    thresholds: dict[str, float]
    run_prefix: str = "e0b"
    """Run ids are ``<prefix>-b1-<source>-p<n>`` (mining passes), ``<prefix>-b2-...`` (held-out
    passes) and ``<run>-ref``; E0c uses ``e0c`` under its own runs_root."""
    splits_path: str = str(SPLITS_PATH)

    def block(self, name: str) -> dict[str, Any]:
        value = self.E0b.get(name)
        return dict(value) if isinstance(value, dict) else {}

    def e0d_block(self, name: str) -> dict[str, Any]:
        """An addendum block: from E0d, else from E0c (the E0c spec carries the F_mde and
        coherent-wrong settings under its own block)."""
        value = self.E0d.get(name)
        if not isinstance(value, dict):
            value = self.E0c.get(name)
        return dict(value) if isinstance(value, dict) else {}

    def e0c_block(self, name: str) -> dict[str, Any]:
        value = self.E0c.get(name)
        return dict(value) if isinstance(value, dict) else {}

    def e0c_cap(self) -> float | None:
        value = self.E0c.get("hard_cap_usd")
        return (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        )

    def e0d_cap(self) -> float | None:
        value = self.E0d.get("hard_cap_usd")
        return (
            float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
        )

    def e0d_order(self) -> tuple[str, ...]:
        value = self.E0d.get("order")
        return tuple(str(x) for x in value) if isinstance(value, list) else ("B", "C", "D", "A")

    def corruption_seed(self) -> int:
        value = self.E0d.get(
            "corruption_seed",
            self.E0c.get("corruption_seed", self.block("B6_clusters").get("corruption_seed", 0)),
        )
        return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0

    def scope(self, source: str) -> dict[str, Any]:
        scope = self.block("scope").get(source)
        return dict(scope) if isinstance(scope, dict) else {}

    def workers_for(self, source: str) -> int:
        value = self.block("workers_by_source").get(source)
        return (
            int(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else self.workers
        )


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
    for source, model in spec.judges.primary.items():
        if model != config.judge.model:
            problems.append(f"judges.primary.{source} != judge.model")
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
        self.splits_path = Path(self.spec.splits_path)
        self.splits_sha256: str | None = (
            sha256_file(self.splits_path) if self.splits_path.is_file() else None
        )
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

    def all_tasks(self) -> TaskSet:
        """Validation and evaluation tasks in one set (E0b diagnoses the mining pool)."""
        validation = self.taskset("validation")
        evaluation = self.taskset("evaluation")
        return validation.model_copy(update={"tasks": validation.tasks + evaluation.tasks})

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

    def judge(
        self,
        provider: DeepSeekClient,
        config: JudgeConfig | None = None,
        *,
        arm: str = JUDGE_ARM,
    ) -> AhdJudgeClient:
        return AhdJudgeClient(
            provider,
            config=config or self.config.judge,
            api_base=self.config.llm.base_url,
            seed=self.config.seed,
            arm=arm,
        )

    def vision_judge_config(self, source: str) -> JudgeConfig | None:
        model = self.spec.judges.secondary.get(source)
        if model is None:
            return None
        return self.config.judge.model_copy(update={"model": model, "multimodal": True})

    def secondary_scorers(
        self, provider: DeepSeekClient, ledger: Ledger, *, arm: str, sources: Sequence[str]
    ) -> dict[str, Scorer]:
        out: dict[str, Scorer] = {}
        for source in sources:
            cfg = self.vision_judge_config(source)
            if cfg is not None:
                out[source] = Scorer(
                    judge=self.judge(provider, cfg, arm=VISION_JUDGE_ARM),
                    ledger=ledger,
                    arm=arm,
                    seed=self.config.seed,
                    claw_repo=self.claw_repo,
                )
        return out

    def judges_block(self, sources: Sequence[str]) -> dict[str, JsonValue]:
        return to_json_value(
            {
                "primary": {
                    s: self.spec.judges.primary.get(s, self.config.judge.model) for s in sources
                },
                "secondary": {s: m for s, m in self.spec.judges.secondary.items() if s in sources},
            }
        )  # type: ignore[return-value]

    def experiment_block(self, stage: Stage) -> dict[str, JsonValue]:
        block: dict[str, JsonValue] = {
            "role": self.spec.role,
            "stage": stage,
            "spec_path": str(self.spec_path),
            "spec_sha256": self.spec_sha256,
        }
        if stage in ("E0b", "E0c", "E0d"):
            block["splits_path"] = str(self.splits_path)
            block["splits_sha256"] = self.splits_sha256
        return block

    # -- spend and the hard cap
    def spend_e0b(self) -> float:
        """E0b spend: the E0b runs' main ledgers (held-out passes added by E0d-A carry the E0d
        stage in their manifest and are excluded), judge calibration and preflight."""
        total = 0.0
        paths = [
            self.runs_root / "judge_calibration.ledger.jsonl",
            self.runs_root / "preflight.ledger.jsonl",
        ]
        for run_dir in self.runs_root.glob("e0b-*"):
            manifest_path = run_dir / "manifest.json"
            if not manifest_path.is_file():
                continue
            experiment = read_manifest(manifest_path).experiment or {}
            if experiment.get("stage") != "E0d":
                paths.append(run_dir / LEDGER_FILENAME)
        for path in paths:
            if path.is_file():
                total += sum(
                    r.usd for r in read_ledger(path) if r.event in ("call", "policy", "search")
                )
        return total

    def guard(self) -> None:
        cap = self.spec.hard_cap_usd
        if cap is None:
            return
        spent = self.spend_e0b()
        if spent >= cap:
            self.log_stage("E0b", "-", "hard_cap_reached", spent_usd=round(spent, 4), cap_usd=cap)
            raise BudgetExhausted(
                f"E0b hard cap reached: {spent:.2f} USD >= {cap:.2f} USD; rerun after raising "
                "hard_cap_usd in the spec (all finished work is reused)",
                budget=cap,
                spent=spent,
                unit="usd",
            )

    # -- E0d spend: its own ledgers only (the E0b ledgers are untouched)
    def e0d_ledger(self) -> Ledger:
        return Ledger(self.runs_root / "e0d.ledger.jsonl", "e0d")

    def e0d_ledger_paths(self) -> list[Path]:
        paths = [
            self.runs_root / "e0d.ledger.jsonl",
            *self.runs_root.glob("e0b-b1-*/ledger.e0d.jsonl"),
        ]
        for run_dir in self.runs_root.glob("e0b-b2-*-p*"):
            manifest_path = run_dir / "manifest.json"
            if manifest_path.is_file():
                experiment = read_manifest(manifest_path).experiment or {}
                if experiment.get("stage") == "E0d":
                    paths.append(run_dir / LEDGER_FILENAME)
        return paths

    def spend_e0d(self) -> float:
        total = 0.0
        for path in self.e0d_ledger_paths():
            if path.is_file():
                total += sum(
                    r.usd for r in read_ledger(path) if r.event in ("call", "policy", "search")
                )
        return total

    def guard_e0d(self) -> None:
        cap = self.spec.e0d_cap()
        if cap is None:
            return
        spent = self.spend_e0d()
        if spent >= cap:
            self.log_stage("E0d", "-", "hard_cap_reached", spent_usd=round(spent, 4), cap_usd=cap)
            raise BudgetExhausted(
                f"E0d hard cap reached: {spent:.2f} USD >= {cap:.2f} USD; rerun after raising "
                "E0d.hard_cap_usd in the spec (all finished work is reused)",
                budget=cap,
                spent=spent,
                unit="usd",
            )

    # -- E0c spend: every ledger under its own runs_root
    def spend_e0c(self) -> float:
        total = 0.0
        for path in self.runs_root.rglob("ledger*.jsonl"):
            total += sum(
                r.usd for r in read_ledger(path) if r.event in ("call", "policy", "search")
            )
        return total

    def guard_e0c(self) -> None:
        cap = self.spec.e0c_cap()
        if cap is None:
            return
        spent = self.spend_e0c()
        if spent >= cap:
            self.log_stage("E0c", "-", "hard_cap_reached", spent_usd=round(spent, 4), cap_usd=cap)
            raise BudgetExhausted(
                f"E0c hard cap reached: {spent:.2f} USD >= {cap:.2f} USD; rerun after raising "
                "E0c.hard_cap_usd in the spec (all finished work is reused)",
                budget=cap,
                spent=spent,
                unit="usd",
            )

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
    workers: int | None = None,
    with_secondary: bool = False,
) -> Path:
    """Start, resume or skip one run; returns its directory."""
    run_dir = ctx.runs_root / run_id
    if (run_dir / "summary.json").is_file():
        logger.info("run finished earlier", extra={"run_id": run_id})
        return run_dir
    if stage == "E0b":
        ctx.guard()
    elif stage == "E0c":
        ctx.guard_e0c()
    elif stage == "E0d":
        ctx.guard_e0d()
    snapshot = ctx.seed_snapshot()
    task_ids = tuple(t.id for t in tasks)
    sources = sorted({t.source_benchmark for t in tasks})
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
            workers=workers or ctx.spec.workers,
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
            judges=ctx.judges_block(sources),
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
    secondary = (
        ctx.secondary_scorers(provider, ledger, arm=spec.arm, sources=sources)
        if with_secondary
        else {}
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
            secondary_scorers=secondary,
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
    schedule: Sequence[int] = SCHEDULE,
    priority: Callable[[AlignmentRecord], float] | None = None,
) -> Path | None:
    """Reference run -> genuineness -> alignment -> replay -> signal -> cluster -> corrupt ->
    leakage for one normal run; every step skipped when its output exists. Returns the
    reference run dir, or None when the run had no harness failure. ``schedule`` is the
    replay schedule (E0c: ``(3,)``, confirmation later for the clusters entering E2);
    ``priority`` orders the failures so a budget cap cuts the least valuable first."""
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
        workers=ctx.spec.workers_for(failures[0].source_benchmark),
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
    guard: Callable[[], None]
    if stage == "E0b":
        guard = ctx.guard
    elif stage == "E0c":
        guard = ctx.guard_e0c
    else:
        guard = lambda: None  # noqa: E731
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
    # the reference run is complete: make it read-only so a replayed policy command with an
    # absolute path cannot delete it (happened to e0b-b1-claw_eval-p1-ref on 2026-09-07)
    lock_tree(ref_dir)
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
    workers = ctx.spec.workers_for(failures[0].source_benchmark)
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
                workers=workers,
                before_each=guard,
                schedule=schedule,
                priority=priority,
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
                workers=workers,
                before_each=guard,
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


# ---------------------------------------------------------------- E0b pre-flight


class PreflightResult(StrictModel):
    ok: bool
    p1_libreoffice_version: str | None
    p1_image_grading_attempted: dict[str, bool]
    p1_vision_probe: dict[str, JsonValue]
    p2_splits_path: str
    p2_splits_sha256: str
    p2_counts: dict[str, dict[str, int]]
    problems: tuple[str, ...]


def vision_probe(ctx: E0Context) -> dict[str, JsonValue]:
    """One multimodal call at temperature 0 to the secondary judge model (P1)."""
    models = sorted(set(ctx.spec.judges.secondary.values()))
    ledger = Ledger(ctx.runs_root / "preflight.ledger.jsonl", "e0-preflight")
    provider = ctx.provider(ledger)
    results: dict[str, JsonValue] = {}
    uri = "data:image/png;base64," + base64.b64encode(_PROBE_PNG).decode()
    for model in models:
        request = ChatRequest(
            model=model,
            messages=(
                ChatMessage(
                    role="user",
                    content=(
                        {
                            "type": "text",
                            "text": (
                                "Reply with the single word: pong. "
                                "Then name the colour of the image."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": uri}},
                    ),
                ),
            ),
            temperature=0.0,
            max_tokens=32,
            timeout_s=120.0,
            use_cache=False,
            attribution=Attribution(arm="preflight", unit_id="vision_probe"),
        )
        try:
            response = provider.complete(request)
            results[model] = to_json_value(
                {
                    "ok": True,
                    "content": response.content[:120],
                    "finish_reason": response.finish_reason,
                    "usage": response.usage.model_dump(),
                    "temperature": 0.0,
                }
            )
        except InfraError as exc:
            results[model] = to_json_value({"ok": False, "error": f"{exc.kind}: {exc}"[:300]})
    return results


def preflight(ctx: E0Context) -> PreflightResult:
    """P1 and P2 of the owner's E0b decisions; refuses E0b when a check fails."""
    problems: list[str] = []
    p1 = ctx.spec.block("preflight").get("P1_gdpval_judge")
    expected_lo = str(p1.get("pilot_libreoffice_version", "")) if isinstance(p1, dict) else ""
    pilot_gdpval = ctx.runs_root / "e0a-gdpval"
    lo_version: str | None = None
    attempted: dict[str, bool] = {}
    if (pilot_gdpval / "manifest.json").is_file():
        manifest = read_manifest(pilot_gdpval / "manifest.json")
        lo_version = manifest.environment.libreoffice_version
        if not lo_version or not lo_version.startswith(f"LibreOffice {expected_lo}"):
            problems.append(f"pilot libreoffice_version {lo_version!r} != {expected_lo}")
        for score_path in sorted(pilot_gdpval.glob("rollouts/*/*/score.json")):
            raw = read_json(score_path)
            detail = (
                raw.get("judge_meta", {}).get("judge_detail", {}) if isinstance(raw, dict) else {}
            )
            grading = detail.get("image_grading", {}) if isinstance(detail, dict) else {}
            flag = bool(grading.get("attempted")) if isinstance(grading, dict) else False
            attempted[score_path.parts[-3]] = flag
            if not flag:
                problems.append(f"pilot {score_path.parts[-3]}: image_grading.attempted is false")
    else:
        problems.append("pilot gdpval run missing; P1 cannot be confirmed")
    probe = vision_probe(ctx)
    for model, outcome in probe.items():
        if not (isinstance(outcome, dict) and outcome.get("ok")):
            problems.append(f"vision probe failed for {model}")
    p2 = ctx.spec.block("preflight").get("P2_splits")
    p2d = p2 if isinstance(p2, dict) else {}
    splits = build_splits(
        ctx.taskset("validation"),
        ctx.taskset("evaluation"),
        sources=ctx.spec.sources,
        eval_dev_per_source=int(str(p2d.get("eval_dev_per_source", 24))),
        heldout_per_source=int(str(p2d.get("heldout_per_source", 30))),
        seed=int(str(p2d.get("seed", 0))),
    )
    path, sha = freeze(splits, Path(str(p2d.get("file", SPLITS_PATH))))
    ctx.splits_sha256 = sha
    result = PreflightResult(
        ok=not problems,
        p1_libreoffice_version=lo_version,
        p1_image_grading_attempted=attempted,
        p1_vision_probe=probe,
        p2_splits_path=str(path),
        p2_splits_sha256=sha,
        p2_counts={
            s: {
                "validation": len(v.validation),
                "eval_dev": len(v.eval_dev),
                "heldout": len(v.heldout),
            }
            for s, v in splits.sources.items()
        },
        problems=tuple(problems),
    )
    atomic_write_text(ctx.runs_root / "preflight.json", result.model_dump_json(indent=2) + "\n")
    ctx.log_stage("E0b", "-", "preflight", ok=result.ok, problems=list(problems))
    return result


def require_preflight(ctx: E0Context) -> PreflightResult:
    path = ctx.runs_root / "preflight.json"
    if not path.is_file():
        raise ConfigError("E0b needs a passed pre-flight: run `e0_run.py E0b --preflight` first")
    result = PreflightResult.model_validate(read_json(path))
    if not result.ok:
        raise ConfigError(f"pre-flight failed: {list(result.problems)}")
    if result.p2_splits_sha256 != sha256_file(Path(result.p2_splits_path)):
        raise ConfigError("experiments/splits_v1.json changed after pre-flight")
    ctx.splits_sha256 = result.p2_splits_sha256
    return result


# ---------------------------------------------------------------- E0b stages


def _int(value: object, default: int) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else default


def b1_tasks(ctx: E0Context, splits: Splits, source: str) -> list[Task]:
    """Owner scope: claw_eval/gdpval mining pool; hle validation; browsecomp 10 validation
    tasks (seed 0)."""
    scope = ctx.spec.scope(source)
    kind = str(scope.get("B1", "validation"))
    all_tasks = ctx.all_tasks()
    if kind == "mining_pool":
        ids = splits.mining_pool(source)
    elif kind == "validation":
        ids = splits.sources[source].validation
    elif kind == "validation_sample":
        validation = ctx.taskset("validation").select(sources=[source])
        sample = sample_per_source(
            validation, per_source=_int(scope.get("B1_n"), 10), seed=_int(scope.get("B1_seed"), 0)
        )
        ids = sample.ids()
    else:
        raise ConfigError(f"unknown B1 scope {kind!r} for {source}")
    return [all_tasks.by_id(t) for t in ids]


def b1_baseline(ctx: E0Context, splits: Splits) -> dict[str, list[Path]]:
    dirs: dict[str, list[Path]] = {}
    for source in ctx.spec.sources:
        scope = ctx.spec.scope(source)
        passes = _int(scope.get("passes"), 2)
        tasks = b1_tasks(ctx, splits, source)
        dirs[source] = [
            run_or_resume(
                ctx,
                run_id=f"e0b-b1-{source}-p{p}",
                tasks=tasks,
                mode="normal",
                replicates=trials_for(source),
                stage="E0b",
                workers=ctx.spec.workers_for(source),
                with_secondary=bool(scope.get("secondary_judge")),
            )
            for p in range(1, passes + 1)
        ]
    return dirs


def b2_heldout(ctx: E0Context, splits: Splits) -> dict[str, list[Path]]:
    dirs: dict[str, list[Path]] = {}
    all_tasks = ctx.all_tasks()
    for source in ctx.spec.sources:
        scope = ctx.spec.scope(source)
        if str(scope.get("B2", "none")) != "heldout":
            ctx.log_stage("E0b", f"e0b-b2-{source}", "skipped_by_scope")
            continue
        passes = _int(scope.get("passes"), 2)
        tasks = [all_tasks.by_id(t) for t in splits.sources[source].heldout]
        dirs[source] = [
            run_or_resume(
                ctx,
                run_id=f"e0b-b2-{source}-p{p}",
                tasks=tasks,
                mode="normal",
                replicates=trials_for(source),
                stage="E0b",
                workers=ctx.spec.workers_for(source),
                with_secondary=bool(scope.get("secondary_judge")),
            )
            for p in range(1, passes + 1)
        ]
    return dirs


def full_arms_subset(ctx: E0Context, run_dirs: Sequence[Path]) -> dict[str, tuple[str, ...]]:
    """30 failures, seed 0, stratified by source (all failures when fewer); keys per run."""
    block = ctx.spec.block("B5_replay")
    raw_subset = block.get("full_arms_subset")
    subset: dict[str, Any] = raw_subset if isinstance(raw_subset, dict) else {}
    n = _int(subset.get("n"), 30)
    seed = _int(subset.get("seed"), 0)
    per_source: dict[str, list[tuple[Path, str]]] = {}
    for run_dir in run_dirs:
        for f in task_failures(run_dir):
            # keys in the alignment/replay failure_key format (task/replicate/attempt)
            per_source.setdefault(f.source_benchmark, []).append(
                (run_dir, f"{f.task_id}/{f.replicate}/{f.attempt}")
            )
    total = sum(len(v) for v in per_source.values())
    chosen: dict[str, list[str]] = {}
    if total <= n:
        for items in per_source.values():
            for run_dir, key in items:
                chosen.setdefault(run_dir.name, []).append(key)
        return {k: tuple(sorted(v)) for k, v in chosen.items()}
    quota = n // max(1, len(per_source))
    leftovers: list[tuple[Path, str]] = []
    for source in sorted(per_source):
        items = sorted(per_source[source], key=lambda x: (x[0].name, x[1]))
        random.Random(f"{seed}:{source}").shuffle(items)
        for run_dir, key in items[:quota]:
            chosen.setdefault(run_dir.name, []).append(key)
        leftovers.extend(items[quota:])
    taken = sum(len(v) for v in chosen.values())
    for run_dir, key in sorted(leftovers, key=lambda x: (x[0].name, x[1]))[: max(0, n - taken)]:
        chosen.setdefault(run_dir.name, []).append(key)
    return {k: tuple(sorted(v)) for k, v in chosen.items()}


def b3_to_b6(ctx: E0Context, b1_dirs: dict[str, list[Path]]) -> None:
    """References, alignment, replay (+ full-arms subset), clusters, corruption, leakage over
    every B1 run of a source whose scope has ``diagnosis: true``."""
    eligible = [
        d
        for source, dirs in b1_dirs.items()
        if bool(ctx.spec.scope(source).get("diagnosis"))
        for d in dirs
    ]
    for source in ctx.spec.sources:
        if not bool(ctx.spec.scope(source).get("diagnosis")):
            ctx.log_stage(
                "E0b",
                f"e0b-b1-{source}",
                "no_diagnosis_by_scope",
                note=str(ctx.spec.scope(source).get("note", "")),
            )
    subset = full_arms_subset(ctx, eligible)
    taskset = ctx.all_tasks()
    for run_dir in eligible:
        ctx.guard()
        diagnose_run(
            ctx,
            run_dir,
            stage="E0b",
            taskset=taskset,
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
    original_secondary_passed: bool | None
    original_secondary_value: float | None
    text_bypass_passed: bool | None
    text_bypass_value: float | None
    vision_bypass_passed: bool | None
    vision_bypass_value: float | None
    error: str | None = None


def _rebuild_workspace(ctx: E0Context, record: RolloutRecord) -> Path:
    """A judge-only workspace: ``outputs/`` from the rollout's ``artifacts/`` copy."""
    ws = (
        ctx.runs_root
        / "b7_workspaces"
        / record.rollout_dir.parent.parent.parent.name
        / record.task_id
        / f"{record.replicate}-a{record.attempt}"
    )
    outputs = ws / "outputs"
    if not outputs.is_dir():
        outputs.mkdir(parents=True, exist_ok=True)
        artifacts = record.rollout_dir / "artifacts"
        if artifacts.is_dir():
            shutil.copytree(artifacts, outputs, dirs_exist_ok=True)
    return ws


def b7_judge_calibration(ctx: E0Context, run_dirs: Sequence[Path]) -> Path:
    """Owner P1/B7: 50 scored artifacts pooled from claw_eval and gdpval; every artifact is
    re-judged by the text judge with the cache bypassed (self-consistency); gdpval artifacts
    also by the vision judge with the cache bypassed. Text-vs-vision agreement over ALL
    gdpval artifacts comes from the dual scores recorded in B1/B2 (report side)."""
    block = ctx.spec.block("B7_judge_calibration")
    n = _int(block.get("n_artifacts"), 50)
    pooled_raw = block.get("pooled_from")
    pooled = (
        [str(x) for x in pooled_raw] if isinstance(pooled_raw, list) else ["claw_eval", "gdpval"]
    )
    candidates: dict[str, list[tuple[Path, RolloutRecord]]] = {s: [] for s in pooled}
    for run_dir in run_dirs:
        for marker in sorted(run_dir.glob("rollouts/*/*/done.json")):
            record = RolloutRecord.model_validate(read_json(marker))
            if (
                record.source_benchmark in candidates
                and (marker.parent / "score.json").is_file()
                and record.error_family != "infra"
            ):
                candidates[record.source_benchmark].append((run_dir, record))
    chosen: list[tuple[Path, RolloutRecord]] = []
    quota = max(1, n // max(1, len(candidates)))
    for source in sorted(candidates):
        items = sorted(candidates[source], key=lambda x: (x[0].name, x[1].task_id, x[1].replicate))
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
    text_bypass = Scorer(
        judge=ctx.judge(
            provider,
            ctx.config.judge.model_copy(update={"use_cache": False}),
            arm="judge_calibration_text",
        ),
        ledger=ledger,
        arm="judge_calibration",
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    vision_scorers: dict[str, Scorer] = {}
    for source in pooled:
        cfg = ctx.vision_judge_config(source)
        if cfg is not None:
            vision_scorers[source] = Scorer(
                judge=ctx.judge(
                    provider,
                    cfg.model_copy(update={"use_cache": False}),
                    arm="judge_calibration_vision",
                ),
                ledger=ledger,
                arm="judge_calibration",
                seed=ctx.config.seed,
                claw_repo=ctx.claw_repo,
            )
    taskset = ctx.all_tasks()
    for run_dir, record in chosen:
        key = f"{run_dir.name}/{record.task_id}/{record.replicate}/{record.attempt}"
        if key in done:
            continue
        ctx.guard()
        task = taskset.by_id(record.task_id)
        original = read_json(record.rollout_dir / "score.json")
        assert isinstance(original, dict)
        secondary = original.get("secondary_judge")
        sec = secondary if isinstance(secondary, dict) else {}
        workspace = _rebuild_workspace(ctx, record)
        artifacts = Artifacts(
            workspace=workspace,
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
            original_secondary_passed=sec.get("passed")
            if isinstance(sec.get("passed"), bool)
            else None,
            original_secondary_value=float(str(sec["value"]))
            if isinstance(sec.get("value"), int | float)
            else None,
            text_bypass_passed=None,
            text_bypass_value=None,
            vision_bypass_passed=None,
            vision_bypass_value=None,
        )
        try:
            t = text_bypass.score(task, artifacts)
            row = row.model_copy(
                update={"text_bypass_passed": t.passed, "text_bypass_value": t.value}
            )
            vision = vision_scorers.get(record.source_benchmark)
            if vision is not None:
                v = vision.verdict(task, artifacts)
                row = row.model_copy(
                    update={
                        "vision_bypass_passed": v.passed,
                        "vision_bypass_value": v.value,
                        "error": v.error,
                    }
                )
        except InfraError as exc:
            row = row.model_copy(update={"error": f"{exc.kind}: {exc}"[:300]})
        done[key] = row
        atomic_write_text(
            out, json.dumps([r.model_dump(mode="json") for r in done.values()], indent=2) + "\n"
        )
    return out


def pass_runs(
    runs_root: Path,
    block: str,
    source: str,
    *,
    reference: bool = False,
    marker: str = "manifest.json",
    prefix: str = "e0b",
) -> list[Path]:
    """Runs ``e0b-<block>-<source>-p<n>`` (``...-p<n>-ref`` when ``reference``) that have
    ``marker``, in pass order. A plain ``p*`` glob also matches the ``-ref`` runs, which are
    reference-mode retries of the failed tasks, not passes: B7 once pooled artifacts from one."""
    suffix = "-ref" if reference else ""
    pattern = re.compile(
        rf"{re.escape(prefix)}-{re.escape(block)}-{re.escape(source)}-p(\d+){suffix}"
    )
    found: list[tuple[int, Path]] = []
    for d in runs_root.glob(f"{prefix}-{block}-{source}-p*{suffix}"):
        m = pattern.fullmatch(d.name)
        if m is not None and (d / marker).is_file():
            found.append((int(m.group(1)), d))
    return [d for _, d in sorted(found)]


def e0b(ctx: E0Context, *, stages: Sequence[str] = ("B1", "B2", "B3-6", "B7")) -> None:
    require_preflight(ctx)
    splits = load_splits()
    b1_dirs: dict[str, list[Path]] = {}
    if "B1" in stages:
        b1_dirs = b1_baseline(ctx, splits)
    if "B2" in stages:
        b2_heldout(ctx, splits)
    if not b1_dirs:
        b1_dirs = {
            s: pass_runs(ctx.runs_root, "b1", s, marker="summary.json") for s in ctx.spec.sources
        }
    if "B3-6" in stages:
        b3_to_b6(ctx, b1_dirs)
    if "B7" in stages:
        dirs = [d for dirs in b1_dirs.values() for d in dirs]
        b7_judge_calibration(ctx, dirs)


# ---------------------------------------------------------------- E0d (M3.2 addendum)


def diagnosed_runs(ctx: E0Context) -> dict[str, list[tuple[Path, Path]]]:
    """source -> [(run dir, reference run dir)] for every E0b B1 pass with a finished diagnosis."""
    out: dict[str, list[tuple[Path, Path]]] = {}
    for source in ctx.spec.sources:
        if not bool(ctx.spec.scope(source).get("diagnosis")):
            continue
        for run_dir in pass_runs(
            ctx.runs_root, "b1", source, marker="summary.json", prefix=ctx.spec.run_prefix
        ):
            ref_dir = ctx.runs_root / f"{run_dir.name}-ref"
            if (run_dir / "diagnosis" / "clusters.json").is_file() and ref_dir.is_dir():
                out.setdefault(source, []).append((run_dir, ref_dir))
    return out


def _e0d_llm(ctx: E0Context, ledger: Ledger, *, arm: str = "diagnosis") -> DiagnosisLLM:
    return DiagnosisLLM(
        ctx.provider(ledger),
        config=DiagnosisModelConfig(model=ctx.config.judge.model),
        seed=ctx.config.seed,
        arm=arm,
    )


def e0d_b_escalate(ctx: E0Context, run_dir: Path, ref_dir: Path, *, taskset: TaskSet) -> None:
    """E0d-B: re-open the marginal replay verdicts of one run through the M3.2 schedule, then
    re-chain signal -> cluster -> corrupt -> leakage (cached calls; only diagnoses whose oracle
    step changed are regenerated). Pre-M3.2 files are kept as ``*.pre_m32.json``."""
    out = diagnosis_dir(run_dir)
    marker = out / "escalation_done.json"
    if marker.is_file():
        ctx.log_stage("E0d", run_dir.name, "escalation_skipped", reason="done earlier")
        return
    rows = _escalate_run(ctx, run_dir, ref_dir, taskset=taskset, stage="E0d")
    atomic_write_text(
        marker,
        json.dumps({"ts": datetime.now(UTC).isoformat(), "escalated_failures": len(rows)}, indent=2)
        + "\n",
    )


def _escalate_run(
    ctx: E0Context,
    run_dir: Path,
    ref_dir: Path,
    *,
    taskset: TaskSet,
    stage: Stage,
    reopen_by_key: dict[str, Callable[[CandidateReplay], bool]] | None = None,
    backup_suffix: str = "pre_m32",
) -> list[EscalationRow]:
    """Escalate (E0d-B: marginal candidates; E0c: the given failures and predicates) and
    re-chain signal -> cluster -> corrupt -> leakage. Returns the escalation rows."""
    out = diagnosis_dir(run_dir)
    guard = ctx.guard_e0c if stage == "E0c" else ctx.guard_e0d
    run_ctx, manifest = load_run_context(run_dir)
    assert manifest.harness_snapshot_id is not None and manifest.run_spec is not None
    ledger = Ledger(run_dir / "ledger.e0d.jsonl", run_ctx.run_id)
    provider = ctx.provider(ledger)
    spec = RunSpec.model_validate(manifest.run_spec)
    studied = SnapshotStore(run_dir / "harness").load(manifest.harness_snapshot_id)
    instrument = instrument_snapshot(run_dir, ctx.components)
    source = run_dir.name.split("-")[2]
    scorer = Scorer(
        judge=ctx.judge(provider),
        ledger=ledger,
        arm="replay",
        seed=ctx.config.seed,
        claw_repo=ctx.claw_repo,
    )
    ctx.log_stage(stage, run_dir.name, "escalation_start")
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
        rows = escalate_replays(
            run_dir,
            ref_dir,
            runner=runner,
            spec=spec,
            studied=studied,
            instrument=instrument,
            taskset=taskset,
            max_candidates=ctx.spec.replay.max_candidates,
            economize=ctx.spec.replay.economize,
            workers=ctx.spec.workers_for(source),
            before_each=guard,
            reopen_by_key=reopen_by_key,
            backup_suffix=backup_suffix,
        )
    ctx.log_stage(
        stage,
        run_dir.name,
        "escalation_done",
        failures=len(rows),
        type_changed=sum(1 for r in rows if r.failure_type_before != r.failure_type_after),
        oracle_changed=sum(1 for r in rows if r.oracle_step_before != r.oracle_step_after),
        usd=round(sum(r.usd for r in rows), 4),
    )
    for name in ("diagnoses.json", "clusters.json", "activity.json", "leakage.json"):
        src, dst = out / name, out / name.replace(".json", f".{backup_suffix}.json")
        if src.is_file() and not dst.is_file():
            shutil.copyfile(src, dst)
    llm = _e0d_llm(ctx, ledger)
    signal_failures(
        run_dir,
        ref_dir,
        taskset=taskset,
        manifest=ctx.components,
        harness_snapshot_id=studied.snapshot_id,
        llm=llm,
        prompts=load_prompts(),
    )
    cluster_run(
        run_dir,
        manifest=ctx.components,
        reference_run=ref_dir.name,
        instrument_snapshot_id=instrument.snapshot_id,
    )
    corrupt_run(run_dir, seed=ctx.spec.corruption_seed(), manifest=ctx.components)
    leakage_run(run_dir, manifest=ctx.components, llm=llm, prompt_template=leakage.load_prompt())
    ctx.log_stage(stage, run_dir.name, "rechain_done")
    return rows


def e0d_c_coherent(
    ctx: E0Context,
    runs_by_source: dict[str, list[tuple[Path, Path]]],
    *,
    stage: Stage = "E0d",
) -> None:
    """E0d-C: COH-WRONG texts for every cluster with >= min_members, parity pooled per source,
    regeneration with a new variant seed on violation (at most max_regenerations times)."""
    block = ctx.spec.e0d_block("C_coherent_wrong")
    min_members = _int(block.get("min_members"), 2)
    max_regenerations = _int(block.get("max_regenerations"), 3)
    seed = ctx.spec.corruption_seed()
    llm = _e0d_llm(ctx, ctx.e0d_ledger())
    prompts = coherent.load_prompts()
    vocabulary = load_causes()
    for source, runs in runs_by_source.items():
        summary_path = ctx.runs_root / f"e0d_parity_{source}.json"
        if summary_path.is_file() and all(
            (rec := load_coherent_set(run_dir, seed)) is not None
            and rec.final_generation_seed is not None
            and rec.parity_scope == "source"
            for run_dir, _ in runs
        ):
            ctx.log_stage(
                stage,
                f"{ctx.spec.run_prefix}-b1-{source}",
                "coherent_skipped",
                reason="done earlier",
            )
            continue
        rounds: list[dict[str, JsonValue]] = []
        pooled = None
        generation_seed = 0
        guard = ctx.guard_e0c if stage == "E0c" else ctx.guard_e0d
        for generation_seed in range(max_regenerations + 1):
            guard()
            scores: list[PlausibilityScore] = []
            for run_dir, _ in runs:
                record = coherent_round(
                    run_dir,
                    seed=seed,
                    generation_seed=generation_seed,
                    manifest=ctx.components,
                    llm=llm,
                    prompts=prompts,
                    vocabulary=vocabulary,
                    min_members=min_members,
                )
                scores.extend(record.rounds[-1].scores)
            pooled = parity(scores)
            rounds.append(
                {"generation_seed": generation_seed, "parity": to_json_value(pooled.model_dump())}
            )
            ctx.log_stage(
                stage,
                f"{ctx.spec.run_prefix}-b1-{source}",
                "coherent_round",
                generation_seed=generation_seed,
                n=pooled.n,
                mean_difference=pooled.mean_difference,
                sign_test_p=pooled.sign_test_p,
                ok=pooled.ok,
            )
            if pooled.ok:
                break
        assert pooled is not None
        for run_dir, _ in runs:
            finalize_coherent(
                run_dir,
                seed=seed,
                generation_seed=generation_seed,
                parity_ok=pooled.ok,
                scope="source",
                manifest=ctx.components,
            )
        atomic_write_text(
            summary_path,
            json.dumps(
                {
                    "source": source,
                    "seed": seed,
                    "min_members": min_members,
                    "rounds": rounds,
                    "final_generation_seed": generation_seed,
                    "parity_ok": pooled.ok,
                    "persistent_violation": not pooled.ok,
                },
                indent=2,
            )
            + "\n",
        )


def e0d_d_probe(
    ctx: E0Context,
    runs_by_source: dict[str, list[tuple[Path, Path]]],
    *,
    taskset: TaskSet,
    splits: Splits,
) -> None:
    """E0d-D: the privileged-information probe over REF / SELF / SHUF / COH-WRONG renders."""
    seed = ctx.spec.corruption_seed()
    block = ctx.spec.e0d_block("D_pi_probe")
    raw_arms = block.get("arms")
    arms = tuple(str(a) for a in raw_arms) if isinstance(raw_arms, list) else PROBE_ARMS
    llm = _e0d_llm(ctx, ctx.e0d_ledger(), arm=probe_module.PROBE_ARM)
    for source, runs in runs_by_source.items():
        pool_ids = [*splits.sources[source].validation, *splits.sources[source].eval_dev]
        for run_dir, ref_dir in runs:
            probe_path = diagnosis_dir(run_dir) / f"probe-s{seed}.json"
            clusters_path = diagnosis_dir(run_dir) / "clusters.json"
            if probe_path.is_file() and probe_path.stat().st_mtime >= clusters_path.stat().st_mtime:
                ctx.log_stage("E0d", run_dir.name, "probe_skipped", reason="done earlier")
                continue
            ctx.guard_e0d()
            report = probe_run(
                run_dir,
                ref_dir,
                seed=seed,
                taskset=taskset,
                pool_ids=pool_ids,
                llm=llm,
                prompt_template=probe_module.load_prompt(),
                arms=arms,
            )
            ctx.log_stage("E0d", run_dir.name, "probe_done", records=len(report.records))


def e0d_a_seed_noise(ctx: E0Context, splits: Splits) -> None:
    """E0d-A: more held-out passes of the seed harness (continuing the B2 numbering)."""
    block = ctx.spec.e0d_block("A_seed_noise")
    raw_totals = block.get("passes_total")
    totals = raw_totals if isinstance(raw_totals, dict) else {}
    all_tasks = ctx.all_tasks()
    for source in ctx.spec.sources:
        if source not in totals:
            continue
        total = _int(totals[source], 2)
        scope = ctx.spec.scope(source)
        tasks = [all_tasks.by_id(t) for t in splits.sources[source].heldout]
        for p in range(1, total + 1):
            run_or_resume(
                ctx,
                run_id=f"e0b-b2-{source}-p{p}",
                tasks=tasks,
                mode="normal",
                replicates=trials_for(source),
                stage="E0d",
                workers=ctx.spec.workers_for(source),
                with_secondary=bool(scope.get("secondary_judge")),
            )


def e0d(ctx: E0Context, *, stages: Sequence[str] | None = None) -> None:
    """The M3.2 calibration addendum in the spec's order (B, C, D, A by default); every stage
    checks the E0d hard cap before spending and is resumable."""
    require_preflight(ctx)
    splits = load_splits()
    taskset = ctx.all_tasks()
    runs_by_source = diagnosed_runs(ctx)
    for stage in stages or ctx.spec.e0d_order():
        if stage == "B":
            for runs in runs_by_source.values():
                for run_dir, ref_dir in runs:
                    ctx.guard_e0d()
                    e0d_b_escalate(ctx, run_dir, ref_dir, taskset=taskset)
        elif stage == "C":
            e0d_c_coherent(ctx, runs_by_source)
        elif stage == "D":
            e0d_d_probe(ctx, runs_by_source, taskset=taskset, splits=splits)
        elif stage == "A":
            e0d_a_seed_noise(ctx, splits)
        else:
            raise ConfigError(f"unknown E0d stage {stage!r}; known: A B C D")
    ctx.log_stage("E0d", "-", "e0d_done", spent_usd=round(ctx.spend_e0d(), 4))


# ---------------------------------------------------------------- E0c (minimal, owner 2026-09-09)


def e0c_preflight(ctx: E0Context) -> dict[str, JsonValue]:
    """Freeze splits_v2 (v1 plus eval_rest; validation, eval_dev and heldout untouched) at the
    spec's splits path and record the condition."""
    v1 = load_splits(SPLITS_PATH)
    v2 = with_eval_rest(v1, ctx.taskset("evaluation"))
    path, sha = freeze(v2, ctx.splits_path)
    ctx.splits_sha256 = sha
    record: dict[str, JsonValue] = {
        "ts": datetime.now(UTC).isoformat(),
        "ok": True,
        "condition": str(ctx.spec.policy.get("reasoning_effort")),
        "spec_sha256": ctx.spec_sha256,
        "splits_path": str(path),
        "splits_sha256": sha,
        "eval_rest_per_source": {s: len(v.eval_rest) for s, v in v2.sources.items()},
    }
    atomic_write_text(ctx.runs_root / "preflight.json", json.dumps(record, indent=2) + "\n")
    return record


def e0c_failure_priority(run_dirs: Sequence[Path]) -> Callable[[AlignmentRecord], float]:
    """Failures of tasks that failed most often across the mining passes first: those are
    the likeliest to form clusters with >= 2 members, so a budget cap cuts singletons."""
    counts: dict[str, int] = {}
    for d in run_dirs:
        for f in task_failures(d):
            counts[f.task_id] = counts.get(f.task_id, 0) + 1

    def priority(record: AlignmentRecord) -> float:
        return float(counts.get(record.task_id, 0))

    return priority


def e0c_confirm_targets(
    run_dirs: Sequence[Path], *, min_members: int
) -> dict[Path, dict[str, Callable[[CandidateReplay], bool]]]:
    """Per run, the failures (and candidate predicates) to confirm at n >= 5: for every cluster
    key with >= min_members members across the mining passes, the per-run representative's
    unconfirmed candidates (oracle and placement steps) and each other member's oracle-step
    candidate."""
    merged: dict[tuple[str, str], list[tuple[Path, str, bool]]] = {}
    for d in run_dirs:
        if not (diagnosis_dir(d) / "clusters.json").is_file():
            continue
        clusters, _ = load_clusters(d)
        for c in clusters.clusters:
            for member in c.members:
                merged.setdefault((c.cause_label, c.component), []).append(
                    (d, member, member == c.representative)
                )
    targets: dict[Path, dict[str, Callable[[CandidateReplay], bool]]] = {}
    for members in merged.values():
        if len(members) < min_members:
            continue
        for d, member, is_representative in members:
            task_id, replicate, attempt = member.rsplit("/", 2)
            key = safe_key(task_id, replicate, int(attempt))
            replay = load_replays(d).get(key)
            if replay is None:
                continue
            predicate: Callable[[CandidateReplay], bool] = (
                unconfirmed if is_representative else _oracle_only(replay.oracle_step)
            )
            if any(predicate(c) for c in replay.candidates):
                targets.setdefault(d, {})[key] = predicate
    return targets


def _oracle_only(oracle: int | None) -> Callable[[CandidateReplay], bool]:
    def predicate(c: CandidateReplay) -> bool:
        return unconfirmed(c) and c.step == oracle

    return predicate


def e0c_confirm(
    ctx: E0Context,
    runs: Sequence[tuple[Path, Path]],
    *,
    taskset: TaskSet,
    min_members: int,
    max_rounds: int,
) -> None:
    """Owner (E0c): oracle steps and corruption-placement steps are confirmed at n >= 5 ONLY
    for clusters entering E2. Rounds repeat while a confirmation changes the clusters."""
    for round_index in range(max_rounds):
        targets = e0c_confirm_targets([d for d, _ in runs], min_members=min_members)
        ctx.log_stage(
            "E0c",
            "-",
            "confirm_round",
            round=round_index,
            failures=sum(len(v) for v in targets.values()),
        )
        if not targets:
            break
        for run_dir, ref_dir in runs:
            reopen_by_key = targets.get(run_dir)
            if not reopen_by_key:
                continue
            ctx.guard_e0c()
            _escalate_run(
                ctx,
                run_dir,
                ref_dir,
                taskset=taskset,
                stage="E0c",
                reopen_by_key=reopen_by_key,
                backup_suffix="pre_confirm",
            )


def e0c(ctx: E0Context, *, stages: Sequence[str] | None = None) -> None:
    """E0c (minimal): low reasoning effort on the v2 mining pool (validation, eval_dev,
    eval_rest), 3 mining passes, 4 held-out passes, references -> genuineness -> alignment ->
    first-stage replay -> clusters -> confirmation for the clusters entering E2 -> COH-WRONG
    parity -> feasibility. Every stage checks the E0c cap."""
    e0c_preflight(ctx)
    splits = load_splits(ctx.splits_path)
    all_tasks = ctx.all_tasks()
    block = ctx.spec.E0c
    source = str(block.get("source", "claw_eval"))
    mining_passes = _int(block.get("mining_passes"), 3)
    heldout_passes = _int(block.get("heldout_passes"), 4)
    replay_block = ctx.spec.e0c_block("replay")
    first_stage = _int(replay_block.get("first_stage"), 3)
    max_rounds = _int(replay_block.get("max_confirm_rounds"), 3)
    min_members = _int(ctx.spec.e0d_block("C_coherent_wrong").get("min_members"), 2)
    prefix = ctx.spec.run_prefix
    order = tuple(stages) if stages else ("B1", "B2", "B3-6", "CONFIRM", "C")
    pool = [all_tasks.by_id(t) for t in splits.mining_pool(source, with_rest=True)]
    heldout = [all_tasks.by_id(t) for t in splits.sources[source].heldout]
    b1: list[Path] = []
    for stage_name in order:
        if stage_name == "B1":
            b1 = [
                run_or_resume(
                    ctx,
                    run_id=f"{prefix}-b1-{source}-p{p}",
                    tasks=pool,
                    mode="normal",
                    replicates=trials_for(source),
                    stage="E0c",
                    workers=ctx.spec.workers_for(source),
                )
                for p in range(1, mining_passes + 1)
            ]
        elif stage_name == "B2":
            for p in range(1, heldout_passes + 1):
                run_or_resume(
                    ctx,
                    run_id=f"{prefix}-b2-{source}-p{p}",
                    tasks=heldout,
                    mode="normal",
                    replicates=trials_for(source),
                    stage="E0c",
                    workers=ctx.spec.workers_for(source),
                )
        elif stage_name in ("B3-6", "CONFIRM", "C"):
            if not b1:
                b1 = pass_runs(ctx.runs_root, "b1", source, marker="summary.json", prefix=prefix)
            if stage_name == "B3-6":
                priority = e0c_failure_priority(b1)
                for run_dir in b1:
                    ctx.guard_e0c()
                    diagnose_run(
                        ctx,
                        run_dir,
                        stage="E0c",
                        taskset=all_tasks,
                        corruption_seed=ctx.spec.corruption_seed(),
                        schedule=(first_stage,),
                        priority=priority,
                    )
            else:
                runs = [
                    (d, ctx.runs_root / f"{d.name}-ref")
                    for d in b1
                    if (diagnosis_dir(d) / "clusters.json").is_file()
                ]
                if stage_name == "CONFIRM":
                    e0c_confirm(
                        ctx, runs, taskset=all_tasks, min_members=min_members, max_rounds=max_rounds
                    )
                else:
                    e0d_c_coherent(ctx, {source: runs}, stage="E0c")
        else:
            raise ConfigError(f"unknown E0c stage {stage_name!r}; known: B1 B2 B3-6 CONFIRM C")
    ctx.log_stage("E0c", "-", "e0c_done", spent_usd=round(ctx.spend_e0c(), 4))


__all__ = [
    "E0Context",
    "E0Spec",
    "PreflightResult",
    "b1_baseline",
    "b2_heldout",
    "b3_to_b6",
    "b7_judge_calibration",
    "diagnose_run",
    "diagnosed_runs",
    "e0b",
    "e0c",
    "e0c_confirm",
    "e0c_confirm_targets",
    "e0c_failure_priority",
    "e0c_preflight",
    "e0d",
    "e0d_a_seed_noise",
    "e0d_b_escalate",
    "e0d_c_coherent",
    "e0d_d_probe",
    "full_arms_subset",
    "load_spec",
    "pass_runs",
    "pilot",
    "pilot_tasks",
    "preflight",
    "require_preflight",
    "run_or_resume",
    "task_failures",
    "vision_probe",
]
