# ADR-004 — The historical authority block stays where retained digests bind it

**Decision (2026-09-03):** every `"*_authority": false` literal stays in the serialised payloads of
existing record kinds; the read-side re-checks were deleted. The full removal was tried and discarded:
retained records (probes p062, p027, p061, workspace runs) bind digests computed over the block
(BuildPolicy@1 ↔ DesignProgram, CandidateExecutablePlan@2 ↔ design_state_digest, terrain receipts ↔
plan_digest).

**Do not:** remove the block from a digested payload, re-add reader checks, or add the block to a new
record kind. Removing it needs a re-digest of the retained corpus, which is a separate decision.
