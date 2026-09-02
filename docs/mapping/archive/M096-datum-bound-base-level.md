# M096 — Datum-bound base level for extrusion and loft

- Origin: Modify
- Status: Ready after P095
- Depends on: P090, P095

## Goal

P090 lowered "derived, not restated" to the coordinate level in the
compiler, but the only production op vocabulary (extrusion, loft) keeps
an object's elevation inside its profile points, so a datum bound to a
parameter never moved geometry: the villa clash families were embeds of
exactly that kind. Add one optional, datum-bindable parameter,
``base_level`` (program Y, the elevation the profile's lowest point must
sit on), consumed consistently by the CAD translator, the analytic
bounds replay, and the sandbox realization. Dependents author their
profiles as shape only; the level they sit on comes from the datum.

## Acceptance

- ``lift_to_base_level`` shifts profile points so the lowest point sits
  on the level, keeps the shape, passes points through when the
  parameter is absent, and fails typed on a non-finite or non-numeric
  level.
- One datum bound to ``base_level`` through the real producer moves the
  object in the sandbox realization, in ``expected_object_bounds``, and
  in the emitted Rhino script, while the authored profile still sits at
  Y = 0.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/adapters/cad_program.py`
- `archflow/realization/sandbox.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Lift helper: pass-through, shift, typed failure.
- Datum -> compiler -> sandbox / bounds / script end to end.
- Architecture firewall.

## Stop conditions

- Stop before changing any existing op's meaning when ``base_level`` is
  absent.
- Stop before a building-type default for any level.


## Completion

- Completed: 2026-09-01
- Evidence: lift_to_base_level (cad_program) and _lift_to_base_level (sandbox) apply one optional datum-bindable parameter base_level to extrusion and loft profiles: the lowest point is lifted onto the level, shape kept, absent parameter leaves every op unchanged, non-finite or non-numeric fails typed. End to end: a LEVEL datum bound to base_level through the real producer moves the object in the sandbox realization, the analytic bounds replay, and the emitted Rhino script while the authored profile stays at Y = 0. Live proof: villa run-017 west portico through three seats (P095) realized in Rhino via the COM export with readback verified - 49 objects, every derived level (landing 3.570, column top 9.798, abacus top 9.980, entablature soffit/top 9.980/11.335, course seats, wall and opening levels) lands within 1 mm of its datum; 5 tests in test_base_level_datum; full suite 1683 with only the known viewer socket flake (passes alone); ARCHITECTURE PASS (295 files).
