# GH-293

Issue: https://github.com/cogco1/MonkeyHub/issues/293
Base: `a512018a` (batch F).

The first Sync is never refused with WORKING_DRAFT_STALE because a candidate job started: the position revision excludes the execution ledger, and the local draft writer re-reads and retries once.

Batch G (2026-09-25), planned in the owner's order; plans are kept outside the repo.
