/**
 * The retained preview images the close level draws on its cards (#406).
 *
 * Only a node that is in view at the close level asks for its image; each image
 * is read once per preview source, a few at a time, and what the view no longer
 * wants is not started. A retained preview that arrives later (the
 * `MODEL_PREVIEW_RETAINED` event) is read again; nothing polls (ADR-008). The
 * read itself is `readModelPreview`, the one the inspector's thumbnail uses.
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
  /** A preview was retained for this key: read it again when it is wanted, keeping the old image until then. */
  refresh(key: string): void;
  dispose(): void;
}

/**
 * Reads each key once, at most `limit` at a time. A read that fails counts as no
 * image, never as an error; `onChange` runs whenever a key's image changes.
 */
export function createPreviewLoader<T>(load: (key: string) => Promise<T | null>, { limit = 3, onChange }: {
  limit?: number;
  onChange(): void;
}): PreviewLoader<T> {
  const read = new Map<string, T | null>();
  const stale = new Set<string>();
  const running = new Map<string, number>();
  const generation = new Map<string, number>();
  let wanted: readonly string[] = [];
  let disposed = false;
  const pump = () => {
    for (const key of wanted) {
      if (disposed || running.size >= limit) return;
      if (running.has(key) || (read.has(key) && !stale.has(key))) continue;
      const started = generation.get(key) ?? 0;
      running.set(key, started);
      void Promise.resolve().then(() => load(key)).catch(() => null).then((value) => {
        running.delete(key);
        if (disposed) return;
        if ((generation.get(key) ?? 0) === started) {
          const changed = !read.has(key) || read.get(key) !== value;
          read.set(key, value);
          stale.delete(key);
          if (changed) onChange();
        }
        pump();
      });
    }
  };
  return {
    want(keys) { wanted = [...new Set(keys)]; pump(); },
    get: (key) => read.get(key),
    refresh(key) {
      generation.set(key, (generation.get(key) ?? 0) + 1);
      if (read.has(key)) stale.add(key);
      pump();
    },
    dispose() { disposed = true; wanted = []; },
  };
}
