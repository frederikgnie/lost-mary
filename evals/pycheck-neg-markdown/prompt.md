---
name: pycheck-neg-markdown
description: Should not fire - a Markdown-only task gets no Python written and no lint or type-check talk.
tags: [pycheck, negative]
# Three runs: the floor for every case in this suite.
runs: 3
max_turns: 8
timeout_seconds: 180
# Write only for the CHANGELOG.md itself; the operator grants it.
allowed_tools: [Read, Glob, Grep, Write]
---

Add a `CHANGELOG.md` at the top of this directory with a first entry for the
`fx` package: `lookup_rate` is new, and it returns `None` for a currency the
nightly feed did not publish. Keep it to a few lines.
