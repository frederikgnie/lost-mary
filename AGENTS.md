# Claude Code Agent Library - contributor notes

This repository is the canonical source for a personal, reusable Claude Code
operating layer. `README.md` explains what it is; this file is for changing it.

## Design intent (v2)

Four concerns, kept apart:

1. **Mechanisms** - `scripts/` hooks that run checks and compare claims with
   evidence. The only part the runtime enforces. Everything else is text.
2. **Roles** - `agents/`, three of them, each defined by what it *cannot* do
   (`explore` and `review` cannot mutate; `implement` must prove its work) and
   routed to a model/effort that fits.
3. **Procedures** - `skills/`, invoked when needed, kept to one screen.
4. **Project facts** - not here. Each repository's `CLAUDE.md` / `AGENT.md`
   owns its commands, invariants and danger zones; the roles are told those win.

Runtime state (`~/.claude/projects`, `tasks`, `teams`) belongs to Claude Code:
never versioned, never hand-edited.

## Changing things - checklist

- **Verify the runtime surface from the docs, not from memory**, before
  relying on a hook field, frontmatter key or file layout. Every unverified
  assumption in this project's history turned out wrong at least once. Record
  what you checked in `capabilities.md` (date, version, source).
- **Hooks fail open and say so on stderr.** Keep both properties. A hook that
  wedges every edit is worse than one that misses; a hook that goes quiet is
  worse than one that complains.
- **Read stdin as bytes, decode `utf-8-sig`.** PowerShell pipes prepend a BOM.
- **A hook imported by path is registered in `sys.modules` before `exec_module`.**
  Python 3.14's `dataclasses` looks the module up there; without it the import
  raises and `friction` silently counts nothing (2026-09-17).
- **Tests never touch the real `~/.claude`.** Installer scenarios run against a
  sandboxed `HOME` and assert the sandbox is not the real one before writing.
  A CI step run locally without that gate has destroyed a developer's config
  once already.
- **`tests/` must stay green on Linux and Windows** with real `ruff`/`ty`
  (`pip install ruff ty`). `test-hooks.py` pins both shapes the evidence hook
  reads - the witness lines under `~/.claude/agent-library/evidence/` (the
  source) and the transcript (the fallback); update the fixtures when either
  shape changes.
- **Update the installers together** (`install.sh`, `install.ps1`): the managed
  file lists, the retired-file lists and the `settings.json` drift check must
  match. Retire, never delete.
- **Keep `global-CLAUDE.md` short.** It loads into every session.
- **One canonical statement of the procedure** - in `skills/lost-mary/SKILL.md`.
  Do not restate it in the README or the roles.

## Measuring whether the roles and skills change anything

`tests/` proves the hook *scripts* behave. Nothing proved that the agents and
skills change an **outcome** rather than merely reading well. `claude plugin
eval` is what answers that: it runs each case with the plugin and again without
it, and reports the delta.

`.claude-plugin/plugin.json` makes this repository an eval target and
`hooks/hooks.json` carries the wiring from `settings.example*.json` rewritten
to `${CLAUDE_PLUGIN_ROOT}`, so the plugin arm fires the same hooks an installed
copy does. This is **additive**: `install.sh` / `install.ps1` remain the
supported way to use the library and are untouched.

**Stage first; do not run it from the repository root.** The harness refuses a
plugin in which any file has more than one name, it scans the whole plugin root,
and it does not honour `.gitignore` - so a `uv`-created `.venv` (475 hard links
out of its cache here) aborts every run with "a file in the plugin has more than
one name (a hard link)". `evals/stage.ps1` and `evals/stage.sh` copy the six
directories that *are* plugin content to a temp dir, fail loudly if a hard link
survives the copy, and print the path:

```powershell
claude plugin eval (./evals/stage.ps1) --allow-tools Write --no-publish
```

From a script or any shell without a terminal add `--trust-plugin`: a freshly
staged directory is untrusted, and with nobody to ask the run aborts before it
spends anything (measured 2026-09-16, 2.1.273).

