# Render workspace reuse

The AI workspace consumes `studio.render` on the existing project Runtime.
Its results remain `SourceDocument` pages shared with Board; switching workspace
does not accept a model, issue a Stage, or cancel a running generation.

Reused from **YNNAP-HelloWorld (Panny)**, PR [#235](https://github.com/cogco1/MonkeyHub/pull/235),
fixed source commit `6f39e67116a2716c2dac70bb4ee3cf1b369afd9d`:

- `RenderResults.tsx`: authenticated document byte reads, object URL cleanup,
  image history, fit/zoom/pan and download. Adapted to Runtime job records,
  exact original-source comparison and Board page handoff.
- `render.css`: gallery, image viewer, toolbar and history layout. Adapted to
  existing Hub theme tokens and narrow workspace panels.

The following completed PR #235 sources stay at their original fixed commit for
the Physical integration in #218/#40. They are not copied as unused production
code or advertised as connected executors in this AI slice:

- `apps/monkeyhub/web/workspaces/src/workspaces/render/NativePreview.tsx`
- `apps/monkeyhub/web/workspaces/src/workspaces/render/LookDevelopment.tsx`
- `apps/monkeyhub/web/workspaces/src/workspaces/render/nativeRender.ts`
- `apps/monkeyhub/web/workspaces/src/workspaces/render/renderCamera.ts`
- `apps/monkeyhub/web/workspaces/src/workspaces/render/visualization.ts`

`RenderWorkspace` owns the AI/Physical mode choice. A future Physical consumer
can lazily mount its preview and look development there and provide actual
retained outputs to the same results surface. AI does not import the native
renderer, request a model, or infer a camera from an uploaded drawing.


## Modeling camera preview

Open a model in Modeling, choose a perspective or orthographic view, then choose
Render in the existing workspace rail. The Modeling view preview borrows the same
mounted Three.js scene (including the current local draft); it does not import a
second model, reparent objects, write project data, or create a Render scene store.
The project-scoped ProjectWorkspace connects a read-only viewport reader to Render.
The active camera is copied with position, quaternion, target/up, FOV, aspect,
near/far, zoom and projection matrix; orthographic extents are preserved too.
The preview fits its canvas inside the panel without modifying the source lens.
Hidden Modeling containers do not overwrite the last visible camera aspect.

Use **Adjust view in Modeling** to change the view, then re-enter Render. Model
replacement and local draft changes are read from the current scene. The reader is
released when Modeling unmounts; preview GPU resources are released when Render
is hidden. Scene resources remain owned by Modeling. No model means an explicit
empty state; entering Render first does not initialize a modeling project.

This is a read-only frontend preview, sampled at up to 10 fps while visible. It is
not a retained camera preset or an AI source-image capture. No Blender, D5, HDRI,
material editor or asset library is introduced. Transient Modeling display overlays
are part of the shared scene and may be visible. Current-session local .3dm files
remain subject to Modeling's existing retention behavior; this branch does not
include the separate external-model-ingest work.

Validation: renderView.test.ts compares shared scene/object identity, camera pose,
lens/clipping/zoom, projection matrices and projected points for both camera types.
viewportFit.test.ts covers hiding/returning without changing aspect or pose.
Manual local Hub verification used an authorized architectural .3dm, perspective
and Top orthographic views, Render entry and return to Modeling. The model is not
included in the repository or sent to an image provider.

## Integration handoff to Panny

Continue from the integrated Render workspace in this checkout. It combines
the Runtime, AI adapter and Hub workspace from PR [#257](https://github.com/cogco1/MonkeyHub/pull/257)
with the Modeling camera preview from PR [#258](https://github.com/cogco1/MonkeyHub/pull/258).
Do not replace this workspace with the older #235 shell: fold the Native preview
and look-development implementation into its Physical mode.

- **Workspace:** extend `RenderWorkspace.tsx` and reuse `RenderResults.tsx` for
  saved results, download, comparison and the exact-page Board handoff.
- **Runtime:** `studio.render` owns attempts and history in the existing project
  Runtime. `application/render_contract.py` defines the image-provider seam;
  `routes/rendering.py` and `transport/rendering.py` define its HTTP contract.
  See [Render protocol](../../../../../../../docs/PROTOCOL.md#render-image-attempts)
  for configuration, source binding and recovery behavior.
- **Persistence:** reuse P036 and `studio.artifacts.save_document`. Keep source,
  editable representation choices and output artifacts distinct. A render does
  not advance Design HEAD or accept a Stage. Legacy Native history is read-only.
- **Execution:** the current POST endpoint accepts only `server-image` adapters.
  `browser-native` and `host` identify future execution modes, not implemented
  dispatch paths. Native, Blender or D5 therefore need their actual executor
  wired through this owner; do not send host jobs to the image endpoint or add
  another project backend, launcher or persistence root.

The next physical slice should restore one exact model source with its camera
and supported look settings, render it through a verified executor, retain the
result through the existing artifact owner, and show it in the shared gallery.
Check cold reopen, changed/missing sources, independent representation changes
and no automatic replay after an uncertain external result. #223 tracks the
three-representation/invalidation experiment separately from the shared UI.

AI input is currently a registered PNG/JPEG page, with optional ordered reference
pages; it is not a full Rhino/SketchUp model-import implementation. Gemini has
offline transport and Runtime coverage, but real-provider acceptance is still
open under #253. Physical rendering and a new desktop release are not delivered
by this branch.
