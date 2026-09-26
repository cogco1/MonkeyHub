import type { DecisionDto, DecisionRequestDto, PlanRequestDto, PlanStatusDto, SourceDocumentDto } from "../../api/generated";

/** The paper pens and hatch spacing of a cut plan, in paper millimetres. */
export const PAPER_PENS = ["cutLineMm", "visibleLineMm", "hatchSpacingMm"] as const;
export type PaperPen = typeof PAPER_PENS[number];
/** The code default the runtime falls back to below the project recipe; a retained revision names all three. */
const PAPER_DEFAULTS: Record<PaperPen, number> = { cutLineMm: .35, visibleLineMm: .18, hatchSpacingMm: 2 };

/** The drawing decision target each paper value of a project recipe sits under, as the runtime's RECIPE_KEYS has it. */
export const RECIPE_TARGETS = { cutLineMm: "drawing:lineweight", visibleLineMm: "drawing:lineweight", hatchSpacingMm: "drawing:hatch" } as const;
export type RecipeTarget = typeof RECIPE_TARGETS[PaperPen];
/** One registered drawing page, the exact evidence a project recipe is confirmed on. */
export type RecipePage = { runId: string; assetSha256: string; revisionRef: string; pageIndex: number };

/**
 * A person's confirmed project recipe for some paper values of one target (05 3.2 D): a require decision
 * in the drawing domain, held as strong_preference for the whole project, applying by scope and evidenced
 * by the page it was confirmed on. `words` are what the person was shown and confirmed.
 */
export function recipeDecision({ projectId, words, target, graphics, page }: {
  projectId: string; words: string; target: RecipeTarget; graphics: Partial<Record<PaperPen, number>>; page: RecipePage;
}): DecisionRequestDto {
  return { projectId, rawLanguage: words, disposition: "require", strength: "strong_preference", targetRef: target,
    scope: { domain: "drawing", extent: "project" },
    source: { kind: "document", runId: page.runId, assetSha256: page.assetSha256, revisionRef: page.revisionRef, pageIndex: page.pageIndex },
    applicability: "scope", sourceKind: "human", typedBinding: { kind: "recipe", graphics } };
}

/** One write that makes paper values the project recipe: a new decision, or the supersession of one that holds some of them. */
export type RecipeWrite = { target: RecipeTarget; graphics: Partial<Record<PaperPen, number>>; replaces: { decisionId: string; revisionRef: string } | null };

/**
 * What saving these values as the project recipe writes. The runtime keeps one active value per key,
 * strength and reach, so a project recipe decision that already holds a key at that hold is superseded
 * with the new value; a key nobody holds gets a new decision under its target; a target whose values
 * the recipe already holds writes nothing.
 */
export function recipeWrites(decisions: readonly DecisionDto[], values: Record<PaperPen, number>): RecipeWrite[] {
  const held = (row: DecisionDto): Partial<Record<PaperPen, number | null>> => row.typedBinding?.kind === "recipe" ? row.typedBinding.graphics : {};
  const holders = decisions.filter(row => row.status === "active" && row.typedBinding?.kind === "recipe" && row.strength === "strong_preference"
    && row.scope.domain === "drawing" && row.scope.extent === "project");
  const pick = (keys: readonly PaperPen[]) => Object.fromEntries(keys.map(key => [key, values[key]]));
  const writes: RecipeWrite[] = [];
  for (const target of new Set(Object.values(RECIPE_TARGETS))) {
    const keys = PAPER_PENS.filter(key => RECIPE_TARGETS[key] === target);
    const owners = holders.filter(row => keys.some(key => held(row)[key] != null));
    const unowned = keys.filter(key => !owners.some(row => held(row)[key] != null));
    const changed = owners.filter(row => keys.some(key => held(row)[key] != null && held(row)[key] !== values[key]));
    changed.forEach((row, index) => writes.push({ target, replaces: { decisionId: row.decisionId, revisionRef: row.revisionRef },
      graphics: pick([...keys.filter(key => held(row)[key] != null), ...(index === 0 ? unowned : [])]) }));
    if (changed.length === 0 && unowned.length > 0) writes.push({ target, graphics: pick(unowned), replaces: null });
  }
  return writes;
}

/**
 * A form for the existing document's recipe; never a second retained design.
 * A new drawing's pens start empty: a pen nobody set is not asked for, so the
 * project recipe (else the code default) draws it.
 */
export type PlanForm = { [K in "cutHeight" | "bottom" | "scaleDenominator" | "dimensions" | "dressing"]: NonNullable<PlanRequestDto[K]> }
  & { [K in PaperPen]?: number } & Pick<PlanRequestDto, "cropUv" | "hiddenObjectIds">;

