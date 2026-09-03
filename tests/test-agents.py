#!/usr/bin/env python3
"""Structural checks for the agent definitions under agents/.

A malformed agent file fails silently (the role does not register) and a role
that grows a tool it should not have (Bash on a review role) silently loses the
property the library promises. Both are asserted here. Stdlib only.

Usage: python tests/test-agents.py     (exit 0 = all valid)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "agents"

# Frontmatter keys Claude Code documents for subagents (code.claude.com/docs/en/sub-agents).
KNOWN_KEYS = {
    "name",
    "description",
    "model",
    "effort",
    "permissionMode",
    "tools",
    "disallowedTools",
    "maxTurns",
    "skills",
    "mcpServers",
    "memory",
    "background",
    "isolation",
    "color",
    "initialPrompt",
    "hooks",
    "experimental",
}
MODEL_ALIASES = {"sonnet", "opus", "haiku", "fable", "inherit"}
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}

# The property each role promises. Read-only roles must not be able to mutate
# the tree by any route, which on Windows (no Bash sandbox) means no Bash.
ROLE_RULES: dict[str, dict[str, set[str]]] = {
    "explore": {"forbid": WRITE_TOOLS, "require": {"Read", "Grep", "Glob"}},
    "review": {"forbid": WRITE_TOOLS, "require": {"Read", "Grep", "Glob"}},
    "implement": {"forbid": set(), "require": {"Read", "Edit", "Write", "Bash"}},
}
# Text every role must carry so behaviour matches the docs.
BODY_MUST_MENTION: dict[str, tuple[str, ...]] = {
    "explore": ("CLAUDE.md", "FINDINGS", "path:line"),
    "implement": ("CLAUDE.md", "CHANGED:", "RAN:", "DONE MEANS:", "RISKS:"),
    "review": ("CLAUDE.md", "POSTURE:", "FINDINGS:"),
}

failures: list[str] = []


def parse_frontmatter(text: str, label: str) -> tuple[dict[str, str], str] | None:
    if not text.startswith("---\n"):
        failures.append(f"{label}: does not open with a '---' frontmatter block")
        return None
    end = text.find("\n---", 4)
    if end == -1:
        failures.append(f"{label}: frontmatter block is never closed")
        return None
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            failures.append(f"{label}: frontmatter line is not 'key: value': {line!r}")
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data, text[end + 4 :]


def split_tools(value: str) -> set[str]:
    value = value.strip().strip("[]")
    return {t.strip().strip("'\"") for t in value.split(",") if t.strip()}


def main() -> int:
    files = sorted(AGENTS.glob("*.md"))
    if not files:
        print(f"no agent files under {AGENTS}", file=sys.stderr)
        return 1
    seen: set[str] = set()
    for path in files:
        label = f"agents/{path.name}"
        parsed = parse_frontmatter(path.read_text(encoding="utf-8"), label)
        if parsed is None:
            continue
        front, body = parsed

        for key in ("name", "description"):
            if not front.get(key):
                failures.append(f"{label}: frontmatter missing required key {key!r}")
        unknown = sorted(set(front) - KNOWN_KEYS)
        if unknown:
            failures.append(f"{label}: unrecognized frontmatter keys {unknown}")

        name = front.get("name", "")
        if name != path.stem:
            failures.append(f"{label}: name {name!r} does not match file name {path.stem!r}")
        if not re.fullmatch(r"[a-z0-9-]+", name):
            failures.append(f"{label}: name must match ^[a-z0-9-]+$")
        seen.add(name)

        model = front.get("model")
        if model and model not in MODEL_ALIASES and not model.startswith("claude-"):
            failures.append(f"{label}: model {model!r} is not an alias or full model id")
        effort = front.get("effort")
        if effort and effort not in EFFORT_LEVELS:
            failures.append(f"{label}: effort {effort!r} not in {sorted(EFFORT_LEVELS)}")

        if "tools" not in front:
            failures.append(f"{label}: no 'tools' allowlist - the role would inherit every tool")
        tools = split_tools(front.get("tools", ""))
        rules = ROLE_RULES.get(name)
        if rules is None:
            failures.append(f"{label}: no ROLE_RULES entry - add one so the role's tool property is asserted")
        else:
            bad = sorted(tools & rules["forbid"])
            if bad:
                failures.append(f"{label}: read-only role has mutating tools {bad}")
            missing = sorted(rules["require"] - tools)
            if missing:
                failures.append(f"{label}: missing required tools {missing}")

        if len(body.strip()) < 300:
            failures.append(f"{label}: body is too short to be a usable role")
        for needle in BODY_MUST_MENTION.get(name, ()):
            if needle not in body:
                failures.append(f"{label}: body does not mention {needle!r}")
        if re.search(r"^\s*@[~./\w-]+\.(?:md|json|ya?ml|txt)\s*$", body, re.MULTILINE):
            failures.append(f"{label}: body contains an @-import line; those work in CLAUDE.md only")

        if not [f for f in failures if f.startswith(label)]:
            print(f"  ok   {label}: tools={sorted(tools)} model={model or 'default'} effort={effort or 'session'}")

    missing_roles = sorted(set(ROLE_RULES) - seen)
    if missing_roles:
        failures.append(f"roles declared in ROLE_RULES but not present: {missing_roles}")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"All {len(files)} agent definitions valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
