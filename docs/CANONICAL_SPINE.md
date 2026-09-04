# One spine

**Status:** specification, decided 2026-09-03 with Kaiwen. Execution starts when the sessions
still landing work on `main` (Studio lane, codex) have finished; nothing moves before that.

The repository grew five production spines, each with its own vocabulary for the same ideas
(state, components, geometry emission, relations, levels, validation results, stage exit,
obligations, evidence, provider invocation). The survey of 2026-09-03 counted 13 validation
result vocabularies, 5 relation types with 4 kind enumerations, 3 emitters of
`GeometryOperation@1`, 3 CAD translations, 2 coordinate frames, 2 builders of one state type
with opposite semantics, 2 write paths and 9 runtime loops with no caller. From here on there
is one spine. Everything else is folded into it, archived, or deleted.

## 1. The spine that stays

The **record-driven** chain, the newest and the one the product (Studio) and the paper's
replay experiments run on:

```
StateRecord@1  (archflow/state/state_record.py)
  → developed_design_view / design_components_of / project_levels_of / project_grids_of
  → element_producers (archflow/capabilities/element_producers.py) with reference_resolver
  → GeometryProgramProposal (archflow/state/geometry_program.py)
  → compile_geometry_program (archflow/compilers/geometry.py)
  → cad_program → cad_execution → cad_patch → three_dm_inspector (archflow/adapters/)
  → relation_checks (archflow/capabilities/relation_checks.py)
  → stage_workflow + StageExecutionGuard + StageExitBinding (archflow/state/stage_workflow.py, runtime/project_runner.py)
  → P036 repository (archflow/project/repository.py), one HEAD, compare-and-swap
```

Entry points: `tools/run_project.py`, `tools/verify_state_record.py`,
`tools/freeze_project_stage_workflow.py`, `apps/archflow-studio/api` (and `web/`).
Shared foundations: `archflow/project/refs.py`, `archflow/contracts/{canonical,fields}.py`,
`archflow/validation/{model,engine}.py` (`validate_submission`, `Finding`,
`ValidationReceipt`), `archflow/ports/model.py` (model invocation: one request, one receipt,
signed by whoever crossed the boundary), `archflow/state/model.py` (`CanonicalState`, `Commitment`),
`archflow/state/operational_state.py` (`DesignObligation`, `DependencyEdge`).

Retained data under `probes/` and the external workspace is never moved: it is data. Readers
of archived lanes' data move with their lane and stay runnable there.

## 2. Verdicts

Four verdicts. **KEEP** = the owner on the spine. **FOLD** = the spine lacks this behaviour or
carries it twice; the parallel is merged into the owner and then deleted. **ARCHIVE** = an
early-test project lane, moved whole (tools, lane-only kernel modules, its tests, its facades)
under `archive/<lane>/`, runnable from there, never imported by the spine. **DELETE** = dead
code with no lane, no probe and no spine consumer; Git keeps it.

### 2.1 Lanes

| Lane | Entry points today | Verdict | Consequence |
|---|---|---|---|
| Record-driven (P089/P102/P103/P108) | `tools/run_project.py`, Studio API | KEEP | the spine |
| Agent-driven portfolio (P053) | `python -m archflow.runtime run-project` → `production_runtime`, `production_compiler`, `design_development`, `design_portfolio`, `semantic_spatial_authoring` | ARCHIVE as `archive/portfolio/` | live-model authoring re-enters through the Studio intent path (typed intent → record → runner); `provider_runtime`, `responsibility` and `adapters/model_provider` followed the lane on 2026-09-03, having no spine caller |
| Pantheon (P058/P064/P065/P069) | `tools/run_pantheon_reconstruction.py`, `pantheon_relation_control.py`, `build_pantheon_progress_snapshot.py`, `tools/projects/pantheon`, `monument_common` | ARCHIVE as `archive/monuments/` | P069 and P066 close as superseded; monuments come back only as State Records (P105/P106 are the door) |
| Parthenon | `tools/run_parthenon_*.py`, `parthenon_stage4_*.py`, `refine_parthenon_stage4_visual_regions.py`, `run_parthenon_stage4_visual_rag.py`, `state_tree_viewer.py` | ARCHIVE as `archive/monuments/` | untyped op dicts, direct rhino3dm and the Z-up frame leave the tree with it |
| Design controller (P042–P060) | `archflow/runtime/design_controller.py` and its loops (`hierarchical_search`, `architectural_revision`, `repair_loop`, `staged_build`, `player_control`, `operational_transition`, `walking_skeleton`, `primary_architect`, `development_controller`), `commit/` in-memory store and committer, `workspace/manager.py`, `event_log.py`, `skills/`, `capabilities/experts.py`, `adapters/cli_retrieval.py`, `interaction/` | ARCHIVE as `archive/controller/` | no production caller today; the second write path goes with it |
| Sandbox / voxel / Minecraft (P026/P027/P030) | `realization/`, `adapters/{minecraft_mcp,fake_voxel,sandbox_render,site_observation}.py`, `runtime/{world_recovery,environment_feedback,terrain_adaptation}.py`, `validation/{usability,use_scenarios,spatial}.py` | ARCHIVE as `archive/sandbox/` | probes p026/p027/p030 stay as data |
| Experiments and research (P062/P063, web precedent, basis index) | `evaluation/`, `research/`, `tools/run_experiment.py`, `run_assignment.py`, `run_decision_research.py`, `run_web_precedent.py`, `run_basis_*.py`, `tools/projects/web_precedent` | ARCHIVE as `archive/research/` unless P094 names a reader it needs on the spine before execution | the paper reads retained records; the readers run from the archive |
| V3 legacy diagnostic (P013) | `adapters/v3_legacy_cli.py`, `capabilities/v3_diagnostic.py` | ARCHIVE as `archive/v3/` | probes p013 stay as data |

