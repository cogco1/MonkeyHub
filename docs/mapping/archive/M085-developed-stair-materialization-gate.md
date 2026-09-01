# M085 — Developed stair materialization gate

- Origin: Modify
- Status: Done
- Depends on: M084

## Goal

Close the gap between recording downstream stair obligations and emitting
developed stair solids.  A current stair may remain a spatial reservation,
but it cannot materialize as developed geometry until the exact solver
obligations for host opening, site support, and load path have evidence-bound
dispositions and developed vertical-circulation assembly validation is due.

## Boundaries

- The framework owns the generic questions and materialization boundary; it
  never chooses a building-specific stair form, support system, dimension, or
  underpass answer.
- Historical stair policy v1 remains replayable evidence only.  Current
  authoring uses policy/pack v2 and cannot downgrade to v1.
- A materialization gate has no selection, validation, stage-acceptance,
  canonical-write, or promotion authority.
- Project data remains in its P036 project root; no Villa Rotonda answer is
  persisted in framework or test paths.

## Acceptance

- Current stair policy v2 adds developed site-support, support/load-path, and
  underpass-applicability rules while preserving v1 exact replay.
- Developed vertical circulation requires assembly maturity; coordinated
  maturity retains assembly and adds coordination-only collision and guard
  questions.
- `StairMaterializationGate@1` binds the exact solve-result digest and exactly
  one disposition for every solver obligation.
- `HOST_OPENING`, `SITE_SUPPORT`, or `LOAD_PATH` left OPEN blocks a developed
  gate; a reservation gate, reservation semantic, stale solve, missing gate,
  or legacy binding cannot emit developed geometry.
- Closed dispositions require evidence and authority refs, but those refs do
  not self-validate the resulting geometry or close a stage.
- P036 durable replay, canonical HEAD immutability, compileall, architecture
  checks, and focused regressions remain passing.

## Tests

- Semantic policy v1 replay and v2 current-authoring regressions.
- Stair materialization gate roundtrip, tamper, OPEN-obligation, reservation,
  stale-solve, and legacy-binding rejection tests.
- Developed baseline assembly-maturity and current policy replay tests.
- Durable controller resume regressions, compileall, archcheck, and diff check.

## Stop conditions

- Stop before hard-coding a project, named building, geometry answer, or
  construction system into the generic policy.
- Stop before treating an evidence-bound disposition as independent geometry
  validation, stage acceptance, or canonical-write authority.
- Stop if current authoring can omit the gate, downgrade to policy v1, or
  materialize developed stair solids with a blocking OPEN obligation.


## Completion

- Completed: 2026-08-31
- Evidence: 83 focused semantic-capability, stair, vertical-circulation, baseline, and inventory tests plus 22 durable-resume tests passed; compileall, git diff check, and ARCHITECTURE PASS (193 files) passed; current policy v2 and exact developed materialization gate reject OPEN host-opening, site-support, and load-path obligations while v1 remains replay-only.
