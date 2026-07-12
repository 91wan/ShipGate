"""Typed models shared by ShipGate checks and report renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


def sanitize_text(value: object) -> str:
    return "".join(
        character if ord(character) >= 32 and ord(character) != 127 else "?"
        for character in str(value)
    )


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_APPLICABLE = "not-applicable"
    ERROR = "error"


class Operation(StrEnum):
    LOCAL = "local"
    PUBLIC_PUSH = "public-push"
    TAG = "tag"
    RELEASE = "release"


class ProjectType(StrEnum):
    AUTO = "auto"
    CODEX_SKILL = "codex-skill"
    MACOS_APP = "macos-app"


class SourceKind(StrEnum):
    WORKING_TREE = "working-tree"
    INDEX = "index"
    HEAD = "head"
    GIT_REF = "git-ref"
    HISTORY_ALL = "history-all"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    path: str
    message: str
    line: int | None = None
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity.value,
            "path": sanitize_text(self.path),
            "line": self.line,
            "message": sanitize_text(self.message),
        }
        if self.fingerprint is not None:
            value["fingerprint"] = self.fingerprint
        return value


@dataclass(frozen=True)
class Evidence:
    code: str
    path: str
    detail: str
    project_type: ProjectType

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": sanitize_text(self.path),
            "detail": sanitize_text(self.detail),
            "project_type": self.project_type.value,
        }


@dataclass(frozen=True)
class GateResult:
    id: str
    status: Status
    detail: str
    findings: tuple[Finding, ...] = ()

    @property
    def name(self) -> str:
        return self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "detail": sanitize_text(self.detail),
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    size: int
    source: str
    fs_path: Path | None = field(default=None, repr=False, compare=False)
    object_id: str | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class Exclusion:
    path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": sanitize_text(self.path), "reason": sanitize_text(self.reason)}


@dataclass
class Inventory:
    entries: tuple[InventoryEntry, ...]
    errors: list[Finding] = field(default_factory=list)
    excluded: tuple[Exclusion, ...] = ()
    scanned_files: int = 0
    scanned_bytes: int = 0
    project_root: Path = field(default=Path("."), repr=False)
    git_root: Path | None = field(default=None, repr=False)

    @property
    def considered_files(self) -> int:
        return len(self.entries) + len(self.errors) + len(self.excluded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "considered_files": self.considered_files,
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "excluded": [item.to_dict() for item in self.excluded],
            "errors": [item.to_dict() for item in self.errors],
        }


@dataclass(frozen=True)
class AssetRecord:
    path: str
    size: int
    sha256: str | None
    status: Status
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": sanitize_text(self.path),
            "size": self.size,
            "sha256": self.sha256,
            "status": self.status.value,
            "detail": sanitize_text(self.detail),
        }


@dataclass(frozen=True)
class ProjectDetection:
    candidates: tuple[ProjectType, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class SourceMetadata:
    kind: SourceKind
    ref: str | None = None
    commit: str | None = None
    dirty: bool | None = None
    shallow: bool | None = None
    submodules: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ref": sanitize_text(self.ref) if self.ref is not None else None,
            "commit": self.commit,
            "dirty": self.dirty,
            "shallow": self.shallow,
            "submodules": list(self.submodules),
        }


@dataclass
class Report:
    schema_version: str
    shipgate_version: str
    status: Status
    operation: Operation
    requested_type: ProjectType
    detected_type: ProjectType | None
    evidence: tuple[Evidence, ...]
    source: SourceMetadata
    inventory: Inventory
    gates: tuple[GateResult, ...]
    assets: tuple[AssetRecord, ...]
    recommendations: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        if any(item.status is Status.ERROR for item in self.gates):
            return 3
        return 0 if self.status is Status.PASS else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "shipgate_version": self.shipgate_version,
            "status": self.status.value,
            "operation": self.operation.value,
            "project": {
                "root": ".",
                "requested_type": self.requested_type.value,
                "detected_type": (
                    self.detected_type.value if self.detected_type is not None else None
                ),
                "evidence": [item.to_dict() for item in self.evidence],
            },
            "source": self.source.to_dict(),
            "inventory": self.inventory.to_dict(),
            "gates": [item.to_dict() for item in self.gates],
            "assets": [item.to_dict() for item in self.assets],
            "recommendations": list(self.recommendations),
        }


def overall_status(gates: tuple[GateResult, ...] | list[GateResult]) -> Status:
    if any(item.status in {Status.FAIL, Status.ERROR} for item in gates):
        return Status.FAIL
    return Status.PASS
