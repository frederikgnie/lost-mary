# Structured Handoff Contract

Every specialist agent ends its turn with **exactly one JSON object** that matches `orchestration/handoff.schema.json`.

Nothing after the JSON. No trailing commentary. The lead treats the JSON as the interface; free-text outside it is ignored for gating and task completion.

**This is enforced, not merely requested.** With `scripts/check-handoff-hook.py`
wired as a `SubagentStop` hook, a specialist that stops without a conforming
handoff is blocked and handed the validation errors, and must re-emit before it
can finish. The hook also rejects a handoff whose `from_role` does not match the
agent actually running. Without the hook installed the contract degrades to
documentation and the lead must validate manually — see the Enforcement section
in `README.md`.

## Why

Free-text reports force the lead to trust claims. Structured handoffs make claims checkable:

- required fields cannot be silently omitted
- proof tokens turn “tests passed” into something the lead can re-run
- ownership and off-limits violations become visible
- optional cost/stall signals give the lead something to gate on besides vibes
- reviewers and the lead can consume prior results without re-parsing prose

## Envelope (all roles)

```json
{
  "schema_version": "1.0",
  "handoff_id": "<uuid-or-short-trace-id>",
  "from_role": "<role>",
  "to": "lead",
  "task_id": "<stable-task-id>",
  "status": "done | blocked | needs_review | failed | plan_ready",
  "confidence": 0.0,
  "summary": "<1-2 sentences + proof token>",
  "timestamp": "<ISO-8601 UTC>",
  "tokens_used": 0,
  "attempts": 1,
  "stop_reason": "completed",
  "payload": { }
}
```

`tokens_used`, `attempts`, and `stop_reason` are **optional**. Emit them when the information is available; the lead may use them for cost awareness and stall detection.

### Proof token (required)

A proof token is an anchor the lead can **re-check**. The validator accepts any
one of these:

| Source | Example |
| --- | --- |
| test ratio in `summary` | `95/95 tests pass` |
| test count + outcome in `summary` | `12 tests passed`, `0 failures` |
| `file:line` in `summary` | `checkout.tsx:88` |
| commit SHA in `summary` | `a1b2c3d` |
| backticked command in `summary` | `` `npm test -- billing` `` |
| a `payload.validation[].command` entry | any non-empty command the lead can re-run |
| a `payload.findings[].location` matching `file:line` | `src/billing/lifecycle.ts:88` |

Bad: `"Fixed the billing bug."` — nothing to re-check.
Bad: `"Fixed the billing bug: it works now."` — a colon is not evidence.
Bad: `"Addressed 3 issues from the review."` — a bare digit is not an anchor.
Good: `"Checkout 500 fixed at checkout.tsx:88 (UUID was unquoted); 95/95 tests pass."`

> Earlier revisions of the validator accepted any summary containing a digit
> **or** a colon, which passed almost anything. That is fixed; see
> `tests/handoff-fixtures-runtime-invalid/` for the cases that now fail.

**Clean-review carve-out.** Demanding a proof token for a negative result is
incoherent — a `reviewer` or `security-reviewer` that found nothing has no
location to cite. When `from_role` is a review role and `payload.findings` is an
empty list, the handoff is accepted and the lead gets a warning noting that no
proof token was present.

### Optional cost / stall signals

| Field | Type | Purpose |
| ------- | ------ | --------- |
| `tokens_used` | number ≥ 0 | Rough estimate of tokens consumed this turn |
| `attempts` | integer ≥ 1 | How many implementation/diagnosis attempts before this handoff |
| `stop_reason` | enum string | Why the agent stopped: `completed`, `blocked_on_dependency`, `blocked_on_scope`, `stalled`, `budget`, `needs_human` |

Lead guidance: if `attempts` is high or `stop_reason` is `stalled` / `budget`, pause and re-plan instead of spawning another retry.

## Role payloads

### implementer / debugger

```json
{
  "result": "what was implemented or fixed",
  "owned_scope": ["src/billing/**", "tests/billing/**"],
  "files_changed": [
    {"path": "src/billing/lifecycle.ts", "action": "modified", "lines": "+42/-8"}
  ],
  "files_off_limits_touched": [],
  "validation": [
    {"command": "npm test -- billing", "passed": true, "output_snippet": "12 passed"}
  ],
  "tests_added_or_updated": ["tests/billing/lifecycle.test.ts"],
  "risks": ["migration not yet run in staging"],
  "integration_notes": ["frontend expects new webhook shape"],
  "rejected_approaches": ["full rewrite of state machine — too large for scope"]
}
```

