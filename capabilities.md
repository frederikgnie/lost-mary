# Capability declaration (library v2)

What this library depends on in the Claude Code runtime, how each dependency
was verified, and how each piece degrades when the runtime changes. Claude
Code's own documentation is the authority; this file records what was checked
and when, so a future upgrade has a checklist instead of a surprise.

Last verified: **2026-09-06**, Claude Code **2.1.260**, against
`code.claude.com/docs` (hooks, sub-agents, skills, settings) and against real
transcripts on disk.

## Hooks

| Script | Event / matcher | Runtime fields relied on | Status |
| --- | --- | --- | --- |
| `scripts/pycheck.py --hook` | `PostToolUse`, matcher `Edit\|Write\|MultiEdit` | `tool_name`, `tool_input.file_path`. Exit 2 = stderr shown to the model (non-blocking; the edit already happened). | Documented |
| `scripts/check-evidence.py` | `SubagentStop`, matcher on agent type (`implement\|general-purpose`) | `agent_id`, `agent_type`, `agent_transcript_path` (preferred), `transcript_path` (the **parent** session's file, used to derive the subagent's transcript when `agent_transcript_path` is absent), `last_assistant_message`, `stop_hook_active`. Exit 2 = block the stop, stderr returned to the subagent. | Confirmed live (see log below): `SubagentStop` exit 2 blocks the stop and the derived transcript path resolves. |
| `scripts/no-ask.py` | `PreToolUse`, matcher `AskUserQuestion` | `tool_name`, `transcript_path`; a slash-command turn is a `type: user` record whose `message.content` string starts with `<command-name>/<name></command-name>` (observed, not documented). Exit 2 = block the call, stderr returned to the model. This is the scoped mechanism: menus stay available outside the procedure. (A global `permissions.deny: ["AskUserQuestion"]` would remove the tool everywhere and make this hook inert - deliberately not used here.) | Contract documented; that `PreToolUse` fires for `AskUserQuestion` is not documented but is **confirmed live** (2026-09-06, in both directions: missed while the detector only accepted a leading `<command-name>`, blocked after the fix). |
| `scripts/no-punt.py` | `Stop` (no matcher - the event has none) | `last_assistant_message`, `stop_hook_active`; falls back to the last `type: assistant` record of `agent_transcript_path` / `transcript_path`. Exit 2 is the only blocking mechanism `Stop` honours; stderr returned to the model. Claude Code overrides a Stop hook after eight consecutive blocks without progress; this hook blocks once. | Documented (hooks reference, Stop). Confirmed live 2026-09-06: a real turn ending in a hand-back was blocked once and the re-emitted stop went through. |
| `scripts/check-spawn.py` | `PreToolUse`, matcher `Agent` | `tool_name`, `tool_input.subagent_type`, `tool_input.prompt` (the Agent tool's parameters). Exit 2 blocks the spawn, stderr (`MISSING: ...`) returned to the model. Only `implement` is governed; the required-field table mirrors the spawn block in skills/lost-mary/SKILL.md. | Documented (PreToolUse). Confirmed live 2026-09-06: a field-less `implement` spawn was refused with `MISSING: OWNED, OFF-LIMITS, DONE MEANS, VALIDATION`. |
| `scripts/check-evidence.py --lead` | `Stop` (the lead; a Stop payload without `agent_id` is treated as the lead even without the flag) | `last_assistant_message`, `transcript_path` (the session), `prompt_id` (the turn = records from the first one carrying that `promptId` to the next prompt), `stop_hook_active`. Rules A and C on the turn's edits/runs, claims against the session's runs. Exit 2 = block, stderr to the model; one strike. | Documented fields (Stop). **Confirmed live** 2026-09-06: the real Stop payload carries `prompt_id`, `last_assistant_message`, `transcript_path` and no `agent_id` (keys observed in a firing), the dispatch routes it to the lead path with or without the flag, it blocks a false claim and allows an honest message. Its first two firings failed open (`payload lacks ['agent_id']`) because the installed copy predated `--lead` - a stale install, not a contract change. |
| `scripts/ledger.py brief` | `SubagentStart`, no matcher | `agent_id`, `agent_type`, `transcript_path` (project slug); prints `hookSpecificOutput.additionalContext` with the last three entries (<= 2500 chars). SubagentStart can inject context but not block (documented). | Documented. Confirmed live 2026-09-06: an `explore` spawn quoted the first entry's `## ` header back from its own starting context. |
| `scripts/permit.py` | `PermissionRequest`, matcher `Bash\|PowerShell` | `tool_name`, `tool_input.command`, `cwd`; output is the documented `decision` object (`behavior: allow`); exit 2 is ignored for this event and never used. Allows only: push to a non-default branch (refspec or current branch via `git symbolic-ref`), validation-only commands (check-evidence's classifier + benign wrappers, redirects to relative paths only), installer verify. An allow is still subject to deny/ask rules (documented). Background non-interactive subagents are auto-denied without a PermissionRequest hook (documented) - this is what lets them run pytest. | Documented (hooks reference, PermissionRequest decision control). **Not reachable in auto mode, and - measured, not assumed - not reachable headless either.** The reference says Claude Code still runs these hooks in sessions that cannot show a prompt; on 2.1.261 a `claude -p --permission-mode default` session denied a `git push` with "This command requires approval" and the hook was **never invoked** (proven by logging every invocation from the installed copy: no entry). So the only path confirmed to reach it is a real interactive permission prompt - Manual mode, including the Remote Control approval flow. It is allow-only and never denies, so an unreachable hook costs nothing. |
| `scripts/ledger.py record` | `SubagentStop`, no matcher (every role); `Stop` for the lead's own turn | Subagent: `agent_id`, `agent_type`, `agent_transcript_path` / `transcript_path`, `last_assistant_message`, `stop_hook_active`, `cwd` (to make edited paths relative). Lead (`Stop`, no `agent_id`): `transcript_path`, `prompt_id` (the turn = records from the first one carrying that `promptId` to the end; assistant records carry none - observed, not documented), `session_id`; written only when the turn edited or ran something; skipped when `stop_hook_active`. The ledger lives at `~/.claude/agent-library/ledger/<slug>/` (`LEDGER_ROOT` overrides), one file per stop, where `<slug>` is the directory name of `transcript_path` - the per-project key Claude Code itself uses - so a worktree subagent's entry lands with its lead's project and nothing in a work tree can pose as the record. Edits are confirmed by a non-error `tool_result`; runs come from `check-evidence.classify`/`run_failed` (imported by path, registered in `sys.modules`). One shell call has one verdict for every segment in it, so a chain whose output merely mentions `exit 2` (seen live: an echoed probe result) records its runs as FAILED until the same segment passes later in the turn - latest outcome wins. Observed, not documented: `agent-<id>.meta.json` with `agentType`/`description`; `gitBranch` on records. Always exit 0. **Confirmed live** 2026-09-05/06 on both paths (Stop and SubagentStop), including entries written by unrelated sessions in two other projects. It does NOT emit `additionalContext` on `SubagentStop`: the reference documents that Stop/SubagentStop `additionalContext` continues the stopping agent's conversation (loop-protected), i.e. it would give the subagent an extra turn - so the hand-back to the lead is `ledger.py attach` on `PostToolUse` matcher `Agent`, which fires in the lead's context when the Agent call returns and looks the entry up by the `agentId:` line in the result. | Fields documented; meta/branch shape observed on 2.1.260. Confirmed live - see the log. |
| `scripts/ledger.py attach` | `PostToolUse`, matcher `Agent` | `tool_name`, `tool_output` (the Agent result text, which carries `agentId: <id>` - observed, not documented), `transcript_path` (project slug). Prints `hookSpecificOutput.additionalContext` with the matching entry; nothing when there is no entry yet (background spawns return at launch). | `additionalContext` on PostToolUse documented (inserted where the hook fired, read on the next request). Confirmed live 2026-09-06 after fixing the result shape: `tool_response` for Agent is a structured object (`agentId`, `agentType`, `content` blocks, `status`, `usage`...), and the entry arrived in the lead's context beside the subagent's answer. |
| `scripts/ledger.py recall` | `SessionStart`, matcher `startup\|resume\|clear\|compact` | `transcript_path` (its directory name selects the project's ledger); stdout is added to the session's context (documented). Whole entries only, at most ~6 KB; prints nothing when there is no ledger. | Documented (hooks guide: SessionStart stdout -> context; `compact` matcher re-injects after compaction). **Confirmed live** 2026-09-06: fired twice on the `compact` matcher in two other sessions (exit 0, banner in stdout, recorded as an `attachment` with `hookEvent`). |

**Subagent transcript location.** The hook reads `agent_transcript_path`
directly when the payload carries it. Otherwise it derives (relied on by
`check-evidence.py`, observed, not documented)
`<parent-dir>/<parent-stem>/subagents/agent-<agent_id>.jsonl` from
`transcript_path`. Records are JSONL with `type: assistant|user` and
`message.content[]` items of type `tool_use` `{id, name, input}` and
`tool_result` `{tool_use_id, content, is_error}`; a failing Bash result has
`is_error: true` and text starting with `Exit code N`. The docs call this
format internal and unstable. If neither resolves, the hook falls back to
searching the project directory for `agent-<id>.jsonl`, then fails open with a
notice. `tests/test-hooks.py` pins the observed shape.

**Every hook fails open, loudly.** An unreadable payload, a missing transcript
or a missing tool prints a one-line notice on stderr and exits 0. That is the
right call for availability and the wrong one for enforcement: if Claude Code
renames a field, the hooks stop acting and only the notice tells you. After an
upgrade, re-run `tests/test-hooks.py` (catches our bugs, not theirs) **and** the
live smoke test in `GETTING-STARTED.md` §3 (catches theirs).

**Stdin encoding.** All hooks read `sys.stdin.buffer` and decode `utf-8-sig`.
PowerShell prepends a UTF-8 BOM when piping to a native executable; text-mode
stdin on Windows would turn it into mojibake and the hook would fail open.

**Shell on Windows.** Hook `command` strings run under Git Bash (`sh -c`) when
it is installed, otherwise PowerShell. `$HOME` expands in both, which is why
`settings.example.windows.json` uses `$HOME` rather than `%USERPROFILE%`. The
interpreter is the trap: `python` on PATH is often the Microsoft Store stub, so
the Windows example expects an absolute `python.exe` path.

**Kill switches.** `PYCHECK_DISABLE=1` in the environment silences `pycheck`;
`"disableAllHooks": true` in `settings.json` disables every hook.

**Not used, deliberately.** `permissions.deny` cannot be scoped to one agent
(documented), and Bash sandboxing is **not available on Windows native**
(WSL2 only) - hence the read-only roles simply have no Bash. `agent_type` is
not documented on `PreToolUse`/`PostToolUse` payloads, so nothing here keys on
it there.

## Agents (`agents/*.md` -> `~/.claude/agents/`)

Frontmatter keys used and their documented values:

| Key | Used as | Documented values |
| --- | --- | --- |
| `name`, `description` | required | `name` matches `^[a-z0-9-]+$` |
| `tools` | strict allowlist | tool names; omitting `Bash` denies Bash |
| `model` | `fable` (explore, review), `opus` (implement) | `sonnet`, `opus`, `haiku`, `fable`, `inherit`, or a full model id |
| `effort` | `medium` (explore), `high` (review) | `low`, `medium`, `high`, `xhigh`, `max` |

Available but not used yet: `hooks` (per-agent hooks; `Stop` maps to
`SubagentStop` at runtime - would let `implement.md` carry the evidence hook
itself, at the cost of hard-coding an interpreter path in a shipped file),
`permissionMode`, `maxTurns`, `isolation: worktree` (the lead passes it per
spawn instead), `skills`, `background`. `memory` is used - `local` on
`implement`, see below.

`tests/test-agents.py` asserts the tool properties (`explore`/`review` carry
no mutating tool; `implement` has `Edit`, `Write`, `Bash`), the key set, and
the report formats the docs promise.

**Subagent frontmatter, verified 2026-09-06 against `code.claude.com/docs/en/sub-agents`:** `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `skills`, `hooks`, `memory` (`user` | `project` | `local`; enables Read/Write/Edit for the memory directory and injects memory instructions - so it is used on `implement` only, never on the read-only roles), `background`, `effort`, `isolation`, `color`. Memory depends on auto memory being enabled (`autoMemoryEnabled`). `tests/test-agents.py` `KNOWN_KEYS` lists them.

**Model routing (2026-09-06).** Role frontmatter carries the defaults (`explore` and `review` `model: fable` - planning and judgment, where an error multiplies; `implement` `model: opus` - execution, caught by the contract, the hooks and the tests); the Agent tool's `model` parameter overrides per spawn; `~/.claude/agent-library/model-policy.md`, when present, is `cat`-ed into the session at `SessionStart` (plain stdout -> context). No hook can read quota or switch the session's model (`PreModelSwitch` can only block a switch), so the policy file is the dial and `/model` stays the user's.

**What happens when a pinned model runs out (verified 2026-09-07).** A usage limit is NOT covered by `fallbackModel`: that chain catches overloaded/unavailable/server errors and explicitly excludes rate-limit, auth and billing errors, so a role pinned with `model: <x>` does not silently fall back when `<x>`'s weekly cap is reached. For Fable the cap raises a usage-credits consent prompt, shown only in the session's own terminal - **a Remote Control client cannot display it**, and unanswered within `dialogExpiry` (5 min) Claude Code sends nothing and keeps the model. It applies to Remote Control, background and agent-team teammate sessions. Recovery is `/model <other>`, answering at the terminal, or the weekly reset; `autoContinueAtUsageLimit` waits out session/weekly limits, not a model-family limit. Without a chain a subagent whose model is unavailable ends with `Agent terminated early due to an API error`. Consequence for this library: pinning a scarce model in role frontmatter is a real availability risk - the machine policy file names the manual fallback, and `fallbackModel` in the example settings covers only the overload case.

**Agent Teams, verified the same day:** `TaskCompleted` carries `task_id`, `task_subject`, `task_description`, `teammate_name`, `team_name` and `TeammateIdle` carries `teammate_name`, `team_name` - both with the *session's* `transcript_path`, no teammate transcript and no final message. A teammate's evidence therefore cannot be checked by a hook; `/lost-mary` offers teams as an option with the same spawn contract and keeps the gate with the lead re-running the decisive check.

## Skills (`skills/<name>/SKILL.md` -> `~/.claude/skills/<name>/` -> `/<name>`)

Keys used: `name`, `description`, `argument-hint`, `disable-model-invocation`
(`/lost-mary`, `/pr` are user-invoked only; `/validate` may be invoked by the
model). Substitutions used: `$ARGUMENTS`. Dynamic context used: `` !`command` ``
lines run in bash (default `shell`) before the skill body reaches the model.

`@path` imports do **not** work inside a skill body (CLAUDE.md only); the
skills instruct explicit reads instead. A skill invoked with `/name` stays in
context for the rest of the session - bodies are kept short for that reason.

## Settings (`settings.example*.json`)

The `hooks` block is prescribed; `autoMode` is a template (below). Agent Teams
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) are no longer part of the library's
model; the Agent tool's `isolation: worktree` covers concurrent writers.

**`autoMode` template and the extra `--verify` checks (2026-09-04).** The
examples carry `autoMode.allow` with `"$defaults"` plus two prose rules; auto
mode reads it from `~/.claude/settings.json` only. `--verify` reports DRIFT when
`autoMode.allow` exists without `"$defaults"` (the list would replace the
built-in classifier rules), a NOTE when `defaultMode` is `auto` with no
`autoMode.allow`, and a NOTE when five or more `Bash(...)` allow rules are exact
strings (they match one command forever - the shape that keeps prompts coming).
`scripts/friction.py` is the read-only CLI behind that last number: it counts
questions, refusals, hand-backs and dead allow rules over the last N transcripts
and depends on the same transcript shape as `check-evidence`.

**Permissions and auto mode** (verified 2026-09-04 against
`code.claude.com/docs/en/auto-mode-config` and `permissions`):

- `permissions.allow` rules are prefix matches (`Bash(git push *)`; the space
  before `*` matters). A compound command (`cd X && cmd`) is checked one
  subcommand at a time, so a chain passes when every part is allowed.
  `PowerShell(cmd *)` rules exist and behave the same way.
- `autoMode.allow`, `autoMode.soft_deny`, `autoMode.hard_deny` and
  `autoMode.environment` are lists of plain prose the classifier reads; the
  literal `"$defaults"` keeps the built-in list and adds to it. Auto mode reads
  `autoMode` from `~/.claude/settings.json` only, never from a project
  `.claude/settings.json`.
- A bare tool name in `permissions.deny` (e.g. `"AskUserQuestion"`) removes
  the tool from the model's context. That is the mechanism behind the
  "decide, do not ask" rule in `global-CLAUDE.md`; the text alone is advisory.
- In auto mode the classifier refuses every attempt by Claude to write
  `~/.claude/settings.json` (Bash, Python, the Write tool) and also refused
  scratchpad scripts whose content assigned `permissions.deny` / `autoMode`
  keys or documented how to. No allow rule lifts this. Prepare the change,
  put the JSON in the reply, let the user apply it. A project
  `.claude/settings.json` in the cwd is writable via the Write tool.

## Degradation guidance

| If Claude Code ... | Then |
| --- | --- |
| renames `tool_input.file_path` or `tool_name` | `pycheck` stops firing (fail open). Update `hook_main()`; the CLI and `/validate` keep working. |
| renames `agent_id` / `transcript_path` / `last_assistant_message` | `check-evidence` stops firing with a stderr notice. Update field names in `main()`. |
| moves subagent transcripts | `subagent_transcript()` falls back to a project-dir search; update the derived path if the fallback misses. |
| changes the transcript record shape | `read_transcript()` finds no evidence and the hook allows everything (with edits/no-runs no longer detected). Re-derive the shape from a real file and update `tests/test-hooks.py`. |
| stops honouring exit 2 on `SubagentStop` | switch `check-evidence` to the JSON form (`{"decision":"block","reason":...}` on stdout, exit 0). |
| does not fire `PreToolUse` for `AskUserQuestion`, or changes the slash-command transcript shape | `no-ask` silently allows (fail open). Fall back to `"deny": ["AskUserQuestion"]` under `permissions` (global or per project); re-derive the `<command-name>` shape from a real transcript. |
| drops `last_assistant_message` from `Stop` | `no-punt` reads the last assistant record from `transcript_path`; if that shape changes too it allows with a notice. |
| stops adding `SessionStart` stdout to context, or renames the `compact` source | `recall` prints to nowhere; the ledger file is still written and can be read by hand. Switch to `hookSpecificOutput.additionalContext` if that becomes the documented form. |
| drops `agent-<id>.meta.json` or `gitBranch` | entries lose role description / branch; `agent_type` from the payload still labels them. |
| changes agent/skill frontmatter keys | update `tests/test-agents.py` `KNOWN_KEYS`, `tests/test-skills.py` `KNOWN_KEYS`, and the files; reinstall. |
| ships Bash sandboxing on Windows | consider giving `explore` a read-only Bash for `git log`/`blame`; keep `review` without. |
| ships per-agent `permissions` in settings | prefer that over tool omission where finer control is wanted. |

**How to see whether a hook is actually working.** Claude Code records every firing in the session
transcript as an `attachment` object carrying `hookEvent`, `hookName`, `exitCode`, `stdout` and `stderr`
(observed 2026-09-06, not documented). Exit 2 is the hook blocking on purpose; **exit 0 with `stderr` is a
hook that failed open and explained itself on a stream nobody reads** - a stale install or a wrong payload
assumption hides there. `scripts/friction.py` counts both, so `friction.py --record` is also the hook
health check. Two real defects were found this way on 2026-09-06, hours after they started: an installed
`ledger.py` without `brief` (`usage:` on every `SubagentStart`) and an installed `check-evidence.py`
without `--lead` (`payload lacks ['agent_id']` on every `Stop`). Both failed open, so nothing broke and
nothing was reported - which is exactly why the counter exists.

**A run the classifier cannot see (2026-09-07).** `classify()` reads the command text, so a validation run
hidden behind a shell variable - `for t in tests/*.py; do python "$t"; done` - is not credited, and
`check-evidence` reports "ran no validation" for a turn that did validate. This is deliberate: crediting
`python "$VAR"` would credit any command, which is the one thing the hook exists to prevent. The fix is on
the caller's side - run the suites as explicit commands - and the bounce is one strike, so an honest
re-emission goes through. This is the only known false positive; it errs toward demanding evidence.

## Verified-live log

Fill this in after a real session confirms behaviour; the test suite cannot.

| Date | Claude Code | Check | Result |
| --- | --- | --- | --- |
| 2026-09-03 | 2.1.259 | `pycheck --hook` on a real monorepo file: shared-sibling venv discovered, ruff + ty diagnostics returned | OK (manual invocation of the hook script) |
| 2026-09-03 | 2.1.259 | `pycheck` feedback appears after an `Edit` inside a live session | OK - the hook fired on an `Edit` of `tests/fixtures/pycheck/bad.py` and the F401 + `invalid-assignment` findings came back as tool feedback. Observed along the way: the running session picked up the new `hooks` block from `settings.json` without a restart. |
| 2026-09-03 | 2.1.259 | `check-evidence` bounces an `implement` subagent that claims a run it never made | OK - an `implement` subagent was told to write one file, run nothing, and report "RAN: pytest -q -> 12 passed". Its stop was blocked and it re-emitted "BOUNCED: Evidence check failed (implement). Your final message claims validation, but the transcript contains no test/typecheck/lint command...". Confirms that SubagentStop exit 2 blocks on this version and that the derived transcript path (parent dir / parent stem / subagents / agent-id) resolves. |
| 2026-09-04 | 2.1.260 | Claude writes `~/.claude/settings.json` in auto mode (Bash heredoc, Python `write_text`, Write tool) | BLOCKED by the auto-mode classifier on every route. Scratchpad scripts that assigned `permissions.deny` / `autoMode` keys, or wrote prose about doing so into this file, were blocked as well (content-aware). Writing the project's own `.claude/settings.json` via the Write tool succeeded; the Edit tool on this file succeeded. |
| 2026-09-04 | 2.1.260 | `install.ps1` from a Bash tool call in auto mode (writes `~/.claude/skills`, `~/.claude/agent-library`) | OK - ran unprompted, installed the three changed files, wrote `.backup.<ts>` copies. |
| 2026-09-04 | 2.1.260 | `no-ask` blocks `AskUserQuestion` in a session that ran `/lost-mary` | **NOT YET VERIFIED LIVE.** `tests/test-hooks.py` covers the script; the docs do not say whether `PreToolUse` fires for this tool. To verify: fresh session, `/lost-mary <anything>`, then ask the lead to present an option menu - expect the block message in its reply, not a menu. Record the result here. |
| 2026-09-04 | 2.1.260 | `no-punt` bounces a lead message containing "I'll leave that for you" | **NOT YET VERIFIED LIVE.** `tests/test-hooks.py` covers the script (hand-backs blocked - pronoun, noun phrase or backticked object; closing, quoted and negated messages allowed; one-strike; streamed-message fallback). To verify: ask the lead to end a turn with that sentence; expect the bounce and a re-emitted message. |
| 2026-09-06 | 2.1.260 | `no-punt` on a real `Stop`, wired live | OK - a turn deliberately ended with "I'll leave the merge of PR #4 for you" was blocked once; the model received the bounce (header, quoted phrase, the three ways to close, the re-emit rule) and the re-emitted stop went through. The first attempt the day before was NOT blocked: the object pattern capped at four words and excluded '#', so 'the merge of PR #4' slipped past - widened the same day (`d4a48aa`), the sentence is a test. Also confirmed: the `Stop` hook runs in the lead session and its stderr reaches the model. |
| 2026-09-05 | 2.1.260 | `ledger record` on a real subagent transcript from this machine (review agent, 23 tool uses) with a synthetic payload | OK - entry carried role, description and branch from `.meta.json` / records, the prompt head, and the correct empty edited/ran sets for a read-only role. Independent review the same day found the file path fields unsanitised (a crafted `file_path` could forge an entry) and the in-repo location unsafe; both fixed before any live wiring. Live firing on SubagentStop and the SessionStart injection are NOT yet verified: wire both, spawn an `implement`, start a new session, expect the "Ledger for" block. |
| 2026-09-05/06 | 2.1.260 | `ledger record` on `Stop` (the lead's own turn), wired live at 16:20 on 09-05 | OK - the wiring turn itself produced `20260905T142204Z-lead-c98fd40.md` under `~/.claude/agent-library/ledger/c--repo-lost-mary/` with the right prompt, branch, edited files and runs; by 09-06 08:36 two other projects held 13 lead entries written by unrelated sessions (night-slot and heartbeat runs). The first real entries exposed two defects, fixed the same day: a chained shell call shares one result, so an early failure masked a later pass (latest outcome now wins), and scratchpad/home paths printed in full (now `scratchpad/<name>`, `~/...`). |
| 2026-09-06 | 2.1.260 | `ledger record` on `SubagentStop`, wired live | OK - a one-minute read-only `explore` spawn (sonnet, its default at the time) produced `...-ac2aaf7500a4.md` under `~/.claude/agent-library/ledger/c--repo-lost-mary/` within seconds of stopping: role and description from `.meta.json`, the prompt head, empty edited/ran for a read-only role, `claimed` = its two-line answer. |
| 2026-09-06 | 2.1.260 | `no-ask` in a real `/lost-mary` session | First attempt NOT blocked: a skill invocation is recorded as `<command-message>` before `<command-name>` and the detector accepted only a leading `<command-name>`. Fixed (tags anywhere in the leading tag block), reinstalled, second `AskUserQuestion` call BLOCKED with the hook's message. `PreToolUse` fires for `AskUserQuestion` - confirmed. |
| 2026-09-06 | 2.1.260 | `check-spawn` on a real `Agent` call | OK - an `implement` spawn with a one-line brief was blocked before starting; the model received `MISSING: OWNED, OFF-LIMITS, DONE MEANS, VALIDATION` and the pointer to the spawn block. |
| 2026-09-06 | 2.1.260 | `ledger attach` on a real `Agent` return | First attempt: entry written, nothing attached - `tool_response` is a structured object and attach read only strings. Fixed (`1ff73a8`); second attempt: the entry appeared in the lead's context as `PostToolUse:Agent hook additional context`. |
| 2026-09-07 | 2.1.261 | Can `permit` be reached without Manual mode? | NO. `permit` allows the exact command offline. A headless `claude -p --permission-mode default --model opus` session in this repo was told to run `git push -u origin permit-probe`: the call was denied ("This command requires approval"), the session transcript recorded only `SessionStart` hook firings, and an invocation log added to the installed `permit.py` stayed empty. The documented "runs these hooks when it cannot prompt" path does not cover `-p` sessions on this version. Incidental finding: the first attempt on the session's default model returned "You've hit your monthly spend limit. Switch to another model to continue." |
| 2026-09-07 | 2.1.260 | `check-evidence --lead` on a real `Stop`, wired live | OK - the first live block: a turn that edited `~/.claude/settings.json` and `model-policy.md` and ran its suites through a `for` loop was bounced with "You changed 2 file(s) this turn but ran no validation". Correct by its own rules (the loop hid the test paths from `classify()`), one strike, and the re-emission after running the suites explicitly went through. The lead is now checked like every subagent. |
| 2026-09-06 | 2.1.260 | `ledger brief` on `SubagentStart`, wired live | OK - a read-only `explore` spawn reported that its starting context began with "Recent ledger for this project" and quoted the newest entry's header. SubagentStart `additionalContext` reaches the subagent, as documented. |
| 2026-09-06 | 2.1.260 | `ledger recall` on `SessionStart` | NOT YET OBSERVED - no new session has started in a project that already had entries since the wiring. The transcript of a new session in `c--repo-lost-mary` or `C--repo-powercountant` should show the "Ledger for" block as injected context. Record here when seen. |
