# Modeling ↔ Physical camera slice — PARTIAL

This independent follow-up to draft #399 connects the existing Physical scene draft to the matching Modeling viewport. It does not finish #319 or establish D5 rendering quality. No merge, Issue closure, CLA acceptance, source replacement or history rewrite is included.

## Ownership and behavior

- `hub.shell` / `ProjectWorkspace` scopes a transient `CameraLink` to one project connection. `PhysicalWorkspace` still owns its existing scene draft; Studio's existing scene Save/CAS and project repository remain the only persistence path. No new camera/geometry/material schema or writer is added.
- Modeling reads its exact displayed registered asset. Native sources retain `modelSource`; external imports use their existing run + content identity, without inventing OCCT state or granting editing/AI-capture permissions. Different sources, stale scene geometry, loading/local unsaved geometry and unsupported units do not link.
- The viewport reports actual 3DM file units. Supported mm/cm/m/km/inch/foot values map to scene metres. Both views use the scene's output aspect: Modeling expands its surrounding field of view and shows the final render frame. Resizing reapplies that composition rather than publishing a camera edit.
- Controls publish during gestures; programmatic application suppresses feedback. Projection/up-axis changes refresh OrbitControls' cached axis transform. Physical remains the scene-save UI. Gestures update its dirty draft; users explicitly Save scene. The existing Cycles submission button requires a saved, clean scene; it does not auto-save a dirty camera. Dragging does not create a revision/job per pointer event.
- Opening Modeling initializes the retained Physical draft without displaying a second canvas. Hidden Physical suspends its WebGL rendering. Existing AI inputs/history and explicit source capture stay independent.
- Geometry revision remains the source geometry identity; scene revision changes on saved appearance/camera updates; fixed engineering drawing revisions depend on geometry/recipe rather than the active presentation camera. An external asset identity is not an OCCT working-state claim.

## Executed tests and reproduction

Use the repository's Python API/OCCT environment and Playwright Chromium:

```
PYTHON=<api-python> PLAYWRIGHT_MODULE=<playwright-module> PENGUIN_TEST_GLB=<original-penguin.glb> node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs --camera-link-only
```

Omit the filter for the complete Render browser suite. Without `PENGUIN_TEST_GLB`, the imported camera scenario uses the authored architectural facade fixture and must not be described as Penguin acceptance. Tests use real Runtime, retained imported assets, OCCT output and production workspace components; only the AI adapter is offline. Camera observations report the actual rendering camera; they cannot set it. API reads verify saved state. Small authored facade geometry is a component-level architectural regression, not a full-building acceptance test.

Original input SHA-256 remains `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`. Temporary project copies receive the test changes. The original Penguin project, GLB/reference images, historical outputs and running user Hub remain untouched. Large GLB/3DM/.blend/background assets are not committed.

Final local source-tree results (2026-09-29):

- Full Render browser: **18/18 PASS** in one final run (`browser.log`), including existing AI/Board/late-read/Working Head/cold-Hub checks. The original assertions remain.
- Both directions: actual OCCT building component, original Penguin GLB and architectural facade. Modeling orbit/pan/dolly; Physical orbit while held updates hidden Modeling; perspective/orthographic FOV/height/target and Top/Front/Right/Isometric/Perspective presets; output-gate math and resize; whole-page refresh; two actual Runtime processes stopped/restarted; direct reopen into Modeling; independent retained scenes and unchanged fixed engineering drawings.
- The held actual Save response regression passes: earlier acknowledgement cannot replace a newer camera or clear its dirty state. A subsequent Save persists that camera.
- Web tests: **439 PASS / 2 SKIP** (52 Hub + 387 workspace; 389 workspace tests total). The two existing skips require `ARCHFLOW_PREVIEW_3DM` for native material/hidden aperture fixtures; no skipped test counts as PASS.
- Focused camera/fit/projection math: **24/24 PASS**. Build/typecheck and architecture (580 files): **PASS**. Commit write scope is checked after committing, recorded in the PR.
- Exact-head required CI results are separate and recorded in the PR; this Render browser script is not executed by existing CI workflows. Backend suites and packaged native lifecycle are not rerun locally for this frontend slice.

Evidence: `camera-native.json` contains actual rendered camera/matrix/viewport readbacks, saved scene responses, preset comparisons and late-save before/newer/after observations. `camera-imports.json` contains the original-input hash, both projects' camera and complete cold scene/drawing readbacks. `penguin.png`, `architecture.png`, `native.png` are real UI screenshots. `cold-trace.json` records project-specific preparation requests/responses and final project bytes. `environment.json` records the local runtime; `manifest.json` hashes this bounded evidence set. Logs preserve injected resource failures and temporary connection errors while intentionally stopping Runtime processes; zero console warnings is not claimed.

## Failed attempts retained

