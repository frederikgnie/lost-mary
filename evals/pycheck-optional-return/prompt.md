---
name: pycheck-optional-return
description: A rate lookup typed through an alias as `float | None` is handled, not multiplied blind - the hazard ty flags and a reader skimming the prompt misses.
tags: [pycheck, types]
# Three runs: whether the model notices the hazard unaided varies run to run;
# three is the floor that separates a habit from a lucky draw.
runs: 3
max_turns: 15
timeout_seconds: 420
# Write/Edit are gated: the operator passes --allow-tools Write Edit. No Bash in
# either arm (AGENTS.md: no case may need it), so the model cannot run ty itself -
# the pycheck PostToolUse hook is the only type checker in the room.
allowed_tools: [Read, Glob, Grep, Write, Edit]
---

Add `fx/convert.py` with `to_eur(amount: float, code: str) -> float`, converting
an amount in the given currency to euros. `fx.rates.lookup_rate(code)` gives the
EUR rate as a float, so it is basically a one-liner. The invoicing job imports
it tomorrow.
