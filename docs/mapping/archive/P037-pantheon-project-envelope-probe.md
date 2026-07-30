# P037 — Pantheon project-envelope probe

- Origin: Planning
- Status: Ready after P036
- Depends on: P036

## Goal

Create `probes/test_pantheon/` by calling only generic project modules. The
case supplies one raw Pantheon request; framework code creates the durable
project envelope, input record, run, and bootstrap receipt without deriving or
hard-coding any architectural answer.

## Write scope

- `archflow/project/bootstrap.py`
- `archflow/project/__init__.py`
- `archflow/project/repository.py`
- `probes/test_pantheon/`
- `tests/integration/test_pantheon_probe.py`
- `probes/README.md`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- The probe is generated through `archflow.project`, not hand-authored output.
- The case input contains a raw Pantheon request but no supplied dimensions,
  room list, column count, proportions, materials, topology, coordinates,
  geometry commands, expert order, or derived building answer.
- Framework bootstrap code contains no Pantheon or building-type branch.
- Project, HEAD, canonical initialization, input, run, and receipt reload with
  verified digests after restart.
- The receipt declares synthetic test status, no generation authority, and no
  architectural-usability proof.
- No model, CLI retrieval, spatial derivation, candidate, or Minecraft success
  is fabricated.

## Tests

- Generated probe structure and reload.
- Input-answer contamination scan.
- Framework instance-literal and branching scan.
- Data-only/no-executable scan.
- Receipt authority-denial assertions.

## Stop conditions

- Stop before placing a Pantheon fact or design value in framework code.
- Stop before claiming that storage bootstrap is architectural derivation.
- Stop if a required retained record has no destination in the project layout.

## Implementation evidence

- Added a building-agnostic raw-request bootstrap that writes only through
  `FilesystemProjectRepository`.
- Generated `probes/test_pantheon/` through that module from one raw request.
- The probe contains verified project identity, initial event/canonical state,
  `HEAD`, run, input record, and bootstrap receipt; it contains no executable.
- The request has no numeric or structured architectural answer fields.
- The receipt sets `synthetic_test=true`, `generation_authority=false`,
  `derived_design_available=false`, and
  `architectural_usability_proven=false`.
- Three focused integration tests verify reload, contamination boundaries, and
  absence of instance literals or building-type branches in framework code.


## Completion

- Completed: 2026-07-25
- Evidence: Added generic raw-request project bootstrap and verified record discovery; generated probes/test_pantheon through archflow.project from one non-numeric raw request; no executable or derived architecture data; receipt denies generation/usability authority; 100 full tests, compileall, scope, and rendered map pass.
