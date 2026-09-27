# GH-319 — Working Head to Physical and mesh drawings

Status: PARTIAL. Independent slice on PR #372 (`0d4d720e20f373fa3fbd4957ac1aed616f380c2b`), stacked on draft #330. No merge or Issue closure. The user's original Penguin model/project and earlier evidence remain unchanged.

## Behavior and owners

Before this slice, Physical and its four mesh views required a separate retained geometry selection even when Modeling already had a valid OCCT Working Head. `studio.artifacts.current_geometry` now reads the existing `studio.binding.resolve_working_source(..., "render")` when there is no explicit selection. Candidate creation alone does not move that source. Continue moves the existing Working Head; no additional geometry-selection record is written by the read.

Explicit retained imports stay pinned. This preserves an imported Penguin scene even when native Working Head changes. There is no new control to unpin an explicit selection in this slice.

The existing ProjectWorkspace design-tree reader provides the source hash to Render/Physical refresh. Its on-screen poll/focus/action lifecycle is reused. Local dirty scene edits are not overwritten by refresh; stale writes remain rejected by the existing API. Conflict-resolution UX is not completed here.

P036 still owns every persistent write. OCCT owns native design geometry. The existing runner 3DM is a tessellated visualization derivative. Here `geometryRevision` is the retained projection hash (the pre-existing scene contract), while `modelSource` retains run/state identity and `workingRevision` describes resolver metadata; these are not a new canonical design revision. Scene edits change scene revision. Drawing recipes retain the exact geometry hash and become outdated when it differs. Regeneration is explicit, not automatic. Old drawings and saved appearances remain retained. A stale appearance currently hides the preview until explicitly rebound; this is a known limitation, not successful seamless material/camera migration.

## Reproduction and evidence

Use the existing API environment with the `cad-occt` extra and API requirements. From `apps/archflow-studio/api`, set `PYTHONPATH` to that directory plus repository root, then run:

```
python -m unittest tests.test_working_geometry tests.test_render_scene tests.test_working_source
```

Set `PENGUIN_TEST_GLB` to an available original Penguin GLB for the actual asset test; absent that, the portable test uses the small repository source fixture and must not be reported as Penguin acceptance. `WORKING_GEOMETRY_EVIDENCE` optionally exports the numerical test record. The user file read in local execution has SHA-256 `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`. Large inputs and blend/background assets are not committed.

The architectural sample is the existing real OCCT portico-base/cornice fixture, not a whole building. Through ordinary proposal/candidate/Continue APIs, base height changes from 0.6 m to 1.1 m. The dependent cornice remains 0.3 m. Old total height: 0.8999999761581421 m; new: 1.399999976158142 m. New front drawing height equals the new mesh bounding height exactly (difference 0 m); difference from design total 1.4 m is about 0.000024 mm, consistent with this exported mesh's precision. This does not establish exact BRep drawing accuracy for arbitrary curved geometry.

Tests check: source reads do not change project data (excluding only repository-declared runtime lock paths); an uncontinued candidate does not replace geometry; Continue updates source; stale drawing generation is rejected; old four views remain outdated and four regenerated views are current; a fresh API application restores exact geometry and retained appearance; canonical HEAD is unchanged; explicit Penguin import is unchanged across native Continue and fresh application reads.

This is a real native parameter proposal/API acceptance test, not a full Modeling GUI parameter-edit acceptance. API cold reads use a fresh application, not a desktop process restart. The browser suite separately restarts the two region-test Runtime processes.

Browser reproduction from repository root with `PYTHON` and `PLAYWRIGHT_MODULE` configured:

```
node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs
```

Default execution retains every previous AI/Board/camera/region/cold-Hub assertion and adds the real OCCT scene/drawing scenario through ProjectWorkspace. `--working-only` is an explicitly focused replay, not the full result. AI provider tests use the existing offline adapter; no live image-provider acceptance or new Cycles/D5 acceptance is claimed.

## Failures retained

