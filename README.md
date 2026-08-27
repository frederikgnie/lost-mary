# Claude Code Agent Library

A reusable, team-aware Claude Code setup for software engineering.

> **New here? Start with [GETTING-STARTED.md](GETTING-STARTED.md)** — a 5-minute
> 101 covering install, turning on enforcement, verifying it works, picking the
> right mode, and the spawn-prompt template. This README is the full reference.

## What this gives you

- Global specialist agents that work across projects.
- A deliberate split between subagents and Agent Teams.
- Guidance for parallel work and file ownership.
- Review and security roles held read-only by tool allowlist **and** a Bash guard hook (see [Enforcement](#enforcement-hooks) — the allowlist alone does not make a role read-only, because Bash can write).
- Hook-based enforcement: handoffs are validated by the runtime, not just by CI fixtures.
- A bootstrap script that installs the agents into `~/.claude/agents/` and wires the operating rules into `~/.claude/CLAUDE.md` so they actually load.
- Structured JSON handoffs (`orchestration/handoff.md`) so the lead can gate on checkable results instead of free-text claims.
- Concrete worktree naming and merge conventions.
- A short threat model with a pre-spawn trust check for untrusted input.
- Optional cost/stall signals on handoffs (`tokens_used`, `attempts`, `stop_reason`).
- A capability declaration so the library can degrade when Claude Code changes.
- Lean playbooks and principles (routing + quality gates without a large skill tree).
- Acceptance predicates (`Done means: …`) on spawn prompts and lead completion checks.

## Layout

```text
.
├── AGENTS.md
├── README.md
├── GETTING-STARTED.md              # start here
├── global-CLAUDE.md
├── capabilities.md
├── agents/
│   ├── architect.md
│   ├── researcher.md
│   ├── implementer.md
│   ├── debugger.md
│   ├── tester.md
│   ├── reviewer.md
│   └── security-reviewer.md
├── skills/
│   └── lost_mary/SKILL.md          # the /lost_mary slash command
├── orchestration/
│   ├── team-lead.md
│   ├── delegation-rules.md
│   ├── worktree-rules.md
│   ├── playbooks.md
│   ├── principles.md
│   ├── handoff.md
│   └── handoff.schema.json
├── scripts/
│   ├── validate-handoff.py
│   ├── validate-handoff.sh
│   ├── validate-handoff.ps1
│   ├── guard-readonly-bash.py      # PreToolUse hook
│   └── check-handoff-hook.py       # SubagentStop hook
├── tests/
│   ├── handoff-fixtures/           # valid: pass schema + runtime
│   ├── handoff-fixtures-invalid/   # fail schema + runtime
│   ├── handoff-fixtures-runtime-invalid/  # pass schema, fail runtime
│   ├── test-hooks.py
│   └── test-agent-templates.py
├── .github/workflows/
│   └── handoff-contract.yml
├── settings.example.json           # POSIX hook paths
├── settings.example.windows.json   # Windows hook paths
├── install.sh
└── install.ps1
```

## Install globally

Run:

```bash
./install.sh
```

On Windows PowerShell:

```powershell
./install.ps1
```

Preview all actions first (no file writes):

```bash
./install.sh --dry-run
```

PowerShell equivalent:

```powershell
./install.ps1 -DryRun
```

Install only missing files and never overwrite existing ones:

```bash
./install.sh --no-overwrite
```

PowerShell equivalent:

```powershell
./install.ps1 -NoOverwrite
```

Combine both to audit what would be skipped:

```bash
./install.sh --dry-run --no-overwrite
```

PowerShell equivalent:

```powershell
./install.ps1 -DryRun -NoOverwrite
```

Verify installed global files without writing anything (exit 0 on no drift, 1 on drift):

```bash
./install.sh --verify
```

PowerShell equivalent:

```powershell
./install.ps1 -Verify
```

The script copies the reusable agent files into:

```text
~/.claude/agents/
```

It also installs shared handoff and validation assets into:

```text
~/.claude/agent-library/
```

The global directory is the user-level Claude Code scope, so the agents are available in every repository on that machine.

### The CLAUDE.md import (required, or the rules are inert)

Claude Code auto-loads `~/.claude/CLAUDE.md` only. It does **not** load
`~/.claude/agent-library/global-CLAUDE.md`. So the installer idempotently
ensures this line exists in `~/.claude/CLAUDE.md`:

```text
@~/.claude/agent-library/global-CLAUDE.md
```

Without it the agents install correctly but the delegation policy, threat model,
and handoff mandate never enter context. `./install.sh --verify` fails on a
missing import line for exactly this reason.

Safety behavior:

- Installer only touches `~/.claude/agents`, `~/.claude/agent-library`, and the single import line in `~/.claude/CLAUDE.md`.
- Your own `~/.claude/CLAUDE.md` content is never rewritten — the import is appended after a timestamped backup.
- Existing files are preserved by default via timestamped backups (`.backup.<timestamp>`).
- Unchanged files are left untouched.
- Under `--no-overwrite` the import is **not** added; the installer prints the line for you to add manually.
- The installer does not touch `settings.json`, so hooks are never enabled behind your back.

## Enable Agent Teams

Agent Teams are currently experimental and disabled by default. The official Claude Code setting is:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

See `settings.example.json` for a full example and `orchestration/team-lead.md` for the operating policy.

## The `/lost_mary` slash command

`skills/lost_mary/SKILL.md` installs to `~/.claude/skills/lost_mary/SKILL.md`,
which Claude Code exposes as `/lost_mary`. Invoke it with a task:

```text
/lost_mary add idempotency keys to the payout webhook
```

It puts the session into this library's operating model: frame the task and
state an acceptance predicate before any code, pick the smallest execution mode
that can work, understand before editing, carry all eight fields in every spawn
prompt, gate completion on re-checkable evidence, and deslop before closing.

**Why a slash command rather than always-on context.** The orchestration docs
are ~400 lines. Loading them into every session via `CLAUDE.md` is the expensive
way to be ignored. The skill inlines the compressed decision procedure — the
part that actually changes behavior — and names the reference files to read only
when a step needs them. You pay for the operating model when you ask for it.

`disable-model-invocation: true` keeps it user-invoked. Claude will not pull it
in on its own, so it never becomes a hidden per-task tax.

Note: Claude Code's documented convention for skill names is kebab-case. The
underscore in `lost_mary` follows no documented prohibition but is off-convention;
if `/lost_mary` does not appear after a restart, rename the directory to
`lost-mary` and it becomes `/lost-mary`.

## Enforcement (hooks)

Everything else in this library is instruction text. Hooks are the only part the
runtime enforces. Two are provided; both are **opt-in** — merge the `hooks` block
from `settings.example.json` (POSIX) or `settings.example.windows.json` (Windows)
into your `~/.claude/settings.json`. Adjust the interpreter and path to match
your machine.

| Hook | Event | What it does |
| --- | --- | --- |
| `scripts/guard-readonly-bash.py` | `PreToolUse` on `Bash` | Blocks write-shaped Bash for the read-only roles (`architect`, `researcher`, `reviewer`, `security-reviewer`). Scoped by the `agent_type` field, so writable roles and the main session are unaffected. |
| `scripts/check-handoff-hook.py` | `SubagentStop` | Runs `validate-handoff.py` against the specialist's actual final message. An invalid or missing handoff blocks the stop and returns the errors to the agent, which must re-emit. Also rejects a handoff whose `from_role` does not match the running agent. |

Why the Bash guard is needed: `tools:` is an allowlist and the read-only roles
omit `Write`/`Edit`, but they retain `Bash` for inspection (`git diff`, `rg`) —
and Bash can write files. `permissions.deny` in `settings.json` is session-wide
and cannot be scoped to one subagent, so a hook is the only place this can be
enforced per role.

**Honest limits.** The Bash guard is a denylist over shell text. It is a
guardrail, not a sandbox: obfuscation, unusual interpreters, and scripts invoked
by path can slip past. It reliably catches the realistic cases (redirection,
`sed -i`, `rm`/`mv`, git writes, package installs). The real boundary remains
the withheld `Write`/`Edit` tools. Blocked-by-design conveniences include
`python -c` and `node -e`, since inline interpreter code is the widest bypass.

Both hooks are covered by `tests/test-hooks.py` (32 allow/block assertions) and
run in CI on Linux and Windows.

## How to use it

For a simple change, use the main session or a focused subagent.

For a substantial cross-layer feature, ask the main session to form an agent team and explicitly give teammates independent ownership boundaries. Example:

```text
Implement the billing feature.

Use an agent team if parallel work is justified.
Create:
- backend teammate: src/billing/**
- frontend teammate: src/components/billing/**
- integration teammate: tests/integration/billing/**

Have each teammate use the appropriate reusable agent role where useful.
Require a plan before risky implementation, and perform a final review before integration.
```

The team lead remains responsible for the overall task, integration decisions, and final validation.

## Role Selection Quick Table

Use this lightweight mapping to reduce token waste:

| Situation | Primary role | Required gate before done |
| --- | --- | --- |
| New feature with cross-module impact | `architect` then `implementer` | `plan_ready` handoff then passing validation evidence |
| Bug with unclear cause | `debugger` | root cause + regression test evidence |
| Medium-risk behavior change | `implementer` + `reviewer` | independent review handoff with no unresolved HIGH+ findings |
| Security-sensitive change | `implementer` + `security-reviewer` | security handoff with posture not `needs rework` |
| Test coverage strengthening | `tester` | test command evidence + findings/recommendation |
| Unknown code or external API research | `researcher` | sourced findings + clear recommendation/caveats |

## Validate handoffs

Specialist handoffs are expected to be a single raw JSON object (no markdown wrapper).

From this repository:

```bash
./scripts/validate-handoff.sh path/to/handoff.json
cat handoff.json | ./scripts/validate-handoff.sh -
```

On Windows PowerShell:

```powershell
./scripts/validate-handoff.ps1 .\path\to\handoff.json
Get-Content .\path\to\handoff.json -Raw | ./scripts/validate-handoff.ps1 -
```

## Contract Drift Checks

CI at `.github/workflows/handoff-contract.yml` enforces three tiers of fixture,
because the runtime validator is deliberately stricter than JSON Schema can be:

| Directory | JSON Schema | Runtime validator |
| --- | --- | --- |
| `tests/handoff-fixtures/` | must pass | must pass |
| `tests/handoff-fixtures-invalid/` | must fail | must fail |
| `tests/handoff-fixtures-runtime-invalid/` | must **pass** | must **fail** |

The third tier is the interesting one: proof-token heuristics (does the summary
contain a re-checkable anchor?) cannot be expressed in JSON Schema. If a fixture
there ever starts passing the runtime validator, the tiers have collapsed and the
proof-token check has gone slack.

CI additionally runs:

- `tests/test-agent-templates.py` — asserts the JSON template embedded in every `agents/*.md` carries exactly the payload keys the validator requires. These templates are what the model actually reads; fixtures can stay green while an agent file drifts.
- `tests/test-hooks.py` — allow/block assertions for both hooks, on Linux and Windows.
- The shell wrapper (`scripts/validate-handoff.sh`) on Linux and the PowerShell wrapper on Windows.
- Installer integration on Linux and Windows: pre-existing `CLAUDE.md` content is preserved, the import line lands, re-running is idempotent, and `--verify` fails when the import is missing.

Python compatibility policy:

- Runtime validator targets Python 3.11+.
- CI runs the contract workflow on Python 3.11 and 3.12.

This keeps schema and runtime enforcement in sync and fails fast when either side drifts.

## Important

Do not version Claude Code's runtime team state. Team configuration and task state are generated locally by Claude Code.
