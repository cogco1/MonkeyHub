# ADR-013 — A person's working head belongs to their authenticated actor

**Decision (#619, 2026-10-03):** extend the existing working position rather than
creating another project or identity authority. This first slice supplies the
personal-line prerequisite for team sharing; it does not enable sharing.

- `design/working.json` remains owned by `archflow.project.repository` and the
  open Runtime remains its only writer. `ProjectWorkingDraft@2` retains the
  shared run catalogue and active execution ledger, with a separate current
  run, branch context and local recovery reference for each stable actor ID.
- Existing `ProjectWorkingDraft@1` is explicitly read as the configured project
  owner's position. Reading never migrates or rewrites bytes. The first explicit
  actor position write writes @2. `ownerActorId` is retained then; a later
  display-name or runtime setting change cannot reassign those positions.
- Authentication supplies actor identity. Payloads and arbitrary headers cannot
  choose another actor. Display names are optional presentation metadata from
  configured actors, not authority. An unauthenticated local action retains its
  existing boundary attribution; it is not proof that a named human acted.
- Actor-position CAS revisions include only that actor's position, not other
  people's moves. The scoped port inserts missing run rows but never overwrites
  shared rows or the active ledger. Naming and retention use the existing
  full-document CAS. Immutable result records remain shared.
- All normal source/status reads use the authenticated request context, including
  threadpool calls. Conditional response caches and ETags include the actor.
  Request context is reset on completion; background retention examines every
  actor's head and ancestry. Current local recovery for every actor is protected.
- Without an explicit personal head, a team actor starts at an accepted shared
  Stage, never an arbitrary newer unaccepted result. With no Stage, the response
  states that none is available. Ordinary local legacy fallback remains intact.
- The tree exposes named peer heads as read-only views; viewing one does not move
  any head. Continue still moves only the caller's position. Stage acceptance
  and release keep their existing permission and exact-base boundaries.

Do not expose the Hub or broaden `shared_project` routes to make this work.
Invites, key provisioning, automatic remote replication, offline reconciliation,
work-zone fencing and all-action attribution remain subsequent slices. Current
candidate transfer continues to exclude personal working positions; full project
archives retain them. Content-addressed union does not resolve mutable pointers.