Lane membership is decided mechanically at execution time: a module belongs to the spine when
it is in the transitive import closure of the spine entry points, computed on direct imports
after step 0 below removes re-export-only package `__init__` imports (today
`archflow/runtime/__init__.py` alone imports every loop). Everything the closure does not reach
is assigned to the lane whose entry points reach it; what nothing reaches is DELETE.

### 2.2 Concepts

| Concept | Owner on the spine | FOLD (merge, then delete the parallel) | Leaves with a lane or is deleted |
|---|---|---|---|
| Identity | `project/refs.py`, `contracts/canonical.py` | the four Ref types get `to_dict`/`from_dict` once; the 20+16+12+11 module-local copies and `contracts/branch.py` serde go | |
| Field parsing | `contracts/fields.py` | 86 module-local `_text/_mapping/_list/…` copies go | |
| Persistence and commit | `project/repository.py` (P036) | | `commit/store.py`, `commit/committer.py`, `workspace/manager.py` (controller lane); artifact side-channel writes (sandbox lane) |
| HEAD schema | `CanonicalProjectState@1` | P110 makes `canonical_state_from_dict` accept it; `CanonicalState@1` as a HEAD shape goes | |
| Design state | `StateRecord@1` + `developed_design_view` | `developed_design_view` emits components, dependencies and obligations itself; the `SchematicPack`/`SpatialOptionProposal` fabrication in `project_runner.py` and the `state → runtime` import inversion go; `initialize_developed_design` goes with the portfolio lane | `OperationalMarkovState`, `DesignStateTree`, `design_maturity` phase gates (controller lane); P109 decides what of `decision_operator` the record needs |
| Components | `Component@1` → `design_components_of` → `DesignComponent` | component library / templates / stair solver fold into element producers per the consolidation plan | `DevelopedComponent@2` (portfolio lane), `ComponentFamilyInstance` (research lane), `BuildingAssemblyTemplate`, `component_catalog` (DELETE) |
| Geometry emission | `element_producers.py` | P105 gives the producers the monument vocabulary (revolve, boolean chain, arrays as one op kind) | pantheon hand-builders and parthenon op dicts (monuments lane); `GeometryProducer`/`ProducedAssembly`, `portico_geometry` (DELETE) |
| Program and compiler | `state/geometry_program.py`, `compilers/geometry.py` | `runtime/geometry_compiler.py` shim goes | |
| CAD | `cad_program`, `cad_execution`, `cad_patch`, `three_dm_inspector`; one frame (Y-up building-local) | P107 adds the in-process OCCT executor as the second *executor* of the one program, not a second translation | `realization/sandbox.py` and `sandbox_render` (sandbox lane), direct rhino3dm writers (monuments lane), `three_dm_materialization` (DELETE), `ifc_export` (research lane until a spine consumer appears) |
| Relations | `Relation` in the record, `ArchitecturalRelationKind`, `capabilities/relation_checks.py` | one checker per kind the record can declare (today only `support` is checked; `project_runner.py` drops every other kind before checking) | `ArchitecturalRelation` graph, `relations/{authoring,realization,traversal,coverage,adapters}`, `control/relation_*`, `RelationshipRequirement`, `ProgramRelationshipKind`, parthenon vocabulary (monuments/controller lanes); `capabilities/relation_authoring.py` (DELETE) |
| Levels, grids, datums, references | `ProjectLevels`, `ProjectGrids`, `InterfaceDatum`, `reference_resolver`, `DerivationTable` | one datum namespace (level ids and element-top ids no longer merged by `dict.update`); `Level@1` only, `MassingLevel@1` goes with the massing view; stair datums resolve through `ReferenceContext` | `SpatialLevel` (monuments lane) |
| Dependencies and impact | `DependencyEdge` + `StateRecord.closure` | one closure honouring `DependencyEffect`; the `component_library` and `repair_experiment` walkers go; `DevelopmentDependency.impact` goes with the portfolio lane | `relations/adapters.compile_dependency_edges` (DELETE) |
| Validation result | `Finding` / `ValidationReceipt` (`validation/model.py`, `engine.py`); `RelationCheckReport` is a plain domain value that renders as findings | | `CheckReceiptEnvelope` and its 27 users, `StageClosureFinding`, `StageRequirementProfile`, architectural usability, spatial, component lineage, cad readback, stage completeness, commitment monitor (their lanes); `usability`, `use_scenarios`, `architectural_invariants`, `validation/program`, `vaulted_passage`, `interface_continuity` (with the lanes or DELETE) |
| Stage and exit | `ProjectStageWorkflow`, `StageRunEnvelope`, `StageExitBinding`, `StageExecutionGuard` | the runner writes the closure record from its own relation checks; `CompositeStageClosureReceipt` stays as that record's shape and loses its compile pipeline | `StageConvergenceReceipt`, `control/{baseline,convergence,subjects,profile,requirements,check_requirements,stage_artifacts,semantic_capabilities,genesis_completeness}`, `stage_control_chain`, `DesignPhase` gates (monuments/controller lanes) |
| Obligations and commitments | `DesignObligation`; `Commitment` (for `validate_submission`) | the nine role-specific obligation classes either become `DesignObligation` or leave with their lane; `CommitmentRevisionProposal` vs `CommitmentRevision` become one | legacy `Obligation` in `state/model.py` (with `walking_skeleton`) |
| Branch | `BranchRef` | envelopes and exit bindings carry a `BranchRef`, not `branch_id`+`epoch` pairs | `DesignBranch` (portfolio lane), research branch (research lane) |
| Evidence and claims | none on the spine today; a record's `evidence_refs` are strings | | `evidence/`, `research/`, both `EvidenceClaimBinding`s, visual inventory (monuments/research lanes) |
| Provider invocation | `ModelInvocationRequest` / `ModelInvocationReceipt` (`ports/model.py`) | the runner's inline `RecordedProposalProvider` stays for replay; a live provider is called by whoever authored the request and signs one receipt for it (the Studio's intent compiler, `ModelPhase.INTENT_COMPILATION`) | the P053 envelope, authority token and lifecycle (`production/`), the subprocess adapter, retrieval, skills, experts, v3 (their lanes) |
| Runtime loop | `project_runner.run_project` | | every other loop (controller/portfolio lanes) |

