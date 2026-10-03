import type { BoardDto, SourceDocumentDto } from "../../api/project-runtime/generated";

export type BoardDraft = Pick<BoardDto, "projectId" | "title" | "elements" | "seenDocuments">;
export type PageSource = Pick<SourceDocumentDto, "runId" | "assetSha256"> & {
  revisionRef: string | null;
  pageIndex: number;
};

/** A receipt revision remains distinct even when it produced identical pixels. */
export function documentKey(source: Pick<SourceDocumentDto, "runId" | "revisionRef" | "assetSha256">): string {
  return JSON.stringify([source.runId, source.revisionRef ?? source.assetSha256]);
}

export function pageKey(source: PageSource): string {
  return JSON.stringify([source.runId, source.assetSha256, source.revisionRef, source.pageIndex]);
}

export function pageSource(document: SourceDocumentDto, pageIndex: number): PageSource {
  return { runId: document.runId, assetSha256: document.assetSha256,
    revisionRef: document.revisionRef ?? null, pageIndex };
}

/** Exact page mappings travel with the delivered file; names never choose a target. */
export function pageReplacements(documents: readonly SourceDocumentDto[]): Map<string, PageSource> {
  const edges = new Map<string, PageSource>();
  for (const document of documents) {
    for (const replacement of document.replacesPages ?? []) {
      const key = pageKey({ ...replacement, revisionRef: replacement.revisionRef ?? null });
      const target = pageSource(document, replacement.newPageIndex);
      const previous = edges.get(key);
      if (previous && pageKey(previous) !== pageKey(target)) throw new Error("A drawing page has competing replacements. Choose its current revision before returning another update.");
      edges.set(key, target);
    }
  }
  const result = new Map<string, PageSource>();
  for (const [key, first] of edges) {
    const visited = new Set([key]);
    let target = first;
    while (true) {
      const nextKey = pageKey(target);
      if (visited.has(nextKey)) throw new Error("A drawing replacement refers back to an earlier page.");
      visited.add(nextKey);
      const next = edges.get(nextKey);
      if (!next) break;
      target = next;
    }
    result.set(key, target);
  }
  return result;
}

export function imageSource(element: Record<string, unknown>): PageSource | null {
  if (element.type !== "image") return null;
  const custom = element.customData;
  if (!custom || typeof custom !== "object") return null;
  const source = (custom as Record<string, unknown>).sourceDocument;
  if (!source || typeof source !== "object") return null;
  const row = source as Record<string, unknown>;
  if (typeof row.runId !== "string" || typeof row.assetSha256 !== "string"
    || !(row.revisionRef === null || typeof row.revisionRef === "string")
    || typeof row.pageIndex !== "number" || !Number.isInteger(row.pageIndex) || row.pageIndex < 0) return null;
  return { runId: row.runId, assetSha256: row.assetSha256,
    revisionRef: row.revisionRef, pageIndex: row.pageIndex };
}

export function findSource(documents: readonly SourceDocumentDto[], source: PageSource): SourceDocumentDto | undefined {
  return documents.find((document) => document.runId === source.runId
    && document.assetSha256 === source.assetSha256
    && (document.revisionRef ?? null) === source.revisionRef
    && document.pages.some((page) => page.pageIndex === source.pageIndex));
}


export function isTracingPaperReview(document: Pick<SourceDocumentDto, "viewRecipe">): boolean {
  return document.viewRecipe?.kind === "tracing-paper-review" && document.viewRecipe?.schema === "TracingPaperSnapshot@1";
}

export function boardDocumentFrameName(document: SourceDocumentDto, pageIndex: number): string {
  const page = `${pageIndex + 1}/${document.pageCount}`;
  return isTracingPaperReview(document) ? `Tracing Paper review · ${document.fileName} · ${page}` : `${document.fileName} · ${page}`;
}

/** Select one image explicitly or through its native frame; marks never choose a source. */
export function selectedPageSource(
  elements: readonly Record<string, unknown>[], selectedElementIds: Readonly<Record<string, boolean>>,
): PageSource | null {
  const visible = elements.filter((element) => !element.isDeleted);
  const byId = new Map(visible.map((element) => [String(element.id), element]));
  const selected = new Set<string>();
  const include = (element: Record<string, unknown>) => {
    const id = String(element.id);
    if (selected.has(id)) return;
    selected.add(id);
    if (element.type === "frame") visible.filter((item) => item.frameId === id).forEach(include);
  };
  for (const [id, active] of Object.entries(selectedElementIds)) {
    if (!active) continue;
    const element = byId.get(id);
    if (!element) return null;
    include(element);
  }
  const images = visible.filter((element) => element.type === "image" && selected.has(String(element.id)));
  return images.length === 1 ? imageSource(images[0]) : null;
}

/** New deliveries occupy fresh space; existing elements are never arranged again. */
export function nextDocumentPosition(elements: readonly Record<string, unknown>[]): { x: number; y: number } {
  const visible = elements.filter((element) => !element.isDeleted
    && [element.x, element.y, element.width, element.height].every((value) => typeof value === "number" && Number.isFinite(value)));
  if (!visible.length) return { x: 80, y: 80 };
  return { x: Math.max(...visible.map((element) => Number(element.x) + Math.abs(Number(element.width)))) + 96,
    y: Math.min(...visible.map((element) => Number(element.y))) };
}

/** The space between two pages in a row, as between a new drawing and existing work. */
const PAGE_GAP = 96;
/** What Excalidraw adds around the image of a page frame it builds (``convertToExcalidrawElements``). */
const PAGE_FRAME_PADDING = 10;

