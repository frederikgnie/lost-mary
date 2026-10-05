#!/usr/bin/env python3
"""Behavioural tests for scripts/test-scope.py. Exit 0 = all passed.

Everything runs in a throwaway temp dir: a bare origin (so origin/HEAD
resolves in the clone), a clone of it, a nested repository and a linked
worktree inside the clone. HOME/USERPROFILE and GIT_CONFIG_GLOBAL point into the
sandbox, so neither the real ~/.claude nor any real checkout is read or written.
Each case starts from a fresh branch off main and a clean tree.

Usage: python tests/test-test-scope.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCOPE = ROOT / "scripts" / "test-scope.py"
failures: list[str] = []

TMP = Path(tempfile.mkdtemp(prefix="test-scope-")).resolve()
HOME = TMP / "home"
HOME.mkdir()
REAL_HOME = Path.home().resolve()
if HOME == REAL_HOME or HOME in REAL_HOME.parents:
    sys.exit(f"refusing to run: sandbox HOME {HOME} is or contains the real home {REAL_HOME}")
PROJ = TMP / "proj"
ENV = {
    **os.environ,
    "HOME": str(HOME),
    "USERPROFILE": str(HOME),
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": str(HOME / ".gitconfig"),
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CEILING_DIRECTORIES": str(TMP),  # -C outside a repo must not find one above the sandbox
}

# Tracked on main. The skip-dir copies of test_alpha.py must never be selected.
TRACKED = [
    "README.md",
    "src/pkg/__init__.py",
    "src/pkg/alpha.py",
    "src/pkg/beta.py",
    "src/pkg/gamma.py",  # no test file of its own
    "tests/conftest.py",
    "tests/test_alpha.py",
    "tests/integration/test_flow.py",
    "tests/unit/conftest.py",
    "tests/unit/alpha_test.py",
    "tests/unit/test_beta.py",
    "tests/unit/deep/test_deep.py",
    "tests/helpers.py",  # not a test file: a conftest never selects it
    *(f"{d}/test_alpha.py" for d in (".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist")),
]
ALL_TESTS = [
    "tests/integration/test_flow.py",
    "tests/test_alpha.py",
    "tests/unit/alpha_test.py",
    "tests/unit/deep/test_deep.py",
    "tests/unit/test_beta.py",
]
ALPHA = ["tests/test_alpha.py", "tests/unit/alpha_test.py"]


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}")
    if not ok:
        failures.append(f"{label}: {detail}"[:2000])


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {cwd} failed: {r.stderr}")
    return r.stdout


def cli(*args: str, cwd: Path = TMP) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCOPE), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        timeout=120,
    )


def write(rel: str, text: str = "x = 1\n", base: Path = PROJ) -> None:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def touch(rel: str) -> None:
    """Change a tracked file's content."""
    write(rel, (PROJ / rel).read_text(encoding="utf-8") + "# changed\n")


def commit_all(message: str) -> None:
    git(PROJ, "add", "-A", "--", ".", ":!inner_wt", ":!vendor")  # both stay untracked: status lists them
    git(PROJ, "commit", "-q", "-m", message)


def fresh(branch: str) -> None:
    """A new branch off local main and a clean tree; the nested repo and the inner worktree survive `clean -fd`."""
    git(PROJ, "checkout", "-q", "-f", "-B", branch, "main")
    git(PROJ, "clean", "-fdq")


def expect(label: str, r: subprocess.CompletedProcess[str], targets: list[str]) -> None:
    got = r.stdout.splitlines()
    check(label, r.returncode == 0 and got == targets, f"exit {r.returncode}, stdout {got}, stderr {r.stderr!r}")


def scope(*args: str) -> subprocess.CompletedProcess[str]:
    return cli("-C", str(PROJ), *args)


# --- fixture: origin, clone, a nested repo and a linked worktree inside the clone ------------------
seed = TMP / "seed"
seed.mkdir()
git(seed, "init", "-q", "-b", "main")
for rel in TRACKED:
    write(rel, base=seed)
