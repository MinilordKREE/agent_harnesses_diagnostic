"""Command line: ``ahd version``, ``ahd llm ping``, ``ahd runs new``.

No reference source: written fresh for ahd (see docs/reuse/M0.md). argparse, per the survey
(7 of 8 references); subcommands are the only place in the package that prints.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ahd import __version__
from ahd.core.config import RunConfig, load_run_config
from ahd.core.context import RunContext, create_run_context, git_state, new_run_id
from ahd.core.environment import probe_environment
from ahd.core.hashing import JsonValue
from ahd.core.manifest import write_manifest
from ahd.core.trace import TRACE_FILENAME, TraceWriter
from ahd.errors import ConfigError, InfraError, TaskFailure
from ahd.harness.components import ComponentManifest
from ahd.harness.snapshot import SnapshotStore, diff_snapshots, snapshot_from_dir
from ahd.harness.validate import validate_tree
from ahd.llm.cache import ResponseCache
from ahd.llm.deepseek import PROVIDER_NAME, DeepSeekClient, Transport, make_openai_transport
from ahd.llm.ledger import LEDGER_FILENAME, Ledger, read_ledger
from ahd.llm.pricing import load_pricing
from ahd.llm.types import Attribution, ChatMessage, ChatRequest
from ahd.logs import configure_logging
from ahd.runner.runner import Runner
from ahd.runner.spec import RunSpec
from ahd.settings import Settings, load_settings
from ahd.tasks.evobench import EvoBenchLoader, cached_snapshot_dir, fetch_snapshot
from ahd.tasks.judge import AhdJudgeClient
from ahd.tasks.kinds import EVOBENCH_DATASET_ID, EVOBENCH_PINNED_REVISION, Split
from ahd.tasks.models import TaskSet
from ahd.tasks.sampling import stratified_sample
from ahd.tasks.scorer import Scorer

LOG_FILENAME = "log.jsonl"
DEFAULT_MANIFEST = Path("configs/harness/seed_components.yaml")
HARNESS_STORE_DIRNAME = "harness_store"
CLAW_REPO_DEFAULT = Path("external/claw-eval")
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INFRA = 3
EXIT_TASK = 4


def make_transport(settings: Settings, config: RunConfig) -> Transport:
    """Build the real network transport. Tests monkeypatch this symbol."""
    if config.llm.provider != PROVIDER_NAME:
        raise ConfigError(f"provider {config.llm.provider!r} has no CLI transport; use 'deepseek'")
    return make_openai_transport(
        api_key=settings.deepseek_api_key,
        base_url=config.llm.base_url,
        timeout_s=config.llm.timeout_s,
    )


def _start_run(
    config: RunConfig,
    config_path: Path,
    *,
    runs_root: Path | None,
    run_id: str | None,
    harness_snapshot_id: str | None = None,
    run_spec: dict[str, JsonValue] | None = None,
) -> RunContext:
    ctx = create_run_context(config, runs_root=runs_root or config.runs_root, run_id=run_id)
    tasks_cfg = config.tasks
    dataset_id = tasks_cfg.dataset_id if tasks_cfg else EVOBENCH_DATASET_ID
    revision = tasks_cfg.revision if tasks_cfg else EVOBENCH_PINNED_REVISION
    snapshot = cached_snapshot_dir(dataset_id, revision)
    environment = probe_environment(
        repo_dir=Path.cwd(),
        evobench_dataset_id=dataset_id,
        evobench_snapshot_sha=revision if snapshot is not None else None,
    )
    write_manifest(
        ctx,
        config,
        config_path=config_path,
        environment=environment,
        harness_snapshot_id=harness_snapshot_id,
        run_spec=run_spec,
    )
    configure_logging(json_path=ctx.out_dir / LOG_FILENAME)
    logging.getLogger(__name__).info(
        "run created",
        extra={"run_id": ctx.run_id, "git_sha": ctx.git_sha, "config_sha256": ctx.config_sha256},
    )
    return ctx


def cmd_version(args: argparse.Namespace) -> int:
    print(f"ahd {__version__}")
    try:
        state = git_state(Path.cwd())
    except InfraError as exc:
        print(f"git: unavailable ({exc})")
    else:
        print(f"git {state.sha}{' (dirty)' if state.dirty else ''}")
    return EXIT_OK


def cmd_runs_new(args: argparse.Namespace) -> int:
    config = load_run_config(args.config)
    ctx = _start_run(config, args.config, runs_root=args.runs_root, run_id=args.run_id)
    with TraceWriter(ctx.out_dir / TRACE_FILENAME, ctx.run_id) as trace:
        trace.write(
            "run_created",
            {
                "config_path": str(args.config),
                "config_sha256": ctx.config_sha256,
                "git_sha": ctx.git_sha,
                "git_dirty": ctx.git_dirty,
            },
        )
    print(ctx.out_dir)
    return EXIT_OK


def cmd_llm_ping(args: argparse.Namespace) -> int:
    config = load_run_config(args.config)
    settings = load_settings()
    pricing = load_pricing(config.pricing_path)
    ctx = _start_run(
        config,
        args.config,
        runs_root=args.runs_root,
        run_id=args.run_id or new_run_id(prefix="ping-"),
    )
    ledger = Ledger(ctx.out_dir / LEDGER_FILENAME, ctx.run_id)
    cache = ResponseCache(config.llm.cache_dir, provider=PROVIDER_NAME)
    client = DeepSeekClient(
        transport=make_transport(settings, config),
        ledger=ledger,
        pricing=pricing,
        retry=config.llm.retry,
        cache=cache,
    )
    request = ChatRequest(
        model=config.llm.model,
        messages=(ChatMessage(role="user", content=args.prompt),),
        temperature=config.llm.temperature,
        seed=config.seed,
        max_tokens=config.llm.max_tokens,
        thinking=config.llm.thinking,
        reasoning_effort=config.llm.reasoning_effort,
        timeout_s=config.llm.timeout_s,
        use_cache=args.cache,
        attribution=Attribution(arm="ping", unit_id="ping"),
    )
    with TraceWriter(ctx.out_dir / TRACE_FILENAME, ctx.run_id) as trace:
        trace.write("llm_ping_request", {"request_sha256": client.request_sha256(request)})
        response = client.complete(request)
        trace.write(
            "llm_ping_response",
            {"finish_reason": response.finish_reason, "cached": response.cached},
        )
    last = read_ledger(ledger.path)[-1]
    print(f"model:              {response.model}")
    print(f"content:            {response.content[:200]!r}")
    print(f"finish_reason:      {response.finish_reason}")
    print(f"prompt_tokens:      {response.usage.prompt_tokens}")
    print(f"  cache_hit:        {response.usage.cache_hit_prompt_tokens}")
    print(f"completion_tokens:  {response.usage.completion_tokens}")
    print(f"  reasoning:        {response.usage.reasoning_tokens}")
    print(f"latency_ms:         {response.latency_ms}")
    print(f"cached:             {response.cached}")
    print(f"usd:                {last.usd}")
    print(f"pricing_tier:       {last.pricing_tier}")
    print(f"pricing_version:    {last.pricing_version}")
    print(f"ledger:             {ledger.path}")
    return EXIT_OK


def _loader(args: argparse.Namespace) -> EvoBenchLoader:
    return EvoBenchLoader(
        dataset_id=args.dataset, revision=args.revision, snapshot_dir=args.snapshot_dir
    )


def _load_split(args: argparse.Namespace) -> TaskSet:
    split: Split = args.split
    taskset = _loader(args).load(split)
    return taskset.select(
        domains=[args.domain] if args.domain else None,
        sources=[args.source] if args.source else None,
        include_excluded=args.include_excluded,
    )


def cmd_tasks_fetch(args: argparse.Namespace) -> int:
    settings = load_settings()
    path = fetch_snapshot(args.dataset, args.revision, token=settings.hf_token)
    print(path)
    return EXIT_OK


def cmd_tasks_list(args: argparse.Namespace) -> int:
    taskset = _load_split(args)
    print(f"{'id':<48} {'source':<11} {'domain':<8} {'scorer':<18} excluded")
    for task in taskset.tasks:
        flag = "yes" if task.excluded else "no"
        print(
            f"{task.id:<48} {task.source_benchmark:<11} {task.domain:<8} "
            f"{task.evaluator.type:<18} {flag}"
        )
    print(f"-- {len(taskset)} tasks; by source {taskset.counts_by_source()}")
    return EXIT_OK


def cmd_tasks_show(args: argparse.Namespace) -> int:
    split: Split = args.split
    task = _loader(args).load(split).by_id(args.task_id)
    print(f"id:               {task.id}")
    print(f"split:            {task.split}")
    print(f"domain:           {task.domain}")
    print(f"source_benchmark: {task.source_benchmark}")
    print(
        f"scorer:           {task.evaluator.type} (judge_required={task.evaluator.judge_required})"
    )
    excluded = f"True - {task.exclusion_reason}" if task.excluded else "False"
    print(f"excluded:         {excluded}")
    print(f"asset_files:      {task.resources.asset_files or '{}'}")
    print(f"public_files:     {list(task.resources.public_files)}")
    print(f"metadata:         {json.dumps(task.metadata, ensure_ascii=False)}")
    print("prompt:")
    print(task.prompt)
    if args.show_gold:
        print("scorer spec (gold data, never shown to a policy):")
        print(json.dumps(task.evaluator.spec, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_tasks_sample(args: argparse.Namespace) -> int:
    taskset = _load_split(args)
    sample = stratified_sample(taskset, n=args.n, seed=args.seed)
    for task in sample.tasks:
        print(f"{task.source_benchmark:<11} {task.id}")
    print(
        f"-- {len(sample)} of {len(taskset)} tasks, seed {args.seed}; "
        f"by source {sample.counts_by_source()}"
    )
    return EXIT_OK


def _store(args: argparse.Namespace) -> SnapshotStore:
    root = (
        args.store
        if args.store is not None
        else load_run_config(args.config).runs_root / HARNESS_STORE_DIRNAME
    )
    return SnapshotStore(Path(root))


def cmd_harness_snapshot(args: argparse.Namespace) -> int:
    manifest = ComponentManifest.load(args.manifest)
    store = _store(args)
    snapshot = snapshot_from_dir(
        Path(args.source), store=store, manifest=manifest, provenance=args.provenance
    )
    report = validate_tree(snapshot.tree)
    unresolved = snapshot.resolved_manifest().unresolved()
    print(f"snapshot_id:        {snapshot.snapshot_id}")
    print(f"sha256:             {snapshot.meta.sha256}")
    print(f"evobench_revision:  {snapshot.meta.evobench_revision}")
    print(f"files:              {snapshot.meta.file_count}")
    print(f"store:              {store.root}")
    print(f"valid:              {report.ok}")
    for problem in report.problems:
        print(f"  problem: {problem}")
    for span in unresolved:
        print(f"  unresolved symbol: {span.component_id}: {span.symbol} ({span.detail})")
    return EXIT_OK if report.ok else EXIT_USAGE


def cmd_harness_components(args: argparse.Namespace) -> int:
    snapshot = _store(args).load(args.snapshot_id)
    resolved = snapshot.resolved_manifest()
    print(f"{'component':<20} {'layer':<14} {'file':<24} {'lines':<11} {'kind':<11} symbol")
    for span in resolved.spans:
        lines = f"{span.start_line}-{span.end_line}" if span.resolved else "-"
        component = resolved.manifest.by_id(span.component_id)
        print(
            f"{span.component_id:<20} {component.layer:<14} {span.file:<24} {lines:<11} "
            f"{span.kind:<11} {span.symbol}"
        )
    unresolved = resolved.unresolved()
    print(
        f"-- {len(resolved.spans)} symbols, {len(unresolved)} unresolved, "
        f"tree {resolved.tree_sha256[:12]}"
    )
    return EXIT_OK if not unresolved else EXIT_USAGE


def cmd_harness_diff(args: argparse.Namespace) -> int:
    store = _store(args)
    old, new = store.load(args.old), store.load(args.new)
    diff = diff_snapshots(old, new)
    print(diff, end="")
    for mapping in old.resolved_manifest().diff_to_components(diff):
        ids = ",".join(mapping.component_ids) or "(unmapped)"
        exact = "" if mapping.exact else " (approximate)"
        print(
            f"# {mapping.file} @@ -{mapping.old_start},{mapping.old_lines} "
            f"+{mapping.new_start},{mapping.new_lines} -> {ids}{exact}"
        )
    return EXIT_OK


def _resolve_task_ids(config: RunConfig, spec: str) -> tuple[str, ...]:
    tasks_cfg = config.tasks
    if tasks_cfg is None:
        raise ConfigError("run config has no `tasks` section")
    loader = EvoBenchLoader(dataset_id=tasks_cfg.dataset_id, revision=tasks_cfg.revision)
    taskset = loader.load(tasks_cfg.split).select(
        domains=tasks_cfg.domains, sources=tasks_cfg.sources, include_excluded=False
    )
    if spec == "all":
        if tasks_cfg.n is not None:
            taskset = stratified_sample(taskset, n=tasks_cfg.n, seed=config.seed)
        return taskset.ids()
    ids = tuple(item.strip() for item in spec.split(",") if item.strip())
    for task_id in ids:
        taskset.by_id(task_id)  # raises ConfigError if absent
    return ids


def cmd_run_start(args: argparse.Namespace) -> int:
    config = load_run_config(args.config)
    settings = load_settings()
    pricing = load_pricing(config.pricing_path)
    manifest = ComponentManifest.load(args.manifest)
    store = SnapshotStore(config.runs_root / HARNESS_STORE_DIRNAME)
    harness_arg = Path(args.harness)
    if harness_arg.is_dir():
        snapshot = snapshot_from_dir(harness_arg, store=store, manifest=manifest, provenance="seed")
    else:
        snapshot = store.load(args.harness)
    task_ids = _resolve_task_ids(config, args.tasks)
    spec = RunSpec.from_config(
        config,
        harness_snapshot_id=snapshot.snapshot_id,
        task_ids=task_ids,
        mode=args.mode,
        replicates=args.replicates,
        arm=args.arm,
    )
    ctx = _start_run(
        config,
        args.config,
        runs_root=args.runs_root,
        run_id=args.run_id,
        harness_snapshot_id=snapshot.snapshot_id,
        run_spec=spec.manifest_view(),
    )
    assert config.tasks is not None
    loader = EvoBenchLoader(dataset_id=config.tasks.dataset_id, revision=config.tasks.revision)
    taskset = loader.load(config.tasks.split)
    tasks = [taskset.by_id(task_id) for task_id in task_ids]
    ledger = Ledger(ctx.out_dir / LEDGER_FILENAME, ctx.run_id)
    judge = AhdJudgeClient(
        DeepSeekClient(
            transport=make_transport(settings, config),
            ledger=ledger,
            pricing=pricing,
            retry=config.llm.retry,
            cache=ResponseCache(config.llm.cache_dir, provider=PROVIDER_NAME),
        ),
        config=config.judge,
        api_base=config.llm.base_url,
        seed=config.seed,
    )
    claw_repo = CLAW_REPO_DEFAULT.resolve() if CLAW_REPO_DEFAULT.is_dir() else None
    scorer = Scorer(judge=judge, ledger=ledger, arm=spec.arm, seed=config.seed, claw_repo=claw_repo)
    with TraceWriter(ctx.out_dir / TRACE_FILENAME, ctx.run_id) as trace:
        runner = Runner(
            ctx=ctx,
            config=config,
            settings=settings,
            pricing=pricing,
            ledger=ledger,
            scorer=scorer,
            trace=trace,
            claw_repo=claw_repo,
        )
        result = runner.run(spec, tasks, snapshot=snapshot)
    print(f"run_id:    {ctx.run_id}")
    print(f"out_dir:   {ctx.out_dir}")
    print(f"snapshot:  {result.harness_snapshot_id}")
    print(f"tasks:     {len(result.tasks)}  failures: {len(result.failures)}")
    print(f"summary:   {result.summary_path}")
    return EXIT_OK


def cmd_run_summarize(args: argparse.Namespace) -> int:
    runs_root = (
        args.runs_root if args.runs_root is not None else load_run_config(args.config).runs_root
    )
    summary_path = Path(runs_root) / args.run_id / "summary.json"
    if not summary_path.is_file():
        raise InfraError(
            f"no summary.json for run {args.run_id} under {runs_root}", kind="missing_file"
        )
    print(summary_path.read_text(encoding="utf-8"), end="")
    return EXIT_OK


def _add_task_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--split", default="validation", choices=["validation", "evaluation"])
    parser.add_argument("--dataset", default=EVOBENCH_DATASET_ID)
    parser.add_argument("--revision", default=EVOBENCH_PINNED_REVISION)
    parser.add_argument("--snapshot-dir", type=Path, default=None, help="override the HF cache")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ahd", description="agent harnesses diagnostic")
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_version = sub.add_parser("version", help="print version and git state")
    p_version.set_defaults(func=cmd_version)

    p_llm = sub.add_parser("llm", help="LLM provider commands")
    llm_sub = p_llm.add_subparsers(dest="llm_command", required=True)
    p_ping = llm_sub.add_parser("ping", help="one real call; prints token counts and usd")
    p_ping.add_argument("--config", type=Path, default=Path("configs/runs/example.yaml"))
    p_ping.add_argument("--prompt", default="Reply with the single word: pong")
    p_ping.add_argument("--run-id", default=None)
    p_ping.add_argument("--runs-root", type=Path, default=None)
    p_ping.add_argument("--cache", action="store_true", help="use the response cache")
    p_ping.set_defaults(func=cmd_llm_ping)

    p_tasks = sub.add_parser("tasks", help="Evo-Bench task substrate")
    tasks_sub = p_tasks.add_subparsers(dest="tasks_command", required=True)
    p_fetch = tasks_sub.add_parser(
        "fetch", help="download the pinned dataset revision into the HF cache"
    )
    _add_task_source_args(p_fetch)
    p_fetch.set_defaults(func=cmd_tasks_fetch)
    for name, func, help_text in (
        ("list", cmd_tasks_list, "list tasks of a split"),
        ("sample", cmd_tasks_sample, "deterministic stratified subsample"),
    ):
        sp = tasks_sub.add_parser(name, help=help_text)
        _add_task_source_args(sp)
        sp.add_argument("--domain", default=None, choices=["search", "office", "general"])
        sp.add_argument(
            "--source", default=None, choices=["browsecomp", "hle", "gdpval", "claw_eval", "apex"]
        )
        sp.add_argument("--include-excluded", action="store_true")
        if name == "sample":
            sp.add_argument("--n", type=int, required=True)
            sp.add_argument("--seed", type=int, default=0)
        sp.set_defaults(func=func)
    p_show = tasks_sub.add_parser("show", help="show one task")
    _add_task_source_args(p_show)
    p_show.add_argument("task_id")
    p_show.add_argument("--show-gold", action="store_true", help="also print the scorer spec")
    p_show.set_defaults(func=cmd_tasks_show)

    p_harness = sub.add_parser("harness", help="harness snapshots and components")
    harness_sub = p_harness.add_subparsers(dest="harness_command", required=True)
    for name, func, help_text in (
        ("snapshot", cmd_harness_snapshot, "copy a harness dir into the store and hash it"),
        ("components", cmd_harness_components, "print the resolved component spans of a snapshot"),
        ("diff", cmd_harness_diff, "unified diff between two snapshots with component mapping"),
    ):
        hp = harness_sub.add_parser(name, help=help_text)
        hp.add_argument("--config", type=Path, default=Path("configs/runs/example.yaml"))
        hp.add_argument(
            "--store",
            type=Path,
            default=None,
            help="snapshot store (default <runs_root>/harness_store)",
        )
        hp.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
        if name == "snapshot":
            hp.add_argument("--from", dest="source", type=Path, required=True)
            hp.add_argument("--provenance", default="seed", choices=["seed", "manual"])
        elif name == "components":
            hp.add_argument("snapshot_id")
        else:
            hp.add_argument("old")
            hp.add_argument("new")
        hp.set_defaults(func=func)

    p_run = sub.add_parser("run", help="execute rollouts (or summarize a run)")
    run_sub = p_run.add_subparsers(dest="run_command", required=True)
    p_start = run_sub.add_parser("start", help="run tasks through a harness snapshot")
    p_start.add_argument("--config", type=Path, default=Path("configs/runs/example.yaml"))
    p_start.add_argument(
        "--harness", required=True, help="snapshot id in the store, or a harness directory"
    )
    p_start.add_argument(
        "--tasks", default="all", help="'all' (config tasks section) or comma-separated task ids"
    )
    p_start.add_argument("--replicates", type=int, default=None)
    p_start.add_argument("--mode", default=None, choices=["normal", "reference"])
    p_start.add_argument("--arm", default=None)
    p_start.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p_start.add_argument("--run-id", default=None)
    p_start.add_argument("--runs-root", type=Path, default=None)
    p_start.set_defaults(func=cmd_run_start)
    p_sum = run_sub.add_parser("summarize", help="print summary.json of a run")
    p_sum.add_argument("run_id")
    p_sum.add_argument("--config", type=Path, default=Path("configs/runs/example.yaml"))
    p_sum.add_argument("--runs-root", type=Path, default=None)
    p_sum.set_defaults(func=cmd_run_summarize)

    p_runs = sub.add_parser("runs", help="run directory commands")
    runs_sub = p_runs.add_subparsers(dest="runs_command", required=True)
    p_new = runs_sub.add_parser("new", help="create a run dir with manifest and resolved config")
    p_new.add_argument("--config", type=Path, required=True)
    p_new.add_argument("--run-id", default=None)
    p_new.add_argument("--runs-root", type=Path, default=None)
    p_new.set_defaults(func=cmd_runs_new)
    return parser


def _normalize_argv(argv: Sequence[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    items = list(argv)
    if items and items[0] == "run" and (len(items) == 1 or items[1].startswith("-")):
        items.insert(1, "start")
    return items


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(_normalize_argv(argv))
    configure_logging(level=getattr(logging, args.log_level))
    try:
        result: int = args.func(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except InfraError as exc:
        print(f"infra error: {exc}", file=sys.stderr)
        return EXIT_INFRA
    except TaskFailure as exc:
        print(f"task failure [{exc.kind}]: {exc}", file=sys.stderr)
        return EXIT_TASK
    return result


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
