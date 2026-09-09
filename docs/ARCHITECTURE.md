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

## Parallel user workflows: MonkeyArch and MonkeyDiagram

**MonkeyArch** is the 3D modeling and spatial-revision workflow. **MonkeyDiagram**
is the parallel drawing and diagram workflow: existing plans, elevations, sections,
furniture and connection details, PDF/image markup, text and dimensions, model-derived
views, graphic composition, sheet layout and export. This includes the drawing work
already being developed; it is not a new name for one redraw button.

The distinction follows the editing task. Changing building geometry or spatial
relationships belongs to MonkeyArch. Authoring or revising a drawing, its graphical
content or its presentation belongs to MonkeyDiagram, including axonometric or
perspective views placed on a sheet. A proposed change drawn in 2D does not silently
change the 3D model; applying it to the model is an explicit handoff to MonkeyArch.

**ArchFlow** owns the shared project and contract foundation. The current `archflow/`
Python package also contains modeling and drawing domain code; its directory name is
not proof that every module is shared infrastructure. The long-term target separates
`archflow/`, `monkeyarch/` and `monkeydiagram/`, with two peer Web workspaces and a shared
application host. The dependency direction, current-to-target file map and staged
migration are defined in [REPO_LAYOUT.md](REPO_LAYOUT.md). Existing registered paths
remain authoritative until their actual consumers migrate; no empty package is needed.

The UI direction is two peer workspace entries. MonkeyDiagram can begin with an
existing document or diagram, or reference a specific model version from MonkeyArch.
Both workflows reuse ArchFlow project identity, source binding, execution and P036
storage. They do not require separate repositories, databases or geometry engines.
Existing module owners keep their contracts; product names do not rename module ids.

This is the agreed product boundary, not a claim that every listed drawing operation
is implemented. The current document canvas and markup are usable code; the model-axis
elevation owner and the project-specific drawing consumer also exist. Peer workspace
navigation and the explicit redraw-from-new-model action remain to be integrated.
See [the MonkeyDiagram plan](DRAWING_MODULE_ARCHITECTURE_PLAN.md) for that work.

## Four areas of architectural work

Contributors can locate a capability through four responsibilities. These group
existing owners; they are not new packages, four toolbar panels or mandatory stages.

| Area | Responsibility and result | Existing capability and remaining scope |
| --- | --- | --- |
| Read: sources and brief | Turn relevant material into project facts, requirements and attributable evidence. | ProgramSheet and Reading provide structured inputs; automatic brief/document extraction is not yet a complete service. |
| Make: design and modeling | Generate or revise massing, components and their relationships, returning an editable candidate. | Massing options, component edits, type/reference resolution and CAD production exist; a general brief-to-design generator does not. |
| Check: analysis and verification | Measure a selected design and return results, findings and assumptions. | Massing/envelope metrics, realized-relation checks and CAD readback exist; environmental simulation remains a separate capability to add. |
| Present: views and drawings | Derive inspectable or deliverable representations from a selected model. | Model export/download and viewport captures exist; plans, sections, elevations, detailed drawing sets and architectural rendering are not established production services. |

The agent and workbench combine these areas according to the request. An early-design
workflow may read a brief, make a massing and check sunlight; one contributor may develop
all three without making them one inseparable module. Drawing a detail that changes the
building returns to design/modeling; presenting an analysis does not change its results.

Shared project state, exact source binding, model invocation, job execution, artifact
storage, candidate continuation and formal issue serve all four areas. Development and
research tooling remain cross-cutting, not additional architectural workflow stages.
Detailed ownership stays in SYSTEM_MAP and the existing registries.