**Read the delta, not the score.** A case that scores 1.0 with the plugin *and*
1.0 without it means the plugin is not what made it pass - the model would have
done that anyway - so the case needs sharpening or retiring. Graders marked
`arm: with-only` are a plugin-fired indicator, not part of the score.

Five cases live in `evals/<case>/prompt.md` with `evals/<case>/graders/*.md`;
each `runs:` value carries a comment saying why it is what it is. Before adding
one:

- **No case may need `Bash`.** Granting it requires an OS sandbox backend and
  native Windows has none, so a Bash-granting case only runs under WSL2.
  `--allow-tools Write` is needed for `evidence-in-report`, which writes files,
  and `--scaffold --allow-tools Edit Write` for `spawn-contract`, which stages a
  package and hands the edit to a subagent.
- **Tools follow graders.** A grader that implies a side effect only passes if
  the case's `allowed_tools` permits the tool that produces it *and* the
  operator granted it.
- **Hooks run through `hooks/run.sh`.** A versioned manifest cannot carry the
  absolute `python.exe` path `settings.example.windows.json` needs, and inside
  the eval sandbox `python3` on PATH was the Windows Python Install Manager
  shim with no runtimes (the sandbox redirects the profile), so every plugin
  hook had failed open in every reading before 2026-09-17. The shim, invoked as
  `/bin/bash` (a bare `bash` reaches the WSL stub on Windows), tries
  `$LOST_MARY_PYTHON`, `python3`, `python`, then the user's `pythoncore-3*`
  directories. When a with-arm transcript shows `hook_non_blocking_error` on
  every Stop, the hooks are not running and the reading measures agents only.
- Results land in `evals/results/` and are git-ignored, like every other piece
  of runtime state.

**First reading, 2026-09-15 (2.1.272), `spawn-contract`, 1 run per arm, $0.73.**
`with 0.00  without 0.00  delta 0.00`, both arms failing `contract-fields` with
"Agent called 0x". The harness works; the case did not. The prompt told the
model the checkout was on a build box it could not reach, which makes delegating
work on files that cannot exist the obviously futile move, so a sensible model
described the brief instead of spawning - and a grader keyed on
`tool_used: Agent` scores that zero.

**Second reading, 2026-09-16 (2.1.273), after a prompt rewrite, $0.76.**
Identical - `with 0.00  without 0.00  delta 0.00`, "Agent called 0x" in both
arms, both answering in 2 turns, 91-98 s. Rewriting the prose around the same
fiction changed nothing. The case had to stop pretending the files were
elsewhere.

**Fourth reading, 2026-09-17 (2.1.274), `keep-going`, 3 runs per arm, $0.55, 108 s.**
`with 0.67  without 0.22  delta +0.44` - and worthless as a hook measurement:
the with-arm transcripts show all three Stop hooks failing with exit 1 (the
Python Install Manager shim, see `capabilities.md`), so no bounce ever fired.
The regex grader failed all six runs: every reply, both arms, ended on "say
the word and I'll add a `procces_rows = process_rows` shim" - exactly the
hand-back the hook exists to bounce. The llm grader passed 3/3 with and 1/3
without, which with three runs is within noise. `hooks/run.sh` came out of
this reading; the next one is the first in which the hooks can fire.

**Fifth reading, 2026-09-17 (2.1.274), `keep-going` with `hooks/run.sh`, $0.59, 161 s.**
`with 1.00  without 0.44  delta +0.56`, pass rate 3/3 with and 0/3 without.
One with-arm run took two turns: its sandbox transcript holds a `Stop hook
feedback:` record whose bracketed command is `/bin/bash ".../hooks/run.sh"
".../scripts/no-punt.py"` - the first bounce ever fired inside the eval - and
its final message passes both graders. The other two with-arm runs closed
cleanly in one turn. All three without-arm runs ended on the shim offer
("say the word and I'll add the alias", "drop that line if you'd rather").
This is the first reading in this repository where the delta is the hooks'
doing and can be read as such.

**The redesign, 2026-09-16.** Three changes, all inside `evals/spawn-contract/`:

