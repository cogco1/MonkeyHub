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

## Integration handoff to Panny

Continue from PR [#257](https://github.com/cogco1/MonkeyHub/pull/257), branch
`codex/render-runtime`. It combines the Runtime, AI adapter and Hub workspace.
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
