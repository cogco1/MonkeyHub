# P068 — Staged declaration gates

- Origin: Planning
- Status: Active (design revised after practice-code research)
- Depends on: P054, P062, P067

## Goal

Gate each design phase behind a mandatory typed declaration set grouped
into irreversibility-ordered quadrants; a phase passes only when every
mandatory declaration is filled, in range, and held by the authored
geometry — and the irreversible quadrants then compile into hard
commitments.

## Grounding (retained research, 2026-08-28)

- Chinese design-depth code (2016): scheme design must fix siting and
  overall massing; preliminary design must fix height, storeys, area,
  and the structural system; construction documents fix everything.
- RIBA 2020: the architectural concept is signed off at stage 2; stage 3
  exists to spatially coordinate so stage 4 has only minor iteration.
- AIA schematic phase: structural grid, floor-to-floor height, riser and
  plant positions are schematic decisions, costly to move later.
- MacLeamy curve: cost of change rises monotonically while influence
  falls — gates belong where the curve is still flat.
- Brand's shearing layers give the irreversibility ordering: site >
  structure > skin > services > space plan > stuff.

## Quadrants and stage attachment

| Quadrant | Content | Gate | On pass |
| --- | --- | --- | --- |
| site | placement, orientation, entry axis | schematic | hard commitment |
| massing-and-grid | footprint, heights, primary span, fill ratio, structural system and grid | schematic | hard commitment |
| skin-and-openings | entries, fenestration strategy, colonnade counts | spatial coordination | commitment |
| space-plan | interior subdivision, rings, secondary spaces | design development | declaration only |
| detail | coffer counts, ornament, patterns | technical design | declaration only |

## Design

- `StageDeclarationContract` (implemented): quadrant-grouped required
  fields with kinds, units, authorized ranges (program ranges plus
  adopted precedent facts), and range provenance; framework stores no
  values.
- Contracts attach to P039 phase gates as typed deliverables; the
  authoring prompt carries the current gate's contract and the strict
  output parse requires a complete `stage_declarations` block
  (implemented for the semantic root).
- Validation (implemented): presence, range, and geometry cross-checks
  (footprint extent, overall height, primary span, fill ratio) within an
  explicit tolerance — a declared footprint cannot host a token model.
- Remaining scope: the site quadrant fields; declaration-to-commitment
  compilation on gate pass; project-authored root contract for the live
  monument; paired live rerun retained as evidence.

## Stop conditions

- Stop if the framework would own any field value or stage default.
- Stop if an inconsistent declaration would be repaired instead of
  rejected.
- Stop if an irreversible-quadrant value could change later without an
  authority-gated commitment transition.

## Tests

- Contract schema, presence, range, and geometry cross-check tests.
- Declaration-gated accept and typed rejection integration paths.
- Declaration-to-commitment compilation on gate pass.
- Architecture V3 scope diff, compileall, full discovery.
