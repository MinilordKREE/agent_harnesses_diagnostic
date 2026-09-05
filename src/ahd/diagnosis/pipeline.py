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
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import JsonValue, to_json_value
from ahd.core.io import atomic_write_text, read_json
from ahd.core.manifest import update_manifest_diagnosis
from ahd.diagnosis import corrupt as corrupt_module
from ahd.diagnosis.align import Alignment, actions_from_trajectory, align
from ahd.diagnosis.attribution import active_steps
from ahd.diagnosis.cluster import ClusterSet, FailureCluster, cluster, failure_key
from ahd.diagnosis.corrupt import ARM_CORRUPTION, AssignmentTable
from ahd.diagnosis.genuineness import GenuinenessRecord, verify
from ahd.diagnosis.leakage import LeakageReport, probe
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.replay import Replayer, ReplayResult
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
from ahd.tasks.models import TaskSet

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
        task = taskset.by_id(record.task_id)
        failed, failed_dir = _failed_trajectory(run_dir, record)
        reference = _trajectory(
            rollout_dir(
                reference_run, record.task_id, record.reference_replicate, record.reference_attempt
            )
        )
        results.append(
            replayer.validate(
                task,
                failed_trajectory=failed,
                reference_trajectory=reference,
                alignment=record.alignment,
                replicate=record.replicate,
                attempt=record.attempt,
                recorded_workspace=_recorded_workspace(failed_dir),
            )
        )
    if subdir != "replay":
        atomic_write_text(diagnosis_dir(run_dir) / f"replays_{subdir}.json", _dump(results))
        return results
    atomic_write_text(diagnosis_dir(run_dir) / "replays.json", _dump(results))
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
    return results


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
        sufficient[c.id] = replay.sufficient_set if replay is not None else ()
        for step_actions in actions_from_trajectory(trajectory):
            tool_names.update(a.name for a in step_actions.actions if a.klass == "tool")
    activity_record = ClusterActivity(
        activity=activity, sufficient=sufficient, tool_names=tuple(sorted(tool_names))
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
) -> tuple[Diagnosis | None, str | None]:
    """The diagnosis a proposer in ``arm`` would receive for this cluster, or why none."""
    if arm == "system":
        system = system_by_key.get(cluster_.representative)
        if system is None:
            return None, "no system diagnosis for the representative"
        return system, None
    if assignment.impossible is not None:
        return None, assignment.impossible
    return corrupt_module.apply(cluster_.diagnosis_reference, assignment, usable), None


def corrupt_run(
    run_dir: Path,
    *,
    seed: int,
    manifest: ComponentManifest,
    arms: Sequence[str] = tuple(ARM_CORRUPTION),
    template: str | None = None,
) -> dict[str, tuple[AssignmentTable, list[RenderedCluster]]]:
    """Assignment tables for every arm (written before any rendering), then rendering with
    per-cluster caps taken across all arms of this seed (owner decision, M3.1). Returns the
    requested arms only; the tables of the other arms are still written."""
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
            per_arm_diag[arm][c.id] = _arm_diagnosis(arm, c, assignment, usable, system_by_key)
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
