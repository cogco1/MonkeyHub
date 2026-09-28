# Construction first: the agent authors geometry, the runtime chooses producers (#419)

Status: design for cogco1/MonkeyHub#419, after the owner's two comments of 2026-09-28. It extends
`docs/2026-09-28-construction-voids-lowering.md`, which delivered the L5 half of the issue
(construction intermediates without owners, voids as a relation, OCCT lowering).

## 1. Why Codex does well in SketchUp and Blender and is held back in the Hub

### 1.1 What works in a native modeller

The public cases summarised in `docs/2026-09-28-construction-voids-lowering.md` §2.2 share six traits.

1. **The action is a program.** One Ruby or bpy script makes many shapes with variables, loops,
   helper functions and arithmetic. One round trip carries a whole idea.
2. **The vocabulary is small, orthogonal and familiar.** Face, push-pull, move, rotate, copy, array,
   intersect, subtract. Pretraining has seen these APIs many times; nothing has to be discovered.
3. **The scene is the state.** Whatever the agent made is there. Helpers and cutters are ordinary
   geometry. Names, tags and materials are optional and come later.
4. **Errors are local.** A failing call raises at its line; the agent fixes one line.
5. **No bookkeeping.** An operation returns a handle. Nobody asks for a second id, a digest or a job.
6. **Checking is separate.** The agent looks (screenshot, bounds) and corrects. Nothing proves a
   boolean before the agent may write one.

### 1.2 What the Hub asks of the same agent today

Traced in the code at base `0efe3765` (main `a1df9b2b` is the same in these places):

| # | Friction | Where |
|---|---|---|
| F1 | **Classify before geometry.** `studio_schema POST /api/proposals` answers a producer index (`prism, planar-surface, curve, loft, wall`); the agent picks one, then reads that producer's contract. | `apps/monkeyhub/api/monkeyhub_api/chat.py` `_call_tool` (studio_schema), `element_producers.producer_signatures()` |
| F2 | **The in-app agent's output is a union of producer-tagged rows.** `response_schema()` builds one `Element@1` variant per producer with a required `producer`; the record sheet carries `producerSignatures` and every element's `producer`; the prompt ranks producers. | `application/intent_agent.py` `response_schema`, `record_sheet`, `SYSTEM_PROMPT` |
| F3 | **Meaning in the construction path.** `SketchActionDto.semanticKind`, `DocumentTracingRequestDto.semanticKind`; the Hub prompt tells the agent when to send it. | `transport/proposal.py`, `chat.py` modelling text |
| F4 | **Two ids for one thing.** `componentId` and `elementId` (they must differ), `parentComponentId` (a buildable component), `baseLevel` xor `baseDatum`. | `SketchActionDto`, `sketch_prism_proposal` |
| F5 | **One RPC per step.** Create one box: read state → sketch → candidate → poll job → read candidate. A composition is N sketches or one batch with no variables, loops or arithmetic across items. | `routes/proposals.py`, `routes/candidates.py` |
| F6 | **The runtime contract is the authoring contract.** `producer_signatures()` serves both validation and the agent: plan `[x, z]`, `references.base`, `top`, `rectangular_cutouts`, `openings[]` with `sill/head/along/type_id`. | `element_producers.py` |
| F7 | **Specialised capabilities are gated by producer, not meaning.** A door exists only in the `wall` producer's `openings`; `wall` is not even a registered semantic; to get a door the agent must choose `wall` at creation, which fixes meaning at creation. | `produce_wall`, `archflow/semantics` |
| F8 | **Backend proofs leaked into authoring.** Host cuts had to be `boolean_intersection` apertures; the bounds predictor refused doors in rotated walls. Fixed by the L5 half of #419. | `geometry_proposal.py`, `cad_program.py` |

F1–F7 are the "束手束脚". Each one adds a decision, a round trip or a refusal that has nothing to do
with the form the architect asked for.

## 2. Frame: layers and maturity stages

The owner's frame, adopted as the rule for every interface in this change:

- **L0 Intent**: what the person wants now. **L1 Construction**: making the form with generic verbs;
  helpers allowed. **L2 Persistent design state**: identity, parameters, dependencies, history;
  facets may be empty. **L3 Semantic facets**: meaning added to the same identity; wall-specific
  capabilities live here. **L4 Domain compilation**: each domain asks for the semantics it needs.
  **L5 Backend lowering and certification**: OCCT and other backends; they never dictate authoring.
