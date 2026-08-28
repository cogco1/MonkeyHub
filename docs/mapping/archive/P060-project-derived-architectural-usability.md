# P060 — Project-derived architectural usability

- Origin: Planning
- Status: Ready
- Depends on: P059, P020, P021, P028, P030, P048, P058

## Goal

Replace artifact-presence-only acceptance with one exact-project architectural
usability contract compiled from authorized brief evidence, adopted retrieval
evidence, and current design state, then evaluate the realized neutral artifact
without putting a building answer or model self-certification into the
framework.

## Write scope

- `archflow/validation/architectural.py`
- `archflow/validation/__init__.py`
- `archflow/validation/README.md`
- `tests/test_architectural_usability.py`
- `tests/integration/test_project_derived_architectural_usability.py`
- `probes/p060-architectural-usability/`
- `docs/ARCHITECTURE.md`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- A typed project validation contract binds one exact project/run/base, current
  design-state digest, selected component tree, neutral geometry program,
  sandbox realization, and the authorized project records that supplied every
  mandatory criterion.
- Criteria are compiled only from authorized brief facts or commitments,
  adopted retrieval evidence, current program/site/build-policy constraints,
  and explicit current-state obligations. Empty evidence cannot produce a
  passing contract, and retrieved text alone cannot acquire design authority.
- The framework provides only generic measurable relation and threshold
  operators. Building types, room lists, dimensions, topology, materials,
  components, and pass values remain project records or probe data.
- Architectural acceptance requires artifact presence plus every mandatory
  project-derived criterion to be evaluable and pass. Missing, stale,
  unsupported, or contradictory evidence yields typed unknown or failed
  findings, never silent omission or model self-certification.
- Findings retain criterion, source, component, geometry-object, and obligation
  references so a local failure can drive local semantic-geometry revision.
- Two non-isomorphic project fixtures prove different contracts and outcomes;
  a fresh P036 probe retains only inputs, compiled contract, observations,
  receipt, and claim boundary. P058 remains frozen and artifact-presence-only.

## Tests

- Exact schema reload, provenance membership, digest binding, authority,
  operator, unit, and unsupported-criterion rejection.
- Pass, fail, unknown, stale-state, missing-observation, contradictory-source,
  and local-finding tests over two non-isomorphic projects.
- P036 probe reload, no instance literals in framework, architecture firewall,
  V3 boundary, scope, diff, and full unit discovery.

## Stop conditions

- Stop before inventing a default architectural rule, threshold, room,
  topology, component, material, or building-type answer in `archflow/`.
- Stop if retrieval output or an LLM receipt becomes validation authority
  without an adopted exact-project record.
- Stop if validation mutates geometry, design state, project HEAD, or replaces
  candidate review, commitment completion, or canonical promotion.
- Stop before interpreting artifact presence, visual plausibility, or a model
  assertion as architectural usability.


## Completion

- Completed: 2026-08-17
- Evidence: Project-derived ArchitecturalUsabilityContract@1 binds exact P036 base developed-design digest sole selected component tree CompiledGeometryProgram@2 sandbox scene realization receipt artifact and authorized sources. Fourteen P060 tests prove schema authority adoption pass fail unknown stale contradiction locality two non-isomorphic project outcomes and fresh P036 reload; full discovery passed 553 tests with 3 skips; ARCHITECTURE PASS 118 files V3 boundary PASS 118 production files 2 frozen oracles P060 scope diff compileall and retained-probe integrity checks pass. Probe claim boundary is project://p060-architectural-usability/runs/architectural-usability-001/records/architectural-usability-claim-boundary-c8cf8276dbfede60466a29d0af8beed9c8a86dfcce52c21fc9c155229c686719.json; P058 remains artifact-presence-only.
