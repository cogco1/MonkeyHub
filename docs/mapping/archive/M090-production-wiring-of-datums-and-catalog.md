# M090 — Production wiring of P090/P093 kernels

- Origin: Modify
- Status: Ready after P093
- Depends on: P090, P091, P093

## Goal

Bounded repair found by the 2026-09-01 self-review: the P090 datum
kernel and the P093 catalog gate were built with green unit tests but
had no production caller, and the P091 template's `datum_role` fields
and `mathematics_ref` were never resolved against anything. Put them
on the write path without widening scope: the geometry proposal
producer and the semantic geometry lifecycle accept and forward
`interface_datums`/`datum_bindings` to the compiler; the producer
accepts a `CatalogConfrontationReceipt@1` ref and narrows the model's
admissible template set to the confrontation's selections; template
harvest resolves `mathematics_ref` to an importable archflow solver
module and fails closed otherwise; `verify_template_datums` resolves
obligation datum roles against a template's published datums.

## Acceptance

- A datum bound to an operation parameter through the producer lands on
  the compiled program with the derived value, and the authored proposal
  never restated it.
- A family declined in a recorded confrontation cannot be re-selected by
  the model in the same authoring call; a selected family passes; a
  non-confrontation record fails typed.
- Harvesting a template whose `mathematics_ref` does not resolve to an
  archflow module with a `solve_*`/`compile_*` entry point fails typed;
  the stair template resolves.
- An obligation `datum_role` without a binding, or bound to an
  unpublished datum, is reported as a violation.
- Retained `CompiledGeometryProgram@2` canonical program records still
  wrap in the artifact library; the accepted schema set is declared once
  on the program class and consumed by both readers.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/capabilities/component_library.py`
- `archflow/runtime/semantic_geometry_lifecycle.py`
- `archflow/state/component_template.py`
- `tests/`
- `docs/mapping/`
- `archflow/compilers/geometry.py`
- `archflow/runtime/artifact_library.py`
- `governance/work_registry.json`

(Scope amended 2026-09-01 during execution: the full suite exposed a
P090 read-path regression — `CanonicalProgramRecord` compared stored
programs strictly against the current `@3` schema, so retained `@2`
records in the relocated probes no longer wrapped. The program class now
declares `RETAINED_SCHEMAS`/`ACCEPTED_SCHEMAS` once; the artifact
library and the proposal loader's key set both consume it.)

## Tests

- Producer datum derivation and catalog gate on the write path
  (`tests/test_production_wiring.py`).
- Mathematics-ref resolution and datum-role resolution
  (`tests/test_component_templates.py`).
- Lifecycle compile regression.
- Architecture firewall.

## Stop conditions

- Stop before changing any record schema (proposal, lineage, program).
- Stop if a gate would need a building-type default inside the kernel.


## Completion

- Completed: 2026-09-01
- Evidence: Production wiring: produce_geometry_program_proposal and compile_semantic_geometry_lifecycle accept and forward interface_datums/datum_bindings to compile_geometry_program (a bound parameter derives on the compiled program, never restated by the authored proposal); the producer accepts catalog_confrontation_ref (CatalogConfrontationReceipt@1) and narrows the model's admissible template set to the confrontation's selections - a declined family cannot be re-selected, a foreign record fails typed. resolve_mathematics_ref resolves capability:<archflow module> to an importable solver with a solve_*/compile_* entry point and harvest fails closed otherwise; verify_template_datums resolves obligation datum_role against published datums. Read-path repair found by the full suite: CompiledGeometryProgram now declares RETAINED_SCHEMAS/ACCEPTED_SCHEMAS once, consumed by CanonicalProgramRecord and the proposal loader's key set (retained @2 records in the relocated probes wrap again). Tests: test_production_wiring (4), MathematicsRefTests (3), DatumRoleResolutionTests, RetainedProgramGenerationTests (3); full suite 1667 OK (6 external skips); ARCHITECTURE PASS (294 files).
