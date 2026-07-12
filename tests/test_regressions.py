import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "shipgate.py"


def load_compat_module():
    spec = importlib.util.spec_from_file_location("shipgate_compat", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def github_token() -> str:
    return "gh" + "p_" + ("A" * 36)


def openai_token() -> str:
    return "sk" + "-proj-" + ("A" * 32)


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_skill(self, name: str = "project") -> Path:
        project = self.root / name
        project.mkdir()
        (project / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: Validate a demo skill.\n---\n\n# Demo\n",
            encoding="utf-8",
        )
        (project / "README.md").write_text(
            "# Demo\n\nEnglish | [中文](README_ZH.md)\n\nRelease documentation.\n",
            encoding="utf-8",
        )
        (project / "README_ZH.md").write_text(
            "# Demo\n\n[English](README.md) | 中文\n\n发布文档。\n",
            encoding="utf-8",
        )
        return project

    def check(self, project: Path, **kwargs):
        module = load_compat_module()
        return module.check_project(project, project_type="codex-skill", **kwargs)

    def redaction_gate(self, report):
        return next(item for item in report["gates"] if item["name"] == "redaction")

    def test_scans_github_workflows(self):
        project = self.make_skill()
        workflow = project / ".github" / "workflows"
        workflow.mkdir(parents=True)
        (workflow / "release.yml").write_text(github_token(), encoding="utf-8")

        report = self.check(project)

        self.assertEqual(self.redaction_gate(report)["status"], "fail")

    def test_scans_large_file_and_new_openai_key(self):
        project = self.make_skill()
        (project / "large.txt").write_text(("x" * 1_100_000) + openai_token(), encoding="utf-8")

        report = self.check(project)

        self.assertEqual(self.redaction_gate(report)["status"], "fail")

    def test_scans_token_crossing_stream_chunk_boundary(self):
        project = self.make_skill()
        token = github_token()
        prefix = token[:3]
        suffix = token[3:]
        (project / "boundary.txt").write_text(
            ("x" * ((64 * 1024) - len(prefix))) + prefix + suffix,
            encoding="utf-8",
        )

        report = self.check(project)

        self.assertEqual(self.redaction_gate(report)["status"], "fail")

    def test_scans_utf16_and_windows_private_path(self):
        project = self.make_skill()
        payload = "C:" + "\\Users\\" + "alice\\private\\file " + github_token()
        (project / "utf16.txt").write_text(payload, encoding="utf-16")

        report = self.check(project)

        self.assertEqual(self.redaction_gate(report)["status"], "fail")

    def test_report_does_not_disclose_absolute_project_path(self):
        project = self.make_skill()
        report_path = self.root / "report.json"

        self.check(project, report_json=report_path)

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn(str(project.resolve()), content)
        self.assertEqual(json.loads(content)["project"]["root"], ".")

    def test_readme_requires_real_top_links_and_exact_names(self):
        project = self.make_skill()
        (project / "README.md").write_text(
            "# Demo\n\nREADME_ZH.md is mentioned but not linked.\n", encoding="utf-8"
        )

        report = self.check(project)

        gate = next(item for item in report["gates"] if item["name"] == "readme-bilingual")
        self.assertEqual(gate["status"], "fail")

        (project / "README.md").rename(project / "README.markdown")
        report = self.check(project)
        gate = next(item for item in report["gates"] if item["name"] == "readme-bilingual")
        self.assertEqual(gate["status"], "fail")

    def test_release_requires_asset_unless_source_only(self):
        project = self.make_skill()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
        subprocess.run(["git", "config", "user.name", "ShipGate Tests"], cwd=project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.invalid"], cwd=project, check=True
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=project, check=True)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "check",
                str(project),
                "--operation",
                "release",
                "--project-type",
                "codex-skill",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertNotIn("traceback", result.stderr.lower())

    def test_ios_xcode_project_is_not_macos(self):
        module = load_compat_module()
        project = self.root / "ios"
        pbxproj = project / "Demo.xcodeproj" / "project.pbxproj"
        pbxproj.parent.mkdir(parents=True)
        pbxproj.write_text(
            "SDKROOT = iphoneos;\nSUPPORTED_PLATFORMS = iphoneos;\n",
            encoding="utf-8",
        )

        self.assertIsNone(module.detect_project_type(project))

    def test_swiftpm_comment_does_not_prove_macos(self):
        module = load_compat_module()
        project = self.root / "swift"
        project.mkdir()
        (project / "Package.swift").write_text(
            '// .macOS(.v13)\nlet package = Package(name: "Demo")\n',
            encoding="utf-8",
        )

        self.assertIsNone(module.detect_project_type(project))

    def test_frontmatter_supports_crlf_and_rejects_empty_or_bad_delimiter(self):
        module = load_compat_module()
        project = self.make_skill()
        skill = project / "SKILL.md"
        skill.write_bytes(b"---\r\nname: demo-skill\r\ndescription: Valid description.\r\n---\r\n")
        self.assertEqual(module.check_codex_skill(project)["status"], "pass")

        skill.write_text(
            '---\nname: demo-skill\ndescription: ""\n---oops\n',
            encoding="utf-8",
        )
        self.assertEqual(module.check_codex_skill(project)["status"], "fail")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_broken_and_external_symlinks_fail_without_following(self):
        project = self.make_skill()
        (project / "broken").symlink_to(project / "missing")
        external = self.root / "outside.txt"
        external.write_text(github_token(), encoding="utf-8")
        (project / "external").symlink_to(external)

        report = self.check(project)

        inventory = report["inventory"]
        self.assertGreaterEqual(len(inventory["errors"]), 2)
        self.assertEqual(report["status"], "fail")

    def test_project_file_returns_controlled_nonzero_without_traceback(self):
        project_file = self.root / "not-a-directory"
        project_file.write_text("x", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "check", str(project_file)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("traceback", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
