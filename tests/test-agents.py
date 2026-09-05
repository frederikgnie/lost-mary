#!/usr/bin/env python3
"""Structural checks for the agent definitions under agents/.

A malformed agent file fails silently (the role does not register) and a role
that gains a tool it should not have silently loses the property the library
promises. Read-only roles are checked against an ALLOW-list: anything not in it
- Bash in any form, Agent (which can spawn a writer), Skill, MCP tools - fails.
Stdlib only.

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
READ_ONLY_ALLOWED = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}

# The property each role promises.
READ_ONLY_ROLES = {"explore", "review"}
REQUIRED_TOOLS: dict[str, set[str]] = {
    "explore": {"Read", "Grep", "Glob"},
    "review": {"Read", "Grep", "Glob"},
    "implement": {"Read", "Edit", "Write", "Bash"},
}
# Text every role must carry so behaviour matches the docs.
BODY_MUST_MENTION: dict[str, tuple[str, ...]] = {
    "explore": ("CLAUDE.md", "FINDINGS", "path:line"),
    "implement": ("CLAUDE.md", "CHANGED:", "RAN:", "DONE MEANS:", "RISKS:", "MISSING:", "test"),
    "review": ("CLAUDE.md", "POSTURE:", "FINDINGS:", "NOT CHECKED", "git diff"),
}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, list[str]]:
    problems: list[str] = []
    if not text.startswith("---\n"):
        return {}, text, ["does not open with a '---' frontmatter block"]
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text, ["frontmatter block is never closed"]
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            problems.append(f"frontmatter line is not 'key: value': {line!r}")
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data, text[end + 4 :], problems


def split_tools(value: str) -> set[str]:
    """Normalise `Bash(git log:*)` -> `Bash`, YAML-list or comma forms alike."""
    value = value.strip().strip("[]")
    tools: set[str] = set()
    for raw in re.split(r",(?![^()]*\))", value):
        token = raw.strip().strip("'\"")
        if token:
            tools.add(re.sub(r"\(.*\)$", "", token).strip())
    return tools


def check_agent(text: str, stem: str) -> list[str]:
    front, body, problems = parse_frontmatter(text)
    if not front and problems:
        return problems
    for key in ("name", "description"):
        if not front.get(key):
            problems.append(f"frontmatter missing required key {key!r}")
    unknown = sorted(set(front) - KNOWN_KEYS)
    if unknown:
        problems.append(f"unrecognized frontmatter keys {unknown}")

    name = front.get("name", "")
    if name != stem:
        problems.append(f"name {name!r} does not match file name {stem!r}")
    if not re.fullmatch(r"[a-z0-9-]+", name):
        problems.append("name must match ^[a-z0-9-]+$")

    model = front.get("model")
    if model and model not in MODEL_ALIASES and not model.startswith("claude-"):
        problems.append(f"model {model!r} is not an alias or full model id")
    effort = front.get("effort")
    if effort and effort not in EFFORT_LEVELS:
        problems.append(f"effort {effort!r} not in {sorted(EFFORT_LEVELS)}")

    if "tools" not in front:
        problems.append("no 'tools' allowlist - the role would inherit every tool")
    tools = split_tools(front.get("tools", ""))
    required = REQUIRED_TOOLS.get(name)
    if required is None:
        problems.append("no REQUIRED_TOOLS entry - add one so the role's tool property is asserted")
    else:
        if name in READ_ONLY_ROLES:
            extra = sorted(tools - READ_ONLY_ALLOWED)
            if extra:
                problems.append(f"read-only role has tools outside the allow-list {sorted(READ_ONLY_ALLOWED)}: {extra}")
        missing = sorted(required - tools)
        if missing:
            problems.append(f"missing required tools {missing}")

    if len(body.strip()) < 300:
        problems.append("body is too short to be a usable role")
    for needle in BODY_MUST_MENTION.get(name, ()):
        if needle not in body:
            problems.append(f"body does not mention {needle!r}")
    if re.search(r"^\s*@[~./\w-]+\.(?:md|json|ya?ml|txt)\s*$", body, re.MULTILINE):
        problems.append("body contains an @-import line; keep roles self-contained")
    return problems


def self_test() -> list[str]:
    """The checker must reject what it is meant to reject."""
    problems: list[str] = []
    base = (
        "---\nname: review\ndescription: d\ntools: {tools}\n---\n"
        + "x " * 200
        + "CLAUDE.md POSTURE: FINDINGS: NOT CHECKED git diff"
    )
    for tools in (
        "Read, Grep, Glob, Bash",
        "Read, Grep, Glob, Bash(git log:*)",
        "Read, Grep, Glob, Agent",
        "Read, Grep, Glob, Skill",
        "Read, Grep, Glob, mcp__github__create_pull_request",
        '[Read, Grep, Glob, "Bash(git diff:*)"]',
    ):
        found = check_agent(base.format(tools=tools), "review")
        if not any("outside the allow-list" in p for p in found):
            problems.append(f"self-test: read-only checker accepted tools={tools!r}")
    found = check_agent(base.format(tools="Read, Grep, Glob"), "review")
    if any("outside the allow-list" in p for p in found):
        problems.append("self-test: read-only checker rejected a clean allow-list")
    return problems


def main() -> int:
    files = sorted(AGENTS.glob("*.md"))
    if not files:
        print(f"no agent files under {AGENTS}", file=sys.stderr)
        return 1
    failures: list[str] = []
    seen: set[str] = set()
    for path in files:
        label = f"agents/{path.name}"
        problems = check_agent(path.read_text(encoding="utf-8"), path.stem)
        seen.add(path.stem)
        if problems:
            failures.extend(f"{label}: {p}" for p in problems)
        else:
            front, _, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            tools = sorted(split_tools(front.get("tools", "")))
            model = front.get("model") or "default"
            effort = front.get("effort") or "session"
            print(f"  ok   {label}: tools={tools} model={model} effort={effort}")
    missing_roles = sorted(set(REQUIRED_TOOLS) - seen)
    if missing_roles:
        failures.append(f"roles declared in REQUIRED_TOOLS but not present: {missing_roles}")
    st = self_test()
    if st:
        failures.extend(st)
    else:
        print("  ok   self-test: read-only allow-list rejects Bash(...), Agent, Skill, mcp__ tools")

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
