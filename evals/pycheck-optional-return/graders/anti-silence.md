---
type: regex
target: {source: file, path: fx/convert.py}
match: not_contains
weight: 1
pattern: |-
  type:\s*ignore|\bnoqa\b|\bcast\(|:\s*Any\b|\[Any\b|->\s*Any\b|\bAny\]
---

`fx/convert.py` must not silence the checker instead of fixing the code: no
`# type: ignore`, no `# noqa`, no `cast(...)`, no `Any` annotation. With the
plugin, pycheck reports the type error right after the Write; hiding it passes
ty and ships the bug, so it scores 0 here. Case-sensitive on purpose, so the
English word "any" in a docstring does not trip it. Also fails when the file was
never written, which is correct - the task was to write it.
