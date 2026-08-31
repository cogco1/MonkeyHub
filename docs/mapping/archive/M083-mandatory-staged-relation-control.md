# M083 — Mandatory staged architectural relation control

- Origin: Modify
- Status: Done
- Depends on: P077, M082

## Goal

Make architectural relation completeness, exact physical realization, and
cross-stage inheritance mandatory shared stage-exit controls instead of
optional helpers that an individual runner can omit.

## Root-cause audit

The missed topology was created before CAD realization.  The prior shared
control path had four independent omissions:

- The stage subject inventory declared which semantic components existed, but
  did not mechanically derive the relation-question and mechanical-slot
  denominator among those exact subjects.
- The baseline source set treated topology, realization, and inheritance as
  optional caller inputs.  Composite closure could therefore prove every
  requirement in a sparse caller-authored profile while never asking about an
  omitted roof interface, stair bearing, opening clearance, support chain, or
  load path.
- Geometry and readback checks established object presence, bounds, and broad
  collisions, but did not require purpose-specific contact, host, access,
  support, or load-transfer witnesses for every declared architectural
  relation.
- A project runner could create and display proposal geometry without passing
  the durable controller's stage-exit proof.  A viewer could therefore expose
  an unaccepted candidate even though no canonical stage had closed.

The first omission is the semantic denominator defect.  The remaining three
allowed that omission to survive profile compilation, physical validation,
cross-stage inheritance, and presentation.

## Control boundary after M083

M083 does not invent a building-specific topology.  It derives the complete
set of questions from the exact stage subjects, requires Agent/RAG evidence or
an explicit not-applicable decision for each answer, and keeps unknown answers
open.  Spatial exits require promoted topology; developed exits additionally
require exact program/readback realization; coordinated exits additionally
bind the full set of predecessor topology sources actually credited by the
accepted P036 baseline coverage and freshly revalidate their successors.

The durable controller replays the checkpoint, stage-exit anchor, baseline
source set, baseline coverage, and exact promoted graph.  Hierarchical search
remains proposal-only and accepts current or inherited stage governance only
through the concrete read-only `ProjectControllerArchiveAdapter`: it reloads
the current profile, checks, closure, convergence, source set, inventory, and
coverage, then replays any predecessor checkpoint and event lineage.  Claimed
same-path refs or a caller-authored structural replay port cannot substitute
for retained P036 bytes.  Direct interfaces require independent narrow-phase
evidence for each endpoint pairing; an indirect path through a third object
cannot close them.

## Acceptance

- Spatial stage exits derive relation-question and mechanical slot coverage
  from the exact stage subject inventory; a sparse caller-authored profile
  cannot omit the relation denominator.
- Developed and coordinated stage exits additionally require exact relation
  realization against the current compiled program and CAD readback, with
  explicit object pairings or ordered paths and independent narrow-phase
  receipts.
- Contact interfaces, host interfaces, walking paths, support chains, and load
  paths have non-interchangeable typed purposes. A chain through a third object
  cannot satisfy a declared direct interface.
- Coordinated/detail stages mechanically cover every predecessor relation and
  freshly revalidate retained or refined relations against the current stage
  subject, program, and readback.
- Exact legacy stage-exit records remain read-only replayable, but cannot be
  used to author a new sparse stage closure under the current contract.
- The shared controller and governance replay paths enforce the same derived
  denominator. No building name, topology answer, dimension, or construction
  system is introduced by the framework.

## Tests

- Stage-baseline source derivation, missing topology/realization source, sparse
  profile, exact replay, and legacy read-only compatibility tests.
- Direct-interface versus indirect-chain, host/contact purpose, and exact
  pairing-denominator relation-realization tests.
- Cross-stage relation denominator, stale predecessor, missing/duplicate
  successor, and fresh current-stage revalidation tests.
- Design-controller and hierarchical-search stage-governance regressions.
- Architecture check, compileall, and diff check.

## Stop conditions

- Stop before deriving a project-specific relation or structural system in the
  framework.
- Stop before using geometry-operation input edges as architectural relations.
- Stop before treating an old-stage receipt as current-stage revalidation.
- Stop before changing canonical project state or promoting the Villa Rotonda
  candidate.


## Completion

- Completed: 2026-08-31
- Evidence: 140 targeted relation-control tests passed; compileall passed; ARCHITECTURE PASS (189 files); exact P036 byte-digest replay rejects forged same-path refs; full suite 1026 passed, 6 skipped, 40 known unrelated failures from missing promoted probes and stale oculus fixtures
