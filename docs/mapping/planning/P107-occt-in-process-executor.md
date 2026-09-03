# P107 — OCCT in-process executor (the native lane)

**Status:** ready (not started)
**Lane:** productization and componentization
**Depends on:** P102 (the State Record binds programs), P103 (the patch selector names what changed)
**Retires:** Rhino as the *only* execution path. The Rhino executor stays as the stage-gate certifier and the
independent instrument; it stops being the price of seeing geometry.

## Why (decided 2026-09-02 night with Kaiwen)

Every measured export path costs 37 to 40 seconds because the Rhino COM start and save are the cost, not the
work. The project's geometry vocabulary — polyline-profile extrusions, polygon lofts, revolves, booleans of
polyhedra — falls inside the subset OCCT covers exactly, and the villa's "circles" are already 24-gons. An
in-process OCCT executor turns the vibe-modeling loop (edit record, see geometry, see relation checks) from
37 seconds into roughly a second for a full rebuild and milliseconds per object incrementally, which is what
makes the frontend lane (P108) worth building.

The independent-witness story is restated, not weakened: the analytic predictor (`expected_object_bounds`)
never used a kernel, so predictor/builder independence is preserved; OCCT is thirty years of someone else's
code; the readback discipline becomes "write STEP, cold-read with a fresh process and measure". Receipts carry
an evidence tier: `self_measured_cold_read` for the OCCT lane, `independent_instrument` for the Rhino gate.

## Mechanism

1. An in-process interpreter of `CompiledGeometryProgram` (no script generation): each operation kind maps to
   OCP/build123d calls; outputs tessellated for preview meshes and measured for bounds.
2. Bounds receipt compared against the analytic bounds exactly as the Rhino path does; same 3 mm tolerance,
   tessellator named in the receipt.
3. STEP export always (exact for this vocabulary); mesh `.3dm` via `rhino3dm` for Studio viewing; BREP `.3dm`
   only if later justified.
4. The patch selector (`select_patch_operations`) drives incremental re-execution per object.
5. The Rhino executor runs at stage gates: import the STEP or rebuild from the program, run the existing
   readback, certify. First runs of every project execute both lanes and compare (the same both-lanes
   discipline the patch oracle used).

## Acceptance

- [ ] The villa west band and Rocca records execute in-process and match their analytic bounds within 3 mm;
      wall-clock for a full villa rebuild and for a one-object patch reported as measurements, not claims.
- [ ] Both projects' OCCT-lane geometry matches the Rhino-lane geometry object by object on first
      certification.
- [ ] Receipts carry the evidence tier and the tessellator identity.
- [ ] STEP opens in Rhino with correct object identity (user strings preserved through the mesh `.3dm` path).
- [ ] Boolean edge cases (coplanar tool faces — the 1 cm overshoot class) have a tuning receipt, not silence.
- [ ] Full unittest suite and the architecture firewall pass.

## Do-not-do

No second program schema for the OCCT lane — the same `CompiledGeometryProgram` feeds both executors. No
weakening of the Rhino gate to make the lane look equivalent. No client-side geometry (that is P108's fence).

## Consequences

P104 (resident Rhino host) loses most of its value if this lands: one 37-second certification per stage gate is
tolerable. P104 is annotated accordingly and waits for this card's measurements before anyone builds it.