- **Stage A Explore** uses L0+L1. **B Promote** stabilises in L2. **C Architectural enrichment** adds
  L3. **D Technical enrichment** is L4. **E Deliver/certify** is L5.

An interface belongs to the layer that owns its information. A producer name is L5 knowledge
("how the runtime realises an extrusion"); it may not appear in an L1 contract.

## 3. Design

### 3.1 L1: the construction script

The agent sends one bounded **construction script**: a small Python subset, interpreted (never
executed) by `monkeyarch/construction`. It is parsed with `ast` and walked by a whitelist interpreter.

**World.** Metres. Y is up. A plan point is `(x, z)`; a 3D point is `(x, y, z)`. This is the Hub's
existing convention (`plan points are [x, z], with Y up`), unchanged.

**Language.** Numbers, strings, lists, tuples; `=`, `+=`, tuple unpacking; `for … in` over
`range`, lists, `enumerate`, `zip`; `if/elif/else`; `def` with positional and keyword parameters and
`return`; `break`, `continue`, `pass`; list comprehensions; arithmetic and comparisons. Builtins:
`range len min max abs round sum enumerate zip list tuple float int print`; math: `pi sqrt sin cos
tan atan2 radians degrees floor ceil`. No imports, attributes, dunder names, `while`, `with`,
`try`, `lambda`, `global`, classes or string formatting beyond `+`. Limits: 20 000 characters,
20 000 evaluation steps, 1 000 loop iterations per loop, call depth 16, 300 geometry results.

**Vocabulary** (every verb returns a handle or a value; handles are opaque):

| Verb | Meaning |
|---|---|
| `rect(x, z, width, depth)` | Plan rectangle with corner `(x, z)`. |
| `polygon(points)` | Plan polygon `[(x, z), …]`, not repeating its first point. |
| `circle(x, z, radius, segments=24)` | Regular polygon (8–128 segments); faceted, stated as such. |
| `offset(profile, distance)` | Mitred offset of a simple polygon; positive grows it. |
| `plane(origin, x_axis, y_axis)` / `front(z=0)` / `side(x=0)` | A drawing plane for vertical profiles; `front` draws in `(x, y)` and extrudes along `+z`, `side` draws in `(z, y)` and extrudes along `+x`. |
| `extrude(profile, height, at=0, plane=None)` | Push-pull a profile into a solid. In plan it rises from `at`; a negative height goes down. |
| `face(profile, at=0, plane=None)` | A flat face. |
| `path(points)` | An open 3D polyline `[(x, y, z), …]`. |
| `loft(sections, cap=True)` | A solid through `section(profile, at)` rings (same point count). |
| `move(obj, dx=0, dy=0, dz=0)`, `rotate(obj, degrees, about=(x, z))`, `scale(obj, factor, about=(x, z))`, `mirror(obj, x=None, z=None)` | Change a handle in place. Rotation is about a vertical axis and turns +x toward +z; `scale` scales plan positions about `about` and heights about the base; `mirror` reflects across the vertical plane `x = …` or `z = …`. |
| `copy(obj, dx=0, dy=0, dz=0)`, `array(obj, count, dx=0, dy=0, dz=0)` | New handles with the same geometry (not its cuts); `array` returns `[obj, copy1, …]`. |
| `pushpull(obj, distance)`, `set_height(obj, h)`, `set_base(obj, at)` | Edit a solid's height or base. |
| `cut(host, *cutters)`, `uncut(host, *cutters)` | Remove the cutters' solids from the host, or stop removing them. A cutter keeps its id and stays in the model hidden; `uncut` delivers it again. |
| `level(id)`, `top(obj)` | Anchors for `at`: a project level, or the top of a solid (a dependency: it follows that solid). `anchor + number` offsets it. |
| `param(key)` | A project parameter, used directly as a height or an `at` offset (a binding, not a number). |
| `bounds(obj)` | `((xmin, ymin, zmin), (xmax, ymax, zmax))` from the definition. |
| `name(obj, id)`, `get(id)`, `delete(obj)` | Identity: name a result, reach existing geometry, remove it. |

