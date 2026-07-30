# P018 — Append-only event log and canonical state rebuild

- Origin: Planning
- Status: Completed
- Depends on: P017, P020

## Goal

Keep justification history separate from the operational Markov state and
prove that a versioned deterministic reducer can reconstruct commitment-aware
canonical state from append-only events.

This card proves state reconstruction, not external-world or byte-exact replay.

## Write scope

- `archflow/runtime/event_log.py`
- `archflow/runtime/state_reducer.py`
- `archflow/runtime/README.md`
- `archflow/commit/`
- `tests/test_event_state_rebuild.py`
- `docs/mapping/`

## Event boundary

Events record actor/authority, prior state reference, proposed delta, evidence
references, validation/commit receipts, reducer version, and resulting state
digest. External MCP traffic remains in bounded referenced artifacts.

Commitment changes use append-only revision, release, supersession, or
compensation events; prior accepted events are never rewritten.

## Acceptance

- Event identities and hash linkage detect reordering, omission, or mutation.
- A versioned reducer rebuilds the exact canonical state and commitment monitor
  reference for one accepted lifecycle.
- State rebuild does not require raw model reasoning or tool transcript.
- Rejected candidates and failed external calls remain evidence but do not
  advance canonical state.
- Mid-run requirement additions and authorized commitment revisions retain
  distinct append-only events; reconstruction never rewrites the earlier brief.
- External side effects are represented by artifact and receipt references;
  no execution replay is claimed.
- `trace`/audit, state rebuild, and execution replay are named as separate
  capabilities.
- Unit reconstruction tests use disposable temporary directories. Any retained
  integration history is written through the project envelope under one
  probe's `canonical/`, `events/`, and `runs/` directories.

## Tests

- Append-only ordering and tamper detection.
- Commitment create/activate/violate/repair/revise lifecycle rebuild.
- Rejected event leaves canonical version unchanged.
- Requirement-addition and authorized-revision event reconstruction.
- Reducer-version and state-digest mismatch rejection.
- Archive reload without MCP/Rhino/Minecraft access.

## Completion evidence

- `DesignEvent@1` is an immutable, content-addressed event with sequence/hash
  linkage, exact prior/resulting state references, authority, evidence,
  artifact, validation, commit, and reducer references.
- `AppendOnlyEventLog` requires an injected store and has no default directory,
  repository fallback, or canonical writer.
- `CanonicalStateMutation@1` applies typed fact, commitment lifecycle,
  obligation, artifact, and evaluation changes. New commitments enter proposed;
  existing commitments change only through the existing authority-checked
  lifecycle.
- A seven-event lifecycle reconstructs requirement addition, acceptance,
  activation, violation, repair, and authorized revision with
  predecessor/successor lineage and the commitment monitor reference intact.
- Rejected candidate and external-preview-failure events reload as trace
  evidence while canonical version and digest remain unchanged.
- Event content, order, omission, exact base, reducer version, state content,
  and claimed resulting digest all fail closed when inconsistent.
- 43 related event, repository, commit-boundary, commitment-monitor, contract,
  and compiler tests pass.

## Stop conditions

- Stop if reconstruction depends on mutable external state.
- Stop if a previous event must be edited or deleted.
- Stop if event-log presence is used to claim execution replay.


## Completion

- Completed: 2026-07-26
- Evidence: DesignEvent@1 hash chain and injected append-only store; CanonicalStateMutation@1 versioned reducer; seven-event commitment lifecycle rebuild; rejected/external failure no canonical advance; tamper/exact-base/reducer/digest checks; 43 related tests, compileall, and scope check passed.
