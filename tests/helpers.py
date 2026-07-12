from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "shipgate.py"


def github_token() -> str:
    return "gh" + "p_" + ("Z" * 36)


def make_skill(project: Path) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    (project / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Validate a demo skill.\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# Demo\n\nEnglish | [中文](README_ZH.md)\n\nEnglish documentation.\n",
        encoding="utf-8",
    )
    (project / "README_ZH.md").write_text(
        "# Demo\n\n[English](README.md) | 中文\n\n中文文档。\n",
        encoding="utf-8",
    )
    return project


def git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def init_git(project: Path) -> None:
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.name", "ShipGate Tests")
    git(project, "config", "user.email", "tests@example.invalid")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "initial")


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
