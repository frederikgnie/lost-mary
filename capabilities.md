# Capability Declaration

This file documents the runtime assumptions of the Claude Code Agent Library so agents and leads can degrade gracefully when Claude Code evolves.

Claude Code's own documentation is always the authority for runtime behavior. This library layers opinionated workflow on top of that runtime.

## Declared surface (library v1)

### Agent frontmatter

Agents under `agents/` use Claude Code frontmatter fields:

| Field | Purpose |
|-------|---------|
| `name` | Role identifier |
| `description` | When to use the role |
| `tools` | Allowed tools (allow-list) |
| `disallowedTools` | Explicitly blocked tools (used for read-only roles) |

If Claude Code renames or removes these fields, update the agent files and this declaration. Do not assume frontmatter is stable forever.

### Expected tools

| Tool | Used by | Notes |
|------|---------|-------|
| `Read` | all roles | Required |
| `Grep` | all roles | Required |
| `Glob` | all roles | Required |
| `Bash` | all roles | Required for validation and inspection |
| `Write` | implementer, debugger, tester | Blocked on read-only roles |
| `Edit` | implementer, debugger, tester | Blocked on read-only roles |

Read-only roles (`architect`, `researcher`, `reviewer`, `security-reviewer`) set `disallowedTools: Write, Edit`.

If a tool name changes in Claude Code, search-and-replace across `agents/*.md` and reinstall.

### Model assumptions

Agent files intentionally do **not** hard-code model aliases (`opus`, `sonnet`, etc.). Model selection is left to the lead / Claude Code settings so the library does not rot when Anthropic renames or retires model aliases.

Prefer escalating model cost only for high-ambiguity architecture, severe debugging uncertainty, or security-critical review (see `orchestration/delegation-rules.md`).

### Structured handoff

| Asset | Path (installed) |
|-------|------------------|
| Contract docs | `~/.claude/agent-library/orchestration/handoff.md` |
| JSON Schema | `~/.claude/agent-library/orchestration/handoff.schema.json` |
| Validator | `~/.claude/agent-library/scripts/validate-handoff.py` |
| Playbooks | `~/.claude/agent-library/orchestration/playbooks.md` |
| Principles | `~/.claude/agent-library/orchestration/principles.md` |

Schema version is currently `1.0`. Additive optional envelope fields (`tokens_used`, `attempts`, `stop_reason`) do not require a version bump.

Playbooks and principles are lead-facing guidance only (not agent frontmatter). They do not depend on Claude Code APIs.

### Experimental runtime features

Agent Teams require:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

See `settings.example.json`. Treat team orchestration docs as best-effort until the feature is stable.

## Degradation guidance

When the runtime changes:

1. **Tool rename / removal** — update `tools` / `disallowedTools` in affected agent files; reinstall; keep role behavior the same.
2. **Frontmatter schema change** — update all `agents/*.md` and this file; prefer the minimal set of fields Claude Code still honors.
3. **Agent Teams disabled or changed** — fall back to subagents + lead-only integration; the handoff contract still applies.
4. **Worktree helpers appear in Claude Code** — prefer the built-in command; keep the naming convention in `orchestration/worktree-rules.md` as the coordination contract.
5. **Handoff emission fails** — lead should reject free-text-only completions and ask the specialist to re-emit valid JSON.

## Versioning this library

- Agent role behavior changes → note in commit message / changelog.
- Handoff envelope breaking change → bump `schema_version` and update validator + docs together.
- Capability surface change (tools, frontmatter) → update this file in the same change.
