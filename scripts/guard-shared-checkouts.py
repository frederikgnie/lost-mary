#!/usr/bin/env python3
"""guard-shared-checkouts: PreToolUse hook that keeps sessions from moving checkouts other sessions share.

Why this exists
---------------
Several Claude sessions work in the same checkouts at once while a scheduler
executes that tree live. On 2026-09-24 one session ran `git checkout -b
feat/typed-identity-legs` in a shared checkout: the live roles ran that branch
with 41 uncommitted files and another session's commit landed on it. The
transcripts hold 97 tree-changing git calls by four concurrent sessions in
those checkouts since 2026-09-20. A rule in prose did not stop it; this does.
It stops the git-state class only - uncommitted edits reaching the live roles
are stopped by a separate frozen deploy tree, which this hook also protects.

Config, read on every call (no restart): `$LOST_MARY_SHARED_CHECKOUTS`, else
`~/.claude/agent-library/shared-checkouts.json` (template:
shared-checkouts.example.json in the library repository):

  {"shared": [dirs], "frozen": [dirs], "frozen_by": "path, optional"}

  shared  - checkouts several sessions use at once. Blocked there: `switch`,
            a branch-changing `checkout` (file restores are fine), `stash`
            other than list/show, `reset --hard|--merge|--keep`, `rebase`,
            `clean` with a force flag. Work on a branch in your own worktree.
  frozen  - trees only a deploy script may change. Blocked: every git command
            aimed there and every Edit/Write/MultiEdit/NotebookEdit inside.
            Running the deploy script is fine - only `git` invocations are
            inspected.
  frozen_by - the deploy script, named in the block message.

A target matches a configured dir when it is that dir or lies inside it by
path components (`X_wt_y` is not inside `X`). The working dir is tracked
through the command from the event's `cwd` across cd / pushd / Set-Location /
sl / Push-Location and `git -C`, relative paths included; anything it cannot
resolve matches nothing.

Wiring: PreToolUse, matcher "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit".
Exit 2 blocks the call and returns stderr to the model; exit 0 allows. Fails
OPEN: no config file -> silent no-op; an unreadable or malformed config or
payload -> one notice on stderr and allow. It prevents a known mistake; it is
not a sandbox.

Runtime dependencies (see capabilities.md): PreToolUse stdin fields
`tool_name`, `tool_input.command`, `tool_input.file_path` /
`tool_input.notebook_path`, `cwd`.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

ENV_VAR = "LOST_MARY_SHARED_CHECKOUTS"
SHELL_TOOLS = ("Bash", "PowerShell")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
CD_COMMANDS = ("cd", "chdir", "pushd", "set-location", "sl", "push-location")
POP_COMMANDS = ("popd", "pop-location")
BRANCH_FLAGS = ("-b", "-B", "--orphan", "--detach")


class Dir(NamedTuple):
    shown: str
    key: tuple[str, ...]


class Config(NamedTuple):
    shared: list[Dir]
    frozen: list[Dir]
    frozen_by: str | None


def notice(text: str) -> None:
    print(f"guard-shared-checkouts: {text}", file=sys.stderr)


def config_path() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".claude" / "agent-library" / "shared-checkouts.json"


def norm(path: str, base: PureWindowsPath | None = None) -> PureWindowsPath | None:
    """Normalise `C:\\x`, `C:/x`, `/c/x`, quoted or not; resolve a relative path against `base`.

    Returns None when the path cannot be resolved (empty, `~`, a variable, relative with no base).
    """
    p = path.strip().strip("\"'").replace("\\", "/")
    if not p or p == "-" or p.startswith(("~", "$", "%")):
        return None
    if m := re.match(r"^/([a-zA-Z])(/.*)?$", p):
        p = f"{m[1]}:{m[2] or '/'}"
    candidate = PureWindowsPath(p)
    if not candidate.drive:
        if candidate.root or base is None:
            return None
        candidate = base / candidate
    parts: list[str] = []
    for part in candidate.parts[1:]:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return PureWindowsPath(candidate.anchor, *parts)


def key_of(path: PureWindowsPath) -> tuple[str, ...]:
    return tuple(part.lower() for part in path.parts)


def match(target: PureWindowsPath | None, dirs: list[Dir]) -> Dir | None:
    """The configured dir `target` is, or lies inside by path components."""
    if target is None:
        return None
    key = key_of(target)
    for d in dirs:
        if key[: len(d.key)] == d.key:
            return d
    return None


def parse_config(raw: object) -> Config | str:
    """A Config, or a one-line reason the data is not one."""
    if not isinstance(raw, dict):
        return "top level is not an object"
    lists: dict[str, list[Dir]] = {}
    for field in ("shared", "frozen"):
        value = raw.get(field, [])
        if not isinstance(value, list):
            return f'"{field}" is not a list of strings'
        dirs: list[Dir] = []
        for v in value:
            if not isinstance(v, str):
                return f'"{field}" is not a list of strings'
            p = norm(v)
            if p is None:
                return f'"{field}" entry {v!r} is not an absolute path'
            dirs.append(Dir(v, key_of(p)))
        lists[field] = dirs
    frozen_by = raw.get("frozen_by")
    if frozen_by is not None and not isinstance(frozen_by, str):
        return '"frozen_by" is not a string'
    return Config(lists["shared"], lists["frozen"], frozen_by or None)


def load_config() -> Config | None:
    """The config, or None: silently when the file does not exist, with a notice when it is broken."""
    path = config_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        notice(f"cannot read {path} ({type(exc).__name__}) - guard off")
        return None
    parsed = parse_config(raw)
    if isinstance(parsed, str):
        notice(f"{path}: {parsed} - guard off")
        return None
    return parsed


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple-command token lists on ; && || | and newlines."""
    out: list[list[str]] = []
    # Backslashes are Windows path separators here, not escapes; posix shlex would eat them.
    command = command.replace("\\", "/")
    for chunk in re.split(r"&&|\|\||[;|\n]", command):
        try:
            tokens = shlex.split(chunk, posix=True)
        except ValueError:
            tokens = chunk.split()
        if tokens:
            out.append(tokens)
    return out


