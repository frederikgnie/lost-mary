#!/usr/bin/env python3
"""Behavioural tests for scripts/prune-worktrees.py. Exit 0 = all passed.

Everything runs in a throwaway temp dir: a bare origin, a clone of it, and one
sibling worktree per verdict. HOME/USERPROFILE point into the sandbox and
$LOST_MARY_SHARED_CHECKOUTS at a temp config, so neither the real ~/.claude nor
any real checkout is read or written. gh is never called: the CLI runs with
LOST_MARY_NO_GH=1, and the PR paths are driven through an injected lookup.

Usage: python tests/test-prune-worktrees.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
PRUNE = ROOT / "scripts" / "prune-worktrees.py"
failures: list[str] = []

TMP = Path(tempfile.mkdtemp(prefix="prune-wt-")).resolve()
HOME = TMP / "home"
HOME.mkdir()
REAL_HOME = Path.home().resolve()
if HOME == REAL_HOME or HOME in REAL_HOME.parents:
    sys.exit(f"refusing to run: sandbox HOME {HOME} is or contains the real home {REAL_HOME}")
CONFIG = TMP / "shared-checkouts.json"
WORK = TMP / "work"
PROJ = WORK / "proj"
ENV = {
    **os.environ,
    "HOME": str(HOME),
    "USERPROFILE": str(HOME),
    "LOST_MARY_SHARED_CHECKOUTS": str(CONFIG),
    "LOST_MARY_NO_GH": "1",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": str(HOME / ".gitconfig"),
    "GIT_CONFIG_NOSYSTEM": "1",
}


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


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PRUNE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=ENV,
        timeout=300,
    )


def commit(cwd: Path, name: str) -> None:
    (cwd / name).write_text(name, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", name)


def worktree(name: str, *extra: str) -> Path:
    path = WORK / f"proj_wt_{name}"
    git(PROJ, "worktree", "add", "-q", str(path), *extra)
    return path


def age_tree(root: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirnames:
            dirnames.remove(".git")
        dirs.append(Path(dirpath))
        for f in filenames:
            os.utime(Path(dirpath) / f, (stamp, stamp))
    for d in reversed(dirs):  # the pruner reads directory mtimes too (a deletion changes only those)
        os.utime(d, (stamp, stamp))


def age_gitdir(wt: Path, seconds: float) -> None:
    """Age git's own records for a worktree (index, HEAD, logs/HEAD): the pruner counts them as activity."""
    stamp = time.time() - seconds
    gitdir = Path(git(wt, "rev-parse", "--absolute-git-dir").strip())
    for name in ("index", "HEAD", "logs/HEAD"):
        f = gitdir / name
        if f.exists():
            os.utime(f, (stamp, stamp))


def merge_to_main(wt: Path, branch: str) -> None:
    git(wt, "push", "-q", "origin", branch)
    git(PROJ, "merge", "-q", "--ff-only", branch)
    git(PROJ, "push", "-q", "origin", "main")


