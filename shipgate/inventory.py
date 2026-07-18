"""Build deterministic filesystem inventories without following unsafe links."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path

from .model import (
    Exclusion,
    Finding,
    Inventory,
    InventoryEntry,
    MetadataEntry,
    MetadataScope,
    Severity,
    metadata_label,
)

CHUNK_SIZE = 64 * 1024


def safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "external/" + path.name
    value = relative.as_posix()
    return value if value and value != "." else "."


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def inventory_error(code: str, path: str, message: str) -> Finding:
    return Finding(
        code=code,
        severity=Severity.ERROR,
        path=path,
        message=message,
        fingerprint=_fingerprint(f"{code}:{path}"),
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def build_filesystem_inventory(
    project: Path,
    excluded_paths: Iterable[Path] = (),
) -> Inventory:
    root = project.resolve()
    excluded_resolved = {item.expanduser().resolve(strict=False) for item in excluded_paths}
    entries: list[InventoryEntry] = []
    errors: list[Finding] = []
    exclusions: list[Exclusion] = []
    metadata_entries: list[MetadataEntry] = []

    if not root.exists() or not root.is_dir():
        errors.append(
            inventory_error(
                "inventory.invalid-project",
                ".",
                "Project must be an existing directory.",
            )
        )
        return Inventory((), errors, (), project_root=root)

    def visit(directory: Path) -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            rel = safe_relative(directory, root)
            errors.append(
                inventory_error(
                    "inventory.unreadable-directory", rel, "Directory could not be read."
                )
            )
            return
        for child in children:
            path = Path(child.path)
            rel = safe_relative(path, root)
            if rel == ".git" or rel.startswith(".git/"):
                exclusions.append(Exclusion(rel, "git-internal"))
                continue
            if path.resolve(strict=False) in excluded_resolved:
                exclusions.append(Exclusion(rel, "report-output"))
                continue
            raw_path = rel.encode("utf-8", "surrogateescape")
            metadata_entries.append(
                MetadataEntry(
                    metadata_label(MetadataScope.FILE_PATH, raw_path),
                    MetadataScope.FILE_PATH,
                    raw_path,
                )
            )
            try:
                mode = child.stat(follow_symlinks=False).st_mode
            except OSError:
                errors.append(
                    inventory_error(
                        "inventory.unreadable-entry", rel, "Entry metadata could not be read."
                    )
                )
                continue
            if stat.S_ISLNK(mode):
                try:
                    target = path.resolve(strict=True)
                except OSError:
                    errors.append(
                        inventory_error(
                            "inventory.broken-symlink", rel, "Broken symlink is not scannable."
                        )
                    )
                    continue
                if not _is_within(target, root):
                    errors.append(
                        inventory_error(
                            "inventory.external-symlink",
                            rel,
                            "Symlink points outside the project and was not followed.",
                        )
                    )
                    continue
                try:
                    target_stat = target.stat()
                except OSError:
                    errors.append(
                        inventory_error(
                            "inventory.unreadable-symlink", rel, "Symlink target could not be read."
                        )
                    )
                    continue
                if not stat.S_ISREG(target_stat.st_mode):
                    errors.append(
                        inventory_error(
                            "inventory.unsupported-symlink",
                            rel,
                            "Only symlinks to regular files inside the project are supported.",
                        )
                    )
                    continue
                entries.append(
                    InventoryEntry(rel, target_stat.st_size, "working-tree", fs_path=target)
                )
            elif stat.S_ISDIR(mode):
                visit(path)
            elif stat.S_ISREG(mode):
                entries.append(
                    InventoryEntry(
                        rel, child.stat(follow_symlinks=False).st_size, "working-tree", fs_path=path
                    )
                )
            else:
                errors.append(
                    inventory_error(
                        "inventory.special-file",
                        rel,
                        "Special files cannot be scanned safely.",
                    )
                )

    visit(root)
    entries.sort(key=lambda item: (item.path, item.source, item.object_id or ""))
    errors.sort(key=lambda item: (item.path, item.code))
    exclusions.sort(key=lambda item: (item.path, item.reason))
    return Inventory(
        tuple(entries),
        errors,
        tuple(exclusions),
        project_root=root,
        metadata_entries=tuple(metadata_entries),
    )


def stream_file(entry: InventoryEntry) -> Iterator[bytes]:
    if entry.fs_path is None:
        raise OSError("filesystem entry has no file path")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(entry.fs_path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("entry changed and is no longer a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        closed = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (
            closed.st_dev,
            closed.st_ino,
            closed.st_size,
            closed.st_mtime_ns,
        ):
            raise OSError("entry changed while it was being scanned")
    finally:
        os.close(descriptor)
