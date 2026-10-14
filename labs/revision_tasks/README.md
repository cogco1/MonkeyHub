# revision_tasks — deterministic revision tasks with an independent geometry oracle

Small synthetic building assemblies, one typed change per task, executed through
the production producers → compiler → OCCT path, and judged by an oracle that
shares no code with that path. Research tooling (#635): nothing here writes a
project, changes an owner's contract or is imported by production code.

## Files

| File | What it is |
|---|---|
| `fixtures.py` | Three parametric fixtures as in-memory `StateRecord`s: F1 portico colonnade, F2 portico–transition–rotunda, F3 wall–door–lintel–stair. `bound()` binds a record to a synthetic run (a value, no project). |
| `tasks/*.json` | `TaskSpec@paper/1` task files (one per task): frozen start digests, typed change, protected relations with the oracle's measure for each, gold expectations. |
| `taskspec.py` | Validator, and `--freeze` to recompute the derived fields (start digests, fixture and oracle file hashes). It never touches expectations. |
| `pipeline.py` | Realization through the owners' public functions, the system's own checks, and the ArchFlow arm. |
| `oracle.py` | The independent judge, run as its own `python -I` process. Imports only `rhino3dm` and `numpy`. |
| `run.py` | Runs tasks into an empty directory, one bundle per attempt, an index and a gate summary; `--compare` replays against an earlier run. |
| `carrier.py`, `state_page.html` | State-bearing files: the design state written into a 3DM, GLB, IFC, USD or single-page HTML file (or a sidecar), and read back with its binding checked; `state_page.html` is the HTML file's page and viewer. |
| `reviser.py` | The hostless reviser: read a state file, apply a typed change, realize it, write the next state file. No CAD application runs. |
| `attacks.py` | Eleven attacks on state-bearing files, each compared with the honest revision; `python -m labs.revision_tasks.attacks --out <dir>`. |
| `x1.py`, `x1_tasks.json`, `history/events_v1.json` | Sufficiency: one reference decision from four starting contexts (history, state file, geometry only, state file without obligations); `python -m labs.revision_tasks.x1 --out <dir>`. |
| `x2.py`, `prompts/` | A model decides a revision from one kind of material in one `claude -p` call (arms A history, B geometry readback, C state without guards, D state as text, F state as embedded JSON); its changes are executed and judged by the oracle; `python -m labs.revision_tasks.x2 --out <dir>` writes the prompts only, `--cli <claude> --ledger <file>` calls the model. |
| `x2_g.py`, `prompts/g_intro.md` | X2's arm G: the same task worked through MonkeyHub's own tools in one multi-turn `claude -p` turn (an isolated Hub on the family's production project, the prepared context of X3's mode M, the lab proxy's 20 calls, G's own cap `G_MAX_CALLS`, stated in `g_intro.md`); the model the turn left is judged by the oracle and scored as x2's arms, with the landing classes of protocol 7.11; `python -m labs.revision_tasks.x2_g --out <dir> --cli <claude> --ledger <file> --limit-usd <n>`. |
| `x7.py` | Scale: a grid of N real prism posts, C-full against D in 3DM and GLB, a chain of single-parameter revisions through the hostless reviser with a cold read of each result in a new process; phases, bytes and a two one-sided test at 5 %; `python -m labs.revision_tasks.x7 --out <dir> --sizes 20,500,5000`. |
| `x8.py` | Undeclared relations, third layer (I3): a model proposes missing relations from the elements' roles and boxes and the declared relations (`prompts/i3_*.md`); a proposal is kept only if the start geometry confirms it the way I1 and I2 are confirmed; `python -m labs.revision_tasks.x8 --out <dir>` writes the prompts only. |
| `x3_hub.py` | X3's MonkeyHub way of working (M): a disposable P036 project holding a lab building, an isolated Hub and Project Runtime on free ports (settings and temp under the run root), one `claude -p` turn connected to MonkeyHub's own MCP server as an external host (`mcp_server.py --project-dir --source-session-id`), cost into the shared ledger, and `result_model`, which finds the model the turn left on the working design through Runtime and Hub read routes. |
| `x3.py`, `x3_requests.json`, `x3_sf_mcp.py`, `prompts/x3_system.md` | X3 sessions: one building (BA, BB) in one way of working (M, M+, T, H, SF), its requests in order, the oracle after each and at most one correction sentence; SF works on the state file through `x3_sf_mcp` (read the state, read the boxes, submit typed changes to the hostless reviser); `python -m labs.revision_tasks.x3 --out <dir>` with `--cli <claude> --ledger <file>` calls the model. |
| `native3dm.py` | A native editable 3DM: each delivered object as the rhino3dm primitive that holds it exactly (extrusions for prisms, 24-sided columns, ring pieces, lofts of two sections, and a wall minus its through-openings as its elevation outline); the dome as its exact triangulation; `archflow:primitive` per object; the C-full state and binding. `apply_edits` is the field-level edit hook; `mesh_model` gives the oracle named meshes. |
| `x9.py` | Edit the 3DM's own data: DD-det (read the native file, apply the typed change, compile, edit only the objects whose numbers differ, re-seal; no OCCT) against REGEN (`pipeline.realize`), judged by the oracle and compared object by object; `python -m labs.revision_tasks.x9 --out <dir>`. |
| `x9_agent.py`, `prompts/x9_*.md` | X9's DD-agent: a model gets the native file's object table and embedded state and answers a verdict and field-level edits in one call; `native3dm.apply_edits` writes them as given; the result is judged by the oracle and compared with REGEN; `python -m labs.revision_tasks.x9_agent --out <dir>` writes the prompts only. |
| `x5.py` | Two short transport chains (3DM to GLB and back, 3DM to IFC and back) through tools that know nothing of the state, plus a plain-resave control: what survives each hop and what the reviser does; `python -m labs.revision_tasks.x5 --out <dir>`. |
| `buildings.py` | The X3 buildings as authored records: BB (three-storey house and studio, spec v2: 86 design rows, 46 openings, three alternating straight flights, hidden voids for the stair holes) and BA (the Pantheon at its measured size, simplified: 16-column portico, link block with the second pediment, rotunda, attic, dome and oculus; the drum family on one 48-gon; attic, dome and oculus ring as eight sector pieces each; the portico roof's clearance to the second pediment, J3, declared nowhere). |
| `production_project.py` | A disposable project holding a fixture as production does: a frozen one-stage workflow, a stage run through the runner (the repository's own tools), decisions recorded against it, and the context pack read for that run. |
| `infer.py` | Undeclared relations made explicit: geometric (I1) and role-rule (I2) inference on the start, kept as open candidate obligations and checked on a candidate. |
| `context_pack.py` | The `ContextPack@1` MonkeyHub would give an agent, read from a disposable project by an in-process Project Runtime. |
| `test_*.py` | Task-file validity and freezing; the oracle on a real F3 candidate and on eight wrong geometries; state files on every arm and format; what inference finds on F1 and F3. |

## Run

From the checkout root (the lab puts this checkout's source roots first). This needs
`cadquery-ocp` and `rhino3dm` (`pip install -e packages/monkeycad[occt]`). Rhino itself is not needed.

```
python -m pytest -q labs/revision_tasks
python -m labs.revision_tasks.run --out <new empty dir>
python -m labs.revision_tasks.run --out <another new dir> --compare <first dir>
python -m labs.revision_tasks.taskspec --check
```

`--freeze` is used only when a fixture or the oracle changes on purpose. Run it before
any counted run; the run itself refuses a task whose fixture or oracle no longer
matches what the task froze (`FIXTURE_DRIFT`, `ORACLE_DRIFT`).

## The ArchFlow arm (`pipeline.run_archflow_arm`)

The arm works through these steps in order and stops at the first refusal or reopen.

| Step | Owner | Outcome |
|---|---|---|
| A change with no value | harness | `refuse MISSING_INFORMATION` |
| Exact base: the task's frozen digests are passed to `apply_state_record_operator`; they are never recomputed from the input | kernel (`archflow.state.state_record`) | `refuse STALE_BASE` |
| Closure of the changed refs | kernel (`StateRecord.closure`) | reported |
| Open obligations the record declares with `validator:parameter-at-most`, evaluated on the successor's parameters | harness (the kernel stores obligations but evaluates none) | `refuse OBLIGATION_UNSATISFIABLE` |
| Produce, compile and export incrementally from the start's verified STEP (`prior_program`/`prior_step`) | `monkeyarch` producers and compiler, `monkeycad.occt` | `refuse` if it cannot be built |
| Declared relations, measured with `check_relations` on the **cold-read STEP bounds** | `monkeyarch.domain.relation_checks` | `reopen DECLARED_RELATION_VIOLATED` |
| Undeclared interference: visible solids that newly penetrate (`measure_occt_solid_pairs`), compared with the start and minus pairs a declared relation covers | harness over `monkeycad.occt` measurement | `reopen UNDECLARED_INTERFERENCE` |
| A declared relation that cannot be measured | — | `unknown` |
| Otherwise | — | `accept` |

## Baseline arms (`run.py --arms`)

The same task can be run by five arms (`--arms B0,B1,B2,B3_archflow,B3+I --experiment X4`).

- **B0** rebuilds the whole model.
- **B1** replaces only the elements whose own rows bind a changed parameter.
- **B2** replaces the elements in the declared closure.
- **B3** is the ArchFlow arm above.
- **B3+I** is B3 with the inferred relations of the start; an accepted candidate that breaks one becomes `ask`.

B1 and B2 reuse every other object from the start model as it was, composed at the 3DM level. B0, B1 and B2 skip the base check, read no obligations and relations, and claim `accept` whenever the change executes.

A task with `setup` starts from the fixture with those changes applied. With `patch_base: before_setup` (T8X) its patch carries the digests of the fixture before them, so only an arm that checks the base can see it is stale. With `patch_base: after_setup` the patch was made on that start and carries its digests.

**X3 buildings.** `tasks/BA-*.json` and `tasks/BB-*.json` are the X3 requests whose change is a typed patch of the building's parameters (BA A1-A4, A6, A7; BB B1, B4, B5); `fixture.generator` names `buildings.py`. Each starts where its request is made in X3: the earlier requests that land in the gold main line are its `setup`, with `patch_base: after_setup` (A4, A6 and A7 start after A1-A3; B4 and B5 after B1). B2 cannot be a SET_SCALAR patch (its lintel's ends are derived), so B4 and B5 start without it; `x3_request.start_note` says so. Relations come from the request's `measures`, `recheck` and the session's conditions. `test_x4_buildings.py` checks the chain without OCCT.

A false accept is an `accept` that the oracle finds violated, or an `accept` of a task whose gold is `refuse`.

## State-bearing files (`carrier.py`, `reviser.py`)

The four arms carry the same state. They differ only in where it lives and what binds it.

| Arm | Where the state is | Binding |
|---|---|---|
| C-full | in the file: the whole record | full |
| C-task | in the file: the task view of the record (elements, levels, grids, components, evidence, parameters, relations, obligations) and the `ContextPack@1` | full |
| C-unbound | in the file: the whole record | no per-object geometry digests |
| D | beside the file (sidecar JSON); the file keeps only its existing `archflow:*` references | full |

The full binding is `{record_digest, state_digest, sufficient_digest, parent_file_sha256, element_geometry_digests}`.

**3DM.** The state is written into the document string `archflow:state` through `execute_occt_export(provenance=...)`, which also verifies it on its cold read. The per-object geometry digests exist only once the file does. So the harness reads the file with rhino3dm, adds `archflow:binding` and saves it again; a test checks that this changes no object geometry.

**GLB.** Converted from that 3DM by `InProcessMeshProvider`. The state and binding go into `asset.extras.archflow`; each node's identity goes into `node.extras.archflow`.

**IFC.** The 3DM's named meshes as `IfcTriangulatedFaceSet` bodies on `IfcBuildingElementProxy` products, in metres, under identity placements (a harness conversion). The state and binding go into the property set `ArchFlow_State` on `IfcProject`; each product's identity goes into `ArchFlow_Object`. Geometry digests cover each face set's coordinates (rounded to 1e-9 m) and indices.

**USD.** The 3DM's named meshes as `UsdGeom.Mesh` prims under `/World` (z up, metres), written as `.usda` text. A prim name cannot hold a hyphen, so each mesh carries its object name in the string attribute `objectName`. The state and binding go into the root layer's `customLayerData`; each prim's reference into its `customData`. Geometry digests cover the points (32-bit in USD, rounded to 1e-5 m) and faces.

**HTML.** One self-contained page (`state_page.html`) that a person can open to see the model: a small three.js viewer loaded from a CDN, beside the parameters and obligations the page carries. The model is the GLB above with each node's identity and the design-state reference only, base64 in the data block `<script type="model/gltf-binary" id="model-glb">`. The state and binding are the JSON block `<script type="application/json" id="decision-projection">`, `{"binding":β,"state":S}` with S and β the same canonical JSON text as the 3DM's `archflow:state` and `archflow:binding` strings and every `<` written `<`, so no end tag or comment can start inside it. The binding's per-object digests are the embedded GLB's, as in the GLB format. The reader reads only the page: S and β from the JSON block, geometry and the design-state reference from the embedded GLB. A page with two blocks of the same id is refused. The oracle reads no HTML; its tests judge the embedded GLB.

**Checks on read.** In order:

1. the state and binding are present;
2. the record matches its digest;
3. the pack matches its digest;
4. the model's own design-state reference matches;
5. every object's geometry digest matches.

A file that fails is refused with a code and never repaired.

`reviser.revise` takes its exact base from the file's binding and can be given the file hash a change was made against (`STALE_BASE` otherwise). The rest is the arm above, with a full rebuild from the file's state.

**Harness additions.** These are not kernel capabilities. Each revision reports them under `harness_added`:

- the binding seal;
- the at-most obligation;
- the undeclared-interference scan;
- the retention of the context pack;
- rebuilding a state from an external file.

## Model calls (`x2.py`)

Every arm gets the same request (`x1_tasks.json` `utterance`), the system prompt `prompts/system_common.md`, the template `prompts/request.md` and one intro line from `prompts/materials.json`; only the materials differ. One generic renderer writes every text arm. The C-task state comes from a disposable project that holds the fixture as production does, with the history's rejections recorded as decisions.

The call is `claude -p --model claude-opus-5-5 --output-format stream-json --verbose --permission-mode dontAsk --setting-sources project,local --disable-slash-commands --max-turns 1 --tools "" --disallowedTools WebSearch,WebFetch --strict-mcp-config --mcp-config <empty> --append-system-prompt-file prompts/system_common.md --settings {"disableBundledSkills": true} --no-session-persistence --max-budget-usd 1`, from an empty trial directory, with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` as MonkeyHub's own chat sets it. A probe call with these flags saw no CLAUDE.md, memory file, skill, hook, MCP server or tool; Claude Code's own system prompt and account context remain and are the same for every arm.

Each call is priced with `monkeymonitor.pricing.quote` at the manifest's API-equivalent rates (input $4, output $20 per million; cache reads and writes at 0.1, 1.25 and 2 times input), and the larger of that and the CLI's `total_cost_usd` is appended to a shared ledger. A run stops before a call that could take its part or the total past its cap.

The answer's `changes` are mapped to fixture parameters (the history's names through `x1.HISTORY_PARAMETERS`; B names none), applied to the start, realized, and judged by the oracle with the X4 task file of the same id. Scores: the verdict against the gold, affected and recheck recall, a false accept (`held` where the gold does not hold or the oracle finds a violation) and a usable successor (executed and held). The multi-turn G arm is in `x2_g.py`.

**Arm G (`x2_g.py`).** One round is a family's production project made as the C-task pack's is (`x1.production_pack`: the record through the runner, the history's rejections recorded as decisions), a disposable Hub on it, and one fresh `claude -p` turn with MonkeyHub's tools through the lab proxy (20 calls, G's own cap, which does not follow X3's; 600 s). The system prompt is `system_common.md` and the request template `request.md`; the intro `prompts/g_intro.md` is the only G-specific text (G has tools, carries out a held or violated change itself, and ends with the same JSON), and the materials are the context MonkeyHub's own chat gives a turn (X3 mode M's injection). The answer is parsed as every arm's. The model the turn left (`x3_hub.result_model`) is judged by the oracle against the Hub's own start model, or, when MonkeyHub exported none, the run's state record realized by the lab against the lab's start. The score is x2's (verdict, affected, recheck, false accept, usable successor, which counts the left model whether landed or not), the four verdict categories of manifest 7.2, and `landing`: `landed`, `correct_not_landed` (with `would_complete_if_landed`), `candidate_not_landed` or `no_change`. A round is refused before it could take its part, the total or the invocation's `--limit-usd` past the cap.
## Editing the file's numbers (`native3dm.py`, `x9.py`)

Each family's start is written once as a native 3DM. DD-det reads it and checks its state, applies the task's typed change, compiles the result (pure Python) and edits only the objects whose primitive numbers changed: a move or a new height is set on the stored path in place; a new profile is a new extrusion under the same id and name, because openNURBS sets a profile only on an empty extrusion; a mesh is moved or regenerated. The state and binding are sealed again. A task is `full` when every changed object is native, `partial` when one is a mesh, `impossible` when the data cannot express it, and `not_applicable` when the gold only refuses, the request has no value, or nothing moves.

DD-det learns the new numbers from the compiler, so it is an upper bound on what editing data alone can do. Native files carry no render meshes. Columns and rings are polygonal extrusions, as the program's are; a door whose sill is on the floor is a notch in the wall's outline, not an inner profile.

## MonkeyHub turns (`x3_hub.py`)

`isolated_hub(root)` makes (or reuses) the building's production project and starts a disposable Hub and its Project Runtime; nothing of the parent's Studio or Monitor settings is inherited and CUDA is hidden. `run_turn` writes an MCP config that starts MonkeyHub's own `chat/mcp_server.py` as an external host and runs one `claude -p` turn with MonkeyHub's tools named one by one (no built-in tool, no `computer_*` tool), the fairness flags of `x2` and `--max-budget-usd 3`; the agent first publishes the user's message (`chat_present`), as MonkeyHub requires before any project tool answers. `result_model` chooses the turn's result in this order: the working design if the turn moved it, an admitted candidate, the candidate the answer names, the latest candidate.

A resumable turn (M+) writes the CLI's transcript into its configuration directory; `run_turn` refuses to persist or resume unless that directory is given or the CLI's default one is allowed (M+ only, its transcripts measured by `transcript_bytes`), so no transcript lands where it was not meant to.

The lab proxy (`serve_proxy`) fronts MonkeyHub's server for every Hub mode: it counts design tool calls (X3: 30 per request; G: 20; MonkeyHub's presentation calls, `presentation_bind` and `chat_present`, do not count) and, for T and H, refuses every Studio action outside construction proposals, candidates and the model readback. Every turn raises the CLI's MCP output limit (`MAX_MCP_OUTPUT_TOKENS`); below it the CLI replaces a large model readback with a file path that an agent without built-in tools cannot open. `continue_on` moves the working design as the architect's Continue on the Hub page.

## X3 sessions (`x3.py`)

Request 0 (the opening conditions and the history's rejections) is written by the harness where each mode keeps it, not sent as a model turn. After each request the oracle runs in its own process; when a condition broke, the simulated architect sends the matching correction once. Outcome classes are provisional and every row is flagged `needs_coding`. A correct candidate the agent left for acceptance is `correct_not_landed` (with `would_complete_if_landed`); the next request continues from the unchanged working design. A turn that changed nothing and says its tools cannot do the request is flagged for the hand-coded class `honest_limitation`. In M and M+, an answer that says its change is now the current or working design while the Working Head did not move is flagged `false_landing_claim_auto`. Each bundle lists `harness_added`; every request has 30 design tool calls (MonkeyHub's presentation calls do not count) and 600 s, and the CLI's turn limit is 34. GH is paused.

**File hand-over (A10, B10).** The harness makes the consultant's file from the design as it stands when the request comes (Hub modes: the Working Head's model; SF: the current file): `external_edit` moves the one named object and rhino3dm writes everything else back as read (object ids, attributes and user text, document strings, layers, materials). The file is kept beside the bundle (`<request>.consultant.3dm`), and `<request>.handover.json` records both SHA-256, the edit and the object's box before and after.

- **Delivery.** Hub modes: the architect attaches it, `POST /api/model-assets` `{projectId, fileName, contentBase64}` with no run or state, sent through the Hub's runtime route with an idempotency key (`x3_hub.attach_model`). The Runtime keeps it as an external model asset in its own run `studio-model-<sha256>`, with no design state. The request text gains one line, `(Attached: <fileName>, model asset <sha256>, run <runId>.)`. In T and H, that request's allow-list adds `GET /api/model-assets/{sha256}/index` (`T_HANDOVER_REQUESTS`, `harness_added` `returned_file_readable`). SF: the file becomes the current file.
- **Held in the Hub modes.** MonkeyHub offers a client no read of an attached model's objects. `GET /api/model-assets/{sha256}/index` requires an exact run state; for an external asset's run it answers `REFERENCE_STATE_NOT_EXACT`, and the index it gives a state-bound asset carries native metadata only, with no geometry. So `x3.HUB_HANDOVER_READY` is false and a Hub session stops at A10/B10 (`NotBuilt`) until the protocol owner decides.
- **SF.** The file's binding check refuses it (`GEOMETRY_BINDING_STALE`, naming the moved object); `state_read` still reads it, marked unchecked, and `model_objects` shows the moved box. `submit_change` refuses it as before unless the agent passes `rebuild_from_state: true` (`harness_added` `sf_rebuild_from_state`, switch `x3_sf_mcp.REBUILD_FROM_STATE_ALLOWED`). That option is taken only when the geometry is the one failed check, the state, record, context and design-state digests all passing. The carried state is then realized into a file of its own (`state-NNN-rebuilt`, its parent the returned file), and the change is applied to that file. Every outside change is discarded and listed: the object, its box in the file and as the state realizes it, its centre shift and size change. Any other refusal stands as before.
- **Judging and scoring.** The request is judged against the design as recorded, before the edit. Two oracle `bbox` relations give `external_change_adopted`: the moved object stands where the consultant left it, not where the design recorded it. In SF, what the agent itself changed is measured against the file it was given (`summary.delivered`). `external_change_reported_auto` is a flag for coding: the answer names the object, or says near "consultant" or "returned" that something moved or differs. The existing complete logic stays. If the agent changed nothing, asking first is `asked_rightly`. Carrying the outside change without a word is `wrong`: SF's file is its design, so the move counts as an unrequested change.

On a fresh session (`--requests A10`), the start design stands in for the design at request 10, so A10's target numbers (`ring-top`, `dome-flat-top`, `R2-recorded`), which assume A1-A9, do not hold there.
## Transport chains (`x5.py`)

Each arm's 3DM state file is passed through tools that do not know the state, and after every hop one benign revision is tried (a D arm keeps its sidecar, which stays with the authority).

| chain | hop 1 | hop 2 |
|---|---|---|
| `3dm-glb` | production mesh conversion to GLB | trimesh reads it, rhino3dm writes the named meshes to 3DM |
| `3dm-ifc` | named meshes to IFC4, no property sets | ifcopenshell's geometry reader, rhino3dm writes the meshes |
| `control` | rhino3dm reads and saves the 3DM | — |

Six items are recorded per hop: object identity, parameters, dependency edges, obligations, version binding and geometry digests. The attempt is `correct_accept`, `silent_wrong_accept`, `correct_refuse`, `unneeded_refuse` or `crash`.

On F1 and F3, every lossy hop is refused and the control is accepted, on all four arms. Object names survive every hop. The C arms lose the whole state (`METADATA_MISSING`). D keeps its state outside but the file no longer matches its binding (`GEOMETRY_BINDING_STALE`): the reviser does not carry a D state over to a re-meshed file.
## Attacks (`attacks.py`)

The attacker holds the model file and never a D arm's sidecar, which stands for the authority outside the file. Attackers who recompute digests use the same public library a reader does.

Each case is classed by comparing it with the same probe change on the untouched file:

- `detected`;
- `undetected_same_outcome`;
- `undetected_different_outcome`;
- `not_applicable`: the attack edits state the file does not carry;
- `crash`.

Outcome on F1 and F3, 3DM:

- **What the in-file arms miss.** All three in-file arms let three attacks through, each changing the result: a guard relaxed and its digests recomputed, a guard deleted and its digests recomputed, and an older file replayed in place of the current one.
- **What D catches.** D detects the replay, and every other attack it is open to.
- **Without geometry digests.** C-unbound also accepts an outside geometry edit and a duplicated object name. Its full rebuild silently discards the file's own geometry.

A file's own digests prove that the file is self-consistent. They cannot prove that it is the current state or that its guards are the ones that were agreed.

## Undeclared relations (`infer.py`)

Two deterministic layers, both harness additions (the kernel infers nothing):

- **I1, geometry.** On the start's cold-read STEP, every pair of elements no declared relation covers is measured with OCCT. A distance of at most 1 mm is a contact: a support when one element's top meets the other's bottom over a shared plan, otherwise an abutment. Up to 5 cm is a near contact. Tops (or non-ground bottoms) at one level are an alignment.
- **I2, role rules.** Each element gets a role from its producer and id; a frozen rule table proposes the relations those roles should have (a roof rests on its entablature or wall, a stair does not run into a wall, ...).

Two narrowing rules, frozen with the role table, remove proposals before they are measured: an alignment of two faces the record binds to the same declared datum (same level or datum, same offset; a top with no reference of its own is bound by its base and its declared height), and a role rule's non-interference between elements more than 1.0 m apart. Raw and narrowed counts are both reported. Every inference that remains is kept only if it holds on the start geometry; a role proposal of a support needs the upper element resting on the lower one's top (F2's roof only touches the rotunda wall's side, so that proposal is dropped). It is written into the record as an open `DesignObligation` with `validator:inferred-<kind>`, marked unconfirmed. Before execution (`predict_inferred`), each inferred relation is measured on the compiler's analytic per-object bounds (compile only, no OCCT; on the three fixture starts they equal the cold-read boxes exactly). Where boxes cannot settle it (no analytic bounds, or a non-interference whose boxes already overlapped at the start), the fallback is the closure boundary: one element inside the change's closure and one outside. Each result names its method. After execution, a broken one (a contact that opens or penetrates, a support whose upper element no longer rests on the lower one's top, an alignment that breaks, an interference) turns the decision into `ask`; it is never silently repaired or treated as a refusal.

They appear as the X1 start `SF+I` and the X4 arm `B3+I`. Scoring counts `ask` as right wherever the gold says violated and reports it separately as an unneeded question where the gold holds.

On the fixtures: the pediment's support on the entablature (F1) is found by both layers; the stair-to-wall non-interference (F3) by I2. F2's transition roof (J3) is not found: it sits 0.2 m from the portico at the start, outside the near band, and the role proposal does not hold on the start, so it is dropped.
## The oracle (`oracle.py`)

- **What it reads.** Each object's name (identity) and mesh, from a `.3dm` (rhino3dm), a `.glb` (struct and numpy; glTF's y-up is rotated to the model's z-up; node transforms are refused) an `.ifc` (read as STEP text: products with triangulated face sets under identity placements in metres; anything else is refused), or a `.usda` (read as text: each mesh's `objectName`, points and faces; a layer not z-up in metres, or a mesh with a transform, is refused). No user strings, document strings, layers or state; `reads_metadata` is always false.
- **Where expectations come from.** Every expected number is in the task's `protected_relations[].measure`, written by a person from the fixture's physical definition. Gold is never the record's dependency closure (a test checks one case).
- **Checks.** `support_contact`, `vertical_clearance`, `non_interference` (grid sampling of the boxes' overlap with ray-parity containment), `aperture` (opening extents measured from the host mesh, plus rays through and beside it), `bbox`, `within`, `geometry_unchanged`, `column_row`, `stair` (rises, goings, top, landing contact, headroom under the slab), and for whole buildings (X3): `wall_piers` (wall beside every opening, edges from the wall mesh's own breakpoints and rays through it), `wall_above_lintels`, `flight` (risers, rise and going limits, headroom over the usable part of each tread), `landing_depth`, `door_beside_flight` and `collisions` (every pair of solids sharing volume, largest first; cavities are recognised by name, accepted embeddings by name prefix).
- **Unchanged geometry.** Compared by shape signature (bounds, volume, area) with 1e-5 m / 1e-6 relative tolerance. An unchanged B-rep can be re-meshed differently: another diagonal on a planar quad, or a few more triangles on a curved face reused from STEP.
- **Coverage on F3.** `test_oracle.py` plants eight wrong geometries, and the oracle must reject each: opening shifted 8 cm; same opening area at another aspect; lintel shifted; left jamb 0.3 m short; leaf shifted; leaf out of the wall plane; opening filled at the same wall volume; a metadata-only edit.

## Bundles

`<out>/<task_id>/bundle.json` has two parts.

- **`deterministic`.** Task and harness identity, the start and candidate digests, the typed patch and the operators applied, the cold readback (rounded), the system checks, the oracle report, the ids, the arm's claim, the status and the score.
- **`observed`.** Wall-clock phases, environment versions and the artifact file hashes.

`bundle_sha256` covers `deterministic` only. STEP and 3DM bytes differ between runs of the same program: header timestamps, the order of style entities, and in the 3DM the creating user and file path. Keep raw `.3dm` files out of anything published.

## Current tasks (smoke)

| Task | Change | Expected | Status |
|---|---|---|---|
| F1-T1-01 | six → four columns, ends fixed | accept, all held | as expected |
| F1-T6-01 | four columns and clear spacing ≤ 2.2 m | refuse | as expected |
| F2-T3-01 | transition block raised into the declared cornice clearance (J1) | reopen/refuse; J1 violated | as expected |
| F2-T4-01 | portico columns +1.0 m; the transition roof is hit where no relation is declared (J3) | not accept; J3 violated | as expected |
| F3-T1-01 | door 1.2 → 1.6 m | accept, all held | as expected |
| F3-T5-01 | storey height changed, new level not given | refuse | as expected |

Two runs into fresh directories give identical bundle hashes.

The gold in these files is a draft written for smoke runs. A run counts only after the gold has been reviewed and the task files frozen.

## Limits

- Contact and clearance in the oracle use bounding boxes over plan overlap. Interference uses sampling: 14³ points per overlapping pair of boxes, so a sliver smaller than a sample cell can be missed.
- The undeclared-interference scan finds solid penetration only. A clearance that nothing declares (the space under a stair, say) is invisible to it.
- A hostless revision rebuilds the whole model from the file's state: the file does not carry the previous exact STEP, so shapes are not reused across files.
- A C-unbound file that another tool edited is accepted, and its rebuild silently discards the edit (`test_carrier`).
- Inference sees only what touches, nearly touches or aligns at the start, plus what its role table names. A relation that holds across a gap (F2's J3) or between roles the table does not list stays undeclared.
- Role rules over-propose: on a change that moves an element away from an inferred contact on purpose, the answer is a question the gold did not need.
