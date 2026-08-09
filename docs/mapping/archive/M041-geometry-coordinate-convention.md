# M041 - Geometry coordinate convention

- Origin: Modify
- Status: Done
- Depends on: M040, P048, P050

## Goal

Publish the sandbox kernel's coordinate convention as an exact machine-readable
geometry-authoring contract so a provider cannot silently exchange Y-up and
Z-up interpretations.

## Acceptance

- Every geometry request identifies a right-handed `[x,y,z]` convention with
  Y vertical and XZ as the horizontal footprint plane.
- Footprint cells, spatial bounds, vector parameters, and solid size fields
  publish their exact axis order.
- The authoring instruction states that solid `origin[1]` is elevation and
  `size[1]` is height and explicitly forbids treating Z as vertical.
- The same coordinate contract appears in the output contract and the request
  context.
- No coordinate adapter rotates a model response after the fact; typed model
  geometry remains the geometry that realization validates.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `tests/test_geometry_proposal_producer.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Exact request and output-contract coordinate convention.
- Explicit Y-up/Z-horizontal authoring instruction.
- Existing accepted proposal compilation and realization.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the change would rotate accepted geometry implicitly, reinterpret
  persisted records, or embed a building-specific coordinate answer.


## Completion

- Completed: 2026-08-10
- Evidence: Published one exact right-handed Y-up XZ-footprint coordinate contract across request and output guidance after run 016 proved the prior convention was hidden by authoring roof door and floor geometry as Z-up; 26 focused tests, architecture firewall, and deterministic full discovery passed.
