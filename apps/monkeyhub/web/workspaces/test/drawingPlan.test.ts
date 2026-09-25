import assert from "node:assert/strict";
import test from "node:test";
import { defaultPlanForm, drawingDocumentKey, planFormFromDocument } from "../src/workspaces/monkeydiagram/drawingPlan.ts";
import type { SourceDocumentDto } from "../src/api/generated";

test("a retained cut-plan restores its actual frame and keeps dimension identity, placement and hidden intent", () => {
  const dimension = { id: "door-dimension", entityRef: "entity:wall", openingId: "door", placement: { offsetMm: -12 } };
  const document = { runId: "model-A", assetSha256: "a".repeat(64), revisionRef: "old-revision", viewRecipe: {
    kind: "cut-plan", frame: { origin: [0, 0, 1500], far_depth: 1300, scale: "1:50", crop_uv: [-2, -3, 12, 8] },
    graphics: { cutLineMm: .5, visibleLineMm: .2, hatchSpacingMm: 3 }, dimensions: [dimension], hiddenObjectIds: ["hidden-object"],
  } } as SourceDocumentDto;
  const form = planFormFromDocument(document, "millimeter");
  assert.deepEqual(form, { cutHeight: 1500, bottom: 200, scaleDenominator: 50,
    cutLineMm: .5, visibleLineMm: .2, hatchSpacingMm: 3, dimensions: [dimension], dressing: [], cropUv: [-2, -3, 12, 8], hiddenObjectIds: ["hidden-object"] });
  form.dimensions[0].placement!.offsetMm = 20;
  assert.equal(dimension.placement.offsetMm, -12, "editing the form never mutates its saved source recipe");
  assert.notEqual(drawingDocumentKey(document), drawingDocumentKey({ ...document, revisionRef: "new-revision" }),
    "identical document bytes do not collapse revision identities");
});

test("the initial cut is expressed in the exact source length unit", () => {
  assert.equal(defaultPlanForm("meter").cutHeight, 1.2);
  assert.equal(defaultPlanForm("millimeter").cutHeight, 1200);
  assert.equal(defaultPlanForm("foot").cutHeight * .3048, 1.2);
  assert.equal(defaultPlanForm("inch").cutHeight * .0254, 1.2);
});
