# Live section in the Modeling viewport (G5)

Design note, 2026-10-20. Not built yet; the steps below are the order of work.

## Why

An architect checks a section by cutting the model while turning it. Today the Hub shows a cut only as a drawing
(`POST /api/drawings/plans` for a cut plan or vertical section, `POST /api/drawings/section-perspectives`), which
takes seconds to minutes per try, and the Modeling viewport cannot hide what stands in front of a plane. A live cut
in the viewport is for looking; the exact section stays the drawing routes' work, so nothing about P264 changes:
what is drawn and issued still comes from the Hub's own drawing.

## What the person does

- A **Section** button in the viewport's tools turns one cutting plane on or off. The plane is horizontal (a plan
  cut at a height) or vertical along X or Y, or the plane of a picked face.
- The plane shows as a faint rectangle over the model's bounds, with the existing translation gizmo along its
  normal. Dragging it, or typing an offset in the model's unit, moves it; **Flip** keeps the other side.
- Everything on the cut-away side is not drawn. Where a closed member is cut, its cut face is filled (poché); an open
  surface is cut with no fill, so a sheet never looks solid.
- **Draw this section** sends the same plane to the existing routes: a horizontal plane to a cut plan at that height,
  a vertical one to a vertical section (a cut plan whose frame has +Z up, as the web's `isVerticalSection` reads),
  and, with a perspective camera, a section perspective. The result is a normal registered drawing.
- A captured view (the render input, a camera pass's saved view) keeps the plane in its recipe, so a render or camera
  pass of that view is cut the same way.

## How, in the web

- `renderer.localClippingEnabled = true`, and one `THREE.Plane` given to every material the model display prepares
  (`modelingMaterial`, `presentationMaterial`, the highlight and ghost copies) as `clippingPlanes`, lines included. No
  geometry is rebuilt; moving the plane only changes its constant.
- Caps use the stencil buffer, in the pattern three.js documents for clipping: for each closed mesh, a stencil-only
  pass where back faces increment and front faces decrement, then one plane-sized quad drawn where the stencil is not
  zero, in the cap colour. Only members the model says are closed get the cap pass (the CAD receipt's `closed`, which
  `POST /api/quantities` now returns per member); open ones skip it.
- Picking, preselection and snapping ignore whatever lies on the cut-away side (a plane test before the raycast hit is
  accepted).
- Cost: the stencil passes add two draw calls per cut closed mesh. A construction model like Hemp's V14 (282
  members) is well within that; for a site model of thousands of objects the cap passes run only for members whose
  bounds cross the plane.
- The plane belongs to the viewport, not the project: nothing is saved by moving it. Capturing a view writes it into
  that capture's recipe as `clip: {origin, normal}`, in model coordinates.

## How, in the Studio (step 4)

- The saved-view recipe and `POST /api/render/views/passes` accept `clip`; the mesh views clip the tessellation at the
  plane before drawing, so the ID, line and depth passes match the viewport. No new drawing kind: exact sections stay
  with the drawing routes.

## Steps and their acceptance

1. **Cut, no caps.** The Section button, the plane, the gizmo and the offset field. Accepted when a browser test shows
   geometry beyond the plane is not drawn (a pixel probe on a known box), the plane moves with the offset, and a pick
   on the cut-away side hits nothing.
2. **Caps for closed members.** Accepted when a closed box cut at mid-height shows a filled rectangle at the cut and an
   open surface cut the same way shows only its edge.
3. **Draw this section.** Accepted when the drawing made from a horizontal plane is a cut plan whose frame origin
   height equals the plane's, and from a vertical plane a vertical section through the same line.
4. **Captured views and camera passes keep the cut.** Accepted when a camera pass of a captured cut view has no
   depth sample beyond the plane.

## For Kaiwen to choose

- The cap colour: the member's material colour, darkened (the default in this note), or solid black poché as the
  drawings use.
- Whether **Draw this section** also places the new drawing on the Board, as generated drawings already are.
