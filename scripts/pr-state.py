#!/usr/bin/env python3
"""pr-state: a pull request's review, recorded against its head commit, so any session can merge it.

Why this exists
---------------
guard-shared-checkouts holds back a `gh pr merge` until a `review` agent has run in
the SAME session, and the auto-mode allow rule covers only a PR the session opened.
So when one session finished and reviewed a PR and another session was asked to
consolidate it, the second one could not merge: its transcript holds no review.
Reviewing again costs a full review per hand-over; the first review is evidence
that only lived in the first session's transcript.

This records it where every session can read it: one line per stamp in
`<state>/reviews.jsonl` (`$LOST_MARY_STATE_DIR`, else
`~/.claude/agent-library/state`), naming the repository, the PR number and the
head commit the review covered. A merge of that PR passes the guard - and the
guard tells auto mode it may run - while the PR's head on GitHub is still that
commit. A push after the stamp voids it: the new commits are unreviewed.

  python pr-state.py reviewed <n> [-R owner/repo] [--tests quick|full|none]
      Stamp PR <n> as reviewed at its current head. Run it in the PR branch's
      worktree, after the review's findings are fixed and pushed: the local HEAD
      must be the PR's head and no tracked file may be changed, so the stamp names
      the tree the session reviewed. The guard lets this command run only in a
      session that spawned `review` since its last merge of another PR - the same
      evidence a merge needs. `--tests` says which tier ran (informational).
  python pr-state.py status <n> [-R owner/repo]
      The PR's head, base, review stamp and whether its base is a merged branch.
  python pr-state.py show [-R owner/repo] [--limit N]
      The latest stamp per PR, newest first.

A merged base. A PR whose base is a feature branch that was itself merged lands
its commits on a branch nothing will merge again - they never reach main.
stale_branch() names one: a PR from it (same repository, not a fork) MERGED,
none OPEN, and its tip - the local `origin/<b>` ref in a clone of that
repository - is still the head that merged. A branch that took commits after
its merge is in use; without a clone to read the tip from nothing is judged.
origin/HEAD's branch, long-lived names (main, master, develop, dev, trunk,
staging, qa, integration, release-*, hotfix-*) and any branch the caller passes
as `keep` (the guard's `homes`) are never stale. The guard holds such a merge
and `gh pr create --base <that branch>`. A stamp also names the base it was
reviewed against; a retargeted PR needs a new one.

gh is the only source of a PR's head and base. Without it (missing, failing, the
wrong account active, or LOST_MARY_NO_GH=1) nothing is recorded and nothing is
looked up: the guard falls back to the session's own review. Exit 0 on success,
1 when gh or git cannot answer, 2 on a refused stamp or bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_ENV = "LOST_MARY_STATE_DIR"
NO_GH_ENV = "LOST_MARY_NO_GH"
GH_CMD_ENV = "LOST_MARY_GH"  # the gh command, shlex-split (tests point it at a fake)
RECORDS = "reviews.jsonl"
GH_TIMEOUT = 8.0
DEADLINE: float | None = None  # time.monotonic() by which every gh / git call must end (the guard sets it)
NOTES: list[str] = []  # why a lookup came back empty when that was not "no such thing" - the guard prints them
TIERS = ("quick", "full", "none")
LONG_LIVED = re.compile(r"(?:main|master|develop|dev|trunk|staging|qa|integration|(?:release|hotfix)[/-].+)", re.I)
PR_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Pr:
    repo: str  # owner/repo, lower case
    number: str
    head: str
    branch: str
    base: str
    state: str  # OPEN | MERGED | CLOSED
    url: str


def state_dir() -> Path:
    return Path(os.environ.get(STATE_ENV) or Path.home() / ".claude" / "agent-library" / "state")


def records_path() -> Path:
    return state_dir() / RECORDS


def time_left(cap: float) -> float:
    """Seconds the next call may take: `cap`, or less when DEADLINE is near. 0 or below means do not start."""
    return cap if DEADLINE is None else min(cap, DEADLINE - time.monotonic())


def gh_json(args: list[str], cwd: str | None = None) -> Any:
    """gh's JSON output, or None when gh is off, missing, failing or slow."""
    left = time_left(GH_TIMEOUT)
    if os.environ.get(NO_GH_ENV) == "1":
        return None
    if left <= 0:
        NOTES.append(f"the gh time budget was spent before `gh {' '.join(args[:2])}` - that check was skipped")
        return None
    try:
        done = subprocess.run(
            [*(shlex.split(os.environ.get(GH_CMD_ENV, "")) or ["gh"]), *args],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=left,
        )
    except subprocess.TimeoutExpired:
        NOTES.append(f"`gh {' '.join(args[:2])}` ran out of time ({left:.1f} s) - that check was skipped")
        return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        return None


