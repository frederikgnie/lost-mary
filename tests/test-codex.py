#!/usr/bin/env python3
"""Hermetic Codex packaging/update checks; never install into the real home."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("install_codex", ROOT / "scripts/install-codex.py")
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)

smoke_spec = importlib.util.spec_from_file_location("codex_smoke", ROOT / "evals/codex-smoke.py")
assert smoke_spec and smoke_spec.loader
smoke = importlib.util.module_from_spec(smoke_spec)
sys.modules[smoke_spec.name] = smoke
smoke_spec.loader.exec_module(smoke)


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
        # The installer wrote the old copy, so replacing it needs no backup.
        self.assertEqual(list(self.dest.rglob("*.backup.*")), [])
        self.assertEqual(self.install(verify=True), 0)

    def test_only_local_edits_are_backed_up(self):
        self.install()
        edited = self.dest / "references/implement.md"
        edited.write_bytes(b"local change")
        for name in ("references/implement.md", "references/review.md"):
            self.files[name] = self.files[name] + b"\nUpstream update.\n"
        self.install()
        backups = list(self.dest.rglob("*.backup.*"))
        self.assertEqual([b.name.split(".backup.")[0] for b in backups], ["implement.md"])
        self.assertEqual(backups[0].read_bytes(), b"local change")
        self.assertEqual(self.install(verify=True), 0)

    def test_install_without_a_record_backs_up_what_it_replaces(self):
        self.install()
        (self.dest / installer.STATE).unlink()
        self.files["SKILL.md"] = self.files["SKILL.md"] + b"\nUpdated.\n"
        self.install()
        self.assertEqual(len(list(self.dest.glob("SKILL.md.backup.*"))), 1)

    def test_retired_files_are_reported_and_renamed_not_deleted(self):
        self.install()
        old = self.dest / "references/old.md"
        old.write_text("shipped by an earlier bundle")
        retired = ("references/old.md",)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(installer.install(self.dest, self.files, verify=True, retired=retired), 1)
        self.assertIn("RETIRED references/old.md", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.dest, self.files, retired=retired)
            self.assertEqual(installer.install(self.dest, self.files, verify=True, retired=retired), 0)
        self.assertFalse(old.exists())
        kept = list((self.dest / "references").glob("old.md.retired.*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), "shipped by an earlier bundle")

    def test_unowned_directory_is_not_overwritten(self):
        self.dest.mkdir(parents=True)
        sentinel = self.dest / "SKILL.md"
        sentinel.write_text("user's skill")
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.install()
        self.assertEqual(sentinel.read_text(), "user's skill")

    def sandbox_env(self) -> dict[str, str]:
        """A child environment whose home is inside the temp dir, checked before any write."""
        home = self.root / "home"
        home.mkdir(exist_ok=True)
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        probe = subprocess.run(
            [sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        child_home = Path(probe.stdout.strip()).resolve()
        self.assertEqual(child_home, home.resolve())
        self.assertNotEqual(child_home, Path.home().resolve())
        return env

    def test_cli_install_and_drift_in_a_path_with_spaces(self):
        env = self.sandbox_env()
        target = self.root / "project with spaces" / ".agents" / "skills" / "lost-mary"
        args = [sys.executable, str(ROOT / "scripts/install-codex.py"), "--dest", str(target)]
        for extra in ([], ["--verify"]):
            result = subprocess.run(args + extra, capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        (target / "references/implement.md").write_text("local change")
        result = subprocess.run(args + ["--verify"], capture_output=True, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"DRIFT references/implement.md", result.stdout)

    def test_cli_default_destination_is_under_home(self):
        env = self.sandbox_env()
        result = subprocess.run([sys.executable, str(ROOT / "scripts/install-codex.py")], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "home" / ".agents" / "skills" / "lost-mary" / "SKILL.md").is_file())

    def test_unmanaged_files_survive_and_missing_files_are_repaired(self):
        self.install()
        note = self.dest / "personal-note.txt"
        note.write_text("keep me")
        (self.dest / "references/review.md").unlink()
        self.assertEqual(self.install(verify=True), 1)
        self.install()
        self.assertEqual(note.read_text(), "keep me")
        self.assertEqual(self.install(verify=True), 0)

    def test_failed_replacement_preserves_previous_file(self):
        self.install()
        previous = (self.dest / "SKILL.md").read_bytes()
        self.files["SKILL.md"] = previous + b"\nUpdated.\n"
        with mock.patch.object(installer.os, "replace", side_effect=OSError("simulated interrupted update")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.install()
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), previous)
        self.assertEqual(list(self.dest.rglob(".lost-mary-*.tmp")), [])
        self.assertEqual(self.install(verify=True), 1)
        self.install()
        self.assertEqual(self.install(verify=True), 0)

    def test_file_directory_collision_is_rejected_before_any_updates(self):
        self.install()
        previous = (self.dest / "SKILL.md").read_bytes()
        self.files["SKILL.md"] = previous + b"\nUpdated.\n"
        collision = self.dest / "references/review.md"
        collision.unlink()
        collision.mkdir()
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            self.install()
        self.assertEqual((self.dest / "SKILL.md").read_bytes(), previous)

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


class SmokeGraderTests(unittest.TestCase):
    @staticmethod
    def event(kind, **fields):
        return {"type": "item.completed", "item": {"type": kind, **fields}}

    def setUp(self):
        self.before = {"labels.py": "old", "test_labels.py": "protected"}
        self.after = {**self.before, "labels.py": "fixed"}
        self.events = [
            self.event("command_execution", command="read procedure.md", exit_code=0),
            self.event(
                "command_execution",
                command="python -m unittest test_labels.Labels.test_order",
                exit_code=1,
                aggregated_output="Ran 1 test\nFAILED",
            ),
            self.event("file_change"),
            self.event(
                "command_execution",
                command="python -m unittest test_labels",
                exit_code=0,
                aggregated_output="Ran 3 tests\nOK",
            ),
            {"type": "turn.completed"},
        ]

    def grade(self, events=None, after=None, passed=True):
        return smoke.grade(
            self.events if events is None else events,
            self.before,
            self.after if after is None else after,
            "DONE: passed",
            passed,
        )

    def test_reproduction_failure_then_validated_fix_passes(self):
        self.assertTrue(all(self.grade().values()))

    def test_final_claim_cannot_replace_missing_execution(self):
        result = self.grade(events=[{"type": "turn.completed"}])
        self.assertFalse(result["quick_check_ran"])
        self.assertFalse(result["full_check_after_last_recorded_file_change"])

    def test_stale_and_redundant_validation_is_detected(self):
        events = self.events[:-1] + [self.event("file_change"), self.events[-1]]
        self.assertFalse(self.grade(events=events)["full_check_after_last_recorded_file_change"])
        events = self.events[:-1] + [self.events[-2], self.events[-1]]
        self.assertFalse(self.grade(events=events)["no_redundant_full_checks"])

    def test_test_tampering_and_real_failures_are_rejected(self):
        self.assertFalse(self.grade(after={**self.after, "test_labels.py": "weakened"})["only_owned_file_changed"])
        self.assertFalse(self.grade(passed=False)["independent_tests_pass"])

    def test_non_json_trace_lines_are_kept_aside_not_fatal(self):
        events, stray = smoke.parse_trace('{"type": "turn.completed"}\nwarning: slow\n\n[1]\n')
        self.assertEqual(events, [{"type": "turn.completed"}])
        self.assertEqual(stray, ["warning: slow", "[1]"])

    def test_timeout_kill_reaches_grandchildren(self):
        # The shape of a .cmd shim: a parent whose real work runs in a child process.
        pidfile = Path(tempfile.mkdtemp(prefix="lost-mary-kill-")) / "child.pid"
        self.addCleanup(shutil.rmtree, pidfile.parent, True)
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(120)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", script], start_new_session=os.name != "nt")
        deadline = time.monotonic() + 30
        while not (pidfile.is_file() and pidfile.read_text().strip()):
            self.assertLess(time.monotonic(), deadline, "child never started")
            time.sleep(0.1)
        child = int(pidfile.read_text())
        smoke.kill_tree(process)
        deadline = time.monotonic() + 10
        while alive(child):
            self.assertLess(time.monotonic(), deadline, f"grandchild {child} survived kill_tree")
            time.sleep(0.1)


def alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A reparented zombie still answers kill(0); it is dead for our purposes.
    with contextlib.suppress(OSError):
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    return True


if __name__ == "__main__":
    unittest.main()
