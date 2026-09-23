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
