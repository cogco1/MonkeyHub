# Architecture review: six tables → one State Record (2026-09-02)

Reviewer role: architecture referee + migration designer. No code changed.
Every claim below was checked against the working tree at `20cdc7b` plus
codex's uncommitted stage-workflow edits (`archflow/state/stage_workflow.py`,
`archflow/control/stage_closure.py`, `archflow/runtime/project_runner.py`).

## 1. Verdict

**Modify.** Reject "six peer tables"; adopt the user's shape — one
**State Record** whose entities are typed schemas (level, grid, type,
element, assembly, space, reading), with parameters, relations,
obligations, evidence, provenance and stage/authority as sibling sections,
and geometry programs / validation results / receipts / SQLite as derived.
The decisive finding: the kernel **already has this record**
(`OperationalMarkovState`: facts, bindings, locks, commitments,
obligations, dependencies, invalidated_refs, evidence_refs; plus
`ArchitecturalRelationGraph` with propagation rules and epistemic status;
plus codex's `StageRunEnvelope` / `StageExitBinding`). What the villa script
and all production runs did was bypass it with a second object model
(`DevelopedDesignState` → `GeometryProgramProposal`). The migration is not
"invent tables"; it is "feed geometry production from the state record the
control kernel already defines, and retire the parallel model".

## 2. What the current architecture actually is

Two object models exist side by side:

**Control kernel (state-driven, unused by production):**
`OperationalMarkovState` (facts with `FactEpistemicStatus`
OBSERVED/DECLARED/DERIVED/HYPOTHESIS/DISPUTED/UNKNOWN, `ParameterBinding`
key/value/source_ref, `StateLock` target/authority, `Commitment`,
`DesignObligation` with `validator_ref`, `condition`, `blocked_by`,
status OPEN/BLOCKED/SATISFIED/WAIVED, `DependencyEdge` with effect
INVALIDATES/REQUIRES_REVALIDATION/BLOCKS/SUPPORTS_ONLY, `invalidated_refs`);
`compile_decision_operator` (exact-base check, `StateDelta`, closure over
INVALIDATES + REQUIRES_REVALIDATION edges, obligation readiness iterated to
a fixed point, `ClosureReceipt`); `ArchitecturalRelation` (25 kinds incl.
SUPPORT, HOST, HOSTS_VOID, FILLS_VOID, ADJACENT, INTERFACE, ALIGNMENT,
CLEARANCE, LINEAGE, REFINES, REPLACES; participants by role;
`RelationPropagationRule` trigger_role → affected_role →
UNCHANGED/REVALIDATE/INVALIDATE; epistemic status; `source_stage_id`;
`predecessor_relation_ref`); `DesignMaturityState` /
`PhaseGateReceipt` / `StageEntryProof`; codex's `ProjectStageWorkflow`
(stages with required_roles, required_checks, close_obligation_id),
`StageRunEnvelope` (subject_ref, state_digest, predecessor exit binding),
`StageExitBinding` + `CompositeStageClosureReceipt` (OPEN/SATISFIED,
mismatch codes).

**Geometry production (what actually ran):** `DevelopedDesignState`
(`SelectedSchematicInput` + `DevelopedComponent` + `DevelopmentDependency`
with impact RECHECK/INVALIDATE + `DevelopmentInvalidationReceipt`) →
`GeometryProgramProposal` (operations, semantic bindings, `HostedAssembly`,
`ObjectRevisionPrecondition` / `ObjectRetirement` with expected digests,
`responds_to_*`) → `compile_geometry_program` (object digest = operation
dict + frame digest + binding digests + input object digests; predecessor
and STALE / UNACKNOWLEDGED_* checks; datum resolution) → P097 producer loop
→ Rhino export with read-back → receipts. Producers: `GeometryProducer`
protocol (`ProducerContext`, `ProducedAssembly`, `GeometryAssemblyBuilder`,
`GeometryProductionBundle` = operations + semantic bindings) implemented
only by `portico_geometry.py` and used only by its tests; the runner's
producers (wall, prism, ring, loft, column-array, dome-cap) are not on that
protocol; `wall_solver` / `opening_solver` / `stair_solver` are separate.

**Per project:** the villa monolith (data + program), the run-017 seat
scripts, the Rocca derivation scripts, the runner packs.

Capability audit:

| Capability | Current implementation | Proposed destination | Migration difficulty | Hidden coupling | Risk of semantic loss |
| --- | --- | --- | --- | --- | --- |
| Project runner | `project_runner.py`: packs → fixture-free state → seat rounds → producer → gates → CAD; codex binding it to stage envelopes | Runner reads the State Record; entities resolve by reference | Medium | Seat scope is computed from `SpatialOptionProposal.components`; `owned_subtree` assumes the schematic tree | Low if the tree becomes entities with parent refs |
| State model | Two models: `OperationalMarkovState` (control) vs `DevelopedDesignState` (production) | One State Record = operational state + entity schemas | High: the producer validates `design_state_digest` and seat scope against `DevelopedDesignState` | `produce_geometry_program_proposal`, `project_seat_context`, `compile_handover`, `invalidate_developed_design` all take `DevelopedDesignState` | Medium: `DevelopmentDependency.impact` and `DependencyEdge.effect` are two vocabularies that must be reconciled once |
| Geometry compiler | Deterministic; digests; revisions/retirements; datum resolution | Unchanged | None | Object digest includes the full operation dict → any producer refactor changes digests | None for geometry; provenance continuity needs a `REPLACES` lineage record when digests change |
| Producer / builder | `GeometryProducer` protocol (unused in production); runner mini-producers; solvers | One registry on the protocol; specs built from entities + parameters | Medium | `portico_geometry` writes absolute Y (no datum bindings); runner producers bind `base_level` | Low if producers gain datum bindings |
| Revision / retirement | `ObjectRevisionPrecondition` / `ObjectRetirement` with expected digests; P097 fills bookkeeping from records | Driven by closure: the runner emits revisions/retirements for the affected object set | Low | Retirements must be sorted; reason_refs required | None |
| Relation manifest | Villa: 40 tuples in a script; kernel: `ArchitecturalRelation` with propagation rules; P096 template relations | Relations section of the State Record (kernel type) | Low for data, High for use: nothing in production consumes propagation rules | `relation_authoring` / `relation_coverage` capabilities are provider-facing, not runner-facing | High if relations are copied without their propagation rules and epistemic status |
| Stage checks | Villa: 967 lines of bounding-box checks keyed by relation check ids; kernel: `RequiredCheck` kinds (P096), `stage_closure` (codex), coverage/evidence packs | Validator bindings on relations → five generic check kinds run by the runner; stage closure consumes their receipts | Medium | Check ids in the villa are strings matched by hand; tolerances are literals (`3 * tolerance.linear`) | Medium: project-specific rulings (e.g. "≤25 mm embed counts as contact") must become obligations, not lost |
| Provenance / evidence / receipt | Content-addressed records, `no_authority` flags, basis_refs, receipts per stage | Unchanged; the State Record cites the same refs | None | Receipts cite program digests; a receipt that reuses an execution must match digests (defect found today in `portico-001`) | None if every derived artifact keeps its `predecessor`/`basis` refs |

## 3. What the proposed architecture improves

1. Design state becomes data the agent edits as rows, not control flow it
   must re-read (the villa's 309 literal-bearing lines and `add_*` functions
   disappear; the agent's per-edit reading drops from thousands of lines to
   the affected rows).
2. Edits cost the closure, not the project: datum bindings (P098),
   instance→edition edges (P099) and relation propagation rules already give
   the edges; the villa facade revisions measured it (A: 15 recomputed / 4
   retained in structure, envelope 25 retained; B: 4 retired / 7 retained /
   8 recomputed; detail and envelope untouched).
3. Producers consume typed specs only — the portico producer already does;
   the runner producers do; the villa `add_portico` (reads `BODY_HALF`,
   `PORTICO_DEPTH`, `SERVICE_TOP` globals) is exactly the anti-pattern.
4. Relations defined once serve generation (datum roles → bindings),
   propagation (`RelationPropagationRule`) and validation (validator binding)
   — the kernel types exist; production never used them.
5. Geometry stays deterministic through the existing compiler and Rhino
   back end; nothing in the proposal touches materialization.
6. Provenance survives because records stay content-addressed and every
   derived artifact cites its basis; the only new obligation is a lineage
   record whenever an object digest changes for non-design reasons.

## 4. What it breaks or oversimplifies (the attack on six tables)

**B1 Type/Element re-creates the family/instance problem** — yes, if types
carry geometry recipes and instances only numbers. Revit's family editor is
a hidden programming language. Mitigation already in the kernel: a type is
a `ComponentTemplate` (parameter bands + obligations + `mathematics_ref`);
the recipe lives in a shared producer; the instance carries placement by
reference (P099 forbids a project coordinate in a type). Residual risk:
type explosion (a type per window size) — solved by parameters on the
instance, not new types.

**B2 Assembly must be first-class** — yes. Element rows cannot say "these
twelve elements are one portico placed four times with roles that carry
relations among themselves". The kernel has both levels: `HostedAssembly`
(geometry: host, socket, members by role) and `BuildingAssemblyTemplate`
(roles, cardinalities, relations, required datums, checks — P096). The
State Record needs an `assembly` entity schema binding roles to element
entities; producers already return `ProducedAssembly`.

**B3 RelationTable sufficiency** — SUPPORTS, HOSTS, CONNECTS (ADJACENT /
INTERFACE / ACCESS), MEETS (INTERFACE with a datum role), ALIGNS_WITH
(ALIGNMENT), CLEAR_OF (CLEARANCE) are covered by the 25 kinds.
CREATES_OBLIGATION, RESOLVES_OBLIGATION and INVALIDATES are **not
relations between entities**: they are edges between decisions/relations
and obligations, and between changes and nodes. The kernel already keeps
them apart: `DesignObligation.source_ref` (what created it),
`blocked_by`, `condition`, `validator_ref`; `DependencyEdge.effect
INVALIDATES`. The user's objection is right: `SUPPORTED_BY(column,
foundation)` is a present-tense fact with an epistemic status;
"a column grid was chosen, therefore a continuous load path must be
completed" is an OPEN obligation created by that decision. Keep them as
two records linked by refs.

**B4 One relation for generation, invalidation and validation?** — Keep
one *relation* but three *bindings on it*: a datum role (generation: the
producer binds to it), propagation rules (invalidation: already a field),
and a validator binding (validation: check kind + tolerance + datum role).
Obligations stay separate (B3). This is the "relation triple", and it is
what P096 `RequiredDatum` / `AssemblyRelation` / `RequiredCheck` already
sketch at template level.

**B5 Derived quantities** — derived **nodes in the state graph**, not a
side table. A `DerivationTable` is the right *definition* record
(expressions with basis refs), but each evaluated value must be a fact
(`FactEpistemicStatus.DERIVED`, `source_ref` = the expression record) with
REQUIRES_REVALIDATION edges from its inputs; otherwise piano_nobile +400 mm
cannot reach `stair_riser = service_top / stair_risers`. The evaluator is
tiny; the graph placement is what matters.

**B6/B7 Stage inheritance and identity** — six flat tables would compress
four stages into one snapshot. Stages must stay as **versions of the State
Record** bound by codex's `StageRunEnvelope` → `StageExitBinding` →
`StageRunPredecessor`, with entity identity stable across stages
(`entity_id` never changes; `DesignComponent.revision` and
`ComponentMaturity` already model this) and lineage relations
(LINEAGE / REFINES / REPLACES, `source_stage_id`,
`predecessor_relation_ref` exist on relations; add the same two fields to
entities). Geometry identity: `CompiledGeometryObject.object_id` stable,
`object_digest` changes; `ObjectRevisionPrecondition.expected_digest` is the
proof the editor saw the current state.

**B8 Parameter lifecycle** — all eight fields exist across three records
and should be unified on one `Parameter` schema: `introduced_at`
(decision/stage ref), `inherited_from` (predecessor parameter ref),
`revised_at` (decision ref), `locked` (`StateLock` target + authority),
`stale` (`DesignMaturityState.revalidation_required_refs` /
`invalidated_refs` with REQUIRES_REVALIDATION), `invalid` (INVALIDATES),
plus `epistemic_status` and `source_ref` already on `StateFact`.

**B9 Project-specific acceptance vs generic relations** — generic: kind +
check kind + tolerance on the relation. Project-specific: stage
`required_checks` and `close_obligation` (codex's `ProjectStage`), human
rulings as records (`human-derived-stack-ruling`, "≤25 mm embed counts as
contact" from the 2026-09-01 audit), template `applicability`. Never encode
a ruling as relation semantics; encode it as an obligation or a waiver with
authority.

**B10 SQLite** — index only. Authoritative = content-addressed records
under the canonical chain and the stage exit bindings. The index is
rebuilt from records and may be deleted (the state-tree viewer already
lives by that rule).

## 5. Recommended canonical data model (field sketch)

```json
StateRecord@1 {
  "record": {"project_id", "run_id", "branch", "base", "stage": {"workflow_ref", "envelope_ref", "stage_id", "predecessor_exit_binding_ref"},
             "authority": {"decision_ref", "actor_id", "locks": ["lock:<target> -> authority_id"]}},
  "entities": [
    {"entity_id": "level-piano-nobile", "schema": "Level@1", "role": "piano-nobile", "elevation": "@piano_nobile", "basis_refs": [..], "introduced_at", "inherited_from", "revised_at"},
    {"entity_id": "axis-2", "schema": "GridAxis@1", "origin": "@axis_x(2)", "direction": [0,0,1], ...},
    {"entity_id": "villa-principal-window", "schema": "Type@1", "template_ref": "...", "producer": "opening:window", "parameters": {...}},
    {"entity_id": "wall-west", "schema": "Element@1", "component_id": "exterior-walls", "producer": "wall", "type_ref": "villa-exterior-wall",
     "placement": {"line": {"from": {"grid": ["axis-1","axis-A"]}, "to": {"grid": ["axis-1","axis-F"]}}, "face": "exterior"},
     "base_level": "level-ground", "top_level": "level-eaves", "hosts": ["window-left", "door", ...]},
    {"entity_id": "portico-west", "schema": "Assembly@1", "template_ref": "...palladian-hexastyle-portico", "roles": {"columns": "columns-west", "entablature": "entablature-west", ...},
     "placement": {"facade": {"grid": "axis-1"}, "outward": "-x", "base_level": "level-piano-nobile"}}
  ],
  "parameters": [
    {"key": "column_height", "expr": "9 * column_diameter", "value": 6.426, "unit": "m", "epistemic_status": "derived", "inputs": ["column_diameter"],
     "source_ref": "rule:ionic-nine-diameters", "introduced_at": "decision:...", "inherited_from": null, "revised_at": null, "lock": null}
  ],
  "relations": [
    {"relation_id": "columns-support-entablature-west", "kind": "support", "participants": [{"role": "support", "node_ref": "columns-west"}, {"role": "load", "node_ref": "entablature-west"}],
     "datum_role": "column-top", "propagation_rules": [{"trigger_role": "support", "affected_role": "load", "effect": "invalidate"}],
     "validator": {"check_kind": "support_contact", "tolerance": "@contact_tol"}, "epistemic_status": "declared", "source_stage_id": "stage-2", "predecessor_relation_ref": null}
  ],
  "obligations": [
    {"obligation_id": "west-architrave-span", "statement": "...", "status": "open", "source_ref": "decision:tetrastyle-respacing", "subject_refs": ["entablature-west"],
     "validator_ref": "capability:archflow.structure.span_check", "condition": null, "blocked_by": []}
  ],
  "evidence": [{"reading_id": "plate-p273-hall-diameter", "value": 30, "unit": "piede", "confidence": "legible", "source": "..."}],
  "provenance": {"basis_refs": [...], "predecessor_state_ref": "...", "invalidated_refs": [...], "revalidation_required_refs": [...]}
}
Derived (never authoritative): GeometryProgram per seat, ValidationResults (check receipts, closure receipts), Receipts (stage, run, export/readback), SQLite index.
```

Levels, grids, types, elements and assemblies are schemas of one entity
list, so a new building kind (a basilica, a tower) adds schemas and
producers without touching the record.

## 6. Three change simulations

### Scenario 1 — piano_nobile elevation +400 mm

- **Authoritative diff:** one entity field (`level-piano-nobile.elevation`
  3.570 → 3.970) or, if derived, one parameter; a decision record with
  authority.
- **Closure:** everything with `base_level` / `sill_level` /
  `top_level` = piano-nobile or a derived parameter of it: 4 porticos ×
  (6 columns, 6 abaci, 3 entablature pieces, 2 roofs, 1 abutment, 4 courses,
  1 pediment) via column-top → abacus-top → entablature-top chain; the four
  wall bands' openings above ground (sill offsets ride on the datum); the
  loggia/landing datums; the exterior stairs (rise = piano_nobile − grade →
  derived `stair_riser` → riser-count band obligation); interior floor
  system; drum/dome untouched only if the main-cornice level is not derived
  from piano-nobile (in the villa it is measured, so preserved; in Rocca it
  is derived `podium + order + entab`, so the cornice, roof, drum, dome all
  move — correct).
- **Stale:** all of the above (REQUIRES_REVALIDATION). **Invalid:** the
  four stair assemblies if the riser band fails (villa: 3.97/23 = 0.1726,
  inside 0.119–0.1785 → stale, not invalid; tread band rechecked).
  **Preserved:** ground-floor walls' solids (base ground) — but their cut
  results change because openings moved: retained by digest for the uncut
  solid, recomputed for the cut; hall, drum, dome, roof (villa).
- **Producers rerun:** column-array ×4, entablature/pediment/roof
  producers ×4, wall ×4 (all openings re-lifted), stair ×4, floor system.
  **Not rerun:** hall ring, drum, dome, roof.
- **Validators rerun:** every relation whose participants are stale (~30
  of 40); alignment `landings-meet-piano` first.
- **Obligations reopened:** stair riser band (per stair), any "meets
  landing" obligations, the P097 span obligation untouched.
- **Geometry ops:** revised ≈ 4×23 + 4×(60−1) + stairs; retired 0; created
  0. Op ids stable; digests change; revisions carry expected digests.
- **Provenance:** decision record → new State Record version citing
  predecessor; programs cite the state digest; receipts per seat.
- **Human commit boundary:** the decision (one line) and the acceptance of
  the revised stage; the runner runs unattended between them.
- **Proportional?** Yes for compute: closure ≈ 45 % of entities, 0 % of
  the hall/dome. **Not proportional for Rhino export** as long as export is
  per seat program (whole program re-exported): four porticos × three seats
  = 12 exports ≈ 6 min serial. Fix later: export only objects whose digest
  changed and read back by name (the exporter already compares named
  bounds).

### Scenario 2 — portico column count 6 → 8

- **Authoritative diff:** assembly parameter `columns: 6 → 8` on four
  assembly entities (or one type parameter if all porticos share the type);
  axis spacing either kept (portico widens → wall opening widens → relation
  with the wall band) or re-derived to fit the loggia width
  (`axis_spacing = (loggia_width − d) / 7`).
- **Closure:** column entities (indices 0–7), abaci, entablature span,
  pediment width, roof; the wall band's loggia void if width changes; grid
  axes 1–6 → 1–8 (grid entities are inputs, so the change is *on* the grid,
  not derived from columns — the honest diff is "grid axes: 8 at new
  spacing"); the stair width if bound to the colonnade.
- **Stale/invalid/preserved:** columns 0–5 revised (position), 6–7
  created; abaci likewise; entablature front revised (span), returns
  preserved if the loggia width is unchanged; pediment revised; courses
  revised (length); walls preserved unless the void widens; hall/drum/dome
  preserved.
- **Producers rerun:** column-array, entablature, pediment/roof, (wall).
- **Validators rerun:** columns-support-capitals / capitals-support-
  entablature (contact ×8), corridor–column clearance, alignment to grid.
- **Obligations reopened:** intercolumniation vs order rule (a
  `RequiresRevalidation` on the Ionic proportion band); the architrave span
  obligation if spacing grows (P097 B recorded exactly this: 2.678 m span
  OPEN).
- **Geometry ops:** revised 6 columns + 6 abaci + front + 4 courses +
  pediment; created 2 columns + 2 abaci; retired 0 (if 8 → 4 as in revision
  B: 4 retired, 7 retained, 8 recomputed — measured).
- **Provenance:** decision record; grid entities revised with
  `revised_at`; assembly binding (P096) re-validated (cardinality band
  6..6 in the current template → binding fails typed → template edition or
  waiver needed — this is the two-vote rule doing its job).
- **Human commit boundary:** the decision plus, if the template's count
  band is exceeded, a template edition or a waiver record.

### Scenario 3 — remove a facade window introduced at stage 2, detailed at stage 4

- **Authoritative diff:** the opening entity `window-left` (stage-2
  `introduced_at`) gets `retired_at` = decision ref; its host wall's
  `hosts` list loses one ref; the stage-4 detail entities that refine it
  (frame members, glazing, shutter) carry `inherited_from` → window-left,
  so they are found by lineage, not by search.
- **Closure:** relations HOSTS_VOID(wall, window), FILLS_VOID(window,
  frame), MEETS(sill datum) → the wall's cut op (one fewer tool), the
  aperture op, frame ×4, glazing, shutter, their assembly; the facade
  symmetry alignment relation (window-left ↔ window-right) → the mirrored
  window becomes **stale with an OPEN obligation** ("facade symmetry
  broken: rule or retire the twin"), not silently accepted.
- **Stale:** wall-west cut, twin window (symmetry); **Invalid:** none;
  **Preserved:** every other side, the wall solid, all other openings.
- **Producers rerun:** wall (west) once; opening producer not rerun for
  the removed window (retired), rerun for nothing else.
- **Validators rerun:** aperture_exists for the remaining west openings,
  symmetry/alignment for the facade.
- **Obligations:** stage-4 detail obligations attached to window-left
  close as moot (status WAIVED with reason = retirement ref, never
  SATISFIED); a new OPEN obligation on the symmetry relation.
- **Geometry ops:** retired: aperture, tool, 4 frames, glazing, shutter
  (each `ObjectRetirement` with `expected_digest` + reason_refs); revised:
  wall-west-cut (inputs changed → UNACKNOWLEDGED_DEPENDENCY_CHANGE unless
  acknowledged via `responds_to_object_ids`); created: none.
- **Provenance:** decision record; stage-2 State Record version N+1
  (lineage REPLACES for the wall entity); stage-3/4 records marked partially
  invalid by closure receipt — not re-run wholesale; the stage-4 exit
  binding for the west facade must be re-closed (codex's stage closure
  would report the changed subject digest → MISMATCH until re-closed).
- **Human commit boundary:** two — the retirement decision at stage 2, and
  the re-closure acceptance of stage 4 for the west facade after the
  symmetry obligation is ruled.
- **Proportional?** Yes: one wall producer, seven retirements, one
  relation reopened; Scenario 3 is the cheapest of the three in compute and
  the most expensive in ruling (the symmetry obligation needs a human).

## 7. Performance impact

Cost breakdown today (measured where noted):

| Cost | Today | After migration |
| --- | --- | --- |
| Agent reasoning | Dominant: 6–47 min per card in this session; a facade revision 8 min; the agent re-derives intent from scripts | 1–3 min per edit: it edits rows; residual = reading evidence |
| Source inspection | Reads 500–3,700-line scripts per change | Reads the affected rows (SQLite query) — disappears |
| State mutation | ms | ms |
| Dependency resolution | Not run in production; P063 closure exists (BFS, bounded) | ms; closure sizes of 10–60 nodes |
| Producer execution | ms (wall solver, column array) | ms |
| Geometry compilation | ≈ 50–100 ms per program (1,700 tests / 180 s) | same |
| Rhino execution | 20–40 s per program (PowerShell + Rhino COM start + export + readback); runner: 37.9 s and 40.5 s per seat incl. export; 12 seat programs ≈ 6 min serial | Unchanged per program; proportional only when export becomes per changed object (not in this proposal) |
| Validation | bounds checks ms; RhinoCommon contact probes seconds | ms + seconds |
| Readback | rhino3dm parse 0.5 s (Studio decode 577 ms for 493 meshes) | same |
| Persistence / receipts | ms per record | same |

Realistic targets (based on this repository, not aspiration):

- **first full compile** (villa, all seats, no Rhino): producers + compile
  + checks < 10 s; with Rhino export of 12 programs ≈ 5–7 min serial, 2 min
  if programs are merged per stage. The evidence-to-state authoring stays
  human hours.
- **local parameter edit preview** (bounds + checks, no Rhino): < 3 s.
- **committed local rebuild** (affected seat programs exported and read
  back): 40–120 s.
- **full audit** (all relation checks, all programs read back, closure
  receipts): 3–6 min.

What the migration does not remove: Rhino's per-program cost and the human
rulings. What it removes: the agent reading code, and the whole-project
re-understanding — which was the hour.

## 8. Migration plan (each step keeps the old path and proves equivalence)

Step 0 — put the runner's producers on `GeometryProducer` (`ProducerContext`
+ `ProducedAssembly`) and add datum bindings to `portico_geometry`.
Invariant: runner receipts for Rocca and the villa west band unchanged
(bounds identical, binding sets identical, op digests identical — pure
refactor).

Step 1 — **PorticoAssembly**: assembly entity + type parameters → registered
portico producer; villa west portico first. Invariants: semantic bindings
identical (component → object id sets); geometry bounds identical to
run-016 within 1 mm (RhinoCommon); operation digests **will change**
(lofted columns from run-015 shapes vs producer sections) → record a
REPLACES lineage relation per object and a written reason; readback
invariant (80/80 contacts); relation results identical (30 checks); stage
inheritance preserved by lineage refs.

Step 2 — **Stair**: stair solver rows → bridge producer (treads, landing);
villa 23 risers. Invariants: riser/tread within template band; landing
meets piano-nobile datum; bounds vs run-016 stair objects within 1 mm;
Rocca's UNSAT stays UNSAT.

Step 3 — **Wall / Opening**: already element-based for the west band
(0.0 m); extend to four sides by placement rows; apertures exist per
HOSTS_VOID relation. Invariants: member bounds identical, aperture count =
opening count minus declined, attic b/c refused by the roof exclusion.

Step 4 — **Grid / Column system**: levels/grids as entities (P098 done),
column arrays placed by axis; `axes_match_grid` invariant; column-top datum
published once per portico.

Then: derived parameters as facts with edges; relations with validator
bindings replacing `stage_checks`; codex's stage envelopes wrapping each
runner stage. Every step produces an equivalence receipt against run-016
before the old path is retired.

## 9. Do-not-do list

- Do not rewrite the kernel; the state record exists.
- Do not put geometry recipes in types (no family editor); recipes live
  in shared producers.
- Do not make SQLite authoritative.
- Do not merge relations and obligations; link them.
- Do not flatten stages into one snapshot; version the record and bind
  stages with envelopes and exit bindings.
- Do not let a producer read a project global; specs and context only.
- Do not re-export a whole model to preview an edit; preview from bounds
  and checks, export on commit.
- Do not skip the human commit boundary on retirements and template
  cardinality changes.
- Do not touch `project_runner.py`, `stage_workflow.py`, `stage_closure.py`
  until codex's stage-envelope work lands.
- Do not accept a receipt that reuses an execution without a matching
  program digest (today's defect).

## 10. Paper implications

- **Does this make ArchFlow a state-driven runtime instead of
  project-specific authoring?** Yes, and honestly only after Steps 0–4:
  today the *kernel* is state-driven and the *production* was script-driven;
  the paper can claim the runtime once one building is derived from a State
  Record with receipts and no project Python. The conservative claim in
  POSITION.md ("a stateful, provenance-aware architectural modeling runtime
  for agents") is exactly what Steps 0–4 deliver.
- **Engineering contribution:** the producer registry on a typed protocol,
  reference placement (grid/level/host → coordinates), relation-bound
  validation replacing per-project checks, the record-driven runner with
  stage envelopes, and the equivalence instrument (run-016 replay).
- **What supports the design-state / dependency / obligation claims:**
  the measured closures (facade revisions A/B; Scenario 1–3 once run on the
  villa), obligations separated from relations (the user's SUPPORTED_BY vs
  load-path example is the paragraph), lineage across stages, and the
  refusal cases (attic windows behind the roof; Rocca stair UNSAT) as
  evidence that relations participate in generation rather than being
  checked afterwards.
