#!/usr/bin/env python3
"""Enforce separate line and branch coverage thresholds."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--line", type=float, required=True)
    parser.add_argument("--branch", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        totals = json.loads(args.report.read_text(encoding="utf-8"))["totals"]
        line = float(totals["percent_covered"])
        branch = percentage(int(totals["covered_branches"]), int(totals["num_branches"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print(
            "coverage threshold check could not read a valid coverage JSON report", file=sys.stderr
        )
        return 2
    print(f"line={line:.2f}% branch={branch:.2f}%")
    return 0 if line >= args.line and branch >= args.branch else 1


if __name__ == "__main__":
    raise SystemExit(main())