- `scaffold.sh` stages a real `billing/` package in the run's working directory:
  a token bucket refilling off `time.time()`, an `auth.py` that must not be
  touched, one test. `scaffold_script` cannot live in `prompt.md` - the loader
  accepts a fixed key list there (`schema_version`, `name`, `description`,
  `tags`, `plugins`, `runs`, `expected_outcome` plus the execution keys) and
  rejects anything else as an unknown frontmatter key - so the case also carries
  a `case.yaml` with `context.scaffold_script`, which the harness merges with the
  prose file (the report calls that source `mixed`). It runs only under
  `--scaffold`, as `bash <script>` with cwd set to the run's workspace, before
  the model starts: a non-zero exit aborts the run for $0.
- `prompt.md` drops the build-box fiction and asks for the change on the staged
  files, naming the off-limits file and the two check commands in plain words
  and the four fields nowhere. `allowed_tools` carries no Edit and no Write, so
  the only route from the session to a changed file is a subagent; the operator
  grants `--allow-tools Edit Write` so the subagent can land it.
- the grading splits in two - `delegated` (`tool_used: Agent`, weight 1) and
  `contract-fields` (the same call plus the four-field `input_match`, weight 3) -
  so "never delegated" (0.00) and "delegated without a contract" (0.25) stop
  reading alike.

**Third reading, 2026-09-16 (2.1.273), 1 run per arm, `--scaffold --allow-tools
Edit Write`, 880 s, $3.38.** Verbatim:

```text
  spawn-contract run 1/1 [with]: score 0.25  $1.67
    ✗ contract-fields (weight 3): Agent called 0x (expected 1..∞)
    ✓ delegated (weight 1): Agent called 4x (expected 1..∞)
  spawn-contract run 1/1 [without]: score 0.25  $1.71
    ✗ contract-fields (weight 3): Agent called 0x (expected 1..∞)
    ✓ delegated (weight 1): Agent called 4x (expected 1..∞)
✗ spawn-contract  with 0.25  without 0.25  Δ 0.00  (2 runs)  $3.38

CASE            WITH  W/OUT Δ      RUNS COST    NOTES
spawn-contract  0.25  0.25  0.00   2    $3.38   contract-fields: Agent called 0x (expected 1..∞)
```

with: 11 turns, 415 s, $1.674. without: 2 turns, 465 s, $1.709.

The case now measures something. Both arms delegate - four `Agent` calls each -
and neither spawn carries the contract; that is a different reading from "never
delegated", which is what the split was for. What it still does not produce is a
delta.

