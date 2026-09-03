# P110 — CanonicalState projection for State-Record projects (kernel)

**Status:** ready (not started)
**Lane:** productization and componentization
**Depends on:** P102
**Retires:** the labelled-vacuous validation receipt in P108 (`canonicalFacts: "unavailable (schema drifted; K2)"`)
and the Studio's `CanonicalState(ref=head)` empty-facts construction.
**Raised by:** 新建会话 during P108 calibration (2026-09-03); reproduced by the main session on the villa HEAD.

## The gap

`canonical_state_from_dict()` (`archflow/runtime/state_reducer.py`) accepts only the exact `CanonicalState@1` key
set and raises `StateReducerError("canonical state schema drifted")` on anything else. On a State-Record project
`repository.load_current_state()` returns `CanonicalProjectState@1` (`authoritative_record_refs`,
`derived_record_refs`, `phase`; reproduced on the villa HEAD, version 0) and the retained snapshot record kind is
`CanonicalSnapshot@2` (`archflow/project/repository.py`); both are ref-based, neither is `CanonicalState@1`. So
`validate_submission()` can only be handed `CanonicalState(ref=head)` with empty facts, commitments and
obligations. With nothing to check, `ObligationDischargeValidator` and `AuthorizedCommitmentClaimsValidator`
return no findings and the receipt reduces to `ArtifactPresentValidator`. `RequiredClaimsValidator` is
compatibility-only and fails on production state (`compatibility.goal_contract_missing`); it is excluded, not
fixed.

## Direction

A projection from a bound `StateRecord@1` plus the HEAD ref to `CanonicalState`: `open_obligations` from
`record.obligations`, `commitments` from the record's authorized commitments (or the seats' `commitment_ref`
lineage), `artifacts` from the run's receipts, `facts` from the run's relation checks. Either
`canonical_state_from_dict` learns `CanonicalSnapshot@2`, or a named `canonical_state_of(record, head)` sits beside
`developed_design_view` — one of the two, never both.

## Acceptance

- [ ] Villa HEAD + bound record → a `CanonicalState` whose `ref` equals `read_head()` and whose open obligations
      equal the record's.
- [ ] `validate_submission` on that state exercises `ObligationDischargeValidator` and
      `AuthorizedCommitmentClaimsValidator` on real content; a test shows at least one finding is reachable.
- [ ] The Studio drops the "unavailable" label and the empty-facts construction in the same change.
- [ ] Tests beside the reducer and validation engine; `python tools/archcheck.py` green.
