# P078 — Branch-conditioned candidate convergence and RAG

- Origin: Planning (user-authored State 01/02/03 convergence architecture)
- Status: Done
- Depends on: P033, P067, P070, P076

## Goal

Make the selected Candidate a real retrieval boundary. A project-supplied
scorecard either enters an explicit human-in-the-loop decision or, when an
authorised automatic policy passes its minimum score and margin, selects one
branch and parks the other candidates with retained evidence. Every later
query, source allowlist, decision universe, adoption, basis shard, prompt
context, and next-round work item must carry the exact selected branch scope.

## Design

- Selection has two explicit modes. Low-confidence automatic assessment
  returns `human_review_required` without changing the portfolio. An explicit
  human choice may override score order. Exact ties also require HITL; loaded
  automatic decisions replay winner, threshold, margin, and authority.
  Neither mode deletes alternatives.
- Every Candidate scorecard carries an exact-revision `BranchResearchProfile`.
  Selecting the Candidate simultaneously selects its decision universe,
  search/exclusion vocabulary, source allowlist, and profile evidence; the
  scope compiler has no free parameters that could substitute another branch.
- The selected Candidate is identified by project/run/canonical base, source
  operational branch, portfolio and portfolio digest, candidate branch id,
  exact revision digest, and content-bound P036 selection, source option-set,
  and source Markov-state records. The runtime reloads those records and the
  selected scorecard profile before every post-selection write; the source
  branch, decisions, vocabulary, allowlist, and evidence cannot be edited.
- Branch search terms are mandatory, pruned-branch terms are excluded, and a
  non-empty domain allowlist may be narrowed but never widened. Both requested
  and final redirected URLs are checked on typed retained snapshots.
- Research output echoes exact query and scope digests. Facts remain
  candidates until an explicit `PrecedentAdoption` is bound to that query.
- The branch basis index ignores foreign and legacy-unscoped records, joins
  exact query/adoption/snapshot identities by full P036 URI, rechecks quote
  spans, starts from the complete decision universe, and exposes uncovered
  decisions as the next retrieval wave.
- `BranchDecisionContext` is compiled from an exact persisted index and must
  match the current operational state and expected scope before semantic
  authoring. The architectural-revision production path reloads and re-derives
  that index from its retained branch records, requires it for a P078-selected
  predecessor, and derives the permitted Candidate identity from that
  predecessor rather than trusting the caller. Public semantic authoring has
  no persisted-branch injection parameter.
  `BranchRAGProgress` exposes complete/total counts, gaps, next queries, and
  continue/complete status for the explicit progress panel.
- `advance_feedback_wave()` is the durable loop: reindex retained adoptions,
  persist every uncovered decision's next query, persist progress, then wait
  for typed retrieval and explicit adoption before the next wave.
- Selection is stored at run scope; all post-selection research records use
  P036 `RUN_BRANCH`. `create_scope()` is the only production scope constructor:
  it reloads all P036 inputs and immediately persists the result. No direct
  filesystem index writer is introduced.

## Stop conditions

- Stop if a foreign branch, stale revision, legacy unscoped fact, widened
  source, or decision outside the active universe can enter context.
- Stop if automatic selection resolves a low-margin comparison or tie silently.
- Stop if a parked alternative or its pruning rationale is deleted.
- Stop if branch records bypass P036 or land in the shared run record area.

## Tests

Automatic and human selection, low-margin and tie pause, tampered selection
replay, stale/unauthorised rejection, branch vocabulary and requested/final URL
filtering, output digest echoes, exact snapshot-ref cross-branch isolation,
explicit adoption, uncovered-to-next-query convergence, current-state prompt
scope validation, source/profile replay attacks, missing synthetic refs,
sibling-Candidate production binding, P036 branch persistence and reload,
architecture firewall.


## Completion

- Completed: 2026-08-28
- Evidence: Branch-conditioned Candidate selection, P036 source/profile replay, exact query-snapshot-adoption indexing, durable feedback waves, and predecessor-bound semantic context verified; 78 focused tests and architecture firewall pass.
