# P024 — Developed-design candidate assembly and downstream handoff

- Origin: Planning
- Status: Done
- Depends on: P005, P007, P008, P017, P033, P040

## Goal

Compile an Architect-selected and coordinated P040 design-development state into a
`CandidateSubmission`, downstream usability inputs, and an executable MCP plan
without making the legacy `BuildingProgram@1` contract a generation authority.

## Write scope

- `archflow/state/candidate_program.py`
- `archflow/runtime/candidate_assembly.py`
- `archflow/submission/`
- `archflow/validation/`
- `archflow/runtime/README.md`
- `tests/test_candidate_assembly.py`
- `tests/integration/test_candidate_handoff.py`
- `docs/mapping/`

## Acceptance

- Every function, area, topology, dimension, material, and coordinate in the
  candidate traces to the current building run.
- Candidate assembly rejects schematic-only or phase-skipped input even when
  its geometry is executable.
- The selected portfolio branch and its lineage are explicit.
- Required approval and build policies are carried forward without fabricating
  an approval.
- A compatibility adapter may feed existing validators but cannot invent
  missing values or become the upstream program compiler.
- Preview and execute bind the same exact plan.
- Hard gates, commitment monitoring, aesthetics, and committer authority remain
  separate.
- Rejected and revised candidates preserve their full derivation receipts.

## Tests

- End-to-end offline handoff from a derived design state.
- Missing derivation receipt fails closed.
- Legacy `BuildingProgram@1` adapter has no defaults or reverse authority.
- Candidate/committed artifact separation.

## Current evidence

- `compile_candidate_program` accepts only a coordinated, non-invalidated P040
  state and copies the exact P033 portfolio, branch revision, selection
  receipts, canonical base, and P040 state digest into the candidate
  projection.
- Function zones, footprint area, topology, levels and bounds, footprint
  coordinates, material values, and developed-component attributes are stored
  as canonical JSON with source and derivation references. Project-supplied
  validation values must also cite the current selected schematic.
- The build and approval policies are explicit digest-bound inputs. Candidate
  assembly records that approval has not yet been evaluated; it cannot create
  an approval receipt.
- Every leaf of the project-authored MCP payload has a JSON-pointer provenance
  binding. Preview and execute receipts fail closed unless both use the frozen
  payload digest.
- MCP execution creates a new speculative submission containing the candidate
  artifact. It leaves hard usability, commitment monitoring, aesthetics, and
  canonical promotion unset.
- The P005 compatibility adapter requires an explicit candidate value for
  every `BuildingProgram@1` field, including empty lists and tolerance. It has
  no default path and no reverse compiler.
- Full assembled, executed, rejected, or revised derivations can be written
  only through the P036 run-candidate destination. Reload verifies the embedded
  projection, plan, policies, receipts, and authority-denial flags while
  project `HEAD` remains unchanged.
- Targeted P024 tests pass (6 tests). Full regression passes (259 tests, 1
  external smoke skipped), compileall passes, and the architecture firewall
  passes 84 production files.

## Stop conditions

- Stop if compatibility code becomes a permanent hidden kernel.
- Stop if MCP success advances canonical state without full acceptance.


## Completion

- Completed: 2026-07-27
- Evidence: Coordinated P040 state now compiles into an exact-lineage CandidateProgramProjection, provenance-complete frozen MCP plan, explicit build/approval policy bindings, pre-execution CandidateSubmission, exact preview/execute artifact handoff, no-default one-way BuildingProgram validator view, and reloadable full rejected/revised derivation archives. 259 tests pass with 1 external smoke skipped; architecture firewall passes 84 files; compileall and scope checks pass.
