# P034 — Resource and constructability compiler

- Origin: Planning
- Status: Ready after P032
- Depends on: P021, P032

## Goal

Compile building-scoped resource, material, build-mode, staging, and
constructability constraints before topology selection.

## Write scope

- `archflow/state/build_policy.py`
- `archflow/runtime/resource_compiler.py`
- `archflow/capabilities/constructability.py`
- `archflow/state/__init__.py`
- `archflow/runtime/README.md`
- `archflow/capabilities/README.md`
- `tests/test_build_policy.py`
- `docs/mapping/`

## Acceptance

- Creative, survival, staged, and externally supplied resource modes are
  explicit policies, not inferred defaults.
- Material availability, shortages, protected blocks, build budget, and staging
  assumptions enter `D_v,k` with authority and evidence.
- Constraints inform alternatives without selecting palette or geometry.
- Unknown resources remain unknown unless the active policy explicitly grants
  an unbounded disposable sandbox.

## Tests

- Creative and survival policies.
- Material-shortage obligation.
- Protected-block constraint.
- No hidden unbounded-resource fallback.

## Stop conditions

- Stop if resource compilation becomes a palette or building generator.
- Stop if creative mode silently replaces an unknown build policy.



## Completion

- Completed: 2026-07-26
- Evidence: Implemented BuildPolicy@1 with independent resource-supply and construction-staging axes, exact-base brief/program/site binding, first-class assumptions, availability and demand ranges, protected rules, budgets, staging, constructability constraints, authority, and evidence.
- Evidence: Only an explicit creative disposable-sandbox grant can be unbounded; survival, external, staged, and unknown conditions retain bounded evidence or obligations. Shortages and uncertain sufficiency never select a substitute palette or geometry.
- Evidence: Site protected cells compile into referenced do-not-replace rules; read-only constructability snapshots remain obligation-driven. 174 tests passed with one opt-in live retrieval smoke skipped, compileall and P034 scope passed, and instance-default scan was clean.
