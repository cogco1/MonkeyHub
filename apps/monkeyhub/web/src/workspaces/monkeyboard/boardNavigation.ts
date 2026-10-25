/**
 * Opening a registered page from the board, and coming back to the same board.
 *
 * The board never draws the page itself: a double click names the exact
 * registered source under the pointer, and the existing document editor is
 * opened on it. What the operator was looking at travels with that handoff so
 * the return is the board they left, not a refitted one.
 */

import type { SourceDocumentDto } from "../../api/project-runtime/generated";
import { sceneBoxContains } from "../../features/canvas/sceneHit";
import { findSource, imageSource, type PageSource } from "./boardScene";

/** The part of the live canvas that is the operator's place on the board. */
export interface BoardViewState {
  scrollX: number;
  scrollY: number;
  zoom: number;
  selectedElementIds: Record<string, true>;
  selectedGroupIds: Record<string, true>;
}

export interface BoardDocumentOpen {
  source: PageSource;
  view: BoardViewState;
}

interface BoardAppState {
  scrollX: number;
  scrollY: number;
  zoom: { value: number };
  selectedElementIds: Readonly<Record<string, boolean>>;
  selectedGroupIds: Readonly<Record<string, boolean>>;
}

const selected = (ids: Readonly<Record<string, boolean>>): Record<string, true> =>
  Object.fromEntries(Object.keys(ids).filter((id) => ids[id]).map((id) => [id, true as const]));

export function captureBoardView(appState: BoardAppState): BoardViewState {
  return {
    scrollX: appState.scrollX, scrollY: appState.scrollY, zoom: appState.zoom.value,
    selectedElementIds: selected(appState.selectedElementIds),
    selectedGroupIds: selected(appState.selectedGroupIds),
  };
}

/**
 * The saved place, expressed as canvas state again. A selection is only
 * restored for elements the board still has: the page may have been replaced
 * or removed while its editor was open.
 */
export function boardViewAppState(view: BoardViewState, elements: readonly Record<string, unknown>[]) {
  const present = new Set(elements.filter((element) => !element.isDeleted).map((element) => String(element.id)));
  const groups = new Set(elements.flatMap((element) => Array.isArray(element.groupIds) ? element.groupIds.map(String) : []));
  const selectedElementIds = selected(Object.fromEntries(Object.entries(view.selectedElementIds)
    .map(([id, active]) => [id, active && present.has(id)])));
  const selectedGroupIds = selected(Object.fromEntries(Object.entries(view.selectedGroupIds)
    .map(([id, active]) => [id, active && groups.has(id)])));
  return { scrollX: view.scrollX, scrollY: view.scrollY, zoom: { value: view.zoom },
    selectedElementIds, selectedGroupIds };
}

/**
 * The registered page under a scene point, topmost first. Marks, frames and
 * unbound images never name a page; only an image carrying its exact source
 * document does.
 */
export function pageSourceAt(
  elements: readonly Record<string, unknown>[], point: { x: number; y: number },
): PageSource | null {
  for (let index = elements.length - 1; index >= 0; index -= 1) {
    const element = elements[index];
    if (element.isDeleted) continue;
    const source = imageSource(element);
    if (source === null) continue;
    // A rotated page keeps its own rectangle (the shared canvas hit test).
    if (!sceneBoxContains({ x: Number(element.x), y: Number(element.y), width: Number(element.width),
      height: Number(element.height), angle: Number(element.angle ?? 0) }, point)) continue;
    return source;
  }
  return null;
}

/**
 * Where a board opens when nobody left a place on it: the pages of the design's current step.
 *
 * A board that grows with the design keeps every earlier version, and its
 * middle is an old one (thi-hemp opened on "V12 · 当前版本" while the model was
 * V16, written far above it). A page belongs to the current step when its
 * document was registered in that step's run or drawn from its model; the
 * text written just above those pages, their title, comes with them. Nothing
 * of the step on the board, or no step known, leaves the board opening on all
 * of its content as before.
 */
export function currentStepElements<T extends Record<string, unknown>>(
  elements: readonly T[], documents: readonly SourceDocumentDto[], runId: string | null,
): T[] {
  if (!runId) return [];
  const pages = elements.filter((element) => {
    if (element.isDeleted) return false;
    const source = imageSource(element);
    if (source === null) return false;
    const document = findSource(documents, source);
    return source.runId === runId || document?.runId === runId || document?.modelSource?.runId === runId;
  });
  if (pages.length === 0) return [];
  const box = (element: Record<string, unknown>) => ({ left: Number(element.x), top: Number(element.y),
    right: Number(element.x) + Number(element.width), bottom: Number(element.y) + Number(element.height) });
  const around = pages.map(box).reduce((all, one) => ({ left: Math.min(all.left, one.left), top: Math.min(all.top, one.top),
    right: Math.max(all.right, one.right), bottom: Math.max(all.bottom, one.bottom) }));
  // A row's title sits just above it and starts at the row's left edge, which may be left of these pages.
  const above = Math.max(400, (around.bottom - around.top) / 2), wide = around.right - around.left;
  const titles = elements.filter((element) => {
    if (element.isDeleted || element.type !== "text") return false;
    const text = box(element);
    return text.bottom <= around.top && text.top >= around.top - above && text.right > around.left - wide && text.left < around.right;
  });
  return [...titles, ...pages];
}
