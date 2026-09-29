# Construction voids and backend lowering (2026-09-28)

**Issue:** [#419](https://github.com/cogco1/MonkeyHub/issues/419), lane `GH-419/construction-voids`.
**Base:** `origin/main` `0efe3765`.
**Scope:** one reviewable slice for #419.
- Any element can remove its solid from another element through a `voids` relation. Neither element has to be classified first, and the removing element keeps its identity.
- A host keeps one delivered object id whether or not it has openings.
- The compiler asks for a design identity only on delivered geometry.
- The provider contract no longer prescribes booleans.
- OCCT realizes the same program either as a profile-with-holes extrusion or as a boolean cut, and records which one it used.
- The analytic bounds predictor stops refusing ordinary voids in hosts that are not boxes.

**Owner decisions (2026-09-28):**
- D-419-0: identity first, meaning later. Creating, cutting or delivering geometry may not require any field, flag, kind, role or owner that states what the geometry *is*. A role in construction is a relation; it can be added and removed without changing identity.
- D-419-1: a wall's delivered object is always `obj-<wall>`. Retained drawing references to the old `obj-<wall>-cut` resolve through one explicit rename rule.
- D-419-2: prism `rectangular_cutouts` keep their panel partition in this slice. New openings use `voids`, and migrating cutouts is [#437](https://github.com/cogco1/MonkeyHub/issues/437).
- D-419-3: the bounds predictor extends the box-corner rule to every vertex of any host. A void it still cannot bound fails with a clear error; kernel-measured certification is deferred.

**Follow-ups:**
- Construction scripts: [#428](https://github.com/cogco1/MonkeyHub/issues/428).
- The look-and-fix loop: #404 (F2, F3), #405 and #303.
- Cutouts: #437.

**Path abbreviations**

| Short | Path |
| --- | --- |
| `GP` | `archflow/state/geometry_program.py` |
| `SR` | `archflow/state/state_record.py` |
| `COMP` | `monkeyarch/compilers/geometry.py` |
| `PROP` | `monkeyarch/capabilities/geometry_proposal.py` |
| `PROD` | `monkeyarch/capabilities/element_producers.py` |
| `WALL` | `monkeyarch/capabilities/wall_solver.py` |
| `CADP` | `archflow/adapters/cad_program.py` |
| `CADX` | `archflow/adapters/cad_execution.py` |
| `OCCT` | `archflow/adapters/occt_backend.py` |
| `ELEV` | `monkeydiagram/drawing_elevation.py` |

## 0. Answer

- **Voids as a relation.** An element acts as a void because a host names it in `references.voids`. Nothing marks it as construction and nothing classifies it.
  - It keeps `obj-<element>`: hidden while a host uses it as a void, visible again when no host names it.
- **Stable host ids.** A host delivers `obj-<host>` with or without voids. Its uncut body becomes the internal `obj-<host>-body`. Walls follow the same rule (D-419-1).
- **Identity only for delivered objects.** `GP` defines "delivered" once, and the compiler asks for a design identity only on delivered objects. Consumed intermediates may carry no binding.
- **Provider contract.** `PROP` no longer requires a `host_cut` member to be a `boolean_intersection` output, and no longer tells providers to cut with booleans. The realization contract drops the rules of a voxel proof that was archived.
- **OCCT lowering.**
  - A difference whose voids pass through an extruded host is realized as a profile-with-holes extrusion; any other difference as a boolean cut.
  - A non-default choice is recorded in the execution receipt.
  - Both strategies give the same program digest, the same object ids and the same certified bounds.
- **Predictor.** A difference is accepted whenever every host vertex survives outside all void boxes. This also fixes rotated walls with doors, which fail today.
- **Unchanged.** StateRecord identity, dependency closure and provenance, exact STEP and cold readback.

## 1. Principle: identity first, meaning later

Semantics accrue on a stable identity. A block may be only a block at Stage 1, later be called a wall, and later receive a niche; Stage 1 cannot know what Stage 4 will say. The slice separates three things that are tied together today:

| | Today | After this slice |
| --- | --- | --- |
| Identity | element → component → `binding-<component>` → object ids | unchanged |
| Meaning | optional on components since #400, but an opening needs a wall and a door or window kind | never required for geometry; added later to the same identity |
| Construction role | implied by the producer (wall tool, prism panel) and fixed at creation | a relation (`voids`) that can be added and removed |

Block → wall → niche:

1. At Stage 1 the agent sketches `block-7`, a prism under a new unclassified component. It is delivered as `obj-block-7`.
2. The architect says it is a wall, and that meaning is added to `block-7`'s component. The producer, the element and `obj-block-7` stay. Structural digests do not change, so no geometry is rebuilt.
3. For a niche, the agent sketches `block-9` and sets `block-7.references.voids = ["block-9"]`.
   - `obj-block-7` is rebuilt with the recess and keeps its id.
   - `obj-block-9` stays in the model, hidden, under its own component. When someone calls it a niche, that meaning is added to `block-9`'s component.
4. If the recess is dropped, removing `block-9` from the list delivers it as a visible block again. Turning an existing block into a void is the same edit in reverse.

## 2. Why this slice

### 2.1 What blocks an agent today

- **Every operation must be owned.**
  - `GeometryOperation` requires at least one semantic binding (`GP`).
  - `COMP._operation_graph` reports `UNOWNED_OBJECT` for every produced object, consumed intermediates included.
- **No element can consume another.** Every producer in `PROD` emits operations without inputs. Openings exist only in two forms:
  - `wall.openings`, which needs `kind: door | window`;
  - prism `rectangular_cutouts`, which split a panel into objects with new ids.
- **A wall changes its delivered id when its first opening is added**: `obj-<wall>` becomes `obj-<wall>-cut`. Drawing dressings anchored to the wall then resolve as missing, because `resolve_plan_dressing` never rebinds.
- **The provider contract prescribes booleans.**
  - `PROP._validate_host_cut_apertures` requires every `host_cut` member to be a `boolean_intersection` output.
  - `_relational_authoring_invariants` tells providers to author an intersection and a difference.
  - `_realization_authoring_contract` says to "consume solids through explicit boolean operations".
  - The voxel proof that contract describes was archived on 2026-09-14 (`1b740335`); nothing evaluates it.

### 2.2 What makes agents effective in Blender

Public cases of Codex and Claude modeling in Blender, through headless scripts or Blender MCP, share four traits:

1. **They write code against a familiar API**, with their own helper functions and loops.
   - Simon Willison's scripts define `mat/ell/rod/path/mesh`, and the final round reuses the previous script.
   - A Codex castle build asked for "reusable generator functions" for repeated towers.
   - In a practitioner's comparison, bpy code built complex structures reliably, while a JSON list of parts with coordinates was "quite hard".
   - LL3M's documentation retrieval raised complex operations per asset from 1.20 to 5.86.
2. **Helper geometry states nothing about meaning.** Cutters stay in the scene, hidden, referenced by a modifier on the host, and the host keeps its identity. The agent chooses the method per case:
   - openings left as gaps in a list of wall segments;
   - Solidify walls with boolean openings;
   - automatic cutouts in Pascal Editor.
3. **Construction is tolerant; the check comes separately.** The Astra architectural test pairs a builder script with an independent checker of bounds and dimensions. Vxlabs compares an orthographic overhead view with the PDF plan, because "quality is not proof of dimensional accuracy".
4. **They act, look and fix on the same file** after every stage.

Failures cluster on precise fits, organic forms and topology, which is where exact B-rep certification helps. This slice supplies traits 2 and 3. #428 takes trait 1, and #404, #405 and #303 take trait 4.

Sources:
- [Willison: scripts and transcript](https://github.com/simonw/gpt-6-astra-blender-pelican-bicycle)
- [Codex castle (Zhihu)](https://zhuanlan.zhihu.com/p/2061810201972454882)
- [OpenAI forum: code vs JSON parts](https://community.openai.com/t/how-does-gpt-6-actually-generate-3d-models-in-release-demo-via-codex-local-blender-or-mcps-apis/1395391)
- [LL3M](https://arxiv.org/abs/2508.08228)
- [Sidharth Satapathy: floor plan to house](https://www.sidharthsatapathy.com/blog/claude-blender-mcp-floor-plan-to-3d-house/)
- [Blender Architect skill](https://mcpmarket.com/tools/skills/blender-architect)
- [Pascal Editor](https://korben.info/en/pascal-editor-3d-floor-plan-claude-mcp.html)
- [Astra architectural test](https://blendermcp.org/guides/astra-blender)
- [Vxlabs](https://vxlabs.in/blog/codex-blender-mcp-3d-floor-plan)

### 2.3 Verification decides what may be built

`CADP.expected_object_bounds` was measured at `1bea11da`; it is unchanged at `0efe3765`.

| Case | Today |
| --- | --- |
| Axis-aligned wall with a window | exact |
| 30° wall with a window | exact: the window's box lies inside the wall's larger box |
| 30° wall with a door | refused: "cutter obj-w-void-door can alter a base extremum" |
| Rectangular slab with a through hole | exact |
| L-shaped slab with the same hole | refused |

In both refused cases, every vertex of the host survives the cut, so the bounds are in fact unchanged.

## 3. Design

### 3.1 Delivered objects and identity

- **One definition of "delivered".** `GP` gains `delivered_object_ids(proposal)`. An output is delivered when either holds:
  - no operation consumes it (curves only when they state `retain_for_inspection`);
  - its operation states `retain_for_inspection: true`.

  It replaces `CADP._physical_ids`. `CADP`, `adapters.cad_patch`, `CADX` and `OCCT` import it, so the compiler, the exporters, incremental rebuild and the predictor share one definition. Existing programs keep the same delivered set, because only curves may state `retain_for_inspection` today.
- **Empty bindings allowed.** `GeometryOperation.semantic_binding_ids` may be empty. An operation's serialization does not change, so retained digests do not move.
- **`COMP._operation_graph`:**
  - `UNOWNED_OBJECT` applies only to delivered objects: "delivered geometry object has no design identity binding".
  - An output listed by a binding must come from an operation that names that binding. This is today's coverage rule, restricted to owned outputs. An output no binding lists needs nothing more.
  - Unknown bindings, ambiguous owners, duplicate producers, cycles and object digests work as today.
- **`COMP._validate_assemblies`.** A `host_cut` member is accepted when it is related to its host by construction, in either of two ways:
  - it is produced from the host (the wall solver's aperture);
  - one operation consumes it together with the host (a void region).
- **The runner is unchanged.** Producers keep binding every operation to a component, as today: normally the component of the element that produced it; for a wall opening, the opening's component. The grouping in `runtime.project_runner` therefore needs no change. Unbound intermediates are available to provider-authored programs and to later producers.

### 3.2 `voids`

**Authoring (`PROD.producer_signatures`).**
- `prism`, `loft` and `wall` accept `references.voids`, a list of unique element ids.
- Description: "Elements whose solids are removed from this one. Each keeps its identity and stays in the model hidden; remove it from this list and it shows again. Neither element needs to be classified."
- Constraints stated with it:
  - a void is a `prism` without `rectangular_cutouts`, or a capped `loft`;
  - a void has no voids of its own;
  - host and void belong to one seat.

**Record (`SR._entity_references`).** Each id in `references.voids` becomes an entity reference. The dependency closure therefore invalidates the host when a void changes. `production_order` does not count `voids` as an ordering dependency: a host names its voids only as objects its difference consumes, so a void may stand on the top of the host it is cut into (a recess read from that top) and is produced after it; the operation graph orders the consumption. A true support cycle is still refused.

**Production (`PROD`).**
- `produce_rows` works out, over the rows it produces, which elements are named as voids. It also gives producers a table of the one closed solid each produced element delivers.
- **A void.** Its own operation also states `retain_for_inspection: true` and `hidden_for_inspection: true`. While it is a void it publishes no `<id>-top` datum and no support relation. Its object stays `obj-<void>`, bound to its own component.
- **A host with voids** emits two operations:
  - `<host>-body` → `obj-<host>-body`: the host's previous operation, with its base-level datum binding;
  - `<host>` → `obj-<host>`: a `boolean_difference` of the body and the void objects.

  Without voids it emits exactly what it emits today.
- **Refused with `ElementProducerError`, naming the element:**
  - a void that is not one closed solid, that has voids itself, or that forms a cycle;
  - an element standing on a void's top;
  - a void produced by another seat: the host's cut then consumes an unknown object, and the compiler refuses it;
  - a void whose box misses its host's box: the bounds predictor refuses it, naming both.
  - a prism that combines `voids` with `rectangular_cutouts`;
  - an uncapped loft named as a host, because it is a surface and booleans consume solids.

**Function contracts (`PROP._FUNCTION_CONTRACTS`).** `retain_for_inspection` becomes an optional parameter of `extrusion` and `loft`, and `hidden_for_inspection` an optional parameter of `loft`.

**Checks.** A void's retained object has predicted bounds, so the relation ledger measures it like any other delivered object. No relation kind is added.

### 3.3 Walls (D-419-1)

- **`WALL.solve_wall` operations:**
  - the wall body `<wall>-body` → `obj-<wall>-body`;
  - tools and apertures as today, consuming the body;
  - the cut `<wall>` → `obj-<wall>`, consuming the body, every tool and the wall's `voids`.

  A wall without openings or voids stays one extrusion `<wall>` → `obj-<wall>`, as today.
- **Void bookkeeping.** `HostedVoid.host_object_id` is the body and `cut_object_id` is `obj-<wall>`. `WallSolution.realized_object_id` is always `obj-<wall>`. `opening_solver` keeps the body as its assembly host.
- **Retained drawing references.** Drawing recipes hold object ids in `anchorObjectId` and `hiddenObjectIds`. An entry `obj-<X>-cut` that the current model lacks resolves to `obj-<X>` when the model has that object.
  - This is one rename rule in `ELEV`, used wherever a retained drawing recipe is resolved against a newer model.
  - No other name is ever rebound.

### 3.4 Prism rectangular cutouts (D-419-2)

Their output does not change. Partitioning keeps every piece's bounds analytic, and `adapters.cad_patch` can reuse unchanged pieces. A difference would hit the predictor limit in §3.5 whenever a cutout crosses the panel edge. The prism signature's description points new openings to `voids`. #437 migrates retained cutouts.

### 3.5 Bounds predictor (D-419-3)

This concerns `boolean_difference` in `CADP.expected_object_bounds`.

- **When a base point survives.** It lies strictly outside the box of every void.
- **The new acceptance rule.** The result keeps the base's bounds and point set when the surviving points reproduce both:
  - the base's bounds;
  - every plan position (x, z) of the base.

  This is today's rule for boxes, applied to every base.
- **Nothing accepted today is refused.** The existing acceptance for non-box bases, where each void is disjoint from the base or strictly inside it, stays.
- **What still fails.** A difference that neither rule accepts fails as today. The message now names the void and the host, and says that the void removes part of the host's outline.
- **Retained objects.** Objects delivered only because they are retained keep their predicted bounds.

### 3.6 Provider contract

- **`_relational_authoring_invariants`.** `host_cut_is_aperture_volume` is removed. `host_cut_depends_on_named_host` becomes `host_cut_relates_to_named_host`, with the rule from §3.1.
- **`_validate_host_cut_apertures`** is removed.
- **`_realization_authoring_contract`** drops:
  - the boolean instructions and the `boolean_scope` and `host_cut_scope` keys;
  - the voxel-occupancy, clear-height and exterior-opening rules, whose proof was archived.

  It now states three things: the delivered-object rule; that a region removed from a host is expressed by consuming it together with the host; and that the backend chooses how to realize it. Supplied realization requirements and commitment-derived requirements pass through unchanged.
- **JSON schema.** `semantic_binding_ids` accepts an empty list "only when every output is consumed by another operation and not retained".
- **Effect on old rounds.** Rounds rejected before this change stay readable but cannot be resumed, because resuming requires the same contract. The same holds for any contract change.

### 3.7 OCCT lowering

This concerns `boolean_difference` in `OCCT._build_operation`.

- **Profile-with-holes applies when all of these hold:**
  - the base is an `extrusion`;
  - every void is an `extrusion` whose vector is parallel to the base vector and whose profile plane is parallel to the base profile plane;
  - each void covers the base's whole extent along that vector;
  - projected onto the base profile plane, the void profiles lie strictly inside the base profile and do not touch each other.

  The face gets one inner wire per void and is extruded by the base vector.
- **Otherwise** the difference uses `BRepAlgoAPI_Cut`, as today.
- **Forcing a strategy.** `build_program_shapes` takes `difference_strategy="auto" | "boolean" | "profile_with_holes"`. Tests force either one. Forcing a strategy that does not apply fails with a typed error before any file is written.
- **Recording.** `OcctProgramBuild` and `OcctExecutionReceipt@1` gain `lowering`, a map from operation id to strategy. It is written only for differences that were not realized by a boolean, the same way `reused_object_ids` is written only when present. Receipts of existing programs therefore keep their bytes.
- **Existing programs.** None of them changes strategy: wall tools stop below the wall top or cross the wall faces, and prism cutouts use no difference. A test pins the automatic choice for the current wall fixtures.
- **Other paths.** The Rhino and Blender paths keep booleans.

### 3.8 Drawings

The model-axis elevation (`ELEV`) and the review sheet (`drawings.py`) exclude hidden inspection witnesses, as the cut plan already does. The cut plan's own comment reads: "They are source evidence, not cut material or occluders in the drawing." Today both still draw them. The wall apertures they draw coincide with the hole's edges, so the difference is barely visible; a void that overshoots its host would add lines outside the host.

## 4. Unchanged

- **StateRecord** identity, dependency closure and provenance. The only change is that `references.voids` names entities.
- **Exact STEP, preview and cold readback**: the same checks, tolerances and failures. Both lowering strategies are read back against the same predicted bounds.
- **Typed openings** (`wall.openings` with `kind` and `type_id`), their assemblies, and the wall solver's aperture evidence.
- **`runtime.project_runner`**, and the booleans of the Rhino and Blender exporters.
- **Direct transforms** (move, rotate, scale) still refuse an element that has dependents, and an element used as a void has one: its host (§6).

## 5. Acceptance and tests

| #419 acceptance | Evidence |
| --- | --- |
| Construction intermediates without a semantic owner | Compiler: a consumed, unbound intermediate compiles, and an unbound delivered object is refused. Studio routes: two unclassified blocks joined by `voids` produce a candidate with no `semanticKind`, role, kind or assembly. |
| Hosted openings without choosing booleans | `PROP`: a program whose `host_cut` member is a void region, consumed together with the host, is accepted; the wall solver's intersection evidence is still accepted; the contract text names no boolean operation. |
| Two lowering strategies with the same identity | OCCT: a slab with a through void is built with each strategy. Both builds have the same program digest and object ids; equal volume, bounds and face count; a symmetric difference between the two solids with no volume beyond tolerance; and both STEP files pass cold readback. `auto` picks profile-with-holes for the through void and a boolean cut for a niche. |
| StateRecord behavior intact | `voids` produces dependency edges, the closure invalidates the host, and the existing state-record tests pass. |
| OCCT exact STEP and cold readback intact | The existing OCCT tests pass, and readback succeeds for both strategies. |
| Geometry-first tests pass; new tests cover construction and late certification | `test_geometry_first_authoring` passes. A new block → wall → niche test runs through Studio routes: ids and structural digests stay stable when meaning is added, and removing the void restores the visible block. Only delivered objects appear in STEP and are certified. |

Additional tests:
- The predictor is exact for the 30° wall with a door and for the L-shaped slab; a void that removes a whole host corner is refused with the new message.
- A wall with none, one or two openings delivers `obj-<wall>`, and a retained `obj-<wall>-cut` anchor resolves to it.
- The model-axis elevation excludes hidden witnesses.
- Tests that pin `-cut` ids, the wall operation graph or the delivered set are updated where this design changes them.

## 6. Limitations

- A delivered element cannot also cut another one, as a column through a slab would. Author a separate void.
- A void cannot have voids of its own. A planar surface cannot host voids, because booleans consume solids.
- The predictor still refuses a void that removes a host corner along the host's whole extent. Kernel-measured certification would lift this.
- A void cannot be moved, rotated or scaled directly, because its host depends on it. Edit its parameters or use push/pull instead.
- A void and its host must be produced by one seat.

## 7. Write scope

The lane claims these paths (`governance/work_registry.json`): `GP`, `SR`, `COMP`, `PROP`, `PROD`, `WALL`, `monkeyarch/capabilities/opening_solver.py`, `CADP`, `archflow/adapters/cad_patch.py`, `CADX`, `OCCT`, `ELEV`, and in `apps/archflow-studio/api/archflow_studio_api/application/`, `drawing_plans.py` and `drawings.py`. Tests fall under the shared scope.

Open pull requests touch some of the same files in other functions:
- #420 (GH-402): `COMP`, `PROP` and `SR`;
- #436 (GH-405): `PROD`.

Whichever lands later merges `origin/main`.
