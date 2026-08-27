# Getting Started — a 101

What this is: a set of specialist agent roles for Claude Code, plus a contract
that forces those agents to report **checkable** results instead of "looks good
to me". Two hooks make the contract real at runtime.

Read time: ~5 minutes. Setup time: ~3 minutes.

---

## 1. Install

```powershell
# Windows
.\install.ps1
```

```bash
# macOS / Linux
./install.sh
```

Want to see what it would do first? Add `-DryRun` / `--dry-run`.

This does four things:

1. Copies the 7 agent roles into `~/.claude/agents/`.
2. Installs the `/lost_mary` skill into `~/.claude/skills/lost_mary/`.
3. Copies the contract, validator, and hook scripts into `~/.claude/agent-library/`.
4. Adds one line to `~/.claude/CLAUDE.md`:
   `@~/.claude/agent-library/global-CLAUDE.md`

**Step 4 is the one that matters.** Claude Code only auto-loads
`~/.claude/CLAUDE.md`. Without that import line the agents exist but the
operating rules never load. Your existing `CLAUDE.md` content is never
rewritten — the line is appended after a timestamped backup.

## 2. Turn on enforcement

The installer deliberately does **not** touch `settings.json`. Merge the `hooks`
block yourself:

- Windows → copy from `settings.example.windows.json`
- macOS/Linux → copy from `settings.example.json`

into `~/.claude/settings.json`. Then **restart Claude Code** — settings are read
at startup.

Without this step the library is documentation. With it:

- read-only roles can't quietly mutate your repo through Bash
- a specialist that finishes without a valid handoff is blocked and told why

## 3. Verify it actually works

Don't trust the install; check it. Three commands:

```powershell
# a) files are in place and the import line landed
.\install.ps1 -Verify          # exit 0 = clean, 1 = drift

# b) the Bash guard blocks a read-only role from writing  (want: 2)
'{"agent_type":"reviewer","tool_input":{"command":"echo x > app.py"}}' | python "$HOME\.claude\agent-library\scripts\guard-readonly-bash.py"; $LASTEXITCODE

# c) ...but allows inspection  (want: 0)
'{"agent_type":"reviewer","tool_input":{"command":"git diff HEAD~1"}}' | python "$HOME\.claude\agent-library\scripts\guard-readonly-bash.py"; $LASTEXITCODE
```

```bash
# macOS / Linux equivalents
./install.sh --verify
echo '{"agent_type":"reviewer","tool_input":{"command":"echo x > app.py"}}' | python3 ~/.claude/agent-library/scripts/guard-readonly-bash.py; echo $?   # want 2
echo '{"agent_type":"reviewer","tool_input":{"command":"git diff HEAD~1"}}' | python3 ~/.claude/agent-library/scripts/guard-readonly-bash.py; echo $?  # want 0
```

If (b) returns 0 instead of 2, your hook path or interpreter is wrong — fix that
before relying on the guard. See §8.

## 4. Daily use: `/lost_mary`

This is the entry point. Instead of remembering the operating model, invoke it:

```text
/lost_mary add idempotency keys to the payout webhook
```

It makes the session state an acceptance predicate before writing code, pick the
smallest execution mode that can work, spawn with full scope boundaries, and gate
"done" on evidence you can re-run. Run it with no argument and it asks what you
want done.

Use it for anything non-trivial. For a one-line fix, just ask normally - the
point of the mode is to stop you skipping the framing step on work where
skipping it costs you.

Sections 5-7 explain what it does, so you can tell when it is steering you
wrong.

## 5. Pick the right mode (this is where tokens are won or lost)

| Your task | Do this | Don't |
| --- | --- | --- |
| Small, local change | Just ask in the main session | Don't spawn anything |
| Focused side question | One subagent (`researcher`) | Don't form a team |
| Bug, cause unclear | `debugger` | Don't guess-and-patch in the main session |
| Change you want a second opinion on | implement, then `reviewer` | Don't ask the implementer to review itself |
| Touches auth/payments/data boundaries | add `security-reviewer` | Don't rely on the general reviewer |
| Genuinely separable layers (backend + frontend + integration) | Agent Team, 2–4 teammates | Don't form a team just because the task is big |

