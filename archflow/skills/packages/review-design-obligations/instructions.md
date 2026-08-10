Inspect only the supplied detached context and its current open obligations.

1. Identify the smallest unresolved relationship or constraint that blocks the current phase.
2. Use only facts, commitments, interfaces, evidence pointers, and authority identifiers present in the detached context.
3. Return read-only advice when the evidence is insufficient for a state change.
4. Return a proposal only when it is bound to the supplied target-state digest, cites at least one current response ref, and uses an allowed authority identifier.
5. Leave hard validation, dependency closure, candidate assembly, promotion, persistence, and external-world mutation to the ArchFlow runtime.
