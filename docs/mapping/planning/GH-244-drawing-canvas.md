# GH-244

Issue: https://github.com/cogco1/MonkeyHub/issues/244
Base: `4d98d3aa` (batch H2 wave 2, on batch H2 wave 1).

The Drawing canvas shows what each projected line is and lets the architect keep a correction: lines are selectable with their component, material and role, a hidden object stays hidden, untouched pens let the project recipe apply, and a repeated correction can be saved as a project recipe.

Batch H2 (2026-09-25), in the owner's order; plans are kept outside the repo.

## Lane `drawing-canvas`

- Landed (244-S4): the cut plan is drawn as inline SVG. Pointing at a projected line shows `component · material · role`, the role being its group (the cut with its hatch or poché, beyond the cut, hidden); clicking the line, or choosing the object under "Objects in this drawing", selects it. "Hide this object" writes `hiddenObjectIds` through the plan request, so the object stays hidden through a rebuild and can be shown again, with no design request. A plan past 20,000 lines is shown as an image whose objects are chosen from the list (D-244-3). A new cut plan asks only for the pens a person set, so the project recipe applies, and the form then shows the values the revision was drawn with. `drawingCanvas.browser.mjs` reads the projection's own SVG for its retained revision and writes `data-dressing` as the runtime does.
- Landed (05-S4, on the drawing-corrections lane): every cut-plan request from the Drawing says `sourceKind: human`. A suggestion from `GET /api/drawings/corrections` is a card at the top of the drawing, "N drawings set <field> to <value> mm — save as project recipe?": Save writes exactly one `POST /api/decisions` (a person's `require` drawing decision, `strong_preference` for the project, under `drawing:lineweight` or `drawing:hatch`, citing the suggestion's page, with a recipe binding of that value), and Ignore hides it for the session and writes nothing. "Save as project recipe" under Linework and hatch saves the open revision's values, cited on its page: values the recipe already holds write nothing, a project recipe decision holding a changed value is superseded, and the rest get a new decision.
- Open: the entourage hint in `DrawingDressing.tsx` still says to save appearance (GH-66's file); the canvas does not show the revision's `cleanup` report; a recipe is saved for the whole project at `strong_preference`, with no choice of a Stage or another strength in the Drawing.
