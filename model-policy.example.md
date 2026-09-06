# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit; the SessionStart hook in
the example settings prints this file into every session, where it wins over the
roles' defaults. Hooks cannot see your quota: this dial is yours.

- Spend the strong model where errors compound, the cheap one where they are caught:
  `explore` (investigation and the PLAN) and `review` on fable - a wrong plan
  multiplies into every implementer and a missed defect ships; `implement` on opus -
  execution, guarded by the spawn contract, the hooks and the tests.
  Never sonnet for code.
- Pass `model:` on a spawn only to move off those defaults, and say why.
- When a task is implementation-heavy and the lead runs on the scarce model, say so
  once and suggest `/model <cheaper>` for the rest of the session; the lead cannot
  switch its own model. Switch back for framing, review of evidence, and decisions
  that are hard to reverse.
- Never spawn a second agent to re-verify what the first proved with a command that
  ran; re-run the decisive check yourself instead.
