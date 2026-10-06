---
name: lost-mary
description: Run a task under Lost Mary's autonomous, evidence-based workflow when the user asks for Lost Mary or its operating mode.
---

# Lost Mary in Codex

Read [the canonical procedure](references/procedure.md), then carry out the
user's task. It is copied from the Claude skill at installation, not maintained
separately. Apply the runtime translations below wherever that procedure or a
role refers to Claude-specific behavior. User instructions and Codex's actual
permissions take precedence over these files.

- `$ARGUMENTS` means the task in the user's message. Slash commands and `!`
  command interpolation in references are Claude syntax, not executed context.
  Run relevant commands with available tools and inspect their results.
- Preserve autonomous completion: make reasonable reversible decisions, work
  through authorized steps, and use the procedure's completion criteria. Do not
  introduce routine approval gates. Respect real permission denials and
  user-only blockers; do not route around them through another agent.
- Use Codex's available search, file, shell, and background execution tools.
  If dedicated file tools are absent, use shell search/read commands. Read the
  project's `AGENTS.md` as well as relevant `CLAUDE.md` / `AGENT.md` conventions.
- `explore`, `implement`, and `review` are responsibilities, not installed Codex
  agent names. When delegation is useful and available, read just that role's
  reference ([explore](references/explore.md), [implement](references/implement.md),
  [review](references/review.md)) and pass its scope, acceptance check, and relevant
  instructions to a native subagent. Pass these runtime translations too. Use
  native restrictions when available; a prose read-only instruction is not an
  enforced sandbox. Use the configured Codex model and effort; ignore Claude
  aliases, model policy, `memory: local`, and Agent/Team parameters. Keep work in
  the lead when delegation is unavailable. Never claim a self-review was independent.
- A background launch acknowledgement is not a completed result. Wait using the
  returned agent id, inspect its result, and resolve findings before counting the
  delegated step as done. Reuse that agent for related follow-up rather than
  repeating its investigation. If it cannot run, finish other authorized work
  and report which acceptance checks remain unmet; do not claim the review passed.
- `ListAgents` / `SendMessage` mean the current task's native subagent lifecycle
  tools, when available. They do not authorize messaging unrelated user chats.
  Use ordinary native subagents or serial work instead of assuming Agent Teams.
- For validation, read [validate](references/validate.md) only when needed.
  Replace `~/.claude/agent-library/scripts/pycheck.py` and `test-scope.py` with
  the absolute paths to this skill's [pycheck](scripts/pycheck.py) and
  [test selector](scripts/test-scope.py). Run them using a working Python 3.11+
  interpreter from the task repository. Project commands win. Preserve quick
  checks per coherent step and broad validation on the confident, integrated
  solution; do not run the full suite after every edit or handoff. Re-run affected
  checks when subsequent edits invalidate their evidence.
- For an authorized PR, read [pr](references/pr.md). Gather its context explicitly,
  use Codex attribution instead of Claude attribution, and restore the prior
  GitHub account even on failure. Use a body file for multiline CLI descriptions.
- This adapter installs no Claude hooks, global instructions, model overrides,
  ledger, cleanup automation, or review-stamp enforcement. Do not run `pr-state.py`
  or claim a stamp, automatic lint, hook bounce, ledger record, or physical role
  restriction exists. Retain native tool results as evidence, re-run the decisive
  check at the procedure's validation stage, and review the actual final diff
  before an authorized merge. Treat native permissions as the enforcement layer.
  Preserve relevant decisions and evidence using the runtime's supported handoff
  or compaction facilities, without writing Claude runtime state.

The procedure's autonomy and evidence discipline apply here as instructions;
Claude's hook-level enforcement does not. Report any verification limitation
honestly and keep completing work that remains possible.
