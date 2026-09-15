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
allowed_tools: [Read, Glob, Grep, Agent, TodoWrite]
---

The checkout this is about is not on this machine, so do not go looking for the
files or try to run anything - hand the work to a subagent from what is below,
then tell me what you asked it for.

`billing/rate_limit.py` holds a token-bucket limiter whose refill clock is
`time.time()`. When the wall clock steps backwards - an NTP correction, a DST
write on a naive timestamp - the bucket stops refilling and every caller
starves until real time catches up. Move the refill to `time.monotonic()` and
pin the behaviour so it cannot come back.

Two things I care about beyond the fix. `billing/auth.py` is being rewritten on
another branch this week and a second set of edits in it would be painful. And
the package's checks are `python -m pytest tests/billing -q` and
`ruff check billing`; I want to know the fix holds, not that it reads well.
