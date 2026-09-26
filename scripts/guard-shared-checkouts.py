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
            `pull --rebase|--autostash`, `clean` with a force flag,
            `gh pr checkout`, and `gh pr merge -d|--delete-branch` (with the
            PR's branch checked out there, gh checks out the base branch and
            pulls; otherwise it deletes the local branch - the guard cannot
            tell which without running git, so it holds both).
            Work on a branch in your own worktree.
  frozen  - trees only a deploy script may change. Blocked: every git command
            aimed there and every Edit/Write/MultiEdit/NotebookEdit inside.
            Running the deploy script is fine - only `git` invocations are
            inspected.
  frozen_by - the deploy script, named in the block message.

A target matches a configured dir when it is that dir or lies inside it by
path components (`X_wt_y` is not inside `X`). The working dir is tracked
through the command from the event's `cwd` across cd / pushd / Set-Location /
sl / Push-Location and `git -C`, relative paths included; anything it cannot
resolve matches nothing. Commands are read the way the shell reads them
(scan_commands): split on ; | & && || and newlines outside quotes and
substitutions, with escapes, comments, heredoc bodies and PowerShell
here-strings handled; a bash `cd` to a path bash would mangle (an unquoted
backslash) goes nowhere, as it does in bash, and so does a `cd` in a bash
pipeline stage or background command. A call whose scan ends inside
something - a quote, a substitution, arithmetic, a heredoc - is also read
naively (every separator and bracket splits, continuations joined): a
misread that leaves the scan inside a construct then finds any command that
starts a naive chunk, at the price of false holds - every directory the call
cd'd into counts. A misread that ends outside every construct can still hide
a command; nine review rounds found such shapes and each now reads correctly
or is caught by the fail-safe, with a test in tests/test-hooks.py, but the
list below is not proof that none is left.

Still passes by design (known gaps, not a sandbox): `git reset <commit>`
(mixed/soft), `commit --amend`, `merge`, `branch -f/-D`, git aliases, inner
commands of eval / backticks / bash -c, commands run by an alias or a
function defined in an earlier call, a `case` arm's command, `xargs git`,
a `{` block glued to its first command (`{git switch main}`), quotes the
shell nests where the scanner does not (`"${x:-"a"}"`), `--git-dir` /
`--work-tree` / `GIT_DIR=`, a `cd` inside a `( ... )` subshell (it leaks to
the commands after it), a `cd` inside a `{ ... }` block or a function
body (counted as run), a `$(...)` inside an unquoted heredoc body (bash runs
it; the body is dropped unread), symlinks and junctions, and a worktree
nested inside a shared checkout. The commands inside `$(...)` are checked, each substitution with its
own working directory in bash (a subshell) and the caller's in PowerShell.

Merges the auto-mode classifier would refuse
--------------------------------------------
With or without a config, a merge is held back when:
  * it is not the only merge in the call, sits inside another command (a
    loop, xargs, ForEach-Object), or shares the call with anything off a
    short list (CHAIN_OK*: a cd, output trimming such as tail or grep,
    `gh pr view|checks|comment`, and git fetch|log|status|diff|show|pull|
    switch|checkout|branch - the last four without a force or discard flag
    such as -f, -D, --, .);
  * no `review` agent was spawned in the session since the last merge of
    another PR that may have run (or the session start). A review counts for
    the PR it precedes: a retry of the same PR - the same literal number in
    the same repository or directory, or the same PR URL - never spends it,
    and another PR's merge spends it unless its result shows it never ran
    (refused, declined, held by a hook). No number, a branch name or a
    variable is never "the same PR". An error exit alone is no sign the merge
    never ran: gh exits 1 when only the cleanup after a merge fails;
  * it goes through the API - a PUT to `.../pulls/<n>/merge`, or a GraphQL
    `mergePullRequest` / `enablePullRequestAutoMerge` written into the call:
    the user's allow rule names `gh pr merge` only. The REST path without PUT
    only asks whether the PR is merged and passes.
A command substitution is a command of its own (`OUT="$(gh pr merge 29)"`),
and comments are dropped before anything is split.
Auto mode refuses such a merge as [Merge Without Review] - and once it has
refused, it refuses the follow-ups too: on 2026-09-25 two c--repo-EU sessions
had `git diff` for the reviewer refused for the same reason and stopped on
BLOCKED:. Held here, the merge never reaches the classifier: a PreToolUse
exit 2 "stops the tool call before permission rules are evaluated" (docs,
permissions page), and permission rules are step 1 of auto mode's decision
order, the classifier step 3. The session spawns `review`, then merges in its
own call - the shape the user's autoMode.allow rule lets through.

Not covered: the allow rule covers only a PR the session opened, merged
into main or master after the project's checks ran - the guard checks none of
that, so such a merge can still be refused once; the session then stops on a
BLOCKED: backed by that refusal, and a user message naming the merge lets it
through. A review of a different PR counts as a review, and so does a review
spawn that failed - a transcript cannot tell which change a review covered.
`bash -c`, eval, backticks, a substituted command word (`$(which gh) pr
merge`) and a GraphQL query read from a file hide a merge; a merge run by a
subagent is
judged against whatever transcript its hook is given. An unreadable
transcript lets the merge through, with a notice.

`python scripts/replay-merges.py` replays every merge call in the local
transcripts through this check, each against its transcript cut at the call;
the reading of 2026-09-25 is in capabilities.md.

Wiring: PreToolUse, matcher "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit".
Exit 2 blocks the call and returns stderr to the model; exit 0 allows. Fails
OPEN: no config file -> silent no-op; an unreadable or malformed config or
payload -> one notice on stderr and allow. It prevents a known mistake; it is
not a sandbox.

Runtime dependencies (see capabilities.md): PreToolUse stdin fields
`tool_name`, `tool_input.command`, `tool_input.file_path` /
`tool_input.notebook_path`, `cwd`; for the merge check `transcript_path`,
`tool_use_id`, and the transcript's assistant `tool_use` blocks (`name`, `id`,
`input.command`, `input.subagent_type`) and user `tool_result` blocks
(`tool_use_id`, `is_error`, `content`), and the assistant record's `cwd`.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shlex
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple

