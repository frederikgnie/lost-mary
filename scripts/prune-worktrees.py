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
  1. clean     - `git status --porcelain --ignored --untracked-files=all
                 --ignore-submodules=none` shows nothing outside the cache allowlist
                 (.venv, __pycache__, .pytest_cache, .ruff_cache, .mypy_cache,
                 node_modules, *.pyc): `git worktree remove` deletes ignored files,
                 so an ignored .env or runner_state.sqlite3 keeps the worktree. The
                 flags override status.showUntrackedFiles and submodule ignore config;
  2. pushed    - HEAD is in some remote-tracking branch (`git branch -r --contains`);
  3. merged    - a merged PR for the branch has headRefOid == HEAD (`gh pr list`),
                 or HEAD is an ancestor of `origin/HEAD` AND the branch has a commit
                 of its own (its reflog holds more than "Created from" - a worktree
                 just added with -b is an ancestor of main and has done nothing yet).
                 Without gh (missing,
                 failing, or LOST_MARY_NO_GH=1) only the ancestor test counts;
                 an open PR for the branch keeps the worktree either way;
  4. idle      - no file under it (outside .git, .venv and tool caches) and none
                 of git's own records for it (index, HEAD, logs/HEAD in its gitdir,
                 touched by a session's `git status` or commit) changed within
                 --idle-hours (default 24);
  5. no session - no transcript under ~/.claude/projects ($LOST_MARY_PROJECTS_DIR)
                 names it in its last SESSION_TAIL bytes - as a record's `cwd` or
                 inside a tool call's input, by absolute path (either slash form)
                 or by folder name after `/`, a space or a quote - counting every
                 transcript changed within --idle-hours AND, at any age, those of
                 a RUNNING session (~/.claude/sessions/<pid>.json with a live
                 pid, $LOST_MARY_SESSIONS_DIR; observed 2.1.283), whose own `cwd`
                 counts too: a session left open in a worktree writes nothing
                 while it waits, and removing the directory under it (on Windows
                 its handle stops the remove halfway) strands it. Tool results
                 are not read, so a listing that merely prints the path (`git
                 worktree list`, a dry run of this CLI) does not count.
"pushed" trusts the local remote-tracking refs (no fetch); a stale ref can only
say "pushed" for a commit the remote had when last fetched - the branch is kept
either way.
Never considered: the main worktree, a detached HEAD, a locked or missing
worktree, anything at or under a `frozen` root. Every KEPT line names the first
reason that failed.

