# Handoff brief: vibe-modeling client/server frontend (P108)

Date: 2026-09-02. Prepared in the ArchFlow V4 main session for the fresh session
(新建会话, cwd `D:\ARCHFLOW_V4`) that builds the vibe-modeling frontend, directed
by Kaiwen Liu. Every factual claim below was verified against the working tree
on 2026-09-02; where verification was partial, the text says so explicitly.

This lane is registered as card **P108** in `governance/work_registry.json`
(status `ready`) with planning card `docs/mapping/planning/P108-vibe-modeling-frontend.md`.
Its write scope is **`apps/archflow-studio/` only**. Codex is concurrently
active in `archflow/runtime/`, `archflow/state/` and `governance/` — do not
write there.

---

## 1. Read first, in this order

1. **`docs/claude-worktree/2026-09-02-vibe-modeling-frontend-spec.md`** — the
   user's specification, retained verbatim (31 sections). Treat it as the
   requirements document. This brief does not repeat it; it builds the
   repo-specific layers around it: what already exists, what is fenced, what is
   genuinely new.
2. **`apps/archflow-studio/README.md`** — ownership and boundaries of the
   existing Studio slice (read-only `.3dm` previewer), run/build/test commands,
   planned slices.
3. **`AGENTS.md`**, section `## One canonical abstraction in, one parallel
   abstraction out` — the standing rule that binds this lane (fence 4 below).
