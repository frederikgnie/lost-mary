---
type: llm
focus: {source: file, path: report.py}
weight: 1
---

This is `report.py`. In the pasted original, `find_peak` returns `int | None`
and `main` indexes `rows[peak]` without checking - an empty argument list
crashes with `TypeError`, and ty flags the indexing.

It PASSES when all hold:
- the `None` result of `find_peak` is handled before `rows[peak]` is used (an
  early return, a message such as "no rows", or an error exit - any of these);
- a `--csv` flag exists (argparse or manual `argv` handling) and, when given,
  prints a `name,mwh` header followed by one line per row, and does not print
  the peak line;
- without `--csv`, the peak line keeps its original format
  (`peak: <name> <mwh:.1f> MWh`).

It FAILS when the file is missing, when `rows[peak]` can still be reached with
`peak` being `None`, when `--csv` is absent or broken, or when the type error is
hidden with `# type: ignore`, `cast`, or `Any`.
