#!/usr/bin/env python3
"""Compatibility entry point for ShipGate."""
# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shipgate.checks.project import (
    check_codex_skill as _check_codex_skill,
)
from shipgate.checks.project import (
    detect_project,
)
from shipgate.cli import main
from shipgate.engine import check_project
from shipgate.model import ProjectType

__all__ = ["check_codex_skill", "check_project", "detect_project_type", "main"]


def detect_project_type(project: Path | str) -> str | None:
    detection = detect_project(Path(project).expanduser().resolve(strict=False))
    if len(detection.candidates) != 1:
        return None
    return detection.candidates[0].value


def check_codex_skill(project: Path | str) -> dict[str, object]:
    return _check_codex_skill(Path(project).expanduser().resolve(strict=False)).to_dict()


SUPPORTED_PROJECT_TYPES = {
    ProjectType.CODEX_SKILL.value,
    ProjectType.MACOS_APP.value,
}


if __name__ == "__main__":
    raise SystemExit(main())
