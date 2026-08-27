# Claude Code Agent Library

A reusable, team-aware Claude Code setup for software engineering.

## What this gives you

- Global specialist agents that work across projects.
- A deliberate split between subagents and Agent Teams.
- Guidance for parallel work and file ownership.
- Review and security roles that are intentionally read-only.
- A bootstrap script for installing the agents into `~/.claude/agents/`.
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
│   └── validate-handoff.ps1
├── tests/
│   ├── handoff-fixtures/
│   └── handoff-fixtures-invalid/
├── .github/workflows/
│   └── handoff-contract.yml
├── settings.example.json
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

Safety behavior:

- Installer only touches `~/.claude/agents` and `~/.claude/agent-library`.
- Existing files are preserved by default via timestamped backups (`.backup.<timestamp>`).
- Unchanged files are left untouched.

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

This repository includes fixture handoffs in `tests/handoff-fixtures/`, invalid fixtures in `tests/handoff-fixtures-invalid/`, and a CI workflow at `.github/workflows/handoff-contract.yml`.

CI validates every fixture against both:

- JSON Schema (`orchestration/handoff.schema.json`)
- Runtime validator (`scripts/validate-handoff.py`)

CI also enforces that intentionally invalid fixtures fail both checks, and runs the shell wrapper (`scripts/validate-handoff.sh`) on Linux to catch wrapper regressions.

Python compatibility policy:

- Runtime validator targets Python 3.11+.
- CI runs the contract workflow on Python 3.11 and 3.12.

This keeps schema and runtime enforcement in sync and fails fast when either side drifts.

## Important

Do not version Claude Code's runtime team state. Team configuration and task state are generated locally by Claude Code.