Debugger additionally requires: `failure`, `reproduction`, `root_cause`, `fix`.

### tester

```json
{
  "coverage_assessment": "happy path and auth failure covered; concurrency not tested",
  "tests_added": ["tests/billing/lifecycle.test.ts"],
  "validation": [
    {"command": "npm test -- billing", "passed": true, "output_snippet": "12 passed"}
  ],
  "findings": [
    {
      "severity": "HIGH",
      "location": "src/billing/lifecycle.ts:88",
      "problem": "null plan id not rejected",
      "repro": "POST /billing/subscribe with planId=null"
    }
  ],
  "recommendation": "block merge until null plan id is rejected"
}
```

### reviewer / security-reviewer

```json
{
  "posture": "acceptable with fixes | needs rework | clean",
  "findings": [
    {
      "severity": "CRITICAL | HIGH | MEDIUM | LOW | INFO",
      "location": "file:line",
      "problem": "...",
      "why_it_matters": "...",
      "recommended_fix": "..."
    }
  ],
  "positive_controls": ["webhook signature verified before body parse"],
  "residual_risk": "rate limiting not reviewed"
}
```

### architect

```json
{
  "understanding": "current system behavior relevant to the task",
  "change_surface": ["src/billing/**", "migrations/00xx_*"],
  "dependencies": ["persist schema before API change"],
  "parallelization": ["backend and frontend after schema freeze"],
  "risks": ["migration requires dual-write window"],
  "recommendation": "smallest change that satisfies requirement",
  "task_breakdown": [
    {
      "task_id": "billing-backend",
      "scope": ["src/billing/**", "tests/billing/**"],
      "off_limits": ["src/ui/**", "package.json"],
      "depends_on": []
    }
  ]
}
```

### researcher

```json
{
  "question": "what was investigated",
  "findings": ["concrete evidence with sources"],
  "options": ["viable approaches"],
  "recommendation": "preferred option and why",
  "caveats": ["unknowns the lead should verify"]
}
```

## Acceptance predicate

The lead defines a checkable “done means…” before implementation. Put it in the spawn prompt. Agents should aim proof tokens and `validation` entries at that predicate. The lead does not mark the overall task complete until the predicate holds under re-check—not merely until a handoff says `done`.

## Lead rules

1. Do not mark a task complete until a valid handoff JSON arrives with `status` in `{done, needs_review}` (or `plan_ready` when a plan gate is required) **and** the acceptance predicate still holds.
2. Prefer the JSON over any prose the agent also emitted.
3. Re-run or spot-check the proof token before integrating.
4. If `files_off_limits_touched` is non-empty, treat as scope violation until explained.
5. For high-risk work, require a `plan_ready` handoff from architect or implementer before allowing implementation to proceed.
6. Treat high `attempts` or `stop_reason` in `{stalled, budget}` as a signal to re-plan rather than retry blindly.

## Emission rules for agents

- Final message = only the JSON object.
- Do not invent fields outside the schema and the role payload shapes above.
- Never claim tests passed unless the command actually ran in this session.
- If blocked, set `status: "blocked"`, explain in `summary` and `payload.risks` / findings, and stop.
- Emit `attempts` and `stop_reason` when useful; omit them when unknown.

Validation rule: unknown top-level fields are rejected. Keep envelope keys limited to the documented set so validator and schema remain aligned.

## Evolution

Bump `schema_version` only on breaking changes. Additive optional fields inside the envelope or `payload` do not require a version bump. Keep the envelope small so models reliably emit it.

### Version notes

- `1.0` (current): strict top-level envelope, role-scoped required payload fields, optional `tokens_used`/`attempts`/`stop_reason`, and enumerated `stop_reason` values for consistent lead-side gating.

## Validate a handoff

From the library root:

```bash
./scripts/validate-handoff.sh path/to/handoff.json
# or paste:
pbpaste | ./scripts/validate-handoff.sh -          # macOS
xclip -o | ./scripts/validate-handoff.sh -         # Linux
```

Exit 0 = OK, 1 = invalid, 2 = usage/IO error. Stdlib Python only; no extra packages.
