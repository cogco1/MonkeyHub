import type { StudioClient } from "../../api/project-runtime/client";
import type { RenderPageRefDto, SourceDocumentDto } from "../../api/project-runtime/generated";
import { findSource, imageSource, pageKey, pageSource, type PageSource } from "./boardScene";

/**
 * A render discussion the Board hands to the Hub's own conversation (#253): the
 * exact registered source page, up to three reference pages and the person's
 * words. The Hub places it in its composer; nothing here sends a message,
 * starts a model, generates an image or saves the Board.
 */
export type BoardRenderChatRequest = {
  projectId: string;
  content: string;
  source: RenderPageRefDto;
  references: RenderPageRefDto[];
};

/** How many reference pages one discussion takes, as the Hub conversation does. */
export const RENDER_REFERENCE_LIMIT = 3;

export type BoardRenderErrorCode = "EMPTY" | "SOURCE_REQUIRED" | "TOO_MANY_REFERENCES" | "DUPLICATE"
  | "PROJECT_CHANGED" | "SOURCE_CHANGED" | "REFERENCE_CHANGED" | "UNSUPPORTED" | "CONVERSATION_UNAVAILABLE" | "CONVERSATION_BUSY";

/**
 * Why a discussion was not handed over. A host that cannot take a request
 * throws one too (PROJECT_CHANGED, CONVERSATION_UNAVAILABLE, CONVERSATION_BUSY),
 * so the Board's dialog stays open with the person's words and the reason.
 */
export class BoardRenderError extends Error {
  constructor(readonly code: BoardRenderErrorCode, message: string) {
    super(message);
    this.name = "BoardRenderError";
  }
}

/** A registered PNG or JPEG page, which the existing Render reads; a plain image needs no model. */
export function isRenderImage(document: Pick<SourceDocumentDto, "mimeType">): boolean {
  return document.mimeType === "image/png" || document.mimeType === "image/jpeg";
}

/** Pages a later registration has replaced; the Board already shows their replacements in place. */
function replacedPages(documents: readonly SourceDocumentDto[]): Set<string> {
  return new Set(documents.flatMap((document) => (document.replacesPages ?? [])
    .map((page) => pageKey({ runId: page.runId, assetSha256: page.assetSha256, revisionRef: page.revisionRef ?? null, pageIndex: page.pageIndex }))));
}

/**
 * The registered image pages a Board selection names, each once: an image
 * selected itself or through its native frame. Marks, labels, position and
 * nearby objects never add a page, and a file name never identifies one.
 */
export function selectedRenderPages(elements: readonly Record<string, unknown>[],
  selectedElementIds: Readonly<Record<string, boolean>>, documents: readonly SourceDocumentDto[]): PageSource[] {
  const visible = elements.filter((element) => !element.isDeleted);
  const chosen = new Set(Object.entries(selectedElementIds).flatMap(([id, active]) => active ? [id] : []));
  const frames = new Set(visible.filter((element) => element.type === "frame" && chosen.has(String(element.id)))
    .map((element) => String(element.id)));
  const pages = new Map<string, PageSource>();
  for (const element of visible) {
    if (element.type !== "image") continue;
    if (!chosen.has(String(element.id)) && !(typeof element.frameId === "string" && frames.has(element.frameId))) continue;
    const source = imageSource(element);
    const document = source && findSource(documents, source);
    if (source && document && isRenderImage(document)) pages.set(pageKey(source), source);
  }
  return [...pages.values()];
}

/** Registered image pages that can still be added as references: every one not chosen and not replaced. */
export function renderReferenceChoices(documents: readonly SourceDocumentDto[], chosen: readonly PageSource[]): PageSource[] {
  const taken = new Set(chosen.map(pageKey)), replaced = replacedPages(documents);
  return documents.filter(isRenderImage)
    .flatMap((document) => document.pages.map((page) => pageSource(document, page.pageIndex)))
    .filter((page) => !taken.has(pageKey(page)) && !replaced.has(pageKey(page)));
}

/** The roles as chosen: one source, and at most three distinct references other than it. */
export function checkRenderRoles(source: PageSource | null, references: readonly PageSource[]): asserts source is PageSource {
  if (!source) throw new BoardRenderError("SOURCE_REQUIRED", "Choose the source image.");
  if (references.length > RENDER_REFERENCE_LIMIT) {
    throw new BoardRenderError("TOO_MANY_REFERENCES", `Choose at most ${RENDER_REFERENCE_LIMIT} reference images.`);
  }
  const keys = [source, ...references].map(pageKey);
  if (new Set(keys).size !== keys.length) {
    throw new BoardRenderError("DUPLICATE", "The source and each reference must be different pages.");
  }
}

const pageRef = (page: PageSource): RenderPageRefDto =>
  ({ runId: page.runId, assetSha256: page.assetSha256, revisionRef: page.revisionRef, pageIndex: page.pageIndex });

/**
 * Re-read the registered documents and name exactly the chosen pages.
 * Each must still be a registered PNG or JPEG page of this project that no
 * newer registration has replaced. Only the document list is read, and
 * nothing is written.
 */
export async function prepareBoardRenderChatRequest(studio: Pick<StudioClient, "documents">, projectId: string,
  content: string, source: PageSource | null, references: readonly PageSource[]): Promise<BoardRenderChatRequest> {
  const words = content.trim();
  if (!words) throw new BoardRenderError("EMPTY", "Write what you would like to discuss or render.");
  checkRenderRoles(source, references);
  const list = await studio.documents();
  if (list.projectId !== projectId) throw new BoardRenderError("PROJECT_CHANGED", "The document list belongs to another project.");
  const replaced = replacedPages(list.documents);
  const current = (page: PageSource, code: "SOURCE_CHANGED" | "REFERENCE_CHANGED") => {
    const document = findSource(list.documents, page);
    if (!document || document.projectId !== projectId || replaced.has(pageKey(page))) {
      throw new BoardRenderError(code, code === "SOURCE_CHANGED"
        ? "The source image is no longer this exact registered page. Select it again."
        : "A reference image is no longer this exact registered page. Choose the references again.");
    }
    if (!isRenderImage(document)) throw new BoardRenderError("UNSUPPORTED", "Only registered PNG or JPEG images can be discussed for a render.");
  };
  current(source, "SOURCE_CHANGED");
  for (const page of references) current(page, "REFERENCE_CHANGED");
  return { projectId, content: words, source: pageRef(source), references: references.map(pageRef) };
}

/**
 * Check the chosen pages, then offer the request to the host conversation.
 * The host takes it or refuses it by throwing, and a refusal reaches the
 * caller, which closes its dialog only once this resolves. A request that
 * fails its own checks is never offered; nothing is saved either way.
 */
export async function handOverBoardRender(studio: Pick<StudioClient, "documents">, projectId: string, content: string,
  source: PageSource | null, references: readonly PageSource[], handOver: (request: BoardRenderChatRequest) => void,
): Promise<BoardRenderChatRequest> {
  const request = await prepareBoardRenderChatRequest(studio, projectId, content, source, references);
  handOver(request);
  return request;
}
