import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "shipgate.py"


def load_shipgate():
    spec = importlib.util.spec_from_file_location("shipgate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ShipGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_readme(self, project, english=True, chinese=True):
        sections = ["# Example\n"]
        if english:
            sections.append(
                "## English\n"
                "Install with make install. Use this project to publish releases. "
                "It supports validation, GitHub release flow, and asset checks.\n"
            )
        if chinese:
            sections.append(
                "## 中文\n"
                "使用 make install 安装。本项目用于公开发布流程，"
                "支持验证、GitHub 发布流程和资产检查。\n"
            )
        (project / "README.md").write_text("\n".join(sections), encoding="utf-8")

    def write_split_readmes(self, project, english=True, chinese=True):
        if english:
            (project / "README.md").write_text(
                "# Example\n\n"
                "English | [中文](README_ZH.md)\n\n"
                "## Install\n\n"
                "Install with make install-local.\n\n"
                "## Usage\n\n"
                "Use this project before creating a public release.\n\n"
                "## Supported project types\n\n"
                "Codex skill and macOS app projects are supported.\n\n"
                "## Validation gates\n\n"
                "Run validation before GitHub release steps.\n\n"
                "## Asset verification\n\n"
                "Pass every release asset for checksum reporting.\n",
                encoding="utf-8",
            )
        if chinese:
            (project / "README_ZH.md").write_text(
                "# 示例\n\n"
                "[English](README.md) | 中文\n\n"
                "## 安装\n\n"
                "使用 make install-local 安装。\n\n"
                "## 使用\n\n"
                "在公开发布项目之前运行本工具。\n\n"
                "## 支持的项目类型\n\n"
                "支持 Codex skill 和 macOS app 项目。\n\n"
                "## 验证门禁\n\n"
                "在 GitHub 发布流程前运行验证。\n\n"
                "## 资产检查\n\n"
                "传入每个发布资产以生成校验报告。\n",
                encoding="utf-8",
            )

    def make_skill_project(self):
        project = self.root / "skill"
        project.mkdir()
        (project / "SKILL.md").write_text(
            "---\n"
            "name: demo-skill\n"
            "description: Use when testing ShipGate skill detection.\n"
            "---\n"
            "\n"
            "# Demo Skill\n",
            encoding="utf-8",
        )
        self.write_split_readmes(project)
        return project

    def make_macos_project(self):
        project = self.root / "macapp"
        xcodeproj = project / "DemoApp.xcodeproj"
        xcodeproj.mkdir(parents=True)
        (xcodeproj / "project.pbxproj").write_text(
            "// !$*UTF8*$!\nSDKROOT = macosx;\n", encoding="utf-8"
        )
        self.write_split_readmes(project)
        return project

    def test_detects_codex_skill_project(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()

        self.assertEqual(shipgate.detect_project_type(project), "codex-skill")

    def test_detects_macos_app_project(self):
        shipgate = load_shipgate()
        project = self.make_macos_project()

        self.assertEqual(shipgate.detect_project_type(project), "macos-app")

    def test_single_file_bilingual_readme_fails_with_migration_guidance(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        (project / "README_ZH.md").unlink()
        self.write_readme(project, english=True, chinese=True)

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "fail")
        bilingual_gate = next(g for g in report["gates"] if g["name"] == "readme-bilingual")
        self.assertEqual(bilingual_gate["status"], "fail")
        self.assertIn(
            "README must use split language pages for public GitHub release",
            bilingual_gate["detail"],
        )

    def test_split_readme_pages_pass(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        (project / "README.md").unlink()
        self.write_split_readmes(project)

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "pass")
        bilingual_gate = next(g for g in report["gates"] if g["name"] == "readme-bilingual")
        self.assertEqual(bilingual_gate["status"], "pass")
        self.assertIn("split language pages", bilingual_gate["detail"])

    def test_split_readme_fails_when_chinese_page_is_missing(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        (project / "README.md").unlink()
        missing_page = project / "README_ZH.md"
        if missing_page.exists():
            missing_page.unlink()
        self.write_split_readmes(project, english=True, chinese=False)

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "fail")
        bilingual_gate = next(g for g in report["gates"] if g["name"] == "readme-bilingual")
        self.assertEqual(bilingual_gate["status"], "fail")
        codes = {item["code"] for item in bilingual_gate["findings"]}
        self.assertIn("readme.missing-chinese", codes)

    def test_split_readme_fails_when_chinese_page_lacks_backlink(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        (project / "README_ZH.md").write_text(
            "# 示例\n\n"
            "中文\n\n"
            "## 安装\n\n"
            "使用 make install-local 安装。\n\n"
            "## 使用\n\n"
            "在公开发布项目之前运行本工具。\n\n"
            "## 支持的项目类型\n\n"
            "支持 Codex skill 和 macOS app 项目。\n\n"
            "## 验证门禁\n\n"
            "在 GitHub 发布流程前运行验证。\n\n"
            "## 资产检查\n\n"
            "传入每个发布资产以生成校验报告。\n",
            encoding="utf-8",
        )

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "fail")
        bilingual_gate = next(g for g in report["gates"] if g["name"] == "readme-bilingual")
        self.assertEqual(bilingual_gate["status"], "fail")
        codes = {item["code"] for item in bilingual_gate["findings"]}
        self.assertIn("readme.missing-english-link", codes)

    def test_readme_content_keywords_are_not_a_structural_gate(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        (project / "README.md").write_text(
            "# Example\n\nEnglish | [中文](README_ZH.md)\n\nShort note only.\n",
            encoding="utf-8",
        )
        self.write_split_readmes(project, english=False, chinese=True)

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "pass")
        bilingual_gate = next(g for g in report["gates"] if g["name"] == "readme-bilingual")
        self.assertEqual(bilingual_gate["status"], "pass")

    def test_redaction_fails_on_private_path_token_env_and_private_key(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        private_path = "/" + "Users/example/private"
        token = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
        private_key_start = "-----BEGIN " + "PRIVATE KEY-----"
        private_key_end = "-----END " + "PRIVATE KEY-----"
        (project / "notes.md").write_text(
            f"Local path: {private_path}\n"
            f"GitHub token: {token}\n"
            f"Private key:\n{private_key_start}\nabc\n{private_key_end}\n",
            encoding="utf-8",
        )
        (project / ".env").write_text("SECRET=value\n", encoding="utf-8")

        report = shipgate.check_project(project, project_type="codex-skill")

        self.assertEqual(report["status"], "fail")
        redaction_gate = next(g for g in report["gates"] if g["name"] == "redaction")
        self.assertEqual(redaction_gate["status"], "fail")
        codes = {item["code"] for item in redaction_gate["findings"]}
        self.assertIn("secret.env-file", codes)
        self.assertIn("path.private-unix", codes)
        self.assertIn("secret.github-token", codes)
        self.assertIn("secret.private-key", codes)

    def test_asset_missing_and_empty_fail(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        empty_asset = project / "empty.zip"
        empty_asset.write_bytes(b"")
        missing_asset = project / "missing.zip"

        report = shipgate.check_project(
            project,
            project_type="codex-skill",
            assets=[empty_asset, missing_asset],
        )

        self.assertEqual(report["status"], "fail")
        assets_gate = next(g for g in report["gates"] if g["name"] == "assets")
        self.assertEqual(assets_gate["status"], "fail")

    def test_asset_checksum_and_reports_are_written(self):
        shipgate = load_shipgate()
        project = self.make_skill_project()
        asset = project / "release.zip"
        asset.write_bytes(b"shipgate")
        report_md = project / "build" / "shipgate.md"
        report_json = project / "build" / "shipgate.json"

        report = shipgate.check_project(
            project,
            project_type="codex-skill",
            assets=[asset],
            report_md=report_md,
            report_json=report_json,
        )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["assets"][0]["size"], len(b"shipgate"))
        self.assertEqual(
            report["assets"][0]["sha256"],
            "d307355a7ccb98212437d6d7d4746a2dd7bced957a1fcea191b3c6064d86a722",
        )
        self.assertTrue(report_md.exists())
        self.assertTrue(report_json.exists())
        loaded = json.loads(report_json.read_text(encoding="utf-8"))
        self.assertEqual(loaded["status"], "pass")
        self.assertIn("ShipGate Report", report_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