ENV_VAR = "LOST_MARY_SHARED_CHECKOUTS"
SHELL_TOOLS = ("Bash", "PowerShell")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
CD_COMMANDS = ("cd", "chdir", "pushd", "set-location", "sl", "push-location")
POP_COMMANDS = ("popd", "pop-location")
PUSH_COMMANDS = ("pushd", "push-location")
BRANCH_FLAGS = ("-b", "-B", "--orphan", "--detach")
# Words that may precede the real command in a simple command; skipped before the cd/git dispatch.
PREFIX_WORDS = ("&", "!", "command", "time", "exec", "nohup", "env")
PREFIX_WORDS += ("then", "do", "if", "elif", "else", "while", "until", "builtin", "coproc", "nice", "timeout")
WRAPPER_OPTIONS = ("time", "env", "nice", "timeout", "command", "exec", "builtin", "nohup")  # take `-x` options
VALUE_FLAGS = ("-erroraction", "-warningaction", "-informationaction")
# `<<EOF`, `<<-EOF`, `<<\EOF`, `<<'END-MSG'`, `<<"EOF"`: the terminator is group 2 (quoted) or group 3.
HEREDOC = re.compile(r"<<-?\s*(?:(['\"])([^'\"\n]+)\1|\\?([\w.-]+))")
REVIEW_ROLE = "review"
MERGE_CHAINED = (
    "guard-shared-checkouts: run `gh pr merge` in its own call - one merge, not inside a loop or another "
    "command, with nothing beside it but a `cd`, output trimming (`2>&1 | tail -3`), `gh pr view|checks|comment`, "
    "or git fetch|log|status|diff|show|pull|switch|checkout|branch without a force or discard flag. "
    "Auto mode judges a chained command as a whole (it refused `push && pr create && pr merge` that way on "
    "2026-09-23), and after a refusal it refuses the follow-ups too."
)
MERGE_UNCLEAR = (
    "guard-shared-checkouts: this call does not parse cleanly ({open}), so a merge in it cannot be checked. Run "
    "`gh pr merge` in its own call."
)
MERGE_API = (
    "guard-shared-checkouts: merge with `gh pr merge <number>`, not the REST endpoint - the user's auto-mode allow "
    "rule names `gh pr merge` only, so auto mode would refuse this and then refuse the follow-ups too."
)
MERGE_UNREVIEWED = (
    "guard-shared-checkouts: no `review` agent has run in this session since the last merge of another PR (a "
    "merge that names no PR number or URL counts as another PR - name the PR so a retry keeps its review). "
    "Auto mode refuses "
    "this merge as [Merge Without Review], and after that refusal it refuses the follow-ups as well - even "
    "`git diff` for the reviewer. So first: spawn `review` with the PR's `git diff`, fix what it finds, run the "
    "project's checks. Then run the same `gh pr merge` again, in its own call."
)
# What may share a call with a merge: moving into the checkout, trimming output, reading state, syncing
# afterwards. Anything else - a push, a commit, `gh pr create`, a loop, an unknown program - holds it back.
# `python scripts/replay-merges.py`: chains like these went through dozens of times, and every refusal but
# one traced to a missing review, not to the chain (2026-09-25).
CHAIN_OK = {
    "cd",
    "chdir",
    "pushd",
    "popd",
    "set-location",
    "sl",
    "push-location",
    "pop-location",
    "get-location",
    "pwd",
    "ls",
    "dir",
    "test-path",
    "tail",
    "head",
    "date",
    "basename",
    "dirname",
    "tee",
    "tee-object",
    "cut",
    "awk",
    "tr",
    "uniq",
    "cat",
    "echo",
    "printf",
    "sleep",
    "start-sleep",
    "wc",
    "grep",
    "sort",
    "jq",
    "true",
    "set",
    "[",
    "test",
    "fi",
    "write-output",
    "write-host",
    "select-object",
    "select-string",
    "where-object",
    "out-null",
    "out-string",
    "try",
    "catch",
    "finally",
}
PS_OPERATORS = {"-eq", "-ne", "-gt", "-ge", "-lt", "-le", "-and", "-or", "-not", "-like", "-match", "-contains"}
CHAIN_OK_GH = {
    ("pr", "view"),
    ("pr", "checks"),
    ("pr", "status"),
    ("pr", "list"),
    ("pr", "diff"),
    ("pr", "comment"),
    ("repo", "view"),
    ("auth", "switch"),
    ("auth", "status"),
    ("run", "list"),
    ("run", "view"),
}
CHAIN_OK_GIT = {"fetch", "log", "status", "diff", "show", "rev-parse", "remote", "pull", "checkout", "switch", "branch"}
GIT_MOVES = ("pull", "checkout", "switch", "branch")  # allowed beside a merge only without GIT_RISKY
GIT_RISKY = {"-f", "--force", "-D", "-B", "-C", "--discard-changes", "--hard", "--", "."}  # force, discard
# A call that never ran: refused by auto mode or declined at a prompt (no-punt's REFUSAL_MARKERS - a test keeps
# them in step), or held by a hook. The marker opens an error result; see did_not_run().
DID_NOT_RUN = (
    "denied by the Claude Code auto mode classifier",
    "requested permissions",
    "haven't granted it yet",
    "user doesn't want to proceed",
    "user doesn't want to take this action",
    "hook error",
)
MERGE_ENDPOINT = re.compile(r"repos/([^/\s]+/[^/\s]+)/pulls/([^/\s]+)/merge/?$")
MERGE_VALUE_FLAGS = (
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-t",
    "--subject",
    "-A",
    "--author-email",
    "--match-head-commit",
)
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A `case WORD in` starting a command - not the word in a branch name (`kebab-case`) or in a grep pattern.
CASE_COMMAND = re.compile(r"(?:^|[\n;&|(])\s*case\s+\S+\s+in(?:\s|$)")
PLACEHOLDER = "$SUBST"  # where a command substitution stood: a `$` word, so it names no repository or PR
ENV_REPO = "\x00GH_REPO="  # a word only pr_key() reads: the repository GH_REPO names (see simple_command)
CD_FAILS = "\x00cd-fails"  # the operand of a cd that goes nowhere (see cd_breaks)
MAX_NESTING = 32  # command substitutions nested deeper are not taken apart: the call counts as uncertain
CURLY_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})  # PowerShell strings
OPEN_CONTEXT = {
    "'": "an unclosed single quote",
    '"': "an unclosed double quote",
    "$'": "an unclosed `$'...'` string",
    '@"': 'an `@"` here-string that never closes',
    "$(": "an unclosed `$(` substitution",
    "$((": "unclosed `$((` arithmetic",
    "((": "an unclosed `((` arithmetic command",
    "${": "an unclosed `${`",
    "(": "an unclosed parenthesis",
}
CD_HINT = (
    " (A bash `cd` to an unquoted backslash path fails and leaves the shell where it was - quote the path or use "
    "forward slashes.)"
)
PR_NUMBER = re.compile(r"#?(\d+)")
PR_URL = re.compile(r"github\.com/([^/\s]+/[^/\s]+)/pull/(\d+)")
REDIRECT = re.compile(r"\d*[<>]|&>")  # a redirection word: `2>&1`, `>out`, `&>log`
REDIRECT_ALONE = re.compile(r"\d*(?:>>?|<)|&>>?")  # one whose target is the next word: `> out`


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


def strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop what may precede the real command - `(`, `{`, PowerShell's `@(`, `command`, `if` ... - and a trailing
    `)` / `}`. A leading `VAR=value` is simple_command()'s to peel."""
    out = list(tokens)
    opened = False
    while out:
        first = out[0]
        if first.startswith(("(", "{", "@(")):
            opened = True
            rest = first[2:] if first.startswith("@(") else first.lstrip("({")
            if rest:
                out[0] = rest
            else:
                out.pop(0)
        elif first in PREFIX_WORDS:
            out.pop(0)
            while first in WRAPPER_OPTIONS and out and out[0].startswith("-") and out[0] != "-":
                flag = out.pop(0)
                if flag in ("-n", "-s", "-k", "-u") and out:
                    out.pop(0)  # the option's value: `nice -n 10`, `timeout -s KILL`, `env -u NAME`
            if first in ("nice", "timeout") and out and re.match(r"^\d", out[0]):
                out.pop(0)  # `timeout 5s git ...`
        else:
            break
    while out and out[-1] in (")", "}", "))"):
        out.pop()
    if opened and out and out[-1].endswith(")"):
        out[-1] = out[-1].rstrip(")")
        if not out[-1]:
            out.pop()
    return out


def arithmetic_at(text: str, j: int) -> bool:
    """Whether the `((` whose inside starts at `j` is arithmetic, as bash decides: the parenthesis that closes at
    depth 0 must be followed by another `)`. Otherwise it opens nested subshells (`((true); git switch main)`, and
    inside a `$(`: `$((true); cmd)` is a command substitution)."""
    depth = 0
    quote: str | None = None
    k = j
    while k < len(text):
        c = text[k]
        if quote is not None:
            if c == "\\" and quote != "'":
                k += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            k += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return text[k + 1 : k + 2] == ")"
            depth -= 1
        k += 1
    return False


