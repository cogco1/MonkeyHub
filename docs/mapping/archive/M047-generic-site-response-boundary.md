# M047 — Generic site-response boundary

- Origin: Modify
- Status: Done
- Depends on: P027

## Goal

Remove fixed site-response vocabulary from `SiteContext` so the framework
stores observations and obligations while project-authored semantics name
possible design strategies.

## Write scope

- `archflow/state/site_context.py`
- `archflow/runtime/site_compiler.py`
- `archflow/runtime/terrain_adaptation.py`
- `archflow/capabilities/terrain.py`
- `tests/test_site_context.py`
- `tests/integration/test_terrain_adaptation_flow.py`
- `probes/p027-terrain-adaptation/`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Acceptance

- Current `SiteContext` records contain no fixed grading, relocation,
  foundation, or other response-selection fields.
- Uneven ground still creates a generic Architect-owned response obligation.
- P027 strategy codes and expert identities remain project data.
- The migrated P027 probe reloads with exact semantic-geometry and contact
  evidence.

## Tests

- Site-context schema and round-trip tests.
- P027 data-isolation and reload tests.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if observations lose ground, approach, protection, or unknown evidence.
- Stop if the framework acquires a replacement fixed strategy vocabulary.
- Stop if the migration changes the selected project strategy or geometry.


## Completion

- Completed: 2026-08-10
- Evidence: SiteContext@2 removes all fixed response-selection fields and the site compiler now emits a generic Architect-owned ground-response obligation.
- Evidence: TerrainResponseOption@2 stores project-authored strategy_code plus explicit resolved obligation ids; the framework no longer registers or enumerates grading, siting, support, or other strategy experts.
- Evidence: The migrated P027 probe keeps its selected strategy and exact semantic geometry while reloading 8 passing contacts and the failed unchanged-plan stop; 12 focused tests, architecture firewall, and 462 full tests passed with 3 skips.
