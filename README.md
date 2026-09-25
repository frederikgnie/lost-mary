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
| [`scripts/check-evidence.py`](scripts/check-evidence.py) | **SubagentStop + Stop hooks.** Reads the subagent's own transcript and bounces its final message when it (a) claims validation that never ran, (b) claims a pass when the last run failed, or (c) edited files, ran nothing, and did not say so. With `--lead` (Stop) the same rules judge the lead's own final message: this turn's edits and runs for (b) and (c), the whole session's runs behind any claim of passing. Honours `stop_hook_active`, so the re-emitted stop after a bounce is allowed through. |
| [`scripts/no-ask.py`](scripts/no-ask.py) | **PreToolUse hook.** While a session runs under `/lost-mary` (invoked since the last `/clear`), an `AskUserQuestion` call is blocked and the model is told to take the option it would have marked recommended, record an `Assumption:` line and continue; a genuinely irreversible choice is asked in plain text. Outside the procedure the option menus work as usual. |
| [`scripts/no-punt.py`](scripts/no-punt.py) | **Stop hook.** Bounces a final message that ends the turn on you: a parked defect ("I'll leave that for you"), a request for a go-ahead ("say the word", "want me to?", "standing by", "your call"), a step assigned to you anywhere in the message ("the merge is yours", "only you can run", "you'll need to type", "run this yourself"), a `Remaining:` list, a closing question - and, under `/lost-mary`, a turn that called tools without closing on `DONE:`, `IN FLIGHT:` or `BLOCKED:`. The bounce quotes the phrase, restates the rule and carries the turn's task and `Done means` line back. `BLOCKED: <what only you can supply>` ends the turn when it is backed: the turn holds a refused tool call (the model tried), or the line names something only you hold - a login, consent or elevated prompt, a credential, money, an irreversible action, or a value asked for as a what/where/who question. An unbacked `BLOCKED:` is bounced with "try it". Loop guard from the transcript: two bounces with no work between them, or six in one turn, and the stop goes through. |
| [`scripts/friction.py`](scripts/friction.py) | **CLI, not a hook.** Reads your last N session transcripts and reports how often Claude asked (and how often the menu already had a "(Recommended)" option), how many tool calls were refused and by which command, how many turns ended on a hand-back or a go-ahead request (and how many of those `no-punt` bounced), how many ended on a `BLOCKED:` line, and which `Bash(...)` allow rules are exact one-offs that can never match again. Run it before and after changing the rules. |
| [`scripts/ledger.py`](scripts/ledger.py) | **SubagentStop + Stop + SessionStart + PostToolUse hooks.** `record` writes one entry per subagent stop - and per lead turn that edited or ran something - to `~/.claude/agent-library/ledger/<project>/` - files it edited (confirmed by the tool result), validation commands and how they exited, what it was asked, its own claims, and any edited file its report does not name - read from its transcript, not from its report. `recall` prints the last entries when a session starts, resumes or is compacted, so the lead never again loses track of who changed what. Kept outside the work tree so nothing a repository ships can pose as the record. When the `Agent` call returns, `attach` (PostToolUse) hands that entry to the lead as context, beside the subagent's own report; `brief` (SubagentStart) hands a starting subagent the last few entries, so it knows what changed. Entries are capped per project (`LEDGER_KEEP`, default 300). |
| [`scripts/check-spawn.py`](scripts/check-spawn.py) | **PreToolUse hook on `Agent`.** An `implement` spawn whose brief lacks `OWNED`, `OFF-LIMITS`, `DONE MEANS` or `VALIDATION` is blocked before it starts, with the `MISSING:` line - the contract the subagent used to enforce after a full round trip. Other roles are never blocked. |
| [`scripts/permit.py`](scripts/permit.py) | **PermissionRequest hook.** Allow-only: `git push` to any branch that is not main/master (no force, delete, tags or `--no-verify`), commands made solely of the project's validation runs plus benign wrappers, and the installers' verify mode. Anything else gets no decision and follows the normal flow. Matters in Manual mode and for background subagents, which are auto-denied without such a hook. |
| [`scripts/witness.py`](scripts/witness.py) | **PostToolUse + PostToolUseFailure hooks.** Appends one JSON line per tool call - which agent, which command or file, the exit code, the output (head and tail) - to `~/.claude/agent-library/evidence/<session>/`, so evidence comes from the runtime handing over each call rather than from parsing a transcript the docs call internal, version-unstable and written asynchronously. It classifies nothing: what counts as a validation run is the reader's judgment, not the record's. Fails open on anything unreadable; session directories whose newest line is older than `EVIDENCE_KEEP_DAYS` (default 14) are pruned - and only directories holding nothing but this hook's own `witness-*.jsonl` files, so a mis-set `EVIDENCE_ROOT` cannot make it delete anything it did not write. |
| [`scripts/guard-shared-checkouts.py`](scripts/guard-shared-checkouts.py) | **PreToolUse hook, config-driven.** In a checkout several sessions use at once it blocks `git switch`, a branch-changing `checkout`, `stash` (but not `stash list`/`show`), `reset --hard|--merge|--keep`, `rebase`, `pull --rebase`, `clean -f` and `gh pr checkout`, and points at `git worktree add <dir>_wt_<name> -b <branch>`; in a frozen tree only a deploy script may change, it blocks every git command and every `Edit`/`Write`. Tracks `cd`/`Set-Location`/`git -C` through the command, relative paths included. Without `~/.claude/agent-library/shared-checkouts.json` (template: [`shared-checkouts.example.json`](shared-checkouts.example.json)) it does nothing. |
| [`agents/explore.md`](agents/explore.md) | Read-only investigation **and planning** on `fable` at medium effort - it returns the `PLAN` when work must be split, and a wrong plan multiplies into every implementer. `Read/Grep/Glob/WebFetch/WebSearch`, no Bash - it cannot change anything. Returns a brief with `path:line` anchors. |
| [`agents/implement.md`](agents/implement.md) | Scoped change plus validation on `opus` (execution), with project-local persistent memory (`memory: local` - commands that work, code paths, traps; not versioned). Reports `CHANGED / RAN / DONE MEANS / RISKS`; the evidence hook checks `RAN` against reality. |
| [`agents/review.md`](agents/review.md) | Independent review on `fable` (judgment is the bottleneck here) with `Read/Grep/Glob` only - you hand it the diff. Applies the project's review lens (for quant code: look-ahead leakage, DST 23/25-hour days, MW vs MWh, NaN propagation, timezone-naive timestamps). |
| [`/lost-mary <task>`](skills/lost-mary/SKILL.md) | The operating procedure on one screen: acceptance predicate first, smallest mode, full spawn block, gate on evidence. User-invoked only. |
| [`/validate [paths]`](skills/validate/SKILL.md) | Lint + typecheck + tests for what changed, commands taken from the project's `CLAUDE.md`, exact results reported. Claude may invoke it itself before declaring something done. |
| [`/pr [notes]`](skills/pr/SKILL.md) | Push and open a PR with the GitHub account that owns the repository (personal vs work), then switch `gh` back. User-invoked only. |
| [`/ledger [n]`](skills/ledger/SKILL.md) | Show the ledger's latest entries for the current project on demand - what agents actually edited and ran. Claude may invoke it itself when a report needs checking against the record. |
| [`global-CLAUDE.md`](global-CLAUDE.md) | ~3 KB of always-on rules, imported by `~/.claude/CLAUDE.md`. |
| [`capabilities.md`](capabilities.md) | Every runtime field, frontmatter key and file layout the library depends on, how each was verified, and how it degrades. |

