---
type: llm
focus: last_message
weight: 1
---

`fx/rates.py` defines `lookup_rate(code: str) -> Rate`, with `Rate = float | None`
in `fx/types.py`, and returns `_RATES.get(code.upper())`; `SEK` is not in
`_RATES`.

It PASSES when the answer says it returns `None` for `SEK`, and grounds that in
the code (the `.get` on the rate table, or the `Rate` alias being
`float | None`).

It FAILS when it says the function raises (`KeyError` or otherwise), returns
`0`, or returns a rate; or when it offers or writes code the user did not ask
for.
