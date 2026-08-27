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

`tools` is an allowlist and `disallowedTools` is a denylist applied first; both are honored in agent frontmatter. Either alone is sufficient to withhold `Write`/`Edit`, so the pair is belt-and-braces by design.

**`Bash` is not read-only.** Every role including the review roles retains `Bash` for inspection, and Bash can write files. `permissions.deny` in `settings.json` is session-wide and cannot be scoped to a single subagent, so the only per-role enforcement point is a `PreToolUse` hook. See the Hooks section below. Do not describe the review roles as read-only without that hook installed.

If a tool name changes in Claude Code, search-and-replace across `agents/*.md` and reinstall.

### Hooks (enforcement surface)

The library ships two optional hooks. They are the only components the runtime enforces, and they depend on specific hook payload fields:

| Hook | Event | Payload fields depended on |
|------|-------|----------------------------|
| `scripts/guard-readonly-bash.py` | `PreToolUse` (matcher `Bash`) | `agent_type`, `tool_input.command` |
| `scripts/check-handoff-hook.py` | `SubagentStop` (matcher `*`) | `agent_type`, `last_assistant_message`, `stop_hook_active` |

Both signal a block with **exit code 2** and put the reason on stderr.

**Silent-degradation risk — read this before trusting the guard.** Both hooks fail *open* when `agent_type` is missing, because a hook that fails closed on an unrecognized payload would wedge every subagent. That is the safe choice for availability and the unsafe one for enforcement: if Claude Code renames or drops `agent_type`, the Bash guard stops protecting the read-only roles and nothing announces it. `tests/test-hooks.py` asserts the block behavior against synthetic payloads, so it will keep passing even if the real payload shape changes. To detect that, confirm a block is still observed in a live session after any Claude Code upgrade — the test suite cannot tell you this.

`check-handoff-hook.py` also fails open (with a warning on stderr) when `validate-handoff.py` is not found beside it, so a partial install does not block all work.

**Stdin encoding.** Both hooks read `sys.stdin.buffer` and decode `utf-8-sig`, never text mode. Windows text-mode stdin decodes with the locale codepage, so the UTF-8 BOM that PowerShell prepends when piping to a native executable arrives as mojibake rather than `U+FEFF` — the payload then fails to parse and the hook fails open, silently disabling enforcement. If you refactor either hook, keep the byte-level read. `tests/test-hooks.py` covers this with BOM-prefixed payloads, and CI exercises a real PowerShell pipe on `windows-latest`.

### Skills / slash commands

`skills/<name>/SKILL.md` installs to `~/.claude/skills/<name>/SKILL.md` and Claude Code exposes it as `/<name>`. Custom commands (`.claude/commands/*.md`) and skills have been merged: both create the same slash command, and skills additionally allow a directory of supporting files plus invocation-control frontmatter.

Frontmatter fields this library relies on:

| Field | Purpose |
|-------|---------|
| `name` | Command identifier |
| `description` | Shown in the command list; also drives model auto-invocation when enabled |
| `argument-hint` | Placeholder text for arguments |
| `disable-model-invocation` | `true` keeps the skill user-invoked only |

`$ARGUMENTS` interpolates everything passed after the command name; `$1`/`$2` and `$ARGUMENTS[N]` address positionally.

**`@path` imports do NOT work inside a skill body.** `@`-style includes are a `CLAUDE.md` feature. In a skill, `@~/.claude/...` is literal text, so an "import" written that way silently ships as prose instead of loading the file — the skill appears to work while carrying none of the referenced content. `skills/lost_mary/SKILL.md` therefore inlines its decision procedure and instructs explicit reads of absolute paths where a step needs the full reference. If you extend it, do not add `@` imports.

Skill naming convention is kebab-case in Claude Code's documentation. `lost_mary` uses an underscore, which no documented rule forbids but which is off-convention; if it stops resolving, rename the directory to `lost-mary`.

A skill invoked with `/name` stays in context for the remainder of the session, so its token cost is paid once per session, not per turn. Keep the body compressed for that reason.

### Model assumptions

Agent files intentionally do **not** hard-code model aliases (`opus`, `sonnet`, etc.). Model selection is left to the lead / Claude Code settings so the library does not rot when Anthropic renames or retires model aliases.

Prefer escalating model cost only for high-ambiguity architecture, severe debugging uncertainty, or security-critical review (see `orchestration/delegation-rules.md`).

### Structured handoff

| Asset | Path (installed) |
|-------|------------------|
| Contract docs | `~/.claude/agent-library/orchestration/handoff.md` |
| JSON Schema | `~/.claude/agent-library/orchestration/handoff.schema.json` |
| Validator | `~/.claude/agent-library/scripts/validate-handoff.py` |
| Bash guard hook | `~/.claude/agent-library/scripts/guard-readonly-bash.py` |
| Handoff check hook | `~/.claude/agent-library/scripts/check-handoff-hook.py` |
| Playbooks | `~/.claude/agent-library/orchestration/playbooks.md` |
| Principles | `~/.claude/agent-library/orchestration/principles.md` |
| Operating rules | `~/.claude/agent-library/global-CLAUDE.md`, imported by `~/.claude/CLAUDE.md` |

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
6. **Hook payload fields change** (`agent_type`, `last_assistant_message`, `stop_hook_active`) — both hooks fail open, so enforcement disappears silently. Re-check a live block after upgrading Claude Code, then update the field names in `scripts/guard-readonly-bash.py` and `scripts/check-handoff-hook.py`.
7. **Per-subagent permissions become supported in `settings.json`** — prefer that over `guard-readonly-bash.py`, which is a shell-text denylist and strictly weaker. Retire the hook rather than running both.
8. **`CLAUDE.md` import syntax changes** — the installer's `ensure_import` / `Set-LibraryImport` and the `--verify` drift check both hard-code `@~/.claude/agent-library/global-CLAUDE.md`. Update the `IMPORT_LINE` / `$ImportLine` constant in `install.sh` and `install.ps1` together.

## Versioning this library

- Agent role behavior changes → note in commit message / changelog.
- Handoff envelope breaking change → bump `schema_version` and update validator + docs together.
- Capability surface change (tools, frontmatter) → update this file in the same change.
