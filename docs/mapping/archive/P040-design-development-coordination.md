# P040 — Design-development coordination

- Origin: Planning
- Status: Done
- Depends on: P017, P023, P033

## Goal

Develop an Architect-selected schematic branch into a coordinated
design-development state. Structure/support, circulation, envelope/openings,
materials, constructability, and use requirements are resolved against the
selected topology without letting any expert redesign the project or skip the
schematic-selection boundary.

This is the V4 equivalent of 扩初. It is distinct from both schematic design
and final candidate/MCP assembly.

## Write scope

- `archflow/state/developed_design.py`
- `archflow/capabilities/design_development.py`
- `archflow/runtime/development_controller.py`
- `archflow/state/__init__.py`
- `archflow/capabilities/README.md`
- `archflow/runtime/README.md`
- `tests/test_design_development.py`
- `tests/integration/test_design_development_resume.py`
- `docs/mapping/`

## Acceptance

- Input is one explicitly selected, exact-base schematic branch with program,
  site, resource, topology, and lineage references.
- Phase-allowed experts inspect bounded slices and return detached advice or
  local proposals; the Primary Architect integrates them.
- Structural/support, circulation, envelope/opening, material, construction,
  and use-coordination obligations remain distinct and traceable.
- Conflicts produce obligations and Architect trade-off receipts, not hidden
  ranking or automatic consensus.
- A coordinated developed design records assumptions and unresolved items but
  contains no MCP execution authority.
- Schematic changes invalidate affected developed-design work and return the
  branch to the appropriate earlier phase.
- Two non-isomorphic cases prove there is no fixed component, palette,
  dimension, or expert-order answer.

## Tests

- Selected-schematic exact-base requirement.
- Flexible within-phase expert order.
- Cross-discipline conflict and obligation propagation.
- Schematic revision invalidates developed work.
- Resume from persisted developed-design state.
- No candidate or MCP authority.

## Current evidence

- `SelectedSchematicInput@1` copies the exact P033 portfolio, selection
  receipt, branch revision, option, run, and canonical base into P040 without
  granting selection or mutation authority.
- Initialization requires project-authored blocking obligations covering
  structure/support, circulation, envelope/openings, materials, construction,
  and use. These are distinct coordination domains, not a fixed invocation
  sequence or built-in component schedule.
- Expert snapshots contain only the selected topology identity, current
  component refs, unresolved obligation slices, bounded evidence pointers, and
  exact state digest. P039 metadata filters phase eligibility; the Architect
  chooses any discovered subset and order.
- `DetachedDevelopmentAdvice@1` has no mutation authority. Every selected claim
  receives an Architect-authored adopted, rejected, or deferred disposition.
  Conflicting values require either one adopted claim plus explicit rejected
  alternatives, or a new obligation referencing the complete conflict set.
- Only `ArchitectDevelopmentDecision@1` may add or revise developed components.
  Component revisions follow exact predecessors, cite adopted claims and
  requirements, and remain bound to the selected schematic.
- `DevelopmentDependency@1` makes schematic-to-developed impacts explicit.
  Unrelated changes cannot invalidate work; matched changes remove or reopen
  only their dependency closure and emit
  `DevelopmentInvalidationReceipt@1`. Preserved components are explicitly
  rebound to a later authorized P033 selection.
- A coordinated state may retain advisory unknowns, but its schema explicitly
  denies candidate creation, hard-usability, MCP execution, and canonical
  writing.
- `DevelopmentControllerArchive` stores monotonic, predecessor-bound immutable
  checkpoints through P036. A restart reloads the latest exact state and can
  continue with dependency-local invalidation without the chat transcript.
- Two cases with different expert order and component-graph cardinality prove
  that ordering and concrete component answers remain project results.

## Stop conditions

- Stop if design development starts before schematic selection.
- Stop if an expert can mutate the selected topology without Architect action.
- Stop if coordination completion is confused with hard candidate acceptance.


## Completion

- Completed: 2026-07-27
- Evidence: DevelopedDesignState@1 now accepts only an exact P033 selected schematic; current obligations drive phase-filtered read-only expert discovery while Architect-selected order is preserved. Detached claims require explicit adopt/reject/defer receipts, conflicts cannot become hidden consensus, project-authored components and cross-discipline dependencies compile only through Architect decisions, unrelated changes do not invalidate work, matched schematic revisions invalidate only their dependency closure, and preserved components/obligations are rebound to a newly selected exact revision. P036-backed monotonic checkpoints reload and continue without chat history. 253 tests passed with 1 external smoke skipped; architecture policy passed 81 production files.
