# GH-223

Issue: https://github.com/cogco1/MonkeyHub/issues/223
Base: `a512018a` (batch F).

One read-only representation-status projection replaces the four stale readers, an evidence document closes the experiment, and decisions can carry a project recipe binding (correction capture, option A, memory side).

Batch G (2026-09-25), planned in the owner's order; plans are kept outside the repo.

## Lane `representation-status` (223-S1, 223-S2)

- Landed: `representation_status` in `application/representation_dependencies.py` (`studio.binding`). The Worktree Graph's drawing rows and Publish's source statuses read it; Drawing's `plan_status` and Render's `_freshness` stay the readers it asks. Evidence and the answers to the Issue: `docs/2026-09-26-representation-state.md`.
- Open: `docs/DRAWING_V0_AUDIT.md` §6 still says no project-level dependency exists (a GH-66 path). The Blender variant and the #217 eye anchor stay with #218.
