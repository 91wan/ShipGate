"""Deterministic split README structure validation."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from ..model import Finding, GateResult, Severity, Status

LINK_PATTERN = re.compile(r"\[[^\]\n]+\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")


def _read_utf8(path: Path) -> tuple[str | None, Finding | None]:
    try:
        return path.read_text(encoding="utf-8-sig"), None
    except (OSError, UnicodeError):
        return None, Finding(
            code="readme.unreadable",
            severity=Severity.ERROR,
            path=path.name,
            message="README must be readable as UTF-8 or UTF-8 with BOM.",
        )


def _top_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if lines and lines[0] == "---":
        try:
            closing = lines.index("---", 1)
            lines = lines[closing + 1 :]
        except ValueError:
            pass
    return [line for line in lines if line.strip()][:10]


def _has_link(text: str, target: str) -> bool:
    for match in LINK_PATTERN.finditer("\n".join(_top_lines(text))):
        parsed = urlsplit(unquote(match.group(1)))
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            continue
        normalized = PurePosixPath(parsed.path).as_posix()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized == target and ".." not in PurePosixPath(normalized).parts:
            return True
    return False


def check_readmes(project: Path) -> GateResult:
    english = project / "README.md"
    chinese = project / "README_ZH.md"
    findings: list[Finding] = []
    if not english.is_file():
        findings.append(
            Finding(
                "readme.missing-english",
                Severity.ERROR,
                "README.md",
                "Exact root file README.md is required.",
            )
        )
    if not chinese.is_file():
        findings.append(
            Finding(
                "readme.missing-chinese",
                Severity.ERROR,
                "README_ZH.md",
                "Exact root file README_ZH.md is required.",
            )
        )
    if findings:
        return GateResult(
            "readme-bilingual",
            Status.FAIL,
            "README must use split language pages for public GitHub release.",
            tuple(findings),
        )
    english_text, english_error = _read_utf8(english)
    chinese_text, chinese_error = _read_utf8(chinese)
    findings.extend(item for item in (english_error, chinese_error) if item is not None)
    if english_text is not None and not _has_link(english_text, "README_ZH.md"):
        findings.append(
            Finding(
                "readme.missing-chinese-link",
                Severity.ERROR,
                "README.md",
                "README.md must link to README_ZH.md within its first 10 content lines.",
            )
        )
    if chinese_text is not None and not _has_link(chinese_text, "README.md"):
        findings.append(
            Finding(
                "readme.missing-english-link",
                Severity.ERROR,
                "README_ZH.md",
                "README_ZH.md must link back to README.md within its first 10 content lines.",
            )
        )
    if findings:
        status = (
            Status.ERROR
            if any(item.code == "readme.unreadable" for item in findings)
            else Status.FAIL
        )
        return GateResult(
            "readme-bilingual",
            status,
            "README must use split language pages for public GitHub release.",
            tuple(findings),
        )
    return GateResult(
        "readme-bilingual",
        Status.PASS,
        "README uses split language pages with reciprocal top links.",
    )
