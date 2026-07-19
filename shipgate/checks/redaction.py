"""Streaming high-risk indicator scanner."""

from __future__ import annotations

import codecs
import hashlib
import ipaddress
import itertools
import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from re import Pattern

from ..git_surface import GitSurfaceError, stream_git_blob
from ..inventory import inventory_error, stream_file
from ..model import (
    Finding,
    GateResult,
    Inventory,
    InventoryEntry,
    MetadataScope,
    Severity,
    Status,
    metadata_label,
)

OVERLAP = 4096
SYNTHETIC_TEST_USERS = frozenset({"alice", "example"})
TEST_SOURCE_SUFFIXES = frozenset({".py", ".swift"})


@dataclass(frozen=True)
class Rule:
    code: str
    message: str
    pattern: Pattern[bytes]
    text_pattern: Pattern[str]
    validator: Callable[[bytes | str], bool] | None = None


@dataclass(frozen=True)
class ScanWindow:
    data: bytes | str
    line_base: int


def _rule(
    code: str,
    message: str,
    pattern: bytes,
    validator: Callable[[bytes | str], bool] | None = None,
) -> Rule:
    return Rule(
        code,
        message,
        re.compile(pattern),
        re.compile(pattern.decode("ascii")),
        validator,
    )


RFC1918_NETWORKS = (
    ipaddress.IPv4Network((0x0A000000, 8)),
    ipaddress.IPv4Network((0xAC100000, 12)),
    ipaddress.IPv4Network((0xC0A80000, 16)),
)


def _is_rfc1918(value: bytes | str) -> bool:
    candidate = value.decode("ascii") if isinstance(value, bytes) else value
    try:
        address = ipaddress.IPv4Address(candidate)
    except ipaddress.AddressValueError:
        return False
    return any(address in network for network in RFC1918_NETWORKS)


RULES = (
    _rule(
        "path.private-unix",
        "Private Unix home path indicator found.",
        rb"/(?:Users|home)/[A-Za-z0-9._-]+(?![A-Za-z0-9._-])",
    ),
    _rule(
        "path.private-windows",
        "Private Windows user path indicator found.",
        rb"[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+\\",
    ),
    _rule(
        "secret.github-token",
        "Configured GitHub token indicator found.",
        rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    ),
    _rule(
        "secret.openai-key",
        "Configured OpenAI key indicator found.",
        rb"sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}",
    ),
    _rule(
        "secret.anthropic-key",
        "Configured Anthropic key indicator found.",
        rb"sk-ant-[A-Za-z0-9_-]{20,}",
    ),
    _rule(
        "secret.slack-token",
        "Configured Slack token indicator found.",
        rb"xox[baprs]-[A-Za-z0-9-]{20,}",
    ),
    _rule(
        "secret.aws-access-key-id",
        "Configured AWS access key ID indicator found.",
        rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    ),
    _rule(
        "secret.private-key",
        "Private key header found.",
        rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
    ),
    _rule(
        "network.private-ipv4",
        "RFC 1918 private IPv4 address found.",
        rb"(?<![A-Za-z0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![A-Za-z0-9.])",
        _is_rfc1918,
    ),
)


def _fingerprint(code: str, matched: bytes | str) -> str:
    value = matched if isinstance(matched, bytes) else matched.encode("utf-8")
    digest = hashlib.sha256(code.encode("ascii") + b"\0" + value).hexdigest()
    return "sha256:" + digest[:16]


def _line_number(data: bytes | str, start: int) -> int:
    if isinstance(data, bytes):
        return data[:start].count(b"\n") + 1
    return data[:start].count("\n") + 1


