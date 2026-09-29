---
type: llm
focus: {source: file, path: client/fetch.py}
weight: 1
---

This is `client/fetch.py`. `_get` is annotated `-> bytes`. Judge only one thing:
can control reach the end of `_get` without a `return` or a `raise`?

A common retry loop - `for attempt in range(retry + 1): try: return ...
except URLError: if attempt == retry: raise` - lets control fall out of the
`for` after the loop as far as a type checker can tell, so `_get` implicitly
returns `None` and ty reports it, even though a human can argue it is
unreachable.

It PASSES when every path through `_get` ends in `return <bytes>` or `raise`:
for example an unguarded final attempt after the loop, a `raise` statement after
the loop, or a `while True` loop whose only exits are `return` and `raise`.

It FAILS when any path can reach the end of the body without a `return` or
`raise` - typically a body that ends with a `for` loop and nothing after it
(which also returns `None` for a negative `retry`); an `if/else` whose branches
all return or raise is fine. It also FAILS when the implicit `None` is hidden with `# type: ignore`, `cast`, or `Any`, or when the file is missing.
