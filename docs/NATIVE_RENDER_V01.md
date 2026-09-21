# Native Render Workspace V0.1

Implementation plan for [GH-216](https://github.com/cogco1/MonkeyHub/issues/216)
and [GH-218](https://github.com/cogco1/MonkeyHub/issues/218), aligned with the
representation investigation in [GH-223](https://github.com/cogco1/MonkeyHub/issues/223).

Status: planning only. This document does not claim that the workspace or
same-camera rendering is implemented. Baseline inspected: `db7aba96`.

## User-visible outcome

From Modeling, Send to Render captures the current model version and viewport,
opens a native Render workspace and starts one asynchronous preview. The user
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
- `BlenderPresentation` currently supports only `overview` / `preview-v1`, square
  output resolution and azimuth/elevation. It cannot express an arbitrary captured
  modeling camera yet.
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
