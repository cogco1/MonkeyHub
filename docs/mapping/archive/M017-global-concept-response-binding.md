# M017 — Global concept response binding

- Origin: Modify
- Status: Done
- Depends on: P022, M016

## Goal

Make active hard commitments owned by the global-concept root explicit
mandatory response references for every nested local Architect action.

## Write scope

- `archflow/runtime/design_controller.py`
- `tests/test_design_controller.py`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- Every local action cites each active or violated hard root commitment.
- Missing a governing root commitment fails before nested state compilation.
- Proposed, negotiable, preference, and hypothesis commitments are not
  promoted into mandatory global constraints.
- Citation records responsibility only; validators and commitment monitors
  still decide semantic satisfaction.

## Tests

- Missing governing commitment rejection.
- Grounded action with one global commitment and one local obligation.
- Existing bounded stop, phase, clarification, and resume behavior.
- Full regression.

## Stop conditions

- Stop if enforcement requires interpreting free-form concept prose.
- Stop if response citation is treated as commitment satisfaction or gate
  waiver.


## Completion

- Completed: 2026-07-28
- Evidence: Active or violated hard commitments at the global-concept root are now mandatory Architect response references without claiming semantic satisfaction; missing-global regression, 18 focused tests, 312 full tests with 1 explicit external smoke skip, architecture firewall, and compileall passed.
