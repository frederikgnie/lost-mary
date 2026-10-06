#!/usr/bin/env python3
"""Opt-in live Codex smoke test (uses your configured model/account, not run in CI).

python evals/codex-smoke.py --output evals/results/codex-local --case local
python evals/codex-smoke.py --output evals/results/codex-review --case review

Creates a fresh isolated repository, installs the real bundle, captures JSONL,
and independently checks the result. Never installs into the user's profile.
Review cases use ordinary persisted Codex sessions: ephemeral mode can prevent
native delegation. The local case uses an ephemeral session.
The timeout bounds the CLI wait, not an account spend limit. Keep output for
manual trace review: passing these small cases is not proof of general autonomy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = """import unittest
from labels import normalize

class Labels(unittest.TestCase):
    def test_order(self):
        self.assertEqual(normalize([" B ", "a", "b", "", " A "]), ["b", "a"])
    def test_none(self):
        self.assertEqual(normalize(None), [])
    def test_input_unchanged(self):
        values = [" B ", "a", "b"]
        normalize(values)
        self.assertEqual(values, [" B ", "a", "b"])
"""


def snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and not {".git", "__pycache__"}.intersection(p.relative_to(root).parts)
    }


def grade(events: list[dict], before: dict, after: dict, final: str, passed: bool) -> dict[str, bool]:
    items = [e["item"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)]
    commands = [(i, item) for i, item in enumerate(items) if item.get("type") == "command_execution"]
    full = [
        (i, item)
        for i, item in commands
        if re.search(r"-m\s+unittest\s+test_labels(?:[\"'\s]|$)", item.get("command", ""))
    ]
    edits = [i for i, item in enumerate(items) if item.get("type") == "file_change"]
    changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}

    # Check recorded successful execution, not the final message's claim.
    def success(item: dict) -> bool:
        return item.get("exit_code") == 0 and "OK" in item.get("aggregated_output", "")

    return {
        "independent_tests_pass": passed,
        "only_owned_file_changed": changed == {"labels.py"},
        "procedure_read": any(
            "procedure.md" in item.get("command", "") and item.get("exit_code") == 0 for _, item in commands
        ),
        "quick_check_ran": any(
            "test_labels.Labels.test_order" in item.get("command", "")
            and item.get("exit_code") is not None
            and "Ran 1 test" in item.get("aggregated_output", "")
            for _, item in commands
        ),
        "full_check_after_final_edit": bool(edits and full and full[-1][0] > max(edits) and success(full[-1][1])),
        "no_redundant_full_checks": len(full) == 1,
        "closed_done": bool(re.search(r"(?m)^DONE:\s*\S", final)),
        "turn_completed": any(e.get("type") == "turn.completed" for e in events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output", required=True, type=Path, help="new output directory; existing directories are refused"
    )
    parser.add_argument("--case", choices=("local", "review"), default="local")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    codex = shutil.which("codex")
    if not codex:
        parser.error("codex is not on PATH")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixture = output / "fixture"
    fixture.mkdir()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run(["git", "init", "-q", str(fixture)], check=True, env=env)
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/install-codex.py"), "--dest", str(fixture / ".agents/skills/lost-mary")],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    (fixture / "labels.py").write_text(
        "def normalize(values):\n    return sorted(set(value.strip().lower() for value in values))\n", encoding="utf-8"
    )
    (fixture / "test_labels.py").write_text(TESTS, encoding="utf-8")
    python = "& '" + sys.executable.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(sys.executable)
    (fixture / "AGENTS.md").write_text(
        "This is an isolated evaluation repo, not a shared checkout. Work here directly.\n"
        "Only labels.py may change. Tests, this file, and the installed skill are off-limits.\n"
        "Do not create branches, commits, PRs or worktrees; do not use network services.\n"
        f"Quick reproduction: {python} -B -m unittest test_labels.Labels.test_order\n"
        f"Final integrated validation: {python} -B -m unittest test_labels\n"
        "Use those project commands, without installing dependencies or extra lint/type tools.\n"
        "Run the full module once the proposed solution is ready.\n",
        encoding="utf-8",
    )
    for tail in (
        ["add", "."],
        [
            "-c",
            "user.name=Smoke Test",
            "-c",
            "user.email=smoke@example.invalid",
            "-c",
            "core.hooksPath=",
            "commit",
            "-qm",
            "Fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(fixture), *tail], check=True, env=env, capture_output=True)
    before = snapshot(fixture)
    prompt = (
        "$lost-mary Fix normalize in labels.py: accept None as empty input, ignore blank labels, "
        "lowercase and deduplicate while preserving first-seen order, without mutating the input. "
        "Finish and validate the change. "
    )
    prompt += (
        "This is a small local change; do not delegate."
        if args.case == "local"
        else (
            "Implement in the lead. Once the solution is ready, delegate exactly one independent read-only "
            "review of the diff to a native subagent, wait for it, and resolve actionable findings before "
            "completing. If delegation is unavailable, state that limitation honestly."
        )
    )
    command = [
        codex,
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        "workspace-write",
        "--json",
        "-C",
        str(fixture),
        "-o",
        str(output / "final.txt"),
        "-",
    ]
    if args.case == "local":
        command.insert(command.index("--json"), "--ephemeral")
    (output / "prompt.txt").write_text(prompt, encoding="utf-8")
    (output / "command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
    with (
        (output / "trace.jsonl").open("w", encoding="utf-8") as stdout,
        (output / "stderr.txt").open("w", encoding="utf-8") as stderr,
    ):
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                stdout=stdout,
                stderr=stderr,
                env=env,
                timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"Timed out; inspect {output}. No pass recorded.")
            return 2
    events = [
        json.loads(line)
        for line in (output / "trace.jsonl").read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    final_path = output / "final.txt"
    final = final_path.read_text(encoding="utf-8") if final_path.exists() else ""
    validation = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "test_labels"],
        cwd=fixture,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    (output / "validation.txt").write_text(validation.stdout + validation.stderr, encoding="utf-8")
    checks = grade(events, before, snapshot(fixture), final, validation.returncode == 0)
    checks["cli_exit_zero"] = result.returncode == 0
    report = {
        "case": args.case,
        "checks": checks,
        "usage": [e.get("usage") for e in events if e.get("type") == "turn.completed"],
        "review_trace_needs_manual_check": args.case == "review",
        "status": "failed"
        if not all(checks.values())
        else ("needs_review_evidence" if args.case == "review" else "passed"),
    }
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Trace and artifacts: {output}")
    if not all(checks.values()):
        return 1
    # Some runtimes omit spawn/results from JSONL. A final claim or empty wait
    # event is insufficient: inspect the persisted child turn before passing it.
    return 2 if args.case == "review" else 0


if __name__ == "__main__":
    sys.exit(main())
