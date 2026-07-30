# P033 — Design option portfolio and lineage

- Origin: Planning
- Status: Done
- Depends on: P007, P022, P023

## Goal

Preserve a reloadable portfolio of alternative design branches, their lineage,
trade-offs, and explicit selection authority.

## Write scope

- `archflow/state/design_portfolio.py`
- `archflow/runtime/branch_portfolio.py`
- `archflow/state/__init__.py`
- `archflow/runtime/README.md`
- `tests/test_design_portfolio.py`
- `tests/integration/test_branch_portfolio_reload.py`
- `docs/mapping/`

## Acceptance

- Fork, revise, park, reject, select, and combine preserve exact branch lineage
  and evidence.
- Expert conflicts and the Architect's adopted/rejected advice rationale remain
  attached to the affected branch.
- Pareto observations compare branches but cannot select or delete one.
- Only an explicitly selected exact-base branch enters candidate assembly.

## Tests

- Three-branch lineage and reload.
- Park, reject, select, and combine lifecycle.
- Conflicting-expert rationale.
- No automatic aesthetic winner.

## Current evidence

- `DesignOptionPortfolio@1` converts the unranked P023 option set into
  project/run/base-bound branches without changing the embedded project
  answers or assigning rank.
- Origin, fork, revision, and combination revisions are immutable. Each names
  exact parent revision digests, requirement/commitment refs, derivation refs,
  evidence, author, trade-off rationale, and adopted/rejected/deferred expert
  advice where the Architect has made that disposition explicit.
- Fork and revision fail if a parent requirement disappears. Combination
  additionally unions every parent requirement and derivation source before a
  child branch can be admitted.
- Park, reject, select, and release-to-park are receipt-backed lifecycle
  transitions. Selection requires a project-supplied named authority, permits
  at most one selected branch, and cannot be inferred from registration order.
- `ParetoBranchObservation@1` remains read-only evidence bound to the exact
  historical revisions it compared. Later revisions preserve that observation
  without turning it into a winner, deletion, hard-gate, or commit decision.
- `SelectedSchematicBranch@1` requires the current portfolio digest, current
  selected revision digest, and latest authorized selection transition. Its
  serialized boundary explicitly denies design-development completion,
  hard-usability, candidate creation, and canonical-write authority.
- `BranchPortfolioArchive` routes immutable snapshots through the P036 project
  repository. Restart reloads the latest exact lineage; equal-depth divergent
  snapshots fail as ambiguous instead of selecting the first record.

## Stop conditions

- Stop if branch registration order becomes ranking.
- Stop if combining branches loses commitments or derivation provenance.


## Completion

- Completed: 2026-07-27
- Evidence: DesignOptionPortfolio@1 now preserves project/base-bound origin, fork, revision, and combine lineage with exact parent digests, requirements, provenance, expert dispositions, and Architect rationale; receipt-backed park/reject/select lifecycle requires named selection authority, detached Pareto observations cannot choose or delete, SelectedSchematicBranch@1 fails closed on stale portfolio/revision and carries no hard/candidate/canonical authority, and P036-backed restart reload rejects equal-depth ambiguity. 246 tests passed with 1 external smoke skipped; architecture policy passed 78 files.
