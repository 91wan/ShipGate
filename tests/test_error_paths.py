from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from shipgate.checks.assets import _hash_stable_file, check_assets
from shipgate.checks.project import (
    check_codex_skill,
    check_macos_app,
    check_project_type,
    detect_project,
    parse_frontmatter,
)
from shipgate.checks.readme import check_readmes
from shipgate.engine import run_check
from shipgate.git_surface import (
    GitSurfaceError,
    _batch_read_objects,
    _commit_path_entries,
    _history_entries,
    _identity_entries,
    _index_entries,
    _object_metadata_entries,
    _ref_entries,
    _working_entries,
    inspect_git,
    require_git_output,
    run_git,
    stream_git_blob,
)
from shipgate.inventory import build_filesystem_inventory, safe_relative, stream_file
from shipgate.model import (
    InventoryEntry,
    Operation,
    ProjectDetection,
    ProjectType,
    SourceKind,
    Status,
)
from shipgate.reporting import ReportWriteError, _atomic_write, render_markdown, report_dict
from tests.helpers import init_git, make_skill


class MockDirEntry:
    def __init__(self, path: Path):
        self.path = str(path)
        self.name = path.name

    def stat(self, *, follow_symlinks: bool = True):
        raise OSError("metadata unavailable")


class ErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_project_metadata_error_paths(self):
        project = self.root / "project"
        project.mkdir()
        self.assertEqual(check_codex_skill(project).status, Status.FAIL)
        self.assertEqual(check_macos_app(project).status, Status.FAIL)

        skill = project / "SKILL.md"
        skill.write_bytes(b"\xff\xfe\x00")
        self.assertEqual(check_codex_skill(project).status, Status.ERROR)
        skill.write_text("title only\n", encoding="utf-8")
        self.assertIsNotNone(parse_frontmatter(skill)[1])
        skill.write_text('---\nname: demo\ndescription: "unterminated\n---\n', encoding="utf-8")
        self.assertIsNotNone(parse_frontmatter(skill)[1])

        make_skill(project)
        agents = project / "agents"
        agents.mkdir()
        metadata = agents / "openai.yaml"
        metadata.write_bytes(b"\xff")
        self.assertEqual(check_codex_skill(project).status, Status.ERROR)

        unknown = detect_project(self.root / "missing")
        gate, selected = check_project_type(unknown, ProjectType.MACOS_APP)
        self.assertEqual(gate.status, Status.FAIL)
        self.assertEqual(selected, ProjectType.MACOS_APP)
        auto_gate, auto_selected = check_project_type(ProjectDetection((), ()), ProjectType.AUTO)
        self.assertEqual(auto_gate.status, Status.FAIL)
        self.assertIsNone(auto_selected)

    def test_unreadable_and_invalid_platform_evidence(self):
        project = self.root / "platform"
        workspace = project / "Demo.xcworkspace" / "contents.xcworkspacedata"
        workspace.parent.mkdir(parents=True)
        workspace.write_text("not xml", encoding="utf-8")
        pbx = project / "Demo.xcodeproj" / "project.pbxproj"
        pbx.parent.mkdir()
        pbx.write_bytes(b"\xff")
        package = project / "Package.swift"
        package.write_bytes(b"\xff")

        self.assertEqual(detect_project(project).candidates, ())

    def test_readme_encoding_and_frontmatter_paths(self):
        project = make_skill(self.root / "readme")
        (project / "README.md").write_bytes(b"\xff")
        self.assertEqual(check_readmes(project).status, Status.ERROR)
        (project / "README.md").write_text(
            "---\ntitle: Demo\n---\n# Demo\n\n[中文](./README_ZH.md)\n",
            encoding="utf-8",
        )
        self.assertEqual(check_readmes(project).status, Status.PASS)

    def test_asset_symlink_directory_and_io_errors(self):
        project = make_skill(self.root / "assets")
        target = project / "real.zip"
        target.write_bytes(b"value")
        link = project / "link.zip"
        link.symlink_to(target)
        gate, _ = check_assets(project, (link,), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.FAIL)

        directory = project / "folder.zip"
        directory.mkdir()
        gate, _ = check_assets(project, (directory,), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.FAIL)

        with patch("shipgate.checks.assets.os.open", side_effect=OSError("denied")):
            gate, _ = check_assets(project, (target,), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.ERROR)
        with self.assertRaises(OSError):
            _hash_stable_file(directory)

        real_fstat = os.fstat
        calls = 0

        def changing_fstat(descriptor: int):
            nonlocal calls
            result = real_fstat(descriptor)
            calls += 1
            if calls == 2:
                values = list(result)
                values[6] += 1
                return os.stat_result(values)
            return result

        with patch("shipgate.checks.assets.os.fstat", side_effect=changing_fstat):
            gate, _ = check_assets(project, (target,), Operation.RELEASE, False)
        self.assertEqual(gate.status, Status.ERROR)

    def test_filesystem_inventory_invalid_internal_link_and_scan_error(self):
        missing = self.root / "missing"
        invalid = build_filesystem_inventory(missing)
        self.assertIn("inventory.invalid-project", {item.code for item in invalid.errors})
        self.assertEqual(
            safe_relative(self.root, self.root / "child"), "external/" + self.root.name
        )

        project = make_skill(self.root / "inventory")
        directory = project / "directory"
        directory.mkdir()
        link = project / "directory-link"
        link.symlink_to(directory, target_is_directory=True)
        inventory = build_filesystem_inventory(project)
        self.assertIn("inventory.unsupported-symlink", {item.code for item in inventory.errors})

        entry = next(item for item in inventory.entries if item.path == "SKILL.md")
        with patch("shipgate.inventory.os.open", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                list(stream_file(entry))
        with self.assertRaises(OSError):
            list(stream_file(InventoryEntry("none", 0, "working-tree")))

    def test_filesystem_inventory_exclusions_and_metadata_failures(self):
        project = make_skill(self.root / "filesystem")
        (project / ".git").mkdir()
        report = project / "report.json"
        report.write_text("{}", encoding="utf-8")
        inventory = build_filesystem_inventory(project, (report,))
        self.assertEqual(
            {item.reason for item in inventory.excluded},
            {"git-internal", "report-output"},
        )

        with patch("shipgate.inventory.os.scandir", side_effect=OSError("denied")):
            unreadable = build_filesystem_inventory(project)
        self.assertIn("inventory.unreadable-directory", {item.code for item in unreadable.errors})

        fake = MockDirEntry(project / "bad")
        with patch("shipgate.inventory.os.scandir", return_value=[fake]):
            invalid = build_filesystem_inventory(project)
        self.assertIn("inventory.unreadable-entry", {item.code for item in invalid.errors})

        target = project / "target.txt"
        target.write_text("value", encoding="utf-8")
        link = project / "internal-link"
        link.symlink_to(target)
        linked = build_filesystem_inventory(project)
        self.assertIn("internal-link", {item.path for item in linked.entries})

    def test_stream_file_rejects_type_or_content_change(self):
        project = make_skill(self.root / "stream")
        path = project / "file.txt"
        path.write_text("value", encoding="utf-8")
        entry = InventoryEntry("file.txt", 5, "working-tree", fs_path=path)
        original = os.fstat
        descriptor = os.open(path, os.O_RDONLY)
        try:
            real = original(descriptor)
        finally:
            os.close(descriptor)
        directory_values = list(real)
        directory_values[0] = directory_values[0] & ~0o170000 | 0o040000
        with patch("shipgate.inventory.os.fstat", return_value=os.stat_result(directory_values)):
            with self.assertRaises(OSError):
                list(stream_file(entry))

        calls = 0

        def changed(descriptor: int):
            nonlocal calls
            value = original(descriptor)
            calls += 1
            if calls == 2:
                parts = list(value)
                parts[6] += 1
                return os.stat_result(parts)
            return value

        with patch("shipgate.inventory.os.fstat", side_effect=changed):
            with self.assertRaises(OSError):
                list(stream_file(entry))

    def test_git_command_and_inventory_errors(self):
        project = make_skill(self.root / "git")
        init_git(project)
        with patch("shipgate.git_surface.subprocess.run", side_effect=OSError("missing")):
            with self.assertRaises(GitSurfaceError):
                run_git(project, ["status"])
        with patch(
            "shipgate.git_surface.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 1),
        ):
            with self.assertRaises(GitSurfaceError):
                run_git(project, ["status"])
        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.return_value = subprocess.CompletedProcess([], 1, "", "error")
            with self.assertRaises(GitSurfaceError):
                require_git_output(project, ["status"])

        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.return_value = subprocess.CompletedProcess([], 1, b"", b"error")
            with self.assertRaises(GitSurfaceError):
                _working_entries(project, set())
            with self.assertRaises(GitSurfaceError):
                _index_entries(project)

        with patch("shipgate.git_surface.require_git_output", return_value=""):
            self.assertEqual(_history_entries(project, "HEAD", False), [])

        nested = project / "nested"
        nested.mkdir()
        with self.assertRaises(GitSurfaceError):
            inspect_git(nested, SourceKind.WORKING_TREE, None)

    def test_git_index_malformed_and_history_object_errors(self):
        project = make_skill(self.root / "objects")
        init_git(project)
        bad_records = (
            b"bad\0",
            b"100644 deadbeef 1\tfile\0",
        )
        for output in bad_records:
            with self.subTest(output=output), patch("shipgate.git_surface.run_git") as mocked:
                mocked.return_value = subprocess.CompletedProcess([], 0, output, b"")
                with self.assertRaises(GitSurfaceError):
                    _index_entries(project)

        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.side_effect = [
                subprocess.CompletedProcess([], 0, b"160000 deadbeef 0\tvendor\0", b""),
                subprocess.CompletedProcess([], 0, "", ""),
            ]
            self.assertEqual(_index_entries(project), [])

        with (
            patch("shipgate.git_surface.require_git_output", return_value="abc file"),
            patch("shipgate.git_surface.run_git") as mocked,
        ):
            mocked.return_value = subprocess.CompletedProcess([], 1, "", "error")
            with self.assertRaises(GitSurfaceError):
                _history_entries(project, "HEAD", False)

        valid_index = b"100644 deadbeef 0\tfile\0"
        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.side_effect = [
                subprocess.CompletedProcess([], 0, valid_index, b""),
                subprocess.CompletedProcess([], 1, "", "error"),
            ]
            with self.assertRaises(GitSurfaceError):
                _index_entries(project)
        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.side_effect = [
                subprocess.CompletedProcess([], 0, valid_index, b""),
                subprocess.CompletedProcess([], 0, "deadbeef tree 10\n", ""),
            ]
            with self.assertRaises(GitSurfaceError):
                _index_entries(project)

    def test_git_publication_metadata_error_paths(self):
        project = make_skill(self.root / "metadata-errors")
        init_git(project)
        self.assertEqual(_batch_read_objects(project, []), {})
        self.assertEqual(_commit_path_entries(project, []), [])
        self.assertEqual(_ref_entries(project, None, False), ([], []))

        with self.assertRaises(GitSurfaceError):
            _identity_entries("identity", b"malformed")
        with self.assertRaises(GitSurfaceError):
            _object_metadata_entries("abc", "commit", b"missing separator")

        failed = subprocess.CompletedProcess([], 1, b"", b"error")
        with patch("shipgate.git_surface.run_git", return_value=failed):
            with self.assertRaises(GitSurfaceError):
                _batch_read_objects(project, ["abc"])
            with self.assertRaises(GitSurfaceError):
                _commit_path_entries(project, ["abc"])
            with self.assertRaises(GitSurfaceError):
                _ref_entries(project, None, True)

        malformed_outputs = (
            b"missing-newline",
            b"abc blob\n",
            b"abc blob 2\nx\n",
            b"abc blob nope\nx\n",
            b"abc blob 1\nx\n",
        )
        for output in malformed_outputs:
            with (
                self.subTest(output=output),
                patch(
                    "shipgate.git_surface.run_git",
                    return_value=subprocess.CompletedProcess([], 0, output, b""),
                ),
            ):
                with self.assertRaises(GitSurfaceError):
                    _batch_read_objects(project, ["def"])

    def test_git_working_symlinks_exclusions_and_missing_paths(self):
        project = make_skill(self.root / "working")
        init_git(project)
        target = project / "target.txt"
        target.write_text("value", encoding="utf-8")
        internal = project / "internal"
        internal.symlink_to(target)
        external_target = self.root / "outside.txt"
        external_target.write_text("outside", encoding="utf-8")
        external = project / "external"
        external.symlink_to(external_target)
        missing = project / "missing"
        missing.symlink_to(project / "absent")
        excluded = project / "excluded.txt"
        excluded.write_text("excluded", encoding="utf-8")
        listed = b"internal\0external\0missing\0target.txt\0excluded.txt\0"
        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.side_effect = [
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess([], 0, listed, b""),
            ]
            entries, exclusions = _working_entries(project, {excluded.resolve()})
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(
            {item.path for item in entries},
            {"external", "internal", "missing", "target.txt"},
        )
        with patch("shipgate.git_surface.run_git") as mocked:
            mocked.side_effect = [
                subprocess.CompletedProcess([], 0, b"excluded.txt\0", b""),
                subprocess.CompletedProcess([], 0, b"excluded.txt\0", b""),
            ]
            with self.assertRaises(GitSurfaceError):
                _working_entries(project, {excluded.resolve()})

    def test_git_blob_reader_rejects_bad_framing(self):
        project = self.root / "blob"
        project.mkdir()

        class FakeProcess:
            def __init__(self, output: bytes):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO(output)
                self.stderr = io.BytesIO()

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                return None

        with patch("shipgate.git_surface.subprocess.Popen", return_value=FakeProcess(b"bad\n")):
            with self.assertRaises(GitSurfaceError):
                list(stream_git_blob(project, "abc"))
        output = b"abc blob 3\nxyzX"
        with patch("shipgate.git_surface.subprocess.Popen", return_value=FakeProcess(output)):
            with self.assertRaises(GitSurfaceError):
                list(stream_git_blob(project, "abc"))
        with patch("shipgate.git_surface.subprocess.Popen", side_effect=OSError("missing")):
            with self.assertRaises(GitSurfaceError):
                list(stream_git_blob(project, "abc"))
        short = b"abc blob 4\nxy"
        with patch("shipgate.git_surface.subprocess.Popen", return_value=FakeProcess(short)):
            with self.assertRaises(GitSurfaceError):
                list(stream_git_blob(project, "abc"))

    def test_reporting_and_engine_error_paths(self):
        project = make_skill(self.root / "report")
        (project / "README_ZH.md").unlink()
        report = run_check(project, project_type=ProjectType.CODEX_SKILL)
        markdown = render_markdown(report)
        self.assertIn("readme.missing-chinese", markdown)
        self.assertEqual(report.exit_code, 1)
        self.assertEqual(report_dict(report)["project_type"], "codex-skill")

        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaises(ReportWriteError):
            _atomic_write(directory, "content")

        with patch("shipgate.engine.inspect_git", side_effect=GitSurfaceError("no git")):
            report = run_check(
                project,
                project_type=ProjectType.CODEX_SKILL,
                operation=Operation.PUBLIC_PUSH,
                source=SourceKind.HISTORY_ALL,
            )
        self.assertEqual(report.exit_code, 3)

    def test_engine_macos_unknown_relative_report_and_module_entrypoints(self):
        mac = self.root / "mac"
        pbx = mac / "Demo.xcodeproj" / "project.pbxproj"
        pbx.parent.mkdir(parents=True)
        pbx.write_text("SDKROOT = macosx;\n", encoding="utf-8")
        (mac / "README.md").write_text("# Demo\n\n[中文](README_ZH.md)\n", encoding="utf-8")
        (mac / "README_ZH.md").write_text("# Demo\n\n[English](README.md)\n", encoding="utf-8")
        report = run_check(
            mac,
            project_type=ProjectType.MACOS_APP,
            report_json="report.json",
        )
        self.assertEqual(report.status, Status.PASS)
        self.assertTrue((mac / "report.json").is_file())

        unknown = self.root / "unknown"
        unknown.mkdir()
        self.assertEqual(run_check(unknown).status, Status.FAIL)

        old_argv = sys.argv
        try:
            sys.argv = ["shipgate", "--version"]
            with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                runpy.run_module("shipgate.__main__", run_name="__main__")
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
