# GH-319 — continuous Physical camera without renderer replacement

PARTIAL prerequisite for bidirectional camera work, **not bidirectional camera acceptance**. Independent branch `codex/319-shared-camera`, based on PR #391 at `8aa4722777e50ce3f685e845a18c2fe3722e16d2`. #391's five required checks have now all passed on that exact head; its draft description records the job links and conditional release skips. This slice does not modify #330, #372 or #391.

## Reproduced behavior and change

PhysicalPreview reported the camera only on OrbitControls `end`. PhysicalWorkspace clones its draft on an edit, so the effect depending on that complete draft disposed/recreated the renderer, geometry, materials and textures after camera edits. That behavior cannot provide continuous camera values to a second viewport.

Extend existing `hub.shell` PhysicalPreview only: report changes during gestures, apply incoming camera values to the live camera/controls, and key resource construction by geometry plus non-camera scene content. Projection changes replace the camera only. Up-axis changes recreate controls to refresh OrbitControls' up-axis transform. Applying programmatic values suppresses callback feedback. Geometry/material/light/environment changes still follow the existing resource lifecycle. Persisted scene schema, writer and explicit Save scene operation are unchanged; gestures do not create persistent scene versions or render jobs.

No shared camera store, cross-workspace wiring, unit conversion or new design state is introduced by this prerequisite. Modeling still has its separate camera and does not follow Physical yet. The new branch name describes the intended follow-up area, not delivered full synchronization.

## Tests and evidence

`camera-before.log` preserves the actual failing assertion: scene camera inputs did not change before pointer release. The post-fix focused test keeps this assertion and additionally requires the exact same canvas node during/after rotation, pan, wheel zoom, projection and numeric camera edits. Existing save, workspace return, whole-page refresh and late-read protection assertions remain.

Reproduce from repository root with the existing Python API/OCCT environment and Playwright Chrome installed:

```
PYTHON=<api-python> PLAYWRIGHT_MODULE=<playwright-module> node apps/monkeyhub/web/workspaces/test/renderWorkspace.browser.mjs --physical-only
```

For the full suite omit the filter. `PENGUIN_TEST_GLB` supplies the user's original GLB read-only (SHA-256 `b8fac80bbbca66bd0ed0ab6f3544e46eab29ad840ceb21f71e9ab89d4e7a6c54`). Without it, the repository sample runs instead and must not be reported as Penguin acceptance. The region scenario applies the live-camera/canvas check separately to the imported Penguin and architectural facade, then explicitly reloads their saved scenes to preserve the existing region/persistence comparisons. Models remain real production imports; the facade is a small authored building-component fixture, not a whole-building validation.

The default full script retains all AI/Board/camera/region/Working Head/cold-Hub scenarios. AI uses the existing offline adapter. This browser script is currently local-only; a green required CI job does not imply it ran there.

Build/typecheck and architecture checks run for this slice. Existing ResizeObserver messages and injected resource errors remain in logs. Committed build-log whitespace is normalized, messages unchanged. No new Cycles render, live AI, desktop lifecycle execution locally, furniture model, D5 lighting or image-quality claim.

## Outstanding work

- #319: connect Modeling and Physical to one project/scene camera draft; adapt actual model units and render gate; prevent stale loads/frame-all resets; verify both directions for rotation/pan/zoom/projection, save/reopen and Cycles snapshot. Board drawing association remains separate. This prerequisite does not mark any of those complete.
- #321: generic mesh regions from #372 remain; chat edits, candidates/undo and detailed boundary diagnosis remain incomplete.
- #324: project/scene resources and texture/background replace/undo lifecycle, HDRI and transparency remain incomplete.
- #327: use-specific conversion admission and full attribute/consumer/fault-recovery matrix remain incomplete. No source geometry repair is performed here.

All PRs remain draft. No Issue is closed or merged. Next camera wiring must be its own reviewable slice with Penguin plus a non-Penguin model; full final coverage still needs architecture and furniture.

## Actual local result

- Before fix: FAIL, camera unchanged while pointer held (raw log retained).
- Focused camera regression after fix: 1/1 PASS.
- Full Render browser with original Penguin GLB and architectural facade: 16/16 PASS, including the two separate live-camera checks and existing Runtime restart/cold Hub/Working Head checks. No original assertions removed or tolerances changed.
- Build/typecheck: PASS. Architecture: PASS (580 files).
- Backend suite and broad frontend unit suite: NOT RERUN locally for this internal renderer-only change. Current-head CI results are reported separately in the PR, never borrowed from #391.
- Visual inspection confirms real Penguin geometry visible; existing coarse material-boundary jaggies remain, with no geometry edits to conceal them. Screenshots are UI evidence, not D5-quality acceptance.
