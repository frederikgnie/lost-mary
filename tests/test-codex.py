#!/usr/bin/env python3
"""Hermetic Codex packaging/update checks; never install into the real home."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("install_codex", ROOT / "scripts/install-codex.py")
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


class CodexInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="lost-mary-codex-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / "skills" / "lost-mary"
        self.files = installer.bundle(ROOT)

    def install(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return installer.install(self.dest, self.files, **kwargs)

    def test_dry_run_and_verify_never_create_destination(self):
        self.assertEqual(self.install(dry_run=True), 0)
        self.assertEqual(self.install(verify=True), 1)
        self.assertFalse(self.dest.exists())

    def test_install_links_helpers_and_idempotence(self):
        self.install()
        self.assertEqual(self.install(verify=True), 0)
        before = {p: p.stat().st_mtime_ns for p in self.dest.rglob("*") if p.is_file()}
        self.install()
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})
        entry = (self.dest / "SKILL.md").read_text()
        for target in re.findall(r"\]\(([^)]+)\)", entry):
            self.assertTrue((self.dest / target).is_file(), target)
        self.assertIn("allow_implicit_invocation: false", (self.dest / "agents/openai.yaml").read_text())
        for helper in ("pycheck.py", "test-scope.py"):
            run = subprocess.run([sys.executable, str(self.dest / "scripts" / helper), "--help"], capture_output=True)
            # pycheck deliberately reports help with its usage exit (3).
            self.assertEqual(run.returncode, 3 if helper == "pycheck.py" else 0, run.stderr)
            self.assertNotIn(b"Traceback", run.stderr)

    def test_canonical_changes_flow_through_without_adapter_edits(self):
        source = self.root / "source"
        for directory in ("skills", "agents", "scripts", "codex"):
            shutil.copytree(ROOT / directory, source / directory, ignore=shutil.ignore_patterns("__pycache__"))
        self.install()
        original = (self.dest / "references/procedure.md").read_bytes()
        upstream = source / "skills/lost-mary/SKILL.md"
        upstream.write_bytes(original + b"\nA new canonical instruction.\n")
        self.files = installer.bundle(source)
        self.assertEqual(self.install(verify=True), 1)
        self.install()
        self.assertEqual((self.dest / "references/procedure.md").read_bytes(), upstream.read_bytes())
        backups = list((self.dest / "references").glob("procedure.md.backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.install(verify=True), 0)

    def test_unowned_directory_is_not_overwritten(self):
        self.dest.mkdir(parents=True)
        sentinel = self.dest / "SKILL.md"
        sentinel.write_text("user's skill")
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.install()
        self.assertEqual(sentinel.read_text(), "user's skill")

    def test_cli_install_and_drift_in_a_path_with_spaces(self):
        target = self.root / "project with spaces" / ".agents" / "skills" / "lost-mary"
        args = [sys.executable, str(ROOT / "scripts/install-codex.py"), "--dest", str(target)]
        for extra in ([], ["--verify"]):
            result = subprocess.run(args + extra, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        (target / "references/implement.md").write_text("local change")
        result = subprocess.run(args + ["--verify"], capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"DRIFT references/implement.md", result.stdout)

    def test_unmanaged_files_survive_and_missing_files_are_repaired(self):
        self.install()
        note = self.dest / "personal-note.txt"
        note.write_text("keep me")
        (self.dest / "references/review.md").unlink()
        self.assertEqual(self.install(verify=True), 1)
        self.install()
        self.assertEqual(note.read_text(), "keep me")
        self.assertEqual(self.install(verify=True), 0)

    def test_redirected_managed_directory_is_rejected(self):
        self.install()
        outside = self.root / "outside"
        outside.mkdir()
        shutil.rmtree(self.dest / "references")
        try:
            (self.dest / "references").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable on this host")
        with self.assertRaisesRegex(ValueError, "redirected"):
            self.install()
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