type Box = { left: number; top: number; right: number; bottom: number };

function boxOf(element: Record<string, unknown>): Box | null {
  const [x, y, width, height] = [element.x, element.y, element.width, element.height];
  if (![x, y, width, height].every((value) => typeof value === "number" && Number.isFinite(value))) return null;
  const [left, top] = [Number(x), Number(y)];
  return { left: Math.min(left, left + Number(width)), top: Math.min(top, top + Number(height)),
    right: Math.max(left, left + Number(width)), bottom: Math.max(top, top + Number(height)) };
}

/**
 * The area an element covers on the board. Excalidraw keeps a line, an arrow or a freehand
 * stroke at its first point, with ``points`` relative to it, so one drawn leftwards or upwards
 * reaches past ``x``/``y``; a rotated element turns about its centre. Both are measured here.
 */
function extentOf(element: Record<string, unknown>): Box | null {
  let box = boxOf(element);
  if (!box) return null;
  const points = element.points;
  if (Array.isArray(points) && points.length && points.every((point) => Array.isArray(point)
      && point.length >= 2 && Number.isFinite(point[0]) && Number.isFinite(point[1]))) {
    const xs = points.map((point) => Number(element.x) + Number(point[0]));
    const ys = points.map((point) => Number(element.y) + Number(point[1]));
    box = { left: Math.min(...xs), top: Math.min(...ys), right: Math.max(...xs), bottom: Math.max(...ys) };
  }
  const angle = Number(element.angle);
  if (!angle || !Number.isFinite(angle)) return box;
  const [cx, cy] = [(box.left + box.right) / 2, (box.top + box.bottom) / 2];
  const [cos, sin] = [Math.cos(angle), Math.sin(angle)];
  const corners = [[box.left, box.top], [box.right, box.top], [box.right, box.bottom], [box.left, box.bottom]]
    .map(([px, py]) => [cx + (px - cx) * cos - (py - cy) * sin, cy + (px - cx) * sin + (py - cy) * cos]);
  return { left: Math.min(...corners.map(([px]) => px)), top: Math.min(...corners.map(([, py]) => py)),
    right: Math.max(...corners.map(([px]) => px)), bottom: Math.max(...corners.map(([, py]) => py)) };
}

/**
 * Where a new page goes so it lines up with the pages already on the board (#615), or null when there is none.
 *
 * The anchor is the page the person has selected (its image, or the frame holding it) or, with none
 * selected, the page placed last. The new page takes the anchor's top edge and height, keeps its own
 * ``aspect`` (width / height), and sits to the right of the anchor's frame by the usual gap, moved on
 * past anything already in that row so it never covers another element. Nothing is repositioned.
 */
export function nextPagePlacement(
  elements: readonly Record<string, unknown>[], selectedElementIds: Readonly<Record<string, boolean>>, aspect: number,
): { x: number; y: number; width: number; height: number } | null {
  if (!(aspect > 0) || !Number.isFinite(aspect)) return null;
  const visible = elements.filter((element) => !element.isDeleted);
  const pages = visible.filter((element) => {
    const box = imageSource(element) && boxOf(element);
    return !!box && box.bottom > box.top;
  });
  if (!pages.length) return null;
  const selected = new Set(Object.entries(selectedElementIds).filter(([, active]) => active).map(([id]) => id));
  const chosen = pages.filter((page) => selected.has(String(page.id))
    || (typeof page.frameId === "string" && selected.has(page.frameId)));
  const anchor = (chosen.length ? chosen : pages).at(-1)!;
  const image = boxOf(anchor)!;
  const frame = typeof anchor.frameId === "string"
    ? visible.find((element) => element.id === anchor.frameId && element.type === "frame") : undefined;
  const outer = (frame && boxOf(frame)) || image;
  const height = image.bottom - image.top;
  const width = height * aspect;
  const others = visible.map(extentOf).filter((box): box is Box => box !== null);
  let x = outer.right + PAGE_GAP + PAGE_FRAME_PADDING;
  // Each pass moves past every element the new frame would cover, so it ends within one pass per element.
  for (let pass = 0; pass <= others.length; pass++) {
    const left = x - PAGE_FRAME_PADDING, right = x + width + PAGE_FRAME_PADDING;
    const top = image.top - PAGE_FRAME_PADDING, bottom = image.bottom + PAGE_FRAME_PADDING;
    const covered = others.filter((box) => box.left < right && box.right > left && box.top < bottom && box.bottom > top);
    if (!covered.length) break;
    x = Math.max(...covered.map((box) => box.right)) + PAGE_GAP + PAGE_FRAME_PADDING;
  }
  return { x, y: image.top, width, height };
}

/** The original file bytes are preserved; only a missing browser MIME label is repaired. */
export function documentMime(file: Pick<File, "name" | "type">): SourceDocumentDto["mimeType"] | null {
  if (file.type === "application/pdf" || file.type === "image/png" || file.type === "image/jpeg") return file.type;
  if (file.type && file.type !== "application/octet-stream") return null;
  const extension = file.name.split(".").pop()?.toLowerCase();
  return extension === "pdf" ? "application/pdf" : extension === "png" ? "image/png"
    : extension === "jpg" || extension === "jpeg" ? "image/jpeg" : null;
}

export function isBoardConflict(error: unknown): boolean {
  return !!error && typeof error === "object" && "code" in error
    && ["BOARD_STALE", "BOARD_CONFLICT", "BOARD_BINDING_MISMATCH"].includes(String(error.code));
}
