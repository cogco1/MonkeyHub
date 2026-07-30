# P012 — Quarantined V3 CLI capability bridge

- Origin: Planning
- Status: Done
- Depends on: P011

## Goal

Provide an explicit, bounded subprocess boundary through which V4 may ask a
frozen V3 capability or oracle for a proposal or observation. The bridge is an
adapter, never a compatibility layer or automatic fallback.

## Write scope

- `archflow/adapters/v3_legacy_cli.py`
- `archflow/adapters/README.md`
- `tests/integration/test_v3_legacy_cli.py`
- `tests/fixtures/v3_legacy/`
- `docs/mapping/`

## Boundary

```text
detached V4 snapshot + obligation + evidence refs
  -> explicit provider command + bounded JSON request
  -> isolated V3 process
  -> Proposal / Observation / FailureReceipt
```

The V3 process receives no canonical store, committer, live world handle,
credentials, or implicit path to another provider.

## Acceptance

- Provider selection and command path are explicit configuration with no
  machine-specific default.
- Requests bind an exact V4 base state and contain only detached, bounded JSON.
- Responses record provider, capability, V3 fingerprint, duration, status,
  evidence references, and output digest.
- Output is workspace-owned and cannot mutate canonical state or a live world.
- Timeout, malformed output, missing V3, and non-zero exit produce bounded,
  named receipts.
- Failure never silently invokes another V3 Pack, composer, or V4 provider.
- Generic V3 CLI, example routing, Pack registration, `compose_building`,
  `decision.v2_stages`, and the legacy adapter are rejected entrypoints.
- The default test suite uses a fake CLI; a real V3 probe is explicit.

## Tests

- Fake success returns a loadable exact-base observation or proposal.
- Timeout, malformed output, and offline provider preserve canonical state.
- Oversized input/output is rejected.
- No silent fallback and no writer handles cross the boundary.

## Stop conditions

- Stop if the bridge needs a direct Python import from V3.
- Stop if the only available command enters the V3/V2 generation path.
- Stop if the selected V3 entry cannot run without global mutable Pack state.
- Stop if failure can change provider without an Architect decision.

## Implemented boundary

- One explicit command binds one provider, one capability, one V3 fingerprint,
  one exact V4 base, and one speculative workspace.
- Requests contain only bounded canonical JSON for the detached snapshot,
  current obligation, and evidence references; writer, world, repository, and
  credential handles are rejected.
- Success repeats every identity and returns a detached proposal or observation
  with a digest. Timeout, offline, exit, malformed, identity drift, capability
  mismatch, and oversized input/output remain named non-writing receipts.
- Generic V3/V2 generation entrypoints are configuration errors. P012 connects
  only a fake CLI and does not claim any real V3 capability is yet safe.


## Completion

- Completed: 2026-07-28
- Evidence: Implemented an explicit shell-free subprocess bridge binding one provider, capability, V3 fingerprint, exact V4 base, and speculative workspace to bounded detached JSON and digest-bearing non-writing receipts.
- Evidence: Generic V3 CLI, examples, Pack registration, compose_building, decision.v2_stages, and legacy adapter commands are rejected; P012 connects only a fake CLI and makes no real V3 safety claim.
- Evidence: 6 focused integration tests and 298 full tests passed with 1 external smoke skipped; architecture firewall passed 88 files, compileall and five-path scope check passed; V3 remained read-only at the previously observed 22 tracked changes.
