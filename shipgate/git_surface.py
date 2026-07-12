"""Read-only Git publication surface discovery."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .inventory import inventory_error, safe_relative
from .model import Exclusion, Finding, Inventory, InventoryEntry, SourceKind, SourceMetadata

GIT_TIMEOUT_SECONDS = 30


class GitSurfaceError(RuntimeError):
    """Raised when Git cannot provide a trustworthy publication surface."""


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return env


def run_git(
    project: Path,
    args: Iterable[str],
    *,
    text: bool = True,
    input_data: str | bytes | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    command = ["git", "-C", str(project), *args]
    try:
        return subprocess.run(
            command,
            input=input_data,
            capture_output=True,
            text=text,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_git_env(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitSurfaceError("Git could not be executed reliably.") from exc


def require_git_output(project: Path, args: Iterable[str]) -> str:
    result = run_git(project, args)
    assert isinstance(result.stdout, str)
    if result.returncode != 0:
        raise GitSurfaceError("Git metadata could not be read.")
    return result.stdout.strip()


@dataclass(frozen=True)
class GitContext:
    root: Path
    metadata: SourceMetadata


def inspect_git(
    project: Path,
    source: SourceKind,
    ref: str | None,
) -> GitContext:
    root_text = require_git_output(project, ["rev-parse", "--show-toplevel"])
    root = Path(root_text).resolve()
    if root != project.resolve():
        raise GitSurfaceError("Public operations must run at the Git repository root.")
    shallow = require_git_output(root, ["rev-parse", "--is-shallow-repository"]) == "true"
    status = run_git(root, ["status", "--porcelain=v1", "-z"], text=False)
    if status.returncode != 0:
        raise GitSurfaceError("Git worktree status could not be read.")
    assert isinstance(status.stdout, bytes)
    dirty = bool(status.stdout)

    selected_ref = ref if source is SourceKind.GIT_REF else "HEAD"
    commit: str | None = None
    if source in {SourceKind.HEAD, SourceKind.GIT_REF, SourceKind.HISTORY_ALL}:
        commit = require_git_output(root, ["rev-parse", "--verify", f"{selected_ref}^{{commit}}"])

    stage = require_git_output(root, ["ls-files", "--stage"])
    submodules = tuple(
        sorted(
            line.split("\t", 1)[1]
            for line in stage.splitlines()
            if line.startswith("160000 ") and "\t" in line
        )
    )
    return GitContext(
        root,
        SourceMetadata(
            kind=source,
            ref=ref,
            commit=commit,
            dirty=dirty,
            shallow=shallow,
            submodules=submodules,
        ),
    )


def _working_entries(
    root: Path, excluded_paths: set[Path]
) -> tuple[list[InventoryEntry], list[Exclusion]]:
    tracked_result = run_git(root, ["ls-files", "-z", "--cached"], text=False)
    if tracked_result.returncode != 0:
        raise GitSurfaceError("Git tracked-file inventory could not be listed.")
    assert isinstance(tracked_result.stdout, bytes)
    tracked = {
        item.decode("utf-8", errors="surrogateescape")
        for item in tracked_result.stdout.split(b"\0")
        if item
    }
    result = run_git(
        root,
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        text=False,
    )
    if result.returncode != 0:
        raise GitSurfaceError("Git working-tree inventory could not be listed.")
    assert isinstance(result.stdout, bytes)
    entries: list[InventoryEntry] = []
    exclusions: list[Exclusion] = []
    for raw in sorted(item for item in result.stdout.split(b"\0") if item):
        path_text = raw.decode("utf-8", errors="surrogateescape")
        path = root / path_text
        resolved = path.resolve(strict=False)
        rel = Path(path_text).as_posix()
        if resolved in excluded_paths:
            if path_text in tracked:
                raise GitSurfaceError("A report output path is tracked and cannot be excluded.")
            exclusions.append(Exclusion(rel, "report-output"))
            continue
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
                target.relative_to(root)
            except (OSError, ValueError):
                entries.append(InventoryEntry(rel, 0, "working-tree", fs_path=path))
                continue
            if target.is_file():
                entries.append(
                    InventoryEntry(rel, target.stat().st_size, "working-tree", fs_path=target)
                )
                continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        entries.append(InventoryEntry(rel, size, "working-tree", fs_path=path))
    return entries, exclusions


def _history_entries(root: Path, ref: str | None, all_refs: bool) -> list[InventoryEntry]:
    rev_args = ["-c", "core.quotePath=false", "rev-list", "--objects"]
    rev_args.append("--all" if all_refs else (ref or "HEAD"))
    listing = require_git_output(root, rev_args)
    object_paths: dict[str, str] = {}
    object_ids: list[str] = []
    for line in listing.splitlines():
        object_id, _, path = line.partition(" ")
        if not object_id:
            continue
        object_ids.append(object_id)
        if path and object_id not in object_paths:
            object_paths[object_id] = path
    if not object_ids:
        return []
    payload = "".join(f"{item}\n" for item in object_ids)
    checked = run_git(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data=payload,
    )
    if checked.returncode != 0:
        raise GitSurfaceError("Git history objects could not be inspected.")
    assert isinstance(checked.stdout, str)
    entries: list[InventoryEntry] = []
    seen: set[str] = set()
    for line in checked.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        object_id, _, size_text = parts
        if object_id in seen:
            continue
        seen.add(object_id)
        path = object_paths.get(object_id, f"objects/{object_id[:12]}")
        entries.append(
            InventoryEntry(
                Path(path).as_posix(),
                int(size_text),
                "git-history",
                object_id=object_id,
            )
        )
    return entries


def _index_entries(root: Path) -> list[InventoryEntry]:
    result = run_git(root, ["ls-files", "--stage", "-z"], text=False)
    if result.returncode != 0:
        raise GitSurfaceError("Git index inventory could not be listed.")
    assert isinstance(result.stdout, bytes)
    pending: list[tuple[str, str]] = []
    for record in (item for item in result.stdout.split(b"\0") if item):
        metadata, separator, raw_path = record.partition(b"\t")
        parts = metadata.split()
        if not separator or len(parts) != 3:
            raise GitSurfaceError("Git index record was malformed.")
        mode, object_id, stage = (item.decode("ascii") for item in parts)
        if stage != "0":
            raise GitSurfaceError("Git index contains unresolved merge stages.")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if mode == "160000":
            continue
        pending.append((object_id, Path(path).as_posix()))
    payload = "".join(f"{object_id}\n" for object_id, _ in pending)
    checked = run_git(
        root,
        ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input_data=payload,
    )
    if checked.returncode != 0:
        raise GitSurfaceError("Git index objects could not be inspected.")
    assert isinstance(checked.stdout, str)
    sizes: dict[str, int] = {}
    for line in checked.stdout.splitlines():
        object_parts = line.split()
        if len(object_parts) != 3 or object_parts[1] != "blob":
            raise GitSurfaceError("Git index contains a non-blob file entry.")
        sizes[object_parts[0]] = int(object_parts[2])
    return [
        InventoryEntry(path, sizes[object_id], "git-index", object_id=object_id)
        for object_id, path in pending
    ]


def build_git_inventory(
    context: GitContext,
    excluded_paths: Iterable[Path],
    *,
    include_working: bool,
    include_history: bool,
    include_index: bool = False,
    history_ref: str | None,
    all_refs: bool,
) -> Inventory:
    entries: list[InventoryEntry] = []
    exclusions: list[Exclusion] = []
    excluded = {item.expanduser().resolve(strict=False) for item in excluded_paths}
    if include_working:
        working, working_excluded = _working_entries(context.root, excluded)
        entries.extend(working)
        exclusions.extend(working_excluded)
    if include_index:
        entries.extend(_index_entries(context.root))
    if include_history:
        entries.extend(_history_entries(context.root, history_ref, all_refs))
    entries.sort(key=lambda item: (item.source, item.path, item.object_id or ""))
    return Inventory(
        tuple(entries),
        [],
        tuple(sorted(exclusions, key=lambda item: (item.path, item.reason))),
        project_root=context.root,
        git_root=context.root,
    )


def stream_git_blob(root: Path, object_id: str, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    command = ["git", "-C", str(root), "cat-file", "--batch"]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
        )
    except OSError as exc:
        raise GitSurfaceError("Git blob reader could not start.") from exc
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write(object_id.encode("ascii") + b"\n")
        process.stdin.flush()
        header = process.stdout.readline().decode("ascii", errors="replace").strip()
        parts = header.split()
        if len(parts) != 3 or parts[1] != "blob":
            raise GitSurfaceError("Git blob header was invalid.")
        remaining = int(parts[2])
        while remaining:
            chunk = process.stdout.read(min(chunk_size, remaining))
            if not chunk:
                raise GitSurfaceError("Git blob ended before its declared size.")
            remaining -= len(chunk)
            yield chunk
        if process.stdout.read(1) != b"\n":
            raise GitSurfaceError("Git blob framing was invalid.")
    finally:
        process.stdin.close()
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def git_policy_errors(context: GitContext, *, require_clean: bool) -> list[Finding]:
    errors: list[Finding] = []
    if context.metadata.shallow:
        errors.append(
            inventory_error(
                "git.shallow-repository",
                ".",
                "Public operations require complete reachable Git history.",
            )
        )
    if context.metadata.submodules:
        for path in context.metadata.submodules:
            errors.append(
                inventory_error(
                    "git.unverified-submodule",
                    safe_relative(context.root / path, context.root),
                    "Submodule content was not verified.",
                )
            )
    if require_clean and context.metadata.dirty:
        errors.append(
            inventory_error(
                "git.dirty-worktree",
                ".",
                "Tag and release operations require a clean working tree.",
            )
        )
    return errors
