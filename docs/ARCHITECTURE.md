# ArchFlow V4 architecture

This document separates the implemented spine from the next architectural capability
to develop. [VISION.md](VISION.md) defines the direction; [DYNAMIC_MAP.md](DYNAMIC_MAP.md)
tracks live work. The goal is usable architectural work that people can understand,
revise and hand over, not more editable objects or more infrastructure layers.

One production spine. Who owns what is in [SYSTEM_MAP.md](SYSTEM_MAP.md) (generated from
`governance/module_registry.json`); why it is one spine is in
[CANONICAL_SPINE.md](CANONICAL_SPINE.md); the decisions a later session would be tempted to
reverse are in [adr/](adr/README.md). Live work is in [DYNAMIC_MAP.md](DYNAMIC_MAP.md). What the
Studio serves on the wire, and what a second client or a remote server may rely on, is
[PROTOCOL.md](PROTOCOL.md) — the open ArchFlow protocol, version 2.

## The spine

This is an implementation map, not an approval checklist for every edit. Studio
candidate work uses the existing harness and does not issue a project version.
Local improvements can be tried without waiting for the formal-issue workflow.

```text
StateRecord@1                     archflow/state/state_record.py      the design, content-addressed
  │ developed_design_view          the bound projection (run, base) — binding identity
  ▼
element producers                 archflow/capabilities/element_producers.py
  │ reference_resolver             levels, grids, datums, derivations resolve here
  ▼
GeometryProgramProposal           archflow/state/geometry_program.py
  │ compile_geometry_program       archflow/compilers/geometry.py — the one compiler
  ▼
CAD                               archflow/adapters/cad_program.py → cad_execution.py → cad_patch.py
  │ three_dm_inspector             readback is evidence, never intent
  ▼
relation checks                   archflow/capabilities/relation_checks.py — plain domain values
  ▼
stage workflow                    archflow/state/stage_workflow.py + StageExecutionGuard (project_runner)
  ▼
P036 repository                   archflow/project/repository.py — content-addressed records; one published design
  │ issue                          archflow/project/issue.py — compare-and-swap from a satisfied closure (ADR-007)
  ▼
the published design              the one issue a project stands at; `HEAD` is the file's name and nothing else uses the word
```

Entry points: `tools/run_project.py` (a run of one project), `tools/verify_state_record.py`
(replay equivalence), `tools/freeze_project_stage_workflow.py`, `tools/issue_project.py`, and the Studio
(`apps/archflow-studio`: FastAPI `api/` as a thin shell over the kernel, `web/` as the client).

Shared foundations: `archflow/project/refs.py` (the four references), `archflow/contracts/`
(canonical JSON, digests, field parsing), `archflow/validation/{model,engine}.py`
(`validate_submission`, `Finding`, `ValidationReceipt`), `archflow/ports/model.py` (the one
model invocation request and receipt; whoever crosses the boundary signs one).

## Architectural revision: responsibilities and actual gaps

Component membership, architectural relationships and change propagation answer
different questions: what belongs together, how the parts work together, and what
this edit affects. They use the existing record, not three competing project stores.

| Responsibility | Existing owner | What remains to be demonstrated or extended |
| --- | --- | --- |
| Understand an architectural request | Studio `application/intent.py`, the intent compiler and target/action resolvers | Ordinary edits compile to one numeric target. A scope names affected parts; it does not yet coordinate an assembly-wide revision. |
| Express the project and change it | `state/state_record.py`: entities, parameters, relations, obligations and exact-base operators | Its four edit kinds serve scalar, massing, program and reindex consumers. A new architectural action needs only the specific missing operation, not a general patch language or a second state model. |
| Resolve dependencies | `StateRecord.dependency_edges/closure`, `capabilities/reference_resolver.py` and producer ordering | Traversing declared edges cannot discover a missing architectural dependency or decide which endpoint should govern a revision. |
| Produce an assembly | `capabilities/element_producers.py`, with existing wall/opening solvers and geometry compiler | The straight-stair producer emits steps and a top datum. Whole-flight support, endpoint constraints and coordination with a passage/landing require a project-backed example; they are not proved by that datum. |
| Check the result | `capabilities/relation_checks.py`, CAD readback and `validation/engine.py` | Declared relations are checked, but omitted requirements can remain unseen. Support-height agreement is not contact-area or structural-capacity analysis. P110 addresses the separate missing requirement input in Studio validation. |
| Continue, inspect and retain | Studio binding/candidate/viewer, `runtime/project_runner.py`, P036 | Explicit candidate continuation is implemented and handed back for trial. Program-sheet and massing-option generation still use the default base; continuation does not prove architectural correctness. |

