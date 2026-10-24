import assert from "node:assert/strict";
import test from "node:test";

import type { SourceDocumentDto } from "../src/api/project-runtime/generated";
import { drawnPlanCalibration } from "../src/workspaces/monkeydiagram/drawnCalibration.ts";

// A 1:100 plan of a 46 m wide crop is a 460 mm wide sheet: 460 / 25.4 * 72 points.
const plan = (frame: Record<string, unknown>, extra: Partial<SourceDocumentDto> = {}): SourceDocumentDto => ({
  projectId: "P", runId: "r", fileName: "plan.pdf", mimeType: "application/pdf", sizeBytes: 1, pageCount: 1, assetSha256: "a".repeat(64),
  pages: [{ pageIndex: 0, width: 460 / 25.4 * 72, height: 300, rotation: 0 }],
  viewRecipe: { kind: "cut-plan", frame: { right: [1, 0, 0], up: [0, 1, 0], scale: "1:100", ...frame } }, ...extra,
} as unknown as SourceDocumentDto);

test("a plan MonkeyDiagram drew gives its page's scale and direction: its bottom edge is the crop's width along +X", () => {
  assert.deepEqual(drawnPlanCalibration(plan({}), 0), { origin: [0, 1], axisPoint: [1, 1], distance: 46, scaleLabel: "1:100" });
  assert.equal(drawnPlanCalibration(plan({ scale: "1:50" }), 0)?.distance, 23);
});

test("any other page keeps the measured line: an upload, a section, a turned plan, another page, an image", () => {
  assert.equal(drawnPlanCalibration(plan({}, { viewRecipe: null }), 0), null, "an uploaded PDF");
  assert.equal(drawnPlanCalibration(plan({}, { viewRecipe: { kind: "elevation", frame: {} } } as Partial<SourceDocumentDto>), 0), null);
  assert.equal(drawnPlanCalibration(plan({ up: [0, 0, 1] }), 0), null, "a vertical section");
  assert.equal(drawnPlanCalibration(plan({ right: [0, 1, 0] }), 0), null, "a plan drawn turned");
  assert.equal(drawnPlanCalibration(plan({ scale: "NTS" }), 0), null);
  assert.equal(drawnPlanCalibration(plan({}), 1), null);
  assert.equal(drawnPlanCalibration(plan({}, { mimeType: "image/png" } as Partial<SourceDocumentDto>), 0), null);
  assert.equal(drawnPlanCalibration(null, 0), null);
});
