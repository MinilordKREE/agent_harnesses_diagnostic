"""E0d tables (M3.2 addendum): seed noise, replay escalation, COH-WRONG parity, the PI probe,
component ambiguity, decoy exclusion, the MDE table (D8) and the measurability funnel.

No reference source: written fresh for ahd. Every table is derived from files under
``runs/E0`` and regenerates byte-identically (the MDE simulation is seeded).
"""

from __future__ import annotations

import itertools
import json
import statistics
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ahd.core.io import read_json
from ahd.core.manifest import read_manifest
from ahd.diagnosis import corrupt as corrupt_module
from ahd.diagnosis.cluster import cluster, failure_key
from ahd.diagnosis.pipeline import ClusterActivity, CoherentWrongSet, EscalationRow
from ahd.diagnosis.probe import ProbeReport
from ahd.diagnosis.replay import ReplayResult, verdict_of
from ahd.diagnosis.schema import Diagnosis
from ahd.experiments import report as base
from ahd.experiments.e0 import E0Spec
from ahd.experiments.power import mde
from ahd.llm.ledger import read_ledger

FUNNEL_ARMS: tuple[str, ...] = (
    "corrupt_where_near",
    "corrupt_where_far",
    "corrupt_why",
    "corrupt_how",
    "shuffled",
    "coherent_wrong",
)


