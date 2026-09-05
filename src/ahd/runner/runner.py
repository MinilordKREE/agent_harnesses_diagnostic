"""Runner: rollouts through Evo-Bench's worker in bounded parallel lanes, resumable.

No reference source: written fresh for ahd (see docs/reuse/M2.md). Uses Evo-Bench's
``prepare_task_workspace`` (Apache-2.0, imported) and drives ``evobench.policy.worker`` the
way ``adapter._run_task_local`` does. The harness tree is never modified: reference mode
appends a block to the public prompt, and the study-wide ``mock_today`` is injected into
``claw_public`` of the task dict, both runner-side.

M2.1: a *lane* is one (task, replicate); lanes run concurrently up to ``spec.workers``
(each lane is one worker subprocess plus its mock services). Every finished attempt writes
``done.json`` (its ``RolloutRecord``); ``run(..., resume=True)`` loads finished attempts
from their markers, re-runs unfinished ones, and skips scoring where ``score.json`` exists.
Ledger rows carry ``rollout_uid`` so a resumed run's summary ignores rows from attempts that
never reached their marker.

Per rollout under ``rollouts/<task>/<replicate>[/attempt_N]/``: Evo-Bench's own files
(``trajectory.json``, ``metadata.json``, ``rollout.log``, worker I/O, Claw trace files) plus
``trajectory.jsonl`` (M0 envelope), ``artifacts/`` (the workspace ``outputs/``), ``done.json``,
``score.json`` and ``failure.json``. Scores for normal mode are written only after all
replicates of a task finished; reference mode scores each attempt because the loop stops at
the first pass.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import traceback
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from pydantic import ValidationError

from ahd.core.config import RunConfig
from ahd.core.context import RunContext
from ahd.core.hashing import JsonValue, sha256_of, to_json_value
from ahd.core.io import atomic_write_text, read_json
from ahd.core.trace import TraceWriter
from ahd.errors import BudgetExhausted, ConfigError, InfraError
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, copy_snapshot
from ahd.harness.validate import require_valid
from ahd.llm.ledger import Ledger, read_ledger
from ahd.llm.pricing import PricingTable
from ahd.runner.records import (
    ErrorFamily,
    FailureFamily,
    FailureRecord,
    ReferenceRecord,
    RolloutRecord,
    RunResult,
    TaskResult,
)
from ahd.runner.reference import load_template, render_reference_block, with_reference
from ahd.runner.serper import count_serper_calls
from ahd.runner.spec import RunSpec
from ahd.runner.summary import aggregate_task, summarize_run
from ahd.runner.trajectory import (
    TrajectoryEvent,
    events_from_rollout_log,
    events_from_trajectory,
    reconcile_usage,
    usage_from_events,
)
from ahd.runner.worker import WorkerOutcome, build_request, invoke_worker
from ahd.settings import Settings
from ahd.tasks.models import Artifacts, Score, Task
from ahd.tasks.scorer import Scorer

logger = logging.getLogger(__name__)

BUDGET_EXIT_REASONS: frozenset[str] = frozenset({"max_steps", "rollout_wall_clock_timeout"})
INFRA_EXIT_REASONS: frozenset[str] = frozenset({"model_call_error"})
ROLLOUTS_DIRNAME = "rollouts"
WORKSPACES_DIRNAME = "workspaces"
HARNESS_DIRNAME = "harness"
DONE_FILENAME = "done.json"
SCORE_FILENAME = "score.json"


class _Attribution(TypedDict):
    arm: str
    unit_id: str
    seed: int
    model: str


class Runner:
    def __init__(
        self,
        *,
        ctx: RunContext,
        config: RunConfig,
        settings: Settings,
        pricing: PricingTable,
        ledger: Ledger,
        scorer: Scorer,
        trace: TraceWriter,
        claw_repo: Path | None,
        reference_template: str | None = None,
        worker_python: str | None = None,
    ) -> None:
        self._ctx = ctx
        self._config = config
        self._settings = settings
        self._pricing = pricing
        self._ledger = ledger
        self._scorer = scorer
        self._trace = trace
        self._claw_repo = claw_repo
        self._reference_template = reference_template
        self._worker_python = worker_python

    # ---------------------------------------------------------------- public

    def run(
        self,
        spec: RunSpec,
        tasks: Sequence[Task],
        *,
        snapshot: HarnessSnapshot,
        resume: bool = False,
    ) -> RunResult:
        require_valid(snapshot.tree, budget=spec.budget)
        run_store = SnapshotStore(self._ctx.out_dir / HARNESS_DIRNAME)
        local = copy_snapshot(snapshot, run_store)
        if local.snapshot_id != spec.harness_snapshot_id:
            raise ConfigError(
                f"snapshot {local.snapshot_id} does not match spec {spec.harness_snapshot_id}"
            )
        for task in tasks:
            if task.excluded:
                raise ConfigError(f"task {task.id} is excluded: {task.exclusion_reason}")
        template = self._reference_template
        if spec.mode == "reference" and template is None:
            template = load_template()
        self._trace.write(
            "run_resumed" if resume else "run_spec",
            {**spec.manifest_view(), "resume": resume},
        )

        lanes = [(task, replicate) for task in tasks for replicate in spec.replicate_ids]
        by_task: dict[str, list[RolloutRecord]] = {task.id: [] for task in tasks}
        references: list[ReferenceRecord] = []
        with ThreadPoolExecutor(max_workers=spec.workers) as pool:
            futures = {
                pool.submit(self._run_lane, task, replicate, spec, local, template, resume): (
                    task,
                    replicate,
                )
                for task, replicate in lanes
            }
            for future in as_completed(futures):
                task, _replicate = futures[future]
                records, reference = future.result()
                by_task[task.id].extend(records)
                if reference is not None:
                    references.append(reference)
            if spec.mode == "normal":
                scoring = {
                    pool.submit(self._score_task, task, by_task[task.id], resume): task
                    for task in tasks
                }
                for score_future in as_completed(scoring):
                    scored_task = scoring[score_future]
                    by_task[scored_task.id] = score_future.result()

        results: list[TaskResult] = []
        failures: list[FailureRecord] = []
        for task in tasks:
            rollouts = sorted(by_task[task.id], key=lambda r: (r.replicate, r.attempt))
            result = aggregate_task(task, rollouts, k=len(spec.replicate_ids))
            results.append(result)
            failures.extend(self._failures(task, rollouts, spec=spec))
            if not spec.keep_workspaces:
                shutil.rmtree(self._ctx.out_dir / WORKSPACES_DIRNAME / task.id, ignore_errors=True)
            self._trace.write(
                "task_done",
                {
                    "task_id": task.id,
                    "pass_hat_k": result.pass_hat_k,
                    "pass_at_k": result.pass_at_k,
                    "mean_value": result.mean_value,
                    "infra": result.infra,
                    "budget": result.budget,
                },
            )
        references.sort(key=lambda r: (r.task_id, r.replicate))

        done_uids = {r.rollout_uid for rollouts in by_task.values() for r in rollouts}
        ledger_rows = read_ledger(self._ledger.path) if self._ledger.path.exists() else []
        summary = summarize_run(
            run_id=self._ctx.run_id,
            harness_snapshot_id=local.snapshot_id,
            mode=spec.mode,
            tasks=results,
            ledger_rows=[
                r for r in ledger_rows if r.rollout_uid is None or r.rollout_uid in done_uids
            ],
        )
        summary_path = self._ctx.out_dir / "summary.json"
        atomic_write_text(summary_path, _dumps(summary))
        atomic_write_text(
            self._ctx.out_dir / "failures.json",
            _dumps([f.model_dump(mode="json") for f in failures]),
        )
        if references:
            atomic_write_text(
                self._ctx.out_dir / "references.json",
                _dumps([r.model_dump(mode="json") for r in references]),
            )
        return RunResult(
            run_id=self._ctx.run_id,
            harness_snapshot_id=local.snapshot_id,
            mode=spec.mode,
            tasks=tuple(results),
            failures=tuple(failures),
            references=tuple(references),
            summary_path=summary_path,
        )

    # ---------------------------------------------------------------- lanes

    def _run_lane(
        self,
        task: Task,
        replicate: str,
        spec: RunSpec,
        snapshot: HarnessSnapshot,
        template: str | None,
        resume: bool,
    ) -> tuple[list[RolloutRecord], ReferenceRecord | None]:
        """All attempts of one (task, replicate); never raises (a crash becomes an infra record)."""
        try:
            if spec.mode == "normal":
                return [
                    self._attempt(task, replicate, 1, spec=spec, snapshot=snapshot, resume=resume)
                ], None
            assert template is not None
            block = render_reference_block(task, template=template, claw_repo=self._claw_repo)
            records: list[RolloutRecord] = []
            passing: int | None = None
            attempts = 0
            for attempt in range(1, spec.reference_max_attempts + 1):
                attempts = attempt
                record = self._attempt(
                    task,
                    replicate,
                    attempt,
                    spec=spec,
                    snapshot=snapshot,
                    resume=resume,
                    reference_block=block,
                )
                record = self._score(task, record, resume=resume)
                records.append(record)
                if record.score is not None and record.score.passed:
                    passing = attempt
                    break
            return records, ReferenceRecord(
                task_id=task.id,
                replicate=replicate,
                attempts=attempts,
                max_attempts=spec.reference_max_attempts,
                passing_attempt=passing,
            )
        except Exception as exc:
            logger.error(
                "lane crashed",
                extra={"task_id": task.id, "replicate": replicate, "error": str(exc)},
            )
            rollout_dir = self._rollout_dir(task, replicate, 1)
            rollout_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(rollout_dir / "runner_exception.txt", traceback.format_exc())
            crashed = RolloutRecord(
                rollout_uid=uuid.uuid4().hex[:12],
                task_id=task.id,
                source_benchmark=task.source_benchmark,
                replicate=replicate,
                attempt=1,
                mode=spec.mode,
                rollout_id=None,
                rollout_dir=rollout_dir,
                workspace_dir=self._ctx.out_dir / WORKSPACES_DIRNAME / task.id / replicate,
                final_answer="",
                exit_reason=None,
                steps=0,
                duration_seconds=0.0,
                started_at=datetime.now(UTC).isoformat(),
                usage=None,
                usd=None,
                pricing_tier=None,
                partial=True,
                error_family="infra",
                error_kind="runner_exception",
                error=f"{type(exc).__name__}: {exc}",
                score=None,
                serper_calls_approx=0,
                reasoning_steps=0,
            )
            return [crashed], None

    def _rollout_dir(self, task: Task, replicate: str, attempt: int) -> Path:
        suffix = "" if attempt == 1 else f"/attempt_{attempt}"
        return self._ctx.out_dir / ROLLOUTS_DIRNAME / task.id / f"{replicate}{suffix}"

    def _attempt(
        self,
        task: Task,
        replicate: str,
        attempt: int,
        *,
        spec: RunSpec,
        snapshot: HarnessSnapshot,
        resume: bool,
        reference_block: str | None = None,
    ) -> RolloutRecord:
        rollout_dir = self._rollout_dir(task, replicate, attempt)
        marker = rollout_dir / DONE_FILENAME
        if resume and marker.is_file():
            record = _load_record(marker)
            self._trace.write(
                "rollout_reused", {"task_id": task.id, "replicate": replicate, "attempt": attempt}
            )
            return record
        if rollout_dir.exists():
            shutil.rmtree(rollout_dir)  # an unfinished attempt: start over
        record = self._rollout(
            task,
            replicate,
            attempt,
            spec=spec,
            snapshot=snapshot,
            rollout_dir=rollout_dir,
            reference_block=reference_block,
        )
        self._write_done_marker(record)
        return record

    def _write_done_marker(self, record: RolloutRecord) -> None:
        atomic_write_text(
            record.rollout_dir / DONE_FILENAME,
            record.model_copy(update={"score": None}).model_dump_json(indent=2) + "\n",
        )

    # ---------------------------------------------------------------- rollout

    def _worker_env(self, spec: RunSpec) -> dict[str, str]:
        env = dict(os.environ)
        env["EVOBENCH_EXECUTION_MODE"] = "local"
        env.pop("EVOBENCH_INJECTED_TOOLS_MANIFEST", None)
        if spec.policy.api_key_env == "DEEPSEEK_API_KEY":
            env["DEEPSEEK_API_KEY"] = self._settings.deepseek_api_key.get_secret_value()
        elif spec.policy.api_key_env not in env:
            raise ConfigError(f"policy api_key_env {spec.policy.api_key_env!r} is not set")
        if self._settings.serper_api_key is not None:
            env["SERPER_API_KEY"] = self._settings.serper_api_key.get_secret_value()
        if self._claw_repo is not None:
            env["EVOBENCH_CLAW_REPO"] = str(self._claw_repo)
        return env

    def _public_task(
        self, task: Task, *, spec: RunSpec, reference_block: str | None
    ) -> dict[str, Any]:
        public = task.public_view()
        if task.source_benchmark == "claw_eval" and spec.mock_today is not None:
            claw_public = public.get("claw_public")
            if isinstance(claw_public, dict) and claw_public.get("mock_today") is None:
                claw_public = dict(claw_public)
                claw_public["mock_today"] = spec.mock_today
                public["claw_public"] = claw_public
        if reference_block is not None:
            public["prompt"] = with_reference(str(public.get("prompt", "")), reference_block)
        return public

    def _rollout(
        self,
        task: Task,
        replicate: str,
        attempt: int,
        *,
        spec: RunSpec,
        snapshot: HarnessSnapshot,
        rollout_dir: Path,
        reference_block: str | None = None,
    ) -> RolloutRecord:
        from evobench.evaluation.tasks import prepare_task_workspace

        suffix = "" if attempt == 1 else f"/attempt_{attempt}"
        workspace = Path(
            prepare_task_workspace(
                {**task.to_evobench_dict(), "id": f"{task.id}/{replicate}{suffix}"},
                self._ctx.out_dir / WORKSPACES_DIRNAME,
            )
        )
        public = self._public_task(task, spec=spec, reference_block=reference_block)
        request = build_request(
            harness_dir=snapshot.tree,
            task=public,
            task_workspace=workspace,
            output_dir=rollout_dir,
            harness_revision=snapshot.meta.evobench_revision,
            model_config_id=spec.policy.model,
            model_config=spec.policy.to_evobench_dict(),
        )
        rollout_uid = uuid.uuid4().hex[:12]
        started_at = datetime.now(UTC)
        context: dict[str, JsonValue] = {
            "rollout_uid": rollout_uid,
            "task_id": task.id,
            "source_benchmark": task.source_benchmark,
            "replicate": replicate,
            "attempt": attempt,
            "mode": spec.mode,
            "harness_snapshot_id": snapshot.snapshot_id,
            "evobench_revision": snapshot.meta.evobench_revision,
            "model": spec.policy.model,
            "arm": spec.arm,
            "started_at": started_at.isoformat(),
            "mock_today": spec.mock_today,
        }
        self._trace.write("rollout_launch", {**context, "rollout_dir": str(rollout_dir)})
        outcome = invoke_worker(
            request=request,
            rollout_dir=rollout_dir,
            env=self._worker_env(spec),
            timeout_s=spec.budget.worker_timeout_seconds(spec.policy.timeout_seconds),
            python=self._worker_python,
        )
        return self._record(
            task,
            replicate,
            attempt,
            spec=spec,
            rollout_dir=rollout_dir,
            workspace=workspace,
            outcome=outcome,
            started_at=started_at,
            context=context,
            rollout_uid=rollout_uid,
        )

    def _record(
        self,
        task: Task,
        replicate: str,
        attempt: int,
        *,
        spec: RunSpec,
        rollout_dir: Path,
        workspace: Path,
        outcome: WorkerOutcome,
        started_at: datetime,
        context: dict[str, JsonValue],
        rollout_uid: str,
    ) -> RolloutRecord:
        events: list[TrajectoryEvent]
        partial = False
        error_family: ErrorFamily = "none"
        error_kind: str | None = None
        error: str | None = None
        exit_reason: str | None = None
        final_answer = ""
        rollout_id: str | None = None
        metadata: dict[str, Any] = {}
        trajectory_path = rollout_dir / "trajectory.json"
        metadata_path = rollout_dir / "metadata.json"
        common: _Attribution = {
            "arm": spec.arm,
            "unit_id": task.id,
            "seed": self._ctx.seed,
            "model": spec.policy.model,
        }
        if outcome.ok and trajectory_path.is_file() and metadata_path.is_file():
            trajectory = read_json(trajectory_path)
            metadata = read_json(metadata_path)
            events = events_from_trajectory(trajectory, metadata, context=context)
            exit_reason = str(metadata.get("exit_reason"))
            final_answer = str(metadata.get("final_answer", ""))
            rollout_id = str(metadata.get("rollout_id")) if metadata.get("rollout_id") else None
        else:
            partial = True
            error_family, error_kind = "infra", outcome.error_type or "policy_worker_error"
            error = outcome.error or "policy worker failed"
            log_path = rollout_dir / "rollout.log"
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            )
            events = events_from_rollout_log(log_text, context=context)
            exit_reason = _final_exit_reason(events)
            atomic_write_text(
                rollout_dir / "worker_failure.json",
                _dumps(
                    {
                        "error": error,
                        "error_type": error_kind,
                        "returncode": outcome.returncode,
                        "timed_out": outcome.timed_out,
                        "stdout_tail": outcome.stdout_tail,
                        "stderr_tail": outcome.stderr_tail,
                    }
                ),
            )
            self._ledger.record_infra_failure_event(
                **common, exc=InfraError(error, kind=error_kind), rollout_uid=rollout_uid
            )

        step_usage = usage_from_events(events)
        usd: float | None = None
        tier: str | None = None
        if not partial:
            try:
                reconcile_usage(step_usage.usage, metadata.get("token_usage", {}))
            except InfraError as exc:
                error_family, error_kind, error = "infra", exc.kind, str(exc)
                self._ledger.record_infra_failure_event(**common, exc=exc, rollout_uid=rollout_uid)
        if step_usage.steps > 0:
            cost = self._pricing.cost(spec.policy.model, step_usage.usage, started_at)
            usd, tier = cost.usd, cost.tier
            self._ledger.record_policy_rollout(
                **common,
                replicate=replicate,
                usage=step_usage.usage,
                cost=cost,
                steps=step_usage.steps,
                latency_ms=int(
                    float(metadata.get("duration_seconds", outcome.elapsed_seconds)) * 1000
                ),
                request_sha256=sha256_of(
                    {"task": task.id, "replicate": replicate, "attempt": attempt}
                ),
                rollout_uid=rollout_uid,
            )
        serper = count_serper_calls(events)
        for call in serper:
            self._ledger.record_search(
                arm=spec.arm,
                unit_id=task.id,
                seed=self._ctx.seed,
                cost=self._pricing.search_cost("serper"),
                query_sha256=call.command_sha256,
                replicate=replicate,
                approximate=True,
                rollout_uid=rollout_uid,
            )
        if error_family == "none" and exit_reason in INFRA_EXIT_REASONS:
            error_family, error_kind = "infra", exit_reason
            error = f"harness exited with {exit_reason}"
            self._ledger.record_infra_failure_event(
                **common, exc=InfraError(error, kind=exit_reason), rollout_uid=rollout_uid
            )
        if error_family == "none" and exit_reason in BUDGET_EXIT_REASONS:
            error_family, error_kind = "budget", "budget_exhausted"
            error = f"harness exited with {exit_reason}"
            limit = (
                spec.budget.max_steps
                if exit_reason == "max_steps"
                else spec.budget.rollout_wall_clock_seconds
            )
            self._ledger.record_task_failure_event(
                **common,
                exc=BudgetExhausted(
                    error,
                    budget=float(limit),
                    spent=float(limit),
                    unit="steps" if exit_reason == "max_steps" else "seconds",
                ),
                rollout_uid=rollout_uid,
            )
        cap = spec.budget.usd_cap_per_rollout
        if error_family == "none" and cap is not None and usd is not None and usd > cap:
            error_family, error_kind = "budget", "budget_exhausted"
            error = f"rollout cost {usd:.4f} USD exceeds cap {cap}"
            self._ledger.record_task_failure_event(
                **common,
                exc=BudgetExhausted(error, budget=cap, spent=usd, unit="usd"),
                rollout_uid=rollout_uid,
            )

        with TraceWriter(rollout_dir / "trajectory.jsonl", self._ctx.run_id) as writer:
            for event in events:
                writer.write(event.kind, event.payload)
        outputs = workspace / "outputs"
        artifacts = rollout_dir / "artifacts"
        if outputs.is_dir():
            shutil.rmtree(artifacts, ignore_errors=True)
            shutil.copytree(outputs, artifacts)
        record = RolloutRecord(
            rollout_uid=rollout_uid,
            task_id=task.id,
            source_benchmark=task.source_benchmark,
            replicate=replicate,
            attempt=attempt,
            mode=spec.mode,
            rollout_id=rollout_id,
            rollout_dir=rollout_dir,
            workspace_dir=workspace,
            final_answer=final_answer,
            exit_reason=exit_reason,
            steps=step_usage.steps,
            duration_seconds=float(metadata.get("duration_seconds", outcome.elapsed_seconds)),
            started_at=started_at.isoformat(),
            usage=step_usage.usage if step_usage.steps > 0 else None,
            usd=usd,
            pricing_tier=tier,
            partial=partial,
            error_family=error_family,
            error_kind=error_kind,
            error=error,
            score=None,
            serper_calls_approx=len(serper),
            reasoning_steps=step_usage.reasoning_steps,
        )
        self._trace.write(
            "rollout_done",
            {
                **context,
                "exit_reason": exit_reason,
                "steps": record.steps,
                "usd": usd,
                "error_family": error_family,
                "error_kind": error_kind,
                "partial": partial,
            },
        )
        return record

    # ---------------------------------------------------------------- scoring

    def _score_task(
        self, task: Task, records: list[RolloutRecord], resume: bool
    ) -> list[RolloutRecord]:
        return [self._score(task, r, resume=resume) for r in records]

    def _score(self, task: Task, record: RolloutRecord, *, resume: bool) -> RolloutRecord:
        if record.error_family == "infra":
            return record
        if record.score is not None:
            return record
        score_path = record.rollout_dir / SCORE_FILENAME
        if resume and score_path.is_file():
            try:
                score = Score.model_validate(read_json(score_path))
            except ValidationError as exc:
                raise InfraError(f"corrupt {score_path}:\n{exc}", kind="corrupt_file") from exc
            return record.model_copy(update={"score": score})
        artifacts = Artifacts(
            workspace=record.workspace_dir,
            final_answer=record.final_answer,
            trajectory_path=record.rollout_dir / "trajectory.json",
            rollout_id=record.rollout_id,
        )
        try:
            score = self._scorer.score(task, artifacts)
        except InfraError as exc:
            logger.error("scoring failed", extra={"task_id": task.id, "kind": exc.kind})
            return record.model_copy(
                update={"error_family": "infra", "error_kind": exc.kind, "error": str(exc)}
            )
        atomic_write_text(score_path, score.model_dump_json(indent=2) + "\n")
        return record.model_copy(update={"score": score})

    def _failures(
        self, task: Task, rollouts: Sequence[RolloutRecord], *, spec: RunSpec
    ) -> list[FailureRecord]:
        failures: list[FailureRecord] = []
        for record in rollouts:
            passed = record.score.passed if record.score is not None else False
            if passed and record.error_family == "none":
                continue
            family: FailureFamily
            if record.error_family == "infra":
                family, reason = "infra", record.error or (record.error_kind or "infra")
            elif record.error_family == "budget":
                family, reason = "budget", record.error or "budget exhausted"
            else:
                family = "task"
                reason = record.score.reason if record.score is not None else "not scored"
            failure = FailureRecord(
                task_id=task.id,
                source_benchmark=task.source_benchmark,
                replicate=record.replicate,
                attempt=record.attempt,
                mode=spec.mode,
                harness_snapshot_id=spec.harness_snapshot_id,
                trajectory_path=str(record.rollout_dir / "trajectory.jsonl"),
                partial=record.partial,
                family=family,
                error_kind=record.error_kind
                or (record.score.task_failure if record.score is not None else None),
                reason=reason,
                score_value=record.score.value if record.score is not None else None,
                passed=passed,
                exit_reason=record.exit_reason,
            )
            atomic_write_text(
                record.rollout_dir / "failure.json", failure.model_dump_json(indent=2) + "\n"
            )
            failures.append(failure)
        return failures


def _load_record(marker: Path) -> RolloutRecord:
    try:
        return RolloutRecord.model_validate(read_json(marker))
    except ValidationError as exc:
        raise InfraError(f"corrupt done marker {marker}:\n{exc}", kind="corrupt_file") from exc


def _final_exit_reason(events: Sequence[TrajectoryEvent]) -> str | None:
    for event in events:
        if event.kind == "final":
            value = event.payload.get("exit_reason")
            return str(value) if isinstance(value, str) else None
    return None


def _dumps(value: object) -> str:
    return json.dumps(to_json_value(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = ["Runner"]
