# Structured Handoff Contract

Every specialist agent ends its turn with **exactly one JSON object** that matches `orchestration/handoff.schema.json`.

Nothing after the JSON. No trailing commentary. The lead treats the JSON as the interface; free-text outside it is ignored for gating and task completion.

## Why

Free-text reports force the lead to trust claims. Structured handoffs make claims checkable:

- required fields cannot be silently omitted
- proof tokens turn “tests passed” into something the lead can re-run
- ownership and off-limits violations become visible
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
  "payload": { }
}
```

### Proof token (required inside `summary`)

Include at least one of:

- commit SHA
- test count (`12/12 passed`)
- `file:line`
- exact command that succeeded

Bad: `"Fixed the billing bug."`  
Good: `"Checkout 500 fixed at checkout.tsx:88 (UUID was unquoted); 95/95 tests pass."`

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

## Lead rules

1. Do not mark a task complete until a valid handoff JSON arrives with `status` in `{done, needs_review}` (or `plan_ready` when a plan gate is required).
2. Prefer the JSON over any prose the agent also emitted.
3. Re-run or spot-check the proof token before integrating.
4. If `files_off_limits_touched` is non-empty, treat as scope violation until explained.
5. For high-risk work, require a `plan_ready` handoff from architect or implementer before allowing implementation to proceed.

## Emission rules for agents

- Final message = only the JSON object.
- Do not invent fields outside the schema and the role payload shapes above.
- Never claim tests passed unless the command actually ran in this session.
- If blocked, set `status: "blocked"`, explain in `summary` and `payload.risks` / findings, and stop.

## Evolution

Bump `schema_version` only on breaking changes. Additive optional fields inside `payload` do not require a version bump. Keep the envelope small so models reliably emit it.

## Validate a handoff

From the library root:

```bash
./scripts/validate-handoff.sh path/to/handoff.json
# or paste:
pbpaste | ./scripts/validate-handoff.sh -          # macOS
xclip -o | ./scripts/validate-handoff.sh -         # Linux
```

Exit 0 = OK, 1 = invalid, 2 = usage/IO error. Stdlib Python only; no extra packages.