**Identity.** Each geometry result that survives the script is persisted under one **geometry id**:
the `name(...)` given, else the top-level variable it was last assigned to (`_` → `-`), numbered
`<name>-2`, `<name>-3` when a loop reuses a variable, else `shape-<n>`. Ids are deterministic, so
re-running a script updates the same geometry. `get(id)` returns existing geometry for editing.
The agent never sees a second id, a producer, a component tree or a digest inside the script.

**Errors.** A refused script saves nothing and answers `422 CONSTRUCTION_INVALID` with `line`,
`column`, the source line and one sentence. Geometric refusals from the runtime (a cutter that has
cutters of its own, a solid standing on a cutter's top) come back through the same shape, naming the
script line that caused them.

**Lowering (L1 → L2).** The compiler turns the final handles into record rows; producers are chosen
here and nowhere else:

| Result | Record rows |
|---|---|
| any new result | `Component@1 <id>` under the project's modelling root (`intent` = the id) and `Element@1 <id>-body` |
| plan `extrude` | producer `prism`: `profile`, `height`, `elevation`, `references.base` = the anchor's level or `<element>-top` datum |
| plane `extrude` | producer `prism` with `work_plane` |
| `face`, `path`, `loft` | `planar-surface`, `curve`, `loft` |
| transforms | baked into points, plane and elevation |
| `cut` | the host element's `references.voids` names the cutters' elements (the voids relation, D-419-0) |
| `get` + edit | the existing element row rewritten in place, same ids |
| `delete` | `removeEntityIds` for the component and its element (dependency refusal applies) |

### 3.2 The agent contract, after this change

| Surface | Before | After |
|---|---|---|
| Create/modify geometry | `POST /api/proposals/sketch`, `/transform`, `/push-pull`, `/elevation`, `/delete`, `semanticEdit` with producer rows | **`POST /api/proposals/construction {stateDigest, script, parameters?, summary?, sourceRunId?, sourceStageRef?, sourceProposalId?, keep?}`** |
| Discover | `studio_schema POST /api/proposals` → producer index → producer contract | **`GET /api/construction`**: the vocabulary, conventions, limits and one example; the Hub prompt carries the same vocabulary so no discovery round trip is needed |
| Read the model | `GET /api/state` (components, elements with `producer`) | **`GET /api/construction/model[?run=]`**: `stateDigest` and one entry per geometry id: form, bounds, `cuts`/`cutBy`, facets, and the capabilities its facets unlock |
| Meaning | `semanticKind` on sketches; `Component@1.semantic_kind` via `semanticEdit` | **`POST /api/proposals/facets {stateDigest, targets:[{id, set:{…}, remove:[…]}]}`** (Stage C) |
| Door or window in a wall | choose producer `wall` at creation | **`POST /api/proposals/hosted-opening`**, listed for an entity only once it has `architectural.role = wall` |

Removed from every agent-facing surface: the producer index and `producer` argument of
`studio_schema`; producer variants in the OpenAPI schema of `semanticEdit` (its entities are
described as parameters, relations, readings and component intents); `semanticKind` on
`SketchActionDto` and `DocumentTracingRequestDto`; the sketch/transform/push-pull/elevation/delete
routes and `/api/state` from the Hub's agent allow-lists (the Studio web client keeps its drawing
routes); `Element@1`/`Type@1` rows in an agent's `semanticEdit` (the Hub refuses them and names the
construction route). The in-app intent agent answers `{status, script, facets, parameters, utterance,
targetId, why, question}`; its sheet describes geometry in construction terms without producers.

### 3.3 L2: identity, promotion, dependencies

A geometry id is a `Component@1`; its geometry is one `Element@1 <id>-body`, bound by the runtime.
Stage A results live in proposals and candidates, which are reversible and move nothing; Stage B
promotion is the existing admission, Continue (Working Head) and Stage acceptance. `top(obj)` and
`cut` record dependencies (`<element>-top` datum, `references.voids`) the moment they are written,
because they are part of the form, not of its meaning.

### 3.4 L3: facets

`Component@1.fields.facets` is a map of namespaced keys to values, validated against
`archflow/semantics/facets.py`:

