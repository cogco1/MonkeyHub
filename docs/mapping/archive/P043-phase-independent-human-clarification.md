# P043 — Phase-independent human clarification and authority receipts

- Origin: Planning
- Status: Ready after P020
- Depends on: P016, P020, P036, M012

## Goal

Allow the design process to stop at any maturity phase, ask the user for a
genuinely authoritative clarification or selection, retain an exact-base
receipt, and resume without treating chat text or model confidence as project
authority.

This is the upstream clarification boundary exposed by P042. It is distinct
from P025 candidate preview/undo and M004 final candidate approval.

## Design boundary

```text
unresolved authority obligation
  -> ClarificationRequest@1
  -> controller pauses
  -> AuthorityDecisionReceipt@1
  -> exact-base DecisionOperator
  -> verified next D_v,k
```

The request may offer named alternatives and an open response channel, but the
framework cannot select for the user. A reply is evidence for a transition, not
a direct state write.

## Write scope

- `archflow/interaction/clarification.py`
- `archflow/runtime/clarification.py`
- `archflow/interaction/__init__.py`
- `archflow/runtime/__init__.py`
- `tests/test_clarification_authority.py`
- `tests/integration/test_clarification_resume.py`
- `docs/ARCHITECTURE.md`
- `docs/mapping/`

## Acceptance

- `ClarificationRequest@1` binds project, run, branch, canonical base,
  operational-state digest, target facts/commitments/obligations, the unresolved
  authority question, alternatives, and why progress is blocked.
- The requesting Agent has no authority to answer its own request.
- `AuthorityDecisionReceipt@1` binds a named authority, the exact request, the
  selected/revised/declined response, and a bounded validity policy.
- Stale, duplicate, expired, cross-project, cross-branch, and unauthorized
  replies fail closed.
- A valid reply becomes an exact-base proposal and passes through the normal
  decision compiler; it cannot directly mutate canonical or operational state.
- The reply may authorize, revise, release, or leave proposed commitments
  unresolved according to their revision policy.
- Clarification cannot waive a hard gate, pretend to be P025 preview/undo, or
  substitute for M004 candidate approval.
- Unanswered, declined, or timed-out requests preserve a reloadable blocked
  state rather than choosing a default.
- Pause and resume require project records and state digests, not a complete
  chat transcript.
- Two non-isomorphic clarification cases prove that alternatives are
  project-scoped and not framework defaults.

## Tests

- Ambiguous target selection pause and exact-base resume.
- Open-text revision and named-option selection.
- Requesting Agent self-answer rejection.
- Stale, duplicate, expired, authority, project, and branch mismatch.
- Decline and timeout remain blocked.
- Clarification cannot waive hard usability or approve a candidate.
- Resume without transcript.
- Two non-isomorphic projects and no instance-answer scan.

## Stop conditions

- Stop if chat text is accepted as an unbound state mutation.
- Stop if the Agent can answer its own authority request.
- Stop if clarification is made mandatory when the brief is already sufficient.
- Stop if this card absorbs candidate preview, world undo, or commit approval.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented project/run/branch/canonical-base/state-bound ClarificationRequest@1 and single-use time-bounded AuthorityDecisionReceipt@1; named selection, typed open revision, commitment authorize/revise/release, decline, unresolved, unanswered, and timeout semantics preserve authority; stale, duplicate, expired, self-answer, unauthorized, cross-project, cross-branch, untargeted, and downstream-authority attempts fail closed; valid replies compile DecisionOperator@2 before state closure, while requests/receipts persist and reload through the project repository without transcripts; 159 tests passed with 1 external smoke skip, compileall, P043 scope, map rendering, and 5 devctl tests passed.
