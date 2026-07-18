from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from shipgate.engine import run_check
from shipgate.model import Operation, ProjectType, SourceKind
from shipgate.reporting import render_json, render_markdown
from tests.helpers import git, github_token, init_git, make_skill


class PublicationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def redaction_codes(self, report) -> set[str]:
        gate = next(item for item in report.gates if item.id == "redaction")
        return {item.code for item in gate.findings}

    def commit_with_identity(self, project: Path, name: str, email: str) -> None:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": name,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": name,
                "GIT_COMMITTER_EMAIL": email,
            }
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-q", "-m", "identity fixture"],
            cwd=project,
            env=env,
            check=True,
        )

    def add_tree_tag(self, project: Path, filename: str, content: bytes) -> None:
        blob = (
            subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=project,
                input=content,
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        tree = (
            subprocess.run(
                ["git", "mktree"],
                cwd=project,
                input=f"100644 blob {blob}\t{filename}\n".encode(),
                capture_output=True,
                check=True,
            )
            .stdout.decode("ascii")
            .strip()
        )
        git(project, "tag", "-a", "ghost-tree", tree, "-m", "ghost tree")

    def assert_reports_mask(self, report, secret: str) -> None:
        self.assertNotIn(secret, render_json(report))
        self.assertNotIn(secret, render_markdown(report))

    def test_commit_message_and_identity_name_are_scanned_but_email_is_allowed(self):
        project = make_skill(self.root / "commit-metadata")
        init_git(project)
        token = github_token()
        git(project, "commit", "--allow-empty", "-q", "-m", "synthetic " + token)

        message_report = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(message_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(message_report))
        self.assert_reports_mask(message_report, token)

        clean_project = make_skill(self.root / "identity-metadata")
        init_git(clean_project)
        self.commit_with_identity(clean_project, "Release Reviewer", "reviewer@example.invalid")
        clean_report = run_check(
            clean_project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(clean_report.status.value, "pass")

        self.commit_with_identity(clean_project, "Reviewer " + token, "reviewer@example.invalid")
        identity_report = run_check(
            clean_project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(identity_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(identity_report))
        self.assert_reports_mask(identity_report, token)

        runner_path = "/home/" + "runner/work/project"
        git(clean_project, "commit", "--allow-empty", "-q", "-m", runner_path)
        runner_report = run_check(
            clean_project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(runner_report.status.value, "fail")
        self.assertIn("path.private-unix", self.redaction_codes(runner_report))
        self.assert_reports_mask(runner_report, runner_path)

    def test_annotated_tag_message_and_ref_name_are_scanned_and_masked(self):
        project = make_skill(self.root / "tag-metadata")
        init_git(project)
        token = github_token()
        git(project, "tag", "-a", "v1.0.0", "-m", "synthetic " + token)

        message_report = run_check(
            project,
            operation=Operation.TAG,
            source=SourceKind.GIT_REF,
            ref="v1.0.0",
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(message_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(message_report))
        self.assert_reports_mask(message_report, token)

        secret_ref = "release-" + token
        git(project, "tag", secret_ref)
        ref_report = run_check(
            project,
            operation=Operation.TAG,
            source=SourceKind.GIT_REF,
            ref=secret_ref,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(ref_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(ref_report))
        self.assert_reports_mask(ref_report, token)

    def test_working_index_and_renamed_history_paths_are_scanned(self):
        token = github_token()
        project = make_skill(self.root / "path-surfaces")
        secret_path = project / f"artifact-{token}.txt"
        secret_path.write_text("safe content\n", encoding="utf-8")

        working_report = run_check(project, project_type=ProjectType.CODEX_SKILL)
        self.assertEqual(working_report.status.value, "fail")
        self.assert_reports_mask(working_report, token)

        init_git(project)
        index_report = run_check(
            project,
            source=SourceKind.INDEX,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(index_report.status.value, "fail")
        self.assert_reports_mask(index_report, token)

        git(project, "mv", secret_path.name, "artifact-clean.txt")
        git(project, "commit", "-q", "-m", "rename fixture")
        history_report = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )
        self.assertEqual(history_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(history_report))
        self.assert_reports_mask(history_report, token)

    def test_non_commit_tree_ref_paths_are_scanned_and_masked(self):
        token = github_token()
        project = make_skill(self.root / "ghost-tree")
        init_git(project)
        self.add_tree_tag(project, f"ghost-{token}.txt", b"safe content\n")

        report = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(report))
        self.assert_reports_mask(report, token)

    def test_content_finding_never_echoes_a_secret_bearing_path(self):
        token = github_token()
        project = make_skill(self.root / "ghost-content")
        init_git(project)
        self.add_tree_tag(
            project,
            f"ghost-{token}.txt",
            ("value=" + token + "\n").encode(),
        )

        report = run_check(
            project,
            operation=Operation.PUBLIC_PUSH,
            project_type=ProjectType.CODEX_SKILL,
        )

        self.assertEqual(report.status.value, "fail")
        self.assert_reports_mask(report, token)

        special_project = make_skill(self.root / "special-path")
        special_path = special_project / f"pipe-{token}"
        os.mkfifo(special_path)
        try:
            special_report = run_check(
                special_project,
                project_type=ProjectType.CODEX_SKILL,
            )
        finally:
            special_path.unlink()
        self.assertEqual(special_report.status.value, "fail")
        self.assertIn("secret.github-token", self.redaction_codes(special_report))
        self.assert_reports_mask(special_report, token)


if __name__ == "__main__":
    unittest.main()
