#!/usr/bin/env python3
"""prune-worktrees: find sibling worktrees that are provably safe to remove; remove them only with --apply.

Why this exists
---------------
guard-shared-checkouts tells every session to work in a sibling worktree
(`git -C <repo> worktree add <repo>_wt_<name> -b <branch>`), and nothing ever
removes one, so the parent directory fills with stale worktrees. This CLI reads
first: a dry run lists every linked worktree with a verdict and changes nothing.

Repos scanned: the positional paths, else the `shared` list of the guard's
config (`$LOST_MARY_SHARED_CHECKOUTS`, else
`~/.claude/agent-library/shared-checkouts.json`; same loader as the guard).
The config's `frozen` roots are honoured either way.

A worktree is REMOVABLE only when all of these hold:
  1. clean     - `git status --porcelain` is empty (ignored files do not count);
  2. pushed    - HEAD is in some remote-tracking branch (`git branch -r --contains`);
  3. merged    - a merged PR for the branch has headRefOid == HEAD (`gh pr list`),
                 or HEAD is an ancestor of `origin/HEAD`. Without gh (missing,
                 failing, or LOST_MARY_NO_GH=1) only the ancestor test counts;
                 an open PR for the branch keeps the worktree either way;
  4. idle      - no file under it (outside .git, .venv and tool caches) changed
                 within --idle-hours (default 6).
Never considered: the main worktree, a detached HEAD, a locked or missing
worktree, anything at or under a `frozen` root. Every KEPT line names the first
reason that failed.

--apply runs `git -C <main> worktree remove <path>` (never --force) for each
REMOVABLE worktree, right after judging it, keeps its branch (local and
remote), then `git worktree prune`. It also rmdirs EMPTY directories named
`<repo>_*` next to a scanned repo that are not registered worktrees - the
leftovers of a remove a Windows file lock interrupted. A dry run reports them.

Exit 0 always, 2 on bad arguments. A repo that cannot be read gets one line on
stderr and the scan continues.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

NO_GH_ENV = "LOST_MARY_NO_GH"
SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # a scan must not take index locks other sessions contend for


def notice(text: str) -> None:
    print(f"prune-worktrees: {text}", file=sys.stderr)


def load_guard() -> ModuleType:
    """The guard module, for its config loader and path matching - one definition of both."""
    path = Path(__file__).resolve().parent / "guard-shared-checkouts.py"
    spec = importlib.util.spec_from_file_location("guard_shared_checkouts", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # registered before exec_module (see AGENTS.md)
    spec.loader.exec_module(module)
    return module


GUARD = load_guard()


@dataclass
class Config:
    shared: list[str]
    frozen: list[tuple[str, ...]]


def load_config() -> Config:
    """shared paths and frozen keys; empty when the file is missing, with a notice when it is broken."""
    path: Path = GUARD.config_path()
    if not path.exists():
        return Config([], [])
    try:
        raw = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        notice(f"cannot read {path} ({type(exc).__name__}) - no config")
        return Config([], [])
    parsed = GUARD.parse_config(raw)
    if isinstance(parsed, str):
        notice(f"{path}: {parsed} - no config")
        return Config([], [])
    return Config([d.shown for d in parsed.shared], [d.key for d in parsed.frozen])


def key(path: str | Path) -> tuple[str, ...]:
    p = GUARD.norm(str(path), GUARD.norm(str(Path.cwd())))
    return GUARD.key_of(p) if p is not None else ()


def under(path: str | Path, roots: list[tuple[str, ...]]) -> bool:
    k = key(path)
    return any(k[: len(r)] == r for r in roots if r)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=GIT_ENV,
        timeout=120,
    )


@dataclass
class Worktree:
    path: Path
    head: str = ""
    branch: str = ""
    detached: bool = False
    locked: bool = False
    prunable: bool = False


def list_worktrees(repo: Path) -> list[Worktree] | str:
    """The repo's worktrees, main first, or a one-line reason they cannot be listed."""
    try:
        r = git(repo, "worktree", "list", "--porcelain")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"git worktree list failed ({exc!r})"
    if r.returncode != 0:
        return (r.stderr.strip().splitlines() or [f"git exited {r.returncode}"])[0]
    out: list[Worktree] = []
    for line in r.stdout.splitlines():
        word, _, value = line.partition(" ")
        match word:
            case "worktree":
                out.append(Worktree(Path(value)))
            case "HEAD" if out:
                out[-1].head = value
            case "branch" if out:
                out[-1].branch = value.removeprefix("refs/heads/")
            case "detached" if out:
                out[-1].detached = True
            case "locked" if out:
                out[-1].locked = True
            case "prunable" if out:
                out[-1].prunable = True
    return out or "git worktree list printed nothing"


