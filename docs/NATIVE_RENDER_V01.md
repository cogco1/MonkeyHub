# MonkeyHub Native Render Workspace V1

Tracks [GH-216](https://github.com/cogco1/MonkeyHub/issues/216) and the current-model/camera handoff in [GH-218](https://github.com/cogco1/MonkeyHub/issues/218). Penguin geometry repair remains GH-217.

## Delivered sequence

1. **Project Visualization State.** Camera, saved cameras, material overrides, lights, environment and render settings are engine-independent values validated by `archflow.visualization`. Studio retains revisioned `ProjectVisualizationState@1` records through P036 in `studio-visualization`. Compare-and-swap rejects stale editors. Geometry and design HEAD are unchanged.
2. **Native Preview.** The Render workspace opens the exact retained model through the existing Rhino3dmLoader pipeline. Three.js owns a display scene with Orbit, Pan, Zoom and Fit. This component receives bytes and visualization callbacks, with no geometry/design command writer.
3. **Look development.** Perspective/orthographic camera, FOV, saved cameras, default and object-specific base color/roughness/metallic/opacity, editable lights and shadows, background/ambient lighting, exposure and output dimensions update immediately and autosave. An explicit Modeling camera transfer updates framing while retaining existing look development for the same model.
4. **Native final image.** A project-runtime request retains an immutable source/settings snapshot. The browser renders that snapshot to an independent multisampled floating-point WebGL2 target, applies ACES/exposure/sRGB through OutputPass, reads pixels and encodes a PNG at the requested dimensions. Preview uses the same color pipeline. The result is registered through the existing document persistence path and opens in History with zoom, pan and download.

The production Render API no longer imports or starts Blender, discovers its executable, creates `.blend` files or accepts the old `POST /api/render/jobs`. Existing GET job readers and previous PNG documents remain compatible. Optional historical Blender adapters/CLI experiments outside this workspace are not invoked by Native Render and are not expanded in this change.

## API and authority

- `GET /api/visualization`: saved state, revision and exact source reference.
- `PUT /api/visualization`: engine-independent state plus expected revision.
- `POST /api/visualization/source`: one certified model source, explicit local 3DM bytes, or retained legacy job source; optional captured viewport camera.
- `GET /api/visualization/source`: hash-verified immutable model bytes.
- `POST /api/render/native-jobs`: request UUID and saved visualization revision.
- `POST /api/render/native-jobs/{job_id}/complete`: snapshot digest and generated PNG.
- `POST /api/render/native-jobs/{job_id}/fail`: explicit rendering failure.
- Existing `GET /api/render/jobs`, single-job reads and document APIs serve history.

Models are limited to 32 MiB; output is 64–4096 pixels per axis, additionally bounded by GPU support. Completion validates PNG format, dimensions, exact request digest and idempotent content. It does not claim server-side proof of browser pixel provenance. Source bytes and visualization are retained in the job independently of subsequent edits. Imported models do not acquire a fabricated design-state association.

Native rendering currently runs in the open browser. Closing it abandons an in-flight job; after 120 seconds or runtime restart, unfinished work is reported as interrupted, never silently replayed. Explicit failures remain failures; there is no Blender/Rhino fallback.

## Verification

- Domain tests reject geometry/engine fields and invalid camera/material/light/output values.
- API tests cover state persistence across restart, stale revision refusal, immutable snapshots during later edits, PNG dimension/digest validation, idempotent completion, conflicting result refusal, interrupted jobs and unchanged design HEAD.
- API native tests forbid process launches and executable discovery. A retained legacy result remains downloadable after the old submission route is removed.
- Actual Hub preview used `snow-penguin-20260914-hub.3dm`, source SHA-256 `ba29e83ef3b84281eb8fd58dd408e9860cb3d261479579a5be7e2e71a3007154`. Blue perspective and gold orthographic variants use the native flow, not fixtures or canvas screenshots. The Hub was launched with a nonexistent Blender executable path.

## Deliberately deferred

- Neutral-asset migration: `.3dm`, `rhino3dm` and Rhino3dmLoader stay in place. They do not require Rhino Desktop. The unchanged design/drawing export pathways are outside this rendering migration.
- Texture/HDRI support, offline/headless rendering, cancellation and richer history-to-scene restoration.
- Cross-module Drawing source restoration remains further GH-218 work; this delivery is not a claim that every requirement in that issue is closed.
- Area lights without shadows use Three.js RectAreaLight. Area shadows use four emission samples, explicitly labelled as an approximation in the editor. This is raster rendering, not path tracing.

## Local use

Start the existing MonkeyHub and frontend; no renderer executable configuration is needed. Open a 3DM model in Modeling, send it to Render, adjust the preview, wait for the saved indicator and choose **Native Render → PNG**. History retains all completed images; **返回实时预览** returns to the current editable visualization.
