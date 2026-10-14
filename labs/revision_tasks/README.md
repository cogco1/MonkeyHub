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
| `context_pack.py` | The `ContextPack@1` MonkeyHub would give an agent, read from a disposable project by an in-process Project Runtime. |
| `test_*.py` | Task-file validity and freezing; the oracle on a real F3 candidate and on eight wrong geometries; state files on every arm and format. |

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

## State-bearing files (`carrier.py`, `reviser.py`)

The four arms carry the same state. They differ only in where it lives and what binds it.

| Arm | Where the state is | Binding |
|---|---|---|
| C-full | in the file: the whole record | full |
| C-task | in the file: the task view of the record (elements, levels, grids, components, parameters, relations, obligations) and the `ContextPack@1` | full |
| C-unbound | in the file: the whole record | no per-object geometry digests |
| D | beside the file (sidecar JSON); the file keeps only its existing `archflow:*` references | full |

The full binding is `{record_digest, state_digest, sufficient_digest, parent_file_sha256, element_geometry_digests}`.

**3DM.** The state is written into the document string `archflow:state` through `execute_occt_export(provenance=...)`, which also verifies it on its cold read. The per-object geometry digests exist only once the file does. So the harness reads the file with rhino3dm, adds `archflow:binding` and saves it again; a test checks that this changes no object geometry.

**GLB.** Converted from that 3DM by `InProcessMeshProvider`. The state and binding go into `asset.extras.archflow`; each node's identity goes into `node.extras.archflow`.

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

## The oracle (`oracle.py`)

- **What it reads.** Each object's name (identity) and mesh, from a `.3dm` (rhino3dm) or a `.glb` (struct and numpy; glTF's y-up is rotated to the model's z-up; node transforms are refused). No user strings, document strings, layers or state; `reads_metadata` is always false.
- **Where expectations come from.** Every expected number is in the task's `protected_relations[].measure`, written by a person from the fixture's physical definition. Gold is never the record's dependency closure (a test checks one case).
- **Checks.** `support_contact`, `vertical_clearance`, `non_interference` (grid sampling of the boxes' overlap with ray-parity containment), `aperture` (opening extents measured from the host mesh, plus rays through and beside it), `bbox`, `within`, `geometry_unchanged`, `column_row`, `stair` (rises, goings, top, landing contact, headroom under the slab).
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
- One arm (ArchFlow) for now; the baselines and repeated timing come with the next slice.
- A hostless revision rebuilds the whole model from the file's state: the file does not carry the previous exact STEP, so shapes are not reused across files.
- A C-unbound file that another tool edited is accepted, and its rebuild silently discards the edit (`test_carrier`).
