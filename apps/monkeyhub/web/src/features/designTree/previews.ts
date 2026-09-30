/**
 * The thumbnails the close level draws on its cards (#406, #367).
 *
 * Only a node that is in view at the close level asks for its image; each image
 * is read once per blob digest, a few at a time, and what the view no longer
 * wants is not started. A digest names its bytes, so an image once read is never
 * read again for it. A read that fails is not final: the server draws a lost
 * blob again, so the digest is read again once the view stops wanting it and
 * wants it back (the store drops and returns it) or after a while. The read
 * itself is the shared thumbnail cache (`modelThumbnails`), which the
 * inspector's thumbnail uses too. Nothing polls (ADR-008).
 */
import type { ModelSourceDto } from "../../api/generated";
import type { DesignTreeSource } from "./contract";
import type { Box, GrowthLayout } from "./layout";
import type { TreeNode } from "./model";
import type { ZoomLevel } from "./scene";

/** The model a node shows: a Stage's, an admitted option's, or the Working Head's. Other nodes have none. */
export function nodeModelSource(source: DesignTreeSource | null, node: TreeNode): ModelSourceDto | null {
  if (!source) return null;
  if (node.kind === "stage") return source.history.stages.find((stage) => stage.stageRef === node.stage?.ref)?.modelSource ?? null;
  if (node.kind === "candidate") return source.history.candidates?.find((candidate) => candidate.candidateId === node.candidate?.candidateId)?.modelSource ?? null;
  if (node.kind === "current") return source.workingSource.head?.modelSource ?? null;
  return null;
}

/** The part of the scene the canvas shows, from Excalidraw's scroll and zoom. */
export interface SceneView {
  readonly scrollX: number;
  readonly scrollY: number;
  readonly zoom: number;
  readonly width: number;
  readonly height: number;
}

/** The nodes whose cards the view shows at least in part; none unless the level is close. */
export function nodesWantingPreviews(layout: GrowthLayout, view: SceneView, level: ZoomLevel): string[] {
  if (level !== "close" || view.zoom <= 0 || view.width <= 0 || view.height <= 0) return [];
  const left = -view.scrollX, top = -view.scrollY, right = left + view.width / view.zoom, bottom = top + view.height / view.zoom;
  const inView = (box: Box) => box.x < right && box.x + box.width > left && box.y < bottom && box.y + box.height > top;
  return [...layout.nodes.values()].filter((placed) => inView(placed.card)).map((placed) => placed.id);
}

export interface PreviewLoader<T> {
  /** The keys the view shows now, in the order to read them: a key it no longer shows is not started. */
  want(keys: Iterable<string>): void;
  /** A read image, null when there is none, undefined while not yet read. */
  get(key: string): T | null | undefined;
  /** Something changed that may give failed keys an image now (a new projection landed): read them again. */
  retry(): void;
  dispose(): void;
}

/** How long a key whose read failed waits before a view that still wants it reads it again. */
export const PREVIEW_RETRY_MS = 30_000;

/**
 * Reads each key once, at most `limit` at a time, and keeps at most `keep` read
 * images, the least recently wanted going first (one the view wants stays). A
 * read that fails, or finds no image, counts as no image, never as an error,
 * and is not kept for good: the key is read again once it has left the wanted
 * keys and come back, or `retryMs` after the failure. `onChange` runs whenever
 * a key's image changes.
 */
export function createPreviewLoader<T>(load: (key: string) => Promise<T | null>, { limit = 3, keep = 64, retryMs = PREVIEW_RETRY_MS,
  now = Date.now, onChange }: {
  limit?: number;
  keep?: number;
  retryMs?: number;
  now?: () => number;
  onChange(): void;
}): PreviewLoader<T> {
  const read = new Map<string, T>();
  // Keys whose last read found nothing, and when.
  const failed = new Map<string, number>();
  const running = new Set<string>();
  let wanted: readonly string[] = [];
  let disposed = false;
  const trim = () => {
    const shown = new Set(wanted);
    for (const key of read.keys()) {
      if (read.size <= keep) return;
      if (!shown.has(key)) read.delete(key);
    }
  };
  const pump = () => {
    for (const key of wanted) {
      if (disposed || running.size >= limit) return;
      if (running.has(key) || read.has(key)) continue;
      const failedAt = failed.get(key);
      if (failedAt !== undefined && now() - failedAt < retryMs) continue;
      running.add(key);
      void Promise.resolve().then(() => load(key)).catch(() => null).then((value) => {
        running.delete(key);
        if (disposed) return;
        const changed = value === null ? !failed.has(key) : read.get(key) !== value;
        if (value === null) failed.set(key, now());
        else { failed.delete(key); read.set(key, value); }
        trim();
        if (changed) onChange();
        pump();
      });
    }
  };
  return {
    want(keys) {
      wanted = [...new Set(keys)];
      // A key no longer wanted is read afresh when it is wanted again: a failed read is not held against it.
      const shown = new Set(wanted);
      for (const key of failed.keys()) if (!shown.has(key)) failed.delete(key);
      // Wanted now: last out.
      for (const key of wanted) if (read.has(key)) { const value = read.get(key)!; read.delete(key); read.set(key, value); }
      pump();
    },
    get: (key) => read.get(key) ?? (failed.has(key) ? null : undefined),
    retry() {
      failed.clear();
      pump();
    },
    dispose() { disposed = true; wanted = []; },
  };
}
