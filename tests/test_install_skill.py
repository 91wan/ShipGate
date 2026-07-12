from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT

INSTALLER = ROOT / "scripts" / "install_skill.py"


class InstallSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["HOME"] = str(self.home)
        self.env.pop("CODEX_HOME", None)

    def tearDown(self):
        self.temp.cleanup()

    def run_installer(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), *args],
            cwd=ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_user_scope_dry_run_and_install(self):
        target = self.home / ".agents" / "skills" / "shipgate"
        dry = self.run_installer("--scope", "user", "--dry-run")
        self.assertEqual(dry.returncode, 0)
        self.assertFalse(target.exists())

        installed = self.run_installer("--scope", "user")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertTrue((target / "SKILL.md").is_file())
        self.assertTrue((target / "shipgate" / "engine.py").is_file())
        self.assertFalse((self.home / "Skills").exists())

        version = subprocess.run(
            [sys.executable, str(target / "scripts" / "shipgate.py"), "--version"],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(version.returncode, 0)

    def test_repo_and_codex_home_scopes(self):
        repo = self.root / "repo"
        repo.mkdir()
        result = self.run_installer("--scope", "repo", "--repo", str(repo))
        self.assertEqual(result.returncode, 0)
        self.assertTrue((repo / ".agents" / "skills" / "shipgate" / "SKILL.md").is_file())

        self.env["CODEX_HOME"] = str(self.root / "codex")
        result = self.run_installer("--scope", "codex-home")
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.root / "codex" / "skills" / "shipgate" / "SKILL.md").is_file())

    def test_custom_scope_rejects_dangerous_targets(self):
        for target in (".", "/", str(self.home), str(self.root / "wrong-name")):
            with self.subTest(target=target):
                result = self.run_installer("--scope", "custom", "--target", target)
                self.assertEqual(result.returncode, 2)

    def test_unknown_existing_target_requires_force(self):
        target = self.root / "custom" / "shipgate"
        target.mkdir(parents=True)
        (target / "unrelated.txt").write_text("keep", encoding="utf-8")

        refused = self.run_installer("--scope", "custom", "--target", str(target))
        self.assertEqual(refused.returncode, 2)
        self.assertTrue((target / "unrelated.txt").exists())

        replaced = self.run_installer("--scope", "custom", "--target", str(target), "--force")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertFalse((target / "unrelated.txt").exists())
        self.assertTrue((target / "SKILL.md").is_file())

    def test_symlink_target_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        target = link / "shipgate"

        result = self.run_installer("--scope", "custom", "--target", str(target))

        self.assertEqual(result.returncode, 2)
        self.assertFalse((real / "shipgate").exists())


if __name__ == "__main__":
    unittest.main()
