# GH-321 — model-driven regions, independent follow-up to PR 330

Status: PARTIAL. Related to #321; does not complete that Issue. Base: PR #330 at `8e1388e20398e7171bb38eb3c87093dc2674ce0e`. The new branch is `codex/321-model-regions`, targeting `codex/319-penguin-scene` to keep this review independent and small. Do not merge or close any Issue as part of this delivery.

## Audit

PR #330 remains draft. Its product code offered `penguinSuggestions` on every Physical workspace: six fixed material IDs/appearances, seven region IDs and metre-space coordinates, plus “Suggest Penguin regions”. Applying it replaced the scene material/region/assignment lists. This was a real generic-path defect, even though creating a scene did not automatically apply the suggestion.

No separate GH-321 implementation PR was found in the repository search at audit time. Uncommitted code in the shared checkout was not treated as delivered implementation and was not changed.

## Bounded change

- Move the original suggestion to explicitly named `test/fixtures/penguinRegions.ts`; production has no reference or button for it. No model-name classifier or semantic region enumeration replaces it.
- New scene fallback ID is `neutral`, with neutral appearance. Readers still accept all existing saved material and region IDs; no migration rewrites Penguin data.
- The user chooses an actual source mesh; a new box region uses its measured bounds and recorded geometry revision. Unnamed meshes remain `Mesh N`; names are not inferred architectural semantics. Box/ellipsoid refinement remains the existing centroid-based selection mechanism.
- Add region naming and explicit source-mesh rebinding, preserve IDs across rename, retain existing material selection and deletion. Add material naming. Removing a region removes only the definition.
- Key the existing Physical editor by project, keeping selections, pending reads and image URL caches within that project lifecycle.
- EXTEND `hub.shell` and `studio.render`; immutable writes still use the existing scene CAS and P036 writer. No new geometry, scene or material authority. Only scene revision changes; geometry remains unchanged.

## Execution and evidence

See adjacent logs and JSON. Build/typecheck pass; region tests 2/2 pass; scene API 4 pass / 1 skipped (real Blender host not configured). Architecture check passes. Full Render browser 15/15 passes, including the pre-existing AI/Board/camera/cold-Hub assertions. Offline AI is not real provider acceptance.

The first additional browser run failed because `model-source-a.3dm` contains only two unrelated objects. The original assertion was retained; the test now imports a separately authored architectural facade fixture containing entrance door, window glazing and wall strips around openings. This is a bounded architectural model, not a full building acceptance. Raw failure log is retained.

The browser imports the user's original Penguin GLB read-only (SHA-256 `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`) into a disposable project via the production conversion and scene APIs. It restores sample regions, creates/renames/rebinds/deletes architectural regions through actual controls, assigns a separate material, saves, switches A→B→A, reloads the browser, gracefully stops both test Runtimes, starts new processes and compares complete saved scenes and revisions. Geometry responses remain exactly unchanged by region edits. The original Penguin project is not edited.

The default portable browser run uses repository geometry with the explicitly marked retained sample configuration. It does not claim CI contains the user's Penguin asset. Actual Penguin execution requires `PENGUIN_TEST_GLB`; `PENGUIN_TEST_REGION_CONFIG` optionally supplies its saved materials/regions/assignments. All original assertions run without filters by default. `--regions-only` runs only the Physical and new region scenarios for a separately reported targeted replay.

Initial API invocation from the repository root resolved the wrong `tests` package and failed before execution; the corrected invocation from `apps/archflow-studio/api` passes. Initial scope check rejected overlapping claims; the independent branch records a narrow handoff from the completed #330 baseline to GH-321. No gate was weakened.

Browser logs retain expected fault-injection 404/503, existing ResizeObserver notices and transient proxy failures while test Runtimes are deliberately stopped. Screenshots are real WebGL/UI captures, not replacement renderings. Large GLB/background/.blend files are not committed.

## Remaining acceptance — not solved by this PR

| Issue | This slice | Still incomplete / not tested here |
|---|---|---|
| #319 | Existing scene persistence and Physical regression retained | OCCT Working Head automatic scene binding; full bidirectional Modeling↔Physical gestures/projection; Board drawing linkage; complete cross-model workflow |
| #321 | Remove global sample defaults; explicit mesh selection; region rename/rebind/delete; material naming; two-project region/material cold persistence | Chat edit/candidate accept/reject/undo, region highlighting, material-group automatic suggestions, same-project multi-model references, texture isolation, fine boundary diagnosis, furniture and full-building acceptance |
| #324 | Existing scene resource consumers untouched | Upload/select/replace/undo resources across projects and scenes, HDRI/transparent output/composition; joint region-texture lifecycle acceptance |
| #327 | Geometry is not changed by this fix | Unified conversion admission/reporting, failure isolation/recovery, historical derivatives, comprehensive architecture/furniture conversion validation |

Priority remains #319 Working Head → bidirectional camera → Board drawing association; clarify #327 admission contract alongside that; then separate #321/#324 slices and joint acceptance. Every slice needs Penguin and one non-Penguin targeted case; final combined acceptance adds both architecture and furniture. Green CI proves only the checks actually executed, not automatic drawings, bidirectional cameras or D5 quality.

For the #327 contract follow-up, distinguish units/transform certainty, geometry integrity, normals/display and material/resource compatibility. Unknown units or geometry loss must block dependent dimensional deliverables; appearance loss may allow an explicitly neutral preview and must not block drawings from independently verified geometry. Bind findings and gates to the exact input/derived revision and intended consumer; this paragraph is a task boundary, not an implemented gate.

Exact saved Penguin replay: **2/2 PASS** (Physical regression + region/isolation). Original saved scene `06a5995bc2ad…`, sequence 7, was read without writes. Its materials/regions/assignments were used verbatim in the disposable test project. The body color `#202831` and roughness `0.72`, as well as all region IDs/masks, compare exactly after save and Runtime restart; see `original-config-comparison.json`. This is a copy-based preservation test, not an in-place modification of the user project. Full workspace suite: **377 PASS / 2 SKIP / 0 FAIL** (the two existing external 3DM-fixture tests remain skipped).