def scan_commands(
    command: str, powershell: bool = False, state: dict[str, Any] | None = None
) -> list[tuple[str, list[str], bool]]:
    r"""Split a shell command into simple commands, each with its command substitutions cut out.

    One pass with a stack of open contexts, so each character is read the way the shell reads it:
      * quotes: single (literal), double, bash `$'...'` (backslash escapes), PowerShell `@"` / `@'` here-strings;
        in PowerShell curly quotes delimit strings too, and after `--%` the rest of the line up to a `|` is literal;
      * an escape (bash backslash, PowerShell backtick) takes the next character literally - a newline too, which
        is how a line continues; none inside single quotes, a comment or a heredoc body;
      * `;`, `|`, `|&`, `&`, `&&`, `||` and newlines end a command only outside every context (`2>&1` is no `&`);
      * a `#` that starts a word outside quotes and `${...}` starts a comment to the end of the line - not right
        after an escaped character or a closing `$(...)` / `$((...))`, where the word goes on; in PowerShell a word
        also starts after `{ } , =`, and `<# ... #>` is a comment;
      * `$(...)`, and in bash `<(...)` / `>(...)` - inside double quotes too, where quotes nest afresh - are cut
        out: their text goes beside the command and PLACEHOLDER stays in its place;
      * bash `$((...))` and a `((...))` command are arithmetic when bash reads them so (arithmetic_at): no command,
        heredoc or comment starts inside, though a `$(...)` in them is still cut out; `${...}` holds no comment;
      * a heredoc opener (`<<EOF`, `<<-'END-MSG'`, `<<"EOF"`, `<<\EOF`) queues its terminator; at the end of the
        line the body is dropped unread up to the exact terminator line (tabs stripped for `<<-`; in an unquoted
        bash heredoc a line ending in `\` joins the next) - inside a substitution an empty heredoc stays, so its
        text still parses. A PowerShell `@'` here-string is dropped the same way; an `@"` one keeps its `$(...)`.
    Each piece carries its substitutions' texts and whether it is detached: in bash a pipeline stage or a command
    sent to the background runs in a subshell, so a `cd` there moves nothing. When the scan ends inside a context,
    a heredoc never ends, a heredoc's terminator cannot be read, or a substitution holds a `case` without `esac`,
    `state["uncertain"]` is set and `state["open"]` names it: the call may be read otherwise by the shell (see
    naive_segments). A misread that ends outside every context is not caught this way.
    """
    text = command.translate(CURLY_QUOTES) if powershell else command
    escape = "`" if powershell else "\\"
    word_start = " \t\n;|&()" + ("{}," if powershell else "")
    pieces: list[tuple[str, list[str], bool]] = []
    buf: list[str] = []
    captured: list[str] = []
    sub: list[str] | None = None  # the text of the outermost open substitution
    stack: list[str] = []  # open contexts, innermost last: "'", '"', "$'", '@"', "$(", "$((", "((", "${", "("
    pending: list[tuple[str, bool, bool]] = []  # heredocs: (terminator, quoted, `<<-`), waiting for the line's end
    escaped_until = glued_at = -1  # just past an escape pair / a closing `$(...)` or `$((...))`: the word goes on
    piped = False  # the piece being read follows a `|`
    opened: str | None = None  # what the scan could not close, for state["open"]
    i, n = 0, len(text)

    def mark(what: str) -> None:
        nonlocal opened
        opened = opened or what

    def emit(chars: str) -> None:
        (sub if sub is not None else buf).append(chars)

    def flush(detached: bool = False) -> None:
        nonlocal piped
        piece = continued("".join(buf), powershell).strip()
        if piece or captured:
            pieces.append((piece, captured.copy(), detached or piped))
        buf.clear()
        captured.clear()
        piped = False

    def open_sub() -> None:
        nonlocal sub
        if sub is None:
            sub = []
        else:
            emit("$(")
        stack.append("$(")

    def close_sub() -> None:
        nonlocal sub
        stack.pop()
        if "$(" in stack:
            emit(")")
            return
        inner = "".join(sub or [])
        if CASE_COMMAND.search(inner) and not re.search(r"\besac\b", inner):
            mark("a `case` inside a substitution")  # its `pattern)` may have closed the substitution early
        captured.append(inner)
        sub = None
        buf.append(PLACEHOLDER)

    while i < n:
        ch = text[i]
        top = stack[-1] if stack else ""
        mode = next((c for c in reversed(stack) if c != "("), "")
        if top == "'":
            if ch == "'":
                stack.pop()
            emit(ch)
            i += 1
            continue
        if top == "$'":
            if ch == "\\" and i + 1 < n:
                emit(text[i : i + 2])
                i += 2
                continue
            if ch == "'":
                stack.pop()
            emit(ch)
            i += 1
            continue
        if top == '@"':
            if text.startswith('\n"@', i):
                stack.pop()
                emit('@""@')
                i += 3
            elif ch == escape and i + 1 < n:
                i += 2
            elif text.startswith("$(", i):
                open_sub()
                i += 2
            else:
                i += 1  # the here-string's text is dropped; its substitutions are not
            continue
        if ch == escape and i + 1 < n:
            pair = 3 if powershell and text.startswith("\r\n", i + 1) else 2
            emit(text[i : i + pair])
            if not (text[i + 1] in "\r\n" and (i == 0 or text[i - 1] in word_start)):
                escaped_until = i + pair  # the word goes on after an escaped character
            i += pair
            continue
        if ch == "\n" and pending and top != '"':
            i += 1
            for term, quoted, dash in pending:
                ended = joined = False
                while i < n and not ended:
                    end = text.find("\n", i)
                    line = (text[i:] if end == -1 else text[i:end]).rstrip("\r")
                    i = n if end == -1 else end + 1
                    ended = not joined and (line.lstrip("\t") if dash else line) == term
                    joined = not quoted and (len(line) - len(line.rstrip("\\"))) % 2 == 1
                if not ended:
                    mark(f"a heredoc that never reaches `{term}`")
            if stack:
                emit("\n" + "\n".join(term for term, _, _ in pending) + "\n")  # an empty heredoc: still parses
            else:
                flush()
            pending = []
            continue
        arith = mode in ("$((", "((")
        if not powershell and text.startswith("$((", i) and mode != "'" and arithmetic_at(text, i + 3):
            stack.append("$((")
            emit("$((")
            i += 3
            continue
        if arith:
            if top in ("$((", "((") and text.startswith("))", i):
                stack.pop()
                emit("))")
                i += 2
                if top == "$((":
                    glued_at = i
            elif text.startswith("$(", i):
                open_sub()
                i += 2
            elif not powershell and text.startswith("$'", i):
                stack.append("$'")
                emit("$'")
                i += 2
            elif ch in "'\"(":
                stack.append(ch)
                emit(ch)
                i += 1
            elif ch == ")" and top == "(":
                stack.pop()
                emit(ch)
                i += 1
            else:
                emit(ch)
                i += 1
            continue
        if mode == "${":
            if top == "${" and ch == "}":
                stack.pop()
                emit(ch)
                i += 1
            elif text.startswith("$(", i):
                open_sub()
                i += 2
            elif text.startswith("${", i):
                stack.append("${")
                emit("${")
                i += 2
            elif text.startswith("$'", i):
                stack.append("$'")
                emit("$'")
                i += 2
            elif ch in "'\"":
                stack.append(ch)
                emit(ch)
                i += 1
            else:
                emit(ch)
                i += 1
            continue
        code = mode in ("", "$(")  # outside quotes: at the top level or inside a `$(`
        at_word = i == 0 or text[i - 1] in word_start
        if code and not powershell and text.startswith("((", i) and at_word and arithmetic_at(text, i + 2):
            stack.append("((")
            emit("((")
            i += 2
            continue
        if code and not powershell and text.startswith("${", i):
            stack.append("${")
            emit("${")
            i += 2
            continue
        if code and powershell and text.startswith("--%", i) and at_word:
            ends = [k for k in (text.find("\n", i), text.find("|", i)) if k != -1]
            end = min(ends) if ends else n
            emit(text[i:end])
            i = end
            continue
        if code and powershell and text.startswith(("@'", '@"'), i):
            eol = text.find("\n", i)
            if not text[i + 2 : n if eol == -1 else eol].strip():
                if text[i + 1] == '"':
                    stack.append('@"')
                    i = n if eol == -1 else eol
                else:
                    end = text.find("\n'@", i)
                    emit("@''@")
                    if end == -1:
                        mark("a here-string that never closes")
                    i = n if end == -1 else end + 3
                continue
        if code and powershell and text.startswith("<#", i):
            end = text.find("#>", i + 2)
            if end == -1:
                mark("a `<#` comment that never closes")
            i = n if end == -1 else end + 2
            continue
        if code and text.startswith("<<<", i):
            emit("<<<")
            i += 3
            continue
        if code and text.startswith("<<", i):
            if m := HEREDOC.match(text, i):
                pending.append((m[2] or m[3], bool(m[2]) or "\\" in m[0], m[0].startswith("<<-")))
                emit(m[0])
                i = m.end()
                continue
            if text[i + 2 : i + 4].strip("- \t"):
                mark("a heredoc whose terminator cannot be read")
            emit("<<")
            i += 2
            continue
        if text.startswith("$(", i) or (code and not powershell and text.startswith(("<(", ">("), i) and at_word):
            open_sub()
            i += 2
            continue
        if top == '"':
            if ch == '"':
                stack.pop()
            emit(ch)
            i += 1
            continue
        # Outside quotes: at the top level, or inside a `$(`.
        if not powershell and text.startswith("$'", i):
            stack.append("$'")
            emit("$'")
            i += 2
        elif ch in "'\"":
            stack.append(ch)
            emit(ch)
            i += 1
        elif ch == "#" and (at_word or ps_assigns(text, i, powershell)) and i not in (escaped_until, glued_at):
            end = text.find("\n", i)
            i = n if end == -1 else end  # the comment goes; the newline still ends the command
        elif ch == "(" and top in ("$(", "("):
            stack.append("(")
            emit(ch)
            i += 1
        elif ch == ")" and top == "(":
            stack.pop()
            emit(ch)
            i += 1
        elif ch == ")" and top == "$(":
            close_sub()
            i += 1
            glued_at = i
        elif stack:
            emit(ch)  # inside a `$(`: part of one word of the enclosing command
            i += 1
        elif text.startswith("|&", i) and not powershell:
            flush(detached=True)
            piped = True
            i += 2
        elif text.startswith(("&&", "||"), i):
            flush()
            i += 2
        elif ch == "&" and text[i - 1 : i] not in (">", "<") and text[i + 1 : i + 2] != ">":
            flush(detached=not powershell)  # bash: sent to the background; PowerShell: the call operator
            i += 1
        elif ch == "|":
            flush(detached=not powershell)
            piped = not powershell
            i += 1
        elif ch in ";\n":
            flush()
            i += 1
        else:
            emit(ch)
            i += 1
    if sub is not None:
        captured.append("".join(sub))
        buf.append(PLACEHOLDER)
    if stack:
        mark(OPEN_CONTEXT.get(stack[-1], "an open context"))
    if pending:
        mark(f"a heredoc that never reaches `{pending[0][0]}`")
    flush()
    if state is not None and opened:
        state["uncertain"] = True
        state.setdefault("open", opened)
    return pieces