GH: Callable[[list[str], str | None], Any] = gh_json  # tests replace this


def drain_notes() -> list[str]:
    """The notes gathered since the last call, each once."""
    notes = list(dict.fromkeys(NOTES))
    NOTES.clear()
    return notes


def view(number: str, repo: str | None = None, cwd: str | None = None) -> Pr | None:
    """PR `number` (of `repo`, else of the repository gh finds from `cwd`), or None when gh cannot say."""
    args = ["pr", "view", number, "--json", "number,url,headRefOid,headRefName,baseRefName,state"]
    data = GH([*args, "-R", repo] if repo else args, cwd)
    if not isinstance(data, dict):
        return None
    url = str(data.get("url") or "")
    found = PR_URL.search(url)
    head = str(data.get("headRefOid") or "").lower()
    if not found or not SHA.fullmatch(head):
        return None
    return Pr(
        found.group(1).lower(),
        found.group(2),
        head,
        str(data.get("headRefName") or ""),
        str(data.get("baseRefName") or ""),
        str(data.get("state") or "").upper(),
        url,
    )


def same_repo(cwd: Path, repo: str) -> bool:
    """Whether the checkout at `cwd` has `repo` (owner/repo) as its origin."""
    url = (git(cwd, "remote", "get-url", "origin") or "").lower().removesuffix(".git").rstrip("/")
    return url.endswith(f"/{repo.lower()}") or url.endswith(f":{repo.lower()}")


def stale_branch(branch: str, repo: str | None, cwd: str | None, keep: frozenset[str] = frozenset()) -> str | None:
    """Why `branch` must not take merges, else None: a PR from it (same repository, not a fork) MERGED, none is
    OPEN, and the branch's tip on origin is still the head that merged - nothing new landed on it since, so a merge
    into it reaches nothing. Long-lived names, `keep` and origin's default branch never are; a branch that kept
    receiving commits after its merge is in use and is not either. The tip is the local `origin/<branch>` ref in
    `cwd`, which must be a checkout of `repo`; without one nothing is judged."""
    if not branch or LONG_LIVED.fullmatch(branch) or branch in keep or cwd is None:
        return None
    here = Path(cwd)
    if repo and not same_repo(here, repo):
        return None
    if git(here, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") == f"origin/{branch}":
        return None
    tip = (git(here, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}") or "").lower()
    fields = "number,state,baseRefName,headRefOid,isCrossRepository"
    args = ["pr", "list", "--head", branch, "--state", "all", "--limit", "30", "--json", fields]
    prs = GH([*args, "-R", repo] if repo else args, cwd)
    if not isinstance(prs, list):
        return None
    rows = [p for p in prs if isinstance(p, dict) and not p.get("isCrossRepository")]
    if any(p.get("state") == "OPEN" for p in rows):
        return None
    if not SHA.fullmatch(tip):
        if any(p.get("state") == "MERGED" for p in rows):
            NOTES.append(
                f"origin/{branch} is not in this clone, so whether `{branch}` already merged cannot be told - "
                f"`git fetch origin {branch}`"
            )
        return None
    merged = [p for p in rows if p.get("state") == "MERGED" and str(p.get("headRefOid") or "").lower() == tip]
    if not merged:
        return None
    into = str(merged[0].get("baseRefName") or "main")
    return (
        f"`{branch}` was already merged (PR #{merged[0].get('number')} into {into}; origin/{branch} is still at "
        f"{tip[:9]}, the head that merged - `git fetch` if it moved) and has no open PR, so commits merged into it "
        f"never reach {into}"
    )


def retarget(why: str) -> str:
    """The branch a stale_branch() message says the merged branch went into."""
    return why.rsplit(" ", 1)[-1]


def stale_base(pr: Pr, cwd: str | None, keep: frozenset[str] = frozenset()) -> str | None:
    """Why PR `pr` must not merge into its base, else None. The message ends with the retarget command."""
    why = stale_branch(pr.base, pr.repo, cwd, keep)
    if why is None:
        return None
    return f"{why}. Retarget the PR first: `gh pr edit {pr.number} -R {pr.repo} --base {retarget(why)}`"


def read_records() -> dict[tuple[str, str], dict[str, str]]:
    """The latest stamp per (repo, number). An unreadable file or line counts as no stamp."""
    latest: dict[tuple[str, str], dict[str, str]] = {}
    try:
        lines = records_path().read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return latest
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("repo", "pr", "sha")):
            continue
        latest[(row["repo"].lower(), row["pr"])] = {k: str(v) for k, v in row.items()}
    return latest


def review_status(pr: Pr) -> tuple[bool, str]:
    """Whether a stamp covers the PR's current head, and a line saying what was found."""
    stamp = read_records().get((pr.repo, pr.number))
    if stamp is None:
        return False, f"no review is recorded for {pr.repo}#{pr.number}"
    sha = stamp["sha"]
    if sha != pr.head:
        return False, (
            f"{pr.repo}#{pr.number} was reviewed at {sha[:9]}, but its head is now {pr.head[:9]}: review "
            f"`git diff {sha[:9]}..{pr.head[:9]}`, then stamp it again"
        )
    if stamp.get("base", pr.base) != pr.base:
        return False, (
            f"{pr.repo}#{pr.number} was reviewed against {stamp.get('base')}, but it now targets {pr.base}: review "
            f"its diff against {pr.base}, then stamp it again"
        )
    tests = stamp.get("tests") or "none"
    return True, f"{pr.repo}#{pr.number} reviewed at {sha[:9]} ({tests} tests) on {stamp.get('at', '?')}"


def git(cwd: Path, *args: str) -> str | None:
    left = time_left(10.0)
    if left <= 0:
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=left,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def stamp(number: str, repo: str | None, tests: str, cwd: Path) -> tuple[int, str]:
    """Record PR `number` as reviewed at its head. (exit code, message)."""
    pr = view(number, repo, str(cwd))
    if pr is None:
        return 1, f"pr-state: gh cannot read PR {number} (wrong account active, or no such PR) - nothing recorded"
    if pr.state != "OPEN":
        return 2, f"pr-state: {pr.repo}#{pr.number} is {pr.state}, not open - nothing recorded"
    local = git(cwd, "rev-parse", "HEAD")
    if local is None:
        return 1, f"pr-state: {cwd} is not a git checkout - run this in the PR branch's worktree"
    if local.lower() != pr.head:
        return 2, (
            f"pr-state: the local HEAD {local[:9]} is not the PR's head {pr.head[:9]} - push the reviewed commits "
            f"(or pull the PR's), and run this in the worktree of `{pr.branch}`"
        )
    if git(cwd, "status", "--porcelain", "--untracked-files=no") != "":
        return 2, "pr-state: tracked files are changed here - the stamp would not name what was reviewed"
    row = {
        "v": "1",
        "repo": pr.repo,
        "pr": pr.number,
        "sha": pr.head,
        "branch": pr.branch,
        "base": pr.base,
        "tests": tests,
        "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"),
    }
    path = records_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError as exc:
        return 1, f"pr-state: cannot write {path} ({exc})"
    return 0, f"recorded: {pr.repo}#{pr.number} reviewed at {pr.head[:9]} ({pr.branch} -> {pr.base}, {tests} tests)"


