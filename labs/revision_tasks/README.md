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
| `test_*.py` | Task-file validity and freezing; the oracle on a real F3 candidate and on eight wrong geometries. |

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

## The oracle (`oracle.py`)

- **What it reads.** Each object's name (identity) and mesh, from the exported `.3dm`. No user strings, document strings, layers or state; `reads_metadata` is always false.
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
