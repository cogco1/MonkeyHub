import type { PlanRequestDto, SourceDocumentDto } from "../../api/generated";

/** A form for the existing document's recipe; never a second retained design. */
export type PlanForm = { [K in "cutHeight" | "bottom" | "scaleDenominator" | "cutLineMm" | "visibleLineMm" | "hatchSpacingMm" | "dimensions" | "dressing"]: NonNullable<PlanRequestDto[K]> }
  & Pick<PlanRequestDto, "cropUv" | "hiddenObjectIds">;

export const drawingDocumentKey = (source: Pick<SourceDocumentDto, "runId" | "assetSha256" | "revisionRef">) =>
  JSON.stringify([source.runId, source.assetSha256, source.revisionRef ?? null]);

export function defaultPlanForm(lengthUnit: string): PlanForm {
  const heights: Record<string, number> = { millimeter: 1200, meter: 1.2, foot: 1.2 / .3048, inch: 1.2 / .0254 };
  return { cutHeight: heights[lengthUnit] ?? 1.2, bottom: 0, scaleDenominator: 100,
    cutLineMm: 0.35, visibleLineMm: 0.18, hatchSpacingMm: 2, dimensions: [], dressing: [] };
}

export function planFormFromDocument(document: SourceDocumentDto, lengthUnit: string): PlanForm {
  const recipe = document.viewRecipe ?? {};
  const frame = recipe.frame && typeof recipe.frame === "object" ? recipe.frame as Record<string, unknown> : {};
  const graphics = recipe.graphics && typeof recipe.graphics === "object" ? recipe.graphics as Record<string, unknown> : {};
  const defaults = defaultPlanForm(lengthUnit);
  const number = (value: unknown, fallback: number) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
  const cutHeight = number(Array.isArray(frame.origin) ? frame.origin[2] : undefined, defaults.cutHeight);
  const scale = typeof frame.scale === "string" ? Number(frame.scale.split(":")[1]) : NaN;
  return { ...defaults, cutHeight,
    bottom: cutHeight - number(frame.far_depth, cutHeight),
    scaleDenominator: Number.isFinite(scale) && scale > 0 ? scale : defaults.scaleDenominator,
    cutLineMm: number(graphics.cutLineMm, defaults.cutLineMm ?? 0.35),
    visibleLineMm: number(graphics.visibleLineMm, defaults.visibleLineMm ?? 0.18),
    hatchSpacingMm: number(graphics.hatchSpacingMm, defaults.hatchSpacingMm ?? 2),
    dressing: Array.isArray(recipe.dressing) ? structuredClone(recipe.dressing) as PlanForm["dressing"] : [],
    dimensions: Array.isArray(recipe.dimensions) ? structuredClone(recipe.dimensions) as PlanForm["dimensions"] : [],
    ...(Array.isArray(frame.crop_uv) ? { cropUv: structuredClone(frame.crop_uv) as PlanForm["cropUv"] } : {}),
    ...(Array.isArray(recipe.hiddenObjectIds) ? { hiddenObjectIds: recipe.hiddenObjectIds.filter((id): id is string => typeof id === "string") } : {}),
  };
}