- Initial PR #477 CI on `ee8c44b8` failed Candidate review: 18 unexpected `GET /api/render/scene` reads in the tree fixture, which has no real Modeling viewport. `failure-ci-candidate.log` retains the Linux job. The shared link object's existence wrongly activated hidden Physical reads. The follow-up gates reads on an attached Modeling viewport or visible Physical, keeping cold camera restoration and all original tree assertions. This is a product activation fix, not a relaxed fixture. The original camera-refresh PNG comparisons in that failed job were zero-difference.
- After this fix, `candidate-retest.log` passes the unchanged tree regression; `browser-retest.log` passes all 18 Render scenarios using the original Penguin and facade; `unit-retest.log` has 439 PASS / 2 existing SKIP; `typecheck-retest.log` and `architecture-retest.log` pass. `camera-native-retest.json`, `camera-imports-retest.json` and `cold-trace-retest.json` retain the new actual readbacks. The initial local candidate attempt lacked the external Playwright module (`failure-local-playwright.log`); supplying the already-installed module resolved setup without dependency changes.

- `failure-initial-types.log`: the view-reader callback widened a source-state union; explicit return typing fixed the compile error.
- `failure-controls-type.log`: rebuilding controls for both projection types needed the existing common OrbitControls type, not inferred perspective-only controls.
- `failure-fixture-url.log`: an initial test routing change stripped Vite's `?url` query, breaking the PDF worker module. The middleware now examines a pathname without modifying module requests. No assertion removed.
- `failure-hmr-interference.log`: editing sources during the browser test triggered a page reload/reset. Later runs fixed source contents for their duration.
- `failure-empty-modeling-base.log` / `failure-invalid-working-base.log`: old Render-only fixtures had no authored Modeling base. Attempting to Continue their external imported run was correctly refused. The test now prepares the normal empty Modeling workspace through the existing API and opens the registered import read-only. It does not invent state or weaken the refusal.
- Inspection also found the product link rejected registered external imports because they have no native `modelSource`; the new exact displayed-asset identity fixes that without changing native edit/capture contracts. The later real Penguin test verifies this path.

- `failure-save-wait.log`: a new test used already-current status as completion of a new Save. It now waits for the actual PUT response and keeps the same position comparison.
- `failure-late-save.log`: a real product race reproduced with the actual PUT reply held: a newer Modeling dolly was replaced by the earlier saved camera. Physical now retains the acknowledged CAS base while preserving later local edits and their dirty state.
- `failure-cold-hub.log`: the old cold-Hub test advanced after the first changed file, while author initialization was still writing its temporary files. The focused diagnostic passed without a product change; the regression now additionally waits for each concrete project-specific Modeling preparation response before switching, retaining all byte and request-count assertions.

- `failure-concurrent-drawing-read.log`: a GET list during the still-running generation POST returned `RUN_NOT_FOUND` for the newly created mesh-view run. The completed directory retains its run manifest. Normal UI-generation regression now awaits the actual POST reply before checking the same four/current/stale assertions; it does not swallow read errors. **Concurrent listing during creation remains an observed reliability failure, not a camera fix or a #327 PASS.**

## Coverage boundaries

| Issue | Implemented and verified in the stack/slice | Not completed or not verified here |
| --- | --- | --- |
| #319 | Retained scene and explicit mesh drawing update from earlier stack; Working Head source integration from #391; continuous Physical controls from #399; this slice adds actual source-bound bidirectional camera projection | Full Board mesh-engineering association and artifact classification; fresh Cycles camera-freeze/job-history acceptance; whole-building/furniture acceptance; current-main integration |
| #321 | Earlier #372 generic mesh/selection-based named regions and two-project save/reopen; no default Penguin menu | Chat material edits, candidates/accept/undo, precise boundary diagnosis, comprehensive UV/material/export fidelity. Neutral exported 3DM is not a complete Penguin appearance delivery |
| #324 | Existing selected registered image references remain in the shared scene | Full upload/replace/undo and cross-scene resource lifecycle; HDR/EXR, transparent output, composition rules, DOF/cast shadows and D5 quality |
| #327 | Earlier black-model fixes, bounded conversion readbacks and #339 missing/unreadable retained-file diagnostics are retained | Comprehensive preflight/use admission, failure isolation, retractable repair, historical derivative recovery, full attribute/normal/unit/consumer matrix; historical background-only cause not established |

The 2026-09-29 #319 owner comment records that #330 now conflicts with main and requires integration/CLA confirmation; it proposes scene/camera → #218 and mesh drawings → #244, pending owner decision. Existing main native-drawing work is not reimplemented here. This slice continues the already-authorized camera work in the draft stack; it neither decides the successor ownership nor claims the stack is merge-ready.

Fresh Cycles/live paid AI and furniture are NOT TESTED in this slice. Millimetre/gate math has direct unit coverage; the actual browser fixtures use retained metre-based model exports, so other unit imports are not a browser PASS. The old 'only background' screenshot is not diagnosed by these newly visible fixture models. Full product result remains PARTIAL even when listed checks pass.

Rapid project switching/cancellation while first-time Modeling initialization is still writing is NOT TESTED by the completed-initialization cold-Hub check.