@dataclass
class PR:
    number: int
    state: str
    head_oid: str


PrLookup = Callable[[Path, str], list[PR] | None]


class GhLookup:
    """`gh pr list --head <branch>`; None when gh is off, missing or failing (one notice per run)."""

    def __init__(self) -> None:
        self.off = bool(os.environ.get(NO_GH_ENV))
        self.warned = False

    def warn(self, why: str) -> None:
        if not self.warned:
            notice(f"{why} - 'merged' means an ancestor of origin/HEAD only")
            self.warned = True

    def __call__(self, cwd: Path, branch: str) -> list[PR] | None:
        if self.off:
            self.warn(f"gh disabled by {NO_GH_ENV}")
            return None
        cmd = ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "number,state,headRefOid"]
        try:
            r = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[0])
            rows = json.loads(r.stdout)
            return [PR(int(x["number"]), str(x["state"]), str(x["headRefOid"])) for x in rows]
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, KeyError, TypeError) as exc:
            self.off = True
            self.warn(f"gh unavailable ({exc})")
            return None


def fmt_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def newest_mtime(root: Path) -> float:
    newest = 0.0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_DIRS:  # a linked worktree's .git is a file
                continue
            try:
                newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
            except OSError:
                continue
    return newest


def first_line(r: subprocess.CompletedProcess[str]) -> str:
    return (r.stderr.strip().splitlines() or r.stdout.strip().splitlines() or [f"exit {r.returncode}"])[0]


def judge(wt: Worktree, is_main: bool, frozen: list[tuple[str, ...]], idle_hours: float, prs: PrLookup) -> str | None:
    """None when `wt` is REMOVABLE, else the first reason to keep it."""
    if is_main:
        return "main"
    if under(wt.path, frozen):
        return "frozen"
    if wt.detached or not wt.branch:
        return "detached"
    if wt.locked:
        return "locked"
    if wt.prunable or not wt.path.is_dir():
        return "missing (git worktree prune clears it)"
    status = git(wt.path, "status", "--porcelain")
    if status.returncode != 0:
        return f"git status failed: {first_line(status)}"
    if status.stdout.strip():
        return "dirty"
    contains = git(wt.path, "branch", "-r", "--contains", wt.head)
    if contains.returncode != 0 or not contains.stdout.strip():
        return "unpushed"
    found = prs(wt.path, wt.branch)
    if found and (open_pr := next((p for p in found if p.state.upper() == "OPEN"), None)):
        return f"open PR #{open_pr.number}"
    merged = found is not None and any(p.state.upper() == "MERGED" and p.head_oid == wt.head for p in found)
    if not merged:
        ancestor = git(wt.path, "merge-base", "--is-ancestor", wt.head, "origin/HEAD")
        if ancestor.returncode != 0:
            return "not merged"
    age = time.time() - newest_mtime(wt.path)
    if age < idle_hours * 3600:
        return f"active {fmt_age(max(age, 0.0))} ago"
    return None


@dataclass
class Row:
    repo: str
    path: str
    branch: str
    verdict: str  # REMOVABLE | KEPT | REMOVED | FAILED | LEFTOVER
    reason: str
    kind: str = "worktree"  # worktree | leftover


