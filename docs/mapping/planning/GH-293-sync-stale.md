# GH-293

Issue: https://github.com/cogco1/MonkeyHub/issues/293
Base: `a512018a` (batch F).

The first Sync is never refused with WORKING_DRAFT_STALE because a candidate job started: the position revision excludes the execution ledger, and the local draft writer re-reads and retries once.

Batch G (2026-09-25), planned in the owner's order; plans are kept outside the repo.

## Landed in the sync-stale lane

- **293-S1, the position revision (D-293-1).** `read_working_draft` and `compare_and_swap_working_draft` answer `_position_revision`, the hash of `design/working.json` without its `active` ledger (`archflow/project/repository.py`). A candidate starting (`protect_working_run`) or ending (`release_working_run`) no longer moves the revision that `PUT /api/working-draft`, `/save` and `/local` compare, so the autosave right after Sync lands. A position writer keeps the retained ledger rather than the one it read, and only protect and release pass `ledger=True`. The bytes and the `ProjectWorkingDraft@1` keys are unchanged; nothing migrates. PROTOCOL §4.1 says what the revision covers.
- **293-S2, the writer (D-293-2).** `createLocalDraftWriter` reads again and retries on `WORKING_DRAFT_STALE`, up to three attempts as `moveHead` does (`syncModelDraft.ts`). Its witness and `expectedSource` still guard the content, and every other refusal answers at once.
- The new API test reproduced the refusal before the fix: 409 `WORKING_DRAFT_STALE`, "The working draft changed before these local commands were saved." modelSync authored-only (`MONKEYARCH_AUTHORED_ONLY=1`) passed three of three on this lane; the local write right after the candidate POST answered 200 each time.

## Open

- **293-S3** belongs to the root session: `MONKEYARCH_AUTHORED_ONLY=undo` and `=continue` ten times each on main once batch F merges.
- A project without `design/working.json` reads a `null` revision, and the first ledger write creates the file, so a writer holding `null` is still refused once. The writer's retry covers it; the Sync path writes the file before its candidate starts.
- The goal above says "retries once"; the plan and the code allow up to three attempts (two retries), as `moveHead` and the session's selection do.