Why, as far as this reading can say - the run sandboxes holding `trace.jsonl`
are deleted on success and `--keep-temp` was not passed, so what follows is
inference, not evidence. None of the plugin's three mechanisms can reach a spawn
like this one. The eval child runs with `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, so
`global-CLAUDE.md` is not in context at all; `skills/lost-mary/SKILL.md` loads
when it is invoked, and a plain task prompt does not invoke it; and
`scripts/check-spawn.py` governs `subagent_type: implement` only, so a
`general-purpose` or `explore` spawn passes it untouched. The with-arm's 11 turns
against the without-arm's 2 says something pushed back, but not what. Next
iteration: pass `--keep-temp` and read the four `Agent` inputs before changing
anything else. The grader is not the suspect - the `input_match` lookaheads match
a `JSON.stringify`d Agent input that carries the four fields and reject a plain
brief.

Budget has moved with the scaffold: a case whose subagent actually edits cost
$1.67-1.71 per arm, not $0.73, so a four-case suite at the default three runs is
well past $9. Keep `--max-cost-usd` on, and read it for what it is - the ceiling
is checked before each run launches, so it bounds the next run, not the ones in
flight; $2 here let $3.38 through and said so.

**Which merges the guard holds.** `python scripts/replay-merges.py --detail`
replays every `gh pr merge` in the local transcripts through
`guard-shared-checkouts`' merge check, each against its transcript cut at the
call, beside what really happened (refused, blocked, ran, no-result). Like `usage.py` it is
not installed and reads nothing but transcripts. Re-run it after changing the
merge check: every `refused` row should be held, few `ran` rows should be.
`python scripts/replay-commands.py` does the same for the shared-checkout
check: every recorded command through this checkout's guard and the guard
at `--baseline` (default `main`), printing each verdict that differs
(`BLOCK->allow` is a check the change lost) and every call the scanner
cannot read cleanly. Real commands are what a model writes; review rounds
kept finding contrived shapes and missed a false hold this found.

**Where the tokens go.** `python scripts/usage.py --since 2026-09-15` sums the
per-message `usage` every transcript under `~/.claude/projects` carries, by
model and by role (lead vs subagent), deduplicated on `message.id` because one
API response is written as several records. It is not installed and reads
nothing but transcripts. It is what set the lead's model on 2026-09-16: over
2,174 messages a fable lead was 90-97% of all fable tokens and the explore and
review spawns 3-10%, so `model-policy.example.md` now puts the lead on opus.
Re-run it when an allowance drains faster than expected, before changing the
policy.

**Where the turns end.** `python scripts/friction.py --since <date>` reports
turn-ending hand-backs - a text-only message followed by a real user record,
judged with `no-punt.py`'s own detector - how many of them `no-punt` bounced,
and how many turns closed on `BLOCKED:`. The 2026-09-17 baseline: 474 of
1,646 turn ends since 09-01 handed the turn back, 5 bounced. Watch that number
fall; if it does not, the detector is missing the phrasing (add it to
`GO_AHEAD_PATTERNS` with a test) or the bounce is not reaching the model.

friction re-judges old transcripts with the current detector, so a new
hand-back class moves every earlier reading; compare readings taken with the
same detector only. **The 2026-09-25 baseline**, after the `assigned` class and
the backed-`BLOCKED:` gate (`--since 2026-09-01`, the default last 50 sessions,
09-02..09-25): 205 turn-ending hand-backs, 57 bounced; 103 turns ended on
`BLOCKED:`, 53 of them still carrying a hand-back and 39 unbacked (no refused
call in the turn, no user-held item named); 0 passed right after a `BLOCKED:`
bounce. Watch "unbacked" and "passed right after a BLOCKED: bounce": the first
should fall to zero, and a rising second means the gate is being reworded past
rather than satisfied.

The keep-going design rests on published mechanisms rather than on prose:
Claude Code's Stop-hook contract and its eight-block cap
(code.claude.com/docs/en/hooks, hooks-guide), the `ralph-wiggum` plugin's
completion promise (github.com/anthropics/claude-code, plugins/ralph-wiggum),
`taskmaster`'s `TASKMASTER_DONE` token (github.com/blader/taskmaster), `/goal`
(code.claude.com/docs/en/goal), and Anthropic's agentic-persistence prompting
guidance (platform.claude.com, prompting best practices). The common thread is
an explicit end token the hook demands; `DONE:` / `IN FLIGHT:` / `BLOCKED:` is
ours.

## GitHub account convention

This repository lives under the personal `frederikgnie` GitHub account, not the
work account. Commits and `gh` operations must use that identity.

**Commit identity** is set per clone, because a repository cannot carry it:

```sh
git config --local user.name  "frederikgnie"
git config --local user.email "60433760+frederikgnie@users.noreply.github.com"
```

**Push and `gh` operations** require the personal account to be active:

```sh
gh auth switch --hostname github.com --user frederikgnie   # before pushing
gh auth switch --hostname github.com --user fgn-odigo      # after, to avoid cross-account surprises
```

An agent working in this repository performs that switch itself rather than
asking, and switches back when finished. `/pr` encodes exactly this procedure.

**Why this is not automatic.** `gh auth git-credential` serves only the token
of the *active* account; it does not select an account from the username in
the remote URL. Rewriting the remote as `https://frederikgnie@github.com/...`
therefore does not work - the helper returns nothing and Git falls through to
an interactive password prompt. With the work account active, this private
repository reads as `Repository not found`. The only mechanisms giving genuinely
per-repository authentication are an SSH host alias with a dedicated key, or a
stored per-repo token. Neither is set up, so the account switch is the
supported path.