git(seed, "add", "-A")
git(seed, "commit", "-q", "-m", "seed")
git(TMP, "clone", "-q", "--bare", str(seed), str(TMP / "origin.git"))
git(TMP, "clone", "-q", str(TMP / "origin.git"), str(PROJ))
check(
    "fixture: origin/HEAD resolves in the clone", "origin/main" in git(PROJ, "symbolic-ref", "refs/remotes/origin/HEAD")
)
nested = PROJ / "vendor" / "nested"
nested.mkdir(parents=True)
git(nested, "init", "-q", "-b", "main")
write("vendor/nested/test_alpha.py")
git(PROJ, "worktree", "add", "-q", str(PROJ / "inner_wt"), "-b", "inner")  # carries tests/test_alpha.py too
check("fixture: the inner worktree holds a test_alpha.py", (PROJ / "inner_wt" / "tests" / "test_alpha.py").is_file())

# --- module rule: a committed change on the branch, both naming forms, every skip dir -------------
fresh("case-module")
touch("src/pkg/alpha.py")
commit_all("alpha")
r = scope()
expect("committed module change -> test_<stem>.py and <stem>_test.py, no skip-dir or nested copy", r, ALPHA)
check("found: exit 0, stderr empty", r.returncode == 0 and r.stderr == "", r.stderr)
r = cli(cwd=PROJ / "src" / "pkg")
expect("no -C: the repo containing the cwd, paths repo-relative not cwd-relative", r, ALPHA)
r = scope("--cmd")
check(
    "--cmd -> one `pytest <targets...>` line",
    r.returncode == 0 and r.stdout == "pytest tests/test_alpha.py tests/unit/alpha_test.py\n",
    repr(r.stdout),
)

# --- working tree, index, untracked ----------------------------------------------------------------
fresh("case-worktree")
touch("src/pkg/beta.py")  # unstaged
expect("unstaged module change -> its test", scope(), ["tests/unit/test_beta.py"])

fresh("case-index")
touch("tests/integration/test_flow.py")
git(PROJ, "add", "tests/integration/test_flow.py")
expect("staged change to a test file -> that test file", scope(), ["tests/integration/test_flow.py"])

fresh("case-untracked")
write("tests/newdir/test_new.py")
write("tests/new_test.py")
expect("untracked test files, in a new dir too -> selected", scope(), ["tests/new_test.py", "tests/newdir/test_new.py"])

fresh("case-rename")
git(PROJ, "mv", "src/pkg/alpha.py", "src/pkg/alpha_v2.py")
expect("a renamed module -> the old name's tests still selected", scope(), ALPHA)

# --- conftest rule ----------------------------------------------------------------------------------
fresh("case-conftest-unit")
touch("tests/unit/conftest.py")
expect(
    "tests/unit/conftest.py -> every test file in its dir and below",
    scope(),
    ["tests/unit/alpha_test.py", "tests/unit/deep/test_deep.py", "tests/unit/test_beta.py"],
)
fresh("case-conftest-tests")
touch("tests/conftest.py")
commit_all("conftest")
expect("tests/conftest.py -> every test under tests/, none in a skip dir", scope(), ALL_TESTS)
fresh("case-conftest-root")
write("conftest.py")  # a new root conftest
expect("a root conftest.py -> every test file outside the skipped dirs", scope(), ALL_TESTS)

# --- deleted test file ----------------------------------------------------------------------------------
fresh("case-deleted")
git(PROJ, "rm", "-q", "tests/test_alpha.py")
touch("src/pkg/alpha.py")
commit_all("drop test_alpha")
expect("a deleted test file is not selected; the module's other test is", scope(), ["tests/unit/alpha_test.py"])
fresh("case-deleted-only")
git(PROJ, "rm", "-q", "tests/integration/test_flow.py")
r = scope()
check(
    "only a deleted test file -> exit 3, stdout empty",
    r.returncode == 3 and r.stdout == "" and "(1 files)" in r.stderr,
    f"exit {r.returncode} {r.stdout!r} {r.stderr!r}",
)