def ps_assigns(text: str, i: int, powershell: bool) -> bool:
    """Whether the `#` at `i` follows a PowerShell assignment's `=` (`$x=#note`), where a comment may start - in an
    argument (`--grep=#12`) the `=` is part of the word (checked in Windows PowerShell 5.1, 2026-09-26)."""
    return powershell and bool(re.search(r"(?:\$[\w:]+|[)\]])\s*=$", text[max(0, i - 80) : i]))


def cd_breaks(word: str) -> bool:
    r"""Whether bash mangles a cd operand: an unquoted backslash before a path character escapes the backslash
    away, so `cd C:\repo\x` asks for `C:repox`, fails, and the shell stays where it was (checked in Git Bash,
    2026-09-25). `\\` is a real backslash; `\ ` or `\(` is a deliberate escape and works."""
    quote: str | None = None
    i = 0
    while i < len(word):
        c = word[i]
        if quote is not None:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                i += 1
        elif c in "'\"":
            quote = c
        elif c == "\\":
            following = word[i + 1 : i + 2]
            if following.isalnum() or following in ("", ".", "_", "-"):
                return bool(following)
            i += 1
        i += 1
    return False


def continued(piece: str, powershell: bool) -> str:
    r"""A piece with its line continuations taken out: bash deletes backslash-newline, PowerShell reads
    backtick-newline as a space. An alias-dodging leading backslash (`\cd`) goes too."""
    if powershell:
        return re.sub(r"`\r?\n", " ", piece)
    return re.sub(r"^(\s*)\\(?=[A-Za-z])", r"\1", re.sub(r"\\\r?\n", "", piece))


def bash_cd_fails(piece: str) -> str | None:
    """The cd / pushd word of a bash command whose path operand bash mangles (cd_breaks), else None."""
    piece = continued(piece, False)
    try:
        raw = shlex.split(piece, posix=False)  # quotes and backslashes kept, as bash will see them
    except ValueError:
        raw = piece.split()
    raw = strip_wrappers(raw)
    while raw and ASSIGNMENT.match(raw[0]):
        raw = strip_wrappers(raw[1:])
    if not raw or raw[0].lower() not in ("cd", "pushd"):
        return None
    operands: list[str] = []
    skip = False
    for arg in raw[1:]:
        if skip:
            skip = False
        elif REDIRECT_ALONE.fullmatch(arg):
            skip = True  # its target is no operand
        elif not (arg.startswith("-") or REDIRECT.match(arg)):
            operands.append(arg)
    return raw[0] if operands and cd_breaks(operands[0]) else None


def simple_command(piece: str, powershell: bool = False) -> list[str]:
    r"""The tokens of one command from scan_commands(), wrappers and leading assignments peeled off.

    Line continuations come out first (continued()). Backslashes are Windows path separators here, not escapes -
    posix shlex would eat them - except one before a quote in bash, which escapes it (`printf "{\"k\": 1}"`). A
    bash `cd` whose operand bash would mangle (bash_cd_fails) goes nowhere: its operand becomes CD_FAILS.
    PowerShell escapes with a backtick, so there every backslash is a separator (`Set-Location "C:\A B\repo\"`),
    and `$out = <command>` or `$out=<command>` assigns. `GH_REPO=owner/repo gh ...` keeps the repository as an
    ENV_REPO word that only pr_key() reads - it names the PR's repository below an explicit `-R`, and unlike
    `-R` it still lets gh delete the local branch.
    """
    if not powershell and (word := bash_cd_fails(piece)):
        return [word, CD_FAILS]
    piece = continued(piece, powershell)
    if powershell:
        piece = piece.replace("\\", "/").replace('`"', '\\"')
    else:
        piece = re.sub(r"\\(?![\"'])", "/", piece)
    try:
        tokens = shlex.split(piece, posix=True)
    except ValueError:
        tokens = piece.split()
    env: dict[str, str] = {}
    part = strip_wrappers(tokens)
    while part:
        glued = re.match(r"^\$[\w:]+=(.*)$", part[0]) if powershell else None
        if ASSIGNMENT.match(part[0]):
            name, value = part[0].split("=", 1)
            env[name] = value
            part = strip_wrappers(part[1:])
        elif powershell and len(part) > 1 and part[0].startswith("$") and part[1] == "=":
            part = strip_wrappers(part[2:])
        elif glued:
            part = strip_wrappers(([glued.group(1)] if glued.group(1) else []) + part[1:])
        else:
            break
    if part and "GH_REPO" in env and gh_args(part) is not None:
        part = [*part, ENV_REPO + env["GH_REPO"]]
    return part


def split_blocks(tokens: list[str]) -> list[list[str]]:
    """A command holding a `{` block, as the command before the block and the block's first command:
    `f() { git switch main`, `ForEach-Object { git switch main }`, PowerShell `if ($x) { git checkout -b y }`."""
    for k, token in enumerate(tokens):
        if k and token == "{":
            rest = tokens[k + 1 :]
            if rest and rest[-1] == "}":
                rest = rest[:-1]
            rest = strip_wrappers(rest)
            return [tokens[:k], *split_blocks(rest)] if rest else [tokens[:k]]
    return [tokens]


class Segment(NamedTuple):
    tokens: list[str]
    group: int  # 0: a command of the call itself; else the command substitution it came from
    detached: bool  # a bash pipeline stage or background command: its cd moves nothing


