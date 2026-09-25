# GH-307 section perspective

Issue: https://github.com/cogco1/MonkeyHub/issues/307
Base: `93677d72` (batch C, `codex/batch-c-0925`).

A true section perspective (剖透视) through the one drawing projection pipeline: section plane cut of the retained STEP solids, exact perspective hidden-line solve, poché on the cut, retained by `_retain_projection`, registered as a document, callable by the Hub Agent. `drawing_elevation.py`, `drawing_svg.py` and `routes/drawings.py` are handed over from GH-66 for this lane.

## What the lane delivers

- **Geometry** (`archflow/adapters/occt_backend.py`, `project_occt_section_perspective`). Every selected shape is cut by the existing depth clip with near depth 0 (a Boolean common with an oriented box on the kept side, bounded by an optional depth). The kept parts take part in one exact `HLRBRep_Algo` solve with a perspective projector whose picture plane is the section plane; visible edges and silhouettes only. The plane's sections of the original solids (lines and even-odd regions) go through the same camera move and projector.
- **Pipeline** (`monkeydiagram/drawing_elevation.py`). `SectionPerspectiveView` checks the request before the model is read; `project_section_perspective` places the default camera from the exact cut and renders with the cut-plan poché and pens; `freeze_section_perspective` retains through `_retain_projection` with view kind `section-perspective`. The view records the request, the exact plane, the resolved camera and the crop; the projection details record the principal point, the focus and the drawn, cut and removed objects. Read-back accepts the three view kinds (`DRAWING_VIEW_KINDS`).
- **API**. `POST /api/drawings/section-perspectives` returns the registered `SourceDocument`. `generate_section_perspective` shares one registration, cache and monitoring helper with `generate_elevation`.
- **Agent**. The route is in the Hub chat's allowed Studio actions, and the DRAWINGS instructions state the minimal request and how to look at the result (`POST /api/board/export`).
- **Drawing tool**. A 剖透视 / Section perspective form (cut across X or Y, position, look toward, eye height, field of view) generates on the drawing's target and opens the result read-only. Cut-plan status, vector and appearance controls stay cut-plan only.

## Conventions

- `HLRAlgo_Projector(gp_Ax2(P, N, X), focus)`: the eye is at `P + focus·N`. The plane through `P` perpendicular to `N` maps 1:1 onto axes `X` and `N × X`. A point `z` behind that plane maps `1 / (1 + z / focus)` of the way toward `P`. With `P` the eye's foot on the section plane, the cut is true to scale and lines along the normal converge at `P`.
- `HLRBRep_Algo` perspective defect in cadquery-ocp 7.9.3: with a non-identity projector frame, back faces on one side of `P` stay visible and a cylinder's silhouettes land off its tangent lines. The shapes are therefore located in the eye's camera frame (an exact rigid move) and solved with the identity-frame projector. Tests pin both cases.
- Section: a plan line `[[x1, y1], [x2, y2]]` with `keep: left | right` (walking from the first point to the second), or `origin` plus `normal` (the normal points to the removed side, where the eye stands).
- Camera: explicit `eye` and `target` (optional `up` and `fovDeg`), or the default one-point perspective. The default puts the eye `eyeHeight` (1.6 m) above the lowest cut point, centred on the cut, at the distance that fits the cut's width plus a 5% margin in 55°, with the cut's centre as target. The target's image centres the frame. The frame's width is the field of view at the plane, and its height keeps the cut's proportions. Moving only the eye moves the vanishing point, not the frame.
- Named refusals: `SECTION_PLANE_MISSES_MODEL`, `SECTION_EYE_ON_KEPT_SIDE`, `SECTION_EYE_ON_PLANE`, `SECTION_CAMERA_DEGENERATE`, `SECTION_LINE_DEGENERATE`, `SECTION_NORMAL_DEGENERATE`, `SECTION_DEPTH_INVALID`, `SECTION_VALUE_NOT_FINITE`, `SECTION_REQUEST_INVALID`, `DRAWING_OBJECT_UNKNOWN` and `DRAWING_EMPTY`.

## Tests

`tests/test_drawing_section_perspective.py` (projector convention, geometry, retention, refusals) and `apps/archflow-studio/api/tests/test_section_perspective.py` (the route on a Studio-built room); the Hub chat test pins the instructions and the allowlist; `drawingCanvas.browser.mjs` covers the form.

## Not in this slice

- An SVG download and appearance edits for a section perspective in the Drawing tool; its drawings open read-only there.
- A freshness status (current or outdated against the Working Head) for section perspectives, as cut plans have.
- A solid-fill poché: the cut is filled with the cut-plan hatch (0.5 mm default spacing) because the PNG renderer draws strokes only.
