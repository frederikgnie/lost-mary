---
type: llm
focus: {source: file, path: fx/convert.py}
weight: 2
---

This is `fx/convert.py`. `fx.rates.lookup_rate(code)` returns `float | None` -
`None` for a currency with no published rate.

It PASSES when all hold:
- `to_eur` never multiplies (or otherwise does arithmetic with) a value that may
  be `None`: the `None` case is detected before use;
- the `None` case raises an exception (any type, ideally naming the currency) or
  returns a fallback the code or docstring states explicitly; a silent `0.0`
  with no comment or docstring saying so FAILS;
- a known currency still converts as `amount * rate` (or an equivalent).

It FAILS when the file is empty or missing, when `None` would reach the
arithmetic, or when the error is hidden with `# type: ignore`, `cast`, or `Any`.