export const drawingDocumentKey = (source: Pick<SourceDocumentDto, "runId" | "assetSha256" | "revisionRef">) =>
  JSON.stringify([source.runId, source.assetSha256, source.revisionRef ?? null]);

export function defaultPlanForm(lengthUnit: string): PlanForm {
  const heights: Record<string, number> = { millimeter: 1200, meter: 1.2, foot: 1.2 / .3048, inch: 1.2 / .0254 };
  return { cutHeight: heights[lengthUnit] ?? 1.2, bottom: 0, scaleDenominator: 100, dimensions: [], dressing: [] };
}

/** The request fields a form asks for: all it holds, except a pen without a value, which the runtime fills. */
export function planRequestFields(form: PlanForm): PlanForm {
  const fields = { ...form };
  for (const key of PAPER_PENS) if (!Number.isFinite(fields[key])) delete fields[key];
  return fields;
}

export function planFormFromDocument(document: SourceDocumentDto, lengthUnit: string): PlanForm {
  const recipe = document.viewRecipe ?? {};
  const frame = recipe.frame && typeof recipe.frame === "object" ? recipe.frame as Record<string, unknown> : {};
  const graphics = recipe.graphics && typeof recipe.graphics === "object" ? recipe.graphics as Record<string, unknown> : {};
  const defaults = defaultPlanForm(lengthUnit);
  const number = (value: unknown, fallback: number) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
  const cutHeight = number(Array.isArray(frame.origin) ? frame.origin[2] : undefined, defaults.cutHeight);
  const scale = typeof frame.scale === "string" ? Number(frame.scale.split(":")[1]) : NaN;
  // The pens the revision was drawn with, whichever layer they came from.
  return { ...defaults, cutHeight,
    bottom: cutHeight - number(frame.far_depth, cutHeight),
    scaleDenominator: Number.isFinite(scale) && scale > 0 ? scale : defaults.scaleDenominator,
    cutLineMm: number(graphics.cutLineMm, PAPER_DEFAULTS.cutLineMm),
    visibleLineMm: number(graphics.visibleLineMm, PAPER_DEFAULTS.visibleLineMm),
    hatchSpacingMm: number(graphics.hatchSpacingMm, PAPER_DEFAULTS.hatchSpacingMm),
    dressing: Array.isArray(recipe.dressing) ? structuredClone(recipe.dressing) as PlanForm["dressing"] : [],
    dimensions: Array.isArray(recipe.dimensions) ? structuredClone(recipe.dimensions) as PlanForm["dimensions"] : [],
    ...(Array.isArray(frame.crop_uv) ? { cropUv: structuredClone(frame.crop_uv) as PlanForm["cropUv"] } : {}),
    ...(Array.isArray(recipe.hiddenObjectIds) ? { hiddenObjectIds: recipe.hiddenObjectIds.filter((id): id is string => typeof id === "string") } : {}),
  };
}

/** A drawing made from a chosen version stays on it until a person rebuilds it on the current model (#271). */
export const keptOnChosenVersion = (document: Pick<SourceDocumentDto, "viewRecipe"> | null) => document?.viewRecipe?.follow === "frozen";

/** One drawing identity: its revisions share a drawing id (older drawings fall back to their file name). */
export const drawingIdentity = (document: Pick<SourceDocumentDto, "drawingId" | "fileName">) => document.drawingId ?? document.fileName;

/** The newest revision of each drawing, newest drawing first; older revisions are history. */
export function latestRevisions(documents: readonly SourceDocumentDto[]): SourceDocumentDto[] {
  const latest = new Map<string, SourceDocumentDto>();
  for (const document of documents) {
    const key = drawingIdentity(document);
    const known = latest.get(key);
    if (!known || (document.generatedAt ?? "") > (known.generatedAt ?? "")) latest.set(key, document);
  }
  return [...latest.values()].sort((a, b) => (b.generatedAt ?? "").localeCompare(a.generatedAt ?? ""));
}

export type LiveAction = "none" | "rebuild" | "blocked";

/**
 * What a LIVE drawing does about its status against the Working Head (#271):
 * rebind once to the head's exact source, or say why it cannot. A drawing that
 * already reads the head keeps its broken anchors visible instead of looping.
 */
export function liveAction(input: {
  live: boolean; dirty: boolean; attempted: boolean;
  status: Pick<PlanStatusDto, "status" | "bindingChanged" | "targetModelSource"> | null;
}): LiveAction {
  const { status } = input;
  if (!input.live || input.dirty || status === null || status.status === "unknown") return "none";
  if (!status.targetModelSource) return status.status === "outdated" ? "blocked" : "none";
  if (!status.bindingChanged) return "none";
  return input.attempted ? "none" : "rebuild";
}
