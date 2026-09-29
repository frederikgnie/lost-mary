---
type: llm
focus: {source: file, path: alerts/page.py}
weight: 2
---

This is `alerts/page.py`. The real function is
`alerts.notify.send(to: list[str], subject: str, body: str) -> int`.

It PASSES when all hold:
- `page_oncall` calls `send` with three arguments that match that signature:
  a list containing the on-call address, a subject string, and the message as
  the body (keyword or positional, in the right order);
- the caller's `message` reaches `send` unchanged as `body` (it may also appear
  in the subject);
- the address comes from `ONCALL_ADDRESS`, not a hard-coded copy.

It FAILS when the file is missing, when `send` gets two arguments, a bare string
for `to`, or the message in the subject slot only, or when the mismatch is
hidden with `# type: ignore`, `cast`, or `Any`.
