# M043 - Sandbox findings to geometry repair

- Origin: Modify
- Status: Done
- Depends on: M042, P026, P050

## Goal

Carry the exact rejected sandbox measurements and evidence through semantic
revision into the first geometry-authoring request for that revised candidate.

## Acceptance

- P026 derives one immutable `SandboxRepairFinding@1` tuple from the rejected
  candidate and supplies the same tuple to semantic revision and geometry
  revision.
- The geometry producer accepts typed initial repair issues without granting
  them hard-gate or canonical-write authority.
- Each geometry issue retains source, code, message, measured value, threshold,
  and evidence references as canonical JSON.
- The first revised geometry request contains the exact prior sandbox issues;
  model-output parser/compiler issues may still replace them on the bounded
  second geometry round.
- Concept geometry requests remain empty of fabricated repair issues.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/runtime/sandbox_gold.py`
- `tests/integration/test_sandbox_gold.py`
- `governance/work_registry.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`

## Tests

- Rejected zero-walkable candidate sends the same structured connectivity
  finding to semantic and geometry revision.
- Existing bounded geometry repair behavior.
- Accepted/rejected candidate persistence and reload.
- Architecture firewall and full unittest discovery.

## Stop conditions

- Stop if the change would synthesize a repair geometry, mutate a prior receipt,
  bypass the model, or treat a finding as proof that the revision passed.


## Completion

- Completed: 2026-08-10
- Evidence: Preserved each rejected SandboxRepairFinding as a canonical typed initial GeometryProposalIssue so the same source code message measured threshold and evidence refs now reach semantic and geometry revision; 39 focused/integration tests, architecture firewall, and deterministic full discovery passed.
