"""Read-only Git publication surface discovery."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .inventory import inventory_error, safe_relative
from .model import (
    Exclusion,
    Finding,
    Inventory,
    InventoryEntry,
    MetadataEntry,
    MetadataScope,
    SourceKind,
    SourceMetadata,
    metadata_label,
)

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


def _path_metadata(path: str) -> MetadataEntry:
    raw = path.encode("utf-8", "surrogateescape")
    return MetadataEntry(metadata_label(MetadataScope.FILE_PATH, raw), MetadataScope.FILE_PATH, raw)


def _safe_metadata(label: str, scope: MetadataScope, content: bytes) -> MetadataEntry:
    return MetadataEntry(label, scope, content)


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


def _batch_read_objects(root: Path, object_ids: list[str]) -> dict[str, tuple[str, bytes]]:
    if not object_ids:
        return {}
    payload = b"".join(item.encode("ascii") + b"\n" for item in object_ids)
    result = run_git(root, ["cat-file", "--batch"], text=False, input_data=payload)
    if result.returncode != 0:
        raise GitSurfaceError("Git metadata objects could not be read.")
    assert isinstance(result.stdout, bytes)
    output = result.stdout
    cursor = 0
    objects: dict[str, tuple[str, bytes]] = {}
    while cursor < len(output):
        line_end = output.find(b"\n", cursor)
        if line_end < 0:
            raise GitSurfaceError("Git metadata object framing was invalid.")
        header = output[cursor:line_end].split()
        if len(header) != 3:
            raise GitSurfaceError("Git metadata object header was invalid.")
        try:
            object_id = header[0].decode("ascii")
            object_type = header[1].decode("ascii")
            size = int(header[2])
        except (UnicodeDecodeError, ValueError) as exc:
            raise GitSurfaceError("Git metadata object header was invalid.") from exc
        content_start = line_end + 1
        content_end = content_start + size
        if content_end >= len(output) or output[content_end : content_end + 1] != b"\n":
            raise GitSurfaceError("Git metadata object content was truncated.")
        objects[object_id] = (object_type, output[content_start:content_end])
        cursor = content_end + 1
    if set(objects) != set(object_ids):
        raise GitSurfaceError("Git metadata object response was incomplete.")
    return objects


def _identity_entries(label: str, raw: bytes) -> list[MetadataEntry]:
    email_end = raw.rfind(b">")
    email_start = raw.rfind(b"<", 0, email_end)
    if email_start < 0 or email_end <= email_start:
        raise GitSurfaceError("Git identity metadata was malformed.")
    name = raw[:email_start].rstrip()
    email = raw[email_start + 1 : email_end]
    return [
        _safe_metadata(f"{label}/name", MetadataScope.IDENTITY_NAME, name),
        _safe_metadata(f"{label}/email", MetadataScope.IDENTITY_EMAIL, email),
    ]


def _object_metadata_entries(
    object_id: str, object_type: str, content: bytes
) -> list[MetadataEntry]:
    header, separator, message = content.partition(b"\n\n")
    if not separator:
        raise GitSurfaceError("Git metadata object body was malformed.")
    prefix = f"git-{object_type}/{object_id[:12]}"
    entries: list[MetadataEntry] = []
    for line in header.splitlines():
        if object_type == "commit" and line.startswith(b"author "):
            entries.extend(_identity_entries(f"{prefix}/author", line[len(b"author ") :]))
        elif object_type == "commit" and line.startswith(b"committer "):
            entries.extend(_identity_entries(f"{prefix}/committer", line[len(b"committer ") :]))
        elif object_type == "tag" and line.startswith(b"tagger "):
            entries.extend(_identity_entries(f"{prefix}/tagger", line[len(b"tagger ") :]))
        elif object_type == "tag" and line.startswith(b"tag "):
            entries.append(
                _safe_metadata(f"{prefix}/name", MetadataScope.TAG_NAME, line[len(b"tag ") :])
            )
    message_scope = (
        MetadataScope.COMMIT_MESSAGE if object_type == "commit" else MetadataScope.TAG_MESSAGE
    )
    entries.append(_safe_metadata(f"{prefix}/message", message_scope, message))
    return entries


def _commit_path_entries(root: Path, commit_ids: list[str]) -> list[MetadataEntry]:
    if not commit_ids:
        return []
    payload = b"".join(item.encode("ascii") + b"\n" for item in commit_ids)
    result = run_git(
        root,
        [
            "diff-tree",
            "--stdin",
            "--root",
            "-r",
            "-m",
            "--no-renames",
            "--name-only",
            "-z",
            "--no-commit-id",
        ],
        text=False,
        input_data=payload,
    )
    if result.returncode != 0:
        raise GitSurfaceError("Git historical paths could not be enumerated.")
    assert isinstance(result.stdout, bytes)
    return [
        MetadataEntry(metadata_label(MetadataScope.FILE_PATH, path), MetadataScope.FILE_PATH, path)
        for path in sorted(set(item for item in result.stdout.split(b"\0") if item))
    ]


def _ref_entries(
    root: Path, ref: str | None, all_refs: bool
) -> tuple[list[MetadataEntry], list[MetadataEntry]]:
    if all_refs:
        result = run_git(root, ["for-each-ref", "--format=%(refname)"], text=False)
        if result.returncode != 0:
            raise GitSurfaceError("Git refs could not be enumerated.")
        assert isinstance(result.stdout, bytes)
        raw_refs = [item for item in result.stdout.splitlines() if item]
    elif ref is not None:
        raw_refs = [ref.encode("utf-8", "surrogateescape")]
    else:
        raw_refs = []

    refs = [
        MetadataEntry(metadata_label(MetadataScope.REF_NAME, raw), MetadataScope.REF_NAME, raw)
        for raw in raw_refs
    ]
    tree_paths: list[MetadataEntry] = []
    seen_trees: set[str] = set()
    for raw in raw_refs:
        ref_name = raw.decode("utf-8", "surrogateescape")
        commit = run_git(root, ["rev-parse", "--verify", "--quiet", f"{ref_name}^{{commit}}"])
        if commit.returncode == 0:
            continue
        tree = run_git(root, ["rev-parse", "--verify", "--quiet", f"{ref_name}^{{tree}}"])
        if tree.returncode != 0:
            continue
        assert isinstance(tree.stdout, str)
        tree_id = tree.stdout.strip()
        if not tree_id or tree_id in seen_trees:
            continue
        seen_trees.add(tree_id)
        listing = run_git(root, ["ls-tree", "-r", "-z", "--name-only", tree_id], text=False)
        if listing.returncode != 0:
            raise GitSurfaceError("Git non-commit tree paths could not be enumerated.")
        assert isinstance(listing.stdout, bytes)
        tree_paths.extend(
            MetadataEntry(
                metadata_label(MetadataScope.FILE_PATH, path),
                MetadataScope.FILE_PATH,
                path,
            )
            for path in listing.stdout.split(b"\0")
            if path
        )
    return refs, tree_paths


def _history_inventory(
    root: Path, ref: str | None, all_refs: bool
) -> tuple[list[InventoryEntry], list[MetadataEntry]]:
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
        return [], []
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
    metadata_entries: list[MetadataEntry] = []
    seen: set[str] = set()
    metadata_ids: list[str] = []
    commit_ids: list[str] = []
    for line in checked.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise GitSurfaceError("Git history object metadata was malformed.")
        if parts[1] in {"commit", "tag"}:
            metadata_ids.append(parts[0])
            if parts[1] == "commit":
                commit_ids.append(parts[0])
            continue
        if parts[1] != "blob":
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
    for object_id, (object_type, content) in _batch_read_objects(root, metadata_ids).items():
        if object_type not in {"commit", "tag"}:
            raise GitSurfaceError("Git metadata object type changed unexpectedly.")
        metadata_entries.extend(_object_metadata_entries(object_id, object_type, content))
    metadata_entries.extend(_commit_path_entries(root, commit_ids))
    ref_metadata, tree_paths = _ref_entries(root, ref, all_refs)
    metadata_entries.extend(ref_metadata)
    metadata_entries.extend(tree_paths)
    metadata_entries.sort(key=lambda item: (item.scope.value, item.label, item.content))
    return entries, metadata_entries


def _history_entries(root: Path, ref: str | None, all_refs: bool) -> list[InventoryEntry]:
    entries, _ = _history_inventory(root, ref, all_refs)
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
    metadata_entries: list[MetadataEntry] = []
    exclusions: list[Exclusion] = []
    excluded = {item.expanduser().resolve(strict=False) for item in excluded_paths}
    if include_working:
        working, working_excluded = _working_entries(context.root, excluded)
        entries.extend(working)
        metadata_entries.extend(_path_metadata(item.path) for item in working)
        exclusions.extend(working_excluded)
    if include_index:
        indexed = _index_entries(context.root)
        entries.extend(indexed)
        metadata_entries.extend(_path_metadata(item.path) for item in indexed)
    if include_history:
        historical, historical_metadata = _history_inventory(context.root, history_ref, all_refs)
        entries.extend(historical)
        metadata_entries.extend(historical_metadata)
    entries.sort(key=lambda item: (item.source, item.path, item.object_id or ""))
    metadata_entries.sort(key=lambda item: (item.scope.value, item.label, item.content))
    return Inventory(
        tuple(entries),
        [],
        tuple(sorted(exclusions, key=lambda item: (item.path, item.reason))),
        project_root=context.root,
        git_root=context.root,
        metadata_entries=tuple(metadata_entries),
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
