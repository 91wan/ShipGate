from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from shipgate import cli
from shipgate.engine import run_check
from shipgate.model import ProjectType
from shipgate.reporting import ReportWriteError, clean_text, render_json, render_markdown
from tests.helpers import ROOT, init_git, make_skill, run_cli


class CliAndReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_version_and_python_module_entrypoint(self):
        script = run_cli("--version")
        module = subprocess.run(
            [sys.executable, "-m", "shipgate", "--version"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(script.returncode, 0)
        self.assertEqual(module.returncode, 0)
        self.assertEqual(script.stdout, module.stdout)

    def test_invalid_argument_combinations_return_two(self):
        cases = (
            ("check", ".", "--source-only"),
            ("check", ".", "--source", "git-ref"),
            ("check", ".", "--ref", "HEAD"),
            ("check", ".", "--operation", "public-push", "--source", "head"),
            ("check", ".", "--operation", "release", "--source", "index"),
        )
        for args in cases:
            with self.subTest(args=args):
                result = run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)

    def test_source_only_release_passes_in_clean_git_repo(self):
        project = make_skill(self.root / "release")
        init_git(project)

        result = run_cli(
            "check",
            str(project),
            "--operation",
            "release",
            "--project-type",
            "codex-skill",
            "--source-only",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("source-only", result.stdout.lower())
        self.assertEqual(result.stderr, "")

    def test_report_is_stable_and_contains_no_absolute_project_path(self):
        project = make_skill(self.root / "stable")
        first = run_check(project, project_type=ProjectType.CODEX_SKILL)
        second = run_check(project, project_type=ProjectType.CODEX_SKILL)

        first_json = render_json(first)
        self.assertEqual(first_json, render_json(second))
        self.assertNotIn(str(project.resolve()), first_json)
        self.assertNotIn(str(Path.home()), first_json)
        self.assertEqual(json.loads(first_json)["project"]["root"], ".")
        self.assertIn("ShipGate Report", render_markdown(first))

    def test_report_output_inside_project_does_not_self_pollute(self):
        project = make_skill(self.root / "reports")
        report_md = project / "out" / "report.md"
        report_json = project / "out" / "report.json"

        report = run_check(
            project,
            project_type=ProjectType.CODEX_SKILL,
            report_md=report_md,
            report_json=report_json,
        )

        self.assertEqual(report.status.value, "pass")
        self.assertTrue(report_md.is_file())
        self.assertTrue(report_json.is_file())

    def test_unwritable_report_shape_returns_three_without_traceback(self):
        project = make_skill(self.root / "write-error")
        destination = self.root / "directory-target"
        destination.mkdir()

        result = run_cli(
            "check",
            str(project),
            "--project-type",
            "codex-skill",
            "--report-json",
            str(destination),
        )

        self.assertEqual(result.returncode, 3)
        self.assertNotIn("Traceback", result.stderr)

    def test_control_characters_are_sanitized(self):
        self.assertEqual(clean_text("a\x00b\n"), "a?b?")

    def test_cli_main_direct_success_failure_and_internal_error(self):
        project = make_skill(self.root / "direct")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(["check", str(project), "--project-type", "codex-skill"])
        self.assertEqual(code, 0)
        self.assertIn("ShipGate Report", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

        (project / "README_ZH.md").unlink()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(
                cli.main(["check", str(project), "--project-type", "codex-skill"]),
                1,
            )

        with patch("shipgate.cli.run_check", side_effect=OSError("broken")):
            stderr = StringIO()
            with redirect_stdout(StringIO()), redirect_stderr(stderr):
                self.assertEqual(cli.main(["check", str(project)]), 3)
            self.assertIn("unable to complete", stderr.getvalue())

    def test_cli_main_direct_report_write_error_and_parser_helpers(self):
        project = make_skill(self.root / "write")
        with patch(
            "shipgate.cli.run_check",
            side_effect=ReportWriteError("cannot write"),
        ):
            stderr = StringIO()
            with redirect_stdout(StringIO()), redirect_stderr(stderr):
                self.assertEqual(cli.main(["check", str(project)]), 3)
            self.assertIn("cannot write", stderr.getvalue())

        parser = cli.build_parser()
        args = parser.parse_args(["check", ".", "--operation", "release", "--source-only"])
        cli.validate_arguments(args)
        invalid = (
            ["check", ".", "--source", "git-ref"],
            ["check", ".", "--ref", "HEAD"],
            ["check", ".", "--operation", "public-push", "--source", "head"],
            ["check", ".", "--operation", "release", "--source", "index"],
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(cli.UsageError):
                    cli.validate_arguments(parser.parse_args(values))
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as raised:
            cli.main(["check", ".", "--source-only"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
