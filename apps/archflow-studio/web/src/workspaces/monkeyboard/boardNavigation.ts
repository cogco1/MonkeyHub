/**
 * Opening a registered page from the board, and coming back to the same board.
 *
 * The board never draws the page itself: a double click names the exact
 * registered source under the pointer, and the existing document editor is
 * opened on it. What the operator was looking at travels with that handoff so
 * the return is the board they left, not a refitted one.
 */

import { imageSource, type PageSource } from "./boardScene";

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
    const x = Number(element.x), y = Number(element.y);
    const width = Number(element.width), height = Number(element.height);
    const angle = Number(element.angle ?? 0);
    if (![x, y, width, height, angle].every((value) => Number.isFinite(value))) continue;
    // A rotated page keeps its own rectangle; the point enters that frame first.
    const centreX = x + width / 2, centreY = y + height / 2;
    const cos = Math.cos(-angle), sin = Math.sin(-angle);
    const dx = point.x - centreX, dy = point.y - centreY;
    const localX = dx * cos - dy * sin + centreX;
    const localY = dx * sin + dy * cos + centreY;
    if (localX < Math.min(x, x + width) || localX > Math.max(x, x + width)) continue;
    if (localY < Math.min(y, y + height) || localY > Math.max(y, y + height)) continue;
    return source;
  }
  return null;
}
