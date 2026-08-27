#!/usr/bin/env python3
"""PreToolUse hook: keep nominally read-only agent roles from mutating the tree.

Why this exists
---------------
`tools:` / `disallowedTools:` in agent frontmatter can withhold Write and Edit,
but the read-only roles still need `Bash` for inspection (`git diff`, `rg`).
Bash can write files, so the allowlist alone does not make a role read-only.
`permissions.deny` in settings.json is session-wide and cannot be scoped to one
subagent, so a hook is the only place this can be enforced per role.

HONEST LIMITS: this is a guardrail, not a sandbox. A denylist over shell text
cannot be complete — obfuscation, unusual interpreters, and scripts invoked by
path can all slip past. It reliably catches the realistic ways a review agent
mutates state (redirection, in-place sed, rm/mv, git writes, installs). Treat
the Write/Edit denial as the boundary and this as defense in depth.

Wiring: PreToolUse matcher "Bash". Exit 2 blocks and returns stderr to the agent.

Exit 0 = allow, 2 = block.
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

# Roles that must not mutate the working tree. Matched against `agent_type`,
# which PreToolUse supplies only when firing inside a subagent.
READ_ONLY_AGENTS = frozenset(
    {"architect", "researcher", "reviewer", "security-reviewer"}
)

# Redirections that write nothing real.
_BENIGN_REDIRECT = re.compile(r"\d?>>?\s*(?:/dev/null|&\s*\d)")

WRITE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Lookbehind/ahead keep `->`, `=>`, `>=`, `2>&1` and fd dups from tripping this.
    (re.compile(r"(?<![0-9<>=!-])>>?(?!\s*(?:/dev/null|&|=))"), "output redirection to a file"),
    (re.compile(r"\bsed\b[^|;&]*\s-[a-zA-Z]*i\b"), "sed in-place edit (-i)"),
    (re.compile(r"\b(?:perl|ruby)\b[^|;&]*\s-[a-zA-Z]*i\b"), "in-place interpreter edit (-i)"),
    (re.compile(r"\btee\b"), "tee writes files"),
    (re.compile(r"\b(?:rm|mv|cp|mkdir|rmdir|touch|truncate|chmod|chown|ln|dd)\b"), "filesystem mutation command"),
    # Only subcommands that alter the working tree or history. `git diff/log/show/
    # status/blame/config --get/remote -v/fetch` stay available for inspection.
    (re.compile(r"\bgit\s+(?:commit|add|push|checkout|switch|reset|restore|clean|apply|stash|rebase|merge|cherry-pick|revert|pull|am|mv|rm|tag)\b"), "git command that writes working tree or history"),
    (re.compile(r"\bgit\s+worktree\s+(?:add|remove|prune)\b"), "git worktree mutation"),
    (re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add|remove|update|ci|link|publish)\b"), "package install/modify"),
    (re.compile(r"\b(?:pip|pip3|uv|poetry|pipx|conda)\s+(?:install|add|remove|uninstall|sync|update|upgrade)\b"), "package install/modify"),
    (re.compile(r"\bcargo\s+(?:add|remove|install|publish|update)\b"), "package install/modify"),
    (re.compile(r"\b(?:python|python3|node|deno)\s+-(?:c|e)\b"), "inline interpreter code can write files"),
    (re.compile(r"\b(?:curl|wget)\b[^|;&]*\s-[a-zA-Z]*[oO]\b"), "download to file"),
)


def offending_reason(command: str) -> str | None:
    """Return why `command` is write-shaped, or None if it looks read-only."""
    # Neutralise benign redirections so they do not trip the redirect pattern.
    scrubbed = _BENIGN_REDIRECT.sub(" ", command)
    for pattern, reason in WRITE_PATTERNS:
        if pattern.search(scrubbed):
            return reason
    return None


def read_stdin_json() -> Any:
    """Parse a JSON payload from stdin, tolerant of encoding and BOM.

    Reads BYTES and decodes utf-8-sig rather than using text mode. On Windows
    text-mode stdin decodes with the locale codepage, so the UTF-8 BOM that
    PowerShell prepends when piping to a native executable arrives as mojibake
    rather than U+FEFF and cannot be stripped as a character. utf-8-sig removes
    the BOM and forces correct decoding regardless of locale.
    """
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        raw = buffer.read().decode("utf-8-sig", "replace")
    else:
        raw = sys.stdin.read().lstrip("﻿")
    return json.loads(raw.strip())


def main() -> int:
    try:
        event = read_stdin_json()
    except (json.JSONDecodeError, ValueError) as exc:
        # Fail open: a hook that wedges on an unexpected payload would break
        # every session. Announce it, so enforcement never vanishes silently.
        print(f"guard-readonly-bash: unreadable hook payload ({exc}); allowing.", file=sys.stderr)
        return 0
    if not isinstance(event, dict):
        print("guard-readonly-bash: hook payload was not an object; allowing.", file=sys.stderr)
        return 0

    agent_type = event.get("agent_type")
    # Absent agent_type means the main session, which is not restricted here.
    if not isinstance(agent_type, str) or agent_type not in READ_ONLY_AGENTS:
        return 0

    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0

    reason = offending_reason(command)
    if reason is None:
        return 0

    print(
        f"Blocked: the '{agent_type}' role is read-only, but this Bash command "
        f"performs {reason}.\n"
        f"Command: {command.strip()[:400]}\n"
        "Inspect with read-only commands (git diff/log/show, rg, ls, cat) and "
        "report the change you would make in your handoff instead of applying it.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
