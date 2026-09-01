# P079 — Evidence sufficiency and decision-universe closure

- Origin: Planning (branch-conditioned RAG follow-through)
- Status: Done
- Depends on: P076, P077, P078

## Goal

Prevent a branch from reporting research complete merely because every decision
currently named by the branch has one adopted fact. Compile a project-authored
decision universe, required dependency edges, evidence-diversity and uncertainty
policies, conflicts, and research saturation into one typed fail-closed receipt.
Newly discovered decisions are a successor-universe proposal bound to the exact
predecessor scope; they never widen an existing branch scope silently.

## Acceptance

- Every mandatory decision is present and meets its project-authored adopted-fact,
  independent-source-family, and uncertainty policy before completion.
- Every required dependency edge is resolved or retained as a typed open edge;
  discovering an unknown decision emits an exact predecessor-bound universe
  expansion proposal instead of querying outside the active scope.
- Material conflicts require an explicit authorised resolution; unresolved
  conflicts return `human_review_required`.
- Research saturation is explicit and bounded. A receipt distinguishes continue,
  successor-universe expansion, human review, and complete without importing any
  historical or modern building vocabulary into the framework.
- Synthetic historical-colonnade and modern-learning-pavilion cases prove that
  facts alone cannot close a missing relation, conflict, or dependency decision.

## Stop conditions

- Stop if the mechanism silently widens a P078 branch scope.
- Stop if one fact per named decision can still imply sufficient research.
- Stop if framework code contains Pantheon, classical, Gothic, pavilion, or other
  project-specific ontology.

## Tests

Evidence policies, source diversity, typed uncertainty, conflict routing,
dependency closure, predecessor-bound universe expansion, saturation, and
historical/modern synthetic fixtures; architecture firewall.


## Completion

- Completed: 2026-08-28
- Evidence: Modality-neutral decision-universe closure and evidence-sufficiency compiler now separates fact presence from adequate research. It binds exact predecessor universe/scope expansion, required edges, source-family diversity, qualifiers, typed uncertainty, policy-authorised conflict review, bounded no-novelty saturation, and explicit continue/universe-expansion/human-review/complete frontier states. Text, visual region, drawing/sketch, measurement, and derivation evidence remain typed candidates; historical and modern synthetic cases plus 12 focused tests pass; architecture firewall passes.
