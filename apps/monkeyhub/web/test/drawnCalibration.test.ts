import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { SourceDocumentDto } from "../src/api/project-runtime/generated";
import { drawnPlanCalibration } from "../src/workspaces/monkeydiagram/drawnCalibration.ts";

// A cut plan as POST /api/drawings/plans returned it (project-runtime's CutPlanTests room, drawn at 1:50): a
// 697 px square PNG of the crop [-0.95, -0.95, 4.95, 4.95] m, 5.9 m at 1:50 being 118 mm, at 150 dpi.
const drawn = JSON.parse(readFileSync(new URL("./fixtures/cut-plan-document.json", import.meta.url), "utf-8")) as SourceDocumentDto;
const plan = (frame: Record<string, unknown> = {}, extra: Partial<SourceDocumentDto> = {}): SourceDocumentDto => {
  const recipe = drawn.viewRecipe as { frame: Record<string, unknown> };
  return { ...drawn, viewRecipe: { ...recipe, frame: { ...recipe.frame, ...frame } }, ...extra } as SourceDocumentDto;
};

test("a plan MonkeyDiagram drew gives its page's scale and direction: its bottom edge is the crop's width along +X", () => {
  assert.equal(drawn.mimeType, "image/png", "the drawing service retains its plans as PNG");
  assert.deepEqual(drawnPlanCalibration(drawn, 0), { origin: [0, 1], axisPoint: [1, 1], distance: 5.9, scaleLabel: "1:50" });
  // The same plan drawn in millimetres: its crop is 5900 units wide and the image the same size.
  assert.equal(drawnPlanCalibration(plan({ crop_uv: [-950, -950, 4950, 4950] }), 0)?.distance, 5.9);
  // At 1:100 the same crop is half the pixels.
  assert.equal(drawnPlanCalibration(plan({ scale: "1:100" }, { pages: [{ pageIndex: 0, width: 348, height: 348, rotation: 0 }] }), 0)?.distance, 5.9);
});

test("any other page keeps the measured line: an upload, a section, a turned plan, another page, a sheet that is not its crop", () => {
  assert.equal(drawnPlanCalibration(plan({}, { viewRecipe: null }), 0), null, "an uploaded image");
  assert.equal(drawnPlanCalibration(plan({}, { viewRecipe: { kind: "elevation", frame: {} } } as Partial<SourceDocumentDto>), 0), null);
  assert.equal(drawnPlanCalibration(plan({ up: [0, 0, 1] }), 0), null, "a vertical section");
  assert.equal(drawnPlanCalibration(plan({ right: [0, 1, 0] }), 0), null, "a plan drawn turned");
  assert.equal(drawnPlanCalibration(plan({ scale: "NTS" }), 0), null);
  assert.equal(drawnPlanCalibration(plan({ crop_uv: null }), 0), null);
  assert.equal(drawnPlanCalibration(plan({ scale: "1:100" }), 0), null, "pixels that are not the crop at its scale in any unit");
  assert.equal(drawnPlanCalibration(plan(), 1), null);
  assert.equal(drawnPlanCalibration(plan({}, { mimeType: "application/pdf" } as Partial<SourceDocumentDto>), 0), null, "a PDF with a plan's recipe was not drawn here");
  assert.equal(drawnPlanCalibration(null, 0), null);
});
