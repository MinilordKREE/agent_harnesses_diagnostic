"""E0 tables and report, regenerated deterministically from ``runs/E0`` (Credit-Without-GT
discipline: derived tables only, never raw trajectories; two runs of the script produce
byte-identical files).

No reference source: written fresh for ahd. Decision rules D1 to D6 are the owner's text in
``experiments/E0/spec.yaml``; :func:`decisions` applies them mechanically and prints the
observed values next to each verdict.
"""

from __future__ import annotations

import csv
import io
import json
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.io import atomic_write_text, read_json
from ahd.core.manifest import read_manifest
from ahd.diagnosis.cluster import ClusterSet, cluster
from ahd.diagnosis.corrupt import AssignmentTable
from ahd.diagnosis.genuineness import GenuinenessRecord
from ahd.diagnosis.leakage import LeakageReport
from ahd.diagnosis.pipeline import DiagnosisSet
from ahd.diagnosis.replay import ReplayResult
from ahd.errors import InfraError
from ahd.experiments.e0 import E0Spec, load_spec
from ahd.llm.ledger import LedgerRow, read_ledger
from ahd.runner.records import FailureRecord, RolloutRecord
from ahd.runner.spec import BENCHMARK_TRIALS_BY_SOURCE
from ahd.tasks.models import Score

SEARCH_SOURCES = ("browsecomp", "hle")
VALIDATION_TASKS_PER_SOURCE = 32
E2_PASSES = 2


# ---------------------------------------------------------------- readers


def _num(x: float | None, digits: int = 4) -> str:
    return "" if x is None else f"{x:.{digits}f}"


def rollouts(run_dir: Path) -> list[RolloutRecord]:
    out: list[RolloutRecord] = []
    markers = sorted(run_dir.glob("rollouts/*/*/done.json")) + sorted(
        run_dir.glob("rollouts/*/*/attempt_*/done.json")
    )
    for marker in markers:
        try:
            record = RolloutRecord.model_validate(read_json(marker))
        except ValidationError as exc:
            raise InfraError(f"corrupt {marker}: {exc}", kind="corrupt_file") from exc
        score_path = marker.parent / "score.json"
        if score_path.is_file():
            record = record.model_copy(
                update={"score": Score.model_validate(read_json(score_path))}
            )
        out.append(record)
    return sorted(out, key=lambda r: (r.task_id, r.replicate, r.attempt))


def failures(run_dir: Path) -> list[FailureRecord]:
    path = run_dir / "failures.json"
    if not path.is_file():
        return []
    raw = read_json(path)
    return [FailureRecord.model_validate(x) for x in raw] if isinstance(raw, list) else []


def ledger(run_dir: Path) -> list[LedgerRow]:
    path = run_dir / "ledger.jsonl"
    return read_ledger(path) if path.is_file() else []


def _json_list[T: StrictModel](path: Path, model: type[T]) -> list[T]:
    if not path.is_file():
        return []
    raw = read_json(path)
    return [model.model_validate(x) for x in raw] if isinstance(raw, list) else []


def replays(run_dir: Path, name: str = "replays.json") -> list[ReplayResult]:
    return _json_list(run_dir / "diagnosis" / name, ReplayResult)


def genuineness(ref_dir: Path) -> list[GenuinenessRecord]:
    return _json_list(ref_dir / "diagnosis" / "genuineness.json", GenuinenessRecord)


def diagnoses(run_dir: Path) -> DiagnosisSet | None:
    path = run_dir / "diagnosis" / "diagnoses.json"
    return DiagnosisSet.model_validate(read_json(path)) if path.is_file() else None


def clusters(run_dir: Path) -> ClusterSet | None:
    path = run_dir / "diagnosis" / "clusters.json"
    return ClusterSet.model_validate(read_json(path)) if path.is_file() else None


def leakage(run_dir: Path) -> LeakageReport | None:
    path = run_dir / "diagnosis" / "leakage.json"
    return LeakageReport.model_validate(read_json(path)) if path.is_file() else None


def assignments(run_dir: Path, seed: int = 0) -> dict[str, AssignmentTable]:
    out: dict[str, AssignmentTable] = {}
    for path in sorted((run_dir / "diagnosis" / "assignments").glob(f"*-s{seed}.json")):
        table = AssignmentTable.model_validate(read_json(path))
        out[table.arm] = table
    return out


def stage_log(runs_root: Path) -> list[dict[str, Any]]:
    path = runs_root / "stages.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def stage_seconds(
    rows: Sequence[dict[str, Any]], run_id: str, start: str, end: str
) -> float | None:
    """Seconds between the LAST ``start`` event and the last ``end`` event of a run."""
    starts = [r for r in rows if r.get("run_id") == run_id and r.get("event") == start]
    ends = [r for r in rows if r.get("run_id") == run_id and r.get("event") == end]
    if not starts or not ends:
        return None
    t0 = datetime.fromisoformat(str(starts[-1]["ts"]))
    t1 = datetime.fromisoformat(str(ends[-1]["ts"]))
    return max(0.0, (t1 - t0).total_seconds())


# ---------------------------------------------------------------- per-run aggregates


class RunAgg(StrictModel):
    run_id: str
    source: str
    tasks: int
    rollouts: int
    scored: int
    passed: int
    pass_hat_k_tasks: int
    policy_usd: list[float]
    wall: list[float]
    judge_usd: float
    judge_calls: int
    judge_cached: int
    diagnosis_usd: float
    replay_usd: float
    replay_rollouts: int
    search_usd: float
    serper_calls_approx: int
    infra: dict[str, int]
    usage_mismatch: int
    partial: int
    harness_failures: int
    infra_failures: int
    budget_failures: int
    git_sha: str
    spec_sha: str | None


