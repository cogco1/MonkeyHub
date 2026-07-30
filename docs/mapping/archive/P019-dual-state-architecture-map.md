# P019 — Dual-state architecture and coverage map

- Origin: Planning
- Status: Done
- Depends on: P000
- Supersedes diagram semantics: P010 (historical layout evidence remains archived)

## Goal

Realign the V4 architecture diagram, terminology, registry, and dynamic map
around three separate objects: canonical project state `C_v`, branch-local
operational design state `D_v,k`, and append-only evidence trace `H<=t`.

## Write scope

- `docs/ARCHITECTURE.md`
- `docs/diagrams/v4-bounded-agency.svg`
- `docs/diagrams/v4-bounded-agency.png`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`
- `tools/devctl.py`
- `tests/test_devctl.py`

## Acceptance

- The diagram connects raw brief compilation, the inner design loop, candidate
  review, single-writer commit, and state rebuild.
- Design experts appear inside the `D_v,k` loop; hard gates, commitment
  monitoring, and aesthetics remain distinct at submission.
- Instance answers such as dimensions, topology, materials, and coordinates are
  building-run results, never framework defaults.
- The dynamic map distinguishes proven, partial, missing, at-risk, and blocked
  architecture layers and names the cards that close each gap.
- Existing archive evidence remains historical and is not rewritten into a new
  implementation claim.

## Tests

- SVG XML parse and PNG visual inspection.
- Registry load and generated-map consistency.
- Architecture-layer card-reference validation.
- Full standard-library tests and compileall.

## Stop conditions

- Stop if a diagram simplification collapses `C_v`, `D_v,k`, and `H<=t`.
- Stop if a map edit falsely marks planned or fixture-only behavior as proven.


## Completion

- Completed: 2026-07-25
- Evidence: Dual-state SVG/PNG now connects brief compilation, D_v,k Architect/expert design loop, candidate review, single-writer commit event, reducer, and C_v+1; registry/dynamic map cover L0-L9 and M001/P020-P031; SVG XML and 2400x1920 PNG anchors, 77 tests, compileall, scope, registry rendering, and Markdown links pass.