def segments_tagged(
    command: str, powershell: bool = False, state: dict[str, Any] | None = None
) -> tuple[list[Segment], dict[int, int]]:
    """segments() as Segments, and each substitution's parent (0 for the call itself). Nesting deeper than
    MAX_NESTING is not taken apart; it marks the call uncertain instead."""
    out: list[Segment] = []
    parents: dict[int, int] = {}
    numbers = itertools.count(1)

    def walk(text: str, group: int, depth: int) -> None:
        for piece, captured, detached in scan_commands(text, powershell, state):
            if captured and depth >= MAX_NESTING and state is not None:
                state["uncertain"] = True
                state.setdefault("open", f"substitutions nested deeper than {MAX_NESTING}")
            for inner in captured if depth < MAX_NESTING else []:
                number = next(numbers)
                parents[number] = group
                start = len(out)
                walk(inner, number, depth + 1)
                # A captured `gh auth token` prints nothing; a captured `$expression` runs nothing in PowerShell.
                out[start:] = [
                    s
                    for s in out[start:]
                    if (gh_args(s.tokens) or [])[:2] != ["auth", "token"] and not value_only(s.tokens, powershell)
                ]
            if tokens := simple_command(piece, powershell):
                out.extend(Segment(part, group, detached) for part in split_blocks(tokens) if part)

    walk(command, 0, 0)
    return out, parents


def segments(command: str, powershell: bool = False) -> list[list[str]]:
    """Split a shell command into simple-command token lists - its command substitutions' commands first."""
    return [s.tokens for s in segments_tagged(command, powershell)[0]]


def unclear(command: str, powershell: bool = False) -> str | None:
    """What scan_commands() could not close in this call, at any nesting level, or None when it reads cleanly."""
    state: dict[str, Any] = {}
    segments_tagged(command, powershell, state)
    return state.get("open") if state.get("uncertain") else None


def naive_segments(command: str) -> list[list[str]]:
    """The call cut at every separator, bracket and substitution, quotes ignored: the fallback for an unclear()
    call, where the careful scan may have hidden a command. It finds anything hidden, at the price of false holds."""
    out: list[list[str]] = []
    command = re.sub(r"[\\`]\r?\n", " ", command)
    for chunk in re.split(r"&&|\|\||\$\(|[;|&\n()`{}]", command):
        chunk = chunk.replace("\\", "/")
        try:
            tokens = shlex.split(chunk, posix=True)
        except ValueError:
            tokens = chunk.split()
        part = strip_wrappers(tokens)
        while part:
            glued = re.match(r"^\$[\w:]+=(.*)$", part[0])
            if ASSIGNMENT.match(part[0]):
                part = strip_wrappers(part[1:])
            elif glued:  # PowerShell `$out=gh pr merge 29`
                part = strip_wrappers(([glued.group(1)] if glued.group(1) else []) + part[1:])
            elif part[0].startswith("$") and part[1:2] == ["="]:  # PowerShell `$out = gh pr merge 29`
                part = strip_wrappers(part[2:])
            else:
                break
        if part:
            out.append(part)
    return out


def value_only(tokens: list[str], powershell: bool) -> bool:
    """A command that only prints a value: PowerShell's lone `$expression`, or the placeholder a substitution left.
    In bash a lone `$CMD` runs the command stored in CMD."""
    return len(tokens) == 1 and (tokens[0] == PLACEHOLDER or (powershell and tokens[0].startswith("$")))


def located(command: str, cwd: str, powershell: bool = False) -> Iterator[tuple[list[str], PureWindowsPath | None]]:
    """Each command of a call but cd / Set-Location / pushd / popd, with the directory it runs in, from `cwd`.

    A bash `$(...)` is a subshell: it starts in the directory of the command around it - of the nearest
    enclosing one that has started, at any depth - and its own `cd` stays inside. So does a bash pipeline stage
    or background command. A PowerShell subexpression shares the caller's location. popd returns to the
    directory its pushd left, and with nothing pushed stays.
    """
    found, parents = segments_tagged(command, powershell)
    dirs: dict[int, PureWindowsPath | None] = {0: norm(cwd) if cwd else None}
    pushed: dict[int, list[PureWindowsPath | None]] = {}

    def started(scope: int) -> PureWindowsPath | None:
        if scope not in dirs:
            dirs[scope] = started(parents.get(scope, 0))
        return dirs[scope]

    for seg in found:
        scope = seg.group if seg.group and not powershell else 0
        here = started(scope)
        head = seg.tokens[0].lower()
        moves = powershell or not seg.detached
        if head in CD_COMMANDS:
            if moves:
                if head in PUSH_COMMANDS:
                    pushed.setdefault(scope, []).append(here)
                dirs[scope] = cd_target(seg.tokens[1:], here)
        elif head in POP_COMMANDS:
            if moves:
                stack = pushed.get(scope)
                dirs[scope] = stack.pop() if stack else here
        else:
            yield seg.tokens, here


def naive_located(command: str, cwd: str) -> Iterator[tuple[list[str], PureWindowsPath | None]]:
    """naive_segments() with every directory the call may be in: the start, and each cd target seen so far - a
    naive read cannot tell which cd ran, so a command counts in all of them."""
    seen: list[PureWindowsPath | None] = [norm(cwd) if cwd else None]
    for tokens in naive_segments(command):
        head = tokens[0].lower()
        if head in CD_COMMANDS:
            target = cd_target(tokens[1:], seen[-1])
            if target is not None and target not in seen:
                seen.append(target)
        elif head not in POP_COMMANDS:
            for where in seen:
                yield tokens, where


def cd_target(args: list[str], current: PureWindowsPath | None) -> PureWindowsPath | None:
    """Where cd / Set-Location / pushd with `args` leaves the tracked dir; None when unknown."""
    if CD_FAILS in args:
        return current  # bash mangled the path: the cd failed and the shell stayed
    operands: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a.lower() in VALUE_FLAGS or REDIRECT_ALONE.fullmatch(a):
            skip = True  # a flag's value, or a redirection's target
        elif REDIRECT.match(a):
            continue  # `2>err.log`, `2>&1`: not where the cd goes
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
    """What a git, `gh pr checkout` or `gh pr merge -d` invocation does, or None when `tokens` is none of them."""
    name = PureWindowsPath(tokens[0]).name.lower()
    if name in ("gh", "gh.exe"):
        label = " ".join(["gh", *(t for t in tokens[1:] if not t.startswith(ENV_REPO))])
        args = tokens[3:]
        deletes = any(
            re.match(r"^-[a-zA-Z]*d[a-zA-Z]*$|^--delete-branch(?:=(?:1|t|T|true|TRUE|True))?$", a) for a in args
        )
        remote = any(a in ("-R", "--repo") or a.startswith(("--repo=", "-R")) for a in args)  # gh keeps them
        if tokens[1:3] == ["pr", "merge"] and deletes and not remote:
            hint = (
                " Or merge without `-d` / `--delete-branch`, or run the merge from your own worktree, where gh"
                " leaves the checkout alone: with the PR's branch checked out here, gh would check out the base"
                " branch and pull."
            )
            return Call(current, label, True, hint)
        if tokens[1:3] != ["pr", "checkout"]:
            return None
        return Call(current, label, True, "")
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