def aggregate_run(run_dir: Path) -> RunAgg:
    manifest = read_manifest(run_dir / "manifest.json")
    records = rollouts(run_dir)
    rows = ledger(run_dir)
    summary_path = run_dir / "summary.json"
    summary = read_json(summary_path) if summary_path.is_file() else {}
    tasks = summary.get("tasks", []) if isinstance(summary, dict) else []
    pass_hat = sum(1 for t in tasks if isinstance(t, dict) and t.get("pass_hat_k"))
    source = next(
        (r.source_benchmark for r in records),
        run_dir.name.split("-")[1] if "-" in run_dir.name else "",
    )
    fails = failures(run_dir)
    infra_kinds = Counter(r.error_kind or "infra" for r in records if r.error_family == "infra")
    exp = manifest.experiment or {}
    return RunAgg(
        run_id=run_dir.name,
        source=source,
        tasks=len({r.task_id for r in records}),
        rollouts=len(records),
        scored=sum(1 for r in records if r.score is not None),
        passed=sum(1 for r in records if r.score is not None and r.score.passed),
        pass_hat_k_tasks=pass_hat,
        policy_usd=[r.usd for r in records if r.usd is not None],
        wall=[r.duration_seconds for r in records],
        judge_usd=sum(r.usd for r in rows if r.event == "call" and r.arm == "judge"),
        judge_calls=sum(1 for r in rows if r.event == "call" and r.arm == "judge"),
        judge_cached=sum(1 for r in rows if r.event == "call" and r.arm == "judge" and r.cached),
        diagnosis_usd=sum(r.usd for r in rows if r.event == "call" and r.arm == "diagnosis"),
        replay_usd=sum(r.usd for r in rows if r.event == "policy" and r.arm == "replay"),
        replay_rollouts=sum(1 for r in rows if r.event == "policy" and r.arm == "replay"),
        search_usd=sum(r.usd for r in rows if r.event == "search"),
        serper_calls_approx=sum(r.serper_calls_approx for r in records),
        infra=dict(sorted(infra_kinds.items())),
        usage_mismatch=sum(1 for r in records if r.error_kind == "usage_mismatch"),
        partial=sum(1 for r in records if r.partial),
        harness_failures=sum(1 for f in fails if f.family != "infra"),
        infra_failures=sum(1 for f in fails if f.family == "infra"),
        budget_failures=sum(1 for f in fails if f.family == "budget"),
        git_sha=manifest.git_sha,
        spec_sha=str(exp.get("spec_sha256")) if exp.get("spec_sha256") else None,
    )


