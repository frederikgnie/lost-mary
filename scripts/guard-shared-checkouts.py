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
            `pull --rebase|--autostash`, `clean` with a force flag, and
            `gh pr checkout`. Work on a branch in your own worktree.
  frozen  - trees only a deploy script may change. Blocked: every git command
            aimed there and every Edit/Write/MultiEdit/NotebookEdit inside.
            Running the deploy script is fine - only `git` invocations are
            inspected.
  frozen_by - the deploy script, named in the block message.

A target matches a configured dir when it is that dir or lies inside it by
path components (`X_wt_y` is not inside `X`). The working dir is tracked
through the command from the event's `cwd` across cd / pushd / Set-Location /
sl / Push-Location and `git -C`, relative paths included; anything it cannot
resolve matches nothing. Commands split on ; | && || and newlines outside
quotes; heredoc bodies are skipped, backslash-newline continuations joined.

Still passes by design (known gaps, not a sandbox): `git reset <commit>`
(mixed/soft), `commit --amend`, `merge`, `branch -f/-D`, git aliases, inner
commands of eval / $(...) / bash -c, `--git-dir` / `--work-tree` / `GIT_DIR=`,
symlinks and junctions, and a worktree nested inside a shared checkout.

Unreviewed merges
-----------------
A `gh pr merge` is held back, with or without a config, when it is chained
with anything else, or when no `review` agent was spawned in the session
since the last `gh pr merge` call (or the session start). Auto mode refuses
such a merge as [Merge Without Review] - and once it has refused, it refuses
the follow-ups too: on 2026-09-25 two c--repo-EU sessions had `git diff` for
the reviewer refused for the same reason and stopped on BLOCKED:. Stopped
here, the merge never reaches the classifier, so nothing latches: the session
spawns `review`, then merges alone - the shape the user's autoMode.allow rule
lets through. An unreadable transcript lets the merge through (fail open).

Wiring: PreToolUse, matcher "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit".
Exit 2 blocks the call and returns stderr to the model; exit 0 allows. Fails
OPEN: no config file -> silent no-op; an unreadable or malformed config or
payload -> one notice on stderr and allow. It prevents a known mistake; it is
not a sandbox.

Runtime dependencies (see capabilities.md): PreToolUse stdin fields
`tool_name`, `tool_input.command`, `tool_input.file_path` /
`tool_input.notebook_path`, `cwd`, `transcript_path` (the merge check).
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
# Words that may precede the real command in a simple command; skipped before the cd/git dispatch.
PREFIX_WORDS = ("&", "!", "command", "time", "exec", "nohup", "env")
PREFIX_WORDS += ("then", "do", "if", "elif", "else", "while", "until")
VALUE_FLAGS = ("-erroraction", "-warningaction", "-informationaction")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
REVIEW_ROLE = "review"
MERGE_CHAINED = (
    "guard-shared-checkouts: run `gh pr merge` as a lone call - nothing piped, nothing before or after it. "
    "Auto mode judges a chained command as a whole and refuses a merge inside one; after that refusal it "
    "refuses the follow-ups too."
)
MERGE_UNREVIEWED = (
    "guard-shared-checkouts: no `review` agent has run since the last merge in this session. Auto mode refuses "
    "this merge as [Merge Without Review], and after that refusal it refuses the follow-ups as well - even "
    "`git diff` for the reviewer. So first: spawn `review` with the PR's `git diff`, fix what it finds, run the "
    "project's checks. Then run the same `gh pr merge` again, alone."
)


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
    if not candidate.drive and not candidate.root:  # relative; a POSIX absolute path keeps its root
        if base is None:
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