Rule of thumb: **start with the smallest mode that could work, add a specialist
only when it removes a concrete risk.** Every extra agent costs tokens and
coordination.

## 6. The one thing to get right: the spawn prompt

A vague prompt produces a vague result and you pay for both. Every
implementation spawn should carry these eight things:

```text
GOAL:       Implement subscription lifecycle handling.
SCOPE:      Subscription create/cancel/renew only.
OWNED:      src/billing/**, tests/billing/**
OFF-LIMITS: src/ui/**, package.json, database/migrations/**
DEPENDS ON: schema freeze (task billing-schema) is already merged
DONE MEANS: billing unit tests pass AND POST /subscribe rejects null planId
VALIDATION: run the billing test suite and paste the count
HANDOFF:    final message is handoff JSON only, task_id = billing-backend
```

`DONE MEANS` is the highest-value line. It's an observable check *you* can re-run.
Without it, "done" means whatever the agent decided it means.

Copy-paste starting point:

```text
GOAL:
SCOPE:
OWNED:
OFF-LIMITS:
DEPENDS ON:
DONE MEANS:
VALIDATION:
HANDOFF: final message is handoff JSON only, task_id = <id>
```

## 7. Reading the handoff

Each specialist ends with one JSON object. You don't need to read all of it.
Check four fields:

1. **`status`** — `done` / `needs_review` / `plan_ready` are acceptable; `blocked` and `failed` are not.
2. **`summary`** — must contain a proof token: a test count, commit SHA, `file:line`, or a command. If it doesn't, the validator rejects it.
3. **`payload.files_off_limits_touched`** — non-empty means a scope violation. Investigate before merging.
4. **`attempts` / `stop_reason`** — high attempts or `stalled`/`budget` means re-plan, don't retry.

Then **re-run the proof token yourself.** That's the whole point of it.

Validate a handoff by hand:

```powershell
.\scripts\validate-handoff.ps1 .\handoff.json
```

```bash
./scripts/validate-handoff.sh handoff.json
```

## 8. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Agents work but ignore the delegation rules | import line missing from `~/.claude/CLAUDE.md` | `install --verify`; re-run installer |
| Hooks never fire | `settings.json` not merged, or Claude Code not restarted | merge the `hooks` block, restart |
| Hook fires but errors | wrong interpreter (`python3` vs `python`) or wrong path | run the §3 smoke test and fix the command string |
| Specialist keeps getting blocked at the end | it's emitting prose, not JSON | the block message says what's missing; it should re-emit |
| A reviewer complains it can't run something | the Bash guard blocked a write | that's working as designed — reviewers propose, they don't apply |
| `--no-overwrite` left the library inert | that flag skips the import line on purpose | add the printed line to `~/.claude/CLAUDE.md` manually |
| `/lost_mary` doesn't appear | not restarted, or the underscore is rejected | restart; if still absent rename `~/.claude/skills/lost_mary/` to `lost-mary` and use `/lost-mary` |

## 9. Honest limits

Worth knowing before you rely on this:

- **The Bash guard is a guardrail, not a sandbox.** It's a denylist over shell text. Obfuscation and scripts invoked by path can slip past. The real boundary is that read-only roles don't have `Write`/`Edit`.
- **Both hooks fail open** if Claude Code renames the payload fields they read. Enforcement would disappear silently, and the test suite would still pass. Re-run the §3 smoke test after upgrading Claude Code.
- **Agent Teams are experimental.** If they break, everything still works via subagents plus lead-only integration.
- **Nothing here knows your codebase yet.** The roles are generic: no test command, no build command, no architecture map, no danger zones. On a large production repo that means agents rediscover the same facts every session, which is exactly where tokens go. Encoding that in your repo's own `CLAUDE.md` (and a project `.claude/settings.json`) is the highest-leverage next step, and this library deliberately doesn't do it for you.

---

Deeper reading: `README.md` (full reference), `orchestration/handoff.md` (the
contract), `orchestration/playbooks.md` (routing), `capabilities.md` (what
happens when Claude Code changes).
