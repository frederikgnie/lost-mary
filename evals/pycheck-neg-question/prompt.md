---
name: pycheck-neg-question
description: Should not fire - a question about the code is answered from the source, with no file written.
tags: [pycheck, negative]
# Three runs: the floor for every case in this suite.
runs: 3
max_turns: 5
timeout_seconds: 120
allowed_tools: [Read, Glob, Grep]
---

What does `fx.rates.lookup_rate` return for a currency code it does not know,
say `"SEK"`?