| Key | Values |
|---|---|
| `architectural.role` | `wall, slab, floor, roof, column, beam, stair, ramp, door, window, opening, railing, ceiling, partition, canopy, screen, foundation, space, furniture, site` |
| `architectural.enclosure` | `exterior, interior` |
| `structural.role` | `load_bearing, non_load_bearing, bracing` |
| `material.name` | free text |
| `fabrication.method` | free text |

An unknown key is refused with the nearest known key. Adding, changing or removing a facet upserts
the component only: its elements, objects, datums and dependency edges are byte-identical before and
after. `semantic_kind`, `roles` and `conditions` stay readable for existing projects.

### 3.5 Stage C: capabilities appear with meaning

`GET /api/construction/model` lists, per entity, the capabilities its facets unlock. Today there is
one: `hosted-opening` (a door or window with its family, frame and leaf) for
`architectural.role = wall`. `POST /api/proposals/hosted-opening {stateDigest, host, kind, along,
width, sill, head, typeId?}` refuses a host without that facet with `409 ENRICHMENT_REQUIRED`
naming the facet to add. For a wall-faced block whose geometry is a straight rectangular prism
standing on a level or datum, the runtime re-realises the element with the `wall` producer, keeping
the element id and `obj-<element>` (D-419-1). Anything else is refused with the reason (`the block's
footprint is not a rectangle`).

### 3.6 L4: domains ask for what they need

`GET /api/domains/{domain}/readiness[?run=]` answers `ready` with the entities it will read, or
`enrichment_required` with one request per entity: the facets missing and why. `structure` needs
`structural.role` and `material.name` on anything whose `architectural.role` is load-bearing by
nature (wall, column, beam, slab, roof, foundation); `envelope` needs `architectural.enclosure` on
walls, roofs and slabs. No domain guesses a facet from shape or producer.

### 3.7 L5 stays behind the line

The construction contract text contains no producer name, `boolean`, `aperture`, `topology`, `OCCT`
or `semanticKind`; a test pins that. OCCT keeps exact B-rep, lowering strategy and cold readback.

## 4. Benchmark

One fixed Stage A task, run by Codex through three paths, recording tool calls, refusals, model
rounds, wall time, first-result time and human corrections:

1. **Native baseline.** Codex writing a script for a desktop modeller through its own MCP. SketchUp
   2025 cannot run unattended here: a fresh instance with `-RubyStartup` never ran its script within
   120 s because the welcome screen waits for a person (probe, 2026-09-28). The baseline is Rhino 8
   through Codex's configured Rhino MCP, the same kind of direct scripting.
2. **Hub, producer path** (origin/main).
3. **Hub, construction path** (this branch).

Harness: `tests/monkeymonitor/run_turn_benchmark.py` (paths 2 and 3), `tests/monkeymonitor/run_native_baseline.py` (path 1).
Results: §7, filled from the retained outputs.

## 5. Acceptance

| Owner's criterion | Where it is met |
|---|---|
| Stage A discovery shows no producer menu and no `semanticKind` | §3.2; OpenAPI and Hub tool tests |
| One small construction program does the task without classification | §3.1; construction route test; benchmark |
| New geometry's primary path has no `semanticKind` | §3.2; DTO tests |
| Producers only inside runtime/compiler/lowering | §3.1 lowering; contract text test |
| Enriching as a wall keeps identity, history and dependencies | §3.4, §3.5; Studio end-to-end test |
| Wall capabilities only after semantics | §3.5 |
| Domains ask for enrichment instead of guessing | §3.6 |
| OCCT constraints absent from Stage A | §3.7 and the L5 half |
| Native vs producer vs construction comparison | §4, §7 |

## 6. Limitations

- `trim`, `split`, `join`/`union` and `sweep` are not in the first vocabulary. `union` needs an
  element relation like `voids` (a follow-up with the same lowering pattern); `trim`/`split` need a
  kernel-side split whose pieces get identities.
- Facet vocabularies are deliberately short; new keys are additive.
- Construction edits change `prism`, `planar-surface`, `curve`, `loft` and `wall` rows; rows of the
  retired runner producers (`column-array`, `stair`, …) are readable but not editable by script.

## 7. Benchmark results

(Filled after the runs.)
