# GH-244

Issue: https://github.com/cogco1/MonkeyHub/issues/244
Base: `4d98d3aa` (batch H2 wave 2, on batch H2 wave 1).

The Drawing canvas shows what each projected line is and lets the architect keep a correction: lines are selectable with their component, material and role, a hidden object stays hidden, untouched pens let the project recipe apply, and a repeated correction can be saved as a project recipe.

Batch H2 (2026-09-25), in the owner's order; plans are kept outside the repo.

## Lane `drawing-canvas`

- Landed (244-S4): the cut plan is drawn as inline SVG. Pointing at a projected line shows `component · material · role`, the role being its group (the cut with its hatch or poché, beyond the cut, hidden); clicking the line, or choosing the object under "Objects in this drawing", selects it. "Hide this object" writes `hiddenObjectIds` through the plan request, so the object stays hidden through a rebuild and can be shown again, with no design request. A plan past 20,000 lines is shown as an image whose objects are chosen from the list (D-244-3). A new cut plan asks only for the pens a person set, so the project recipe applies, and the form then shows the values the revision was drawn with. `drawingCanvas.browser.mjs` reads the projection's own SVG for its retained revision and writes `data-dressing` as the runtime does.
- Open: the entourage hint in `DrawingDressing.tsx` still says to save appearance (GH-66's file); the canvas does not show the revision's `cleanup` report; 05-S4 (the suggestion card, saving a project recipe, `sourceKind: human`) waits for the drawing-corrections lane.
