#!/usr/bin/env python
"""Run E0 stages (experiments/E0/spec.yaml). Resumable: rerun the same command after a crash.

    uv run python scripts/e0_run.py E0a            # pilot: 20 tasks + diagnosis of failures
    uv run python scripts/e0_run.py E0b            # after owner approval: B1..B7
    uv run python scripts/e0_run.py E0b --stages B1 B2

No reference source: written fresh for ahd.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ahd.errors import AhdError
from ahd.experiments.e0 import E0Context, e0b, pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e0_run")
    parser.add_argument("stage", choices=["E0a", "E0b"])
    parser.add_argument("--spec", type=Path, default=Path("experiments/E0/spec.yaml"))
    parser.add_argument("--stages", nargs="*", default=None, help="E0b sub-stages: B1 B2 B3-6 B7")
    args = parser.parse_args(argv)
    try:
        ctx = E0Context(args.spec)
        if args.stage == "E0a":
            dirs = pilot(ctx)
            for source, run_dir in dirs.items():
                print(f"{source}: {run_dir}")
        else:
            e0b(ctx, stages=tuple(args.stages) if args.stages else ("B1", "B2", "B3-6", "B7"))
        print("done; regenerate tables with: uv run python scripts/e0_report.py")
    except AhdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
