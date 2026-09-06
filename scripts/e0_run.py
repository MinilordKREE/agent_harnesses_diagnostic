#!/usr/bin/env python
"""Run E0 stages (experiments/E0/spec.yaml). Resumable: rerun the same command after a crash.

    uv run python scripts/e0_run.py E0a            # pilot: 20 tasks + diagnosis of failures
    uv run python scripts/e0_run.py E0b --preflight   # P1 vision probe + P2 splits
    uv run python scripts/e0_run.py E0b            # B1..B7; refuses without pre-flight,
                                                   # stops at hard_cap_usd
    uv run python scripts/e0_run.py E0b --stages B1 B2

No reference source: written fresh for ahd.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ahd.errors import AhdError
from ahd.experiments.e0 import E0Context, e0b, pilot, preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e0_run")
    parser.add_argument("stage", choices=["E0a", "E0b"])
    parser.add_argument("--spec", type=Path, default=Path("experiments/E0/spec.yaml"))
    parser.add_argument("--stages", nargs="*", default=None, help="E0b sub-stages: B1 B2 B3-6 B7")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="E0b only: run P1 (vision probe, pilot checks) and P2 (freeze splits), then stop",
    )
    args = parser.parse_args(argv)
    try:
        ctx = E0Context(args.spec)
        if args.stage == "E0a":
            dirs = pilot(ctx)
            for source, run_dir in dirs.items():
                print(f"{source}: {run_dir}")
        elif args.preflight:
            result = preflight(ctx)
            print(json.dumps(result.model_dump(mode="json"), indent=2))
            if not result.ok:
                return 4
        else:
            e0b(ctx, stages=tuple(args.stages) if args.stages else ("B1", "B2", "B3-6", "B7"))
        print("done; regenerate tables with: uv run python scripts/e0_report.py")
    except AhdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