def check_command(command: str, cwd: str, config: Config, powershell: bool = False) -> str | None:
    """A block reason for a Bash/PowerShell command, or None to allow it.

    An unclear() call is also read naively (naive_located), so a command the careful scan hid is still seen.
    """
    found = list(located(command, cwd, powershell))
    if unclear(command, powershell):
        found += list(naive_located(command, cwd))
    for tokens, current in found:
        call = repo_call(tokens, current)
        if call is None:
            continue
        reason = None
        if where := match(call.target, config.frozen):
            reason = frozen_reason(f"`{call.label}` in {call.target}", where, config)
        elif (where := match(call.target, config.shared)) and call.changes_shared:
            base = where.shown.rstrip("/\\")
            reason = (
                f"guard-shared-checkouts: blocked `{call.label}` in {call.target} - {where.shown} is a checkout "
                f"other sessions use at the same time, and changing its branch or stash moves their work too. "
                f"Work on a branch in your own worktree: git -C {base} worktree add {base}_wt_<name> -b <branch>"
                f"{call.hint}"
            )
        if reason:
            return reason + (CD_HINT if any(CD_FAILS in t for t in segments(command, powershell)) else "")
    return None


def check_edit(file_path: str, cwd: str, config: Config) -> str | None:
    """A block reason for an Edit/Write/MultiEdit/NotebookEdit target, or None to allow it."""
    target = norm(file_path, norm(cwd) if cwd else None)
    if where := match(target, config.frozen):
        return frozen_reason(f"editing {file_path}", where, config)
    return None


def gh_args(tokens: list[str]) -> list[str] | None:
    """The arguments after `gh` when `tokens` run gh (named bare or by path), else None."""
    if not tokens:
        return None
    exe = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return tokens[1:] if exe in ("gh", "gh.exe") else None


def api_put_merge(args: list[str]) -> bool:
    """`gh api` with method PUT on a pull request's merge endpoint. Without PUT the same path only asks."""
    if args[:1] != ["api"] or not any(MERGE_ENDPOINT.search(a) for a in args[1:]):
        return False
    method = "GET"
    for i, arg in enumerate(args):
        if arg in ("-X", "--method") and i + 1 < len(args):
            method = args[i + 1]
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1]
        elif arg.startswith("-X") and len(arg) > 2:
            method = arg[2:]
    return method.upper() == "PUT"


def merge_kind(tokens: list[str]) -> str | None:
    """ "cli" for `gh pr merge ...`, "api" for a PUT to the REST merge endpoint or a GraphQL mergePullRequest.

    `--help` and `--disable-auto` merge nothing.
    """
    args = gh_args(tokens)
    if args is None:
        return None
    if args[:2] == ["pr", "merge"]:
        return None if any(a in ("-h", "--help", "--disable-auto") for a in args[2:]) else "cli"
    if args[:2] == ["api", "graphql"] and any(
        re.search(r"mergePullRequest|enablePullRequestAutoMerge", a) for a in args
    ):
        return "api"
    return "api" if api_put_merge(args) else None


def buried_merge(tokens: list[str]) -> bool:
    """A `gh pr merge` inside another command: `xargs gh pr merge`, `ForEach-Object { gh pr merge $_ }`."""
    return any(
        gh_args([tokens[i].lstrip("$@({<>")]) is not None and tokens[i + 1 : i + 3] == ["pr", "merge"]
        for i in range(1, len(tokens) - 2)
    )


def is_merge(tokens: list[str]) -> bool:
    return merge_kind(tokens) is not None or buried_merge(tokens)


def is_review_role(role: object) -> bool:
    """`review`, or a plugin-namespaced `<plugin>:review`."""
    return isinstance(role, str) and role.strip().lower().rsplit(":", 1)[-1] == REVIEW_ROLE


def did_not_run(block: dict[str, Any]) -> bool:
    """Whether a call's result shows it never ran: refused by auto mode, declined at a prompt, or held by a hook.

    The marker must open an error result, as no-punt's refused_body() requires (182 of 182 real refusals): a
    merge that ran and printed one of these words further on still ran.
    """
    if block.get("is_error") is not True:
        return False
    raw = block.get("content")
    if isinstance(raw, list):
        raw = "\n".join(str(b.get("text", "")) for b in raw if isinstance(b, dict))
    body = str(raw or "")
    return any(0 <= body.find(marker) < 120 for marker in DID_NOT_RUN)


def pr_key(tokens: list[str], where: str) -> tuple[str, str]:
    """Which pull request a merge names: (repository, number) - or a key of its own that matches nothing.

    Only a literal PR number - with a literal `-R owner/repo`, else scoped to the directory the merge runs in
    (`where`, "?" when unknown) - or a github.com PR URL names a PR. No argument (the current branch's PR, which
    changes with the branch), a branch name, a variable (`"$url"`) or a substitution may mean another PR each
    time, so each gets a fresh key: never the same PR, not even as itself - the strict side. Redirections
    (`2>&1`, `> out`) and flag values are skipped.
    """
    args = gh_args(tokens) or []
    repo = ref = env_repo = ""
    if merge_kind(tokens) == "api":
        for arg in args[1:]:
            if found := MERGE_ENDPOINT.search(arg):
                repo, ref = found.group(1), found.group(2)
    else:
        rest = args[2:]
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg in ("-R", "--repo") and i + 1 < len(rest):
                repo = rest[i + 1]
                i += 1
            elif arg.startswith("--repo="):
                repo = arg.split("=", 1)[1]
            elif arg.startswith("-R") and len(arg) > 2:
                repo = arg[2:]
            elif arg.startswith(ENV_REPO):
                env_repo = arg[len(ENV_REPO) :]
            elif arg in MERGE_VALUE_FLAGS or REDIRECT_ALONE.fullmatch(arg):
                i += 1  # a flag's value, or a redirection's target, is not the PR
            elif not (arg.startswith("-") or REDIRECT.match(arg) or ref):
                ref = arg
            i += 1
        if found := PR_URL.search(ref):
            repo, ref = found.group(1), found.group(2)
        repo = repo or env_repo
    number = PR_NUMBER.fullmatch(ref)
    literal_repo = bool(repo) and not re.search(r"[$`{%]", repo)
    if number and (literal_repo or (not repo and where != "?")):
        return (repo.lower() if repo else f"@{where}", number.group(1))
    return ("?", uuid.uuid4().hex)


