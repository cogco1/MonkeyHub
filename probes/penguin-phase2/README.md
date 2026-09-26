# Penguin Phase 2 — reviewable partial baseline

Executed 2026-09-26 on Windows, Blender 4.3 / Cycles / OptiX, Three.js WebGL and
the real MonkeyHub Runtime/P036 project. Related to #319, #321, #324 and #327.
All four Issues remain open. **This is not whole-Issue acceptance or D5 quality.**

Original implementation commit `8afa896f968b489ff860cfee485d661dfb4c8324` is kept
unchanged in history. Integration commit `a1956c5b` merges main `e33fbf46`
(including #325's conversion repair and section-perspective API) and reconciles
attribute preservation; the PR does not claim unrelated upstream work. The
follow-up fixes one reproduced asynchronous scene-read race and adds regression
coverage and these review artifacts. It adds no new Penguin-specific feature.

This report narrows and supersedes the PASS wording in the preserved local
`validation/phase2/Penguin-Phase2-Validation-Report.md`. That original report,
commit and delivery files have not been overwritten.

## Owners and versions

| Component | Existing owner / responsibility |
| --- | --- |
| `model_formats.py`, `model_providers.py`, Blender adapters | `adapters.cad_execution`: bounded conversion, projection and independent native readback; no project writer |
| `geometry_sources.py`, `mesh_drawings.py`, dependency reader | `studio.artifacts`: retained source selection, existing SourceDocument recipe and staleness |
| `mesh_views.py` | `runtime.drawing_elevation`: projections and dimensions from actual mesh vertices |
| Render routes/DTO, `render_scene.py`, `physical_render.py`, Physical UI | `studio.render`: editable visualization scene, immutable jobs, Three.js/Cycles consumers |
| `record_kinds.py` | Existing P036 record registry; P036 remains the sole persistence writer |

`StudioGeometrySelection@1` identifies retained source bytes; their SHA256 is the
imported `geometryRevision`. It is not a new copy of design geometry and not an
automatic OCCT Working Head binding. `StudioRenderScene@1` stores a geometry
reference plus materials, regions, camera, lights, environment and quality; its
canonical content digest is `sceneRevision`. Scene writes use expected-revision
checks. A `StudioRenderJob@3` pins both revisions; older @1/@2 jobs remain readable.
Drawing recipes bind geometry revision, projection axes, dimensions and drawing
revision. Source changes mark old drawings stale; regeneration creates retained
new documents. Feature references without stable CAD identities stay unresolved.

The old and integrated converters have different version keys (`mesh/2` and
`mesh/3`). The integrated adapter retains main's diagnostic winding/normal repair,
vertex colors and neutral fallback while retaining this branch's UV/PBR factors.
**Repair is not generally lossless or a complete #327 reliability mechanism.**
The actual Penguin readback shows zero changed vertex positions or face indices.

## Strict acceptance boundary

| Acceptance | Result | Actual execution / evidence | Limits / remaining work |
| --- | --- | --- | --- |
| Original GLB import | PASS | Production UI/API import; retained SHA in [conversion data](evidence/production-quantitative.json) | This static asset only; transformed/animated/skinned GLB unsupported |
| GLB → 3DM | PASS | Independent rhino3dm reopen, 1 mesh, 58,790 vertices, 19,600 triangles; both [original](evidence/production-quantitative.json) and [integrated readback](evidence/conversion-readback.json) | Both exports are **neutral display models**, without the scene's colored Penguin appearance |
| Geometry consistency | PASS | Vertex max error 0 m; identical triangle indices; normal minimum cosine >0.99999999999999 | Not lossless CAD/texture/hierarchy interchange; no source rewrite |
| Retained geometry → Three.js | PASS | Actual source mesh used by Physical; [product screenshot](evidence/08-review-physical-before.png) | Modeling shows a registered 3DM derivative; automatic OCCT Working Head binding **not implemented** |
| Physical preview controls and persistence | PASS | Real architectural 3DM browser fixture: orbit, right-drag pan, wheel dolly, orthographic mode/scale, target, save, workspace return, full reload; [browser log](evidence/browser-physical-rerun.log) | Does not prove a shared Modeling camera; large-model performance not measured |
| Modeling ↔ Physical camera | FAIL | Modeling Front orthographic leaves Physical at perspective [1.3,-2.3,.75]; Physical position/target/ortho edits leave Modeling front unchanged. [camera checks](evidence/camera-checks.json), [before](evidence/09-modeling-front.png), [after](evidence/10-modeling-after-physical-camera.png) | Separate camera owners; live bidirectional protocol **not implemented**, remains #319 |
| Three.js visual quality | PARTIAL | PBR factors, lights, photo environment/background and ACES tone mapping | No preview cast shadows, HDR/EXR loading or DOF; ACES vs Cycles AgX is not pixel-identical |
| Three.js/Cycles scene material agreement | PASS | Every triangle assignment matches native Blender; [counts](evidence/three-cycles-material-consistency.json) | Scene-only appearance; **not written into Penguin-production.3dm** |
| MonkeyHub → Blender / Cycles | PASS | Real retained job `render-109bcee53bfd4d9ea8d241bfa7318f7f`, 1000×1200, 128 samples, denoise, OptiX; cold native readback: [data](evidence/cycles-readback.json), [image](evidence/Penguin-Cycles-Phase2.png) | One Phase 2 output; not four newly accepted compositions or D5 quality |
| Photo background chain | PASS | Registered Antarctic image enters shared scene, Three.js and Cycles | 2D photo, no depth/contact with terrain; not HDRI lighting, ground shadow or DOF acceptance |
| Four geometry drawings | PASS | Front/side/top/isometric generated from mesh, [SVG outputs](drawings/original/front.svg) and [measurements](evidence/drawing-quantitative.json) | Silhouette views, not full hidden-line/internal-edge CAD drawings |
| Imported revision → drawing invalidation/update | PASS | Explicit 1000→1100 mm **test-copy replacement**; four stale → four regenerated current; source original restored | Does **not** prove editing OCCT parameters in Modeling automatically updates drawings |
| Drawing accuracy | PASS | 8 view/variant checks: dimension error 0 m, max SVG extent rounding 0.055563 mm, max displayed-label rounding 0.000438 mm | Measurements of this imported mesh/recipe only |
| Board drawing association | PARTIAL | Board loads retained pages: [screenshot](evidence/11-board-loaded.png) | Board's engineering Side current/stale/regenerate/reopen UI chain NOT TESTED; `modelSource` remains null and recipe binding does not prove Board association. A Cycles side PNG is not a drawing |
| Drawing workspace current model | FAIL | [Product screenshot](evidence/12-drawing-workspace.png): no retained working state; generation controls disabled | Actual Working Head binding remains #319; Physical's four-view list is separate from this missing integration |
| Project scene/drawing persistence | PASS | Original two cold reopens [data](evidence/persistence-comparison.json); follow-up [cold reopen](evidence/review-persistence.json) | Limited to retained imported geometry/scene/drawings; not all MonkeyHub state |
| Game-ready asset | PARTIAL | Original has normals, no UV, materials or PBR textures; source geometry unchanged | No optimized asset/rigging/topology certification in this PR |

### Camera checks required by the latest #319 comment

| Operation | Physical alone | Live Modeling ↔ Physical |
| --- | --- | --- |
| Orbit | PASS, real browser pointer drag | Not implemented; per-gesture cross-workspace orbit NOT TESTED |
| Pan | PASS, real browser right drag | Not implemented; per-gesture cross-workspace pan NOT TESTED |
| Dolly/zoom | PASS, real browser wheel and orthographic height | Not implemented; cross-workspace zoom NOT TESTED |
| Perspective/orthographic | PASS, saved and cold page reload | FAIL: actual two-direction product check above |
| Presets, FOV, target/up, output framing | Basic scene fields and Cycles camera readback only | Full preset/FOV/up protocol and 16:9/4:3/1:1 multi-pane framing NOT TESTED |
| Return to workspace | Physical saved state PASS | Latest Modeling camera restoration into Physical not implemented |

Do not interpret missing per-gesture cross-workspace evidence as PASS. The actual
negative projection/position/target check establishes the missing shared camera,
not a complete measurement of every requested interaction.

## Regression execution and failures

All logs below are actual runs; only local filesystem prefixes were replaced with
`<repo>`, `<temp>` or `<project>` for publication. Assertions were not removed.

| Run | Result / evidence |
| --- | --- |
| Original modified `renderWorkspace.browser.mjs` on 8afa896f | **13/13 PASS**: [log](evidence/browser-regression-initial.log); explicit AI selection added for default Physical; original assertions retained |
| Added Physical case, before fix | **FAIL**: late scene read reset newly edited target X .75 to saved .25; [log](evidence/browser-physical-initial.log) |
| Same full browser file after fix | **14/14 PASS**, including original AI paths and cold real Hub; [log](evidence/browser-physical-rerun.log) |
| Conversion merge initial | **20 passed / 1 failed**: output loss-description no longer contained upstream's promised wording. Restored accurate `neutral display material` description, kept assertion; [log](evidence/merge-conversion-initial.log) |
| Root conversion / Blender projection / record kinds rerun | Initially **43 passed, 2 optional Blender skips** because the executable variable was absent: [log](evidence/root-tests-review.log). With the installed Blender configured, **45/45 passed, zero skips**: [log](evidence/root-tests-with-blender.log) |
| Affected API suite after integration | **55 passed, 4 subtests passed**, real Blender; [log](evidence/api-tests-review.log) |
| Camera unit tests | **3 passed**; [log](evidence/camera-unit-review.log) |
| TypeScript + Vite build | PASS; [log](evidence/frontend-build-review.log); chunk-size warnings remain |
| Generated API clients | Initial generation failed on Windows cp1252 console path encoding; rerun with `PYTHONUTF8=1` passed; final `api:check` PASS: [log](evidence/api-check-review.log) |
| Architecture | First check rejected executable code under `probes/`. Moved the reproduction helper into `tests/`, kept probes data-only; rerun PASS: [log](evidence/archcheck-review-rerun.log) |
| Historical changed-commit scope gate | **FAIL**: preserved `8afa896f` says `(#319)` rather than the required `GH-319` work claim. [log](evidence/archcheck-changed.log). Its 28 non-shared paths are claimed by the GH-319 registry, but the history parser cannot attribute the commit. Original SHA is preserved as requested; checker/policy is not weakened. The PR remains draft pending the repository's decision on this historical gate. An initially omitted probe scope in the follow-up was explicitly registered. |

Browser console 503/corrupt-image/lost-response failures are deliberately injected
by the existing assertions; 404 resource noise and a Vite ResizeObserver warning
are retained, not filtered out to obtain green. No `pageerror` remained in the
asserted browser array. The image adapter is offline, labeled synthetic in its
own fixture; **live paid AI provider is NOT TESTED**. It is used for AI-path
regression, never as Penguin render evidence.

Manual product regression switched Modeling → Physical → Modeling → Physical →
Board → Drawing → Render, then refreshed and stopped/reopened the project.
Board dynamic-module loading failure was not reproduced in this run. A loaded
Drawing page with unavailable Working Head is recorded as a feature limitation,
not silently counted as successful drawing integration. The old “background
only” incident was not reproduced after restoring/reloading the saved camera;
its original root cause is **UNRESOLVED**. No delete/reimport/automatic fit was
used to declare it repaired. Adversarial slow image/project-switch races and
full three-model camera parity remain NOT TESTED.

During the follow-up reopen, the old Hub rejected a new worker with
`SERVICE_IDENTITY_MISMATCH` after the source merge. Restarting the same Hub with
the updated source is required. This operational failure and subsequent worker
identity/readback are recorded in [cold reopen evidence](evidence/review-persistence.json).

## Reproduction

Use an installed supported runtime with repository dependencies. From repo root:

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD;$PWD/apps/archflow-studio/api"
$env:ARCHFLOW_BLENDER_EXECUTABLE='<installed Blender executable>'
python -m unittest tests.test_model_formats tests.test_blender_projection tests.test_record_kinds -v
python -m pytest apps/archflow-studio/api/tests/test_rendering.py apps/archflow-studio/api/tests/test_representation_dependencies.py apps/archflow-studio/api/tests/test_model_exports.py apps/archflow-studio/api/tests/test_render_scene.py apps/archflow-studio/api/tests/test_runtime.py -q -p no:cacheprovider --basetemp '<new disposable directory>'
$env:PYTHON='<same configured Python executable>'
$env:PLAYWRIGHT_MODULE='<installed playwright/index.mjs>'
node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs
node --test apps/monkeyhub/web/workspaces/test/renderView.test.ts
node apps/monkeyhub/web/workspaces/scripts/check-generated.mjs
python tests/inspect_model_conversion.py '<Penguin GLB>' '<new output directory>'
python tools/archcheck.py
python tools/archcheck.py --changed origin/main
```

The browser test needs installed Chrome (`channel: chrome`), Playwright, Vite/
frontend dependencies, and a built Hub (`npm run build` in `apps/monkeyhub/web`).
It creates isolated real Runtime/P036 projects and uses the existing architectural
3DM fixture. Tests can run without private Penguin/background assets; the final
conversion command needs the user's original GLB, identified in the manifest.

Product steps: open the retained Penguin project, select Render/Physical; inspect
geometry/scene revisions; change and save visualization fields; submit Cycles;
inspect the immutable job/readback. Generate four views. In a **copy** import the
110%-height GLB with the expected old revision; observe stale drawings and update
them; compare dimensions. Restore the original source selection, close project,
confirm worker exit, reopen and compare stored scene/drawing records. Modeling's
neutral 3DM is a derivative. These steps do not involve an OCCT parameter edit.

## Follow-up boundaries (issue bodies and latest comments reviewed)

| Issue | Covered here | Not covered / next owner work |
| --- | --- | --- |
| [#319](https://github.com/cogco1/MonkeyHub/issues/319) | Persisted imported geometry selection/scene, mesh drawing revision update, physical Cycles projection, read-race fix | OCCT Working Head/actual modeling integration; live bidirectional camera; Board engineering drawing association. Read [Board comment](https://github.com/cogco1/MonkeyHub/issues/319#issuecomment-5843050462) and [camera comment](https://github.com/cogco1/MonkeyHub/issues/319#issuecomment-5843313615) |
| [#321](https://github.com/cogco1/MonkeyHub/issues/321) | Basic scene material/region editor; shared triangle assignment | General inspect/select/revision-bound patch/candidate accept/reject/chat loop not implemented. Existing `penguinSuggestions` remains a **known hardcoded baseline limitation**, not general semantic selection. Region lists must derive from actual model structure/user selection; no new Penguin menus in this follow-up. Boundary aliasing root cause not diagnosed: neutral/material-ID/wireframe controlled comparisons and non-Penguin validation remain. Read [regions](https://github.com/cogco1/MonkeyHub/issues/321#issuecomment-5843276011) and [boundary diagnosis](https://github.com/cogco1/MonkeyHub/issues/321#issuecomment-5843315514) |
| [#324](https://github.com/cogco1/MonkeyHub/issues/324) | Existing registered PNG/JPEG can be selected in a scene and consumed by both renderers | Unified project upload/select/replace resources, project isolation/same-name/slow-upload validation, missing-image recovery, fit/fill controls, transparent output and HDRI behavior remain; selecting an existing document is not complete resource UX. Read [resource isolation](https://github.com/cogco1/MonkeyHub/issues/324#issuecomment-5843279725) |
| [#327](https://github.com/cogco1/MonkeyHub/issues/327) | Normal/material fallback, upstream diagnostics, quantitative readback and versioned conversion | Full preflight/purpose gates/failure quarantine/reversible repair/provenance/cache invalidation and prevention system not implemented; legitimate black materials and complex transforms/seams need wider cases. Issue had no comments at review |

The architectural fixture adds non-Penguin regression coverage for basic controls;
it does **not** satisfy the full general-capability acceptance across Penguin,
another character and an architectural model.

## Artifact policy and availability

This directory follows existing `probes/blender-projection-v1`: promote bounded
framework-generated evidence, not the live project. [Manifest](manifest.json)
records source and retained output identities/sizes, plus hashes of the published
copies. Screenshots are genuine browser captures; render pixels come from Cycles.
PNG text metadata is removed from promoted images, with decoded pixels unchanged.
No mock image is used as Penguin success evidence.

The original GLB, Reference photos and `.blend` remain in the user's existing
P036/external delivery envelope; they are not added to Git or a newly invented
artifact service. Their third-party redistribution rights were not established.
Reviewers can access this report, logs, measurements, screenshots, render and SVGs
through the PR. Exact Penguin rerender requires separately authorized access to
the original inputs; that reproducibility limit is explicit. No CLA acceptance
is asserted on behalf of the contributor.