The current-and-planned capability inventory and item-by-item consolidation checklist
are indexed by [P115](mapping/planning/P115-capability-consolidation.md). Existing P cards
keep their own acceptance; delivered behavior is not reopened merely to fill the plan.

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
| Understand an architectural request | Studio `application/intent.py`, `application/intent_agent.py` and target/action resolvers | Scalar edits and typed component edits both reach the candidate path. Interpretation still crosses overlapping routing and target-resolution logic; source retrieval and inspection-driven repair are not yet an integrated agent loop. |
| Express the project and change it | `state/state_record.py`: entities, parameters, relations, obligations and exact-base operators | `EDIT_COMPONENTS` adds atomic entity, parameter and relation edits and explicit removals alongside existing scalar, massing, program and reindex consumers. Reuse these operations; a new architectural action needs only its specific missing capability, not a second state model. |
| Resolve dependencies | `StateRecord.dependency_edges/closure`, `capabilities/reference_resolver.py` and producer ordering | Traversing declared edges cannot discover a missing architectural dependency or decide which endpoint should govern a revision. |
| Produce an assembly | `capabilities/element_producers.py`, with existing wall/opening solvers and geometry compiler | Stair and window producers remain reusable. Wall authoring with an arched opening now produces a real candidate in the project's side-support task. Successful solids do not establish passage alignment: the observed obstruction at the existing side entrance still needs correction. |
| Check the result | `capabilities/relation_checks.py`, CAD readback and `validation/engine.py` | Declared relations are checked, but omitted requirements can remain unseen. Support-height agreement is not contact-area or structural-capacity analysis. P110 addresses the separate missing requirement input in Studio validation. |
| Continue, inspect and retain | Studio binding/candidate/viewer, `runtime/project_runner.py`, P036 | Explicit candidate continuation is implemented. Program and massing APIs now accept a selected source run; clients must pass it to continue that candidate, while omission preserves the default base. Continuation does not prove architectural correctness. |

These are existing ownership boundaries, not new modules to create. Public APIs and
callers remain in [SYSTEM_MAP.md](SYSTEM_MAP.md). Source/runtime placement and extension
steps remain in the [work-environment guide](WORK_ENVIRONMENT_AND_EXTENSION_GUIDE.md).

The next capability is a complete revision loop under actual project conditions:

```text
request + current candidate -> relevant evidence and modeling method
  -> existing modeling tools -> inspect geometry and views -> repair -> visual candidate
```

The agent owns interpretation, relevant source retrieval, method selection, tool use
and inspection-driven repair. Existing domain algorithms own calculations, reference
resolution, geometry and persistence. The architect decides genuine spatial and design
trade-offs. Retrieve evidence when a method or applicable condition is uncertain;
routine edits do not require a research stage. Connect usable evidence to the affected
condition; an evidence-reference string alone is not an enforced requirement.

For a clear request, produce a reversible candidate without a separate parameter or
text-proposal approval. Continue against the candidate being inspected, with its exact
base bound internally and return/compare available. Adapters should supply mechanically
derivable ids, units, references and unchanged fields. Feed correctable tool and field
errors back to the agent within a bounded attempt budget; report an unresolved tool
limitation as such, rather than asking the architect to repair a schema or repeat the
request. Precise values and diagnostics remain available on demand. Keep conditions,
geometry failures and formal-issue checks remain real; preview is not publication.

This is the target behavior, not a completed capability claim. The current flow still
separates proposal submission from candidate execution and exposes semantic-edit
validation failures without an integrated repair turn. Implement the loop through the
existing owners, removing superseded routing and approval steps in the same change;
do not add a parallel planner, project store or approval framework.

For a stair joining a fixed entrance to the ground, its endpoints govern the flight.
Publishing `base + count * rise` and moving the landing with it can be correct in
another design, but is not a universal dependency direction. A changed design may
require revising relationships as well as values. Keep fixed conditions fixed unless
the architect changes that decision; ask about a real conflict, not an internal id.

## Development order

1. **One stair revision.** Use the existing project's stair, landing, side supports
   and underpass. Use the exact starting candidate and relevant source views to identify
   fixed/mutable conditions internally. First correct the side-passage obstruction,
   show the candidate without parameter approval, then exercise a follow-up request
   against that visible result and verify the retained stair conditions and recovery.
   Exercise width, endpoint-height and footprint changes as this task requires. Extend the existing
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
   whole-stair/window production and synthetic candidate continuation are implemented.
   Explicitly uncapped polyline lofts preserve the drum and dome as open surfaces.
   The current short demo defers complex passage and ornament details so that a
   recognizable building candidate and a visible stair-width revision can be tried.
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