--apply runs `git -C <main> worktree remove <path>` (never --force) for each
REMOVABLE worktree, right after judging it, and keeps its branch (local and
remote). It never runs `git worktree prune`: that clears the record of EVERY
missing worktree at once, including one on an unmounted drive or one a session
moved, which `git worktree repair` could relink. It also rmdirs EMPTY
directories named `<repo>_wt_*` (the guard's convention) next to a scanned repo
that are not registered worktrees, re-listed just before the sweep - the
leftovers of a remove a Windows file lock interrupted. A dry run reports them.

"merged" through gh means a PR from the same repository (not a fork), merged
into any branch, whose head is HEAD - a feature or integration branch counts,
since the branch itself is kept. Tracked files marked
skip-worktree or assume-unchanged keep the worktree, since status cannot see
edits to them.

Exit 0 when every repo was scanned and nothing FAILED, 1 otherwise, 2 on bad
arguments. A repo that cannot be read gets one line on stderr and the scan
continues.
"""

from __future__ import annotations

import argparse
import functools
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
# Ignored paths `git worktree remove` may delete without losing anything: rebuildable caches only.
DISPOSABLE = frozenset({".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"})
DISPOSABLE_SUFFIXES = (".pyc", ".pyo")
DEFAULT_IDLE_HOURS = 24.0
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}  # a scan must not take index locks other sessions contend for
SESSION_TAIL = 512 * 1024  # bytes read from the end of each recent transcript


def norm_path(text: str) -> str:
    """A path or text with every slash form folded to `/` and lower-cased, so mentions compare as substrings."""
    return text.replace("\\\\", "/").replace("\\", "/").lower()


def pid_alive(pid: int) -> bool:
    """Whether a process runs with this pid; an answer it cannot get reads as alive (that only keeps more).

    Never os.kill on Windows: signal 0 is CTRL_C_EVENT there (Ctrl+C to the console), any other value terminates.
    """
    if pid <= 0:
        return False
    if pid > 0xFFFFFFFF:
        return True  # not a pid any platform hands out; a malformed registry file must not abort the scan
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists, owned by someone else
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return True
    return True


def live_sessions() -> tuple[set[str], list[str]]:
    """(sessionIds, cwds) of running Claude Code sessions, from `~/.claude/sessions/<pid>.json` (observed, 2.1.283)."""
    root = Path(os.environ.get("LOST_MARY_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")
    ids: set[str] = set()
    cwds: list[str] = []
    for path in root.glob("*.json") if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if not isinstance(data, dict):
            continue
        pid, sid = data.get("pid"), data.get("sessionId")
        if isinstance(pid, int) and isinstance(sid, str) and pid_alive(pid):
            ids.add(sid)
            if isinstance(cwd := data.get("cwd"), str):
                cwds.append(norm_path(cwd))
    return ids, cwds


@functools.cache
def session_mentions(idle_hours: float) -> tuple[str, ...]:
    """The `cwd` values and tool-call inputs, normalised, of every transcript changed within the window and of
    every running session's transcripts at any age - a session left open writes nothing while it waits. Once per run.
    """
    root = Path(os.environ.get("LOST_MARY_PROJECTS_DIR") or Path.home() / ".claude" / "projects")
    since = time.time() - idle_hours * 3600
    live, found = live_sessions()
    for path in root.glob("**/*.jsonl") if root.is_dir() else []:
        try:
            # <project>/<sessionId>.jsonl, and a subagent's <project>/<sessionId>/subagents/agent-<id>.jsonl
            running = path.stem in live or (path.parent.name == "subagents" and path.parent.parent.name in live)
            if not running and path.stat().st_mtime < since:
                continue
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - SESSION_TAIL))
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
            if size > SESSION_TAIL:
                lines = lines[1:]  # the read began mid-record
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(record, dict):
                continue
            if isinstance(cwd := record.get("cwd"), str):
                found.append(norm_path(cwd))
            message = record.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    found.append(norm_path(json.dumps(block.get("input"), ensure_ascii=False)))
    return tuple(found)


def in_session(path: Path, idle_hours: float) -> bool:
    """Whether a recent session names this worktree (a false match only keeps more)."""
    p = norm_path(str(path)).rstrip("/")
    forms = {p}
    if len(p) > 2 and p[1] == ":":  # C:/repo/x as Git Bash writes it: /c/repo/x
        forms.add(f"/{p[0]}{p[2:]}")
    base = p.rsplit("/", 1)[-1]
    forms |= {f"/{base}", f" {base}", f'"{base}', f"'{base}"}  # relative: `cd ../x`, `cd x`, `cd "x"`, `cd 'x'`
    return any(form in text for text in session_mentions(idle_hours) for form in forms)


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
    """At or under a root, by the path as given or as resolved (junctions, subst drives, short names, links)."""
    keys = {key(path)}
    try:
        keys.add(key(Path(path).resolve()))
    except OSError:
        pass
    return any(k[: len(r)] == r for k in keys for r in roots if r)


def git(cwd: Path, *args: str, timeout: float | None = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=GIT_ENV,
        timeout=timeout,
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
    base: str = ""  # "" = unknown (an injected lookup); gh always fills it
    cross_repo: bool = False


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
        fields = "number,state,headRefOid,baseRefName,isCrossRepository"
        cmd = ["gh", "pr", "list", "--head", branch, "--state", "all", "--json", fields]
        try:
            r = subprocess.run(
                cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[0])
            rows = json.loads(r.stdout)
            return [
                PR(
                    int(x["number"]),
                    str(x["state"]),
                    str(x["headRefOid"]),
                    str(x.get("baseRefName") or ""),
                    bool(x.get("isCrossRepository")),
                )
                for x in rows
            ]
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError, KeyError, TypeError) as exc:
            # Not switched off for later repos: the active gh account may see one repository and not another.
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
        try:  # a deletion changes only its directory's mtime
            newest = max(newest, Path(dirpath).stat().st_mtime)
        except OSError:
            pass
        for name in filenames:
            if name in SKIP_DIRS:  # a linked worktree's .git is a file
                continue
            try:
                newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
            except OSError:
                continue
    return newest


def git_activity(wt: Path) -> float:
    """The newest mtime of git's own records for a linked worktree: its gitdir's index, HEAD and logs/HEAD."""
    r = git(wt, "rev-parse", "--absolute-git-dir")
    if r.returncode != 0 or not r.stdout.strip():
        return 0.0
    gitdir = Path(r.stdout.strip())
    newest = 0.0
    for name in ("index", "HEAD", "logs/HEAD"):
        try:
            newest = max(newest, (gitdir / name).stat().st_mtime)
        except OSError:
            continue
    return newest


def disposable(path: str) -> bool:
    """An ignored path that is only a rebuildable cache."""
    parts = [x for x in path.strip().strip('"').replace(chr(92), "/").split("/") if x]
    return any(x in DISPOSABLE for x in parts) or path.strip().rstrip("/").endswith(DISPOSABLE_SUFFIXES)


def hidden_edits(wt: Path) -> str | None:
    """A tracked file whose edits status cannot see: skip-worktree (S) or assume-unchanged (lowercase tag)."""
    r = git(wt, "ls-files", "-v")
    if r.returncode != 0:
        return "git ls-files failed"
    for line in r.stdout.splitlines():
        tag, _, name = line.partition(" ")
        if tag == "S" or (tag.isalpha() and tag.islower()):
            return name
    return None


def own_commits(wt: Worktree) -> bool:
    """Whether the branch ever got a commit of its own: its reflog holds more than the "Created from" entry.

    No reflog at all (expired, or a branch fetched rather than created here) cannot show work was done, so it
    reads False and the ancestry route keeps the worktree; a merged PR whose head is HEAD still removes it.
    """
    r = git(wt.path, "reflog", "show", "--format=%gs", f"refs/heads/{wt.branch}", "--")
    if r.returncode != 0:
        return False
    return any(line.strip() and not line.startswith("branch: Created from") for line in r.stdout.splitlines())


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
        return "missing (run `git worktree prune` yourself if it is really gone)"
    status = git(wt.path, "status", "--porcelain", "--ignored", "--untracked-files=all", "--ignore-submodules=none")
    if status.returncode != 0:
        return f"git status failed: {first_line(status)}"
    for line in status.stdout.splitlines():
        code, entry = line[:2], line[3:]
        if code == "!!":
            if not disposable(entry):
                return f"ignored file {entry.strip()} (remove would delete it)"
        elif line.strip():
            return "dirty"
    if hidden := hidden_edits(wt.path):
        return f"skip-worktree/assume-unchanged file {hidden} (status cannot see its edits)"
    contains = git(wt.path, "branch", "-r", "--contains", wt.head)
    if contains.returncode != 0 or not contains.stdout.strip():
        return "unpushed"
    found = prs(wt.path, wt.branch)
    if found and (open_pr := next((p for p in found if p.state.upper() == "OPEN"), None)):
        return f"open PR #{open_pr.number}"
    # Any base counts - a feature or integration branch too: the branch itself is kept, so removing its worktree
    # loses nothing, and the EU repos merge into feature branches (a default-branch rule held all six, 2026-09-29).
    merged = found is not None and any(
        p.state.upper() == "MERGED" and p.head_oid == wt.head and not p.cross_repo for p in found
    )
    if not merged:
        ancestor = git(wt.path, "merge-base", "--is-ancestor", wt.head, "origin/HEAD")
        if ancestor.returncode != 0:
            return "not merged"
        if not own_commits(wt):
            return "no commits of its own yet"
    age = time.time() - max(newest_mtime(wt.path), git_activity(wt.path))
    if age < idle_hours * 3600:
        return f"active {fmt_age(max(age, 0.0))} ago"
    if in_session(wt.path, idle_hours):
        return f"a Claude session worked here within {idle_hours:g} h"
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
    for i, wt in enumerate(listed):
        try:
            reason = judge(wt, i == 0, cfg.frozen, idle_hours, prs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f"check failed ({exc!r})"
        row = Row(str(main), str(wt.path), wt.branch or "-", "KEPT" if reason else "REMOVABLE", reason or "")
        if reason is None:
            row.reason = "clean, pushed, merged, idle"
            if apply:
                try:  # no timeout: killing git mid-delete leaves a half-removed, still registered tree
                    r = git(main, "worktree", "remove", str(wt.path), timeout=None)
                    if r.returncode == 0:
                        row.verdict = "REMOVED"
                    else:
                        row.verdict, row.reason = "FAILED", first_line(r)
                except OSError as exc:
                    row.verdict, row.reason = "FAILED", f"worktree remove failed ({exc!r})"
        rows.append(row)
    return rows, main


def leftovers(mains: list[Path], registered: set[tuple[str, ...]], cfg: Config, apply: bool) -> list[Row]:
    """Empty `<repo>_wt_*` directories next to a scanned repo that no scanned repo has registered."""
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
            if k in seen or not entry.name.lower().startswith(main.name.lower() + "_wt_"):
                continue
            seen.add(k)
            junction = getattr(entry, "is_junction", lambda: False)()  # Path.is_junction is 3.12+; CI runs 3.11
            if entry.is_symlink() or junction or not entry.is_dir() or under(entry, cfg.frozen):
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


def common_dir(repo: Path) -> tuple[str, ...] | None:
    """The repository's shared git dir, so two paths into one repository are scanned once."""
    try:
        r = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return key(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def run(repos: list[Path], idle_hours: float, apply: bool, prs: PrLookup) -> tuple[list[Row], int, int]:
    """(rows, repos scanned, repos skipped)."""
    cfg = load_config()
    if not repos:
        repos = [Path(p) for p in cfg.shared]
    rows: list[Row] = []
    mains: list[Path] = []
    done: set[tuple[str, ...]] = set()
    skipped = 0
    for repo in repos:
        common = common_dir(repo)
        if common is not None and common in done:
            continue
        try:
            result = scan_repo(repo, cfg, idle_hours, apply, prs)
        except Exception as exc:  # one repo's failure never aborts the others
            notice(f"{repo}: scan failed ({exc!r}) - skipped")
            skipped += 1
            continue
        if result is None:
            skipped += 1
            continue
        if common is not None:
            done.add(common)
        repo_rows, main = result
        mains.append(main)
        rows.extend(repo_rows)
    registered = {key(r.path) for r in rows}
    for main in mains:  # re-listed: a `git worktree add` since the scan started owns its (still empty) directory
        listed = list_worktrees(main)
        if not isinstance(listed, str):
            registered |= {key(w.path) for w in listed}
    rows.extend(leftovers(mains, registered, cfg, apply))
    return rows, len(mains), skipped


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
    ap.add_argument(
        "--idle-hours",
        type=float,
        default=DEFAULT_IDLE_HOURS,
        help="a file or git record changed more recently keeps it (default 24)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    if args.idle_hours < 0:
        ap.error("--idle-hours must be >= 0")
    return args


def main(argv: list[str] | None = None, prs: PrLookup | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    rows, repos, skipped = run(args.repos, args.idle_hours, args.apply, prs or GhLookup())
    totals = summary(rows, repos, args.apply) | {"skipped": skipped}
    code = 1 if skipped or totals["failed"] else 0
    if args.json:
        print(json.dumps({"rows": [asdict(r) for r in rows], "summary": totals}, indent=2))
        return code
    for r in rows:
        print(f"{r.verdict:<9}  {r.path}  {r.branch}  {r.reason}")
    tail = "" if args.apply else " (dry run - pass --apply to remove)"
    print(
        f"{totals['repos']} repos, {len(rows)} entries: {totals['removable']} removable, {totals['removed']} removed, "
        f"{totals['failed']} failed, {totals['kept']} kept, {totals['leftover']} empty leftover dirs, "
        f"{skipped} repos skipped{tail}"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
