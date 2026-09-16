---
name: spawn-contract
description: A delegated code change must go out with a scope contract - OWNED, OFF-LIMITS, DONE MEANS, VALIDATION - that the task text never names.
tags: [delegation, roles]
# Three runs: whether a spawn carries the four fields is close to binary, so
# three separates a habit from a lucky draw without paying for more.
runs: 3
# A spawn plus the subagent's round trip plus the report needs more than the
# default 10 turns; the case is worthless if it is cut off before the Agent call.
max_turns: 16
timeout_seconds: 600
# No Edit and no Write on purpose. case.yaml stages a real `billing/` package in
# the run's working directory, so the files are there to be read - but the only
# route from this session to a changed one is a subagent. The operator grants
# Edit and Write to the run (`--allow-tools Edit Write`) so the subagent can
# actually land the change; the graders score the spawn, not the landing.
allowed_tools: [Read, Glob, Grep, Agent, TodoWrite]
---

`billing/rate_limit.py` in this directory holds a token-bucket limiter whose
refill clock is `time.time()`. When the wall clock steps backwards - an NTP
correction, a DST write on a naive timestamp - the bucket stops refilling and
every caller starves until real time catches up. Move the refill to
`time.monotonic()` and pin the behaviour with a test so it cannot come back.

I do not want you touching the files yourself. Hand the change to a subagent,
and when it comes back tell me what you asked it for and what it reported.

Two things I care about beyond the fix. `billing/auth.py` is being rewritten on
another branch this week and a second set of edits in it would be painful. And
the package's checks are `python -m pytest tests/billing -q` and
`ruff check billing`; I want to know the fix holds, not that it reads well.
