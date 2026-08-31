# M080 — Explicit open-loft CAD translation

- Origin: Modify
- Status: Done
- Depends on: M079

## Goal

Honor an explicit open-ended loft in exact Rhino translation so a measured
ring profile remains an actual opening instead of being silently capped or
rebuilt through an analytically indeterminate boolean.

## Acceptance

- `cap_ends=false` reaches Rhino as an uncapped Brep; `cap_ends=true` retains
  the existing cap behavior.
- The choice comes only from the typed operation parameter and introduces no
  building-specific dimension or default.
- Visible bounds remain derived from the exact profile denominator; tolerance
  is not widened and no boolean is faked.

## Tests

- CAD translation open-versus-capped loft regression tests.
- Architecture check and compileall.

## Stop conditions

- Stop before inventing an opening when the typed operation does not request
  one.
- Stop before using a boolean whose visible bounds cannot be independently
  derived.
- Stop before weakening the existing capped-loft behavior.


## Completion

- Completed: 2026-08-30
- Evidence: 16 CAD translation tests passed; cap_ends=false omits Rhino capping while the default/cap_ends=true path is unchanged; repository verification, architecture check, compileall, and diff check passed.