def cd_target(args: list[str], current: PureWindowsPath | None) -> PureWindowsPath | None:
    """Where cd / Set-Location / pushd with `args` leaves the tracked dir; None when unknown."""
    operands = [a for a in args if a == "-" or not a.startswith("-")]
    if len(operands) != 1:
        return None
    return norm(operands[0], current)


def git_call(tokens: list[str], current: PureWindowsPath | None) -> tuple[PureWindowsPath | None, list[str]] | None:
    """(target dir, subcommand args) when `tokens` runs git, else None."""
    i = 0
    while i < len(tokens) and (tokens[i] == "&" or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[i])):
        i += 1
    if i >= len(tokens) or PureWindowsPath(tokens[i]).name.lower() not in ("git", "git.exe"):
        return None
    target = current
    args = tokens[i + 1 :]
    while args and args[0].startswith("-"):
        opt = args[0]
        if opt == "-C" and len(args) >= 2:
            target = norm(args[1], target)
            args = args[2:]
        elif opt == "-c" and len(args) >= 2:
            args = args[2:]
        else:
            args = args[1:]
    return target, args


def changes_shared_state(args: list[str]) -> bool:
    """Whether `git <args>` moves branch state or the stash (not a file restore)."""
    if not args:
        return False
    sub, rest = args[0], args[1:]
    if sub in ("switch", "rebase"):
        return True
    if sub == "checkout":
        if "--" in rest or rest == ["."]:
            return False
        return any(not a.startswith("-") for a in rest) or any(a in BRANCH_FLAGS for a in rest)
    if sub == "stash":
        return not rest or rest[0] not in ("list", "show")
    if sub == "reset":
        return any(a in ("--hard", "--merge", "--keep") for a in rest)
    if sub == "clean":
        if any(a in ("-n", "--dry-run") or (re.match(r"^-[a-zA-Z]+$", a) and "n" in a) for a in rest):
            return False
        return any(a == "--force" or (re.match(r"^-[a-zA-Z]+$", a) and ("f" in a or "x" in a)) for a in rest)
    return False


def frozen_reason(what: str, where: Dir, config: Config) -> str:
    via = f" only {config.frozen_by} may change it" if config.frozen_by else " only its deploy script may change it"
    return f"guard-shared-checkouts: blocked {what} - {where.shown} is a frozen tree the live jobs run;{via}."


def check_command(command: str, cwd: str, config: Config) -> str | None:
    """A block reason for a Bash/PowerShell command, or None to allow it."""
    current = norm(cwd) if cwd else None
    for tokens in segments(command):
        head = tokens[0].lower()
        if head in CD_COMMANDS:
            current = cd_target(tokens[1:], current)
            continue
        if head in POP_COMMANDS:
            current = None
            continue
        call = git_call(tokens, current)
        if call is None:
            continue
        target, args = call
        if where := match(target, config.frozen):
            return frozen_reason(f"`git {' '.join(args)}` in {target}", where, config)
        if (where := match(target, config.shared)) and changes_shared_state(args):
            base = where.shown.rstrip("/\\")
            return (
                f"guard-shared-checkouts: blocked `git {' '.join(args)}` in {target} - {where.shown} is a checkout "
                f"other sessions use at the same time, and changing its branch or stash moves their work too. "
                f"Work on a branch in your own worktree: git -C {base} worktree add {base}_wt_<name> -b <branch>"
            )
    return None


def check_edit(file_path: str, cwd: str, config: Config) -> str | None:
    """A block reason for an Edit/Write/MultiEdit/NotebookEdit target, or None to allow it."""
    target = norm(file_path, norm(cwd) if cwd else None)
    if where := match(target, config.frozen):
        return frozen_reason(f"editing {file_path}", where, config)
    return None


def read_payload() -> dict[str, Any] | None:
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    config = load_config()
    if config is None or not (config.shared or config.frozen):
        return 0
    event = read_payload()
    if event is None:
        notice("could not read the hook payload - allowing")
        return 0
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    cwd = str(event.get("cwd") or "")
    reason: str | None = None
    if tool in SHELL_TOOLS:
        reason = check_command(str(tool_input.get("command") or ""), cwd, config)
    elif tool in EDIT_TOOLS:
        reason = check_edit(str(tool_input.get("file_path") or tool_input.get("notebook_path") or ""), cwd, config)
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
