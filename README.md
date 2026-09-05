# Claude Code Agent Library (lost-mary)

A small, enforced operating layer for Claude Code: three roles, three skills,
hooks that do real work, and one short page of always-on rules. Built to be
excellent on a typed Python monorepo and to work unchanged anywhere else.

> New here? [GETTING-STARTED.md](GETTING-STARTED.md) is the 5-minute version.

## The idea

Output quality in Claude Code comes from three things, in this order:

1. **Project facts the model can trust** - verified commands, test roots,
   architecture, danger zones, domain invariants. Those live in each repo's own
   `CLAUDE.md` / `AGENT.md`. This library deliberately does not try to know your
   code; it makes the model read those files and act on them.
2. **Mechanisms, not rules.** A rule ("run the type checker") is forgotten under
   load. A hook that runs the type checker on every edit is not. A rule ("never
   claim tests passed unless you ran them") is a wish; a hook that compares the
   claim with the transcript is a check.
3. **Cheap, sharp delegation.** A read-only explorer on a fast model, an
   implementer that must prove its work, a reviewer that physically cannot
   touch anything.

Everything else - long persona prompts, a rigid JSON handoff contract, a
regex denylist over Bash, team-formation playbooks - existed in v1 and was
removed because it optimised how agents *report* rather than whether the work
is *correct*. See [Migration from v1](#migration-from-v1).

## What you get

| Piece | What it does |
| --- | --- |
| [`scripts/pycheck.py`](scripts/pycheck.py) | **PostToolUse hook.** After every `Edit`/`Write` of a `.py` file: `ruff check`, `ruff format --check`, `ty check`, using the venv that owns the file - a project `.venv`, or a shared `<workspace>/<env>/.venv` beside several package repos - and the diagnostics go straight back to the model while the edit is still in its working memory. Also a CLI (`pycheck.py --changed`, `pycheck.py <paths>`) used by `/validate`. |
| [`scripts/check-evidence.py`](scripts/check-evidence.py) | **SubagentStop hook.** Reads the subagent's own transcript and bounces its final message when it (a) claims validation that never ran, (b) claims a pass when the last run failed, or (c) edited files, ran nothing, and did not say so. Honours `stop_hook_active`, so the re-emitted stop after a bounce is allowed through - the lead sees both messages and judges. |
| [`scripts/no-ask.py`](scripts/no-ask.py) | **PreToolUse hook.** While a session runs under `/lost-mary` (invoked since the last `/clear`), an `AskUserQuestion` call is blocked and the model is told to take the option it would have marked recommended, record an `Assumption:` line and continue; a genuinely irreversible choice is asked in plain text. Outside the procedure the option menus work as usual. |
| [`scripts/no-punt.py`](scripts/no-punt.py) | **Stop hook.** Bounces a final message that hands work back to you - "I'll leave that for you", "for you to fix", "you may want to look at" - once, with the three ways to close it: fix in place, delegate to an `implement` in a worktree, or commit a failing test. Quoted text is ignored; the re-emitted stop goes through (`stop_hook_active`). |
| [`scripts/friction.py`](scripts/friction.py) | **CLI, not a hook.** Reads your last N session transcripts and reports how often Claude asked (and how often the menu already had a "(Recommended)" option), how many tool calls were refused and by which command, how many messages handed work back to you, and which `Bash(...)` allow rules are exact one-offs that can never match again. Run it before and after changing the rules. |
| [`agents/explore.md`](agents/explore.md) | Read-only investigation on a cheap fast model (`sonnet`, medium effort). `Read/Grep/Glob/WebFetch/WebSearch`, no Bash - it cannot change anything. Returns a brief with `path:line` anchors. |
| [`agents/implement.md`](agents/implement.md) | Scoped change plus validation, on the session model. Reports `CHANGED / RAN / DONE MEANS / RISKS`; the evidence hook checks `RAN` against reality. |
| [`agents/review.md`](agents/review.md) | Independent review at high effort with `Read/Grep/Glob` only - you hand it the diff. Applies the project's review lens (for quant code: look-ahead leakage, DST 23/25-hour days, MW vs MWh, NaN propagation, timezone-naive timestamps). |
| [`/lost-mary <task>`](skills/lost-mary/SKILL.md) | The operating procedure on one screen: acceptance predicate first, smallest mode, full spawn block, gate on evidence. User-invoked only. |
| [`/validate [paths]`](skills/validate/SKILL.md) | Lint + typecheck + tests for what changed, commands taken from the project's `CLAUDE.md`, exact results reported. Claude may invoke it itself before declaring something done. |
| [`/pr [notes]`](skills/pr/SKILL.md) | Push and open a PR with the GitHub account that owns the repository (personal vs work), then switch `gh` back. User-invoked only. |
| [`global-CLAUDE.md`](global-CLAUDE.md) | ~2.4 KB of always-on rules, imported by `~/.claude/CLAUDE.md`. |
| [`capabilities.md`](capabilities.md) | Every runtime field, frontmatter key and file layout the library depends on, how each was verified, and how it degrades. |

## Layout

```text
.
├── README.md / GETTING-STARTED.md / AGENTS.md
├── global-CLAUDE.md              # always-on rules (imported into ~/.claude/CLAUDE.md)
├── capabilities.md               # runtime dependencies + degradation
├── agents/       explore.md  implement.md  review.md
├── skills/       lost-mary/  validate/  pr/            (each a SKILL.md)
├── scripts/      pycheck.py  check-evidence.py  no-ask.py  no-punt.py  friction.py
├── tests/        test-hooks.py  test-agents.py  test-skills.py  fixtures/pycheck/
├── settings.example.json         # hooks block, POSIX
├── settings.example.windows.json # hooks block, Windows (absolute interpreter path)
├── install.sh / install.ps1
└── .github/workflows/ci.yml
```

## Install

```bash
./install.sh            # macOS / Linux
```

```powershell
.\install.ps1           # Windows
```

Flags: `--dry-run` / `-DryRun` (print, write nothing), `--no-overwrite` /
`-NoOverwrite` (never replace or retire existing files), `--verify` /
`-Verify` (compare installed files with source, check the import line and the
hooks wiring; exit 0 clean, 1 drift).

The installer copies the three agents to `~/.claude/agents/`, the three skills
to `~/.claude/skills/<name>/`, the hook scripts and rules to
`~/.claude/agent-library/`, and ensures exactly one line
`@~/.claude/agent-library/global-CLAUDE.md` in `~/.claude/CLAUDE.md` (your
existing content is never rewritten; a timestamped backup is taken first).
Files from v1 that this version no longer ships are **retired** - renamed to
`<name>.retired.<timestamp>` - never deleted.

### Enable the hooks

The installer never touches `settings.json`. Merge the `hooks` block from
[`settings.example.json`](settings.example.json) (POSIX) or
[`settings.example.windows.json`](settings.example.windows.json) (Windows) into
`~/.claude/settings.json`. A running Claude Code normally picks it up live;
restart if it does not.

The examples also carry an `autoMode` template: two prose rules the auto-mode
classifier reads, alongside `"$defaults"`, which keeps the built-in rules -
without it your list *replaces* them, and `--verify` flags that as drift. Edit
the placeholders or drop the block; auto mode reads `autoMode` from
`~/.claude/settings.json` only, never from a project's `.claude/settings.json`.

Windows: hook commands run under Git Bash when it is installed (PowerShell
otherwise); `$HOME` expands in both. Use an **absolute path to `python.exe`** -
`python` on PATH is often the Microsoft Store stub, which would make the hooks
fail open on every event. `./install.sh --verify` reports whether the hooks are
wired and whether v1 entries linger.

Kill switches: `PYCHECK_DISABLE=1` in the environment silences `pycheck`;
`"disableAllHooks": true` in `settings.json` disables every hook.

## Using it

**Daily loop.** Ask for the change. Every Python edit comes back with ruff/ty
findings if there are any; fix them in place. Before calling it done, `/validate`
runs the project's checks and reports exact commands and counts. `/pr` pushes
and opens the PR with the right account.

**Measuring it.** `python ~/.claude/agent-library/scripts/friction.py` reads your
last 50 sessions and prints how often Claude asked, was refused, or handed work
back, plus the allow rules that can never match again. `--json` for diffing.

**Delegating.** `explore` to understand, `implement` to change, `review` to
critique. Every `implement` spawn carries `GOAL / SCOPE / OWNED / OFF-LIMITS /
DEPENDS ON / DONE MEANS / VALIDATION / REPORT`; `/lost-mary <task>` walks the
whole procedure. Several implementers only for disjoint scopes, each spawned
with `isolation: worktree`.

**Multi-repo workspaces** (a root that is not a git repo, several package repos,
one shared venv): `pycheck` finds the shared env by walking up from the edited
file to one level above the package root; `/validate` runs per package. Keep the
per-package facts in each package's `CLAUDE.md` / `AGENT.md`.

## Honest limits

- **Hooks fail open by design.** If Claude Code renames a payload field the
  enforcement disappears with only a stderr notice. `capabilities.md` lists the
  exact fields; re-run the smoke test in `GETTING-STARTED.md` §3 after upgrades.
- **`check-evidence` verifies that commands ran and how they exited**, not that
  the tests are any good, and it depends on the (documented-as-internal)
  transcript layout. It honours `stop_hook_active`, so a stubborn agent gets
  through on the second, re-emitted stop and the lead sees both messages.
- **`pycheck` is Python-only.** Other languages get no edit-time feedback; add
  a sibling script on the same `PostToolUse` matcher.
- **No Bash sandbox on Windows native**, so read-only roles have no Bash at
  all. When an investigation needs `git log`/`blame`, the lead runs it.
- **Model and effort routing is an opinion** (`explore` on `sonnet`, `review`
  at `high`). Change the frontmatter to taste; `tests/test-agents.py` only
  guards the tool properties.

## Migration from v1

Run the installer: it retires the seven v1 role files, `agent-library/orchestration/`
and the five v1 scripts. Then **replace the `hooks` block** in
`~/.claude/settings.json` - the v1 `PreToolUse` Bash guard and `SubagentStop`
handoff validator now point at retired scripts, and `--verify` flags that as
drift until you do. A running Claude Code normally picks up the replaced block
live; restart if it does not.

| v1 | v2 | Why |
| --- | --- | --- |
| 13-key JSON handoff envelope + schema + validator + `SubagentStop` format hook | 4-line prose report + `check-evidence` transcript hook | Nothing downstream parsed the JSON except the hook; it enforced the *shape* of a claim, not its truth. The transcript is the evidence. |
| `PreToolUse` regex denylist keeping review roles from writing via Bash | Review roles have no Bash | A denylist over shell text is bypassable (v1 said so itself); no sandbox exists on Windows; the lead can paste `git diff`. |
| 7 persona roles, no model routing | 3 roles with `model`/`effort` in frontmatter | Persona text moves modern models little; routing moves cost a lot. |
| 6 orchestration docs + playbooks + principles restating one procedure | `/lost-mary` (one screen) + `global-CLAUDE.md` (~2.2 KB) | Rules compete for attention; one canonical copy, on demand. |
| Manual `git worktree add` convention | Agent tool `isolation: worktree` | The runtime does it. |
| Nothing runs on edit | `pycheck` PostToolUse hook | The single highest-leverage mechanism for a typed codebase. |

## Tests and CI

`python tests/test-hooks.py` (every hook and the friction CLI, real `ruff`/`ty`, synthetic
transcripts in the observed format, BOM cases), `python tests/test-agents.py`
(frontmatter, tool properties, report formats), `python tests/test-skills.py`
(frontmatter, no `@` imports). [`ci.yml`](.github/workflows/ci.yml) runs them on
Linux and Windows × Python 3.11/3.13, plus PowerShell-pipe BOM checks and
hermetic installer scenarios (sandboxed `HOME`, gated before any write) for
`install.sh` and `install.ps1`, including retirement of v1 files and detection
of stale v1 hooks in `settings.json`.

## Contributing

See [AGENTS.md](AGENTS.md) for the design intent, the change checklist, and the
GitHub account convention for this repository.
