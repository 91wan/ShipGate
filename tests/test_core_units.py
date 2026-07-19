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

    def test_rfc1918_ipv4_ranges_are_blocked_without_public_false_positives(self):
        private_addresses = (
            ".".join(("10", "1", "2", "3")),
            ".".join(("172", "16", "0", "1")),
            ".".join(("172", "31", "255", "254")),
            ".".join(("192", "168", "1", "10")),
        )
        for index, address in enumerate(private_addresses):
            project = make_skill(self.root / f"private-ip-{index}")
            (project / "config.txt").write_text(address, encoding="utf-8")
            report = run_check(project, project_type=ProjectType.CODEX_SKILL)
            redaction = next(item for item in report.gates if item.id == "redaction")
            self.assertIn("network.private-ipv4", {item.code for item in redaction.findings})

        public_or_invalid = (
            ".".join(("9", "255", "255", "255")),
            ".".join(("172", "15", "255", "255")),
            ".".join(("172", "32", "0", "1")),
            ".".join(("192", "169", "1", "1")),
            ".".join(("999", "1", "1", "1")),
        )
        project = make_skill(self.root / "public-ip")
        (project / "config.txt").write_text("\n".join(public_or_invalid), encoding="utf-8")
        report = run_check(project, project_type=ProjectType.CODEX_SKILL)
        redaction = next(item for item in report.gates if item.id == "redaction")
        self.assertNotIn("network.private-ipv4", {item.code for item in redaction.findings})

    def test_anthropic_key_uses_its_own_rule_code(self):
        project = make_skill(self.root / "anthropic-key")
        token = "sk" + "-ant-api03-" + ("A" * 40)
        (project / "config.txt").write_text(token, encoding="utf-8")

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        redaction = next(item for item in report.gates if item.id == "redaction")
        codes = {item.code for item in redaction.findings}
        self.assertIn("secret.anthropic-key", codes)
        self.assertNotIn("secret.openai-key", codes)

    def test_synthetic_unix_path_fixture_is_allowed_in_test_source(self):
        project = make_skill(self.root / "synthetic-test-source")
        tests = project / "DemoTests"
        tests.mkdir()
        (tests / "PathTests.swift").write_text(
            'let fixture = "/Users/alice/project/input.md"\nlet bareFixture = "/home/example"\n',
            encoding="utf-8",
        )

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        redaction = next(item for item in report.gates if item.id == "redaction")
        self.assertEqual(redaction.status, Status.PASS)

    def test_synthetic_unix_path_outside_test_source_is_blocked(self):
        project = make_skill(self.root / "synthetic-production-source")
        sources = project / "Sources"
        sources.mkdir()
        (sources / "App.swift").write_text(
            'let fixture = "/Users/example/project/input.md"\n', encoding="utf-8"
        )

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        redaction = next(item for item in report.gates if item.id == "redaction")
        self.assertIn("path.private-unix", {item.code for item in redaction.findings})

    def test_bare_unix_path_and_synthetic_prefix_are_blocked(self):
        macos_private = "/" + "Users/project-owner"
        linux_private = "/" + "home/project-owner"
        symbol_suffix = "/" + "Users/project-owner-"
        synthetic_prefix = "/" + "Users/alice-real"
        cases = (
            ("bare-macos", "Sources/App.swift", f'let path = "{macos_private}"\n'),
            ("bare-linux", "Sources/App.swift", f'let path = "{linux_private}"\n'),
            ("symbol-suffix", "Sources/App.swift", f'let path = "{symbol_suffix}"\n'),
            (
                "synthetic-prefix",
                "DemoTests/PathTests.swift",
                f'let path = "{synthetic_prefix}"\n',
            ),
        )
        for name, relative_path, content in cases:
            with self.subTest(name=name):
                project = make_skill(self.root / name)
                source = project / relative_path
                source.parent.mkdir()
                source.write_text(content, encoding="utf-8")

                report = run_check(project, project_type=ProjectType.CODEX_SKILL)

                redaction = next(item for item in report.gates if item.id == "redaction")
                self.assertIn("path.private-unix", {item.code for item in redaction.findings})

    def test_non_synthetic_unix_path_in_test_source_is_blocked(self):
        project = make_skill(self.root / "private-test-source")
        tests = project / "DemoTests"
        tests.mkdir()
        private_path = "/" + "Users/project-owner/project/input.md"
        (tests / "PathTests.swift").write_text(
            f'let fixture = "{private_path}"\n', encoding="utf-8"
        )

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        redaction = next(item for item in report.gates if item.id == "redaction")
        self.assertIn("path.private-unix", {item.code for item in redaction.findings})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support required")
    def test_special_file_is_inventory_error(self):
        project = make_skill(self.root / "fifo")
        os.mkfifo(project / "pipe")

        report = run_check(project, project_type=ProjectType.CODEX_SKILL)

        self.assertEqual(report.status, Status.FAIL)
        self.assertIn("inventory.special-file", {item.code for item in report.inventory.errors})


if __name__ == "__main__":
    unittest.main()
