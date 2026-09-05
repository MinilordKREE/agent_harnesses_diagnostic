#!/usr/bin/env python
"""Regenerate data/E0/*.csv and docs/experiments/E0_REPORT.md from runs/E0 (deterministic).

    uv run python scripts/e0_report.py [--spec experiments/E0/spec.yaml]

No reference source: written fresh for ahd.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ahd.errors import AhdError
from ahd.experiments.report import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e0_report")
    parser.add_argument("--spec", type=Path, default=Path("experiments/E0/spec.yaml"))
    parser.add_argument("--data", type=Path, default=Path("data/E0"))
    parser.add_argument("--report", type=Path, default=Path("docs/experiments/E0_REPORT.md"))
    args = parser.parse_args(argv)
    try:
        written = build_report(spec_path=args.spec, data_dir=args.data, report_path=args.report)
    except AhdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
