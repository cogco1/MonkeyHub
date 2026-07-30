# P014 — V3 ownership handover and dependency-backflow guard

- Origin: Planning
- Status: Done
- Depends on: P013

## Goal

Make the first V3 capability handover durable: V4 production owns the neutral
contract, the frozen V3 implementation remains an explicit oracle/provider,
and Pack/V2 dependencies cannot silently return through imports or fallback.

## Write scope

- `governance/v3_legacy_ownership.json`
- `tools/check_v3_boundary.py`
- `tests/test_v3_boundary.py`
- `docs/mapping/`
- `docs/migration/`

## Acceptance

- Ownership records distinguish contract owner, native provider, legacy
  provider, oracle evidence, and retirement exit gate.
- V4 production has no direct import of V3, its Packs, composers, keyword
  router, or legacy adapters.
- Any legacy invocation is explicit and emits a provider/version receipt.
- Provider failure returns a named unavailable result; automatic fallback is
  rejected.
- The migrated responsibility has one production owner and zero competing
  writers.
- Retirement means zero production authority, not deletion of V3 evidence.
- A deterministic boundary audit is suitable for the default test suite.

## Tests

- Static direct-import and forbidden-symbol scan.
- Explicit-provider and no-fallback regression.
- Single-owner and zero-writer-conflict validation.
- Frozen oracle remains separately runnable and loadable.

## Stop conditions

- Stop if the guard would require deleting V3 or its Gold/Red evidence.
- Stop if both native and legacy providers retain production ownership.
- Stop if a compatibility shim can bypass the explicit provider receipt.

## Implemented boundary

- A versioned ownership declaration binds the frozen V3 revision, selected
  responsibility, one V4 contract owner, zero writers, explicit legacy
  provider identity, and preserved oracle evidence.
- The deterministic boundary checker scans production ASTs for direct V3,
  Pack, V2, composer, keyword-router, and legacy-adapter backflow. It also
  prevents undeclared bridge consumers and embedded machine-specific V3 paths.
- Native absence and legacy failure remain named non-authoritative states.
  Missing providers emit a provider/version receipt with no fallback.
- The two P013 records are reloaded by content digest and must continue to say
  that generation, architectural usability, and canonical writes are unproven.

## Completion evidence

- `V3LegacyOwnershipPolicy@1` binds the P011 manifest digest and V3 commit,
  identifies one V4 contract owner and zero writers, and records the native
  provider as absent and the legacy provider as explicit and non-authoritative.
- The boundary audit passes across 89 V4 production files and two frozen P013
  oracle receipts. Mutation tests reject direct imports, retired symbols,
  undeclared bridge consumers, embedded V3 machine paths, competing owners,
  and legacy writers.
- The missing-provider path retains provider identity, reports version
  `unavailable`, emits no command, and records that no fallback or canonical
  write was attempted.
- Six focused tests and 309 full tests passed with one external smoke skipped;
  the architecture firewall, compileall, and five-path scope check passed.
  V3 remained at commit `3e70a4d23adb9c77e09fd230e38260389d44b8cd`
  with the same 22 tracked changes captured by P011.


## Completion

- Completed: 2026-07-28
- Evidence: One V4 contract owner and zero writers; static AST guard blocks V3/Pack/V2/composer/router/legacy-adapter and undeclared bridge backflow; explicit provider/version receipts never fallback; two frozen P013 oracles reload by digest; 309 tests pass with 1 external smoke skipped, archcheck/compileall/scope pass; V3 remains commit 3e70a4d with 22 tracked changes.
