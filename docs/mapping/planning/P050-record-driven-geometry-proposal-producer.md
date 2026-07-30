# P050 — Record-driven geometry proposal producer

- Origin: Planning
- Status: Ready after P023, P029, P047, P048, and P049
- Depends on: P023, P029, P047, P048, P049

## Goal

Build the missing producer between Architect semantics and the geometry
compiler: a capability that derives `GeometryProgramProposal` project
records from P023 `SpatialOptionProposal` records (topology, massing,
typology hypothesis), program facts, and bounded model rounds. Today no
production component authors a geometry proposal — only test fixtures
and the hardcoded template inside `sandbox_gold` do — which is the
architectural gap that forced instance answers into framework code.

## Acceptance

- A producer compiles the selected spatial option, program facts, and
  Architect model rounds into a typed `GeometryProgramProposal`
  persisted as a project record with derivation receipts.
- The model authors operations over the architecture's function
  vocabulary; malformed or out-of-vocabulary output fails typed and
  enters the repair loop; no framework fallback supplies geometry.
- The framework owns no massing, opening count, dimension-bound, or
  typology constant; any reusable template is a provenance-bound
  project input record the Architect selects and parameterizes, with
  its selection recorded and receipted.
- Proposal records bind exact-base digests, the source spatial-option
  record, and semantic bindings to candidate values and commitments.
- Rejected proposal rounds persist alongside the accepted one; lineage
  is reloadable.

## Write scope

- `archflow/capabilities/`
- `archflow/runtime/`
- `tests/`
- `docs/mapping/`

## Tests

- Producer unit tests with a scripted provider (accept, repair, refuse
  paths).
- Record round-trip and reload of proposal lineage.
- Compiler integration: produced proposals compile and realize.
- Static no-instance-default scan over the producer.

## Stop conditions

- Stop if any geometry answer must be hardcoded for the producer to
  proceed.
- Stop if persistence would bypass the `archflow.project` ports.
- Stop before granting the producer or the model any hard-gate,
  acceptance, or canonical-write authority.
