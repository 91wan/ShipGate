"""Release asset validation and stable SHA-256 hashing."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path

from ..model import AssetRecord, Finding, GateResult, Operation, Severity, Status


def _asset_label(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return "external/" + path.name


def _hash_stable_file(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("asset is not a regular file")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OSError("asset changed while hashing")
        return before.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


def check_assets(
    project: Path,
    assets: Iterable[Path | str],
    operation: Operation,
    source_only: bool,
) -> tuple[GateResult, tuple[AssetRecord, ...]]:
    raw_assets = list(assets)
    if not raw_assets:
        if operation is Operation.RELEASE and not source_only:
            finding = Finding(
                "asset.required",
                Severity.ERROR,
                ".",
                "Release operation requires at least one asset or explicit --source-only.",
            )
            return GateResult("assets", Status.FAIL, finding.message, (finding,)), ()
        detail = (
            "Source-only release explicitly selected; no binary assets apply."
            if operation is Operation.RELEASE
            else f"Asset verification does not apply to {operation.value} without --asset."
        )
        return GateResult("assets", Status.NOT_APPLICABLE, detail), ()

    records: list[AssetRecord] = []
    findings: list[Finding] = []
    seen: set[Path] = set()
    root = project.resolve()
    for raw in raw_assets:
        supplied = Path(raw).expanduser()
        path = supplied if supplied.is_absolute() else root / supplied
        resolved = path.resolve(strict=False)
        label = _asset_label(resolved, root)
        try:
            resolved.relative_to(root)
        except ValueError:
            message = "Release assets must be inside the project root."
            findings.append(Finding("asset.external", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.FAIL, message))
            continue
        if path.is_symlink():
            message = "Symlink assets are not accepted."
            findings.append(Finding("asset.symlink", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.FAIL, message))
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            message = "Asset is missing."
            findings.append(Finding("asset.missing", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.FAIL, message))
            continue
        try:
            mode = resolved.stat().st_mode
        except OSError:
            message = "Asset metadata could not be read."
            findings.append(Finding("asset.unreadable", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.ERROR, message))
            continue
        if not stat.S_ISREG(mode):
            message = "Asset is not a regular file."
            findings.append(Finding("asset.not-regular", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.FAIL, message))
            continue
        try:
            size, digest = _hash_stable_file(resolved)
        except OSError:
            message = "Asset is missing, unreadable, unstable, or not a regular file."
            findings.append(Finding("asset.unreadable", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.ERROR, message))
            continue
        if size == 0:
            message = "Asset is empty."
            findings.append(Finding("asset.empty", Severity.ERROR, label, message))
            records.append(AssetRecord(label, 0, None, Status.FAIL, message))
            continue
        records.append(AssetRecord(label, size, digest, Status.PASS, "Asset hash is stable."))
    ordered_records = tuple(sorted(records, key=lambda item: item.path))
    ordered_findings = tuple(sorted(findings, key=lambda item: (item.path, item.code)))
    if ordered_findings:
        status = (
            Status.ERROR
            if any(item.code == "asset.unreadable" for item in ordered_findings)
            else Status.FAIL
        )
        return GateResult(
            "assets",
            status,
            f"Asset verification found {len(ordered_findings)} blocking issue(s).",
            ordered_findings,
        ), ordered_records
    return GateResult(
        "assets",
        Status.PASS,
        f"Verified {len(ordered_records)} unique release asset(s).",
    ), ordered_records
