# P110 — CanonicalState projection for State-Record projects (kernel)

**Status:** blocked (awaiting a selected actual requirement and its authorized source/candidate pair; not started)
**Lane:** productization and componentization
**Depends on:** P102
**Write scope:** the existing runtime/state/validation projection owner and its tests, plus
`apps/archflow-studio/api/archflow_studio_api/application/validation.py` and its API validation tests for the
same-change integration. Coordinate ownership of those Studio files with P108/P111; do not add a BFF-side
projection or validator.
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

Start with an actual requirement and the existing validator that should consume it.
Follow the exact retained references through the existing project/state owners, then
provide one view for that consumer: obligations from the record, authorized commitments
from their verified source, and artifacts/findings from the corresponding run. A HEAD
or snapshot reference envelope is not itself the design content; accepting its key set
in a deserializer is not a substitute for reading what it references.

Implement only the projection supported by those sources. Do not manufacture commitments
from ordinary parameters or build a second validator just to populate CanonicalState.
Declared relation checks already run independently and remain in use. The P111 candidate
continuation repair can be tried before this card closes, with unverified requirements
still explicit; this is not permission to issue an unchecked project version.

## Acceptance

- [ ] Select one actual retained obligation and one authorized commitment/claim use case before implementing
      the projection. Preserve their origin and authorization; an ordinary parameter, evidence citation or
      agent assertion does not become a HARD commitment merely to populate this view. If required source
      content is absent, identify that concrete gap rather than inventing it.
- [ ] Villa HEAD + bound record → a `CanonicalState` whose `ref` equals `read_head()` and whose open obligations
      equal the record's.
- [ ] `validate_submission` on that state exercises `ObligationDischargeValidator` and
      `AuthorizedCommitmentClaimsValidator` on real content; a test shows at least one finding is reachable.
- [ ] The Studio drops the "unavailable" label and the empty-facts construction in the same change.
- [ ] Tests beside the reducer and validation engine; `python tools/archcheck.py` green.

## CAADRIA-linked execution scope — 2026-09-08

This card is **blocked, not started**, pending the concrete inputs described below. The paper review assigns this gap 22% of
its relative remediation priority; the weight is neither effort nor acceptance
probability and does not reorder unrelated product work. This update authorizes
planning only, not implementation or a new experiment.

- **Owners:** `state.record` owns the design content and consumer views;
  `state.model` owns the receiving canonical validation value;
  `state.commitments` / `state.operational_state` own commitment authority and
  obligation status. `studio.binding` and the project repository resolve the exact
  retained inputs. `studio.validation` assembles the submission and calls
  `validation.engine`; the actual architectural check remains with its existing
  relation/CAD measurement owner. Coordinate the Studio caller with P108.
- **Verified break:** `validate_candidate` still passes `CanonicalState(ref=head)`.
  Source coverage does not populate its obligations or commitments. In addition,
  `ObligationDischargeValidator` only rejects discharge of unknown ids, while
  `AuthorizedCommitmentClaimsValidator` checks that an authorized criterion has a
  claim and evidence references. Neither proves the architectural criterion true.
  Populating these fields alone therefore cannot close this card.
- **First slice:** after the author selects one actual task requirement, follow its
  source and authorization through the retained record, the candidate's exact base,
  the executing state/handover view and the measured result. Extend only the
  existing consumer view and validator/caller needed to carry that requirement and
  consume its actual check. Replace the empty input on that same Studio path;
  retain checks and readers that already work. Missing source or measurement stays
  explicit rather than being turned into a fabricated HARD commitment or a pass.
- **Task acceptance:** for that same requirement and binding, a measured violation
  must produce the corresponding finding; a satisfied result must name the actual
  supporting measurement; absent evidence or an unperformed check must remain
  unchecked and must not discharge the requirement. Include a positive case, a
  violated case, a missing-measurement case and an exact-base mismatch. A supplied
  evidence-reference string alone is not the satisfaction test.
- **Dependencies:** P094's author first fixes the task, exact source/candidate pair,
  allowed changes, preserved conditions and intended claim. The 10% independent
  geometry review can supply the same requirement's measurement through existing
  owners. Interface inspection and protocol preparation can proceed in parallel;
  implementation is selected only after this scope is concrete. P108/P111 candidate
  use and the accepted 3DM display repair do not wait for this card.
