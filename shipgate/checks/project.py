"""Project type evidence and strict Codex skill metadata checks."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..model import (
    Evidence,
    Finding,
    GateResult,
    ProjectDetection,
    ProjectType,
    Severity,
    Status,
)

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PBX_MACOS_PATTERN = re.compile(
    r"(?:SDKROOT\s*=\s*macosx|SUPPORTED_PLATFORMS\s*=\s*[^;]*\bmacosx\b|MACOSX_DEPLOYMENT_TARGET\s*=)"
)


def _decode_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def parse_frontmatter(path: Path) -> tuple[dict[str, str] | None, Finding | None]:
    try:
        text = _decode_utf8(path)
    except (OSError, UnicodeError):
        return None, Finding(
            "skill.frontmatter-unreadable",
            Severity.ERROR,
            "SKILL.md",
            "SKILL.md must be readable as UTF-8 or UTF-8 with BOM.",
        )
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, Finding(
            "skill.frontmatter-opening",
            Severity.ERROR,
            "SKILL.md",
            "SKILL.md must start with an exact --- delimiter.",
        )
    try:
        closing = lines.index("---", 1)
    except ValueError:
        return None, Finding(
            "skill.frontmatter-closing",
            Severity.ERROR,
            "SKILL.md",
            "SKILL.md frontmatter must use an exact closing --- delimiter.",
        )
    values: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip() or ":" not in line:
            return None, Finding(
                "skill.frontmatter-unsupported",
                Severity.ERROR,
                "SKILL.md",
                "Frontmatter supports only non-empty single-line name and description scalars.",
            )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key not in {"name", "description"} or key in values:
            return None, Finding(
                "skill.frontmatter-keys",
                Severity.ERROR,
                "SKILL.md",
                "Frontmatter must contain name and description exactly once.",
            )
        if raw_value.startswith(("|", ">", "[", "{")):
            return None, Finding(
                "skill.frontmatter-unsupported",
                Severity.ERROR,
                "SKILL.md",
                "Multiline and complex YAML values are not supported.",
            )
        if raw_value.startswith(('"', "'")):
            quote = raw_value[0]
            if len(raw_value) < 2 or raw_value[-1] != quote:
                return None, Finding(
                    "skill.frontmatter-unsupported",
                    Severity.ERROR,
                    "SKILL.md",
                    "Quoted values must close on the same line.",
                )
            value = raw_value[1:-1].strip()
        else:
            value = raw_value.split(" #", 1)[0].strip()
            if value.startswith("#"):
                value = ""
        if not value or value in {"null", "~"}:
            return None, Finding(
                "skill.frontmatter-empty",
                Severity.ERROR,
                "SKILL.md",
                f"Frontmatter {key} must be a non-empty scalar.",
            )
        values[key] = value
    if set(values) != {"name", "description"}:
        return None, Finding(
            "skill.frontmatter-required",
            Severity.ERROR,
            "SKILL.md",
            "Frontmatter must contain name and description exactly once.",
        )
    if not NAME_PATTERN.fullmatch(values["name"]):
        return None, Finding(
            "skill.invalid-name",
            Severity.ERROR,
            "SKILL.md",
            "Skill name must use lowercase hyphen-case.",
        )
    return values, None


def _parse_openai_yaml(path: Path) -> Finding | None:
    if not path.exists():
        return None
    try:
        lines = _decode_utf8(path).splitlines()
    except (OSError, UnicodeError):
        return Finding(
            "skill.openai-yaml-unreadable",
            Severity.ERROR,
            "agents/openai.yaml",
            "agents/openai.yaml could not be read as UTF-8.",
        )
    in_interface = False
    found_interface = False
    values: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_interface = line.strip() == "interface:"
            if in_interface:
                if found_interface:
                    return Finding(
                        "skill.openai-yaml-interface",
                        Severity.ERROR,
                        "agents/openai.yaml",
                        "Interface metadata must contain one interface mapping.",
                    )
                found_interface = True
            continue
        if in_interface and line.startswith("  ") and not line.startswith("   ") and ":" in line:
            key, value = line.strip().split(":", 1)
            value = value.strip().strip('"').strip("'")
            if key in {"display_name", "short_description", "default_prompt"}:
                if key in values:
                    return Finding(
                        "skill.openai-yaml-interface",
                        Severity.ERROR,
                        "agents/openai.yaml",
                        "Interface metadata keys must not be duplicated.",
                    )
                values[key] = value
        elif in_interface:
            return Finding(
                "skill.openai-yaml-interface",
                Severity.ERROR,
                "agents/openai.yaml",
                "Interface metadata uses an unsupported YAML form.",
            )
    required = {"display_name", "short_description", "default_prompt"}
    if set(values) != required or any(not value for value in values.values()):
        return Finding(
            "skill.openai-yaml-interface",
            Severity.ERROR,
            "agents/openai.yaml",
            "Interface metadata must define non-empty display_name, "
            "short_description, and default_prompt.",
        )
    return None


def check_codex_skill(project: Path) -> GateResult:
    skill = project / "SKILL.md"
    if not skill.is_file():
        return GateResult(
            "validation",
            Status.FAIL,
            "SKILL.md is required for Codex skill projects.",
        )
    _, error = parse_frontmatter(skill)
    openai_error = _parse_openai_yaml(project / "agents" / "openai.yaml")
    findings = tuple(item for item in (error, openai_error) if item is not None)
    if findings:
        status = (
            Status.ERROR if any("unreadable" in item.code for item in findings) else Status.FAIL
        )
        return GateResult("validation", status, "Codex skill metadata is invalid.", findings)
    return GateResult(
        "validation",
        Status.PASS,
        "Codex skill metadata satisfies the strict supported contract.",
    )


def _xcode_evidence(project: Path) -> list[Evidence]:
    evidence: list[Evidence] = []
    project_files: set[Path] = set(project.glob("*.xcodeproj/project.pbxproj"))
    for workspace in project.glob("*.xcworkspace"):
        data = workspace / "contents.xcworkspacedata"
        try:
            tree = ET.parse(data)
        except (OSError, ET.ParseError):
            continue
        for node in tree.iter("FileRef"):
            location = node.attrib.get("location", "")
            if location.startswith("group:") and location.endswith(".xcodeproj"):
                project_files.add(project / location.removeprefix("group:") / "project.pbxproj")
    for pbxproj in sorted(project_files):
        try:
            text = _decode_utf8(pbxproj)
        except (OSError, UnicodeError):
            continue
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
        text = re.sub(r"//[^\n]*", " ", text)
        if PBX_MACOS_PATTERN.search(text):
            evidence.append(
                Evidence(
                    "project.macos-xcode",
                    pbxproj.relative_to(project).as_posix(),
                    "Xcode project declares macOS platform settings.",
                    ProjectType.MACOS_APP,
                )
            )
    return evidence


def _strip_swift_comments_and_strings(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def _swiftpm_evidence(project: Path) -> list[Evidence]:
    package = project / "Package.swift"
    if not package.is_file():
        return []
    try:
        text = _strip_swift_comments_and_strings(_decode_utf8(package))
    except (OSError, UnicodeError):
        return []
    if re.search(r"platforms\s*:\s*\[[^\]]*\.macOS\s*\(", text, re.DOTALL):
        return [
            Evidence(
                "project.macos-swiftpm",
                "Package.swift",
                "Swift package declares macOS in its platforms argument.",
                ProjectType.MACOS_APP,
            )
        ]
    return []


def detect_project(project: Path) -> ProjectDetection:
    evidence: list[Evidence] = []
    skill = project / "SKILL.md"
    if skill.is_file():
        metadata, error = parse_frontmatter(skill)
        if metadata is not None and error is None:
            evidence.append(
                Evidence(
                    "project.codex-skill",
                    "SKILL.md",
                    "SKILL.md has valid required frontmatter.",
                    ProjectType.CODEX_SKILL,
                )
            )
    evidence.extend(_xcode_evidence(project))
    evidence.extend(_swiftpm_evidence(project))
    candidates = tuple(
        sorted({item.project_type for item in evidence}, key=lambda item: item.value)
    )
    return ProjectDetection(candidates, tuple(evidence))


def check_project_type(
    detection: ProjectDetection,
    requested: ProjectType,
) -> tuple[GateResult, ProjectType | None]:
    if requested is ProjectType.AUTO:
        if len(detection.candidates) == 1:
            selected = detection.candidates[0]
            return GateResult(
                "project-type",
                Status.PASS,
                f"Detected one supported project type: {selected.value}.",
            ), selected
        code = "project.ambiguous-type" if detection.candidates else "project.unknown-type"
        detail = (
            "Multiple project types have valid evidence; pass --project-type explicitly."
            if detection.candidates
            else "No supported project type has sufficient evidence."
        )
        finding = Finding(code, Severity.ERROR, ".", detail)
        return GateResult("project-type", Status.FAIL, detail, (finding,)), None
    if requested not in detection.candidates:
        finding = Finding(
            "project.type-unconfirmed",
            Severity.ERROR,
            ".",
            f"Requested project type {requested.value} lacks sufficient evidence.",
        )
        return GateResult(
            "project-type",
            Status.FAIL,
            finding.message,
            (finding,),
        ), requested
    if len(detection.candidates) > 1:
        detail = f"Selected {requested.value}; other supported project evidence is also present."
    else:
        detail = f"Project type {requested.value} is supported by structural evidence."
    return GateResult("project-type", Status.PASS, detail), requested


def check_macos_app(project: Path) -> GateResult:
    detection = detect_project(project)
    if ProjectType.MACOS_APP in detection.candidates:
        return GateResult(
            "validation",
            Status.PASS,
            "macOS platform evidence is present.",
        )
    return GateResult(
        "validation",
        Status.FAIL,
        "No verified macOS Xcode target or SwiftPM platform declaration was found.",
    )