def status(number: str, repo: str | None, cwd: Path) -> tuple[int, str]:
    pr = view(number, repo, str(cwd))
    if pr is None:
        return 1, f"pr-state: gh cannot read PR {number}"
    _, review = review_status(pr)
    base = stale_base(pr, str(cwd)) if pr.state == "OPEN" else None
    lines = [
        f"{pr.repo}#{pr.number} {pr.state}  {pr.branch} -> {pr.base}  head {pr.head[:9]}",
        f"review: {review}",
        f"base:   {base or 'ok'}",
    ]
    return 0, "\n".join(lines)


def show(repo: str | None, limit: int) -> str:
    rows = sorted(read_records().values(), key=lambda r: r.get("at", ""), reverse=True)
    if repo:
        rows = [r for r in rows if r["repo"] == repo.lower()]
    if not rows:
        return "no reviews recorded"
    return "\n".join(
        f"{r.get('at', '?'):17}  {r['repo']}#{r['pr']:<5} {r['sha'][:9]}  {r.get('branch', '?')} -> "
        f"{r.get('base', '?')}  tests: {r.get('tests', '?')}"
        for r in rows[:limit]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record and read pull-request review stamps.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("reviewed", "status"):
        p = sub.add_parser(name)
        p.add_argument("number")
        p.add_argument("-R", "--repo")
        if name == "reviewed":
            p.add_argument("--tests", choices=TIERS, default="none")
    p = sub.add_parser("show")
    p.add_argument("-R", "--repo")
    p.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)
    if args.cmd == "show":
        print(show(args.repo, args.limit))
        return 0
    number = args.number.lstrip("#")
    if found := PR_URL.search(number):
        args.repo, number = found.group(1), found.group(2)
    if not number.isdigit():
        ap.error(f"not a PR number: {args.number}")
    if args.cmd == "reviewed":
        code, text = stamp(number, args.repo, args.tests, Path.cwd())
    else:
        code, text = status(number, args.repo, Path.cwd())
    print(text, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
