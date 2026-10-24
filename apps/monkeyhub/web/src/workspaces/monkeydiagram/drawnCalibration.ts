import type { DocumentTracingCalibrationDto, SourceDocumentDto } from "../../api/project-runtime/generated";

/** The units a drawing frame may be in, as paper millimetres per unit (monkeydiagram rendering/svg.py drawing_svg). */
const UNIT_MM: Readonly<Record<string, number>> = { meter: 1000, millimeter: 1, inch: 25.4, foot: 304.8 };
/** The resolution MonkeyDiagram rasterises a drawing's SVG at (rendering/svg.py render_svg_png). */
const PNG_DPI = 150;

/**
 * F093: a page's scale and direction, read from the drawing itself when MonkeyDiagram drew it. A horizontal cut
 * plan is retained as a PNG of exactly its crop window (monkeydiagram rendering/svg.py: the SVG's viewBox is the
 * crop, its x along the frame's right, and the PNG is that SVG at 150 dpi), so the image's bottom edge, left to
 * right, is the crop's width along model +X; the page needs no line measured by hand. The recipe does not name the
 * frame's unit: it is the one whose paper size at the drawing's scale gives the image its pixels. Any other page (an
 * upload, a section, an elevation, a sheet of several views, a plan drawn turned) answers null and keeps the
 * measured line.
 */
export function drawnPlanCalibration(document: SourceDocumentDto | null | undefined, pageIndex: number):
  (DocumentTracingCalibrationDto & { scaleLabel: string }) | null {
  const recipe = document?.viewRecipe as { kind?: unknown; frame?: unknown } | null | undefined;
  const frame = recipe?.frame as { right?: unknown; up?: unknown; scale?: unknown; crop_uv?: unknown } | undefined;
  if (!document || document.mimeType !== "image/png" || pageIndex !== 0 || recipe?.kind !== "cut-plan" || !frame) return null;
  const along = (value: unknown, axis: readonly number[]) => Array.isArray(value) && value.length === 3
    && value.every((component, index) => typeof component === "number" && Math.abs(component - axis[index]!) < 1e-9);
  if (!along(frame.right, [1, 0, 0]) || !along(frame.up, [0, 1, 0])) return null;
  const denominator = Number(/^1:(\d+(?:\.\d+)?)$/.exec(String(frame.scale))?.[1]);
  const crop = frame.crop_uv;
  if (!Array.isArray(crop) || crop.length !== 4 || !crop.every((value) => typeof value === "number" && Number.isFinite(value))) return null;
  const [u0, v0, u1, v1] = crop as number[];
  const width = u1! - u0!, height = v1! - v0!;
  const page = document.pages.find((row) => row.pageIndex === 0);
  if (!page || !Number.isFinite(denominator) || denominator <= 0 || !(width > 0) || !(height > 0)
      || !(page.width > 0) || !(page.height > 0) || page.rotation !== 0) return null;
  // Model units to paper millimetres at the scale, then to the PNG's pixels; a pixel either way is rounding.
  const pixels = (length: number, unitMm: number) => length * unitMm / denominator / 25.4 * PNG_DPI;
  const units = Object.values(UNIT_MM).filter((unitMm) =>
    Math.abs(pixels(width, unitMm) - page.width) <= 1 && Math.abs(pixels(height, unitMm) - page.height) <= 1);
  if (units.length !== 1) return null;
  // The crop's width in metres on the model, to the millimetre.
  const distance = Math.round(width * units[0]!) / 1000;
  return { origin: [0, 1], axisPoint: [1, 1], distance, scaleLabel: `1:${denominator}` };
}
