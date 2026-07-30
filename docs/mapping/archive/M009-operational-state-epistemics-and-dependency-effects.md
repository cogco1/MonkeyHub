# M009 — Operational-state epistemics and typed dependency effects

- Origin: Modify
- Status: Done
- Depends on: P041, P042

## Goal

Repair the information loss and over-broad dependency semantics exposed by the
P042 real-building compilation probe. Introduce a versioned operational state
whose facts retain decision-relevant epistemic status, whose obligations retain
conditional and blocked lifecycle state, and whose dependency edges state what
effect may propagate.

This is a framework repair. It must not encode Pantheon facts, output values, or
an expert schedule.

## Why this is a modification

`OperationalMarkovState@2` currently serializes structured case values into a
plain `StateFact.value` string, permits only open/satisfied/waived obligations,
and traverses every dependency edge as an invalidation edge. P042 therefore had
to preserve epistemic status and blocked/conditional obligation semantics in a
mapping receipt instead of the operational state. It also had to omit valid
obligation-dependency edges because the current closure would incorrectly mark
downstream obligation references as invalidated.

The existing compiler is implemented and has made a narrower claim than its
real schema supports, so the correction belongs in the modify stream rather
than a new design-generation feature.

## Write scope

- `archflow/state/operational_state.py`
- `archflow/state/decision_operator.py`
- `archflow/state/__init__.py`
- `tests/test_operational_markov_compiler.py`
- `tests/integration/test_pantheon_compiled_state.py`
- `probes/test_pantheon/`
- `docs/ARCHITECTURE.md`
- `docs/diagrams/`
- `docs/mapping/`

## Required semantics

```text
evidence
  -> fact(value, epistemic status, source, confidence/qualification)

condition / blocker
  -> obligation(open | blocked | satisfied | waived)

dependency(source, target, effect)
  -> invalidates | requires-revalidation | blocks | supports-only
```

Only invalidation-bearing effects participate in invalidation closure.
Blocking edges update obligation readiness but never turn an obligation
reference into an invalidated deliverable.

## Acceptance

- A versioned successor to `OperationalMarkovState@2` represents fact
  epistemic status explicitly, including at least observed/derived,
  hypothesis, disputed/conflicting, and unknown without embedding status in a
  value string.
- Fact qualification is bounded and deterministic; arbitrary transcripts or
  source documents do not enter operational state.
- `DesignObligation` represents conditional activation and blocked-by
  dependencies as typed state rather than prose-only conventions.
- A newly spawned blocked obligation is legal only when its named blockers are
  present and unresolved; blocker discharge deterministically makes it open.
- `DependencyEdge` declares a typed propagation effect. Invalidation closure
  traverses only effects that explicitly authorize invalidation or
  revalidation.
- Support and blocking relations remain queryable but cannot make facts,
  commitments, or obligations falsely invalidated.
- Existing `OperationalMarkovState@2` and `DecisionOperator@1` traces remain
  readable as compatibility records. Automatic migration fails closed when
  the missing epistemic or obligation semantics cannot be reconstructed.
- A new Pantheon probe run recompiles the P042 conflict without overwriting the
  immutable P042 run: the two affected deliverables become stale, the repair
  obligations remain blocked/open according to their blockers, and unrelated
  outputs remain current.
- Operational and sufficient digests include the new future-relevant fields.
- No building answer, named expert order, or persistence path is added to the
  framework.

## Tests

- Epistemic fact round trip and digest sensitivity.
- Structured value/status separation.
- Conditional obligation activation and blocker discharge.
- Invalidation, revalidation, blocking, and support-only edge matrix.
- Obligation references never appear in invalidated deliverables.
- V2 compatibility load and fail-closed migration.
- P042 immutable-run preservation plus successor-run local closure.
- No instance-answer scan.

## Stop conditions

- Stop if the state starts carrying full source documents or transcripts.
- Stop if confidence alone can authorize a fact or phase transition.
- Stop if dependency kinds prescribe a repair solution.
- Stop before rewriting or deleting the immutable P042 evidence run.


## Completion

- Completed: 2026-07-25
- Evidence: Implemented OperationalMarkovState@3, DecisionOperator@2, StateDelta@2, structured epistemic facts, typed conditional and blocked obligations, exact blocking DAG validation, and effect-filtered local closure.
- Evidence: Added read-only V2 state and V1 operator compatibility loaders with fail-closed migration; current schema round trips and digests include all new future-relevant fields.
- Evidence: Created immutable probes/test_pantheon run compiled-state-002: P042 source tree sha256 b5225958fe7c99688491e93c788aaa5ea2a72d4481ad2ae5f23e5c38853a5b9f remained unchanged, exactly two deliverables are stale, unrelated output is current, obligation readiness is typed, and HEAD remains v0.
- Evidence: 134 unittest tests passed with one explicit external CLI smoke skip; compileall, M009 scope, dynamic-map rendering, devctl, SVG/XML, PNG 1570x1280, and framework instance-answer checks passed.
