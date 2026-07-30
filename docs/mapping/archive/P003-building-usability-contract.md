# P003 — Building usability contract

- Origin: Planning
- Status: Done
- Depends on: P001

## Goal

Compile the compressed user brief into a compact, immutable
`BuildingProgram@1` that states what "usable" means for the current voxel
building without prescribing how the Architect must design it.

The first profile is intentionally narrow: use, target size, required spaces,
entrance, circulation, minimum clear height, and tolerances. Regulations remain
out of scope for the voxel-world phase.

## Write scope

- `archflow/state/`
- `archflow/validation/program.py`
- `tests/test_building_program.py`
- `docs/mapping/`

## Acceptance

- Hard requirements, soft preferences, and prohibitions remain distinguishable.
- Conflicting or impossible clauses fail with stable reason codes.
- The contract is compact enough for canonical state and contains no raw
  transcript, MCP traffic, or discarded design alternatives.
- A valid program does not prescribe a layout, style, expert sequence, or tool
  sequence.
- A neutral test-only fixture covers size, use, entrance, circulation, clear
  height, and required spaces without acting as generation authority.

## Tests

- Immutable contract and deterministic serialization.
- Conflict and invalid-tolerance rejection.
- Neutral fixture round trip.
- Existing canonical-state tests remain green.

## Stop conditions

- Stop if resolving the brief requires a design choice rather than contract
  compilation.
- Stop after two failures of the same contract or serialization test.


## Completion

- Completed: 2026-07-24
- Evidence: 7 BuildingProgram contract tests passed
- Evidence: 26 total standard-library tests passed
- Evidence: compileall and P003 scope checks passed