def split_commands(command: str) -> list[str]:
    """Split a shell command on ; | && || and newlines outside quotes, skipping heredoc bodies.

    Backslash-newline continuations are joined first. A heredoc opener (`<<EOF`, `<<-'EOF'`) is
    recognised outside single quotes - inside double quotes too, for `"$(cat <<'EOF' ... EOF)"` -
    and its body, up to the terminator line, is dropped unread.
    """
    text = re.sub(r"\\\r?\n", " ", command)
    pieces: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    pending: list[str] = []
    i, n = 0, len(text)

    def flush() -> None:
        piece = "".join(buf).strip()
        if piece:
            pieces.append(piece)
        buf.clear()

    while i < n:
        ch = text[i]
        if quote != "'" and text.startswith("<<<", i):
            buf.append("<<<")
            i += 3
            continue
        if quote != "'" and text.startswith("<<", i) and (m := HEREDOC.match(text, i)):
            pending.append(m[2])
            buf.append(m[0])
            i = m.end()
            continue
        if ch == "\n" and pending:
            if quote is None:
                flush()
            i += 1
            for term in pending:
                while i < n:
                    end = text.find("\n", i)
                    line = text[i:] if end == -1 else text[i:end]
                    i = n if end == -1 else end + 1
                    if line.strip() == term:
                        break
            pending = []
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            buf.append(ch)
            i += 1
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
        elif text.startswith(("&&", "||"), i):
            flush()
            i += 2
        elif ch in ";|\n":
            flush()
            i += 1
        else:
            buf.append(ch)
            i += 1
    flush()
    return pieces


def strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop what may precede the real command - `(`, `{`, `command`, `if`, `VAR=1` ... - and trailing `)` / `}`."""
    out = list(tokens)
    opened = False
    while out:
        first = out[0]
        if first.startswith(("(", "{")):
            opened = True
            rest = first.lstrip("({")
            if rest:
                out[0] = rest
            else:
                out.pop(0)
        elif first in PREFIX_WORDS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", first):
            out.pop(0)
        else:
            break
    while out and out[-1] in (")", "}", "))"):
        out.pop()
    if opened and out and out[-1].endswith(")"):
        out[-1] = out[-1].rstrip(")")
        if not out[-1]:
            out.pop()
    return out


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple-command token lists, wrappers stripped."""
    out: list[list[str]] = []
    for chunk in split_commands(command):
        # Backslashes are Windows path separators here, not escapes; posix shlex would eat them.
        chunk = chunk.replace("\\", "/")
        try:
            tokens = shlex.split(chunk, posix=True)
        except ValueError:
            tokens = chunk.split()
        tokens = strip_wrappers(tokens)
        if tokens:
            out.append(tokens)
    return out


def cd_target(args: list[str], current: PureWindowsPath | None) -> PureWindowsPath | None:
    """Where cd / Set-Location / pushd with `args` leaves the tracked dir; None when unknown."""
    operands: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a.lower() in VALUE_FLAGS:
            skip = True
        elif a == "-" or not a.startswith("-"):
            operands.append(a)
    if len(operands) != 1:
        return None
    return norm(operands[0], current)


class Call(NamedTuple):
    target: PureWindowsPath | None
    label: str
    changes_shared: bool
    hint: str


def repo_call(tokens: list[str], current: PureWindowsPath | None) -> Call | None:
    """What a git or `gh pr checkout` invocation does, or None when `tokens` is neither."""
    name = PureWindowsPath(tokens[0]).name.lower()
    if name in ("gh", "gh.exe"):
        if tokens[1:3] != ["pr", "checkout"]:
            return None
        return Call(current, " ".join(["gh", *tokens[1:]]), True, "")
    if name not in ("git", "git.exe"):
        return None
    target = current
    args = tokens[1:]
    while args and args[0].startswith("-"):
        opt = args[0]
        if opt == "-C" and len(args) >= 2:
            target = norm(args[1], target)
            args = args[2:]
        elif opt == "-c" and len(args) >= 2:
            args = args[2:]
        else:
            args = args[1:]
    hint = " For a file restore use `git checkout -- <path>`." if args[:1] == ["checkout"] else ""
    return Call(target, " ".join(["git", *args]), changes_shared_state(args), hint)


