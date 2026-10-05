#!/usr/bin/env python3
"""test-scope: the unit tests a change touches, for the QUICK test tier. Reads only; never runs a test.

Why this exists
---------------
Agents ran whole test suites after every small edit - about 5,000 pytest runs
in 14 days on one machine. The policy now has two tiers: a small change runs
QUICK - only the unit tests that cover what changed - and only the final merge
into main runs the FULL suite. This CLI is the deterministic selector for the
quick tier: the same tree and the same base always give the same targets.

Repository: the one containing the cwd, or `-C <dir>`.

Changed files: `git diff --name-only <base>...HEAD` (what the branch committed)
plus `git status --porcelain --untracked-files=all` (index, working tree and
untracked files). Renames are read as a deletion plus an addition, so both
names count. <base> is `--base <ref>`, else the branch origin/HEAD points at,
else `main`, else `master`; the diff runs from its merge-base with HEAD.

From the changed `.py` files, the pytest targets are:
  * a changed test file (`test_*.py` or `*_test.py`) that still exists;
  * for a changed module `<stem>.py` (not `__init__`, not `conftest`), every
    file in the repo named `test_<stem>.py` or `<stem>_test.py`;
  * for a changed `conftest.py`, every test file in its directory and below.
Never searched and never counted as a change: anything under .git, .venv, venv,
node_modules, __pycache__, .tox, build or dist, or under a directory holding
its own `.git` entry (a nested repository or a linked worktree) other than the
root.

Output: one repo-relative POSIX path per line, sorted and unique; `--cmd`
prints a single `pytest <targets...>` line instead.

Exit 0 targets printed; 2 bad arguments, not a repository, or a base that cannot
be resolved (one line on stderr); 3 changed .py files but no test matches them
(stdout empty, stderr says to run the touched package's test root); 4 no Python
change at all.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist"})
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # a read must not take index locks other sessions contend for
EXIT_ERROR = 2
EXIT_NO_MATCH = 3
EXIT_NO_PYTHON = 4


class ScopeError(Exception):
    """A one-line reason the change cannot be scoped (exit 2)."""


def notice(text: str) -> None:
    print(f"test-scope: {text}", file=sys.stderr)


def first_line(text: str, fallback: str) -> str:
    return (text.strip().splitlines() or [fallback])[0]


def probe(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=GIT_ENV,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScopeError(f"git {args[0]} failed ({exc!r})") from exc


def git(repo: Path, *args: str) -> str:
    r = probe(repo, *args)
    if r.returncode != 0:
        raise ScopeError(f"git {args[0]}: {first_line(r.stderr, f'exit {r.returncode}')}")
    return r.stdout


def repo_root(start: Path) -> Path:
    r = probe(start, "rev-parse", "--show-toplevel")
    if r.returncode != 0 or not r.stdout.strip():
        raise ScopeError(f"not a git repository: {start} ({first_line(r.stderr, f'exit {r.returncode}')})")
    return Path(r.stdout.strip())


def resolves(repo: Path, ref: str) -> bool:
    if not ref or ref.startswith("-"):  # never let a ref reach git as an option
        return False
    return probe(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def default_base(repo: Path) -> str:
    """The branch origin/HEAD points at, else `main`, else `master`."""
    origin = probe(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").stdout.strip()
    for ref in (origin, "main", "master"):
        if resolves(repo, ref):
            return ref
    raise ScopeError("cannot resolve a base: no origin/HEAD, main or master - pass --base <ref>")


def merge_base(repo: Path, base: str) -> str:
    if not resolves(repo, base):
        raise ScopeError(f"cannot resolve base {base!r}")
    r = probe(repo, "merge-base", base, "HEAD")
    if r.returncode != 0 or not r.stdout.strip():
        raise ScopeError(f"no merge base between {base!r} and HEAD")
    return r.stdout.strip()


def split_z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def changed_files(repo: Path, base_commit: str) -> set[str]:
    """Repo-relative POSIX paths changed on the branch, in the index, in the working tree, or untracked."""
    changed = set(split_z(git(repo, "diff", "--name-only", "--no-renames", "-z", base_commit, "HEAD")))
    status = git(repo, "status", "--porcelain", "--untracked-files=all", "--no-renames", "-z")
    changed.update(entry[3:] for entry in split_z(status) if len(entry) > 3)
    return changed


def is_test(name: str) -> bool:
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def skipped(root: Path, rel: str) -> bool:
    """Whether a repo-relative path lies under a skipped directory or a nested repository/worktree."""
    here = root
    for part in rel.split("/")[:-1]:
        here = here / part
        if part in SKIP_DIRS or (here / ".git").exists():
            return True
    return False


def test_files(root: Path) -> list[str]:
    """Every test file in the repo outside the skipped directories, repo-relative POSIX."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not (here / d / ".git").exists()]
        found.extend((here / f).relative_to(root).as_posix() for f in filenames if is_test(f))
    return found


def select(root: Path, changed: set[str]) -> tuple[list[str], list[str]]:
    """(changed .py files that count, the sorted pytest targets they select)."""
    py = sorted(c for c in changed if c.endswith(".py") and not skipped(root, c))
    if not py:
        return [], []
    tests = test_files(root)
    by_name: dict[str, list[str]] = {}
    for t in tests:
        by_name.setdefault(t.rsplit("/", 1)[-1], []).append(t)
    targets: set[str] = set()
    for rel in py:
        name = rel.rsplit("/", 1)[-1]
        stem = name.removesuffix(".py")
        if is_test(name):
            if (root / rel).is_file():
                targets.add(rel)
        elif stem == "conftest":
            prefix = rel.removesuffix(name)  # "" at the root, else "dir/sub/"
            targets.update(t for t in tests if t.startswith(prefix))
        elif stem != "__init__":
            for candidate in (f"test_{stem}.py", f"{stem}_test.py"):
                targets.update(by_name.get(candidate, []))
    return py, sorted(targets)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="test-scope",
        description="Print the unit tests that cover the change on this branch (the QUICK test tier).",
    )
    ap.add_argument("-C", dest="directory", type=Path, default=None, help="run in this repository (default: cwd)")
    ap.add_argument("--base", default=None, help="diff against this ref (default: origin/HEAD's branch, main, master)")
    ap.add_argument("--cmd", action="store_true", help="print one `pytest <targets...>` line instead of paths")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        root = repo_root(args.directory or Path.cwd())
        base = args.base if args.base is not None else default_base(root)
        changed = changed_files(root, merge_base(root, base))
    except ScopeError as exc:
        notice(first_line(str(exc), "error"))
        return EXIT_ERROR
    py, targets = select(root, changed)
    if not py:
        notice("no Python change")
        return EXIT_NO_PYTHON
    if not targets:
        notice(f"no unit tests match the change ({len(py)} files) - run the test root of the touched package")
        return EXIT_NO_MATCH
    print(shlex.join(["pytest", *targets]) if args.cmd else "\n".join(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
