#!/usr/bin/env python3
"""Install ShipGate with bounded staging and atomic directory replacement."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
MARKER_NAME = ".shipgate-install.json"
MARKER = {"format": 1, "name": "shipgate"}
RUNTIME_PATHS = (
    Path("SKILL.md"),
    Path("README.md"),
    Path("README_ZH.md"),
    Path("LICENSE"),
    Path("agents"),
    Path("scripts/shipgate.py"),
    Path("shipgate"),
)


class InstallError(RuntimeError):
    """Unsafe or incomplete installation request."""


def _contains_symlink(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == current.parent:
            return False
        current = current.parent


def _safe_target(path: Path, home: Path) -> Path:
    if not path.is_absolute():
        raise InstallError("Installation target must be an absolute path.")
    target = path.resolve(strict=False)
    root = Path(target.anchor)
    if target.name != "shipgate":
        raise InstallError("Installation target basename must be shipgate.")
    if target in {root, home, home.parent, Path.cwd().resolve()}:
        raise InstallError("Installation target is too broad.")
    if _contains_symlink(path):
        raise InstallError("Installation target or one of its parents is a symlink.")
    return target


def resolve_target(args: argparse.Namespace, env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    home = Path(values.get("HOME", str(Path.home()))).expanduser().resolve()
    if args.scope == "repo":
        repo = Path(args.repo or Path.cwd()).expanduser().resolve()
        if not repo.is_dir():
            raise InstallError("--repo must point to an existing directory.")
        candidate = repo / ".agents" / "skills" / "shipgate"
    elif args.scope == "user":
        candidate = home / ".agents" / "skills" / "shipgate"
    elif args.scope == "claude-repo":
        repo = Path(args.repo or Path.cwd()).expanduser().resolve()
        if not repo.is_dir():
            raise InstallError("--repo must point to an existing directory.")
        candidate = repo / ".claude" / "skills" / "shipgate"
    elif args.scope == "claude-user":
        candidate = home / ".claude" / "skills" / "shipgate"
    elif args.scope == "codex-home":
        codex_home = Path(values.get("CODEX_HOME", str(home / ".codex"))).expanduser()
        candidate = codex_home / "skills" / "shipgate"
    else:
        if not args.target:
            raise InstallError("--scope custom requires --target.")
        candidate = Path(args.target).expanduser()
    return _safe_target(candidate, home)


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise InstallError("Runtime source must not contain symlinks.")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            if child.name == "__pycache__" or child.suffix in {".pyc", ".pyo"}:
                continue
            _copy_path(child, destination / child.name)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    else:
        raise InstallError(f"Required runtime path is missing: {source.name}")


def _remove_owned_tree(path: Path, parent: Path) -> None:
    if path.parent != parent or not (path.name == "shipgate" or path.name.startswith(".shipgate.")):
        raise InstallError("Refusing to remove an unbounded directory.")

    def remove(node: Path) -> None:
        if node.is_symlink() or node.is_file():
            node.unlink()
            return
        if not node.exists():
            return
        for child in sorted(node.iterdir(), key=lambda item: item.name, reverse=True):
            remove(child)
        node.rmdir()

    remove(path)


def _is_shipgate_install(path: Path) -> bool:
    marker = path / MARKER_NAME
    try:
        return json.loads(marker.read_text(encoding="utf-8")) == MARKER
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def install(target: Path, *, force: bool, dry_run: bool) -> None:
    parent = target.parent
    if target.exists() and not target.is_dir():
        raise InstallError("Existing installation target is not a directory.")
    if target.exists() and not _is_shipgate_install(target) and not force:
        raise InstallError(
            "Existing target is not a marked ShipGate installation; use --force after review."
        )
    if dry_run:
        return
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / f".shipgate.stage-{uuid.uuid4().hex}"
    backup = parent / f".shipgate.backup-{uuid.uuid4().hex}"
    try:
        stage.mkdir()
        for relative in RUNTIME_PATHS:
            _copy_path(SOURCE_ROOT / relative, stage / relative)
        (stage / MARKER_NAME).write_text(
            json.dumps(MARKER, sort_keys=True) + "\n", encoding="utf-8"
        )
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(stage, target)
        except BaseException:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            _remove_owned_tree(backup, parent)
    finally:
        if stage.exists():
            _remove_owned_tree(stage, parent)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely install the ShipGate skill.")
    parser.add_argument(
        "--scope",
        choices=("repo", "user", "claude-repo", "claude-user", "codex-home", "custom"),
        default="user",
    )
    parser.add_argument("--repo", help="Repository root for --scope repo or --scope claude-repo.")
    parser.add_argument("--target", help="Absolute target ending in shipgate for custom scope.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        target = resolve_target(args)
        install(target, force=args.force, dry_run=args.dry_run)
    except InstallError as exc:
        print(f"install_skill: {exc}", file=sys.stderr)
        return 2
    action = "Would install" if args.dry_run else "Installed"
    print(f"{action} ShipGate at {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
