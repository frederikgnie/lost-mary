# Claude Code Agent Library

A reusable, team-aware Claude Code setup for software engineering.

## What this gives you

- Global specialist agents that work across projects.
- A deliberate split between subagents and Agent Teams.
- Guidance for parallel work and file ownership.
- Review and security roles that are intentionally read-only.
- A bootstrap script for installing the agents into `~/.claude/agents/`.

## Layout

```text
.
├── AGENTS.md
├── README.md
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
│   └── worktree-rules.md
├── settings.example.json
└── install.sh
```

## Install globally

Run:

```bash
./install.sh
```

The script copies the reusable agent files into:

```text
~/.claude/agents/
```

The global directory is the user-level Claude Code scope, so the agents are available in every repository on that machine.

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

## Important

Do not version Claude Code's runtime team state. Team configuration and task state are generated locally by Claude Code.
