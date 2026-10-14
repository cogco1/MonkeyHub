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
| `carrier.py` | State-bearing files: the design state written into a 3DM or GLB (or a sidecar), and read back with its binding checked. |
| `reviser.py` | The hostless reviser: read a state file, apply a typed change, realize it, write the next state file. No CAD application runs. |
| `attacks.py` | Eleven attacks on state-bearing files, each compared with the honest revision; `python -m labs.revision_tasks.attacks --out <dir>`. |
| `x1.py`, `x1_tasks.json`, `history/events_v1.json` | Sufficiency: one reference decision from four starting contexts (history, state file, geometry only, state file without obligations); `python -m labs.revision_tasks.x1 --out <dir>`. |
| `x2.py`, `prompts/` | A model decides a revision from one kind of material in one `claude -p` call (arms A history, B geometry readback, C state without guards, D state as text, F state as embedded JSON); its changes are executed and judged by the oracle; `python -m labs.revision_tasks.x2 --out <dir>` writes the prompts only, `--cli <claude> --ledger <file>` calls the model. |
| `x7.py` | Scale: a grid of N real prism posts, C-full against D in 3DM and GLB, a chain of single-parameter revisions through the hostless reviser with a cold read of each result in a new process; phases, bytes and a two one-sided test at 5 %; `python -m labs.revision_tasks.x7 --out <dir> --sizes 20,500,5000`. |
| `x8.py` | Undeclared relations, third layer (I3): a model proposes missing relations from the elements' roles and boxes and the declared relations (`prompts/i3_*.md`); a proposal is kept only if the start geometry confirms it the way I1 and I2 are confirmed; `python -m labs.revision_tasks.x8 --out <dir>` writes the prompts only. |
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

A task with `setup` starts from the fixture with those changes applied. Its patch carries the digests of the fixture before them, so only an arm that checks the base can see it is stale.

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

The answer's `changes` are mapped to fixture parameters (the history's names through `x1.HISTORY_PARAMETERS`; B names none), applied to the start, realized, and judged by the oracle with the X4 task file of the same id. Scores: the verdict against the gold, affected and recheck recall, a false accept (`held` where the gold does not hold or the oracle finds a violation) and a usable successor (executed and held). The multi-turn G arm is not built yet.
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
