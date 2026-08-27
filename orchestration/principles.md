# Engineering Principles

Short index. Not a second agent system. The lead and specialists cite a principle when it **changed a decision**. A name-drop with no decision behind it is noise.

## 1. Laziness / smallest change

Prefer the smallest reversible change that satisfies the acceptance predicate. Delete before you abstract. Do not add layers, indirection, or “flexibility” without a concrete caller that needs it.

## 2. Subtract before you add

When refactoring or extending, remove dead paths, unused flags, and obsolete compatibility first. Build on the simpler base.

## 3. Prove it works

Never report success from intent or unit-only green when the change is user-visible. Prefer evidence against the real path (CLI output, HTTP response, UI state, side effect). Proof tokens in handoffs must be re-runnable.

## 4. Sequence verifiable units

Split work so each step has a check you can run before the next. Do not batch validation only at the end of a large change.

## 5. Model the domain

Name the data shape and state transitions before writing branching logic. Encode invariants in types or structure where the stack allows; avoid the same assumption repeated ad hoc across files.

## 6. Understand before you edit

Non-trivial or high-risk work gets a read-only pass (architect or researcher) and a `plan_ready` handoff before implementation. Editing code you have not traced is how subtle regressions ship.

## 7. Exhaust the design space (when it matters)

For high-ambiguity design only: produce two competing approaches (or an architect breakdown with explicit alternatives), pick a base, graft the useful parts. Do not average designs. Do not open an “arena” for routine work.

## 8. Outcome over ceremony

Principles and playbooks exist to raise the probability of a correct outcome. If a step does not change the next action, skip it. Conservative team sizing still applies: parallelism is for separable ownership or rare design bakeoffs, not default fan-out.
