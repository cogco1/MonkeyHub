# M097 — Enforce handover exclusion bounds

- Origin: Modify
- Status: Ready after P095
- Depends on: P095, M096

## Goal

P095 handovers carry the from-seat's exclusion bounds, but nothing
consumes them: the villa run-017 mouldings entered the entablature
block unchallenged. Add one pure check, `check_handover_exclusions`,
that takes a handover and the analytic bounds of a consuming seat's
compiled program and reports every object entering an exclusion bound
by more than the tolerance, unless the pair is declared as an
engagement (subject, host, reason). Wire it into the seat scripts'
verify phase and record the result; the producer-side refusal follows
once analytic bounds are reachable from the capabilities layer.

## Acceptance

- Entering an exclusion bound by more than the tolerance is reported
  with the depth; a declared engagement silences exactly its pair; a
  shared face (zero depth) is not a violation.
- The villa run-017 detail seat reports the four course-into-entablature
  pairs, and a declared engagement record silences them.
- Full unittest suite and the architecture firewall pass.

## Write scope

- `archflow/capabilities/discipline_seats.py`
- `tests/`
- `docs/mapping/`
- `governance/work_registry.json`

## Tests

- Exclusion depth, engagement silencing, zero-depth contact.
- Architecture firewall.

## Stop conditions

- Stop before the check gains acceptance authority.
- Stop before an engagement can be declared without a reason and basis.


## Completion

- Completed: 2026-09-01
- Evidence: check_handover_exclusions (discipline_seats): every consuming object entering a handover EXCLUSION_BOUNDS by more than the tolerance on all three axes is reported with its depth; a shared face is contact; DeclaredEngagement (subject, host, reason, basis) silences exactly its pair. Villa run-017 west detail seat: 14 analytic violations before (12 course-into-entablature pairs + 2 pediment-vs-roof), 12 silenced by declared engagements, the remaining 2 resolved against the RhinoCommon both-inside probe as axis-aligned-box false positives (records handover-exclusion-check-73315a3c, handover-exclusion-resolution-b5e50d92). 13 seat tests; ARCHITECTURE PASS.