These are existing ownership boundaries, not new modules to create. Public APIs and
callers remain in [SYSTEM_MAP.md](SYSTEM_MAP.md). Source/runtime placement and extension
steps remain in the [work-environment guide](WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md).

The next capability is to revise a building assembly under its actual project
conditions. An agent should identify the relevant parts and propose a suitable
method; existing domain algorithms should solve the stated conditions and produce
geometry; checks should measure the result against the task; the architect should
decide genuine trade-offs. This is a development direction, not the present promise
of the scalar intent compiler. Retrieve evidence when a method or applicable
condition is uncertain, and connect the usable conclusion to the affected condition;
an evidence-reference string alone is not an enforced requirement.

For a stair joining a fixed entrance to the ground, its endpoints govern the flight.
Publishing `base + count * rise` and moving the landing with it can be correct in
another design, but is not a universal dependency direction. A changed design may
require revising relationships as well as values. Keep fixed conditions fixed unless
the architect changes that decision; ask about a real conflict, not an internal id.

## Development order

1. **One stair revision.** Use the existing project's stair, landing, side supports
   and underpass. Establish the exact starting candidate, source-backed geometry and
   fixed/mutable conditions before implementing. Exercise width, endpoint-height
   and footprint changes, then continue from the saved result. Extend the existing
   operator/producer/check owners only where this task exposes a gap. P111's delivered
   continuation is reused; P108 owns the real Studio trial and P110 owns requirement
   projection. Neither is a blanket authorization for an assembly solver.
2. **A second assembly only after that works.** A wall-opening-frame-glass revision
   can test whether the same project, action and checking mechanisms transfer.
   Reuse shared behavior and add only the opening-specific method; do not generate
   another whole building to avoid examining the first failure.
3. **Consolidate demonstrated reuse and bottlenecks.** Extract shared code when real
   consumers need it and remove the superseded production path together. P105
   follows missing geometry vocabulary. The delivered OCCT export supports the bounded
   solid/loft/boolean slice described in the [Studio README](../apps/archflow-studio/README.md);
   whole-stair/window production and further operation coverage remain live work.
   P104 and P106 retired unbuilt with the
   monument lane on 2026-09-05. Research under P094 proceeds separately.

The [benchmark proposal](RESEARCH_POSITIONING.md) evaluates task completion,
relationship errors, repair and handover cost. Separate executing supplied rules,
recovering relationships in a supported case, and deriving a project-specific method.
Stage completion states the maturity and unresolved work of the project; it does not
substitute for these observations or turn a Studio candidate into an issued version.
Search-policy/OCBA work remains deferred for the algorithm team; do not restore the
archived controller or build a new one as a prerequisite for this revision.

Document roles stay small: VISION explains the purpose, this file explains the
implemented shape and development order, the module registry owns contracts, the
work registry tracks live assignments, and RESEARCH_POSITIONING defines comparisons.
An idea here becomes implementation work only with a concrete task and write scope.

## Rules that hold the shape

- **Two identities.** `StateRecord.digest` is content; `state_digest` is binding (ADR-003).
- **Values, not receipts.** A deterministic in-process transformation returns a plain value; a
  receipt exists only where a write, an external call, an irreversible commit or an explicit
  acceptance crossed a boundary (AGENTS.md).
- **One owner per capability.** The registry names it; `archcheck` fails a second definition,
  a copied helper body, a missing public symbol or a missing test.
- **The spine never imports `archive/`.** Archived lanes run from the archive and return only as
  folds (CANONICAL_SPINE.md §3).
- **Layer imports and write sites** are fenced by `governance/architecture_policy.json`.

## What is not on the spine

`archive/` holds the agent-portfolio lane, the Pantheon and Parthenon tools, the design
controller and its loops, the sandbox/voxel lane, the research and experiment lane and the v3
diagnostic, each runnable from there with its own tests. Retained data under `probes/` and the
external workspace never moved.
