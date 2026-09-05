#!/usr/bin/env python3
"""Validate skill definitions under skills/.

A malformed skill does not announce itself: it silently fails to register, or
registers while carrying none of the content its author thought it referenced.
Both failure modes are silent, so they are asserted here.

Stdlib only, matching the rest of this repo's tooling.

Usage: python tests/test-skills.py     (exit 0 = all valid)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

REQUIRED_KEYS = ("name", "description")
KNOWN_KEYS = {
    "name",
    "description",
    "argument-hint",
    "allowed-tools",
    "disallowed-tools",
    "model",
    "disable-model-invocation",
    "user-invocable",
    "when_to_use",
    "arguments",
    "effort",
    "context",
    "agent",
    "background",
    "hooks",
    "paths",
    "shell",
    "metadata",
    "license",
    "compatibility",
}

# `@path` includes are a CLAUDE.md feature. Inside a skill body they are literal
# text, so an "import" written this way ships as prose and the skill appears to
# work while carrying none of the referenced file. Catch it at author time.
AT_IMPORT = re.compile(r"^\s*@[~./\w-]+\.(?:md|json|ya?ml|txt)\s*$", re.MULTILINE)

failures: list[str] = []


def parse_frontmatter(text: str, label: str) -> dict[str, str] | None:
    if not text.startswith("---"):
        failures.append(f"{label}: file does not open with a '---' frontmatter block")
        return None
    end = text.find("\n---", 3)
    if end == -1:
        failures.append(f"{label}: frontmatter block is never closed")
        return None
    block = text[3:end].strip("\n")
    data: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            failures.append(f"{label}: frontmatter line is not 'key: value': {line!r}")
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip()
    return data


def main() -> int:
    if not SKILLS.is_dir():
        print(f"no skills/ directory at {SKILLS}", file=sys.stderr)
        return 1

    skill_dirs = sorted(d for d in SKILLS.iterdir() if d.is_dir())
    if not skill_dirs:
        print("no skill directories found", file=sys.stderr)
        return 1

    for directory in skill_dirs:
        label = f"skills/{directory.name}"
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            failures.append(f"{label}: missing SKILL.md")
            continue

        text = skill_file.read_text(encoding="utf-8")
        front = parse_frontmatter(text, label)
        if front is None:
            continue

        for key in REQUIRED_KEYS:
            if key not in front:
                failures.append(f"{label}: frontmatter missing required key {key!r}")

        unknown = sorted(set(front) - KNOWN_KEYS)
        if unknown:
            failures.append(f"{label}: unrecognized frontmatter keys {unknown}")

        # The directory name is what becomes /<name>; a mismatch is confusing.
        if front.get("name") and front["name"] != directory.name:
            failures.append(
                f"{label}: frontmatter name {front['name']!r} does not match directory name {directory.name!r}"
            )

        if "disable-model-invocation" in front and front["disable-model-invocation"] not in ("true", "false"):
            failures.append(
                f"{label}: disable-model-invocation must be true or false, got {front['disable-model-invocation']!r}"
            )

        body = text[text.find("\n---", 3) + 4 :]
        if len(body.strip()) < 50:
            failures.append(f"{label}: body is empty or trivially short")

        bad_imports = AT_IMPORT.findall(body)
        if bad_imports:
            failures.append(
                f"{label}: body contains @-import line(s) {bad_imports}. "
                "@ includes do not work in skills (CLAUDE.md only) - they ship "
                "as literal text. Instruct an explicit file read instead."
            )

        if "$ARGUMENTS" not in body and "argument-hint" in front:
            failures.append(f"{label}: declares argument-hint but body never uses $ARGUMENTS")

        if not [f for f in failures if f.startswith(label)]:
            print(f"  ok   {label} (/{directory.name})")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"All {len(skill_dirs)} skill(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
