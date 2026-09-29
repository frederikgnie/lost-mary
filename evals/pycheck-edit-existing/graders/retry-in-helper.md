---
type: regex
target: {source: file, path: client/fetch.py}
match: contains
weight: 0.5
pattern: |-
  def _get\([^)]*\bretry\b
---

Secondary, spec-shaped check: `_get` takes a `retry` parameter as asked. Paired
with `callers-consistent`, which carries the outcome.
