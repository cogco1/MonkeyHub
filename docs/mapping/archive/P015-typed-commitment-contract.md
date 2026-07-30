# P015 — Typed commitment contract and authority

- Origin: Planning
- Status: Done
- Depends on: P019

## Goal

Make commitments first-class canonical objects so that past decisions can
remain normatively active without reinjecting the full transcript into the
operational Markov state.

Commitments are not obligations:

- a commitment says what must remain true, become true, or may be traded;
- a finding records observed mismatch;
- an obligation records the work or proof needed to resolve that mismatch.

## Write scope

- `archflow/state/commitments.py`
- `archflow/state/model.py`
- `archflow/state/__init__.py`
- `archflow/submission/model.py`
- `archflow/commit/committer.py`
- `archflow/runtime/fake_architect.py`
- `tests/test_commitment_contract.py`
- `docs/mapping/`

## Contract

Each commitment has a stable identity and bounded typed fields for:

- kind: `achievement` or `maintenance`;
- strength: `hard`, `negotiable`, `preference`, or `hypothesis`;
- authority and source event/evidence;
- scoped building/object references;
- activation and satisfaction criteria references;
- revision policy and permitted authority;
- lifecycle status;
- dependency and successor references;
- optional monitor-state reference.

Criteria are declarative references to named validators or monitors, never
arbitrary executable strings.

## Acceptance

- Lifecycle distinguishes proposed, accepted, active, satisfied, violated,
  released, revised, and superseded states.
- Only authorized accepted/active commitments can acquire hard-gate authority.
- A preference or hypothesis cannot silently become a hard constraint.
- Candidate submissions may propose commitments but cannot self-authorize or
  activate them.
- Active commitments survive unrelated candidate commits.
- Release, revision, and supersession preserve predecessor/source identity.
- Canonical state stores bounded commitment objects, not raw prompt history.
- Existing facts, obligations, artifacts, and commit regressions remain valid.

## Tests

- Immutable lifecycle and serialization round trip.
- Authority and revision-policy enforcement.
- Achievement versus maintenance distinction.
- Preference/hypothesis cannot gate.
- Unrelated commit preserves active commitments.

## Stop conditions

- Stop if commitment criteria require evaluating arbitrary code.
- Stop if obligations and commitments collapse into one status field.
- Stop if adding commitments requires storing the full event log in state.


## Completion

- Completed: 2026-07-25
- Evidence: Commitment@1 now models achievement/maintenance, hard/negotiable/preference/hypothesis strength, authority, declarative criteria, lifecycle, revision policy, dependencies, predecessor/successor, monitor reference, and bounded deterministic serialization.
- Evidence: Candidate submissions may add only proposed commitments; accepted/active hard authority requires a recognized authorizer, and preferences/hypotheses cannot gate.
- Evidence: Active commitments and BuildingProgram survive unrelated commit; 74 full tests, compileall, legacy-string scan, and P015 scope check pass.