## Layout

```text
.
├── README.md / GETTING-STARTED.md / AGENTS.md
├── global-CLAUDE.md              # always-on rules (imported into ~/.claude/CLAUDE.md)
├── capabilities.md               # runtime dependencies + degradation
├── agents/       explore.md  implement.md  review.md
├── skills/       lost-mary/  validate/  pr/  ledger/   (each a SKILL.md)
├── scripts/      pycheck.py  check-evidence.py  no-ask.py  no-punt.py  friction.py  ledger.py  check-spawn.py  permit.py  witness.py  guard-shared-checkouts.py
├── tests/        test-hooks.py  test-agents.py  test-skills.py  fixtures/pycheck/
├── evals/        spawn-contract/  no-menu/  no-hand-back/  evidence-in-report/  keep-going/  keep-going/
├── .claude-plugin/plugin.json    # makes the repo an eval target (install path unchanged)
├── hooks/        hooks.json  run.sh  # the same wiring for the plugin way; run.sh finds a Python that works
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

The examples also carry an `autoMode` template: three prose rules the auto-mode
classifier reads (non-main pushes, the project's own checks, and merging a PR
the session opened and reviewed - the built-in `[Merge Without Review]` rule
otherwise denies merging a PR no human has approved), alongside
`"$defaults"`, which keeps the built-in rules -
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

### Model policy

Roles carry model defaults - `explore` and `review` on fable (planning and
judgment, where an error multiplies), `implement` on opus (execution, which the
spawn contract, the hooks and the tests already catch) - and
the lead may pass `model:` per spawn. To steer a whole machine - for instance when
one model's weekly budget runs low - copy [`model-policy.example.md`](model-policy.example.md)
to `~/.claude/agent-library/model-policy.md` and edit it; the `SessionStart`
hook in the example settings prints it into every session, where it wins over
the defaults. Hooks cannot see your quota, so the dial is yours; the lead cannot
switch its own model either - `/model` does that.

## Using it

**Daily loop.** Ask for the change. Every Python edit comes back with ruff/ty
findings if there are any; fix them in place. Before calling it done, `/validate`
runs the project's checks and reports exact commands and counts. `/pr` pushes
and opens the PR with the right account.

**Measuring it.** `python ~/.claude/agent-library/scripts/friction.py` reads your
last 50 sessions and prints how often Claude asked, was refused, or handed work
back, plus the allow rules that can never match again. `--json` for diffing;
`--record` saves the reading, `--history` tabulates the saved ones over time.

It is also the **hook health check**: Claude Code records every hook firing in the
transcript, and a hook that fails open writes its reason to a stream nobody reads.
The report counts firings, deliberate blocks and fail-open notices, so a stale
install or a wrong payload assumption shows up as a number instead of silence.

**Remembering it.** `~/.claude/agent-library/ledger/<project>/` holds one file per
subagent stop and per lead turn that changed something - what was edited and
run, from the transcript - and each new,
resumed or compacted session in that project opens with the tail. `edited` and
`ran` are evidence; `claimed` is the subagent's own words. When a report and the
ledger disagree, the ledger is right.

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
- **Model and effort routing is an opinion**: the strong model where errors
  compound (`explore`'s plan, `review`'s judgment), the cheap one where the
  contract, hooks and tests catch them (`implement`). Change the frontmatter or
  the machine policy to taste; `tests/test-agents.py` only guards the tool
  properties.

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
| 6 orchestration docs + playbooks + principles restating one procedure | `/lost-mary` (one screen) + `global-CLAUDE.md` (~3 KB) | Rules compete for attention; one canonical copy, on demand. |
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

Those tests prove the hook scripts behave; they say nothing about whether the
roles and skills change an outcome. `claude plugin eval . --allow-tools Write
--no-publish` does: the four cases under [`evals/`](evals/) run with the plugin
and again without it, so what you read is the delta, not a score. `--allow-tools
Write` is there for the one case that writes files; no case needs `Bash`,
because granting it wants an OS sandbox backend that native Windows does not
have. [AGENTS.md](AGENTS.md) explains what the delta means and what the cases
may assume.

## Contributing

See [AGENTS.md](AGENTS.md) for the design intent, the change checklist, and the
GitHub account convention for this repository.
