from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from shipgate.engine import run_check
from shipgate.model import Operation, ProjectType, SourceKind
from tests.helpers import git, github_token, init_git, make_skill


class GitSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_public_push_scans_deleted_secret_in_history(self):
        project = make_skill(self.root / "history")
        init_git(project)
        secret = project / "old.txt"
        secret.write_text(github_token(), encoding="utf-8")
        git(project, "add", "old.txt")
        git(project, "commit", "-q", "-m", "add old value")
        secret.unlink()
        git(project, "add", "-u")
        git(project, "commit", "-q", "-m", "remove old value")

        report = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(report.status.value, "fail")
        redaction = next(item for item in report.gates if item.id == "redaction")
        self.assertIn("secret.github-token", {item.code for item in redaction.findings})
        self.assertEqual(report.source.commit, git(project, "rev-parse", "HEAD"))

    def test_index_and_working_tree_are_distinct(self):
        project = make_skill(self.root / "index")
        note = project / "note.txt"
        note.write_text("safe\n", encoding="utf-8")
        init_git(project)
        note.write_text(github_token(), encoding="utf-8")
        git(project, "add", "note.txt")
        note.write_text("safe working tree\n", encoding="utf-8")

        index_report = run_check(
            project,
            operation=Operation.LOCAL,
            source=SourceKind.INDEX,
            project_type=ProjectType.CODEX_SKILL,
        )
        working_report = run_check(
            project,
            operation=Operation.LOCAL,
            source=SourceKind.WORKING_TREE,
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(index_report.status.value, "fail")
        self.assertEqual(working_report.status.value, "pass")

    def test_ignored_env_is_outside_surface_but_tracked_env_fails(self):
        project = make_skill(self.root / "ignored")
        (project / ".gitignore").write_text(".env\n", encoding="utf-8")
        init_git(project)
        env_file = project / ".env"
        env_file.write_text("LOCAL=value\n", encoding="utf-8")

        ignored = run_check(project, project_type=ProjectType.CODEX_SKILL)
        self.assertEqual(ignored.status.value, "pass")

        git(project, "add", "-f", ".env")
        tracked = run_check(project, project_type=ProjectType.CODEX_SKILL)
        self.assertEqual(tracked.status.value, "fail")

    def test_dirty_release_and_unverified_gitlink_fail(self):
        project = make_skill(self.root / "dirty")
        init_git(project)
        (project / "README.md").write_text(
            "# Changed\n\nEnglish | [中文](README_ZH.md)\n", encoding="utf-8"
        )
        dirty = run_check(
            project,
            operation=Operation.RELEASE,
            project_type=ProjectType.CODEX_SKILL,
            source_only=True,
        )
        source_gate = next(item for item in dirty.gates if item.id == "publication-source")
        self.assertEqual(source_gate.status.value, "fail")
        self.assertIn("git.dirty-worktree", {item.code for item in source_gate.findings})

        git(project, "restore", "README.md")
        head = git(project, "rev-parse", "HEAD")
        git(project, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor")
        linked = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )
        source_gate = next(item for item in linked.gates if item.id == "publication-source")
        self.assertIn("git.unverified-submodule", {item.code for item in source_gate.findings})

    def test_shallow_repository_blocks_public_operation(self):
        origin = make_skill(self.root / "origin")
        init_git(origin)
        (origin / "note.txt").write_text("second\n", encoding="utf-8")
        git(origin, "add", "note.txt")
        git(origin, "commit", "-q", "-m", "second")
        clone = self.root / "clone"
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)],
            check=True,
        )

        report = run_check(
            clone,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )

        source_gate = next(item for item in report.gates if item.id == "publication-source")
        self.assertIn("git.shallow-repository", {item.code for item in source_gate.findings})

    def test_explicit_ref_binds_report_to_commit(self):
        project = make_skill(self.root / "ref")
        init_git(project)
        commit = git(project, "rev-parse", "HEAD")

        report = run_check(
            project,
            operation=Operation.TAG,
            source=SourceKind.GIT_REF,
            ref="HEAD",
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(report.status.value, "pass")
        self.assertEqual(report.source.commit, commit)


if __name__ == "__main__":
    unittest.main()
