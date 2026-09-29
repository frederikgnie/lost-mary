---
type: regex
target: {source: file, path: fx/convert.py}
match: contains
weight: 1
pattern: |-
  \bis\s+(?:not\s+)?None\b|==\s*None|!=\s*None|\bif\s+not\s+\w*rate\b|\bif\s+\w*rate\s*:
---

`fx/convert.py` tests the looked-up rate for `None` before using it.
`lookup_rate` returns `Rate`, an alias for `float | None` in `fx/types.py`, while
the prompt calls it a float. `amount * lookup_rate(code)` is the ty error
pycheck reports.
