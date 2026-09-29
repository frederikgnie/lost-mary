---
name: pycheck-wrong-arg-type
description: The prompt describes `notify.send(address, message)`; the real signature takes a list of recipients and a subject, which ty flags.
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

Write `alerts/page.py` with `page_oncall(message: str) -> None` that emails the
on-call address (`alerts.oncall.ONCALL_ADDRESS`) through
`alerts.notify.send(address, message)`. Nothing fancy.
