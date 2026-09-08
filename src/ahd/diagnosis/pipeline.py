"""The ``ahd diag`` steps over a run directory: reference, align, replay, signal, cluster,
corrupt, leakage. Each step reads the previous step's JSON under ``<run>/diagnosis/`` and
writes its own; nothing is recomputed silently.

No reference source: written fresh for ahd (see docs/reuse/M3.md).

Layout under ``<run>/diagnosis/``::

    genuineness.json      verdict per reference (written into the REFERENCE run's directory)
    alignments.json       ordered divergence candidates per failure
    harness/              the replay instrument, hashed like a snapshot
    replay/<key>/...      replay rollouts; replay.json per failure
    replays.json          all ReplayResults
    diagnoses.json        reference-arm and system-arm diagnoses per failure
    clusters.json         FailureCluster list + membership hash; activity.json next to it
    assignments/<arm>-s<seed>.json   corruption table, written before any rendering
    rendered/<arm>-s<seed>/<cluster>.md + rendered.json
    leakage.json          blind localization probe
    coherent_wrong-s<seed>.json   COH-WRONG texts per round + parity (M3.2)
    probe-s<seed>.json    privileged-information probe per arm (M3.2)
    replays.pre_m32.json  the fixed-k replay verdicts before E0d-B escalation (M3.2)
    replay_escalation.json  what the escalation changed (M3.2)
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.core.io import atomic_write_text, read_json
from ahd.core.manifest import update_manifest_diagnosis
from ahd.diagnosis import corrupt as corrupt_module
from ahd.diagnosis.align import Alignment, actions_from_trajectory, align
from ahd.diagnosis.attribution import active_steps
from ahd.diagnosis.cluster import ClusterSet, FailureCluster, cluster, failure_key
from ahd.diagnosis.coherent import (
    MAX_REGENERATIONS,
    Member,
    ParityResult,
    PlausibilityScore,
    generate,
    judge_plausibility,
    parity,
)
from ahd.diagnosis.corrupt import ARM_CORRUPTION, COHERENT_ARM, AssignmentTable, CoherentWrong
from ahd.diagnosis.genuineness import GenuinenessRecord, verify
from ahd.diagnosis.leakage import LeakageReport, probe
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.probe import ProbeRecord, ProbeReport, one_line, probe_one
from ahd.diagnosis.replay import Replayer, ReplayResult, is_marginal, verdict_of
from ahd.diagnosis.schema import (
    CauseVocabulary,
    Diagnosis,
    FailureType,
    FieldCaps,
    OracleBasis,
    Rendered,
    caps_for,
    identifier_tokens,
    load_causes,
    load_template,
    render,
)
from ahd.diagnosis.signal import reference_signal, system_signal
from ahd.errors import ConfigError, InfraError, TaskFailure
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import HarnessSnapshot, SnapshotStore, snapshot_from_dir
from ahd.runner.records import FailureRecord, ReferenceRecord, RolloutRecord
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.tasks.models import Task, TaskSet

logger = logging.getLogger(__name__)

DIAGNOSIS_DIRNAME = "diagnosis"
INSTRUMENT_DIR = Path(__file__).resolve().parent / "instrument"


def safe_key(task_id: str, replicate: str, attempt: int) -> str:
    return f"{task_id}__{replicate}__a{attempt}"


def rollout_dir(run_dir: Path, task_id: str, replicate: str, attempt: int) -> Path:
    suffix = "" if attempt == 1 else f"/attempt_{attempt}"
    return run_dir / "rollouts" / task_id / f"{replicate}{suffix}"


def diagnosis_dir(run_dir: Path) -> Path:
    path = run_dir / DIAGNOSIS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_list[T: StrictModel](path: Path, model: type[T], *, what: str) -> list[T]:
    if not path.is_file():
        raise InfraError(f"{what} missing: {path}", kind="missing_file")
    raw = read_json(path)
    if not isinstance(raw, list):
        raise InfraError(f"{path} is not a list", kind="corrupt_file")
    try:
        return [model.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise InfraError(f"corrupt {path}:\n{exc}", kind="corrupt_file") from exc


def _dump(items: Sequence[StrictModel]) -> str:
    return (
        json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False, indent=2) + "\n"
    )


def _trajectory(directory: Path) -> dict[str, Any]:
    path = directory / "trajectory.json"
    if not path.is_file():
        raise InfraError(f"trajectory.json missing under {directory}", kind="missing_file")
    data = read_json(path)
    if not isinstance(data, dict):
        raise InfraError(f"{path} is not an object", kind="corrupt_file")
    return data


def _task_prompt(trajectory: dict[str, Any]) -> str:
    for message in trajectory.get("messages", []):
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


# ---------------------------------------------------------------- 1. reference genuineness


def verify_references(
    reference_run: Path,
    *,
    taskset: TaskSet,
    llm: DiagnosisLLM,
    prompt_template: str,
    claw_repo: Path | None,
) -> list[GenuinenessRecord]:
    references = _load_list(
        reference_run / "references.json", ReferenceRecord, what="references.json"
    )
    records: list[GenuinenessRecord] = []
    for reference in references:
        if reference.passing_attempt is None:
            continue
        task = taskset.by_id(reference.task_id)
        directory = rollout_dir(
            reference_run, task.id, reference.replicate, reference.passing_attempt
        )
        trajectory = _trajectory(directory)
        records.append(
            verify(
                task,
                trajectory,
                replicate=reference.replicate,
                attempt=reference.passing_attempt,
                rollout_dir=directory,
                claw_repo=claw_repo,
                llm=llm,
                prompt_template=prompt_template,
                task_prompt=_task_prompt(trajectory),
            )
        )
    atomic_write_text(diagnosis_dir(reference_run) / "genuineness.json", _dump(records))
    return records


def genuine_references(reference_run: Path) -> dict[str, GenuinenessRecord]:
    """task id -> the first ``genuine`` reference (lowest replicate)."""
    records = _load_list(
        diagnosis_dir(reference_run) / "genuineness.json",
        GenuinenessRecord,
        what="genuineness.json",
    )
    out: dict[str, GenuinenessRecord] = {}
    for record in sorted(records, key=lambda r: (r.task_id, r.replicate)):
        if record.verdict == "genuine" and record.task_id not in out:
            out[record.task_id] = record
    return out


# ---------------------------------------------------------------- 2. alignment


class AlignmentRecord(StrictModel):
    failure_key: str
    task_id: str
    replicate: str
    attempt: int
    family: str
    reference_replicate: str
    reference_attempt: int
    alignment: Alignment
    skipped: str | None = None


def align_failures(run_dir: Path, reference_run: Path) -> list[AlignmentRecord]:
    failures = _load_list(run_dir / "failures.json", FailureRecord, what="failures.json")
    genuine = genuine_references(reference_run)
    records: list[AlignmentRecord] = []
    for failure in failures:
        if failure.family == "infra":
            continue  # infrastructure failures are not harness failures
        reference = genuine.get(failure.task_id)
        if reference is None:
            logger.warning("no genuine reference", extra={"task_id": failure.task_id})
            continue
        # derive the rollout directory from the run layout: the recorded absolute path goes
        # stale when a run directory is copied or moved (observed 2026-09-05)
        failed_dir = rollout_dir(run_dir, failure.task_id, failure.replicate, failure.attempt)
        if not (failed_dir / "trajectory.json").is_file():
            raise InfraError(
                f"trajectory.json missing under {failed_dir} (failures.json recorded "
                f"{failure.trajectory_path})",
                kind="missing_file",
            )
        reference_dir = rollout_dir(
            reference_run, failure.task_id, reference.replicate, reference.attempt
        )
        alignment = align(
            _trajectory(failed_dir),
            _trajectory(reference_dir),
            task_id=failure.task_id,
            failed_exit_reason=failure.exit_reason,
        )
        records.append(
            AlignmentRecord(
                failure_key=failure_key_of(failure),
                task_id=failure.task_id,
                replicate=failure.replicate,
                attempt=failure.attempt,
                family=failure.family,
                reference_replicate=reference.replicate,
                reference_attempt=reference.attempt,
                alignment=alignment,
                skipped=None if alignment.candidates else "no divergence candidate",
            )
        )
    atomic_write_text(diagnosis_dir(run_dir) / "alignments.json", _dump(records))
    return records


def failure_key_of(failure: FailureRecord) -> str:
    return f"{failure.task_id}/{failure.replicate}/{failure.attempt}"


def load_alignments(run_dir: Path) -> list[AlignmentRecord]:
    return _load_list(
        diagnosis_dir(run_dir) / "alignments.json", AlignmentRecord, what="alignments.json"
    )


def _failed_trajectory(run_dir: Path, record: AlignmentRecord) -> tuple[dict[str, Any], Path]:
    directory = rollout_dir(run_dir, record.task_id, record.replicate, record.attempt)
    return _trajectory(directory), directory


def _recorded_workspace(directory: Path) -> str | None:
    marker = directory / "done.json"
    if not marker.is_file():
        return None
    try:
        return str(RolloutRecord.model_validate(read_json(marker)).workspace_dir)
    except ValidationError:
        return None


# ---------------------------------------------------------------- 3. replay


def instrument_snapshot(run_dir: Path, manifest: ComponentManifest) -> HarnessSnapshot:
    store = SnapshotStore(diagnosis_dir(run_dir) / "harness")
    return snapshot_from_dir(
        INSTRUMENT_DIR,
        store=store,
        manifest=manifest,
        provenance="instrument",
        source="ahd.diagnosis.instrument",
    )


def replay_failures(
    run_dir: Path,
    reference_run: Path,
    *,
    runner: Runner,
    spec: RunSpec,
    studied: HarnessSnapshot,
    instrument: HarnessSnapshot,
    taskset: TaskSet,
    k: int,
    max_candidates: int,
    economize: bool,
    only: Sequence[str] = (),
    resume: bool = False,
    subdir: str = "replay",
    workers: int = 1,
    before_each: Callable[[], None] | None = None,
) -> list[ReplayResult]:
    """``subdir`` = ``replay`` writes ``replays.json`` / ``failure_types.json``; any other
    name (E0's ``replay_full``) writes ``replays_<subdir>.json`` and leaves the main files."""
    replayer = Replayer(
        runner=runner,
        spec=spec,
        studied=studied,
        instrument=instrument,
        out_dir=diagnosis_dir(run_dir),
        reference_run=reference_run.name,
        k=k,
        max_candidates=max_candidates,
        economize=economize,
        resume=resume,
        subdir=subdir,
        workers=workers,
    )
    results: list[ReplayResult] = []
    for record in load_alignments(run_dir):
        if record.skipped or (
            only and record.failure_key not in only and record.task_id not in only
        ):
            continue
        if before_each is not None:
            before_each()  # e.g. the E0 hard budget cap; raises to stop before more spend
        task = taskset.by_id(record.task_id)
        failed, failed_dir = _failed_trajectory(run_dir, record)
        reference_dir = rollout_dir(
            reference_run, record.task_id, record.reference_replicate, record.reference_attempt
        )
        reference = _trajectory(reference_dir)
        results.append(
            replayer.validate(
                task,
                failed_trajectory=failed,
                reference_trajectory=reference,
                alignment=record.alignment,
                replicate=record.replicate,
                attempt=record.attempt,
                recorded_workspace=_recorded_workspace(failed_dir),
                reference_workspace=_recorded_workspace(reference_dir),
            )
        )
    if subdir != "replay":
        atomic_write_text(diagnosis_dir(run_dir) / f"replays_{subdir}.json", _dump(results))
        return results
    atomic_write_text(diagnosis_dir(run_dir) / "replays.json", _dump(results))
    _write_failure_types(run_dir, results, k=k, max_candidates=max_candidates, economize=economize)
    return results


def _write_failure_types(
    run_dir: Path, results: Sequence[ReplayResult], *, k: int, max_candidates: int, economize: bool
) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.failure_type] = counts.get(r.failure_type, 0) + 1
    summary = {
        "counts": dict(sorted(counts.items())),
        "per_failure": {
            r.failure_key: {
                "failure_type": r.failure_type,
                "oracle_step": r.oracle_step,
                "oracle_step_basis": r.oracle_step_basis,
                "sufficient_set": list(r.sufficient_set),
                "negative_set": list(r.negative_set),
                "unresolved_set": list(r.unresolved_set),
                "usd": r.usd,
            }
            for r in results
        },
        "k": k,
        "max_candidates": max_candidates,
        "economize": economize,
    }
    atomic_write_text(
        diagnosis_dir(run_dir) / "failure_types.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )


class EscalatedCandidate(StrictModel):
    step: int
    before: str
    after: str
    n: int | None
    substitute: str
    """``passed/k`` before -> after."""
    control: str


class EscalationRow(StrictModel):
    failure_key: str
    failure_type_before: FailureType
    failure_type_after: FailureType
    oracle_step_before: int | None
    oracle_step_after: int | None
    usd: float
    candidates: tuple[EscalatedCandidate, ...]


def escalate_replays(
    run_dir: Path,
    reference_run: Path,
    *,
    runner: Runner,
    spec: RunSpec,
    studied: HarnessSnapshot,
    instrument: HarnessSnapshot,
    taskset: TaskSet,
    max_candidates: int,
    economize: bool,
    workers: int = 1,
    before_each: Callable[[], None] | None = None,
) -> list[EscalationRow]:
    """E0d-B (M3.2): re-open every marginal fixed-k candidate of ``replays.json`` through the
    adaptive schedule, keeping the rollouts already run. The pre-escalation files are kept as
    ``replays.pre_m32.json`` / ``failure_types.pre_m32.json``; ``replays.json`` is rewritten
    after every failure so an interrupted escalation resumes."""
    out = diagnosis_dir(run_dir)
    if not (out / "replays.pre_m32.json").is_file():
        shutil.copyfile(out / "replays.json", out / "replays.pre_m32.json")
        if (out / "failure_types.json").is_file():
            shutil.copyfile(out / "failure_types.json", out / "failure_types.pre_m32.json")
    results = load_replays(run_dir)
    replayer = Replayer(
        runner=runner,
        spec=spec,
        studied=studied,
        instrument=instrument,
        out_dir=out,
        reference_run=reference_run.name,
        max_candidates=max_candidates,
        economize=economize,
        resume=True,
        workers=workers,
    )
    rows: list[EscalationRow] = []
    log_path = out / "replay_escalation.json"
    if log_path.is_file():
        rows = _load_list(log_path, EscalationRow, what="replay_escalation.json")
    done = {r.failure_key for r in rows}
    for record in load_alignments(run_dir):
        key = safe_key(record.task_id, record.replicate, record.attempt)
        existing = results.get(key)
        if existing is None or key in done:
            continue
        marginal = [c for c in existing.candidates if is_marginal(c)]
        if not marginal:
            continue
        if before_each is not None:
            before_each()
        task = taskset.by_id(record.task_id)
        failed, failed_dir = _failed_trajectory(run_dir, record)
        reference_dir = rollout_dir(
            reference_run, record.task_id, record.reference_replicate, record.reference_attempt
        )
        updated = replayer.escalate(
            task,
            existing,
            failed_trajectory=failed,
            reference_trajectory=_trajectory(reference_dir),
            alignment=record.alignment,
            recorded_workspace=_recorded_workspace(failed_dir),
            reference_workspace=_recorded_workspace(reference_dir),
        )
        results[key] = updated
        after = {c.step: c for c in updated.candidates}
        rows.append(
            EscalationRow(
                failure_key=key,
                failure_type_before=existing.failure_type,
                failure_type_after=updated.failure_type,
                oracle_step_before=existing.oracle_step,
                oracle_step_after=updated.oracle_step,
                usd=updated.usd - existing.usd,
                candidates=tuple(
                    EscalatedCandidate(
                        step=c.step,
                        before=verdict_of(c),
                        after=verdict_of(after[c.step]),
                        n=after[c.step].n,
                        substitute=(
                            f"{c.substitute.passed}/{c.substitute.k} -> "
                            f"{after[c.step].substitute.passed}/{after[c.step].substitute.k}"
                        ),
                        control=(
                            (
                                "skipped"
                                if c.control.skipped
                                else f"{c.control.passed}/{c.control.k}"
                            )
                            + " -> "
                            + (
                                "skipped"
                                if after[c.step].control.skipped
                                else f"{after[c.step].control.passed}/{after[c.step].control.k}"
                            )
                        ),
                    )
                    for c in marginal
                ),
            )
        )
        ordered = [
            results[safe_key(a.task_id, a.replicate, a.attempt)]
            for a in load_alignments(run_dir)
            if safe_key(a.task_id, a.replicate, a.attempt) in results
        ]
        atomic_write_text(out / "replays.json", _dump(ordered))
        atomic_write_text(log_path, _dump(rows))
    ordered = [
        results[safe_key(a.task_id, a.replicate, a.attempt)]
        for a in load_alignments(run_dir)
        if safe_key(a.task_id, a.replicate, a.attempt) in results
    ]
    _write_failure_types(
        run_dir, ordered, k=replayer.k, max_candidates=max_candidates, economize=economize
    )
    atomic_write_text(log_path, _dump(rows))
    return rows


def load_replays(run_dir: Path) -> dict[str, ReplayResult]:
    path = diagnosis_dir(run_dir) / "replays.json"
    if not path.is_file():
        return {}
    return {r.failure_key: r for r in _load_list(path, ReplayResult, what="replays.json")}


# ---------------------------------------------------------------- 4. signals


class DiagnosisSet(StrictModel):
    reference: tuple[Diagnosis, ...]
    system: tuple[Diagnosis, ...]
    errors: dict[str, str]
    excluded: dict[str, str] = {}
    """Failures kept out of the oracle (reference) arm, by failure type: ``unrepairable`` and
    ``unreplayable`` (owner decision, M3.1). They still get a SYSTEM-arm diagnosis."""
    failure_types: dict[str, str] = {}
    """failure key -> replay verdict (``unvalidated`` when no replay ran)."""


def signal_failures(
    run_dir: Path,
    reference_run: Path,
    *,
    taskset: TaskSet,
    manifest: ComponentManifest,
    harness_snapshot_id: str,
    llm: DiagnosisLLM,
    prompts: dict[str, str],
    allow_unvalidated: bool = False,
    vocabulary: CauseVocabulary | None = None,
) -> DiagnosisSet:
    """Reference-arm diagnoses need a replay verdict per failure (``ahd diag replay``); with
    ``allow_unvalidated`` a failure without one is diagnosed at ``t_class`` and marked so."""
    failures = {
        failure_key_of(f): f
        for f in _load_list(run_dir / "failures.json", FailureRecord, what="failures.json")
    }
    vocabulary = vocabulary or load_causes()
    replays = load_replays(run_dir)
    reference_out: list[Diagnosis] = []
    system_out: list[Diagnosis] = []
    errors: dict[str, str] = {}
    excluded: dict[str, str] = {}
    failure_types: dict[str, str] = {}
    for record in load_alignments(run_dir):
        if record.skipped:
            errors[record.failure_key] = record.skipped
            continue
        task = taskset.by_id(record.task_id)
        failed, _ = _failed_trajectory(run_dir, record)
        reference = _trajectory(
            rollout_dir(
                reference_run, record.task_id, record.reference_replicate, record.reference_attempt
            )
        )
        replay = replays.get(safe_key(record.task_id, record.replicate, record.attempt))
        failure_type: FailureType | None = replay.failure_type if replay is not None else None
        failure_types[record.failure_key] = failure_type or "unvalidated"
        reference_arm = True
        basis: OracleBasis = "unvalidated"
        step = record.alignment.candidates[0].step
        if replay is None:
            if not allow_unvalidated:
                errors[record.failure_key] = (
                    "no replay verdict; run `ahd diag replay` first (or pass --allow-unvalidated)"
                )
                reference_arm = False
        elif replay.oracle_step is None:
            excluded[record.failure_key] = replay.failure_type
            reference_arm = False
        else:
            step, basis = replay.oracle_step, replay.oracle_step_basis
        candidate = next(c for c in record.alignment.candidates if c.step == step)
        validated = replay is not None and replay.oracle_step is not None
        failure = failures[record.failure_key]
        try:
            if reference_arm:
                reference_out.append(
                    reference_signal(
                        task,
                        failed_trajectory=failed,
                        reference_trajectory=reference,
                        alignment=record.alignment,
                        candidate=candidate,
                        oracle_validated=validated,
                        reference_run=(
                            f"{reference_run.name}:{record.reference_replicate}/"
                            f"{record.reference_attempt}"
                        ),
                        replicate=record.replicate,
                        attempt=record.attempt,
                        harness_snapshot_id=harness_snapshot_id,
                        manifest=manifest,
                        llm=llm,
                        prompt_template=prompts["reference_signal"],
                        vocabulary=vocabulary,
                        failure_type=failure_type,
                        oracle_step_basis=basis,
                    )
                )
            system_out.append(
                system_signal(
                    task,
                    failed_trajectory=failed,
                    exit_reason=failure.exit_reason,
                    score_reason=failure.reason,
                    replicate=record.replicate,
                    attempt=record.attempt,
                    harness_snapshot_id=harness_snapshot_id,
                    manifest=manifest,
                    llm=llm,
                    prompt_template=prompts["system_signal"],
                    vocabulary=vocabulary,
                )
            )
        except TaskFailure as exc:
            errors[record.failure_key] = f"{exc.kind}: {exc}"
            logger.warning("signal failed", extra={"failure": record.failure_key, "kind": exc.kind})
    result = DiagnosisSet(
        reference=tuple(reference_out),
        system=tuple(system_out),
        errors=errors,
        excluded=excluded,
        failure_types=failure_types,
    )
    atomic_write_text(
        diagnosis_dir(run_dir) / "diagnoses.json", result.model_dump_json(indent=2) + "\n"
    )
    return result


def load_diagnoses(run_dir: Path) -> DiagnosisSet:
    path = diagnosis_dir(run_dir) / "diagnoses.json"
    if not path.is_file():
        raise InfraError(
            f"diagnoses.json missing: run `ahd diag signal` first ({path})", kind="missing_file"
        )
    try:
        return DiagnosisSet.model_validate(read_json(path))
    except ValidationError as exc:
        raise InfraError(f"corrupt {path}:\n{exc}", kind="corrupt_file") from exc


# ---------------------------------------------------------------- 5. clusters


class ClusterActivity(StrictModel):
    activity: dict[str, dict[str, tuple[int, ...]]]
    """cluster id -> component -> steps at which it is active in the representative failure."""
    sufficient: dict[str, tuple[int, ...]]
    tool_names: tuple[str, ...]
    negative: dict[str, tuple[int, ...]] = {}
    """cluster id -> validated-negative candidate steps of the representative (M3.2)."""
    unresolved: dict[str, tuple[int, ...]] = {}
    candidate_steps: dict[str, tuple[int, ...]] = {}


def cluster_run(
    run_dir: Path,
    *,
    manifest: ComponentManifest,
    reference_run: str | None,
    instrument_snapshot_id: str | None,
) -> tuple[ClusterSet, ClusterActivity]:
    diagnoses = load_diagnoses(run_dir)
    clusters = cluster(diagnoses.reference)
    replays = load_replays(run_dir)
    activity: dict[str, dict[str, tuple[int, ...]]] = {}
    sufficient: dict[str, tuple[int, ...]] = {}
    negative: dict[str, tuple[int, ...]] = {}
    unresolved: dict[str, tuple[int, ...]] = {}
    candidate_steps: dict[str, tuple[int, ...]] = {}
    tool_names: set[str] = set()
    for c in clusters.clusters:
        p = c.diagnosis_reference.provenance
        trajectory = _trajectory(rollout_dir(run_dir, p.task_id, p.replicate, p.attempt))
        activity[c.id] = {
            spec.id: tuple(sorted(active_steps(spec.id, trajectory)))
            for spec in manifest.components
            if spec.patchable and spec.where_eligible
        }
        replay = replays.get(safe_key(p.task_id, p.replicate, p.attempt))
        verdicts = (
            [(cand.step, verdict_of(cand)) for cand in replay.candidates]
            if replay is not None
            else []
        )
        sufficient[c.id] = tuple(s for s, v in verdicts if v == "positive")
        negative[c.id] = tuple(s for s, v in verdicts if v == "negative")
        unresolved[c.id] = tuple(s for s, v in verdicts if v == "unresolved")
        candidate_steps[c.id] = tuple(s for s, _ in verdicts)
        for step_actions in actions_from_trajectory(trajectory):
            tool_names.update(a.name for a in step_actions.actions if a.klass == "tool")
    activity_record = ClusterActivity(
        activity=activity,
        sufficient=sufficient,
        tool_names=tuple(sorted(tool_names)),
        negative=negative,
        unresolved=unresolved,
        candidate_steps=candidate_steps,
    )
    out = diagnosis_dir(run_dir)
    atomic_write_text(out / "clusters.json", clusters.model_dump_json(indent=2) + "\n")
    atomic_write_text(out / "activity.json", activity_record.model_dump_json(indent=2) + "\n")
    block: dict[str, JsonValue] = {
        "clusters_sha256": clusters.membership_sha256,
        "cluster_count": len(clusters.clusters),
        "unvalidated_failures": list(clusters.unvalidated),
        "reference_run": reference_run,
        "instrument_snapshot_id": instrument_snapshot_id,
    }
    update_manifest_diagnosis(run_dir, block)
    return clusters, activity_record


def load_clusters(run_dir: Path) -> tuple[ClusterSet, ClusterActivity]:
    out = diagnosis_dir(run_dir)
    try:
        return (
            ClusterSet.model_validate(read_json(out / "clusters.json")),
            ClusterActivity.model_validate(read_json(out / "activity.json")),
        )
    except ValidationError as exc:
        raise InfraError(f"corrupt clusters under {out}:\n{exc}", kind="corrupt_file") from exc


# ---------------------------------------------------------------- 6. corruption + rendering


class RenderedCluster(StrictModel):
    cluster_id: str
    arm: str
    seed: int
    corruption: str
    impossible: str | None
    diagnosis: Diagnosis | None
    rendered: Rendered | None


def _arm_diagnosis(
    arm: str,
    cluster_: FailureCluster,
    assignment: corrupt_module.Assignment,
    usable: Sequence[FailureCluster],
    system_by_key: dict[str, Diagnosis],
    generated: dict[str, CoherentWrong],
) -> tuple[Diagnosis | None, str | None]:
    """The diagnosis a proposer in ``arm`` would receive for this cluster, or why none."""
    if arm == "system":
        system = system_by_key.get(cluster_.representative)
        if system is None:
            return None, "no system diagnosis for the representative"
        return system, None
    if assignment.impossible is not None:
        return None, assignment.impossible
    if assignment.corruption == "coherent" and cluster_.id not in generated:
        return None, "coherent-wrong text not generated (run `ahd diag coherent`)"
    return (
        corrupt_module.apply(cluster_.diagnosis_reference, assignment, usable, generated=generated),
        None,
    )


def corrupt_run(
    run_dir: Path,
    *,
    seed: int,
    manifest: ComponentManifest,
    arms: Sequence[str] = tuple(ARM_CORRUPTION),
    template: str | None = None,
    exclude_candidate_set: bool = True,
) -> dict[str, tuple[AssignmentTable, list[RenderedCluster]]]:
    """Assignment tables for every arm (written before any rendering), then rendering with
    per-cluster caps taken across all arms of this seed (owner decision, M3.1). Returns the
    requested arms only; the tables of the other arms are still written. COH-WRONG texts come
    from ``coherent_wrong-s<seed>.json`` when it exists (``coherent_run``); until then that
    arm renders nothing and says so."""
    for arm in arms:
        if arm not in ARM_CORRUPTION:
            raise ConfigError(f"unknown arm {arm!r}; known: {sorted(ARM_CORRUPTION)}")
    clusters, activity = load_clusters(run_dir)
    diagnoses = load_diagnoses(run_dir)
    usable: list[FailureCluster] = list(clusters.clusters)
    activity_sets = {
        cid: {comp: set(steps) for comp, steps in comps.items()}
        for cid, comps in activity.activity.items()
    }
    sufficient_sets = {cid: set(steps) for cid, steps in activity.sufficient.items()}
    negative_sets = {cid: set(steps) for cid, steps in activity.negative.items()}
    generated = load_coherent(run_dir, seed)
    out = diagnosis_dir(run_dir)
    (out / "assignments").mkdir(exist_ok=True)
    tables: dict[str, AssignmentTable] = {}
    for arm in ARM_CORRUPTION:
        table = corrupt_module.assign(
            usable,
            arm=arm,
            seed=seed,
            manifest=manifest,
            activity=activity_sets,
            sufficient=sufficient_sets,
            negative=negative_sets,
            exclude_candidate_set=exclude_candidate_set,
        )
        tables[arm] = table
        atomic_write_text(
            out / "assignments" / f"{arm}-s{seed}.json", table.model_dump_json(indent=2) + "\n"
        )
    # rendering, only after every table is on disk
    tokens = identifier_tokens(manifest, tool_names=activity.tool_names)
    template = template or load_template()
    system_by_key = {failure_key(d): d for d in diagnoses.system}
    per_arm_diag: dict[str, dict[str, tuple[Diagnosis | None, str | None]]] = {}
    for arm, table in tables.items():
        per_arm_diag[arm] = {}
        for assignment in table.assignments:
            c = next(x for x in usable if x.id == assignment.cluster_id)
            per_arm_diag[arm][c.id] = _arm_diagnosis(
                arm, c, assignment, usable, system_by_key, generated
            )
    caps: dict[str, FieldCaps] = {}
    for c in usable:
        present = [
            d for arm in ARM_CORRUPTION for d in [per_arm_diag[arm][c.id][0]] if d is not None
        ]
        caps[c.id] = caps_for(present, tokens)
    results: dict[str, tuple[AssignmentTable, list[RenderedCluster]]] = {}
    for arm in arms:
        table = tables[arm]
        rendered_dir = out / "rendered" / f"{arm}-s{seed}"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        items: list[RenderedCluster] = []
        updated: list[corrupt_module.Assignment] = []
        for assignment in table.assignments:
            c = next(x for x in usable if x.id == assignment.cluster_id)
            diagnosis, impossible = per_arm_diag[arm][c.id]
            if diagnosis is None:
                items.append(
                    RenderedCluster(
                        cluster_id=c.id,
                        arm=arm,
                        seed=seed,
                        corruption=assignment.corruption,
                        impossible=impossible,
                        diagnosis=None,
                        rendered=None,
                    )
                )
                updated.append(assignment)
                continue
            rendered = render(diagnosis, template, tokens=tokens, caps=caps[c.id])
            atomic_write_text(rendered_dir / f"{c.id}.md", rendered.text + "\n")
            items.append(
                RenderedCluster(
                    cluster_id=c.id,
                    arm=arm,
                    seed=seed,
                    corruption=assignment.corruption,
                    impossible=None,
                    diagnosis=diagnosis,
                    rendered=rendered,
                )
            )
            updated.append(
                assignment.model_copy(update={"rendered_lengths": rendered.field_lengths})
            )
        table = table.model_copy(update={"assignments": tuple(updated)})
        atomic_write_text(
            out / "assignments" / f"{arm}-s{seed}.json", table.model_dump_json(indent=2) + "\n"
        )
        atomic_write_text(rendered_dir / "rendered.json", _dump(items))
        atomic_write_text(
            rendered_dir / "caps.json",
            json.dumps({cid: cap.model_dump() for cid, cap in caps.items()}, indent=2) + "\n",
        )
        results[arm] = (table, items)
    return results


# ---------------------------------------------------------------- 7. leakage


def leakage_run(
    run_dir: Path, *, manifest: ComponentManifest, llm: DiagnosisLLM, prompt_template: str
) -> LeakageReport:
    clusters, activity = load_clusters(run_dir)
    tokens = identifier_tokens(manifest, tool_names=activity.tool_names)
    report = probe(
        clusters.clusters,
        manifest=manifest,
        tokens=tokens,
        llm=llm,
        prompt_template=prompt_template,
    )
    atomic_write_text(
        diagnosis_dir(run_dir) / "leakage.json", report.model_dump_json(indent=2) + "\n"
    )
    return report


# ---------------------------------------------------------------- 8. COH-WRONG (M3.2)


class CoherentRound(StrictModel):
    generation_seed: int
    generated: tuple[CoherentWrong, ...]
    scores: tuple[PlausibilityScore, ...] = ()
    parity: ParityResult | None = None
    """Run-level parity; the E0d decision pools the runs of a source."""


class CoherentWrongSet(StrictModel):
    seed: int
    min_members: int
    eligible: tuple[str, ...]
    """Clusters with >= min_members and a feasible coherent assignment."""
    rounds: tuple[CoherentRound, ...] = ()
    final_generation_seed: int | None = None
    parity_ok: bool | None = None
    parity_scope: Literal["run", "source"] = "run"
    generated: tuple[CoherentWrong, ...] = ()
    """The texts in force (the provisional round while judging, the final round after)."""


def coherent_path(run_dir: Path, seed: int) -> Path:
    return diagnosis_dir(run_dir) / f"coherent_wrong-s{seed}.json"


def load_coherent_set(run_dir: Path, seed: int) -> CoherentWrongSet | None:
    path = coherent_path(run_dir, seed)
    if not path.is_file():
        return None
    try:
        return CoherentWrongSet.model_validate(read_json(path))
    except ValidationError as exc:
        raise InfraError(f"corrupt {path}:\n{exc}", kind="corrupt_file") from exc


def load_coherent(run_dir: Path, seed: int) -> dict[str, CoherentWrong]:
    record = load_coherent_set(run_dir, seed)
    return {g.cluster_id: g for g in record.generated} if record is not None else {}


def _parse_key(key: str) -> tuple[str, str, int]:
    task_id, replicate, attempt = key.rsplit("/", 2)
    return task_id, replicate, int(attempt)


def _members(run_dir: Path, cluster_: FailureCluster) -> list[Member]:
    members: list[Member] = []
    for key in cluster_.members:
        task_id, replicate, attempt = _parse_key(key)
        trajectory = _trajectory(rollout_dir(run_dir, task_id, replicate, attempt))
        members.append((key, _task_prompt(trajectory), trajectory))
    return members


def _write_coherent_set(run_dir: Path, record: CoherentWrongSet) -> None:
    atomic_write_text(coherent_path(run_dir, record.seed), record.model_dump_json(indent=2) + "\n")


def coherent_round(
    run_dir: Path,
    *,
    seed: int,
    generation_seed: int,
    manifest: ComponentManifest,
    llm: DiagnosisLLM,
    prompts: dict[str, str],
    vocabulary: CauseVocabulary,
    min_members: int = 2,
) -> CoherentWrongSet:
    """One generation round: COH-WRONG texts for every eligible cluster at ``generation_seed``,
    rendered with the other arms (caps recomputed), then judged for plausibility against the
    rendered REF diagnosis. Appends the round to ``coherent_wrong-s<seed>.json`` and leaves
    its texts in force; ``finalize_coherent`` records the decision."""
    clusters, _activity = load_clusters(run_dir)
    out = diagnosis_dir(run_dir)
    table_path = out / "assignments" / f"{COHERENT_ARM}-s{seed}.json"
    if not table_path.is_file():
        corrupt_run(run_dir, seed=seed, manifest=manifest)
    table = AssignmentTable.model_validate(read_json(table_path))
    by_cluster = {a.cluster_id: a for a in table.assignments}
    eligible = [
        c
        for c in clusters.clusters
        if len(c.members) >= min_members
        and by_cluster[c.id].impossible is None
        and by_cluster[c.id].where is not None
    ]
    existing = load_coherent_set(run_dir, seed)
    rounds = [
        r for r in (existing.rounds if existing else ()) if r.generation_seed != generation_seed
    ]
    generated: list[CoherentWrong] = []
    for c in eligible:
        a = by_cluster[c.id]
        assert a.where is not None and a.where.step is not None
        generated.append(
            generate(
                c,
                members=_members(run_dir, c),
                component=a.where.component,
                step=a.where.step,
                step_basis=a.step_basis or "not_sufficient",
                manifest=manifest,
                llm=llm,
                prompt_template=prompts["forced_where"],
                vocabulary=vocabulary,
                seed=seed,
                generation_seed=generation_seed,
            )
        )
    this_round = CoherentRound(generation_seed=generation_seed, generated=tuple(generated))
    record = CoherentWrongSet(
        seed=seed,
        min_members=min_members,
        eligible=tuple(c.id for c in eligible),
        rounds=tuple([*rounds, this_round]),
        final_generation_seed=None,
        parity_ok=None,
        generated=tuple(generated),
    )
    _write_coherent_set(run_dir, record)
    corrupt_run(run_dir, seed=seed, manifest=manifest)  # renders REF and COH-WRONG with shared caps
    scores: list[PlausibilityScore] = []
    for c in eligible:
        ref_path = out / "rendered" / f"reference-s{seed}" / f"{c.id}.md"
        coh_path = out / "rendered" / f"{COHERENT_ARM}-s{seed}" / f"{c.id}.md"
        if not ref_path.is_file() or not coh_path.is_file():
            continue
        scores.append(
            judge_plausibility(
                c,
                members=_members(run_dir, c),
                rendered_reference=ref_path.read_text(encoding="utf-8"),
                rendered_coherent=coh_path.read_text(encoding="utf-8"),
                llm=llm,
                prompt_template=prompts["plausibility"],
                seed=seed,
                generation_seed=generation_seed,
            )
        )
    this_round = this_round.model_copy(update={"scores": tuple(scores), "parity": parity(scores)})
    record = record.model_copy(update={"rounds": tuple([*rounds, this_round])})
    _write_coherent_set(run_dir, record)
    return record


def finalize_coherent(
    run_dir: Path,
    *,
    seed: int,
    generation_seed: int,
    parity_ok: bool,
    scope: Literal["run", "source"],
    manifest: ComponentManifest,
) -> CoherentWrongSet:
    """Record which round is in force and whether parity held (at the given scope), then
    re-render every arm with those texts."""
    record = load_coherent_set(run_dir, seed)
    if record is None:
        raise InfraError(f"no coherent_wrong-s{seed}.json under {run_dir}", kind="missing_file")
    chosen = next((r for r in record.rounds if r.generation_seed == generation_seed), None)
    if chosen is None:
        raise ConfigError(f"no round with generation seed {generation_seed} in {run_dir}")
    record = record.model_copy(
        update={
            "final_generation_seed": generation_seed,
            "parity_ok": parity_ok,
            "parity_scope": scope,
            "generated": chosen.generated,
        }
    )
    _write_coherent_set(run_dir, record)
    corrupt_run(run_dir, seed=seed, manifest=manifest)
    return record


def coherent_run(
    run_dir: Path,
    *,
    seed: int,
    manifest: ComponentManifest,
    llm: DiagnosisLLM,
    prompts: dict[str, str],
    vocabulary: CauseVocabulary,
    min_members: int = 2,
    max_regenerations: int = MAX_REGENERATIONS,
) -> CoherentWrongSet:
    """Run-level loop (``ahd diag coherent``): generate, judge, regenerate on a parity
    violation up to ``max_regenerations`` times; the last round stays in force either way."""
    record: CoherentWrongSet | None = None
    for generation_seed in range(max_regenerations + 1):
        record = coherent_round(
            run_dir,
            seed=seed,
            generation_seed=generation_seed,
            manifest=manifest,
            llm=llm,
            prompts=prompts,
            vocabulary=vocabulary,
            min_members=min_members,
        )
        result = record.rounds[-1].parity
        if result is not None and result.ok:
            break
    assert record is not None
    final = record.rounds[-1]
    return finalize_coherent(
        run_dir,
        seed=seed,
        generation_seed=final.generation_seed,
        parity_ok=bool(final.parity and final.parity.ok),
        scope="run",
        manifest=manifest,
    )


# ---------------------------------------------------------------- 9. PI probe (M3.2)

PROBE_ARMS: tuple[str, ...] = ("reference", "system", "shuffled", COHERENT_ARM)


def category_of(task: Task) -> str | None:
    for key in ("category", "sector"):
        value = task.metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _tool_names(trajectory: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for step_actions in actions_from_trajectory(trajectory):
        for a in step_actions.actions:
            if a.klass == "tool":
                names.add(a.name)
            elif a.klass in ("shell_ro", "shell_mut", "shell_opaque"):
                names.add("run_shell_command")
    return names


def probe_run(
    run_dir: Path,
    reference_run: Path,
    *,
    seed: int,
    taskset: TaskSet,
    pool_ids: Sequence[str],
    llm: DiagnosisLLM,
    prompt_template: str,
    arms: Sequence[str] = PROBE_ARMS,
) -> ProbeReport:
    """The PI probe over the rendered diagnoses of ``arms`` (M3.2). ``pool_ids`` is the mining
    pool of the run's source; the tool vocabulary is what the failed and reference runs of
    this run's clusters called; the answer categories are those of the pool."""
    clusters, activity = load_clusters(run_dir)
    by_id = {c.id: c for c in clusters.clusters}
    alignments = {a.failure_key: a for a in load_alignments(run_dir)}
    pool_tasks = sorted((taskset.by_id(t) for t in pool_ids), key=lambda t: t.id)
    pool = [(t.id, one_line(t.prompt)) for t in pool_tasks]
    categories = sorted({c for c in (category_of(t) for t in pool_tasks) if c is not None})
    tools: set[str] = set(activity.tool_names)
    required: dict[str, tuple[str, ...]] = {}
    for c in clusters.clusters:
        record = alignments.get(c.representative)
        if record is None:
            required[c.id] = ()
            continue
        reference = _trajectory(
            rollout_dir(
                reference_run, record.task_id, record.reference_replicate, record.reference_attempt
            )
        )
        names = _tool_names(reference)
        tools |= names
        required[c.id] = tuple(sorted(names))
        failed, _ = _failed_trajectory(run_dir, record)
        tools |= _tool_names(failed)
    out = diagnosis_dir(run_dir)
    records: list[ProbeRecord] = []
    for arm in arms:
        rendered_path = out / "rendered" / f"{arm}-s{seed}" / "rendered.json"
        if not rendered_path.is_file():
            continue
        items = _load_list(rendered_path, RenderedCluster, what=str(rendered_path))
        origin_of: dict[str, str | None] = {}
        table_path = out / "assignments" / f"{arm}-s{seed}.json"
        if table_path.is_file():
            table = AssignmentTable.model_validate(read_json(table_path))
            origin_of = {a.cluster_id: a.origin_cluster for a in table.assignments}
        for item in items:
            if item.rendered is None:
                continue
            c = by_id[item.cluster_id]
            assigned_task = _parse_key(c.representative)[0]
            origin = origin_of.get(c.id)
            origin_task = (
                _parse_key(by_id[origin].representative)[0]
                if arm == "shuffled" and origin is not None and origin in by_id
                else assigned_task
            )
            records.append(
                probe_one(
                    item.rendered.text,
                    cluster_id=c.id,
                    arm=arm,
                    assigned_task=assigned_task,
                    origin_task=origin_task,
                    pool=pool,
                    tools=sorted(tools),
                    categories=categories,
                    required_tools=required.get(c.id, ()),
                    true_category=category_of(taskset.by_id(assigned_task)),
                    llm=llm,
                    prompt_template=prompt_template,
                    seed=seed,
                )
            )
    report = ProbeReport(
        seed=seed,
        pool_size=len(pool),
        chance_top1=1.0 / max(1, len(pool)),
        chance_top3=min(1.0, 3.0 / max(1, len(pool))),
        records=tuple(records),
    )
    atomic_write_text(out / f"probe-s{seed}.json", report.model_dump_json(indent=2) + "\n")
    return report


def per_failure_cost(run_dir: Path) -> dict[str, JsonValue]:
    """Ledger spend by arm (``replay``, ``diagnosis``) and per replayed failure."""
    from ahd.llm.ledger import read_ledger

    path = run_dir / "ledger.jsonl"
    rows = read_ledger(path) if path.is_file() else []
    by_arm: dict[str, float] = {}
    for row in rows:
        if row.event in ("call", "policy", "search"):
            by_arm[row.arm] = by_arm.get(row.arm, 0.0) + row.usd
    per_failure: dict[str, float] = {}
    for key, result in load_replays(run_dir).items():
        per_failure[key] = result.usd
    return to_json_value({"by_arm": by_arm, "replay_per_failure": per_failure})  # type: ignore[return-value]
