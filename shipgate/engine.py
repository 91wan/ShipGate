"""Fixed ShipGate gate orchestration."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from . import __version__
from .checks.assets import check_assets
from .checks.project import check_codex_skill, check_macos_app, check_project_type, detect_project
from .checks.readme import check_readmes
from .checks.redaction import scan_inventory
from .git_surface import (
    GitSurfaceError,
    build_git_inventory,
    git_policy_errors,
    inspect_git,
)
from .inventory import build_filesystem_inventory
from .model import (
    GateResult,
    Inventory,
    Operation,
    ProjectType,
    Report,
    SourceKind,
    SourceMetadata,
    Status,
    overall_status,
)
from .reporting import report_dict, write_reports

SCHEMA_VERSION = "1.0"


def default_source(operation: Operation) -> SourceKind:
    if operation is Operation.PUBLIC_PUSH:
        return SourceKind.HISTORY_ALL
    if operation in {Operation.TAG, Operation.RELEASE}:
        return SourceKind.HEAD
    return SourceKind.WORKING_TREE


def _output_path(project: Path, raw: Path | str | None) -> Path | None:
    if raw is None:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else project / path


def _source_and_inventory(
    project: Path,
    operation: Operation,
    source: SourceKind,
    ref: str | None,
    excluded_paths: tuple[Path, ...],
) -> tuple[SourceMetadata, Inventory, GateResult]:
    if operation is Operation.LOCAL and source is SourceKind.WORKING_TREE:
        try:
            context = inspect_git(project, source, ref)
            inventory = build_git_inventory(
                context,
                excluded_paths,
                include_working=True,
                include_history=False,
                history_ref=None,
                all_refs=False,
            )
            detail = (
                "Local operation scans tracked and untracked non-ignored "
                "Git working-tree files only."
            )
            return (
                context.metadata,
                inventory,
                GateResult("publication-source", Status.PASS, detail),
            )
        except GitSurfaceError:
            inventory = build_filesystem_inventory(project, excluded_paths)
            metadata = SourceMetadata(SourceKind.WORKING_TREE)
            return (
                metadata,
                inventory,
                GateResult(
                    "publication-source",
                    Status.PASS,
                    "Local non-Git operation scans the filesystem tree only; "
                    "it is not public-release certification.",
                ),
            )
    try:
        context = inspect_git(project, source, ref)
        require_clean = operation in {Operation.TAG, Operation.RELEASE}
        policy_findings = git_policy_errors(context, require_clean=require_clean)
        include_working = operation is Operation.PUBLIC_PUSH or source is SourceKind.WORKING_TREE
        include_history = source in {
            SourceKind.HEAD,
            SourceKind.GIT_REF,
            SourceKind.HISTORY_ALL,
        } or operation in {Operation.PUBLIC_PUSH, Operation.TAG, Operation.RELEASE}
        history_ref = ref if source is SourceKind.GIT_REF else "HEAD"
        all_refs = operation is Operation.PUBLIC_PUSH or source is SourceKind.HISTORY_ALL
        inventory = build_git_inventory(
            context,
            excluded_paths,
            include_working=include_working,
            include_history=include_history,
            include_index=source is SourceKind.INDEX,
            history_ref=history_ref,
            all_refs=all_refs,
        )
        if policy_findings:
            return (
                context.metadata,
                inventory,
                GateResult(
                    "publication-source",
                    Status.FAIL,
                    "Git publication source policy is not satisfied.",
                    tuple(policy_findings),
                ),
            )
        return (
            context.metadata,
            inventory,
            GateResult(
                "publication-source",
                Status.PASS,
                "Git publication source and reachable history were inventoried.",
            ),
        )
    except GitSurfaceError:
        inventory = Inventory((), project_root=project)
        return (
            SourceMetadata(source, ref=ref),
            inventory,
            GateResult(
                "publication-source",
                Status.ERROR,
                "Git could not provide the requested trustworthy publication source.",
            ),
        )


def _recommendations(gates: tuple[GateResult, ...], operation: Operation) -> tuple[str, ...]:
    failed = {item.id for item in gates if item.status in {Status.FAIL, Status.ERROR}}
    recommendations: list[str] = []
    if "publication-source" in failed or "inventory" in failed:
        recommendations.append(
            "Resolve Git, inventory, symlink, shallow clone, or unreadable-file errors first."
        )
    if "redaction" in failed:
        recommendations.append(
            "Remove configured high-risk indicators from every reported publication-surface path."
        )
    if "readme-bilingual" in failed:
        recommendations.append(
            "Add exact README.md and README_ZH.md files with reciprocal top Markdown links."
        )
    if "project-type" in failed or "validation" in failed:
        recommendations.append("Provide valid project-type evidence and project metadata.")
    if "assets" in failed:
        recommendations.append(
            "Provide stable non-empty regular assets, or use --source-only "
            "for an intentional source-only release."
        )
    if not failed:
        if operation is Operation.LOCAL:
            recommendations.append(
                "Run the matching public-push, tag, or release operation before publication."
            )
        else:
            recommendations.append(
                "Review source and inventory evidence before executing publication separately."
            )
    return tuple(recommendations)


def run_check(
    project: Path | str,
    *,
    project_type: ProjectType = ProjectType.AUTO,
    operation: Operation = Operation.LOCAL,
    source: SourceKind | None = None,
    ref: str | None = None,
    assets: Iterable[Path | str] = (),
    source_only: bool = False,
    report_md: Path | str | None = None,
    report_json: Path | str | None = None,
) -> Report:
    project_path = Path(project).expanduser().resolve(strict=False)
    selected_source = source or default_source(operation)
    resolved_md = _output_path(project_path, report_md)
    resolved_json = _output_path(project_path, report_json)
    outputs = tuple(path for path in (resolved_md, resolved_json) if path is not None)
    exists_gate = GateResult(
        "project-exists",
        Status.PASS if project_path.is_dir() else Status.FAIL,
        "Project root is an existing directory."
        if project_path.is_dir()
        else "Project must be an existing directory.",
    )
    metadata, inventory, source_gate = _source_and_inventory(
        project_path,
        operation,
        selected_source,
        ref,
        outputs,
    )
    detection = (
        detect_project(project_path)
        if project_path.is_dir()
        else detect_project(Path("/nonexistent"))
    )
    type_gate, detected_type = check_project_type(detection, project_type)
    if detected_type is ProjectType.CODEX_SKILL:
        validation_gate = check_codex_skill(project_path)
    elif detected_type is ProjectType.MACOS_APP:
        validation_gate = check_macos_app(project_path)
    else:
        validation_gate = GateResult(
            "validation",
            Status.FAIL,
            "No supported project type was selected.",
        )
    redaction_gate = scan_inventory(inventory)
    inventory_gate = GateResult(
        "inventory",
        Status.ERROR if inventory.errors else Status.PASS,
        (
            f"Inventory contains {len(inventory.errors)} blocking error(s)."
            if inventory.errors
            else f"Scanned {inventory.scanned_files} file(s) and {inventory.scanned_bytes} byte(s)."
        ),
        tuple(inventory.errors),
    )
    readme_gate = (
        check_readmes(project_path)
        if project_path.is_dir()
        else GateResult("readme-bilingual", Status.FAIL, "README files could not be checked.")
    )
    asset_gate, asset_records = check_assets(project_path, assets, operation, source_only)
    gates = (
        exists_gate,
        source_gate,
        inventory_gate,
        type_gate,
        validation_gate,
        redaction_gate,
        readme_gate,
        asset_gate,
    )
    report = Report(
        SCHEMA_VERSION,
        __version__,
        overall_status(gates),
        operation,
        project_type,
        detected_type,
        detection.evidence,
        metadata,
        inventory,
        gates,
        asset_records,
        _recommendations(gates, operation),
    )
    write_reports(report, resolved_md, resolved_json)
    return report


def check_project(
    project: Path | str,
    project_type: str | ProjectType | None = None,
    assets: Iterable[Path | str] | None = None,
    report_md: Path | str | None = None,
    report_json: Path | str | None = None,
    *,
    operation: str | Operation = Operation.LOCAL,
    source: str | SourceKind | None = None,
    ref: str | None = None,
    source_only: bool = False,
) -> dict[str, object]:
    requested = ProjectType(project_type or ProjectType.AUTO)
    selected_operation = Operation(operation)
    selected_source = SourceKind(source) if source is not None else None
    report = run_check(
        project,
        project_type=requested,
        operation=selected_operation,
        source=selected_source,
        ref=ref,
        assets=assets or (),
        source_only=source_only,
        report_md=report_md,
        report_json=report_json,
    )
    return report_dict(report)
