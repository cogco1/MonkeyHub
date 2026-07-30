# Submission

Owns the Candidate Submission boundary between unconstrained working activity
and formal review. A candidate should identify its baseline canonical state,
proposed state or delta, claimed obligation coverage, and evidence references.

It does not validate, score, or commit the candidate. Those are separate
read-only and single-writer responsibilities documented in
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

Current status: P1 frozen `CandidateSubmission`, `CandidateDelta`, and `Claim`
contracts bind intent, workspace, exact base version, evidence, unresolved
items, and the proposed additions/discharges.

`repair.py` converts hard or selected-expert findings into immutable,
receipt-backed `RepairObligation` values. Stable IDs describe what the primary
Architect must reconsider; obligations contain no geometry mutation or
prescribed repair operation.