4. **`docs/mapping/planning/P108-vibe-modeling-frontend.md`** (this lane's card)
   and **`docs/mapping/planning/P107-occt-in-process-executor.md`** (the native
   geometry lane this frontend's preview loop depends on).
5. Useful context, not binding: `docs/claude-worktree/2026-09-02-state-record-architecture-review.md`
   (why StateRecord@1 is the one state), `docs/claude-worktree/2026-09-02-villa-as-tables.md`
   (why buildings are records, not scripts), `docs/claude-worktree/2026-09-01-component-library-consolidation.md`.

---

## 2. The four fences (Kaiwen, 2026-09-02 — mandatory)

### Fence 1 — 防火墙，机器强制 (machine-enforced firewall, both directions)

`tools/archcheck.py` enforces architecture policy over the repo from
`governance/architecture_policy.json`. As of tonight the policy's
`checked_source_roots` includes `apps/archflow-studio/backend`, and this rule
is live (quoted from the policy):

> source `apps/archflow-studio/backend`, forbidden targets `rhino3dm`, `numpy`,
> `networkx`, `OCP`, `build123d`, `shapely`, `trimesh`, `scipy`, `tests`,
> `tools`, `probes` — "The Studio backend orchestrates and delegates: geometry
> mathematics, dependency graphs, and state schemas live in archflow and are
> imported, never reimplemented beside it (P108 fence)."

Verified: `python tools/archcheck.py` currently prints `ARCHITECTURE PASS
(317 files, ...)` with this rule active. The backend may **only orchestrate and
import `archflow`**; locally defining geometry math, graph structures, or state
schemas is a violation the checker reports, not a style preference.

The reverse fence already stands in `apps/archflow-studio/README.md`:
"`archflow/` remains the reusable control kernel and is never imported by the
browser bundle." The browser talks only to the gateway.

These are machine-enforced, not politeness. Run `python tools/archcheck.py`
after every backend change.

### Fence 2 — DO-NOT-REBUILD 清单 (inventory in section 3)

Every module in section 3 exists and does what the line says (each was opened
and read during preparation of this brief). Import it; never reimplement it.

### Fence 3 — 接缝唯一化 (new capability grows only at three named seams)

- `apps/archflow-studio/backend/ports.py` — reserved application ports
  (Protocol classes, deliberately unimplemented). It already reserves an
  `IntentProvider` whose docstring reads, exactly:
  **"Translate a user utterance into a proposal candidate, never a commit."**
  Also reserved there: `RetrievalProvider`, `PreviewBackend`,
  `ViewerAssetProvider`, `HumanReviewPort`, `StudioEventSink`.
- `apps/archflow-studio/backend/contracts.py` — typed, versioned payload
  contracts (`StudioCapability@1`, `StudioHealth@1`, ...).
- `apps/archflow-studio/src/gateway/` — the browser's only backend surface
  (`StudioGateway.ts` interface, `HttpStudioGateway.ts` implementation).

Procedure: declare a port in `ports.py` first, define its payloads in
`contracts.py`, implement by delegation to `archflow`, expose through the
gateway. No fourth seam.

### Fence 4 — 规则绑定 (the AGENTS.md rule binds this lane)

`AGENTS.md` carries the section **"One canonical abstraction in, one parallel
abstraction out"**: every canonical abstraction introduced must retire a
parallel abstraction that expressed the same thing, in the same change or its
card; "retire" means no production path can reach the parallel any more; if
nothing can be retired, the abstraction is not canonical yet. The section lists
the historical duplicates that motivated the rule and five enforcement rules.
It applies to the new session's work: in particular, do not create a second
state schema, second dependency graph, second exporter, or second user-string
key set beside the ones inventoried below.

One more fence the machine cannot hold (stated in the P108 card):
**前端不算几何，只画几何** — the client never computes geometry. Preview meshes
are tessellated by the backend and pushed to the client. No client-side CSG.
(The existing slice-1 viewer parses a user-dragged `.3dm` in the browser via
rhino3dm-wasm for display only; that stays display, not authoring.)

---

## 3. DO-NOT-REBUILD inventory — import, never reimplement

All paths verified to exist; each line summarizes what the module actually
provides.

| Module | Provides (import, never reimplement) |
|---|---|
| `archflow/state/state_record.py` | `StateRecord@1`, the canonical design state: `entities` (typed by schema — `Component@1`, `Element@1`, `Level@1`, `GridAxis@1`, `Space@1`, `Connection@1`, `MassingLevel@1`, `Volume@1`, `Type@1`, `Assembly@1`, `Reading@1`; `Entity` carries `fields`, `parent_id`, `basis_refs`), `parameters` (`Parameter` with `expr`, `inputs`, `lock_authority`, `epistemic_status`), `relations` (`Relation` with `datum_role`, `propagation` ∈ unchanged/revalidate/invalidate, and a `ValidatorBinding`), `obligations` (`DesignObligation`, defined in `archflow/state/operational_state.py`), `option`, `base` (a `ProjectVersionRef`). Views: `dependency_edges()` (relations + parameter inputs + host/level fields as kernel edges), `closure(changed_refs)` (BFS downstream — the affected subgraph), `design_components_of`, `project_levels_of` / `project_grids_of`, `developed_design_view`, `state_digest`. |
| `archflow/capabilities/element_producers.py` | Ten producers that read semantic references, never coordinates: `produce_column_array`, `produce_capitals`, `produce_beam`, `produce_pediment`, `produce_wall`, `produce_prism`, `produce_ring`, `produce_loft`, `produce_dome_cap`, `produce_declined`. `production_order(rows)` topologically orders element rows by referenced names; `element_rows_of(record)` extracts `Element@1` rows already in production order. |
| `archflow/capabilities/reference_resolver.py` | The one resolver (P100) for semantic placement: plan references `GridRef`, `GridIntersection`, `AxisPoint`, `HostAlong` via `resolve_plan`; elevation references `LevelRef`, `OffsetFrom` via `resolve_elevation` (elevations stay symbolic until the compiler resolves them from datums). "No producer computes either on its own." |
| `archflow/state/derivation.py` | `DerivationTable@1` / `DerivedQuantity@1` / `EvaluatedDerivations@1`: named quantities as small arithmetic expressions over readings and other quantities, resolved in dependency order by a safe evaluator (no Python `eval`; numbers, names, `+ - * /`, unary minus, parentheses, `min max abs round sqrt`; cycles and non-finite results fail typed). `substitute(value, derived)` rewrites symbolic values. |
| `archflow/capabilities/relation_checks.py` | `RelationCheck@1` / `RelationCheckReport@1`: runs a record's relation validators against realized bounds (analytic or Rhino readback). Check kinds include `support_contact`, `meets`, `aperture_exists`, `clearance_interval`, `alignment`, `engagement_interval`, `separation_interval`. "No healing: a violated relation is reported, never repaired here." |
| `archflow/compilers/geometry.py` | `compile_geometry_program(state, proposal, ...)` binds a geometry program to the record via `_state_identity` (project_id, run_id, base, `state_digest`, components) — the compiler checks the proposal and state are exact-base peers. The analytic bounds predictor `expected_object_bounds` lives in `archflow/adapters/cad_program.py`. |
| `archflow/adapters/cad_patch.py` | `select_patch_operations(program, prior_program)` → `PatchSelection` (`RhinoPatchSelection@1`): `changed_object_ids` / `added` / `retired` / `kept_object_ids`, `rebuilt_op_ids`, `delete_object_names`, per-object `reasons`. This IS the affected-subgraph-at-geometry-level and the diff data source. The full rebuild stays the oracle. |
| `archflow/adapters/cad_execution.py` | `prepare_rhino_three_dm_export` / `execute_rhino_three_dm_export` / `verify_rhino_export_readback`: the Rhino COM export path with typed receipts (`RhinoCadExecutionReceipt@4`, `RhinoCadExportPlan@4`), restamp vs patch vs rebuild modes, and independent readback verification of the saved file. |
| `archflow/project/repository.py` | `FilesystemProjectRepository` (owner P036): content-addressed, immutable records and runs; `put_json` / `load_json` / `list_json`, `create_run`, head locking with `StaleProjectHead`. This IS the versioning/history store; the write helper is literally named `_write_immutable` ("without ever replacing an existing target"). The architecture policy names it "the sole project-document filesystem writer". Never overwrite; never invent a parallel store. |
| `archflow/runtime/project_runner.py` | `run_project(record, seats, ...)`: the execution engine — `StageExecutionGuard` (will not run without a retained workflow + envelope), seat rounds from `schedule_seats`, canonical producers in reference order, relation checks against compiled bounds, handovers, optional CAD export that reuses/restamps/patches/rebuilds, receipts with per-seat wall time. |
| `tools/verify_state_record.py` | CLI that proves a State Record reproduces a reference run: state-digest identity plus object-by-object geometry bounds comparison, recorded as `StateRecordEquivalence@1`; supports `--rename old=new`. |
| `archflow/relations/contracts.py` | `ArchitecturalRelationKind` — the kernel relation vocabulary (24 kinds: `composition`, `aggregates`, `primary_contains`, `references_zone`, `adjacent`, `intersects`, `dependency`, `support`, `load_transfer`, `host`, `hosts_void`, `fills_void`, `access`, `allows_passage`, `clearance`, `realization`, `realizes`, `lineage`, `refines`, `replaces`, `interface`, `alignment`, `blocks`, `evidences`). `state_record.Relation` accepts exactly this set (`_RELATION_KINDS` is built from it — "one vocabulary: the kernel's"). The spec §6 relation list maps onto it (see mapping table). |

---

## 4. Spec-to-repo mapping — exists vs. genuinely new

For each spec section that says "create": verdict, and where the existing home
is.

| Spec § | Asked for | Verdict | Where / notes |
|---|---|---|---|
| §4 state schema | canonical project state (components, parameters, relations, constraints, locks, versions, history, geometry_bindings) | **EXISTS** | `StateRecord@1`. components → `entities`; relationships/constraints → `Relation` + `ValidatorBinding` + `DesignObligation`; locks → `Parameter.lock_authority` (and `Relation.propagation`); versions/history → the P036 repository's immutable records/runs plus `predecessor_ref`/`base`; geometry_bindings → `SemanticBinding` (`archflow/state/geometry_program.py`) plus the `archflow:*` Rhino user text. Do not write a second schema. Honest caveat: the two live project records currently declare **zero** relations/obligations and no `lock_authority` values — the machinery is real and tested (`tests/test_state_record.py`, `tests/test_relation_checks.py`), but populating relations and locks on the live villa record is part of this lane's work, not a given. |
| §5 component tree | semantic ownership hierarchy | **EXISTS** | `Component@1` entities with `parent_id`; `design_components_of(record)` yields the typed tree. The live villa record carries 41 of them. |
| §6 dependency graph + traversal | explicit edges, upstream/downstream, affected subgraph, topo order, cycles | **EXISTS** | `record.dependency_edges()` + `record.closure()`; vocabulary is `ArchitecturalRelationKind`. **networkx is NOT needed** (`pyproject.toml` has zero dependencies) and is now forbidden in the backend by the firewall rule. Spec-name mapping: depends_on→`dependency`, supports→`support`, hosted_by→`host`, aligned_to→`alignment`, intersects→`intersects`, derived_from→parameter `inputs`/`lineage`, references→`references_zone`, generated_from→`realization`/`lineage`, bounded_by/avoids→`clearance`/`interface` (interval validators), constrained_by→`Relation`+`ValidatorBinding` generally. `symmetric_with` has **no** existing kind — do not add one locally; raise it with Kaiwen (kernel vocabulary changes belong to the main session). |
| §7 constraints | hard + soft constraints | **EXISTS (hard) / partial (soft)** | Hard: `Relation` + `ValidatorBinding` (seven check kinds) measured by `relation_checks`, plus `DesignObligation` for duties. Derived parameters: `DerivationTable` / `Parameter.expr`. Soft-constraint scoring has no existing home; if the MVP needs it, it is new — declare it at the seams. |
| §8–9 mutation patch + preview | state patches, protected sets, conflict detection, preview | **NEW — this is the real work** | But: `closure()` supplies "affected subgraph" at state level and `select_patch_operations` supplies changed/kept/delete sets (with reasons) at geometry level. The mutation engine composes these; it does not re-derive them. |
| §10–11 planner / scheduler | task DAG, parallel waves | **NEW** | Note `production_order` already topologically orders element rows, and the runner already schedules discipline-seat rounds (`schedule_seats`). The new planner sits above `run_project`, not inside it. |
| §13 user strings | `archflow_id`-style keys binding Rhino objects to state | **EXISTS — use it, do NOT invent a second key set** | `expected_object_semantics` in `archflow/adapters/cad_program.py` already writes: `archflow:producer_op`, `archflow:object_ref`, `archflow:operation_ref` (always), and `archflow:bindings`, `archflow:component`, `archflow:material`, `archflow:commitments`, `archflow:evidence`, `archflow:inspection_witness` (when applicable). The spec's proposed `archflow_id` keys are superseded by this set. Repo history: M088 (done) single-sourced the viewer's hand-copied key sets at their definition sites; a second key set is exactly the regression it removed. |
| §15 export | deterministic `.3dm` export, layers, receipt | **EXISTS** | translate (`cad_program`) + prepare/execute (`cad_execution`) + `RhinoCadExecutionReceipt@4`. Layers are already by component (`archflow::<component>` under root layer `archflow`), block-instance families included. Do not write a second exporter; the spec's `00_SITE`-style layer names are a naming preference to raise, not a reason for new code. |
| §16 versioning | immutable versions, never overwrite | **EXISTS** | The P036 `FilesystemProjectRepository`: content-addressed, refuses to replace existing targets, stale-head detection. The spec's `/projects/villa_001/v012/` layout is already realized as `projects/<id>/runs/<run>/records/<name>-<sha256>.json`. |
| §17–18 diff + mutation leakage | semantic diff, leakage metric | **NEW metric, inputs EXIST** | Inputs: `PatchSelection` (changed/kept/retired ids + per-object reasons), `RelationCheckReport@1`, record digests (`state_digest`, `digest`), `StateRecordEquivalence@1` receipts. The metric definition and its report are this lane's contribution (and a paper artifact — design it cleanly). |
| §22 villa fixture | classical villa test case | **EXISTS — extend, do NOT rebuild** | `D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME\workspace\projects\villa-rotonda-reconstruction\input\runner\state-record.json` (+ `seats.json`), and the richer `rocca-pisana\input\runner\state-record.json` (+ `derivations.json`, `seats.json`; 16 elements, 52 parameters with `expr` chains in module ratios). Verified equivalence receipts (`StateRecordEquivalence@1`, reference `runner-002`): villa `equivalence-003` — `worst_m: 0.0`, `geometry_equal: true`, `state_digest_equal: true`; rocca `equivalence-003` — `worst_m: 0.0`, `geometry_equal: true`, `state_digest_equal: false` (element renames only: `dome→dome-body` etc.). So: object-by-object geometry reproduction at 0.0 m holds for both; exact digest identity holds for the villa. The villa input record is deliberately narrow today (4 `Element@1` rows, 0 parameters/relations); the spec's portico mutations mean **extending this record** with elements, parameters, relations and locks — not building a new fixture. |
| §14 stack | Python, FastAPI, Pydantic, networkx, JSON store, rhino3dm | **DEVIATIONS — raise with the user, do not silently adopt** | (a) FastAPI/Pydantic vs. the existing dependency-free stdlib backend (`backend/server.py` uses `http.server.ThreadingHTTPServer`; contracts are frozen dataclasses). (b) networkx: unnecessary (`closure()` exists) and machine-forbidden in the backend. (c) A "JSON state store" would duplicate the P036 repository — forbidden by fence 4. (d) rhino3dm: already present (Python optional dep `rhino3dm==8.32.1`; browser wasm `8.32.2`). The existing frontend stack is React 19 + three.js + rhino3dm-wasm + Vite, backend Python 3.12 stdlib. |

---

## 5. Decided architecture direction (2026-09-02 night — decisions, not options)

1. **Native OCCT lane.** Card **P107** (OCCT in-process executor) is registered
   (`ready`, planning card `docs/mapping/planning/P107-occt-in-process-executor.md`).
   It will interpret the same `CompiledGeometryProgram` the Rhino path runs,
   tessellate preview meshes, measure bounds against the analytic predictor,
   export STEP always and mesh `.3dm` via rhino3dm, and drive incremental
   re-execution from the patch selector. **Until it lands**, geometry
   generation goes through the existing Rhino pipeline or stays read-only over
   existing runs. The Rhino cost is measured, not estimated: 48
   `RhinoCadExecutionReceipt@4` receipts across the two projects show 36.2–41.9 s
   per export (mean ~38 s) — fixed COM start/save cost, not proportional work.
2. **The loop to design for:** edit record → sub-second mesh preview (once P107
   lands) → occasional certified Rhino export at stage gates. Build the
   frontend so the preview backend is a port (`PreviewBackend` is already
   reserved) — read-only over existing runs today, OCCT tomorrow, without a
   frontend rewrite.
3. **Evidence tiers:** OCCT-lane receipts are self-measured plus cold-file
   readback (`self_measured_cold_read`); Rhino-lane receipts are
   independent-instrument certification (`independent_instrument`). Surface the
   tier in the UI; never present a preview as a certification.
4. **前端不算几何，只画几何:** preview meshes are tessellated by the backend and
   pushed to the client. The client never computes geometry; no client-side CSG.
5. **The `.3dm` is an artifact, never canonical state.** This matches the spec
   exactly and is already how the repo works (the runner's exports are
   receipts; the record is the state).

---

## 6. What is genuinely new to build

In dependency order, per the spec's phases, all inside `apps/archflow-studio/`:

1. **Mutation engine** (spec §8): state patches over `StateRecord@1` with
   protected sets, conflict detection via `closure()`, producing a *new* record
   (records are frozen dataclasses — mutate by constructing the successor, then
   persist through the P036 repository as a new immutable version).
2. **Mutation preview** (spec §9): must/may/must-not-change rendering from
   `closure()` (state level) + `select_patch_operations` (geometry level).
3. **Planner + DAG scheduler** (spec §10–11) above `run_project`.
4. **Semantic diff + mutation-leakage metric** (spec §17–18) from
   `PatchSelection`, `RelationCheckReport@1`, and record digests. This feeds
   the CAADRIA paper's benchmark — keep the definition clean and recorded.
5. **Intent parser** at the reserved `IntentProvider` seam (spec §12): proposal
   candidates, never commits. Per the spec §31, do not build the LLM chat
   interface until the deterministic state/mutation/export pipeline works.
6. **Review UX** (spec §21) extending the existing Studio shell (component
   tree, versions, viewer, mutation panel, diff/log panel).
7. Populating the live villa record with the relations, parameters and locks
   the demo mutations need (see §22 row above) — in the runtime workspace, via
   the repository, never by hand-editing files under `runs/`.

Acceptance (from the P108 card): demo 1 (4→6 columns, protected
pediment/axis/width, leakage 0) and demo 2 (cornice depth +20%) run end to end
on the villa record with a mutation diff and a new immutable version;
every exported object carries the existing `archflow:*` identity; archcheck
stays green throughout.

---

## 7. Open questions for Kaiwen (real decisions, not yet made)

1. **FastAPI/Pydantic adoption** (spec §14) vs. extending the dependency-free
   stdlib backend. Recommendation: keep stdlib for the next slice — the backend
   is orchestration-only by fence 1, contracts are already frozen dataclasses,
   and zero-dependency is a stated property of the repo. Adopt FastAPI only if
   SSE/WebSocket streaming for the event sink makes stdlib genuinely painful.
   His call.
2. **Repo layout**: the spec proposes `/client /server /shared`; the repo has
   `apps/archflow-studio/{src,backend}` with the three seams already reserved
   and the firewall already pointed at `apps/archflow-studio/backend`.
   Recommendation: build in `apps/archflow-studio` — the machine-enforced
   fence, the P108 write scope, and the existing gateway/ports/contracts seams
   all name it, and a parallel `/client /server` tree would itself violate
   fence 4. Marked as his call, but the fences follow the recommendation.
3. **`symmetric_with`** (and any other relation kind the mutations need that
   the kernel vocabulary lacks): kernel change, main session's ownership —
   flag, don't fork.
4. **Layer naming** (spec §15's `00_SITE`… vs. the existing
   `archflow::<component>` scheme): raise before changing; the existing scheme
   is what the readback verifies.

---

## 8. How to verify claims yourself (run these before trusting this brief)

```powershell
# architecture firewall (fence 1) — must print ARCHITECTURE PASS
py -3.12 tools/archcheck.py

# full kernel suite (the repo's declared test command)
py -3.12 -m unittest discover -s tests -v

# targeted modules relevant to this lane
py -3.12 -m unittest tests.test_state_record tests.test_element_producers ^
  tests.test_relation_checks tests.test_cad_patch tests.test_cad_program ^
  tests.test_project_repository tests.test_project_runner -v

# studio backend tests + frontend build (from apps/archflow-studio/)
npm run test:backend
npm run typecheck
npm run build
```

Registry/policy facts in this brief (P107/P108 registration, the backend
firewall rule) were verified against the working tree, which at preparation
time carried them as uncommitted changes on `main`; if they are missing when
you read this, check `git log` / `git status` before assuming this brief is
wrong.

Workspace discipline: project artifacts (records, runs, receipts) land in the
runtime workspace `D:\PROJECTS\01_ACTIVE_当前项目\ARCHFLOW CAADRIA 2027\V4_RUNTIME`,
written through the P036 repository. Code lands in this repo. Nothing lands in
scratch folders.

---

## Addendum 2026-09-03 — Kaiwen answered the section-7 questions; this supersedes section 7

1. **FastAPI + Pydantic: adopted.** Pydantic models are transport shapes only. A model mirroring an archflow
   schema is a duplicate-function module — the exact thing this lane must not produce. Convert at the boundary,
   delegate inward.
2. **Layout: top-level `/client` `/server` `/shared`** (spec section 3). `apps/archflow-studio` is demoted to a
   retained read-only demo: harvest its viewer (rhino3dm-wasm + three.js) and launch pattern by MOVING code, do
   not extend it, do not copy-and-diverge. The seams relocate accordingly: `server/ports.py` (the IntentProvider
   protocol moves there from the demo), `shared/contracts/`, the client gateway. Tests live in `server/tests`;
   repo-root `tests/` belongs to archflow.
3. **`symmetric_with` exists now.** `ArchitecturalRelationKind.SYMMETRIC_WITH` is in the kernel
   (`archflow/relations/contracts.py`), participant roles `first`/`second`/`axis` — the mirror axis is a
   participant reference, not a number. `StateRecord` relations accept it immediately.
4. **Numbered layer scheme: decided and mechanised.** `prepare_rhino_three_dm_export`,
   `translate_to_rhino_python` and `expected_object_semantics` now accept `layer_by_component`; a mapped
   component exports on `<category>::<component>` (e.g. `20_STRUCTURE::portico-columns`), an unmapped one stays
   visibly on `archflow::<component>` — the kernel never guesses a category (neutrality rule). **You supply the
   map** from the record's component tree using the spec section-15 categories.
5. **Firewall extended ahead of you:** `server/` and `shared/` are pre-registered checked roots; importing
   rhino3dm, numpy, networkx, OCP, build123d, shapely, trimesh or scipy there fails `python tools/archcheck.py`
   (verified with a probe file before the trees existed). .3dm writing stays in archflow adapters and P107 —
   spec section 14's "rhino3dm for headless writing" is satisfied by delegation, not by importing rhino3dm in
   the server.

