# P005 — Deterministic usability gates

- Origin: Planning
- Status: Done
- Depends on: P003, P004

## Goal

Implement the smallest deterministic hard-gate set that distinguishes a
loadable voxel object from a minimally usable building.

## Write scope

- `archflow/validation/usability.py`
- `archflow/validation/README.md`
- `tests/test_usability_validation.py`
- `tests/fixtures/voxel/`
- `docs/mapping/`

## Acceptance

- Gates cover target envelope or area, occupiable clear height, exterior
  entrance, traversable connectivity, required-use zones, and supported
  geometry/no floating components.
- Every failure has a stable code, measured value, threshold, and spatial
  evidence reference where possible.
- Unknown evidence fails closed only for the hard requirement it prevents from
  being checked.
- Soft evaluation cannot waive a failed hard gate.
- Gates constrain submitted candidates, not the Architect's internal design or
  tool sequence.

## Tests

- Gold fixture passes all six gates.
- One Red fixture per gate fails only the intended primary condition.
- Validation does not mutate observation, workspace, world, or canonical state.
- Existing fake-adapter and promotion regressions remain green.

## Stop conditions

- Stop if a proposed gate requires subjective architectural judgment.
- Stop if fixing one duplicated geometry semantic would create two competing
  sources of truth; identify the shared source before continuing.
- Stop after two failures of the same gate implementation.


## Completion

- Completed: 2026-07-24
- Evidence: Gold fixture passed all six deterministic usability gates
- Evidence: Six Red cases each failed only its intended primary gate
- Evidence: 37 total tests plus compileall and P005 scope checks passed