def load_module() -> ModuleType:
    os.environ.update({k: v for k, v in ENV.items() if k.startswith(("GIT_", "HOME", "USERPROFILE", "LOST_MARY"))})
    spec = importlib.util.spec_from_file_location("prune_worktrees", PRUNE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- fixture: origin, clone, one worktree per verdict -----------------------------------------
seed = TMP / "seed"
seed.mkdir()
git(seed, "init", "-q", "-b", "main")
commit(seed, "README")
git(TMP, "clone", "-q", "--bare", str(seed), str(TMP / "origin.git"))
WORK.mkdir()
git(WORK, "clone", "-q", str(TMP / "origin.git"), str(PROJ))

wt_merged = worktree("merged", "-b", "feat/merged")
commit(wt_merged, "merged.txt")
merge_to_main(wt_merged, "feat/merged")
wt_cache = worktree("cache", "-b", "feat/cache")  # merged; only an ignored cache left behind: removable
commit(wt_cache, "cache.txt")
merge_to_main(wt_cache, "feat/cache")
wt_active = worktree("active", "-b", "feat/active")  # merged, but a file changed just now
commit(wt_active, "active.txt")
merge_to_main(wt_active, "feat/active")
wt_skip = worktree("skip", "-b", "feat/skip")  # merged, but a skip-worktree file could hold invisible edits
commit(wt_skip, "skip.txt")
merge_to_main(wt_skip, "feat/skip")
git(wt_skip, "update-index", "--skip-worktree", "skip.txt")
(wt_skip / "skip.txt").write_text("edited where status cannot see it", encoding="utf-8")
wt_fresh = worktree("fresh", "-b", "feat/fresh")  # just added from main: an ancestor of it, no work done yet

wt_dirty = worktree("dirty", "-b", "feat/dirty")
(wt_dirty / "scratch.txt").write_text("wip", encoding="utf-8")
wt_ignored = worktree("ignored", "-b", "feat/ignored")  # an ignored file `worktree remove` would delete: kept
(wt_ignored / ".git-info-exclude-probe").write_text("x", encoding="utf-8")
exclude = Path(git(wt_ignored, "rev-parse", "--git-path", "info/exclude").strip())
exclude = exclude if exclude.is_absolute() else wt_ignored / exclude
exclude.parent.mkdir(parents=True, exist_ok=True)
exclude.write_text(".git-info-exclude-probe\n__pycache__/\n", encoding="utf-8")
(wt_cache / "__pycache__").mkdir()
(wt_cache / "__pycache__" / "x.cpython-313.pyc").write_bytes(b"\0")
# Review 2026-09-25: user config must not hide an untracked file from the clean check.
git(PROJ, "config", "status.showUntrackedFiles", "no")

wt_unpushed = worktree("unpushed", "-b", "feat/unpushed")
commit(wt_unpushed, "unpushed.txt")
wt_open = worktree("open", "-b", "feat/open")
commit(wt_open, "open.txt")
git(wt_open, "push", "-q", "origin", "feat/open")
wt_detached = worktree("detached", "--detach")
wt_locked = worktree("locked", "-b", "feat/locked")
git(PROJ, "worktree", "lock", str(wt_locked))
wt_frozen = worktree("frozen", "-b", "feat/frozen")

ALL_WT = (wt_merged, wt_cache, wt_active, wt_skip, wt_fresh, wt_dirty, wt_ignored, wt_unpushed, wt_open, wt_detached)
for wt in (*ALL_WT, wt_locked, wt_frozen):
    age_tree(wt, 2 * 86400)
    age_gitdir(wt, 2 * 86400)
(wt_active / "README").touch()  # one file changed just now

leftover = WORK / "proj_wt_gone"
leftover.mkdir()
not_empty = WORK / "proj_backup"
not_empty.mkdir()
(not_empty / "keep.txt").write_text("x", encoding="utf-8")
other = WORK / "other_empty"
other.mkdir()
not_wt = WORK / "proj_data"  # empty, but not the guard's <repo>_wt_* shape: could be a mount point
not_wt.mkdir()

CONFIG.write_text(json.dumps({"shared": [str(PROJ)], "frozen": [str(wt_frozen)]}), encoding="utf-8")
worktrees_before = git(PROJ, "worktree", "list", "--porcelain")
EXPECTED = {
    PROJ: ("KEPT", "main"),
    wt_merged: ("REMOVABLE", "clean, pushed, merged, idle"),
    wt_dirty: ("KEPT", "dirty"),
    wt_ignored: ("KEPT", "ignored file .git-info-exclude-probe (remove would delete it)"),
    wt_cache: ("REMOVABLE", "clean, pushed, merged, idle"),
    wt_fresh: ("KEPT", "no commits of its own yet"),
    wt_skip: ("KEPT", "skip-worktree/assume-unchanged file skip.txt (status cannot see its edits)"),
    wt_unpushed: ("KEPT", "unpushed"),
    wt_open: ("KEPT", "not merged"),
    wt_active: ("KEPT", "active 0m ago"),
    wt_detached: ("KEPT", "detached"),
    wt_locked: ("KEPT", "locked"),
    wt_frozen: ("KEPT", "frozen"),
    leftover: ("LEFTOVER", "empty, not a registered worktree"),
}


def norm_key(p: str | Path) -> str:
    return str(Path(p).resolve()).lower()


def rows_by_path(stdout: str) -> dict[str, dict[str, str]]:
    return {norm_key(r["path"]): r for r in json.loads(stdout)["rows"]}


# --- dry run, JSON, repos from the config -------------------------------------------------------
r = cli("--json")
check("dry run --json exits 0", r.returncode == 0, r.stderr)
check("gh off -> exactly one stderr notice", r.stderr.count("LOST_MARY_NO_GH") == 1, r.stderr)
rows = rows_by_path(r.stdout) if r.returncode == 0 else {}
for path, (verdict, reason) in EXPECTED.items():
    got = rows.get(norm_key(path), {})
    check(
        f"dry run: {path.name} -> {verdict} ({reason})",
        (got.get("verdict"), got.get("reason")) == (verdict, reason),
        str(got),
    )
check("dry run: non-empty proj_backup not reported", norm_key(not_empty) not in rows, str(rows.keys()))
check("dry run: other_empty (not proj_*) not reported", norm_key(other) not in rows, str(rows.keys()))
check("dry run: empty proj_data (not proj_wt_*) not reported", norm_key(not_wt) not in rows, str(rows.keys()))
check("dry run: branch reported", rows.get(norm_key(wt_merged), {}).get("branch") == "feat/merged", str(rows))
summ = json.loads(r.stdout)["summary"] if r.returncode == 0 else {}
check(
    "dry run: summary counts",
    (summ.get("removable"), summ.get("leftover"), summ.get("removed"), summ.get("worktrees")) == (2, 1, 0, 13),
    str(summ),
)
check("dry run changed no worktree", git(PROJ, "worktree", "list", "--porcelain") == worktrees_before)
check("dry run kept the leftover dir", leftover.is_dir())

# --- text output, idle threshold, explicit repo path --------------------------------------------
r = cli(str(PROJ), "--idle-hours", "0")
check("text dry run exits 0", r.returncode == 0, r.stderr)
check("text: one line per entry + summary", len(r.stdout.strip().splitlines()) == len(EXPECTED) + 1, r.stdout)
check("text: summary says dry run", "(dry run - pass --apply to remove)" in r.stdout, r.stdout)
line = next((ln for ln in r.stdout.splitlines() if str(wt_active) in ln), "")
check("--idle-hours 0: the active worktree becomes removable", line.startswith("REMOVABLE"), line)

# --- bad arguments and an unreadable repo --------------------------------------------------------
check("--idle-hours -1 -> exit 2", cli("--idle-hours", "-1").returncode == 2)
check("unknown flag -> exit 2", cli("--bogus").returncode == 2)
r = cli(str(TMP / "not-a-repo"), str(PROJ), "--json")
check("unreadable repo: exit 1, stderr names it", r.returncode == 1 and "not-a-repo" in r.stderr, r.stderr)
check("unreadable repo: the summary counts it as skipped", json.loads(r.stdout)["summary"]["skipped"] == 1, r.stdout)
r = cli(str(PROJ), str(wt_open), "--json")  # two paths into one repository
check(
    "two paths into one repository are scanned once",
    r.returncode == 0 and json.loads(r.stdout)["summary"]["worktrees"] == 13,
    r.stdout[-400:],
)
check("unreadable repo: the next repo is still scanned", norm_key(wt_merged) in rows_by_path(r.stdout), r.stdout)

# --- PR lookup, injected (gh is never called in tests) --------------------------------------------
mod = load_module()
head_open = git(wt_open, "rev-parse", "HEAD").strip()
head_merged = git(wt_merged, "rev-parse", "HEAD").strip()
wt = next(w for w in mod.list_worktrees(PROJ) if Path(w.path).name == wt_open.name)
cases = [
    ("merged PR at HEAD -> removable", [mod.PR(7, "MERGED", head_open)], None),
    ("merged PR at HEAD into another base -> not merged", [mod.PR(7, "MERGED", head_open, "stack")], "not merged"),
    ("merged PR at HEAD from a fork -> not merged", [mod.PR(7, "MERGED", head_open, "main", True)], "not merged"),
    ("merged PR at HEAD into main -> removable", [mod.PR(7, "MERGED", head_open, "main")], None),
    ("merged PR at another commit -> not merged", [mod.PR(7, "MERGED", head_merged)], "not merged"),
    ("open PR -> open PR #8", [mod.PR(8, "OPEN", head_open)], "open PR #8"),
    ("gh failing (None) -> ancestry only", None, "not merged"),
]
for label, prs, want in cases:
    got = mod.judge(wt, False, [], 6.0, lambda _cwd, _branch, prs=prs: prs)
    check(f"judge: {label}", got == want, repr(got))
wt_m = next(w for w in mod.list_worktrees(PROJ) if Path(w.path).name == wt_merged.name)
got = mod.judge(wt_m, False, [], 6.0, lambda _cwd, _branch: [mod.PR(9, "OPEN", "x")])
check("judge: an open PR keeps even an ancestor of origin/HEAD", got == "open PR #9", repr(got))
age_gitdir(wt_merged, 0)  # a session's `git status` or commit touches these; the files stay two days old
got = mod.judge(wt_m, False, [], 6.0, lambda _cwd, _branch: None)
check("judge: fresh git records alone keep a merged worktree", str(got).startswith("active"), repr(got))
age_gitdir(wt_merged, 2 * 86400)
wt_f = next(w for w in mod.list_worktrees(PROJ) if Path(w.path).name == wt_fresh.name)
got = mod.judge(
    wt_f, False, [], 6.0, lambda _cwd, _branch: [mod.PR(3, "MERGED", git(wt_fresh, "rev-parse", "HEAD").strip())]
)
check("judge: a merged PR whose head is HEAD removes even a branch without own reflog", got is None, repr(got))
check(
    "disposable(): caches yes, data no",
    mod.disposable("__pycache__/")
    and mod.disposable("a/b.pyc")
    and not mod.disposable(".env")
    and not mod.disposable("runner_state.sqlite3"),
)

# --- apply ------------------------------------------------------------------------------------------
r = cli("--apply")
check("--apply exits 0", r.returncode == 0, r.stderr)
check("--apply reports REMOVED lines", r.stdout.count("REMOVED") == 3, r.stdout)
check("--apply removed the merged worktree", not wt_merged.exists())
check("--apply removed the worktree holding only an ignored cache", not wt_cache.exists())
check("--apply kept the worktree holding an ignored non-cache file", (wt_ignored / ".git-info-exclude-probe").exists())
for kept in (wt_dirty, wt_unpushed, wt_open, wt_active, wt_fresh, wt_detached, wt_locked, wt_frozen, PROJ):
    check(f"--apply kept {kept.name}", kept.is_dir())
check("--apply kept the branch locally", "feat/merged" in git(PROJ, "branch", "--list", "feat/merged"))
check("--apply kept the branch on origin", "feat/merged" in git(PROJ, "ls-remote", "origin", "feat/merged"))
listed = git(PROJ, "worktree", "list", "--porcelain")
check("--apply unregistered the removed worktree", "proj_wt_merged" not in listed, listed)
check("--apply removed the empty leftover", not leftover.exists())
check("--apply kept the non-empty sibling", (not_empty / "keep.txt").exists())
check("--apply kept other_empty", other.is_dir())
check("--apply kept the empty proj_data (not proj_wt_*)", not_wt.is_dir())
check(
    "--apply kept the skip-worktree edit",
    (wt_skip / "skip.txt").read_text(encoding="utf-8") == "edited where status cannot see it",
)
check("--apply did not run `git worktree prune`", "prunable" not in git(PROJ, "worktree", "list", "--porcelain"))
r = cli("--json")
summ = json.loads(r.stdout)["summary"] if r.returncode == 0 else {}
check("second dry run: nothing left to remove", (summ.get("removable"), summ.get("leftover")) == (0, 0), str(summ))

# --- no config and no repos -------------------------------------------------------------------------
CONFIG.unlink()
r = cli()
check("no config, no repos: exit 0, nothing scanned", r.returncode == 0 and r.stdout.startswith("0 repos"), r.stdout)

git(PROJ, "worktree", "unlock", str(wt_locked))
shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print(f"\n{len(failures)} failed:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("\nall passed")
