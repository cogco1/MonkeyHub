# Native Render Workspace V0.1

Implementation plan for [GH-216](https://github.com/cogco1/MonkeyHub/issues/216)
and [GH-218](https://github.com/cogco1/MonkeyHub/issues/218), aligned with the
representation investigation in [GH-223](https://github.com/cogco1/MonkeyHub/issues/223).

Status: implementation in progress. Baseline inspected: `db7aba96`.

Native Render now submits asynchronous Blender jobs from the current Modeling
mesh and captured camera. Sources, immutable recipes, execution transitions,
verified receipts and PNG documents belong to the existing P036 project. The
Render gallery lists only documents registered with a render recipe. It polls
task status and automatically opens the finished image with zoom/pan/download.
Refreshing reads retained results without resubmitting a job.

The first execution path accepts mesh-only 3DM, default neutral materials and
lighting, and a captured perspective/orthographic camera. Imported local meshes
retain exact source bytes without inventing a design revision; certified model
sources retain their original association. Neither path advances HEAD. Unsupported
geometry is refused explicitly. Shutdown drains accepted work; jobs interrupted
by a process crash are reported as interrupted after restart.

Validation includes real Blender API submission, idempotent request reuse,
conflicting request refusal, exact retained source/result hashes, cold result
readback after runtime restart and unchanged HEAD. Camera adapter tests cover
real Blender save/reopen, projection agreement and tampered-camera refusal.

Live Hub acceptance (2026-09-21): the Modeling toolbar sent
`snow-penguin-20260914-hub.3dm` to Render, and Start render submitted job
`render-d59a64bc04874266ad3bb6d2834f63b1`. Blender produced a 730 x 768 neutral
penguin image, automatically registered and displayed in Render. Source SHA-256
is `ba29e83ef3b84281eb8fd58dd408e9860cb3d261479579a5be7e2e71a3007154`.
Zoom in/out changed the displayed image dimensions; refresh reopened the retained
result without another task. The original failed attempt is retained honestly.
Blender now receives DEVNULL stdin rather than the Hub runtime control pipe,
which otherwise blocked its startup on Windows. A real subprocess regression
keeps the parent pipe open throughout rendering.

Remaining issue scope: cancellation, historical source/camera restoration,
source-linked Drawing workflow, richer geometry/material support and expanded
interaction acceptance. Neither GH-216 nor GH-218 is marked complete.

## User-visible outcome

From Modeling, Send to Render captures the current model version and viewport,
opens a native Render workspace; Start render submits one asynchronous preview. The user
can view and download the result, reopen it from the project gallery, and return
to its source model with the saved camera. Modeling, Drawing and Render retain
the same project context. No separate Blender-control web application is needed.

## Ownership

MonkeyHub Design State remains the only architectural design authority. Render
presentation settings do not redefine building geometry or architectural material
semantics. As specified by GH-218/GH-223, camera and lighting belong to the
representation owner and must not create a design revision when changed alone.

MonkeyHub manages retained editable presentation settings through existing project
owners and P036. Blender receives an immutable, exact-source-bound snapshot; its
scene is derived and must be reconstructable without treating a manually edited
blend file as authoritative. Artifact and job records reference that snapshot.
Do not build another design store or a render-specific dependency graph.

## Verified reusable implementation

- `archflow/adapters/blender_projection.py` already consumes verified OCCT source,
  reconstructs a Blender scene, cold-reads source identity/geometry/presentation,
  and validates the resulting PNG. Extend this execution owner.
- `BlenderPresentation` retains legacy `overview` / `preview-v1` settings and now
  accepts `BlenderCamera` for a captured viewport with rectangular output.
- `tests/test_blender_projection.py` and
  `tests/test_blender_projection_runner.py` provide existing projection coverage;
  native UI and arbitrary-camera acceptance require additional behavioral evidence.
- Hub workspace composition and project runtime own navigation and application
  execution. Resolve their precise contracts and real callers before modifying them.

## Implementation sequence

1. Inspect current Hub navigation/viewer, runtime APIs, artifact readers and live
   source scopes. Extend registered owners and reserve only actual edited paths.
2. Define and validate a reusable captured-camera value: position, orientation/up,
   projection mode, field of view or orthographic bounds, aspect ratio and clipping.
   Capture it before workspace navigation changes viewport dimensions.
3. Extend the projection adapter for that camera and rectangular output; account
   explicitly for viewer/engine coordinate systems and model units. Keep visible
   objects and source transforms consistent with the captured view.
4. Retain a representation recipe bound to the exact model source using existing
   project ports. Identify missing persistence with a reopen test before adding
   new record types. Each job retains one immutable recipe/source combination.
5. Wire native Render navigation, reusable viewport, source/camera information,
   basic settings and gallery. Send to Render starts exactly one default preview.
6. Connect asynchronous preparation/rendering/completion/failure/cancellation and
   result retrieval. Refresh observes retained execution; it never resubmits a job.
   Show percentages only when backed by real progress information.
7. Return to the available exact source and restore its camera. Preserve historical
   results and flag changed/missing sources rather than silently rebinding them.

## Acceptance evidence required before V0.1 completion

- Actual Hub UI: Send to Render, real preview, gallery/download and source return.
- Perspective and orthographic source screenshots, camera data and rendered
  landmark/outline comparisons demonstrating matching composition.
- Correct project/model/version bindings, visibility and unit conversion.
- Failed/canceled jobs preserve saved data; reopening or refreshing does not
  duplicate execution; project switching does not mix results.
- An edit while rendering leaves the in-flight snapshot unchanged.
- Presentation-only changes do not produce architectural design revisions.
- Existing Modeling and Drawing behavior checks plus relevant project tests and
  architecture checks; no claim based solely on an isolated Blender script.
- Startup instructions, changed modules/API documentation, actual UI screenshots,
  test results and explicit limitations.

V0.2 scene editing, V0.3 expanded history/drawing workflows and V0.4 AI features
remain later reviewed stages. GH-217's penguin eye repair remains a separate bug.

## Running the local preview

Set `ARCHFLOW_BLENDER_EXECUTABLE` to the installed Blender executable in the Hub
launch environment, then start the existing Hub and frontend. Hub inherits this
value into the project runtime; no separate renderer launcher is introduced.
Load a mesh-only 3DM in Modeling, choose Send to Render, then Start render.
The rendered image is a project document; its Render details expose the source
hash, captured camera recipe and verification receipt. Inputs are limited to
32 MiB; defaults are 768 pixels on the long side and 16 samples.
