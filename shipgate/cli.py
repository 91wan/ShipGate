"""ShipGate command-line interface and stable exit codes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .engine import run_check
from .model import Operation, ProjectType, SourceKind
from .reporting import ReportWriteError, render_markdown


class UsageError(ValueError):
    """Invalid cross-argument configuration."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail-closed public release checks.")
    parser.add_argument("--version", action="version", version=f"ShipGate {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="Check a project publication surface.")
    check.add_argument("project", help="Project directory to check.")
    check.add_argument(
        "--operation",
        choices=[item.value for item in Operation],
        default=Operation.LOCAL.value,
    )
    check.add_argument(
        "--project-type",
        choices=[item.value for item in ProjectType],
        default=ProjectType.AUTO.value,
    )
    check.add_argument("--source", choices=[item.value for item in SourceKind])
    check.add_argument("--ref")
    check.add_argument("--asset", action="append", default=[])
    check.add_argument("--source-only", action="store_true")
    check.add_argument("--report-md")
    check.add_argument("--report-json")
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    operation = Operation(args.operation)
    source = SourceKind(args.source) if args.source else None
    if args.source_only and operation is not Operation.RELEASE:
        raise UsageError("--source-only is valid only with --operation release.")
    if source is SourceKind.GIT_REF and not args.ref:
        raise UsageError("--source git-ref requires --ref.")
    if args.ref and source is not SourceKind.GIT_REF:
        raise UsageError("--ref requires --source git-ref.")
    if operation is Operation.PUBLIC_PUSH and source not in {None, SourceKind.HISTORY_ALL}:
        raise UsageError("public-push requires --source history-all.")
    if operation in {Operation.TAG, Operation.RELEASE} and source not in {
        None,
        SourceKind.HEAD,
        SourceKind.GIT_REF,
    }:
        raise UsageError("tag and release require --source head or git-ref.")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_arguments(args)
    except UsageError as exc:
        parser.error(str(exc))
    try:
        report = run_check(
            Path(args.project),
            project_type=ProjectType(args.project_type),
            operation=Operation(args.operation),
            source=SourceKind(args.source) if args.source else None,
            ref=args.ref,
            assets=args.asset,
            source_only=args.source_only,
            report_md=args.report_md,
            report_json=args.report_json,
        )
        print(render_markdown(report), end="")
        return report.exit_code
    except ReportWriteError as exc:
        print(f"shipgate: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError) as exc:
        print(f"shipgate: unable to complete check: {type(exc).__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
