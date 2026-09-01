# P083 — Parthenon Rhino Z-up correction

- Origin: user visual defect report, 2026-08-29
- Status: Done
- Depends on: P082

## Goal

Correct the Parthenon Architectural IR and 3DM adapter from the accidental
Y-up convention to Rhino World XY with Z-up, then retain the correction as a
successor run rather than replacing the P082 evidence.

## Boundaries

- The project remains `parthenon-reconstruction` in the existing workspace.
- The faulty `reconstruction-001` run remains immutable evidence of the defect.
- `reconstruction-002` / `research-002` retain the timed-out repeat-fetch
  attempt and its blocker.
- The completed corrected run is `reconstruction-003`, with research run
  `research-003`, reusing the four exact retained source snapshots from run 001.
- All corrected outputs remain `HOLD`; an axis correction does not grant design
  acceptance or canonical-write authority.

## Acceptance

1. Architectural IR declares `RhinoWorldXY`, plan axes X/Y, and up axis Z.
2. Every stage gate checks a grounded Z-up bounding box; the long plan axis is
   Y, width is X, and building height is Z.
3. Exterior-column geometry is measurably vertical along Z, not Y.
4. `reconstruction-003` binds the exact predecessor progress snapshot and an
   explicit coordinate-axis correction receipt without modifying run 001.
5. Four corrected stage packs compile COMPLETE and remain HOLD.
6. The relocation anchor binds the corrected progress snapshot and final 3DM.

## Write scope

- `tools/run_parthenon_reconstruction.py`
- `tests/test_parthenon_reconstruction.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Verification

- Offline first-run and successor-run regression tests.
- Headless coordinate-system, bounding-box, column-axis, unit, and object checks.
- Architecture firewall.


## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/exports/parthenon-progress-snapshot-dc7c1a2b9e52136eb5e3f627eb39b60b042fbb9904c4628d13f4d61267155bff.json
- Evidence: Rhino World XY Z-up bbox 34.48 x 73.10 x 19.38 m; min Z 0.0
- Evidence: final 3dm sha256 3992c65df16554ff2b22b5f2dc68df47bc98f0bcec715acf99f2b139ce407a5c
- Evidence: 5 tests PASS; ARCHITECTURE PASS (142 files)
