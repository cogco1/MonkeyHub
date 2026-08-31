# M081 — Typed straight-loft CAD translation

- Origin: Modify
- Status: Done
- Depends on: M080

## Goal

Expose a typed straight-loft interpolation mode in Rhino translation so
profile-derived analytic bounds remain exact instead of being violated by
smooth NURBS end overshoot.

## Acceptance

- `loft_type=straight` emits ruled sections; an absent value preserves normal
  loft behavior.
- Unsupported loft types fail before script execution and cannot silently map
  to another interpolation.
- No project dimension, component name, or tolerance exception enters the
  reusable adapter.

## Tests

- CAD translation straight/default/invalid loft-type tests.
- Architecture check and compileall.

## Stop conditions

- Stop before changing every historical loft to straight by default.
- Stop before accepting a smooth loft whose readback exceeds its exact profile
  denominator.
- Stop before adding a Villa-specific rule.


## Completion

- Completed: 2026-08-30
- Evidence: 17 CAD translation tests passed; typed straight loft emits Rhino ruled sections, default normal loft is unchanged, invalid types fail closed; repository verification, architecture check, compileall, and diff check passed.
