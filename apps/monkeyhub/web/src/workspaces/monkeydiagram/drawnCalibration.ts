import type { DocumentTracingCalibrationDto, SourceDocumentDto } from "../../api/project-runtime/generated";

/**
 * F093: a page's scale and direction, read from the drawing itself when MonkeyDiagram drew it. A horizontal cut
 * plan's sheet is exactly its crop window at its scale (monkeydiagram rendering/svg.py: the SVG is the crop's
 * width times paper-per-unit, its x along the frame's right), so its bottom edge, left to right, is a known length
 * along model +X; the page needs no line measured by hand. Any other page (an upload, a section, an elevation, a
 * sheet of several views, a plan drawn turned) answers null and keeps the measured line.
 */
export function drawnPlanCalibration(document: SourceDocumentDto | null | undefined, pageIndex: number):
  (DocumentTracingCalibrationDto & { scaleLabel: string }) | null {
  const recipe = document?.viewRecipe as { kind?: unknown; frame?: unknown } | null | undefined;
  const frame = recipe?.frame as { right?: unknown; up?: unknown; scale?: unknown } | undefined;
  if (!document || document.mimeType !== "application/pdf" || pageIndex !== 0 || recipe?.kind !== "cut-plan" || !frame) return null;
  const along = (value: unknown, axis: readonly number[]) => Array.isArray(value) && value.length === 3
    && value.every((component, index) => typeof component === "number" && Math.abs(component - axis[index]!) < 1e-9);
  if (!along(frame.right, [1, 0, 0]) || !along(frame.up, [0, 1, 0])) return null;
  const denominator = Number(/^1:(\d+(?:\.\d+)?)$/.exec(String(frame.scale))?.[1]);
  const page = document.pages.find((row) => row.pageIndex === 0);
  if (!page || !Number.isFinite(denominator) || denominator <= 0 || !(page.width > 0) || page.rotation !== 0) return null;
  // PDF points to paper millimetres, then the scale to metres on the model.
  const distance = Math.round(page.width * 25.4 / 72 * denominator) / 1000;
  return { origin: [0, 1], axisPoint: [1, 1], distance, scaleLabel: `1:${denominator}` };
}