def merges_in(command: str, cwd: str, powershell: bool = False) -> list[tuple[list[str], str]]:
    """Each merge in a command, with the directory it runs in (located())."""
    return [
        (tokens, str(current).lower() if current else "?")
        for tokens, current in located(command, cwd, powershell)
        if is_merge(tokens)
    ]


def reviewed_for(transcript: Path, current: str, merges: list[tuple[list[str], str]], command: str = "") -> bool | None:
    """Whether a `review` was spawned in the session after the last merge of another PR that may have run.

    The review counts for the PR it precedes. A retry of the same PR (`keys` are this merge's) never spends it -
    held here, refused, turned down by GitHub or merged with a failed cleanup alike. A merge of another PR spends
    it unless its result shows it never ran (did_not_run); an error result alone is not that, since gh exits 1
    when only the cleanup after a merge fails (c--repo-EU 2026-09-25: "failed to delete local branch ...", then
    "MERGED 0cc157ce" - and the next PR's merge was refused). A merge with no result yet counts as run. `gh pr
    create` does not spend a review: the usual flow reviews the branch, then opens the PR. `current` is this
    call's tool_use_id; the transcript may already hold the call - without an id, the last merge call with this
    `command` and no result yet is taken to be it. A `gh repo set-default` changes what a bare number names, so a
    number before it and the same number after it are two PRs. `merges` are this call's, with their directories.
    None if the transcript cannot be read.
    """
    epoch = 0

    def scoped(where: str) -> str:
        return where if where == "?" else f"{where}#{epoch}"

    events: list[tuple[str, set[tuple[str, str]] | None, str]] = []  # (id, its merges' keys or None = review, cmd)
    never_ran: set[str] = set()
    answered: set[str] = set()  # merge calls with a result
    merge_ids: set[str] = set()
    try:
        with transcript.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Parse only what can matter - a review spawn, anything mentioning a merge or a default-repo change,
                # the result of an earlier merge call: a 76 MB transcript is read in well under a second.
                if not (
                    "subagent_type" in line
                    or "merg" in line.lower()
                    or "set-default" in line
                    or (merge_ids and '"tool_result"' in line and any(uid in line for uid in merge_ids))
                ):
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
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_result":
                        uid = str(block.get("tool_use_id"))
                        if uid in merge_ids:
                            answered.add(uid)
                            if did_not_run(block):
                                never_ran.add(uid)
                        continue
                    if block.get("type") != "tool_use" or (current and block.get("id") == current):
                        continue
                    tool_input = block.get("input")
                    if not isinstance(tool_input, dict):
                        continue
                    uid = str(block.get("id"))
                    if block.get("name") in ("Agent", "Task") and is_review_role(tool_input.get("subagent_type")):
                        events.append((uid, None, ""))
                    elif block.get("name") in SHELL_TOOLS:
                        text = str(tool_input.get("command") or "")
                        powershell = block.get("name") == "PowerShell"
                        found = merges_in(text, str(record.get("cwd") or ""), powershell)
                        if found:
                            events.append((uid, {pr_key(tokens, scoped(where)) for tokens, where in found}, text))
                            merge_ids.add(uid)
                        if any(
                            (gh_args(t) or [])[:2] == ["repo", "set-default"] and not {"-v", "--view"} & set(t)
                            for t in segments(text, powershell)
                        ):
                            epoch += 1
    except OSError as exc:
        notice(f"cannot read the transcript {transcript} ({exc}) - the merge goes unchecked")
        return None
    keys = {pr_key(tokens, scoped(where)) for tokens, where in merges}
    if not current:
        mine = [e for e in events if e[1] is not None and e[2] == command and e[0] not in answered]
        if mine:
            events.remove(mine[-1])
    reviewed = False
    for uid, merged, _ in events:
        if merged is None:
            reviewed = True
        elif uid not in never_ran and merged - keys:
            reviewed = False  # another PR's merge may have run: this one needs a review of its own
    return reviewed


def harmless(tokens: list[str], powershell: bool = False) -> bool:
    """Whether a command may share a call with a merge: CHAIN_OK, CHAIN_OK_GH, or CHAIN_OK_GIT without GIT_RISKY.

    So is one that only prints a value (value_only()); in bash a lone `$CMD` runs a command and is not harmless.
    """
    if value_only(tokens, powershell):
        return True
    if powershell and tokens[0].startswith("$") and PS_OPERATORS & {t.lower() for t in tokens}:
        return True  # a PowerShell condition such as `$LASTEXITCODE -eq 0`: an expression, not a command
    exe = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    if exe in CHAIN_OK:
        return True
    if exe == "gh":
        return tuple(tokens[1:3]) in CHAIN_OK_GH
    if exe != "git":
        return False
    args = tokens[1:]
    while args and args[0].startswith("-"):  # global options: -C <dir>, -c <key=value>, --no-pager ...
        args = args[2:] if args[0] in ("-C", "-c") else args[1:]
    if not args or args[0] not in CHAIN_OK_GIT:
        return False
    return args[0] not in GIT_MOVES or not any(a in GIT_RISKY or a.startswith("--force") for a in args[1:])


def check_merge(
    command: str, transcript: str, current: str = "", cwd: str = "", powershell: bool = False
) -> str | None:
    """The block message for a merge auto mode would refuse - in a call that does not parse cleanly, chained,
    through the API, or unreviewed - else None."""
    # An unclear call: a merge that heads any naive chunk is held (a merge only named in prose is not).
    if (why := unclear(command, powershell)) and any(merge_kind(t) for t in naive_segments(command)):
        return MERGE_UNCLEAR.format(open=why)
    parts = segments(command, powershell)
    merges = [t for t in parts if is_merge(t)]
    if not merges:
        return None
    if re.search(r"[<>]\(", command):
        return MERGE_CHAINED  # a merge beside a process substitution is no call of its own
    if any(merge_kind(t) == "api" for t in merges):
        return MERGE_API
    only = merges[0]
    if len(merges) > 1 or merge_kind(only) is None or buried_merge(only):
        return MERGE_CHAINED
    if not all(t is only or harmless(t, powershell) for t in parts):
        return MERGE_CHAINED
    if not transcript:
        notice("no transcript_path in the payload - the merge goes unchecked")
        return None
    if reviewed_for(Path(transcript), current, merges_in(command, cwd, powershell), command) is False:
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
                str(event.get("cwd") or ""),
                tool == "PowerShell",
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
        reason = check_command(str(tool_input.get("command") or ""), cwd, config, tool == "PowerShell")
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