- Initial API invocation used the wrong Python tests package; a subsequent fixture used the wrong immutable project name. Both were setup failures.
- Correct pre-change regression returned GEOMETRY_REQUIRED / HTTP 409 in both new tests (`before-behavior.log`).
- First implementation compared all resolver metadata across candidate creation. Candidate recovery metadata legitimately changes `workingRevision`; the assertion now independently compares exact modelSource, geometryRevision and mesh data, preserving the requirement that candidates never take over without Continue.
- Browser fixture `/native` collided with its API proxy prefix, so the page never mounted. The fixture page/module use `/working` while its API remains `/native`. One intermediate route correction was incomplete and also failed. Neither was suppressed.
- After fixing that fixture, automatic refresh still failed: `onHeadMoved` only fires on UI actions, not polled source changes. Render now also tracks the existing design-tree source hash. The original stale-state assertion remains; no tolerance or screenshot baseline was weakened.

## Remaining coverage and next slices

| Issue | Implemented and tested in this slice/baseline | Unfinished or not tested |
|---|---|---|
| #319 | This slice: native Working Head fallback, candidate isolation, stale mesh drawings and explicit update, retained imports, fresh application reads. Baseline: scene persistence and isolated Physical camera controls. | Full Modeling GUI parameter edit chain; bidirectional Modeling/Physical gestures and perspective/orthographic linkage; Board engineering drawing association; safe old-material/region/camera rebinding; full building/furniture end-to-end acceptance. |
| #321 | PR #372 baseline removes global Penguin defaults and tests mesh-driven region CRUD and two-project persistence; same regressions retained. | Chat material edits, candidate acceptance/undo, material-group selection, fine boundary diagnosis, texture lifecycle and furniture coverage. |
| #324 | Existing image background scene consumers preserved. | Full user resource upload/select/replace/undo across projects/scenes, HDRI lighting, transparent output and composition rules. |
| #327 | No new reliability gate in this slice. Latest Issue comments report #336 split-normal/black-material fixes and #339 missing-resource/retry diagnostics merged into main. Those are separate deliveries, not this branch's tests. | Unified preflight/use-specific admission, normal/UV/PBR and real consumer coverage, fault isolation, historical derivative recovery and complete cross-model matrix. |

#327 admission contract to implement through existing conversion/report and consumer owners: report units/transforms, geometry/topology, normals/display, and materials/resources separately. Unknown units or geometry loss block dimensional consumers dependent on that derivative. Appearance-only loss can allow clearly marked neutral preview; it must not block drawings using independently verified geometry. Bind decisions to input hash, converter/options, derivative hash and consumer purpose; scene-dependent tasks also bind scene revision. Preserve prior valid state on failure and expose the same gate in UI and backend. This is the agreed boundary, not an implemented mechanism.

Next reviewable slices: bidirectional camera, then Board association under #319; separate #327 contract/gate work; #321 and #324 resource/material lifecycle work followed by joint architecture/furniture validation. Green checks attest only to executed checks, not completion of these Issues or D5 quality.

## Local results for this implementation

- API: 18 PASS / 1 SKIP (real Cycles host not configured), plus final exact-lock-contract native rerun 2/2 PASS with the user Penguin GLB.
- Full Render browser: 16/16 PASS, including native OCCT, actual Penguin import, architectural facade region isolation, two Runtime process restarts and real cold Hub navigation.
- Focused native browser: 1/1 PASS after fixing the fixture route and polled-source refresh.
- Build/typecheck: PASS. Architecture: PASS (580 files). See adjacent logs (build-log trailing whitespace normalized; messages unchanged).
- This Render browser suite is a local run; the existing required CI workflow does not invoke this script. CI verifies the new native API tests via the existing OCCT-enabled API suite; the private Penguin file is not available there.
- Expected injected HTTP errors, Runtime-stop proxy errors and existing ResizeObserver notices remain visible in the browser log.
- No fresh Cycles output, GPU quality comparison, desktop-native close test or live provider execution was performed locally in this slice. Current-head CI results belong in the PR, separately from local evidence.
