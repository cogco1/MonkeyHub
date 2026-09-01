# P086 — Parthenon Stage 4 visual-region localization gate

- Origin: user continuation after P085, 2026-08-29
- Status: Done
- Depends on: P085

## Goal

Refine the retained Stage 4 source images into real, reviewable element regions
before any detail geometry is opened.  Each region must preserve source
modality, branch identity, supported claims, explicit non-claims, confidence,
and an exact predecessor-manifest identity.

## Boundaries

- The source authority is the exact P085 manifest
  `visual-candidate-manifest-76b73f91...e0fb0.json`, not directory order or
  modification time.
- P086 appends immutable ROI evidence to `research-005`; it does not rewrite the
  P085 source files or either earlier manifest.
- Region crops live under
  `runs/research-005/workspaces/visual-rag/derived/regions/` and labelled
  review overlays under `.../derived/overlays/`.
- ROI coordinates are evidence-localization candidates, not survey
  measurements.  Every candidate continues to reject exact-dimension authority
  unless a separately retained calibration or independent measurement exists.
- `reconstruction-005` remains unopened throughout this card.

## Acceptance

1. All eight retained P085 sources are read back by exact source record and
   object digest; no unreviewed network expansion occurs.
2. The refined manifest contains 32 conservative ROI candidates with real pixel
   and normalized bboxes, labels, hosts, spatial locations, confidence,
   supports, cannot-support, modality, branch, scope, and review state.
3. Every ROI has a deterministic crop and each source has a labelled overlay in
   the declared speculative workspace; retained records include relative paths
   and SHA-256 digests.
4. Reconstruction drawings remain parked as hypotheses; temporary restoration
   equipment is not allowed to drive idealized Periclean geometry.
5. A current ROI selection record explicitly names the predecessor P085
   manifest and its superseded manifest, so downstream gates never infer
   currency from mtime.
6. No ROI claims an exact dimension, canonical project state is unchanged, and
   no `reconstruction-005` run is created.

## Write scope

- `tools/refine_parthenon_stage4_visual_regions.py`
- `tests/test_parthenon_stage4_visual_regions.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Verification

- Offline crop/overlay/manifest tests, including predecessor and bbox failures.
- Live `research-005` ROI artifact and record readback.
- Architecture firewall.

## Stop conditions

- Stop before inventing a bbox for an element that is not visually legible.
- Stop before deriving a metric dimension from an uncalibrated image.
- Stop before opening `reconstruction-005` without the exact current ROI
  manifest and a separate geometry-decision gate.


## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/runs/research-005/branches/idealized-periclean-original/records/visual-region-manifest-d3ff9f427bbe4126d8346ad3be18831b638ea8f08d18e3b007234d7e9a4da7bd.json
- Evidence: project://parthenon-reconstruction/runs/research-005/records/stage4-visual-region-progress-b96542e0f69fcf523f6149038a2bde5b7dab7f43f064b44ac68e0e6d3005d320.json
- Evidence: 32 ROI crops and 8 overlays read back; 14 selected, 13 parked, 5 rejected; 15 tests PASS; ARCHITECTURE PASS (145 files); canonical HEAD unchanged; reconstruction-005 absent
