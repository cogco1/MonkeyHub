import type { BoardDto, SourceDocumentDto } from "../../api/generated";

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

export function documentUrl(currentUrl: string, source: PageSource): string {
  const url = new URL(currentUrl);
  url.searchParams.set("view", "documents");
  url.searchParams.set("documentRun", source.runId);
  url.searchParams.set("documentSource", source.assetSha256);
  url.searchParams.set("documentPage", String(source.pageIndex));
  if (source.revisionRef === null) url.searchParams.delete("documentRevision");
  else url.searchParams.set("documentRevision", source.revisionRef);
  return url.href;
}

/** The way back from a page this tab opened: the board, no longer addressing that page. */
export function boardUrl(currentUrl: string): string {
  const url = new URL(currentUrl);
  url.searchParams.set("view", "board");
  for (const key of ["documentRun", "documentSource", "documentPage", "documentRevision"]) {
    url.searchParams.delete(key);
  }
  return url.href;
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
