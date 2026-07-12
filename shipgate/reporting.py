"""Stable, path-safe Markdown and JSON report rendering."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model import Report


class ReportWriteError(RuntimeError):
    """Raised when an output report cannot be written atomically."""


def clean_text(value: object) -> str:
    text = str(value)
    return "".join(
        character if character >= " " or character == "\t" else "?" for character in text
    )


def escape_table(value: object) -> str:
    return clean_text(value).replace("|", "\\|").replace("\n", " ")


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def render_markdown(report: Report) -> str:
    data = report.to_dict()
    project = data["project"]
    source = data["source"]
    inventory = data["inventory"]
    lines = [
        "# ShipGate Report",
        "",
        f"- Status: `{data['status']}`",
        f"- Operation: `{data['operation']}`",
        f"- Project root: `{project['root']}`",
        f"- Requested type: `{project['requested_type']}`",
        f"- Detected type: `{project['detected_type'] or 'none'}`",
        f"- Source: `{source['kind']}`",
        f"- Commit: `{source['commit'] or 'not-applicable'}`",
        "",
        "## Inventory",
        "",
        f"- Considered files: {inventory['considered_files']}",
        f"- Scanned files: {inventory['scanned_files']}",
        f"- Scanned bytes: {inventory['scanned_bytes']}",
        f"- Errors: {len(inventory['errors'])}",
        f"- Exclusions: {len(inventory['excluded'])}",
        "",
        "## Gates",
        "",
        "| Gate | Status | Detail | Findings |",
        "| --- | --- | --- | ---: |",
    ]
    for gate in data["gates"]:
        lines.append(
            f"| {escape_table(gate['id'])} | {escape_table(gate['status'])} | "
            f"{escape_table(gate['detail'])} | {len(gate['findings'])} |"
        )
    findings = [finding for gate in data["gates"] for finding in gate["findings"]]
    lines.extend(["", "## Findings", ""])
    if findings:
        lines.extend(
            [
                "| Code | Severity | Path | Line | Message | Fingerprint |",
                "| --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for item in findings:
            lines.append(
                f"| {escape_table(item['code'])} | {escape_table(item['severity'])} | "
                f"{escape_table(item['path'])} | {item['line'] or ''} | "
                f"{escape_table(item['message'])} | {escape_table(item.get('fingerprint', ''))} |"
            )
    else:
        lines.append("No blocking findings were recorded.")
    lines.extend(["", "## Assets", ""])
    if data["assets"]:
        lines.extend(
            [
                "| Path | Status | Size | SHA-256 | Detail |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for item in data["assets"]:
            lines.append(
                f"| {escape_table(item['path'])} | {escape_table(item['status'])} | "
                f"{item['size']} | {escape_table(item['sha256'] or '')} | "
                f"{escape_table(item['detail'])} |"
            )
    else:
        lines.append("No assets were supplied or applicable.")
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {clean_text(item)}" for item in data["recommendations"])
    lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise ReportWriteError("Report output could not be written atomically.") from exc


def write_reports(
    report: Report,
    report_md: Path | str | None,
    report_json: Path | str | None,
) -> None:
    if report_json is not None:
        _atomic_write(Path(report_json).expanduser(), render_json(report))
    if report_md is not None:
        _atomic_write(Path(report_md).expanduser(), render_markdown(report))


def report_dict(report: Report) -> dict[str, Any]:
    data = report.to_dict()
    data["project_type"] = data["project"]["detected_type"] or data["project"]["requested_type"]
    return data
