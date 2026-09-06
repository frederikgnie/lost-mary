#!/usr/bin/env python3
"""permit: PermissionRequest hook - allow the routine, decide nothing else.

Why this exists
---------------
Two situations ask for permission on things the operating rules already call
routine. A session in Manual mode (for instance one driven from a phone) prompts
for every command a prefix rule does not name. A subagent in a background or
non-interactive context cannot prompt at all and is auto-denied unless a
PermissionRequest hook allows (documented). Prefix rules cannot say "any branch
except main"; this hook can.

It ALLOWS three things and never denies. A request it does not recognise gets no
decision and passes through unchanged to the normal flow - prompt, auto-mode
classifier, deny and ask rules - and an allow from here is still subject to
deny and ask rules (documented).

  1. git push to a branch that is not main or master: no force, no delete, no
     --no-verify, no tags; the target is an explicit refspec or the current
     branch resolved from the repository - unknown or main/master means no
     decision.
  2. a project's own validation: EVERY segment of the command is a pytest /
     ruff / ty run (check-evidence's classifier) or a benign wrapper (cd, echo,
     tail, head, grep, wc, sort, cut, cat, ls, true, read-only git ...), with
     redirects only to relative paths or /dev/null. One foreign segment - no
     decision. Heredoc bodies are ignored, as in check-evidence.
  3. the library's own installers in verify mode.

Output: {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
"decision": {"behavior": "allow"}}} on stdout, exit 0. Nothing on stdout
otherwise. Exit 2 is ignored for this event (documented), so it is never used.
Fails SILENT-OPEN: an unreadable payload produces a notice on stderr and no
decision.

Runtime dependencies (see capabilities.md): PermissionRequest stdin fields
`tool_name`, `tool_input.command`, `cwd`; the documented `decision` object;
`git symbolic-ref --short HEAD` to resolve the current branch.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
DEFAULT_BRANCHES = frozenset({"main", "master"})
BENIGN = frozenset(
    {
        "cd",
        "echo",
        "printf",
        "tail",
        "head",
        "grep",
        "wc",
        "sort",
        "uniq",
        "cut",
        "cat",
        "ls",
        "true",
        "pwd",
        "date",
        "tr",
        "sed",  # sed only without -i, see benign_segment
        "Get-ChildItem",
        "Get-Content",
        "Get-Item",
        "Test-Path",
        "Select-String",
        "Get-Date",
        "Write-Host",
        "Write-Output",
        "Set-Location",
    }
)
READONLY_GIT = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "fetch", "describe"})
PUSH_FLAGS_OK = frozenset({"-u", "--set-upstream", "-q", "--quiet", "-v", "--verbose", "--porcelain"})
PUSH = re.compile(r"^git\s+(?:-C\s+(?P<dir>\S+)\s+)?(?:-c\s+\S+\s+)*push\b(?P<rest>.*)$", re.IGNORECASE)
INSTALL_VERIFY = re.compile(
    r"^(?:bash\s+)?\.?[\\/]?install\.sh\s+--verify$|^powershell\b.*-File\s+\.?[\\/]?install\.ps1\s+-Verify$",
    re.IGNORECASE,
)
REDIRECT = re.compile(r"(?<![0-9&])>+\s*(\S+)")
ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
DECISION = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}


def notice(text: str) -> None:
    print(f"permit: {text}", file=sys.stderr)


def read_payload() -> dict[str, Any] | None:
    buffer = getattr(sys.stdin, "buffer", None)
    raw = buffer.read() if buffer is not None else sys.stdin.read().encode("utf-8")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_evidence_module() -> Any:
    """check-evidence.py by path: it owns the definition of a validation run and the heredoc/segment splitting."""
    path = Path(__file__).with_name("check-evidence.py")
    spec = importlib.util.spec_from_file_location("check_evidence", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_evidence"] = module
    spec.loader.exec_module(module)
    return module


def current_branch(directory: Path) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(directory), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    branch = done.stdout.strip()
    return branch if done.returncode == 0 and branch else None


def push_allowed(segment: str, cwd: Path | None) -> bool | None:
    """True: a plain push to a non-default branch. None: not a push. False: a push this hook will not decide."""
    match = PUSH.match(segment)
    if not match:
        return None
    tokens = match.group("rest").split()
    positional: list[str] = []
    for token in tokens:
        if token.startswith("-"):
            if token not in PUSH_FLAGS_OK:
                return False  # --force, -f, --force-with-lease, --delete, -d, --tags, --all, --mirror, --no-verify ...
            continue
        positional.append(token)
    if len(positional) > 2:
        return False
    refspec = positional[1] if len(positional) == 2 else None
    if refspec is not None:
        if refspec.startswith("+") or refspec.startswith(":"):
            return False  # forced or deleting refspec
        target = refspec.split(":", 1)[1] if ":" in refspec else refspec
        if target.startswith("refs/tags/"):
            return False
        if target.upper() != "HEAD":
            return target.split("/")[-1] not in DEFAULT_BRANCHES and target not in DEFAULT_BRANCHES
    # No refspec, or HEAD: the target is the current branch of the repository the push runs in.
    directory = Path(match.group("dir")) if match.group("dir") else cwd
    if directory is not None and not directory.is_absolute() and cwd is not None:
        directory = cwd / directory
    branch = current_branch(directory) if directory is not None else None
    return branch is not None and branch not in DEFAULT_BRANCHES


def redirects_ok(segment: str) -> bool:
    for target in REDIRECT.findall(segment):
        if target in ("/dev/null", "NUL", "nul"):
            continue
        if target.startswith(("/", "~", "\\")) or re.match(r"^[A-Za-z]:", target) or target.startswith(".."):
            return False
    return True


def benign_segment(segment: str) -> bool:
    body = ENV_PREFIX.sub("", segment).strip()
    tokens = body.split()
    if not tokens:
        return True
    head = tokens[0].strip("\"'")
    if head == "git":
        return len(tokens) > 1 and tokens[1] in READONLY_GIT and redirects_ok(body)
    if head == "sed" and "-i" in tokens:
        return False
    return head in BENIGN and redirects_ok(body)


def decide(command: str, cwd: Path | None, evidence: Any) -> bool:
    """True when every segment is routine and at least one is a push, a validation run or a verify."""
    cleaned = evidence.HEREDOC.sub(" ", command) if hasattr(evidence, "HEREDOC") else command
    segments = [s.strip() for s in evidence.SEGMENT_SPLIT.split(cleaned) if s.strip()]
    if not segments:
        return False
    substantive = False
    for segment in segments:
        pushed = push_allowed(segment, cwd)
        if pushed is True:
            substantive = True
            continue
        if pushed is False:
            return False
        if INSTALL_VERIFY.match(segment):
            substantive = True
            continue
        if evidence.classify(segment) and redirects_ok(segment):
            substantive = True
            continue
        if benign_segment(segment):
            continue
        return False
    return substantive


def main() -> int:
    payload = read_payload()
    if payload is None:
        notice("unreadable payload - no decision")
        return 0
    if payload.get("tool_name") not in SHELL_TOOLS:
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0
    cwd_raw = payload.get("cwd")
    cwd = Path(cwd_raw) if isinstance(cwd_raw, str) and cwd_raw else None
    try:
        evidence = load_evidence_module()
    except (ImportError, OSError, SyntaxError) as exc:
        notice(f"check-evidence.py not loadable ({exc}) - no decision")
        return 0
    if decide(command, cwd, evidence):
        print(json.dumps(DECISION))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a hook fails open, loudly - here: no decision, never a traceback
        notice(f"unexpected error ({exc!r}) - no decision")
        sys.exit(0)