def _is_test_source(path: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate.suffix.lower() not in TEST_SOURCE_SUFFIXES:
        return False
    return any(
        part.lower() in {"test", "tests", "__tests__"} or part.endswith("Tests")
        for part in candidate.parts[:-1]
    )


def _is_allowed_synthetic_test_fixture(
    rule: Rule,
    path: str,
    matched: bytes | str,
) -> bool:
    if rule.code != "path.private-unix" or not _is_test_source(path):
        return False
    value = matched.decode("ascii") if isinstance(matched, bytes) else matched
    username = value.rstrip("/").rsplit("/", 1)[-1]
    return username in SYNTHETIC_TEST_USERS


def _scan_windows(
    windows: Iterable[ScanWindow],
    rules: tuple[Rule, ...],
    path: str,
) -> list[Finding]:
    findings: dict[str, Finding] = {}
    for scan_window in windows:
        window = scan_window.data
        for rule in rules:
            if rule.code in findings:
                continue
            matches = (
                rule.pattern.finditer(window)
                if isinstance(window, bytes)
                else rule.text_pattern.finditer(window)
            )
            for match in matches:
                matched = match.group(0)
                if rule.validator is not None and not rule.validator(matched):
                    continue
                if _is_allowed_synthetic_test_fixture(rule, path, matched):
                    continue
                findings[rule.code] = Finding(
                    code=rule.code,
                    severity=Severity.ERROR,
                    path=path,
                    line=scan_window.line_base + _line_number(window, match.start()),
                    message=rule.message,
                    fingerprint=_fingerprint(rule.code, matched),
                )
                break
    return list(findings.values())


def mask_sensitive_text(value: str | None, scope: MetadataScope) -> str | None:
    if value is None:
        return None
    raw = value.encode("utf-8", "surrogateescape")
    label = metadata_label(scope, raw)
    return label if _scan_windows((ScanWindow(raw, 0),), RULES, label) else value


def _safe_file_label(path: str) -> str:
    masked = mask_sensitive_text(path, MetadataScope.FILE_PATH)
    assert masked is not None
    return masked


def _is_environment_path(path: bytes) -> bool:
    lower_name = path.rsplit(b"/", 1)[-1].lower()
    return lower_name == b".env" or lower_name.startswith(b".env.")


def _environment_finding(path: bytes, label: str) -> Finding:
    return Finding(
        code="secret.env-file",
        severity=Severity.ERROR,
        path=label,
        message="Environment file is part of the publication surface.",
        fingerprint=_fingerprint("secret.env-file", path),
    )


def _byte_windows(chunks: Iterable[bytes]) -> Iterator[ScanWindow]:
    overlap = b""
    line_count = 0
    for chunk in chunks:
        window = overlap + chunk
        yield ScanWindow(window, line_count - overlap.count(b"\n"))
        line_count += chunk.count(b"\n")
        overlap = window[-OVERLAP:]


def _utf16_windows(chunks: Iterable[bytes], encoding: str) -> Iterator[ScanWindow]:
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    overlap = ""
    line_count = 0
    for chunk in chunks:
        text = decoder.decode(chunk)
        window = overlap + text
        yield ScanWindow(window, line_count - overlap.count("\n"))
        line_count += text.count("\n")
        overlap = window[-OVERLAP:]
    tail = decoder.decode(b"", final=True)
    if tail:
        yield ScanWindow(overlap + tail, line_count - overlap.count("\n"))


def _entry_chunks(inventory: Inventory, entry: InventoryEntry) -> Iterator[bytes]:
    if entry.object_id is not None:
        if inventory.git_root is None:
            raise OSError("Git entry has no repository root.")
        yield from stream_git_blob(inventory.git_root, entry.object_id)
    else:
        yield from stream_file(entry)


def scan_inventory(inventory: Inventory) -> GateResult:
    findings: list[Finding] = []
    for metadata_entry in inventory.metadata_entries:
        findings.extend(
            _scan_windows(
                (ScanWindow(metadata_entry.content, 0),),
                RULES,
                metadata_entry.label,
            )
        )
        if metadata_entry.scope is MetadataScope.FILE_PATH and _is_environment_path(
            metadata_entry.content
        ):
            path = metadata_entry.content.decode("utf-8", "surrogateescape")
            findings.append(_environment_finding(metadata_entry.content, _safe_file_label(path)))
        inventory.scanned_metadata += 1
    for entry in inventory.entries:
        safe_path = _safe_file_label(entry.path)
        try:
            byte_count = 0

            def counted_chunks(current_entry: InventoryEntry = entry) -> Iterator[bytes]:
                nonlocal byte_count
                for chunk in _entry_chunks(inventory, current_entry):
                    byte_count += len(chunk)
                    yield chunk

            chunks = iter(counted_chunks())
            first = next(chunks, b"")
            inventory.scanned_files += 1
            prefix = first[:2]
            if prefix in {codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE}:
                encoding = "utf-16-le" if prefix == codecs.BOM_UTF16_LE else "utf-16-be"
                stream = itertools.chain((first[2:],), chunks)
                findings.extend(_scan_windows(_utf16_windows(stream, encoding), RULES, safe_path))
            else:
                stream = itertools.chain((first,), chunks)
                findings.extend(_scan_windows(_byte_windows(stream), RULES, safe_path))
            inventory.scanned_bytes += byte_count
        except (OSError, UnicodeError, GitSurfaceError, ValueError):
            inventory.errors.append(
                inventory_error(
                    "inventory.scan-error",
                    entry.path,
                    "File content could not be scanned reliably.",
                )
            )
    unique: dict[tuple[str, str], Finding] = {}
    for item in findings:
        unique[(item.path, item.code)] = item
    ordered = tuple(sorted(unique.values(), key=lambda item: (item.path, item.code)))
    if ordered:
        return GateResult(
            "redaction",
            Status.FAIL,
            f"Found {len(ordered)} configured high-risk indicator(s).",
            ordered,
        )
    if inventory.errors:
        return GateResult(
            "redaction",
            Status.ERROR,
            "Redaction scan was incomplete because inventory entries could not be read.",
            tuple(sorted(inventory.errors, key=lambda item: (item.path, item.code))),
        )
    return GateResult(
        "redaction",
        Status.PASS,
        "No configured high-risk indicators were found in the scanned inventory.",
    )