### 2.3 Cards affected

P066 and P069 close as superseded by this spec (their retained records stay readable from
`archive/monuments/`). P105 and P106 become the only way a monument re-enters: as a State
Record replayed through the spine. P094 reads retained records; it names, before execution,
any reader it needs kept on the spine. P104, P107, P109, P110 are spine work and stand.

## 3. The archive

`archive/` at the repository root, created at execution. The lanes share ninety-odd kernel
modules (`control/`, `relations/`, `evidence/`, `validation/`), so the archive is one mirror of
the tree, not one directory per lane. Rules:

1. Moved with `git mv`; history is preserved. A moved module keeps its relative path:
   `archflow/control/baseline.py` becomes `archive/archflow/control/baseline.py`, a tool
   becomes `archive/tools/<name>.py`. Imports among moved modules are rewritten
   `archflow.…` → `archive.archflow.…`; imports of spine modules stay `archflow.…`. The spine
   never imports `archive` (an `archcheck` rule enforces it).
2. `archive/README.md` lists the lanes: what each was, its entry points, the probes it reads,
   the tag at which it last ran green on `main` (`pre-spine`), and the command that runs it
   from the archive.
3. A lane's tests move to `archive/tests/` and leave the main suite; they run on demand.
4. Nothing under `archive/` is a work-card target. A lane comes back only through a card that
   lands it on the spine, as a fold.

## 4. The module registry

`governance/module_registry.json` — separate from `work_registry.json`, which stays a list of
live work items. One entry per concept:

```json
{"concept": "design state",
 "owner": "archflow/state/state_record.py",
 "symbols": ["StateRecord", "developed_design_view", "design_components_of"],
 "stage": "record",
 "replaced": ["OperationalMarkovState", "DesignStateTree", "initialize_developed_design"]}
```

`archcheck` reads it and fails when a symbol listed as owned is defined outside its owner, when
a module outside the owner defines a function whose normalised body equals an owner's function,
or when anything under `archflow/`, `tools/`, `apps/` or `tests/` imports `archive`. The spine
suite is held to the same boundary as the spine: a test that needs an archived lane's code is
that lane's test and lives in `archive/tests/`, which may import the spine and never the other
way round (`tests/test_spine_suite_is_the_spines.py` proves it from inside the suite). The
registry is the map; `docs/ARCHITECTURE.md` is rewritten to describe the one spine and nothing
else.

## 5. Order of execution

0. Wait for the sessions landing on `main` to finish; tag `pre-spine` at that commit.
1. Remove re-export-only package `__init__` imports so reachability is computable; compute
   the spine closure and the lane assignment; review the move list with Kaiwen.
2. Create `archive/` and move the lanes, tests included; rewrite imports; run the spine suite
   and each lane's suite from the archive once.
3. Delete the DELETE list.
4. Land the folds in §2.2 one concept per commit, each with the parallel deleted in the same
   commit.
5. Write `governance/module_registry.json`, add the three `archcheck` rules, rewrite
   `docs/ARCHITECTURE.md`, close and open the cards in §2.3.

Every step commits with explicit paths; nothing is pushed without Kaiwen's word.
