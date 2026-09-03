# P109 — Typed operator on StateRecord@1 (kernel)

**Status:** ready (not started); the Studio carries a candidate under this card
**Lane:** productization and componentization
**Depends on:** P102 (StateRecord is the source of truth)
**Retires:** the Studio's `successor_record` candidate (P108 round one, `api/archflow_studio_api/application/candidate.py`)
the day this lands — one in, one out.
**Raised by:** 新建会话 during P108 calibration (2026-09-03); verified against the kernel by the main session.

## The gap

`DecisionOperator` (DecisionOperator@2) and `compile_decision_operator()` in `archflow/state/decision_operator.py`
are typed to `OperationalMarkovState` and raise `TypeError` on anything else; `compile_nested_decision()` in
`design_state.py` works on the design-state tree. No kernel function takes a typed intent and a `StateRecord@1`
and returns the successor record. The vibe-modeling loop needs exactly that: "set this element's `params.height`
to 4.2" → successor record → runner → receipt.

## Round-one candidate (lives in the Studio, under this card)

AGENTS.md rule 3: nothing can be retired yet, so the abstraction is not canonical; it is written as a candidate
under the card, not into `archflow/`. `successor_record(record, change)` is a `dataclasses.replace` of one
`Element@1` numeric field or one `Parameter` value; the protected set is checked against `record.closure`; base
exactness is `change.base_state_digest == record.state_digest`; it contains no propagation logic (propagation is
the runner's). The candidate must stay that small; the moment it needs more, this card is due.

## Promotion condition

Land in `archflow/state/` when a second consumer appears (a CLI edit path, a batch tool, the runner's own
re-seating) or when the Studio candidate needs anything beyond a single-value replace (multi-field intents, entity
add/delete, relation edits). Landing means one function in the kernel, the Studio candidate deleted in the same
change, tests in `tests/test_state_record.py`, and the P108 DTOs unchanged.

## Acceptance

- [ ] A kernel function applies a typed intent to a `StateRecord@1`: exact-base check on `state_digest`, protected
      set checked against the record's closure, deterministic successor, `StateRecordError` on anything it cannot
      apply.
- [ ] The Studio's `successor_record` is retired in the same change; no second implementation remains.
- [ ] Tests: stale base → error; protected key → error; one value changed → successor whose `digest` differs and
      whose other entities are equal; one villa run through the `tools/verify_state_record.py` harness.
- [ ] `python tools/archcheck.py` green.
