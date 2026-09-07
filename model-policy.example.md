# Model policy

Copy to `~/.claude/agent-library/model-policy.md` and edit; the SessionStart hook in the example
settings prints it into every session, where it wins over the roles' frontmatter defaults. The model
names below are one machine's choice - the mechanism is what ships.

## Routing

Spend the strong model where errors compound, the cheap one where they are
caught. `explore` (investigation and the `PLAN`) and `review` run on fable: a
wrong plan multiplies into every implementer, and a missed defect ships.
`implement` runs on opus - execution, already guarded by the spawn contract, the
hooks and the tests. Never sonnet for code. Pass `model:` on a spawn only to
move off those defaults, and say why.

## When Fable runs out, nothing falls back

Verified 2026-09-07 against the docs. A usage limit is **not** covered by
`fallbackModel` - that chain only catches overloaded/unavailable/server errors,
and explicitly excludes rate-limit, auth and billing errors. Hitting the Fable
weekly cap instead raises a **usage-credits consent prompt**
("continuing on Fable 5.1 uses usage credits"), and:

- it is shown only in the session's own terminal - **a Remote Control client
  cannot display it**, so from the phone the session simply stops responding;
- unanswered within `dialogExpiry` (5 minutes), Claude Code **sends nothing**
  and keeps the model; the next prompt raises it again;
- it applies to Remote Control, background and agent-team teammate sessions.

**Recovery, in order:** `/model opus` (instant, works from the phone's model
picker); or answer the consent at the desktop terminal to continue on credits;
or wait for the weekly reset. `autoContinueAtUsageLimit` does not help here - it
waits out session/weekly limits, not a model-family limit.

**While Fable is out:** spawn `explore` and `review` with `model: opus`
explicitly. Their frontmatter default would otherwise hit the same wall.

## Where the budget actually goes

The lead session is the dominant consumer, not the subagents - `explore` and
`review` spawns are small and infrequent by comparison. If the weekly Fable
budget is tight, the highest-leverage change is the **lead's own model**, not
the roles: run the lead on opus and switch to fable (`/model fable`) for
framing, hard decisions and reading evidence, then switch back.

## Always

Never spawn a second agent to re-verify what the first proved with a command
that ran; re-run the decisive check yourself instead.