def _pct(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _rate(hits: Sequence[bool]) -> float | None:
    return sum(1 for h in hits if h) / len(hits) if hits else None


# ---------------------------------------------------------------- A. seed noise


def seed_noise(
    spec: E0Spec, runs_root: Path, data_dir: Path
) -> tuple[list[Path], list[str], dict[str, float]]:
    """sigma_seed per source (sample sd of the per-pass held-out pass rates, in points), the
    |delta| distribution over all pairs of passes and its 90th / 95th percentiles."""
    rows: list[list[object]] = []
    pass_rows: list[list[object]] = []
    sigma: dict[str, float] = {}
    for source in spec.sources:
        dirs = base.seed_runs(runs_root, "b2", source)
        rates: list[float] = []
        for d in dirs:
            agg = base.aggregate_run(d)
            rate = 100.0 * agg.pass_hat_k_tasks / agg.tasks if agg.tasks else None
            stage = str((read_manifest(d / "manifest.json").experiment or {}).get("stage", ""))
            pass_rows.append([source, d.name, stage, agg.tasks, agg.rollouts, base._num(rate, 2)])
            if rate is not None:
                rates.append(rate)
        if len(rates) < 2:
            rows.append([source, len(rates), "", "", "", "", "", ""])
            continue
        deltas = [abs(a - b) for a, b in itertools.combinations(rates, 2)]
        sd = statistics.stdev(rates)
        sigma[source] = sd
        rows.append(
            [
                source,
                len(rates),
                base._num(statistics.fmean(rates), 2),
                base._num(sd, 2),
                len(deltas),
                base._num(statistics.fmean(deltas), 2),
                base._num(_pct(deltas, 0.90), 2),
                base._num(_pct(deltas, 0.95), 2),
            ]
        )
    written = [
        base.write_csv(
            data_dir / "seed_noise.csv",
            [
                "source",
                "passes",
                "mean_pass_rate_points",
                "sigma_seed_points",
                "pairs",
                "abs_delta_mean",
                "abs_delta_p90",
                "abs_delta_p95",
            ],
            rows,
        ),
        base.write_csv(
            data_dir / "seed_noise_passes.csv",
            ["source", "run_id", "stage", "tasks", "rollouts", "pass_rate_points"],
            pass_rows,
        ),
    ]
    md = [
        "### E0d-A: seed noise band (held-out passes of the seed harness)\n\n"
        + base._md_table(
            ["source", "passes", "mean", "sigma_seed", "pairs", "|d| mean", "p90", "p95"], rows
        )
    ]
    return written, md, sigma


# ---------------------------------------------------------------- B. replay escalation


def _load(path: Path, model: Any) -> list[Any]:
    if not path.is_file():
        return []
    raw = read_json(path)
    return [model.model_validate(x) for x in raw] if isinstance(raw, list) else []


def replay_escalation(
    spec: E0Spec, runs_root: Path, data_dir: Path
) -> tuple[list[Path], list[str], dict[str, dict[str, int]]]:
    rows: list[list[object]] = []
    type_rows: list[list[object]] = []
    after_counts: dict[str, dict[str, int]] = {}
    for source in spec.sources:
        before_types: Counter[str] = Counter()
        after_types: Counter[str] = Counter()
        confirmed = 0
        unconfirmed = 0
        for d in base.seed_runs(runs_root, "b1", source):
            out = d / "diagnosis"
            before = _load(out / "replays.pre_m32.json", ReplayResult)
            after = _load(out / "replays.json", ReplayResult)
            if not after:
                continue
            for r in before or after:
                before_types[r.failure_type] += 1
            for r in after:
                after_types[r.failure_type] += 1
                for c in r.candidates:
                    if c.status in ("skipped", "unreplayable"):
                        continue
                    if c.confirmed:
                        confirmed += 1
                    else:
                        unconfirmed += 1
            log = _load(out / "replay_escalation.json", EscalationRow)
            transitions: Counter[str] = Counter()
            for row in log:
                for c in row.candidates:
                    transitions[f"{c.before}->{c.after}"] += 1
            rows.append(
                [
                    source,
                    d.name,
                    len(log),
                    sum(len(r.candidates) for r in log),
                    sum(1 for r in log if r.failure_type_before != r.failure_type_after),
                    sum(1 for r in log if r.oracle_step_before != r.oracle_step_after),
                    json.dumps(dict(sorted(transitions.items()))),
                    base._num(sum(r.usd for r in log)),
                ]
            )
        if not after_types:
            continue
        after_counts[source] = dict(after_types)
        for t in ("deterministic", "stochastic", "unrepairable", "unreplayable", "unresolved"):
            type_rows.append([source, t, before_types.get(t, 0), after_types.get(t, 0)])
        type_rows.append([source, "candidates_confirmed_n>=5", "", confirmed])
        type_rows.append([source, "candidates_unconfirmed_fixed_k", "", unconfirmed])
    written = [
        base.write_csv(
            data_dir / "replay_escalation.csv",
            [
                "source",
                "run_id",
                "failures_reopened",
                "candidates_reopened",
                "failure_type_changed",
                "oracle_step_changed",
                "verdict_transitions",
                "usd",
            ],
            rows,
        ),
        base.write_csv(
            data_dir / "failure_types_m32.csv",
            ["source", "failure_type", "before_m32", "after_m32"],
            type_rows,
        ),
    ]
    md = [
        "### E0d-B: replay escalation (marginal fixed-k verdicts re-opened)\n\n"
        + base._md_table(
            [
                "source",
                "run",
                "failures",
                "candidates",
                "type changed",
                "oracle changed",
                "transitions",
                "usd",
            ],
            rows,
        ),
        "### E0d-B: failure types before and after M3.2\n\n"
        + base._md_table(["source", "failure_type", "before", "after"], type_rows),
    ]
    return written, md, after_counts


# ---------------------------------------------------------------- C. COH-WRONG parity


def coherent_tables(
    spec: E0Spec, runs_root: Path, data_dir: Path
) -> tuple[list[Path], list[str], dict[str, bool | None]]:
    seed = spec.corruption_seed()
    cluster_rows: list[list[object]] = []
    parity_rows: list[list[object]] = []
    parity_ok: dict[str, bool | None] = {}
    for source in spec.sources:
        summary_path = runs_root / f"e0d_parity_{source}.json"
        for d in base.seed_runs(runs_root, "b1", source):
            path = d / "diagnosis" / f"coherent_wrong-s{seed}.json"
            if not path.is_file():
                continue
            record = CoherentWrongSet.model_validate(read_json(path))
            final = next(
                (r for r in record.rounds if r.generation_seed == record.final_generation_seed),
                record.rounds[-1] if record.rounds else None,
            )
            if final is None:
                continue
            scores = {s.cluster_id: s for s in final.scores}
            for g in final.generated:
                s = scores.get(g.cluster_id)
                cluster_rows.append(
                    [
                        source,
                        d.name,
                        g.cluster_id,
                        g.component,
                        g.step,
                        g.step_basis,
                        g.why.cause_label,
                        g.severity,
                        final.generation_seed,
                        "" if s is None or s.reference_score is None else s.reference_score,
                        "" if s is None or s.coherent_score is None else s.coherent_score,
                        "" if s is None else (s.error or ""),
                    ]
                )
        if summary_path.is_file():
            summary = read_json(summary_path)
            assert isinstance(summary, dict)
            rounds = summary.get("rounds") or []
            last = rounds[-1]["parity"] if rounds else {}
            parity_ok[source] = bool(summary.get("parity_ok"))
            parity_rows.append(
                [
                    source,
                    len(rounds),
                    summary.get("final_generation_seed"),
                    last.get("n"),
                    base._num(last.get("mean_difference")),
                    last.get("reference_higher"),
                    last.get("coherent_higher"),
                    last.get("ties"),
                    base._num(last.get("sign_test_p"), 3),
                    "ok" if summary.get("parity_ok") else "PERSISTENT VIOLATION",
                ]
            )
        elif cluster_rows and any(r[0] == source for r in cluster_rows):
            parity_ok[source] = None
    written = [
        base.write_csv(
            data_dir / "coherent_wrong.csv",
            [
                "source",
                "run_id",
                "cluster_id",
                "decoy_component",
                "decoy_step",
                "step_basis",
                "cause_label",
                "severity",
                "generation_seed",
                "ref_plausibility",
                "coh_plausibility",
                "error",
            ],
            cluster_rows,
        ),
        base.write_csv(
            data_dir / "parity.csv",
            [
                "source",
                "rounds",
                "final_generation_seed",
                "n",
                "mean_diff_ref_minus_coh",
                "ref_higher",
                "coh_higher",
                "ties",
                "sign_test_p",
                "status",
            ],
            parity_rows,
        ),
    ]
    md = [
        "### E0d-C: COH-WRONG plausibility parity (per source, pooled over passes)\n\n"
        + base._md_table(
            [
                "source",
                "rounds",
                "final seed",
                "n",
                "mean diff",
                "ref>",
                "coh>",
                "ties",
                "p",
                "status",
            ],
            parity_rows,
        ),
        "### E0d-C: COH-WRONG assignments and scores\n\n"
        + base._md_table(
            [
                "source",
                "run",
                "cluster",
                "decoy",
                "step",
                "basis",
                "cause",
                "sev",
                "gen",
                "ref",
                "coh",
                "error",
            ],
            cluster_rows,
        ),
    ]
    return written, md, parity_ok


# ---------------------------------------------------------------- D. PI probe


def probe_tables(spec: E0Spec, runs_root: Path, data_dir: Path) -> tuple[list[Path], list[str]]:
    seed = spec.corruption_seed()
    rows: list[list[object]] = []
    gap_rows: list[list[object]] = []
    for source in spec.sources:
        by_arm: dict[str, list[Any]] = {}
        chance: tuple[float, float] | None = None
        per_cluster: dict[str, dict[str, Any]] = {}
        for d in base.seed_runs(runs_root, "b1", source):
            path = d / "diagnosis" / f"probe-s{seed}.json"
            if not path.is_file():
                continue
            report = ProbeReport.model_validate(read_json(path))
            chance = (report.chance_top1, report.chance_top3)
            for r in report.records:
                if r.error is not None:
                    continue
                by_arm.setdefault(r.arm, []).append(r)
                per_cluster.setdefault(f"{d.name}:{r.cluster_id}", {})[r.arm] = r
        for arm, records in sorted(by_arm.items()):
            precision = [r.tool_precision for r in records if r.tool_precision is not None]
            recall = [r.tool_recall for r in records if r.tool_recall is not None]
            cats = [r.category_hit for r in records if r.category_hit is not None]
            rows.append(
                [
                    source,
                    arm,
                    len(records),
                    base._num(_rate([r.hit_top1 for r in records])),
                    base._num(_rate([r.hit_top3 for r in records])),
                    base._num(_rate([r.origin_hit_top1 for r in records])),
                    base._num(statistics.fmean(precision) if precision else None),
                    base._num(statistics.fmean(recall) if recall else None),
                    base._num(_rate(cats) if cats else None),
                    base._num(chance[0] if chance else None),
                    base._num(chance[1] if chance else None),
                ]
            )
        for key, arms in sorted(per_cluster.items()):
            ref, self_ = arms.get("reference"), arms.get("system")
            if ref is None or self_ is None:
                continue
            gap_rows.append(
                [
                    source,
                    key.split(":")[0],
                    key.split(":")[1],
                    int(ref.hit_top1),
                    int(self_.hit_top1),
                    int(ref.hit_top1) - int(self_.hit_top1),
                    int(ref.hit_top3),
                    int(self_.hit_top3),
                    int(ref.hit_top3) - int(self_.hit_top3),
                ]
            )
    written = [
        base.write_csv(
            data_dir / "pi_probe.csv",
            [
                "source",
                "arm",
                "n",
                "task_top1",
                "task_top3",
                "origin_top1",
                "tool_precision",
                "tool_recall",
                "category_hit",
                "chance_top1",
                "chance_top3",
            ],
            rows,
        ),
        base.write_csv(
            data_dir / "pi_probe_clusters.csv",
            [
                "source",
                "run_id",
                "cluster_id",
                "ref_top1",
                "self_top1",
                "gap_top1",
                "ref_top3",
                "self_top3",
                "gap_top3",
            ],
            gap_rows,
        ),
    ]
    md = [
        "### E0d-D: privileged-information probe (recovery from the rendered diagnosis alone)\n\n"
        + base._md_table(
            [
                "source",
                "arm",
                "n",
                "top1",
                "top3",
                "origin top1",
                "tool P",
                "tool R",
                "category",
                "chance1",
                "chance3",
            ],
            rows,
        )
    ]
    return written, md


# ---------------------------------------------------------------- E. component ambiguity


def component_ambiguity(
    spec: E0Spec, runs_root: Path, data_dir: Path
) -> tuple[list[Path], list[str]]:
    rows: list[list[object]] = []
    cluster_rows: list[list[object]] = []
    for source in spec.sources:
        diags: list[Diagnosis] = []
        for d in base.seed_runs(runs_root, "b1", source):
            ds = base.diagnoses(d)
            if ds is not None:
                diags.extend(ds.reference)
        if not diags:
            continue
        sizes = Counter(len(x.where.candidates) for x in diags)
        rule = sum(1 for x in diags if x.where.attribution == "rule")
        merged = cluster(diags)
        unique = 0
        for c in merged.clusters:
            members = [x for x in diags if failure_key(x) in c.members]
            is_unique = all(len(x.where.candidates) == 1 for x in members)
            unique += int(is_unique)
            cluster_rows.append(
                [
                    source,
                    c.id,
                    c.cause_label,
                    c.component,
                    len(c.members),
                    int(is_unique),
                    max(len(x.where.candidates) for x in members),
                    sum(1 for x in members if x.where.attribution == "llm"),
                ]
            )
        rows.append(
            [
                source,
                len(diags),
                base._num(rule / len(diags)),
                base._num(1 - rule / len(diags)),
                json.dumps(dict(sorted(sizes.items()))),
                len(merged.clusters),
                unique,
            ]
        )
    written = [
        base.write_csv(
            data_dir / "component_ambiguity.csv",
            [
                "source",
                "failures",
                "rule_determined_fraction",
                "llm_chosen_fraction",
                "candidate_set_sizes",
                "clusters",
                "clusters_component_unique",
            ],
            rows,
        ),
        base.write_csv(
            data_dir / "component_ambiguity_clusters.csv",
            [
                "source",
                "cluster_id",
                "cause_label",
                "component",
                "members",
                "component_unique",
                "max_candidate_set",
                "llm_chosen_members",
            ],
            cluster_rows,
        ),
    ]
    md = [
        "### E0d-E: component ambiguity (attribution records of the reference arm)\n\n"
        + base._md_table(
            [
                "source",
                "failures",
                "rule",
                "llm",
                "candidate-set sizes",
                "clusters",
                "component_unique",
            ],
            rows,
        )
    ]
    return written, md


# ---------------------------------------------------------------- M3.2 decoy exclusion audit


def decoy_exclusion(
    spec: E0Spec, runs_root: Path, data_dir: Path, *, manifest: Any
) -> tuple[list[Path], list[str]]:
    seed = spec.corruption_seed()
    rows: list[list[object]] = []
    summary: list[list[object]] = []
    for source in spec.sources:
        lost_near = lost_far = total = 0
        for d in base.seed_runs(runs_root, "b1", source):
            cs = base.clusters(d)
            activity_path = d / "diagnosis" / "activity.json"
            if cs is None or not activity_path.is_file():
                continue
            activity = ClusterActivity.model_validate(read_json(activity_path))
            activity_sets = {
                cid: {comp: set(steps) for comp, steps in comps.items()}
                for cid, comps in activity.activity.items()
            }
            sufficient = {cid: set(steps) for cid, steps in activity.sufficient.items()}
            negative = {cid: set(steps) for cid, steps in activity.negative.items()}
            for arm in ("corrupt_where_near", "corrupt_where_far"):
                old = corrupt_module.assign(
                    cs.clusters,
                    arm=arm,
                    seed=seed,
                    manifest=manifest,
                    activity=activity_sets,
                    sufficient=sufficient,
                    negative=negative,
                    exclude_candidate_set=False,
                )
                new = corrupt_module.assign(
                    cs.clusters,
                    arm=arm,
                    seed=seed,
                    manifest=manifest,
                    activity=activity_sets,
                    sufficient=sufficient,
                    negative=negative,
                    exclude_candidate_set=True,
                )
                for a_old, a_new, c in zip(
                    old.assignments, new.assignments, cs.clusters, strict=True
                ):
                    if arm == "corrupt_where_near":
                        total += 1
                    lost = a_old.impossible is None and a_new.impossible is not None
                    if lost:
                        if arm == "corrupt_where_near":
                            lost_near += 1
                        else:
                            lost_far += 1
                    decoy_in_set = (
                        a_old.where is not None
                        and a_old.where.component in c.diagnosis_reference.where.candidates
                    )
                    rows.append(
                        [
                            source,
                            d.name,
                            c.id,
                            arm.replace("corrupt_where_", ""),
                            len(c.diagnosis_reference.where.candidates),
                            "" if a_old.where is None else a_old.where.component,
                            int(decoy_in_set),
                            "" if a_new.where is None else a_new.where.component,
                            "feasible"
                            if a_new.impossible is None
                            else f"impossible: {a_new.impossible}",
                            int(lost),
                        ]
                    )
        if total:
            summary.append([source, total, lost_near, lost_far])
    written = [
        base.write_csv(
            data_dir / "decoy_exclusion.csv",
            [
                "source",
                "run_id",
                "cluster_id",
                "tier",
                "candidate_set_size",
                "old_decoy",
                "old_decoy_in_candidate_set",
                "new_decoy",
                "new_feasibility",
                "lost",
            ],
            rows,
        ),
        base.write_csv(
            data_dir / "decoy_exclusion_summary.csv",
            ["source", "clusters", "lose_near", "lose_far"],
            summary,
        ),
    ]
    in_set = sum(1 for r in rows if r[6] == 1)
    md = [
        "### M3.2: decoy exclusion audit (old rule vs full candidate set)\n\n"
        f"Old-rule decoys that sat inside the candidate set: {in_set} of {len(rows)} "
        "(cluster, tier) draws.\n\n"
        + base._md_table(["source", "clusters", "lose near", "lose far"], summary)
    ]
    return written, md


# ---------------------------------------------------------------- F. minimum detectable effect


def _cost_inputs(spec: E0Spec, runs_root: Path) -> dict[str, tuple[float | None, float | None]]:
    """source -> (mean held-out pass cost in USD, mean diagnosis-arm call cost in USD)."""
    out: dict[str, tuple[float | None, float | None]] = {}
    for source in spec.sources:
        pass_costs: list[float] = []
        for d in base.seed_runs(runs_root, "b2", source):
            path = d / "ledger.jsonl"
            if path.is_file():
                pass_costs.append(sum(r.usd for r in read_ledger(path)))
        calls: list[float] = []
        for d in base.seed_runs(runs_root, "b1", source):
            path = d / "ledger.jsonl"
            if path.is_file():
                calls.extend(
                    r.usd for r in read_ledger(path) if r.event == "call" and r.arm == "diagnosis"
                )
        out[source] = (
            statistics.fmean(pass_costs) if pass_costs else None,
            statistics.fmean(calls) if calls else None,
        )
    return out


def mde_table(
    spec: E0Spec, runs_root: Path, data_dir: Path, sigma: dict[str, float]
) -> tuple[list[Path], list[str], dict[str, Any]]:
    block = spec.e0d_block("F_mde")
    ns = [int(x) for x in block.get("N", [7, 8, 10, 14])]
    ks = [int(x) for x in block.get("k", [3, 5])]
    passes_list = [int(x) for x in block.get("heldout_passes", [1, 2])]
    sims = int(block.get("sims", 1000))
    seed = int(block.get("seed", 0))
    target = float(block.get("power", 0.8))
    arms = int(spec.thresholds.get("e2_arms_m32", 11))
    meaningful = float(spec.thresholds.get("delta_meaningful_points", 3.0))
    budget = spec.owner_budget_usd
    costs = _cost_inputs(spec, runs_root)
    rows: list[list[object]] = []
    decision: dict[str, Any] = {}
    for source, sigma_seed in sorted(sigma.items()):
        spread = max(sigma_seed, 5.0)
        pass_usd, call_usd = costs.get(source, (None, None))
        candidates: list[tuple[float, float | None, int, int, int]] = []
        for n in ns:
            for k in ks:
                for passes in passes_list:
                    value = mde(
                        n_clusters=n,
                        k=k,
                        passes=passes,
                        sigma_seed=sigma_seed,
                        spread=spread,
                        target=target,
                        sims=sims,
                        seed=seed,
                    )
                    cost = (
                        arms * n * k * ((call_usd or 0.0) + passes * (pass_usd or 0.0))
                        if pass_usd is not None
                        else None
                    )
                    rows.append(
                        [
                            source,
                            n,
                            k,
                            passes,
                            base._num(sigma_seed, 2),
                            base._num(spread, 2),
                            base._num(value, 2),
                            base._num(cost, 2),
                            int(value is not None and value <= meaningful),
                        ]
                    )
                    if value is not None:
                        candidates.append((value, cost, n, k, passes))
        within = [c for c in candidates if budget is None or c[1] is None or c[1] <= budget]
        feasible = [c for c in within if c[0] <= meaningful]
        if feasible:
            # D8: the cheapest configuration with MDE <= delta_meaningful within the budget
            best = min(
                feasible, key=lambda c: (c[1] if c[1] is not None else 0.0, c[2], c[3], c[4])
            )
            decision[source] = {
                "choice": f"N={best[2]} k={best[3]} passes={best[4]}",
                "mde": best[0],
                "cost_usd": best[1],
                "meets_d8": True,
                "within_budget": True,
            }
        elif within:
            # D8 fallback: the best available (smallest MDE) configuration within the budget
            best = min(within, key=lambda c: (c[0], c[1] if c[1] is not None else 0.0))
            decision[source] = {
                "choice": f"N={best[2]} k={best[3]} passes={best[4]}",
                "mde": best[0],
                "cost_usd": best[1],
                "meets_d8": False,
                "within_budget": True,
            }
        elif candidates:
            # nothing fits the budget: the cheapest configuration, flagged
            best = min(candidates, key=lambda c: (c[1] if c[1] is not None else 0.0, c[0]))
            decision[source] = {
                "choice": f"N={best[2]} k={best[3]} passes={best[4]}",
                "mde": best[0],
                "cost_usd": best[1],
                "meets_d8": False,
                "within_budget": False,
            }
    written = [
        base.write_csv(
            data_dir / "mde.csv",
            [
                "source",
                "N",
                "k",
                "heldout_passes",
                "sigma_seed",
                "spread",
                "mde_points",
                "cost_usd",
                "meets_d8",
            ],
            rows,
        )
    ]
    md = [
        "### E0d-F: minimum detectable effect "
        "(cluster-level sign-flip test, alpha 0.05, power 0.8)\n\n"
        f"Cost model: {arms} arms x N x k x (proposal call + passes x held-out pass); "
        "inputs from the E0b ledgers.\n\n"
        + base._md_table(
            ["source", "N", "k", "passes", "sigma", "spread", "MDE", "cost", "<=3"], rows
        )
    ]
    return written, md, decision


# ---------------------------------------------------------------- G. funnel


def funnel(spec: E0Spec, runs_root: Path, data_dir: Path) -> tuple[list[Path], list[str]]:
    seed = spec.corruption_seed()
    rows: list[list[object]] = []
    for source in spec.sources:
        dirs = base.seed_runs(runs_root, "b1", source)
        if not dirs:
            continue
        rollouts = failed_rollouts = 0
        failed_tasks: set[str] = set()
        genuine = 0
        replayable = positive = negative = unresolved = unreplayable = 0
        diags: list[Diagnosis] = []
        feasible: Counter[str] = Counter()
        clusters_total = clusters_ge2 = 0
        for d in dirs:
            agg = base.aggregate_run(d)
            rollouts += agg.rollouts
            fails = [f for f in base.failures(d) if f.family != "infra"]
            failed_rollouts += len(fails)
            failed_tasks |= {f.task_id for f in fails}
            genuine += sum(
                1 for g in base.genuineness(runs_root / f"{d.name}-ref") if g.verdict == "genuine"
            )
            for r in base.replays(d):
                replayable += 1
                verdicts = {verdict_of(c) for c in r.candidates}
                if r.failure_type == "unreplayable":
                    unreplayable += 1
                elif "positive" in verdicts:
                    positive += 1
                elif "unresolved" in verdicts:
                    unresolved += 1
                else:
                    negative += 1
            ds = base.diagnoses(d)
            if ds is not None:
                diags.extend(ds.reference)
            for arm, table in base.assignments(d, seed).items():
                if arm in FUNNEL_ARMS:
                    feasible[arm] += sum(1 for a in table.assignments if a.impossible is None)
        if diags:
            merged = cluster(diags)
            clusters_total = len(merged.clusters)
            clusters_ge2 = sum(1 for c in merged.clusters if len(c.members) >= 2)
        rows.append(
            [
                source,
                rollouts,
                failed_rollouts,
                len(failed_tasks),
                genuine,
                replayable,
                positive,
                negative,
                unresolved,
                unreplayable,
                clusters_total,
                clusters_ge2,
                *[feasible.get(arm, 0) for arm in FUNNEL_ARMS],
            ]
        )
    header = [
        "source",
        "rollouts",
        "failed_rollouts",
        "failed_tasks",
        "genuine_references",
        "replayable",
        "validated_positive",
        "validated_negative",
        "unresolved",
        "unreplayable",
        "clusters",
        "clusters_ge2",
        *[f"feasible_{arm.replace('corrupt_', '')}" for arm in FUNNEL_ARMS],
    ]
    written = [base.write_csv(data_dir / "funnel.csv", header, rows)]
    md = [
        "### E0d-G: measurability funnel (per source; feasibility counts per-run clusters)\n\n"
        + base._md_table(header, rows)
    ]
    return written, md


# ---------------------------------------------------------------- entry


def e0d_tables(
    spec: E0Spec, runs_root: Path, data_dir: Path, *, manifest: Any
) -> tuple[list[Path], list[str], dict[str, Any]]:
    written: list[Path] = []
    md: list[str] = []
    extras: dict[str, Any] = {}
    status = spec.E0c.get("status", "unknown")
    md.append(
        f"E0c (reasoning_effort low) status: **{status}** ({spec.E0c.get('note', '')}). "
        f"E0d spend cap {spec.e0d_cap()} USD; stage order {list(spec.e0d_order())}."
    )
    w, m, sigma = seed_noise(spec, runs_root, data_dir)
    written += w
    md += m
    extras["sigma_seed"] = sigma
    w, m, extras["failure_types_after"] = replay_escalation(spec, runs_root, data_dir)
    written += w
    md += m
    w, m, extras["parity_ok"] = coherent_tables(spec, runs_root, data_dir)
    written += w
    md += m
    w, m = probe_tables(spec, runs_root, data_dir)
    written += w
    md += m
    w, m = component_ambiguity(spec, runs_root, data_dir)
    written += w
    md += m
    w, m = decoy_exclusion(spec, runs_root, data_dir, manifest=manifest)
    written += w
    md += m
    w, m, extras["mde_decision"] = mde_table(spec, runs_root, data_dir, sigma)
    written += w
    md += m
    w, m = funnel(spec, runs_root, data_dir)
    written += w
    md += m
    return written, md, extras
