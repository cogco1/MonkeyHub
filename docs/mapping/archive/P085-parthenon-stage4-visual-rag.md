# P085 — Parthenon Stage 4 visual RAG candidate compilation

- Origin: user Stage 4 request, 2026-08-29
- Status: Done
- Depends on: P078, P079, P081, P084

## Goal

Open the Parthenon detail stage with locally retained Web images and compile
them into branch-bound element candidates with labels, spatial locations,
confidence, supported claims, and explicit non-claims before any detail
geometry is allowed to change.

## Boundaries

- The research run is `research-005`; its source and derived-image workspace is
  `runs/research-005/workspaces/visual-rag/`.
- Original binaries are also ingested into the project's content-addressed
  `objects/sha256/<prefix>/<digest>` store through P036. Records retain URL,
  retrieval time, media type, dimensions, SHA-256, source family, and usage
  note.
- Source copies use `source-images/<source-id>__<view-id>.<ext>`. Derived files
  use `derived/regions/<source-id>__rNNN__<tag>.<ext>` and
  `derived/overlays/<source-id>__labels.<ext>`.
- A photo or reconstruction drawing may support element existence, topology,
  relative position, visible morphology, or material condition. Perspective
  pixels do not authorize exact dimensions unless tied to a retained scale or
  independent measurement.
- This card compiles and selects candidates only. `reconstruction-005` remains
  unopened until its selected candidate set and non-claims pass the visual
  evidence gate.

## Acceptance

1. Every downloaded image has a local workspace copy, immutable artifact ref,
   retained source record, digest, pixel dimensions, branch identity, and
   provenance/usage note.
2. Every visual-region candidate declares pixel bbox, normalized bbox,
   element tag, host component, spatial-location statement, confidence,
   supports, cannot-support, and review state.
3. Invalid bboxes, absent sources, duplicate candidate identities, missing
   non-claims, or exact-dimension claims without a measurement basis fail
   closed.
4. The candidate manifest distinguishes measured drawings, reconstruction
   drawings, current-condition photographs, and museum-object images; source
   modality limits survive selection.
5. `research-005` retains the selected and parked candidates plus a Stage 4
   visual-RAG progress snapshot; no `reconstruction-005` model is created by
   this card.

## Write scope

- `archflow/capabilities/visual_evidence.py`
- `tools/run_parthenon_stage4_visual_rag.py`
- `tests/test_visual_evidence.py`
- `tests/test_parthenon_stage4_visual_rag.py`
- `probes/parthenon-reconstruction.anchor.json`
- `docs/DYNAMIC_MAP.md`
- `docs/mapping/`
- `governance/work_registry.json`

## Verification

- Pure visual-evidence contract tests, including invalid-region and
  perspective-exact-dimension negative cases.
- Offline bounded runner test and live `research-005` artifact/record readback.
- Architecture firewall.

## Stop conditions

- Stop before writing an image outside the declared research workspace or P036
  object store.
- Stop before treating a reconstruction image as measured fact.
- Stop before opening `reconstruction-005` without a compiled selected visual
  candidate manifest.



## Completion

- Completed: 2026-08-29
- Evidence: project://parthenon-reconstruction/runs/research-005/branches/idealized-periclean-original/records/visual-candidate-manifest-76b73f91e9ab2af8552a7fcf3abef9cd596702c0f96f78daffc6c1ebd00e0fb0.json
- Evidence: project://parthenon-reconstruction/runs/research-005/records/stage4-visual-rag-progress-de8d5a1b9694647c32aeb9e3ae21485357b28f47501eaebe0f81a8819d527c15.json
- Evidence: 10 tests PASS; ARCHITECTURE PASS (145 files); 8 local source images and 8 content-addressed objects read back; reconstruction-005 absent
