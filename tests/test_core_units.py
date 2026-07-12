from __future__ import annotations

import codecs
import os
import tempfile
import unittest
from pathlib import Path

from shipgate.checks.assets import check_assets
from shipgate.checks.project import (
    check_codex_skill,
    check_project_type,
    detect_project,
    parse_frontmatter,
)
from shipgate.checks.readme import check_readmes
from shipgate.engine import run_check
from shipgate.model import (
    GateResult,
    Operation,
    ProjectType,
    Severity,
    Status,
    overall_status,
    sanitize_text,
)
from tests.helpers import make_skill


class CoreUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_overall_policy_and_sanitization(self):
        self.assertEqual(overall_status([GateResult("pass", Status.PASS, "ok")]), Status.PASS)
        self.assertEqual(overall_status([GateResult("fail", Status.FAIL, "no")]), Status.FAIL)
        self.assertEqual(overall_status([GateResult("error", Status.ERROR, "no")]), Status.FAIL)
        self.assertEqual(sanitize_text("a\x00b\x7f"), "a?b?")
        self.assertEqual(Severity.ERROR.value, "error")

    def test_frontmatter_supported_and_rejected_forms(self):
        project = make_skill(self.root / "frontmatter")
        skill = project / "SKILL.md"
        skill.write_bytes(
            codecs.BOM_UTF8 + b"---\r\nname: demo-skill\r\ndescription: 'Good value'\r\n---\r\n"
        )
        values, finding = parse_frontmatter(skill)
        self.assertIsNone(finding)
        self.assertEqual(values, {"name": "demo-skill", "description": "Good value"})

        invalid_values = (
            "name: Bad_Name\ndescription: okay",
            "name: demo\nname: again\ndescription: okay",
            "name: demo\ndescription: # no value",
            "name: demo\ndescription: |\n  multiline",
            "name: demo\ndescription: null",
            "name: demo\nextra: value\ndescription: okay",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                skill.write_text(f"---\n{value}\n---\n", encoding="utf-8")
                parsed, error = parse_frontmatter(skill)
                self.assertIsNone(parsed)
                self.assertIsNotNone(error)

        skill.write_text("---\nname: demo\ndescription: okay\n---oops\n", encoding="utf-8")
        self.assertIsNotNone(parse_frontmatter(skill)[1])

    def test_openai_interface_metadata_contract(self):
        project = make_skill(self.root / "metadata")
        agents = project / "agents"
        agents.mkdir()
        (agents / "openai.yaml").write_text(
            'interface:\n  display_name: "Demo"\n', encoding="utf-8"
        )
        self.assertEqual(check_codex_skill(project).status, Status.FAIL)
        (agents / "openai.yaml").write_text(
            "interface:\n"
            '  display_name: "Demo"\n'
            '  short_description: "Short"\n'
            '  default_prompt: "Use demo."\n',
            encoding="utf-8",
        )
        self.assertEqual(check_codex_skill(project).status, Status.PASS)
        (agents / "openai.yaml").write_text(
            "interface:\n"
            '  display_name: "Demo"\n'
            '  display_name: "Again"\n'
            '  short_description: "Short"\n'
            '  default_prompt: "Use demo."\n',
            encoding="utf-8",
        )
        self.assertEqual(check_codex_skill(project).status, Status.FAIL)

    def test_readme_requires_exact_top_local_links(self):
        project = make_skill(self.root / "readme")
        (project / "README.md").write_text(
            "# Demo\n\n"
            + "\n".join(f"line {index}" for index in range(11))
            + "\n[中文](README_ZH.md)\n",
            encoding="utf-8",
        )
        self.assertEqual(check_readmes(project).status, Status.FAIL)

        bad_targets = (
            "https://example.invalid/README_ZH.md",
            "../README_ZH.md",
            "README_ZH.md#top",
        )
        for target in bad_targets:
            with self.subTest(target=target):
                (project / "README.md").write_text(
                    f"# Demo\n\n[中文]({target})\n", encoding="utf-8"
                )
                self.assertEqual(check_readmes(project).status, Status.FAIL)

    def test_project_detection_requires_evidence_and_rejects_ambiguity(self):
        project = make_skill(self.root / "ambiguous")
        pbx = project / "Demo.xcodeproj" / "project.pbxproj"
        pbx.parent.mkdir()
        pbx.write_text("SUPPORTED_PLATFORMS = macosx;\n", encoding="utf-8")
        detection = detect_project(project)
        self.assertEqual(
            set(detection.candidates), {ProjectType.CODEX_SKILL, ProjectType.MACOS_APP}
        )
        gate, selected = check_project_type(detection, ProjectType.AUTO)
        self.assertEqual(gate.status, Status.FAIL)
        self.assertIsNone(selected)
        gate, selected = check_project_type(detection, ProjectType.CODEX_SKILL)
        self.assertEqual(gate.status, Status.PASS)
        self.assertEqual(selected, ProjectType.CODEX_SKILL)

    def test_swiftpm_and_workspace_macos_evidence(self):
        swift = self.root / "swift"
        swift.mkdir()
        (swift / "Package.swift").write_text(
            'let package = Package(name: "Demo", platforms: [.macOS(.v13)])\n',
            encoding="utf-8",
        )
        self.assertEqual(detect_project(swift).candidates, (ProjectType.MACOS_APP,))

        workspace = self.root / "workspace"
        data = workspace / "Demo.xcworkspace" / "contents.xcworkspacedata"
        data.parent.mkdir(parents=True)
        data.write_text(
            '<Workspace><FileRef location="group:Demo.xcodeproj"/></Workspace>',
            encoding="utf-8",
        )
        pbx = workspace / "Demo.xcodeproj" / "project.pbxproj"
        pbx.parent.mkdir()
        pbx.write_text("MACOSX_DEPLOYMENT_TARGET = 13.0;\n", encoding="utf-8")
        self.assertEqual(detect_project(workspace).candidates, (ProjectType.MACOS_APP,))

        commented = self.root / "commented-xcode"
        pbx = commented / "Demo.xcodeproj" / "project.pbxproj"
        pbx.parent.mkdir(parents=True)
        pbx.write_text("// SDKROOT = macosx;\nSDKROOT = iphoneos;\n", encoding="utf-8")
        self.assertEqual(detect_project(commented).candidates, ())

    def test_asset_operation_semantics_and_deduplication(self):
        project = make_skill(self.root / "assets")
        local_gate, records = check_assets(project, (), Operation.LOCAL, False)
        self.assertEqual(local_gate.status, Status.NOT_APPLICABLE)
        self.assertEqual(records, ())
        release_gate, _ = check_assets(project, (), Operation.RELEASE, False)
        self.assertEqual(release_gate.status, Status.FAIL)
        source_gate, _ = check_assets(project, (), Operation.RELEASE, True)
        self.assertEqual(source_gate.status, Status.NOT_APPLICABLE)

        asset = project / "asset.zip"
        asset.write_bytes(b"asset")
        gate, records = check_assets(project, (asset, "asset.zip"), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.PASS)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].sha256 or ""), 64)

        external = self.root / "external.zip"
        external.write_bytes(b"outside")
        gate, records = check_assets(project, (external,), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.FAIL)
        self.assertEqual(records[0].path, "external/external.zip")

        link = project / "asset-link.zip"
        link.symlink_to(asset)
        gate, _ = check_assets(project, (asset, link), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.FAIL)

    def test_binary_and_utf16be_indicators_are_scanned(self):
        project = make_skill(self.root / "encodings")
        private_header = "-----BEGIN " + "PRIVATE KEY-----"
        (project / "binary.bin").write_bytes(b"\x00\x01" + private_header.encode("ascii"))
        windows_path = "C:" + "\\Users\\" + "bob\\private\\"
        (project / "utf16be.txt").write_text(windows_path, encoding="utf-16-be")
        data = (project / "utf16be.txt").read_bytes()
        (project / "utf16be.txt").write_bytes(codecs.BOM_UTF16_BE + data)

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        redaction = next(item for item in report.gates if item.id == "redaction")
        codes = {item.code for item in redaction.findings}
        self.assertIn("secret.private-key", codes)
        self.assertIn("path.private-windows", codes)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support required")
    def test_special_file_is_inventory_error(self):
        project = make_skill(self.root / "fifo")
        os.mkfifo(project / "pipe")

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        self.assertEqual(report.status, Status.FAIL)
        self.assertIn("inventory.special-file", {item.code for item in report.inventory.errors})


if __name__ == "__main__":
    unittest.main()