def scan_repo(repo: Path, cfg: Config, idle_hours: float, apply: bool, prs: PrLookup) -> tuple[list[Row], Path] | None:
    listed = list_worktrees(repo)
    if isinstance(listed, str):
        notice(f"{repo}: {listed} - skipped")
        return None
    main = listed[0].path
    rows: list[Row] = []
    removed = False
    for i, wt in enumerate(listed):
        try:
            reason = judge(wt, i == 0, cfg.frozen, idle_hours, prs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f"check failed ({exc!r})"
        row = Row(str(main), str(wt.path), wt.branch or "-", "KEPT" if reason else "REMOVABLE", reason or "")
        if reason is None:
            row.reason = "clean, pushed, merged, idle"
            if apply:
                r = git(main, "worktree", "remove", str(wt.path))
                if r.returncode == 0:
                    row.verdict, removed = "REMOVED", True
                else:
                    row.verdict, row.reason = "FAILED", first_line(r)
        rows.append(row)
    if apply and removed:
        r = git(main, "worktree", "prune")
        if r.returncode != 0:
            notice(f"{main}: git worktree prune failed: {first_line(r)}")
    return rows, main


def leftovers(mains: list[Path], registered: set[tuple[str, ...]], cfg: Config, apply: bool) -> list[Row]:
    """Empty `<repo>_*` directories next to a scanned repo that no scanned repo has registered."""
    rows: list[Row] = []
    seen: set[tuple[str, ...]] = set(registered) | {key(p) for p in cfg.shared}
    for main in mains:
        parent = main.parent
        try:
            entries = sorted(parent.iterdir())
        except OSError as exc:
            notice(f"cannot list {parent} ({type(exc).__name__})")
            continue
        for entry in entries:
            k = key(entry)
            if k in seen or not entry.name.lower().startswith(main.name.lower() + "_"):
                continue
            seen.add(k)
            if entry.is_symlink() or entry.is_junction() or not entry.is_dir() or under(entry, cfg.frozen):
                continue
            try:
                if any(entry.iterdir()):
                    continue
            except OSError:
                continue
            row = Row(str(main), str(entry), "-", "LEFTOVER", "empty, not a registered worktree", "leftover")
            if apply:
                try:
                    entry.rmdir()
                    row.verdict = "REMOVED"
                except OSError as exc:
                    row.verdict, row.reason = "FAILED", f"rmdir failed ({type(exc).__name__})"
            rows.append(row)
    return rows


def run(repos: list[Path], idle_hours: float, apply: bool, prs: PrLookup) -> tuple[list[Row], int]:
    cfg = load_config()
    if not repos:
        repos = [Path(p) for p in cfg.shared]
    rows: list[Row] = []
    mains: list[Path] = []
    done: set[tuple[str, ...]] = set()
    for repo in repos:
        result = scan_repo(repo, cfg, idle_hours, apply, prs)
        if result is None:
            continue
        repo_rows, main = result
        if key(main) in done:
            continue
        done.add(key(main))
        mains.append(main)
        rows.extend(repo_rows)
    registered = {key(r.path) for r in rows}
    rows.extend(leftovers(mains, registered, cfg, apply))
    return rows, len(mains)


def summary(rows: list[Row], repos: int, apply: bool) -> dict[str, int | bool]:
    count = {v: sum(1 for r in rows if r.verdict == v) for v in ("REMOVABLE", "KEPT", "REMOVED", "FAILED", "LEFTOVER")}
    worktrees = sum(1 for r in rows if r.kind == "worktree")
    return {"repos": repos, "rows": len(rows), "worktrees": worktrees, "applied": apply} | {
        k.lower(): v for k, v in count.items()
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="prune-worktrees",
        description="List linked worktrees that are clean, pushed, merged and idle; remove them only with --apply.",
    )
    ap.add_argument("repos", nargs="*", type=Path, help="repos to scan (default: `shared` in the guard's config)")
    ap.add_argument("--apply", action="store_true", help="remove REMOVABLE worktrees and empty leftover dirs")
    ap.add_argument("--idle-hours", type=float, default=6.0, help="a file changed more recently keeps it (default 6)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if args.idle_hours < 0:
        ap.error("--idle-hours must be >= 0")
    return args


def main(argv: list[str] | None = None, prs: PrLookup | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    rows, repos = run(args.repos, args.idle_hours, args.apply, prs or GhLookup())
    totals = summary(rows, repos, args.apply)
    if args.json:
        print(json.dumps({"rows": [asdict(r) for r in rows], "summary": totals}, indent=2))
        return 0
    for r in rows:
        print(f"{r.verdict:<9}  {r.path}  {r.branch}  {r.reason}")
    tail = "" if args.apply else " (dry run - pass --apply to remove)"
    print(
        f"{totals['repos']} repos, {len(rows)} entries: {totals['removable']} removable, {totals['removed']} removed, "
        f"{totals['failed']} failed, {totals['kept']} kept, {totals['leftover']} empty leftover dirs{tail}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
