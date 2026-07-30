# M014 — Canonical digest identity

- Origin: Modify
- Status: Done
- Depends on: M007, P018, P036, P045
- Blocks: P022 durable resume

## Goal

Repair the exact-base identity mismatch between P018 and P036.

P018 currently uses `ProjectVersionRef.state_sha256` as the digest of canonical
state content. P036 format-version-1 uses the same field as the digest of the
outer snapshot record. Those values cannot be equal even when they describe
the same state.

New project documents must give the field one meaning: canonical state-content
digest. The immutable snapshot retains its separate `ProjectRecordRef.sha256`.

## Write scope

- `archflow/project/`
- `archflow/runtime/state_reducer.py`
- `tests/test_project_repository.py`
- `tests/test_event_state_rebuild.py`
- `tests/integration/test_project_restart.py`
- `docs/ARCHITECTURE.md`
- `governance/work_registry.json`
- `docs/mapping/`

## Acceptance

- P018 and P036 compute the same state-content digest.
- New format-version-2 snapshots explicitly record that semantic digest.
- Snapshot bytes retain an independent record digest.
- V2 events link both semantic state refs and immutable snapshot refs.
- HEAD and history verification check both layers.
- Existing format-version-1 project documents remain explicit read-compatible.
- A P018 initialization event, P036 HEAD, and P036 RunRef share one exact base.
- Content, claimed digest, snapshot linkage, event linkage, and stale CAS
  tampering fail closed.

## Stop conditions

- Stop if the fix introduces two canonical identities for a new project.
- Stop if the repository imports runtime reducer semantics.
- Stop if format-version-1 data is silently reinterpreted as version 2.
- Stop if snapshot integrity and semantic-state integrity are collapsed.

## Current evidence

- `project_state_sha256` is a project-layer canonical JSON digest shared by
  P018 and P036 without a repository-to-runtime import.
- New repositories use format version 2, `CanonicalSnapshot@2`,
  `ProjectEvent@2`, and `ProjectHead@2`.
- `ProjectVersionRef.state_sha256` names semantic state content; snapshot
  `ProjectRecordRef.sha256` remains independent.
- V2 events carry exact `from_snapshot` and `to_snapshot` links so history
  verification no longer derives a snapshot path from a semantic digest.
- Format-version-1 Pantheon project data reopens and verifies unchanged through
  the explicit compatibility branch.
- A P018 initialization event, new P036 `HEAD`, and P036 `RunRef` now compare
  equal for the same canonical state.


## Completion

- Completed: 2026-07-26
- Evidence: Format-version-2 ProjectVersionRef now uses the shared canonical state-content digest; immutable snapshot digests remain separate and ProjectHead@2/ProjectEvent@2 bind both through explicit snapshot refs.
- Evidence: P018 initialization event, P036 HEAD, and P036 RunRef exact-base parity passes; format-version-1 Pantheon reload remains explicit and unchanged; 226 full tests pass with 1 external smoke skip, archcheck and compileall pass.
