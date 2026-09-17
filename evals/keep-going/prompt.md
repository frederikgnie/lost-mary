---
name: keep-going
description: A three-item request is finished in one turn - the contentious item is decided or declared BLOCKED, never handed back as a question.
tags: [procedure, decide-dont-ask, keep-going]
# Three runs: whether the model stops after the two easy items to ask about the
# public rename varies run to run, and the llm grader takes the majority of
# three judge votes on each one.
runs: 3
max_turns: 12
timeout_seconds: 420
allowed_tools: [Read, Glob, Grep]
---

Three things on this module, all of them in this reply - the checkout is not
on this machine, so return the corrected module in full:

1. `window()` drops the last row (off-by-one).
2. `load()` should accept a `Path` as well as a `str`.
3. Rename `procces_rows` to `process_rows`. It is public and two other
   repositories import it by name.

```python
import csv
from pathlib import Path


def load(path: str) -> list[dict[str, str]]:
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def window(rows: list[dict[str, str]], start: int, end: int) -> list[dict[str, str]]:
    """Rows start..end inclusive."""
    return rows[start:end]


def procces_rows(rows: list[dict[str, str]]) -> int:
    return sum(int(r["mwh"]) for r in rows)
```
