# P030 — Voxel use-scenario validation

- Origin: Planning
- Status: Done
- Depends on: P024

## Goal

Extend minimal static usability gates with deterministic voxel use scenarios:
enter the building, traverse doors and vertical circulation, reach required use
zones, and verify that occupied routes remain physically usable.

## Write scope

- `archflow/validation/use_scenarios.py`
- `archflow/validation/README.md`
- `tests/test_use_scenarios.py`
- `tests/fixtures/voxel/`
- `tests/integration/test_live_use_scenarios.py`
- `docs/mapping/`

## Acceptance

- Scenarios derive from the current building program and candidate evidence.
- The primary observation is deterministically derived from the exact sandbox
  realization; an external-world scan is optional comparison evidence.
- Entrance-to-zone and inter-zone routes account for collision, openings,
  doors, headroom, level changes, and declared vertical circulation.
- Unknown observation fails only the scenario it prevents checking.
- Scenario failure is a hard finding with measurements and evidence, not a
  direct geometry edit.
- No renderer, exporter, MCP build, Revit model, or other platform result can
  bypass failed use scenarios.

## Tests

- Gold entrance-to-required-zones traversal.
- Blocked door, low headroom, isolated floor, and unreachable use-zone Reds.
- Read-only validator and exact-candidate binding.
- Exact sandbox-realization binding; optional external observation smoke stays
  in its adapter card.

## Stop conditions

- Stop if the primary observation lacks an exact sandbox-realization receipt;
  an external observer cannot substitute for it.
- Stop before treating visual resemblance as functional reachability.

## Implemented contract

- Entrance-to-zone and inter-zone traversal, headroom, openings, disconnected
  levels, vertical circulation, and localized unknown evidence are
  deterministically evaluated without a writer.
- `ScenarioObservationBinding@1` now requires the primary observation to bind
  the exact candidate program, neutral geometry program, deterministic sandbox
  realization receipt, validation program, observation content, artifact, and
  workspace.
- Missing or stale bindings fail closed. An external-world observation is
  explicitly comparison-only and cannot satisfy primary hard validation.
- P030 defines and consumes the binding contract; P048 still owns production of
  a real deterministic sandbox-realization receipt.


## Completion

- Completed: 2026-07-29
- Evidence: Implemented deterministic entrance-to-zone and inter-zone traversal with collision, openings, headroom, levels, explicit vertical circulation, localized unknown handling, and read-only hard findings; added ScenarioObservationBinding@1 so primary validation requires exact candidate-program, neutral-geometry-program, sandbox-realization receipt, validation-program, observation, artifact, and workspace digests, while external observations are comparison-only; 7 P030 focused tests, 20 combined P030/M018 tests with 1 opt-in skip, and 349 full tests with 2 skips passed, plus compileall, 95-file architecture firewall, scope checks, and machine verification. P048 production of a real sandbox receipt remains unproven.