def _stats(values: Sequence[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    return statistics.fmean(values), statistics.median(values), max(values)


# ---------------------------------------------------------------- CSV writing


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> Path:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    atomic_write_text(path, buffer.getvalue())
    return path


def _md_table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(str(h) for h in header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- pilot tables


def pilot_tables(spec: E0Spec, runs_root: Path, data_dir: Path) -> tuple[list[Path], list[str]]:
    written: list[Path] = []
    md: list[str] = []
    tasks_path = runs_root / "e0a_tasks.json"
    if not tasks_path.is_file():
        return written, ["E0a has not run (no runs/E0/e0a_tasks.json)."]
    tasks_by_source = read_json(tasks_path)
    assert isinstance(tasks_by_source, dict)
    written.append(
        write_csv(
            data_dir / "pilot_tasks.csv",
            ["source", "task_id"],
            [(s, t) for s in sorted(tasks_by_source) for t in tasks_by_source[s]],
        )
    )
    log = stage_log(runs_root)
    cost_rows: list[list[object]] = []
    time_rows: list[list[object]] = []
    extrap_rows: list[list[object]] = []
    totals: dict[str, float] = defaultdict(float)
    for source in spec.sources:
        run_dir = runs_root / f"e0a-{source}"
        if not (run_dir / "manifest.json").is_file():
            continue
        agg = aggregate_run(run_dir)
        ref_dir = runs_root / f"e0a-{source}-ref"
        ref = aggregate_run(ref_dir) if (ref_dir / "manifest.json").is_file() else None
        reps = replays(run_dir)
        gen = genuineness(ref_dir) if ref is not None else []
        n_fail = agg.harness_failures
        failed_tasks = len({f.task_id for f in failures(run_dir) if f.family != "infra"})
        p_mean, p_med, p_max = _stats(agg.policy_usd)
        w_mean, w_med, w_max = _stats(agg.wall)
        ref_usd = (sum(ref.policy_usd) + ref.judge_usd) if ref is not None else 0.0
        replay_usd = sum(r.usd for r in reps)
        diag_usd = agg.diagnosis_usd + (ref.diagnosis_usd if ref is not None else 0.0)
        types = Counter(r.failure_type for r in reps)
        cost_rows.append(
            [
                source,
                agg.tasks,
                agg.rollouts,
                _num(agg.passed / agg.scored if agg.scored else None),
                _num(agg.pass_hat_k_tasks / agg.tasks if agg.tasks else None),
                _num(p_mean),
                _num(p_med),
                _num(p_max),
                _num(sum(agg.policy_usd)),
                _num(w_mean, 1),
                _num(w_med, 1),
                _num(w_max, 1),
                _num(agg.judge_usd),
                agg.judge_calls,
                agg.judge_cached,
                n_fail,
                failed_tasks,
                ref.rollouts if ref is not None else 0,
                _num(ref_usd),
                _num(ref_usd / failed_tasks if failed_tasks else None),
                sum(1 for g in gen if g.verdict == "genuine"),
                len(reps),
                _num(replay_usd),
                _num(replay_usd / len(reps) if reps else None),
                agg.replay_rollouts,
                _num(diag_usd),
                _num(diag_usd / len(reps) if reps else None),
                types.get("deterministic", 0),
                types.get("stochastic", 0),
                types.get("unrepairable", 0),
                types.get("unreplayable", 0),
                agg.infra_failures,
                agg.usage_mismatch,
                agg.partial,
                agg.serper_calls_approx,
                _num(agg.search_usd),
                _num(
                    sum(agg.policy_usd)
                    + agg.judge_usd
                    + ref_usd
                    + replay_usd
                    + diag_usd
                    + agg.search_usd
                ),
            ]
        )
        run_wall = stage_seconds(log, run_dir.name, "run_start", "run_done")
        ref_wall = (
            stage_seconds(log, ref_dir.name, "run_start", "run_done") if ref is not None else None
        )
        replay_wall = stage_seconds(log, run_dir.name, "replay_start", "replay_done")
        diag_wall = stage_seconds(log, run_dir.name, "run_done", "diagnosis_done")
        time_rows.append(
            [
                source,
                agg.rollouts,
                spec.workers,
                _num(run_wall, 0),
                _num(sum(agg.wall), 0),
                _num(ref_wall, 0),
                _num(replay_wall, 0),
                _num(replay_wall / len(reps) if replay_wall is not None and reps else None, 0),
                _num(diag_wall, 0),
            ]
        )
        # extrapolation to E0b sizes (spec) from pilot per-unit costs
        trials = BENCHMARK_TRIALS_BY_SOURCE.get(source, 1)
        b2 = spec.E0b.get("B2_heldout") if isinstance(spec.E0b.get("B2_heldout"), dict) else {}
        heldout_n = int(str(b2.get("per_source", 30))) if isinstance(b2, dict) else 30
        b1_rollouts = VALIDATION_TASKS_PER_SOURCE * trials * E2_PASSES
        b2_rollouts = heldout_n * trials * E2_PASSES
        fail_rate = (1 - agg.passed / agg.scored) if agg.scored else 0.0
        exp_failures = b1_rollouts * fail_rate
        exp_failed_tasks = (
            VALIDATION_TASKS_PER_SOURCE
            * E2_PASSES
            * (failed_tasks / agg.tasks if agg.tasks else 0.0)
        )
        per_rollout = p_mean or 0.0
        judge_per_rollout = agg.judge_usd / agg.rollouts if agg.rollouts else 0.0
        ref_per_task = ref_usd / failed_tasks if failed_tasks else 0.0
        replay_per_failure = replay_usd / len(reps) if reps else 0.0
        diag_per_failure = diag_usd / len(reps) if reps else 0.0
        policy_b1 = b1_rollouts * per_rollout
        policy_b2 = b2_rollouts * per_rollout
        judge_est = (b1_rollouts + b2_rollouts) * judge_per_rollout
        ref_est = exp_failed_tasks * ref_per_task
        replay_est = exp_failures * replay_per_failure
        diag_est = exp_failures * diag_per_failure
        total_est = policy_b1 + policy_b2 + judge_est + ref_est + replay_est + diag_est
        wall_rollout = w_mean or 0.0
        replay_wall_per_failure = (
            (replay_wall / len(reps)) if replay_wall is not None and reps else 0.0
        )
        wall_hours = (
            (b1_rollouts + b2_rollouts) * wall_rollout / spec.workers
            + exp_failures * replay_wall_per_failure / spec.workers
            + ref_est / max(per_rollout, 1e-9) * wall_rollout / spec.workers
        ) / 3600
        extrap_rows.append(
            [
                source,
                b1_rollouts,
                b2_rollouts,
                _num(fail_rate),
                _num(exp_failures, 1),
                _num(exp_failed_tasks, 1),
                _num(policy_b1),
                _num(policy_b2),
                _num(judge_est),
                _num(ref_est),
                _num(replay_est),
                _num(diag_est),
                _num(total_est),
                _num(wall_hours, 1),
            ]
        )
        for key, value in (
            ("policy_b1", policy_b1),
            ("policy_b2", policy_b2),
            ("judge", judge_est),
            ("reference", ref_est),
            ("replay", replay_est),
            ("diagnosis", diag_est),
            ("total", total_est),
            ("wall_hours", wall_hours),
            ("b1_rollouts", b1_rollouts),
            ("b2_rollouts", b2_rollouts),
            ("exp_failures", exp_failures),
        ):
            totals[key] += value
    if extrap_rows:
        extrap_rows.append(
            [
                "TOTAL",
                int(totals["b1_rollouts"]),
                int(totals["b2_rollouts"]),
                "",
                _num(totals["exp_failures"], 1),
                "",
                _num(totals["policy_b1"]),
                _num(totals["policy_b2"]),
                _num(totals["judge"]),
                _num(totals["reference"]),
                _num(totals["replay"]),
                _num(totals["diagnosis"]),
                _num(totals["total"]),
                _num(totals["wall_hours"], 1),
            ]
        )
    cost_header = [
        "source",
        "tasks",
        "rollouts",
        "rollout_pass_rate",
        "pass_hat_k_rate",
        "policy_usd_mean",
        "policy_usd_median",
        "policy_usd_max",
        "policy_usd_total",
        "wall_s_mean",
        "wall_s_median",
        "wall_s_max",
        "judge_usd",
        "judge_calls",
        "judge_cached",
        "harness_failures",
        "failed_tasks",
        "reference_rollouts",
        "reference_usd",
        "reference_usd_per_failed_task",
        "genuine_references",
        "failures_replayed",
        "replay_usd",
        "replay_usd_per_failure",
        "replay_rollouts",
        "diagnosis_usd",
        "diagnosis_usd_per_failure",
        "ft_deterministic",
        "ft_stochastic",
        "ft_unrepairable",
        "ft_unreplayable",
        "infra_failures",
        "usage_mismatch",
        "partial_trajectories",
        "serper_calls_approx",
        "search_usd",
        "source_total_usd",
    ]
    time_header = [
        "source",
        "rollouts",
        "workers",
        "run_wall_s",
        "rollout_wall_sum_s",
        "reference_wall_s",
        "replay_wall_s",
        "replay_wall_s_per_failure",
        "diagnosis_wall_s",
    ]
    extrap_header = [
        "source",
        "b1_rollouts",
        "b2_rollouts",
        "rollout_fail_rate",
        "expected_failures",
        "expected_failed_tasks",
        "policy_usd_b1",
        "policy_usd_b2",
        "judge_usd",
        "reference_usd",
        "replay_usd",
        "diagnosis_usd",
        "total_usd",
        "wall_hours_at_workers",
    ]
    drift_rows, host_rows = pilot_findings(spec, runs_root)
    drift_header = [
        "source",
        "failure_key",
        "failure_type",
        "oracle_step",
        "oracle_step_basis",
        "candidates",
        "candidate_statuses",
        "unreplayable_rollouts",
        "drift_reasons",
        "usd",
    ]
    host_header = ["source", "host", "curl_commands"]
    written.append(write_csv(data_dir / "pilot_drift.csv", drift_header, drift_rows))
    written.append(write_csv(data_dir / "pilot_web_hosts.csv", host_header, host_rows))
    md.append(
        "### E0a pilot: replay verdicts and prefix drift\n\n"
        "One row per replayed failure; `drift_reasons` counts why prefix re-execution was "
        "declared unreplayable (exit codes or mutated/quoted outputs differing after masking).\n\n"
        + _md_table(drift_header, drift_rows)
    )
    md.append(
        "### E0a pilot: web hosts fetched by the seed policy\n\n"
        "Hosts in `curl`/`wget` commands of the pilot rollouts (top 8 per source). The seed "
        "harness has no search tool; Serper is only counted when the policy calls it "
        "explicitly, so `serper_calls_approx` understates web use.\n\n"
        + _md_table(host_header, host_rows)
    )
    written.append(write_csv(data_dir / "pilot_cost.csv", cost_header, cost_rows))
    written.append(write_csv(data_dir / "pilot_time.csv", time_header, time_rows))
    written.append(write_csv(data_dir / "pilot_extrapolation.csv", extrap_header, extrap_rows))
    md.append("### E0a pilot: cost per source\n\n" + _md_table(cost_header, cost_rows))
    md.append("### E0a pilot: wall clock\n\n" + _md_table(time_header, time_rows))
    md.append(
        "### E0a pilot: extrapolation to E0b sizes\n\n"
        "B1 = 32 validation tasks x benchmark trials x 2 passes; B2 = held-out per_source x "
        "trials x 2 passes; expected failures use the pilot's rollout fail rate; reference, "
        "replay and diagnosis costs use the pilot's per-unit costs (zero when the pilot had no "
        "failure in that source).\n\n" + _md_table(extrap_header, extrap_rows)
    )
    return written, md


def pilot_findings(spec: E0Spec, runs_root: Path) -> tuple[list[list[object]], list[list[object]]]:
    """Derived only: replay verdicts with drift reasons, and the web hosts the policy fetched."""
    import re

    drift_rows: list[list[object]] = []
    host_rows: list[list[object]] = []
    host_re = re.compile(r"https?://([\w.-]+)")
    for source in spec.sources:
        run_dir = runs_root / f"e0a-{source}"
        if not (run_dir / "manifest.json").is_file():
            continue
        for r in replays(run_dir):
            statuses = ";".join(f"{c.step}:{c.status}" for c in r.candidates)
            unreplayable = sum(
                1
                for c in r.candidates
                for arm in (c.substitute, c.control)
                for x in arm.rollouts
                if x.status == "unreplayable"
            )
            reasons: Counter[str] = Counter()
            for report in r.drift_reports.values():
                if isinstance(report, dict):
                    drifts = report.get("drifts")
                    if isinstance(drifts, list):
                        for d in drifts:
                            if isinstance(d, dict):
                                reasons[str(d.get("reason"))] += 1
            drift_rows.append(
                [
                    source,
                    r.failure_key,
                    r.failure_type,
                    r.oracle_step,
                    r.oracle_step_basis,
                    len(r.candidates),
                    statuses,
                    unreplayable,
                    ";".join(f"{k}={v}" for k, v in sorted(reasons.items())),
                    _num(r.usd),
                ]
            )
        hosts: Counter[str] = Counter()
        for marker in sorted(run_dir.glob("rollouts/*/*/done.json")):
            trajectory_path = marker.parent / "trajectory.json"
            if not trajectory_path.is_file():
                continue
            trajectory = read_json(trajectory_path)
            if not isinstance(trajectory, dict):
                continue
            for entry in trajectory.get("trajectory", []):
                if not isinstance(entry, dict) or entry.get("role") != "tool":
                    continue
                call = entry.get("tool_call")
                if not isinstance(call, dict) or call.get("name") != "run_shell_command":
                    continue
                args = call.get("arguments")
                command = str(args.get("command", "")) if isinstance(args, dict) else ""
                if "curl" in command or "wget" in command:
                    for host in set(host_re.findall(command)):
                        hosts[host] += 1
        for host, count in sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
            host_rows.append([source, host, count])
    return drift_rows, host_rows


# ---------------------------------------------------------------- E0b tables


def _bootstrap_delta(
    a: Sequence[bool], b: Sequence[bool], *, seed: int = 0, n: int = 2000
) -> tuple[float, float]:
    """95% bootstrap CI (over tasks) of |mean(a) - mean(b)| in points."""
    rng = random.Random(seed)
    pairs = list(zip(a, b, strict=True))
    if not pairs:
        return 0.0, 0.0
    deltas: list[float] = []
    for _ in range(n):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        deltas.append(
            abs(statistics.fmean(x for x, _ in sample) - statistics.fmean(y for _, y in sample))
            * 100
        )
    deltas.sort()
    return deltas[int(0.025 * n)], deltas[min(n - 1, int(0.975 * n))]


def _task_pass_hat(run_dir: Path) -> dict[str, bool]:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return {}
    summary = read_json(summary_path)
    assert isinstance(summary, dict)
    out: dict[str, bool] = {}
    for t in summary.get("tasks", []):
        if isinstance(t, dict):
            out[str(t["task_id"])] = bool(t.get("pass_hat_k"))
    return out


class SourceCalibration(StrictModel):
    source: str
    pass_rate: float | None
    aa_delta_points: float | None
    aa_ci: tuple[float, float] | None
    heldout_delta_points: float | None
    clusters_with_two: int
    primary_clusters: int


def e0b_tables(
    spec: E0Spec, runs_root: Path, data_dir: Path
) -> tuple[list[Path], list[str], dict[str, SourceCalibration], dict[str, Any]]:
    written: list[Path] = []
    md: list[str] = []
    calib: dict[str, SourceCalibration] = {}
    extras: dict[str, Any] = {}
    b1 = {s: sorted(runs_root.glob(f"e0b-b1-{s}-p*")) for s in spec.sources}
    b2 = {s: sorted(runs_root.glob(f"e0b-b2-{s}-p*")) for s in spec.sources}
    if not any(b1.values()):
        return written, ["E0b has not run."], calib, extras
    baseline_rows: list[list[object]] = []
    task_rows: list[list[object]] = []
    aa_rows: list[list[object]] = []
    ft_rows: list[list[object]] = []
    ref_rows: list[list[object]] = []
    replay_rows: list[list[object]] = []
    cluster_rows: list[list[object]] = []
    feas_rows: list[list[object]] = []
    leak_rows: list[list[object]] = []
    cost_rows: list[list[object]] = []
    infra_rows: list[list[object]] = []
    for source in spec.sources:
        dirs = [d for d in b1[source] if (d / "manifest.json").is_file()]
        if not dirs:
            continue
        aggs = [aggregate_run(d) for d in dirs]
        passes = []
        for d, agg in zip(dirs, aggs, strict=True):
            rate = agg.passed / agg.scored if agg.scored else None
            passes.append(rate)
            baseline_rows.append(
                [
                    source,
                    d.name,
                    agg.tasks,
                    agg.rollouts,
                    _num(rate),
                    _num(agg.pass_hat_k_tasks / agg.tasks if agg.tasks else None),
                ]
            )
            for task_id, ok in sorted(_task_pass_hat(d).items()):
                task_rows.append([source, d.name, task_id, int(ok)])
            infra_rows.append(
                [
                    source,
                    d.name,
                    agg.infra_failures,
                    json.dumps(agg.infra, sort_keys=True),
                    agg.usage_mismatch,
                    agg.partial,
                    agg.budget_failures,
                ]
            )
            cost_rows.append(
                [
                    source,
                    d.name,
                    agg.rollouts,
                    _num(sum(agg.policy_usd) / agg.rollouts if agg.rollouts else None),
                    _num(agg.judge_usd / agg.rollouts if agg.rollouts else None),
                    _num(agg.replay_usd),
                    _num(agg.diagnosis_usd),
                    _num(agg.search_usd),
                    _num(
                        sum(agg.policy_usd)
                        + agg.judge_usd
                        + agg.replay_usd
                        + agg.diagnosis_usd
                        + agg.search_usd
                    ),
                ]
            )
        aa_delta = None
        ci = None
        if len(dirs) >= 2:
            p1, p2 = _task_pass_hat(dirs[0]), _task_pass_hat(dirs[1])
            common = sorted(set(p1) & set(p2))
            a = [p1[t] for t in common]
            b = [p2[t] for t in common]
            if common:
                aa_delta = abs(statistics.fmean(a) - statistics.fmean(b)) * 100
                ci = _bootstrap_delta(a, b)
                agreement = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(common)
                aa_rows.append(
                    [
                        source,
                        "validation",
                        len(common),
                        _num(agreement),
                        _num(aa_delta, 2),
                        _num(ci[0], 2),
                        _num(ci[1], 2),
                    ]
                )
        held_delta = None
        hdirs = [d for d in b2[source] if (d / "summary.json").is_file()]
        if len(hdirs) >= 2:
            p1, p2 = _task_pass_hat(hdirs[0]), _task_pass_hat(hdirs[1])
            common = sorted(set(p1) & set(p2))
            if common:
                a = [p1[t] for t in common]
                b = [p2[t] for t in common]
                held_delta = abs(statistics.fmean(a) - statistics.fmean(b)) * 100
                hci = _bootstrap_delta(a, b)
                agreement = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(common)
                aa_rows.append(
                    [
                        source,
                        "heldout",
                        len(common),
                        _num(agreement),
                        _num(held_delta, 2),
                        _num(hci[0], 2),
                        _num(hci[1], 2),
                    ]
                )
        # failures, references, replays across passes
        all_reps = [r for d in dirs for r in replays(d)]
        full = [r for d in dirs for r in replays(d, "replays_replay_full.json")]
        types = Counter(r.failure_type for r in all_reps)
        for t in ("deterministic", "stochastic", "unrepairable", "unreplayable"):
            ft_rows.append([source, t, types.get(t, 0)])
        gens = [g for d in dirs for g in genuineness(runs_root / f"{d.name}-ref")]
        failed_tasks = len({f.task_id for d in dirs for f in failures(d) if f.family != "infra"})
        verdicts = Counter(g.verdict for g in gens)
        ref_rows.append(
            [
                source,
                failed_tasks,
                len(gens),
                verdicts.get("genuine", 0),
                verdicts.get("shortcut", 0),
                verdicts.get("undetermined", 0),
            ]
        )
        ctl = [
            c.control.pass_fraction
            for r in full
            for c in r.candidates
            if not c.control.skipped and c.control.scored
        ]
        replay_rows.append(
            [
                source,
                len(all_reps),
                sum(1 for r in all_reps if r.oracle_step is not None),
                types.get("deterministic", 0),
                types.get("stochastic", 0),
                types.get("unrepairable", 0),
                types.get("unreplayable", 0),
                _num(
                    statistics.fmean(len(r.candidates) for r in all_reps) if all_reps else None, 2
                ),
                len(full),
                _num(statistics.fmean(ctl) if ctl else None),
            ]
        )
        # clusters merged across passes (same cause+component key)
        all_ref_diags = [
            d for run in dirs for ds in [diagnoses(run)] if ds is not None for d in ds.reference
        ]
        merged = cluster(all_ref_diags) if all_ref_diags else None
        with_two = 0
        primary = 0
        if merged is not None:
            sizes = sorted((len(c.members) for c in merged.clusters), reverse=True)
            with_two = sum(1 for c in merged.clusters if len(c.members) >= 2)
            det = sum(1 for c in merged.clusters if c.failure_types.get("deterministic", 0) > 0)
            cluster_rows.append(
                [
                    source,
                    len(merged.clusters),
                    json.dumps(sizes),
                    _num(sum(1 for s in sizes if s == 1) / len(sizes) if sizes else None),
                    _num(det / len(merged.clusters) if merged.clusters else None),
                    with_two,
                ]
            )
            feasible_by_cluster: dict[str, dict[str, str]] = {}
            for run in dirs:
                for arm, table in assignments(run).items():
                    if arm in ("reference", "system"):
                        continue
                    for item in table.assignments:
                        feasible_by_cluster.setdefault(item.cluster_id, {})[arm] = (
                            "feasible"
                            if item.impossible is None
                            else f"impossible: {item.impossible}"
                        )
            for cid in sorted(feasible_by_cluster):
                fe = feasible_by_cluster[cid]
                arms_order = (
                    "corrupt_where_near",
                    "corrupt_where_far",
                    "corrupt_why",
                    "corrupt_how",
                    "shuffled",
                )
                feas_row: list[object] = [source, cid, *[fe.get(arm, "") for arm in arms_order]]
                feas_rows.append(feas_row)
            for c in merged.clusters:
                validated = c.oracle_validated_members > 0
                per_run_feasible = any(
                    all(
                        fe.get(arm, "").startswith("feasible")
                        for arm in (
                            "corrupt_where_near",
                            "corrupt_where_far",
                            "corrupt_why",
                            "corrupt_how",
                            "shuffled",
                        )
                    )
                    for cid, fe in feasible_by_cluster.items()
                    if cid == c.id
                )
                if len(c.members) >= 2 and validated and per_run_feasible:
                    primary += 1
        for run in dirs:
            lk = leakage(run)
            if lk is not None:
                leak_rows.append(
                    [
                        source,
                        run.name,
                        lk.n,
                        _num(lk.top1_rate),
                        _num(lk.top3_rate),
                        _num(lk.chance_top1),
                    ]
                )
        rates = [p for p in passes if p is not None]
        calib[source] = SourceCalibration(
            source=source,
            pass_rate=statistics.fmean(rates) if rates else None,
            aa_delta_points=aa_delta,
            aa_ci=ci,
            heldout_delta_points=held_delta,
            clusters_with_two=with_two,
            primary_clusters=primary,
        )
    written.append(
        write_csv(
            data_dir / "baseline.csv",
            ["source", "run_id", "tasks", "rollouts", "rollout_pass_rate", "pass_hat_k_rate"],
            baseline_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "baseline_tasks.csv",
            ["source", "run_id", "task_id", "pass_hat_k"],
            task_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "aa_band.csv",
            ["source", "split", "tasks", "agreement", "delta_points", "ci95_low", "ci95_high"],
            aa_rows,
        )
    )
    written.append(
        write_csv(data_dir / "failure_types.csv", ["source", "failure_type", "count"], ft_rows)
    )
    written.append(
        write_csv(
            data_dir / "reference_rates.csv",
            ["source", "failed_tasks", "references", "genuine", "shortcut", "undetermined"],
            ref_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "replay.csv",
            [
                "source",
                "failures_replayed",
                "oracle_validated",
                "deterministic",
                "stochastic",
                "unrepairable",
                "unreplayable",
                "mean_candidates",
                "full_arms_failures",
                "control_pass_fraction_mean",
            ],
            replay_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "clusters.csv",
            [
                "source",
                "n_clusters",
                "sizes",
                "singleton_fraction",
                "deterministic_fraction",
                "clusters_with_two",
            ],
            cluster_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "corruption_feasibility.csv",
            ["source", "cluster_id", "near", "far", "why", "how", "all"],
            feas_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "leakage.csv",
            ["source", "run_id", "n", "top1", "top3", "chance_top1"],
            leak_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "cost.csv",
            [
                "source",
                "run_id",
                "rollouts",
                "policy_usd_per_rollout",
                "judge_usd_per_rollout",
                "replay_usd",
                "diagnosis_usd",
                "search_usd",
                "total_usd",
            ],
            cost_rows,
        )
    )
    written.append(
        write_csv(
            data_dir / "infra.csv",
            [
                "source",
                "run_id",
                "infra_failures",
                "infra_by_kind",
                "usage_mismatch",
                "partial",
                "budget_failures",
            ],
            infra_rows,
        )
    )
    # judge calibration
    jc_path = runs_root / "judge_calibration.json"
    jc_rows: list[list[object]] = []
    if jc_path.is_file():
        raw = read_json(jc_path)
        rows_: list[dict[str, Any]] = (
            [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        )
        judged = [
            r for r in rows_ if r.get("error") is None and r.get("pro_bypass_passed") is not None
        ]
        self_c = (
            sum(1 for r in judged if r["pro_bypass_passed"] == r["original_passed"]) / len(judged)
            if judged
            else None
        )
        flash = (
            sum(1 for r in judged if r.get("flash_passed") == r["original_passed"]) / len(judged)
            if judged
            else None
        )
        jc_rows.append(
            [
                len(rows_),
                len(judged),
                _num(self_c),
                _num(flash),
                "none: the Evo-Bench snapshot releases expected answers and rubrics, "
                "no per-trajectory judge labels",
            ]
        )
        extras["judge_self_consistency"] = self_c
        extras["judge_flash_agreement"] = flash
    written.append(
        write_csv(
            data_dir / "judge_calibration.csv",
            [
                "artifacts",
                "rejudged",
                "self_consistency",
                "flash_vs_pro_agreement",
                "released_labels",
            ],
            jc_rows,
        )
    )
    md.append(
        "### E0b: baseline\n\n"
        + _md_table(
            ["source", "run_id", "tasks", "rollouts", "rollout_pass_rate", "pass_hat_k_rate"],
            baseline_rows,
        )
    )
    md.append(
        "### E0b: A/A bands\n\n"
        + _md_table(
            ["source", "split", "tasks", "agreement", "delta_points", "ci95_low", "ci95_high"],
            aa_rows,
        )
    )
    md.append(
        "### E0b: failure types\n\n" + _md_table(["source", "failure_type", "count"], ft_rows)
    )
    md.append(
        "### E0b: references\n\n"
        + _md_table(
            ["source", "failed_tasks", "references", "genuine", "shortcut", "undetermined"],
            ref_rows,
        )
    )
    md.append(
        "### E0b: replay\n\n"
        + _md_table(
            [
                "source",
                "failures_replayed",
                "oracle_validated",
                "deterministic",
                "stochastic",
                "unrepairable",
                "unreplayable",
                "mean_candidates",
                "full_arms_failures",
                "control_pass_fraction_mean",
            ],
            replay_rows,
        )
    )
    md.append(
        "### E0b: clusters\n\n"
        + _md_table(
            [
                "source",
                "n_clusters",
                "sizes",
                "singleton_fraction",
                "deterministic_fraction",
                "clusters_with_two",
            ],
            cluster_rows,
        )
    )
    md.append(
        "### E0b: leakage\n\n"
        + _md_table(["source", "run_id", "n", "top1", "top3", "chance_top1"], leak_rows)
    )
    md.append(
        "### E0b: judge calibration\n\n"
        + _md_table(
            [
                "artifacts",
                "rejudged",
                "self_consistency",
                "flash_vs_pro_agreement",
                "released_labels",
            ],
            jc_rows,
        )
    )
    return written, md, calib, extras


# ---------------------------------------------------------------- decision rules


def decisions(
    spec: E0Spec,
    calib: dict[str, SourceCalibration],
    extras: dict[str, Any],
    *,
    cost_per_rollout: float | None,
) -> list[list[object]]:
    th = spec.thresholds
    rows: list[list[object]] = []
    if not calib:
        for rule in sorted(spec.decision_rules):
            rows.append([rule, "E0b not run", "not evaluable"])
        return rows
    for source in spec.sources:
        c = calib.get(source)
        if c is None:
            rows.append([f"D1:{source}", "no B1 runs", "not evaluable"])
            continue
        ok_rate = (
            c.pass_rate is not None and th["pass_rate_min"] <= c.pass_rate <= th["pass_rate_max"]
        )
        ok_band = c.aa_delta_points is not None and c.aa_delta_points <= th["aa_band_max_points"]
        ok_clusters = c.clusters_with_two >= int(th["min_clusters_with_two"])
        observed = (
            f"pass_rate={_num(c.pass_rate)} aa_delta={_num(c.aa_delta_points, 2)} "
            f"clusters_with_two={c.clusters_with_two}"
        )
        rows.append(
            [
                f"D1:{source}",
                observed,
                "enters E2" if ok_rate and ok_band and ok_clusters else "excluded",
            ]
        )
    for source in spec.sources:
        c = calib.get(source)
        if c is not None:
            rows.append(
                [
                    f"D2:{source}",
                    f"primary_clusters={c.primary_clusters}",
                    f"{c.primary_clusters} primary; rest secondary",
                ]
            )
    others = [
        v
        for v in (
            calib[s].aa_delta_points for s in spec.sources if s not in SEARCH_SOURCES and s in calib
        )
        if v is not None
    ]
    for source in SEARCH_SOURCES:
        c = calib.get(source)
        if c is None or c.aa_delta_points is None or not others:
            rows.append([f"D3:{source}", "missing A/A data", "not evaluable"])
            continue
        widest_other = max(others)
        d1 = next((r[2] == "enters E2" for r in rows if r[0] == f"D1:{source}"), False)
        keep = d1 and c.aa_delta_points <= th["search_band_ratio_max"] * max(widest_other, 1e-9)
        rows.append(
            [
                f"D3:{source}",
                f"aa_delta={_num(c.aa_delta_points, 2)} widest_other={_num(widest_other, 2)}",
                "stays in E2" if keep else "descriptive only",
            ]
        )
    n_primary = sum(c.primary_clusters for c in calib.values())
    if spec.owner_budget_usd is None or cost_per_rollout is None:
        rows.append(
            [
                "D4",
                f"primary_clusters={n_primary} budget={spec.owner_budget_usd}",
                "not evaluable: owner_budget_usd not set",
            ]
        )
    else:
        for k in (3, 2):
            projected = th["e2_arms"] * n_primary * k * cost_per_rollout
            if projected <= spec.owner_budget_usd or k == 2:
                rows.append(
                    [
                        "D4",
                        f"projected_usd(k={k})={_num(projected, 2)} budget={spec.owner_budget_usd}",
                        f"k={k}",
                    ]
                )
                break
    for source in spec.sources:
        c = calib.get(source)
        if c is None or c.heldout_delta_points is None:
            rows.append([f"D5:{source}", "no held-out A/A", "not evaluable"])
        else:
            wide = c.heldout_delta_points > th["aa_band_max_points"]
            size = int(th["heldout_per_source_wide" if wide else "heldout_per_source"])
            rows.append(
                [
                    f"D5:{source}",
                    f"heldout_delta={_num(c.heldout_delta_points, 2)}",
                    f"{size}/source",
                ]
            )
    sc = extras.get("judge_self_consistency")
    fl = extras.get("judge_flash_agreement")
    if sc is None:
        rows.append(["D6", "no judge calibration", "not evaluable"])
    else:
        vote = "2-of-3 judge vote" if sc < th["judge_self_consistency_min"] else "single judge"
        both = (
            "; report both judges"
            if fl is not None and fl < th["judge_cross_agreement_min"]
            else ""
        )
        rows.append(["D6", f"self_consistency={_num(sc)} flash_agreement={_num(fl)}", vote + both])
    return rows


# ---------------------------------------------------------------- entry point


def build_report(*, spec_path: Path, data_dir: Path, report_path: Path) -> list[Path]:
    spec = load_spec(spec_path)
    runs_root = Path(spec.runs_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    md: list[str] = [
        "# E0 calibration report\n\n"
        f"Spec: `{spec_path}`; runs: `{runs_root}`; policy {spec.policy}; judge {spec.judge}; "
        f"mock_today {spec.mock_today}; replay k={spec.replay.k}, "
        f"candidates<={spec.replay.max_candidates}, economize={spec.replay.economize}; "
        f"T_att={spec.reference_max_attempts}; workers={spec.workers}."
    ]
    shas = sorted(
        {aggregate_run(d).git_sha for d in runs_root.glob("e0*") if (d / "manifest.json").is_file()}
    )
    spec_shas = sorted(
        {
            str(aggregate_run(d).spec_sha)
            for d in runs_root.glob("e0*")
            if (d / "manifest.json").is_file()
        }
    )
    md.append(
        f"Run git sha(s): {', '.join(shas) or 'none'}; "
        f"spec sha(s) recorded in manifests: {', '.join(spec_shas) or 'none'}."
    )
    pilot_written, pilot_md = pilot_tables(spec, runs_root, data_dir)
    written.extend(pilot_written)
    md.append("## E0a pilot\n\n" + "\n\n".join(pilot_md))
    b_written, b_md, calib, extras = e0b_tables(spec, runs_root, data_dir)
    written.extend(b_written)
    md.append("## E0b calibration\n\n" + "\n\n".join(b_md))
    cost_path = data_dir / "cost.csv"
    cost_per_rollout: float | None = None
    if cost_path.is_file():
        with cost_path.open(encoding="utf-8") as fh:
            values = [
                float(r["policy_usd_per_rollout"]) + float(r["judge_usd_per_rollout"])
                for r in csv.DictReader(fh)
                if r["policy_usd_per_rollout"] and r["judge_usd_per_rollout"]
            ]
        cost_per_rollout = statistics.fmean(values) if values else None
    rows = decisions(spec, calib, extras, cost_per_rollout=cost_per_rollout)
    written.append(write_csv(data_dir / "decisions.csv", ["rule", "observed", "decision"], rows))
    md.append(
        "## Decision rules\n\n"
        + "\n\n".join(f"**{k}.** {v.strip()}" for k, v in sorted(spec.decision_rules.items()))
        + "\n\n"
        + _md_table(["rule", "observed", "decision"], rows)
    )
    atomic_write_text(report_path, "\n\n".join(md) + "\n")
    written.append(report_path)
    return written
