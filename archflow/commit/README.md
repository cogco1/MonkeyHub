# Commit

Owns the sole write boundary for canonical state. The committer consumes an
eligible Candidate Submission and its read-only decision package, verifies the
expected baseline, and atomically promotes the next state while emitting an
external receipt/history record.

It does not generate designs or alter evaluator output. See the transition in
[`docs/DYNAMIC_MAP.md`](../../docs/DYNAMIC_MAP.md).

The P1 `Committer` and locked in-memory compare-and-swap store remain the
walking-skeleton write seam. The durable project repository owns persistent
canonical promotion. P018's event protocol and reducer add reconstruction
evidence but no second writer: they can prove what state follows an accepted
decision package, while actual promotion still requires the existing
single-writer compare-and-swap boundary. Stale, mismatched, invalid, or
conflicting candidates leave canonical state unchanged.

M004 closes the production approval seam with
`PromotionDecisionPackage@1`. The package binds one P024 assembly and executed
submission to the same frozen plan, P025 approval policy and unexpired approval
receipt, P005 hard-validation receipt, completion-boundary commitment-monitor
receipt, and detached aesthetic observations. A package can be handed to
`commit_decision_package` only when all bindings agree and no hard or
commitment failure remains.

Human-required and previously authorized disposable-sandbox policies use the
same exact package contract; the latter does not introduce a forced human
pause. Approval and aesthetic preference still have no waiver or canonical
write authority. The old three-argument `commit` method is retained only for
the explicit P1 compatibility state carrying a `GoalContract`; production
canonical state fails closed unless it supplies the full package. Both paths
share the same private compare-and-swap primitive, so no second writer is
created.
