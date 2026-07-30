# P023 — Spatial topology and footprint proposals

- Origin: Planning
- Status: Done
- Depends on: P022, P032, P034, M015

## Goal

Provide building-scoped capabilities that turn a selected program and relation
state into multiple topology, level, massing, and footprint proposals. Concrete
dimensions appear here only with a derivation chain or external constraint.
These are schematic-design outputs. They must not masquerade as coordinated
design-development or execution-ready candidates.

## Write scope

- `archflow/state/spatial.py`
- `archflow/capabilities/spatial.py`
- `archflow/capabilities/README.md`
- `archflow/state/__init__.py`
- `tests/test_spatial_proposals.py`
- `tests/fixtures/spatial/`
- `docs/mapping/`

## Acceptance

- Proposals bind the current program, area schedule, relation graph,
  `SiteContext`, resource/build policy, and exact design-state digest.
- Input phase must be site/resource coordination and output phase must be
  schematic design under the P039 gate.
- Single/multi-level and compact/distributed alternatives remain comparable.
- Footprint, coordinates, palette, and typology are candidate results, not
  framework defaults.
- The primary Architect selects, revises, combines, or rejects proposals.
- Structure, circulation, envelope, material, and constructability may advise
  schematic risk here, but coordinated resolutions belong to P040.
- Two non-isomorphic cases prove that no building answer leaks across runs.

## Tests

- Multiple topology proposals from one program state.
- Site-envelope and explicit-footprint constraint cases.
- Cross-run answer-isolation regression.
- Stale program or site evidence rejection.
- Wrong-phase input and developed-design-output rejection.

## Current evidence

- `SpatialOptionProposal@1` keeps footprint cells, levels, volumes, function
  zones, topology links, typology hypotheses, palette refs, rationale, expert
  refs, and constraint responses as project-authored proposal data.
- `compile_spatial_options()` requires an exact P022 operational state and the
  deterministic P039 gate from site/resource coordination to schematic design.
- Program, site, build policy, branch, run, canonical base, brief, and content
  digests fail closed on any mismatch.
- Function nodes enter exactly one zone; relationship endpoints and direction
  are preserved; massing and footprint remain within the observed envelope;
  an explicit grid basis connects candidate cell count to its cited footprint
  range.
- Hard policy constraints and open obligations require explicit schematic
  responses. A `risk` response remains unresolved for P040 and never becomes a
  hard-usability verdict.
- Options are normalized into stable ID presentation order with no ranking or
  selection authority. Structurally identical proposals with renamed IDs do
  not count as alternatives.
- Two project cases with different function-graph cardinality compile to
  different option sets without cross-run answer leakage.

## Stop conditions

- Stop if registration order becomes proposal ranking.
- Stop if one reference building becomes the fallback result.


## Completion

- Completed: 2026-07-26
- Evidence: SpatialOptionProposal@1 and deterministic compilation now bind exact P022 state, P039 phase gate, DesignProgram, SiteContext, and BuildPolicy; multiple structurally distinct unranked options preserve project-authored footprint, levels, massing, zones, topology, typology, palette refs, evidence, and typed risks without hard-usability or selection authority. Cross-run non-isomorphic isolation, stale inputs, site envelope, footprint range, phase, hard-constraint response, serialization, tamper, and registration-order tests pass; 238 tests passed with 1 external smoke skipped, architecture firewall and compileall passed.
