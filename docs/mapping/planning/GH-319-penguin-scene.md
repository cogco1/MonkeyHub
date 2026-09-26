# GH-319 — Penguin Phase 2

Issue: https://github.com/cogco1/MonkeyHub/issues/319

Extend, do not replace, the existing owners:

1. `adapters.cad_execution`: production mesh conversion preserves shading attributes,
   winding and neutral fallback. Tests include indexed and triangle-soup inputs.
2. `studio.artifacts`: an explicit current geometry binding names retained export
   source bytes (or a registered model), not a second mesh. Content SHA is the
   imported geometry revision. P036 retains the selection and its history.
3. `studio.render`: immutable scene revisions in P036 contain only geometry refs
   and visualization state. Three.js and the Cycles adapter consume the same scene.
   Saving requires the expected scene revision. Jobs pin geometry and scene revisions.
4. `studio.artifacts` / `runtime.drawing_elevation`: mesh views keep their geometry
   binding, view and measurements in their existing SourceDocument recipe. The
   representation dependency reader exposes stale status; regeneration computes
   measurements from new source bytes. No triangle is treated as a semantic feature.
5. Render's Physical tab exposes materials/regions, lights, environment, camera,
   quality, drawings and Cycles submission. Existing project Runtime owns execution.

New record kinds are necessary for geometry *selection* and editable scene history:
neither a board arrangement nor an image attempt can represent these contracts.
They use existing P036 run records and its project guard, not a new filesystem store.
Imported replacement is explicit; renderer edits never change source vertices.

Acceptance: automated conversion, conflict/stale/reopen/provenance tests, then
Penguin GLB through the production API and visible frontend, actual Cycles job,
geometry replacement, drawing update and full runtime close/reopen.

Product acceptance executed 2026-09-26 on the real Penguin project:
- Original GLB production import: 58,790 vertices / 19,600 triangles; vertex
  difference 0; winding retained; neutral 3DM visible in Modeling after reopen.
- UI saved one editable scene, then submitted a real OptiX Cycles job. Native
  cold Blender readback verified camera, materials, every face assignment,
  lights, packed background, exposure and quality against that scene.
- Explicit height +10% test revision made all four original drawings stale;
  UI regeneration changed front/side dimensions from 1000 to 1100 mm, retained
  unresolved eye-center references, and survived a stopped/restarted Runtime.
- Original source restored; final scene and drawing records compared exactly
  across a second cold reopen. No source file or Design HEAD was rewritten.
- 55 API tests plus 4 subtests passed; 39 root tests (2 optional skips);
  3 camera tests; frontend build and architecture gate passed.

Remaining Phase 2 limitations: mesh-only orthographic silhouettes, coarse region
masks, no preview cast shadows/HDR/EXR/DOF, no low-sample Cycles preview,
no automatic OCCT Working Head binding, and no chat tool access to scene editing.
The product workflow passes for the retained Penguin import; D5 presentation
quality and those broader controls remain PARTIAL. Keep #319 open for that scope.
The detailed report and real artifacts are in the user's explicitly supplied
Penguin test/validation/phase2 directory; no machine-specific project data is committed.
