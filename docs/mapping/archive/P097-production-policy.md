# P097 — Production policy: complete the task, keep the state exact

- Origin: Planning
- Status: Ready after M097
- Depends on: P063, P090, P095, M097

## Goal

Separate retry economics from write-side exactness. The clinic case
spent 34 of 40 refusals on bookkeeping the model was asked to restate
(revision preconditions, semantic-change acknowledgements) and then
restarted the whole chain when a 1–3 round budget ran out. Production
policy, opt-in and recorded, off by default so experiments keep their
one-attempt rule:

1. **Bookkeeping completion** — derivable revision preconditions and
   semantic-change acknowledgements are filled by the producer from the
   prior program and recorded as protocol completions, never restated
   by the model.
2. **Progress budget** — rounds continue while the issue set strictly
   shrinks, under a hard cap; every round repairs the last proposal.
3. **Checkpoint resume** — a rejected round is resumable from its
   record; the runner never restarts the accepted chain.
4. **Partial acceptance** — when every remaining issue is object-scoped,
   the producer compiles the reduced proposal (dropping those objects and
   their dependents), accepts it, and records typed deferrals for the
   rest (HOLD).
5. **Stall escalation** — when progress stops, the exact issue list is
   handed over in a typed escalation record with the last proposal kept,
   instead of a bare EXHAUSTED.

Hard validators, single-writer promotion, and no-silent-fallback are
untouched.

## Acceptance

- With completion on, a proposal missing revision preconditions or
  semantic acknowledgements for unchanged-intent edits compiles; the
  completion is recorded per object; with completion off the refusal
  is unchanged.
- With a progress budget, a run whose issues shrink 5 -> 3 -> 1 -> 0
  continues past the round cap that would have exhausted it; a run
  whose issues stop shrinking stops and escalates.
- Resume: producing from a rejected round record continues the same
  lineage without re-authoring the spatial option.
- Partial acceptance: object-scoped issues yield an ACCEPTED reduced
  program plus deferral records naming the dropped objects and their
  dependents; a non-object-scoped issue blocks partial acceptance.
- Escalation record carries the issue list and the last proposal ref.
- Experiment policy (all flags off) reproduces today's behaviour bit
  for bit on the existing producer tests.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/capabilities/geometry_proposal.py`
- `archflow/compilers/geometry.py`
- `archflow/runtime/production_compiler.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Completion on/off, progress budget, resume, partial acceptance,
  escalation; experiment-policy equivalence; architecture firewall.

## Stop conditions

- Stop before any flag weakens a hard validator or lets a failed object
  into the accepted program.
- Stop before a completion fills anything not derivable from records.
- Stop before experiments inherit production flags by default.


## Completion

- Completed: 2026-09-02
- Evidence: GeometryProposalPolicy gains four opt-in production flags (defaults = experiment policy, bit-for-bit on the existing producer tests): complete_bookkeeping fills missing revision preconditions (prior object digest) and unacknowledged binding/input/frame changes from records, recompiles without a model call and records geometry-proposal-completion-NN; progress_budget continues past the bounded budget only while the issue set strictly shrinks (hard cap 32) and stops on stall; partial_acceptance compiles the reduced proposal when every remaining compiler issue is object-scoped and deferrable (prior objects restored, new objects and dependents removed) and records geometry-proposal-deferral; escalate_on_stall records geometry-proposal-escalation with the issue list and last proposal; resume_geometry_program_proposal continues from the escalation's last round through rejected_round_ref without re-authoring the spatial option. Hard validators, single-writer promotion and no-silent-fallback untouched; production_compiler passes the policy through unchanged. 9 tests in test_production_policy; full suite green apart from the known Windows flakes; ARCHITECTURE PASS (297 files).