# --- exit 3: changed .py, nothing matches -----------------------------------------------------------
fresh("case-nomatch")
touch("src/pkg/__init__.py")
touch("src/pkg/gamma.py")
r = scope()
check(
    "__init__ and an untested module -> exit 3 with the exact message",
    r.returncode == 3
    and r.stdout == ""
    and r.stderr.strip()
    == "test-scope: no unit tests match the change (2 files) - run the test root of the touched package",
    f"exit {r.returncode} {r.stdout!r} {r.stderr!r}",
)

# --- exit 4: no Python change -------------------------------------------------------------------------
fresh("case-clean")
r = scope()
check(
    "no change at all -> exit 4",
    r.returncode == 4 and r.stdout == "" and r.stderr.strip() == "test-scope: no Python change",
    repr(r),
)
touch("README.md")
r = scope()
check("only a non-Python change -> exit 4", r.returncode == 4 and r.stdout == "", repr(r))
for skip in (".venv", "venv", "node_modules", "__pycache__", ".tox", "build", "dist"):
    fresh(f"case-skip-{skip.strip('._')}")
    touch(f"{skip}/test_alpha.py")
    r = scope()
    check(
        f"a change only under {skip}/ is not a Python change -> exit 4", r.returncode == 4 and r.stdout == "", repr(r)
    )

# --- --base -------------------------------------------------------------------------------------------
fresh("case-base")
touch("src/pkg/alpha.py")
commit_all("alpha")
touch("src/pkg/beta.py")
commit_all("beta")
expect("default base: both branch commits count", scope(), [*ALPHA, "tests/unit/test_beta.py"])
expect("--base HEAD~1: only the last commit counts", scope("--base", "HEAD~1"), ["tests/unit/test_beta.py"])
r = scope("--base", "no-such-ref")
check(
    "--base that does not resolve -> exit 2, one stderr line",
    r.returncode == 2 and r.stdout == "" and len(r.stderr.strip().splitlines()) == 1 and "no-such-ref" in r.stderr,
    repr(r),
)
r = scope("--base", "--output=x")
check("--base that looks like an option -> exit 2, not passed to git", r.returncode == 2, repr(r))

# Default base is origin/HEAD's branch, not local main: advance local main past origin/main.
git(PROJ, "checkout", "-q", "-f", "main")
touch("src/pkg/alpha.py")
commit_all("local main only")
fresh("case-origin")
touch("src/pkg/beta.py")
expect("default base is origin/HEAD's branch: local main's commit counts", scope(), [*ALPHA, "tests/unit/test_beta.py"])
git(PROJ, "remote", "set-head", "origin", "-d")
expect("no origin/HEAD -> falls back to main", scope(), ["tests/unit/test_beta.py"])
git(PROJ, "remote", "set-head", "origin", "main")
git(PROJ, "checkout", "-q", "-f", "main")
git(PROJ, "reset", "-q", "--hard", "origin/main")

# --- unresolvable default base and not a repository ------------------------------------------------
trunk = TMP / "trunk"
trunk.mkdir()
git(trunk, "init", "-q", "-b", "trunk")
write("a.py", base=trunk)
git(trunk, "add", "-A")
git(trunk, "commit", "-q", "-m", "a")
r = cli("-C", str(trunk))
check(
    "no origin/HEAD, no main, no master -> exit 2, one stderr line",
    r.returncode == 2 and len(r.stderr.strip().splitlines()) == 1 and "base" in r.stderr,
    repr(r),
)
r = cli("-C", str(HOME))
check(
    "-C outside any repository -> exit 2, one stderr line",
    r.returncode == 2 and len(r.stderr.splitlines()) == 1,
    repr(r),
)

# --- reads only ----------------------------------------------------------------------------------------
fresh("case-readonly")
touch("src/pkg/beta.py")
status_before = git(PROJ, "status", "--porcelain")
scope()
check("the CLI changed nothing in the tree or index", git(PROJ, "status", "--porcelain") == status_before)

git(PROJ, "worktree", "remove", "--force", str(PROJ / "inner_wt"))
shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print(f"\n{len(failures)} failed:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("\nall passed")
