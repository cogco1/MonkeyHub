# Diagrams from data in MonkeyDiagram (gap 38)

Design note, 2026-10-21. Decided the same day (the coordinator, for Kaiwen): stated places first, the layout after
steps 1 to 3; colour by default with a black-and-white style for print; an optional unchecked `ref` on a node; editing
through the chat only.

Status, 2026-10-21: steps 1 to 3 are built; step 4 is next. The kinds are `network`, `section` and `timeline`;
the first is not called topology, a word the agent's texts reserve. A section is drawn in its own units at one scale in
x and height, with spans (floors solid or dashed), polygons, ellipses, markers, moves and notes, as the breakdown's
is, rather than in named levels; a timeline's segments and events may carry a colour of their own, the level's. Revisions
go by drawing id, as a sheet's do: a receipt chain with `previousRevisionRef` exists only for drawings of a model. An
edge may end at a point, for a branch that leads off the plan. The breakdown's plan has 9 zones, 11 routes and 5
branch stubs, not the 13 zones and 9 routes estimated below.

## Why

A level breakdown needs a plan topology (zones and the routes between them), a section schematic (levels and the
moves between them) and a rhythm bar (time segments in lanes). There is no model to draw them from, and MonkeyDiagram
draws only from one: cut plans, sections, elevations, section perspectives and sheets all take a model source. The
breakdown drew them by hand as Excalidraw elements with written coordinates: without revisions, outside the drawing
styles, and redrawn from nothing for every change. Architecture uses the same three: circulation and adjacency
diagrams, programme stacks, phasing bars.

## What the person or agent does

- `POST /api/drawings/diagrams {projectId, drawingId, kind, spec, styleId?, title?, previousRevisionRef?}` makes a
  registered drawing (SVG, PDF and PNG) as a cut plan is made, with `viewRecipe {kind: "diagram", diagramKind,
  specSha256, styleId}`.
- A change is the same request with the edited spec and `previousRevisionRef`: a new revision of the same drawing,
  the earlier one kept.
- It reaches the Board by hand only, as decided on 2026-10-21: the result offers Put on Board through the existing
  page request, and nothing places it. No button is added (ADR-014): the chat reaches it through `studio_request`,
  and the drawings guide, read on demand, describes it.

## Three kinds (`DiagramSpec@1`)

1. **network**: `nodes [{id, label, sublabel?, group?, at, size?}]`, `groups [{id, label, tone}]` (the fills the legend
   names), `edges [{from, to, label?, line, style, arrow}]` with `line` one of main, loop, return, branch, `style`
   solid, dashed or dotted, `arrow` end, both or none; optional `north` (degrees) and `scale {length, label}`. The
   legend is made from the tones and lines used. `at` is a cell `[col, row]` of a stated grid or a point in diagram
   units: what the breakdown already wrote. Edges run orthogonally between node sides around other nodes, or straight
   with `route: "straight"`.
2. **section**: `levels [{id, label, elevation}]` as horizontal bands, `spans [{id, label, level or from/to levels,
   x0, x1, tone}]`, `moves [{from, to, label?, kind, style}]` with `kind` lift, stair, fall or jump; an optional
   vertical scale.
3. **timeline**: `lanes [{id, label}]`, `segments [{lane, start, end, label, tone}]`, `marks [{at, label}]`, and
   `axis {unit, ticks}`.

## How it is drawn

- A `diagrams` module in `monkeydiagram` validates the spec and lays it out as a paper scene of boxes, polylines with
  dash patterns, arrowheads and text, which the existing renderers (`rendering/svg.py`, `rendering/paper.py`) turn into
  SVG, PDF and PNG, as sheets are. No new renderer, no browser, no image model.
- The drawing styles (`documentation/styles.py`) give line weights and fonts; each style maps tones to its palette;
  a black-and-white style draws pure black lines with no fills, its hierarchy in line weight and dash, as the
  course sheets ask.
- Limits: 200 nodes, 400 edges, 50 levels, 500 segments, labels of 120 characters; Chinese labels in the style's font.
- It is registered like a cut plan: a run `diagram-<drawingId>`, its revision chain, the documents list and the files
  route, and `visual_review` can look at its page.

## Steps and their acceptance

1. **Spec, validation and topology at stated places**, with orthogonal routing, the legend and revisions. Accepted when
   the breakdown's plan topology (13 zones, 9 routes, 4 line kinds) is drawn from a spec, a second revision changes one
   route's style and keeps the first, and `visual_review` returns its page.
2. **Section schematic.** Accepted when the breakdown's section schematic (3 levels, 10 spans, 6 moves) is drawn.
3. **Timeline.** Accepted when the breakdown's rhythm bar is drawn.
4. **A network without `at`**: a layered layout by group with fewer crossings. Accepted when a 13-node graph given no
   places draws no node over another and no edge through a node.

Steps 1 to 3 take about two to three working days; step 4 one to two.

## To decide

- Places: stated places only at first (recommended), or a layout from the start.
- The default style: colour tones, as the breakdown's, or black and white, as the course sheets.
- Whether a node may name a model entity (`ref`, such as a zone id) for a later check against the model: recommended
  as optional now, unchecked.
- Editing: by spec through the chat only, with no editor in the web page (recommended, per ADR-014).