def changes_shared_state(args: list[str]) -> bool:
    """Whether `git <args>` moves branch state or the stash (not a file restore)."""
    if not args:
        return False
    sub, rest = args[0], args[1:]
    if sub in ("switch", "rebase"):
        return True
    if sub == "checkout":
        if "--" in rest:
            cut = rest.index("--")
            if cut < len(rest) - 1:
                return False  # checkout [<ref>] -- <path>: a restore
            rest = rest[:cut]  # a bare trailing `--` still checks out the ref
        if rest == ["."]:
            return False
        return any(not a.startswith("-") for a in rest) or any(a in BRANCH_FLAGS for a in rest)
    if sub == "pull":
        return any(a in ("-r", "--autostash") or (a.startswith("--rebase") and a != "--rebase=false") for a in rest)
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
        call = repo_call(tokens, current)
        if call is None:
            continue
        if where := match(call.target, config.frozen):
            return frozen_reason(f"`{call.label}` in {call.target}", where, config)
        if (where := match(call.target, config.shared)) and call.changes_shared:
            base = where.shown.rstrip("/\\")
            return (
                f"guard-shared-checkouts: blocked `{call.label}` in {call.target} - {where.shown} is a checkout "
                f"other sessions use at the same time, and changing its branch or stash moves their work too. "
                f"Work on a branch in your own worktree: git -C {base} worktree add {base}_wt_<name> -b <branch>"
                f"{call.hint}"
            )
    return None


def check_edit(file_path: str, cwd: str, config: Config) -> str | None:
    """A block reason for an Edit/Write/MultiEdit/NotebookEdit target, or None to allow it."""
    target = norm(file_path, norm(cwd) if cwd else None)
    if where := match(target, config.frozen):
        return frozen_reason(f"editing {file_path}", where, config)
    return None


def is_merge(tokens: list[str]) -> bool:
    """`gh pr merge ...`, gh named bare or by path."""
    if len(tokens) < 3:
        return False
    exe = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return exe in ("gh", "gh.exe") and tokens[1] == "pr" and tokens[2] == "merge"


def reviewed_since_merge(transcript: Path, current: str) -> bool | None:
    """Whether a `review` spawn follows the last earlier `gh pr merge` call in the transcript; None if unreadable.

    `current` is this call's tool_use_id: the transcript may already hold it, and it is not an earlier merge.
    """
    reviewed = False
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"tool_use"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = record.get("message") if isinstance(record, dict) else None
                content = message.get("content") if isinstance(message, dict) else None
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    if current and block.get("id") == current:
                        continue
                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        continue
                    role = tool_input.get("subagent_type")
                    if block.get("name") in ("Agent", "Task") and str(role).strip().lower() == REVIEW_ROLE:
                        reviewed = True
                    elif block.get("name") in SHELL_TOOLS and any(
                        is_merge(t) for t in segments(str(tool_input.get("command") or ""))
                    ):
                        reviewed = False  # the next merge needs a review of its own
    except OSError:
        return None
    return reviewed


def check_merge(command: str, transcript: str, current: str = "") -> str | None:
    """The block message for a chained or unreviewed `gh pr merge`, else None."""
    parts = segments(command)
    if not any(is_merge(t) for t in parts):
        return None
    if len(parts) > 1:
        return MERGE_CHAINED
    if not transcript:
        return None
    if reviewed_since_merge(Path(transcript), current) is False:
        return MERGE_UNREVIEWED
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
    event = read_payload()
    if event is None:
        notice("could not read the hook payload - allowing")
        return 0
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    if tool in SHELL_TOOLS:
        try:
            held = check_merge(
                str(tool_input.get("command") or ""),
                str(event.get("transcript_path") or ""),
                str(event.get("tool_use_id") or ""),
            )
        except Exception as exc:  # the merge check is a convenience: never let it cost the call
            notice(f"merge check skipped ({exc!r})")
            held = None
        if held:
            print(held, file=sys.stderr)
            return 2
    config = load_config()
    if config is None or not (config.shared or config.frozen):
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
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - never with a traceback
        notice(f"unexpected error ({exc!r}) - allowing")
        sys.exit(0)
