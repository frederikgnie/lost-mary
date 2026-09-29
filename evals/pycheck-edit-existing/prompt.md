---
name: pycheck-edit-existing
description: The requested signature change for `_get` breaks the other caller in the same file; pycheck reports it on the Edit.
tags: [pycheck, types, edit]
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

In `client/fetch.py`, give `fetch()` a `retry: int = 0` parameter: that many
extra attempts when the request raises `urllib.error.URLError`. Put the retry
loop in `_get`, and change its signature to `_get(url, retry, timeout)` so the
retry count sits next to the URL.
