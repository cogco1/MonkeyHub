# P020 — Building-scoped brief and evidence envelope

- Origin: Planning
- Status: Done
- Depends on: P016, P019, P028, P036, M009

## Goal

Compile one raw request and its available material into an immutable,
building-scoped brief containing explicit facts, hypotheses, ambiguities,
authorities, source references, and open questions without inventing a spatial
answer.

## Write scope

- `archflow/state/design_brief.py`
- `archflow/runtime/brief_compiler.py`
- `archflow/runtime/probe_loader.py`
- `archflow/project/`
- `archflow/state/__init__.py`
- `archflow/runtime/README.md`
- `probes/README.md`
- `tests/test_design_brief.py`
- `tests/test_probe_loader.py`
- `docs/mapping/`

## Acceptance

- User facts, retrieved evidence, hypotheses, preferences, and prohibitions
  remain typed and distinguishable.
- Fact epistemic status uses the M009 first-class state field; it is not hidden
  inside a value string or inferred from model confidence.
- Missing dimensions, room lists, regulations, or occupancy remain unknown
  rather than receiving fixture or Pack defaults.
- Every derived statement binds source evidence, compiler identity, and exact
  base state.
- Ambiguity produces alternatives or a confirmation obligation.
- The output is building-scoped and contains no raw transcript dump.
- A data-only probe envelope separates authorized inputs from generated run
  outputs and is loaded by one generic runner.
- The P035/P036 project repository defines `input/`, immutable objects,
  `canonical/`, append-only `events/`, and branch-local `runs/` ownership
  without creating architectural answers.
- No persistent output path falls back to repository-level `.runs/`.
- Probe packages cannot contain executable derivation logic or case-owned
  archive/checkpoint implementations.

## Tests

- Minimal use-only prompt compilation.
- Explicit size becomes an external proposed commitment.
- Missing size remains unknown.
- Pack, keyword-route, and fixture-default rejection.
- Executable-file and derived-output-as-input rejection.
- Project-path escape and repository-level fallback rejection.

## Stop conditions

- Stop if compilation starts choosing topology or geometry.
- Stop if a hypothesis silently acquires hard authority.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented DesignBrief@1 with typed evidence claims, explicit unknown/proposed/ambiguous slots, proposed-only commitments, per-statement compiler/exact-base provenance, and fail-closed project/slot linkage; added generic data-only probe loader plus repository-owned run-record persistence with executable, derived-input, path-escape, digest, and .runs fallback rejection; 148 tests passed with 1 external smoke skip, compileall passed, P020 scope passed, dynamic map rendered, and 5 devctl tests passed.
