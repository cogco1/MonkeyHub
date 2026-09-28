import { useEffect, useRef } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import type { StudioClient } from "../../api/client";
import type { ModelSourceDto } from "../../api/generated";

/** A viewport screenshot of this model was retained (the chat's study previews follow it); the Design Tree does not. */
export const MODEL_PREVIEW_RETAINED = "monkeyhub:model-preview-retained";
export const previewSourceKey = (source: ModelSourceDto | null | undefined) => source
  ? JSON.stringify([source.runId, source.stateDigest, source.assetSha256]) : "";

/** Only pixels captured while this exact, unchanged model is still on screen. */
export async function retainModelPreview(studio: Pick<StudioClient, "modelPreview" | "capture">,
  source: ModelSourceDto, capture: () => Promise<Blob | null>, isCurrent: () => boolean) {
  if (!isCurrent() || await studio.modelPreview(source) || !isCurrent()) return false;
  const png = await capture();
  if (!png || !isCurrent()) return false;
  await studio.capture(source.runId, png, source);
  return true;
}

// The models each runtime already holds a preview of. A preview is of exact model content, so once
// one is known retained it stays retained: coming back to the model asks nothing again (#366).
const retainedPreviews = new WeakMap<object, Set<string>>();
const retainedOf = (studio: object) => {
  let keys = retainedPreviews.get(studio);
  if (!keys) { keys = new Set(); retainedPreviews.set(studio, keys); }
  return keys;
};

/**
 * Retains a viewport screenshot of the loaded model as a P036 document (#326), for the Board and other uses.
 * Runs after the viewport is ready, never on the candidate execution chain. The Design Tree and the thumbnails
 * show the projection cache's drawings instead (`modelThumbnails`, #367): nothing is looked up here for them.
 */
export function useRetainedModelPreview(source: ModelSourceDto | null, ready: boolean,
  capture: () => Promise<Blob | null>, current: () => ModelSourceDto | null) {
  const studio = useStudio();
  const latest = useRef({ capture, current, ready });
  latest.current = { capture, current, ready };
  const key = previewSourceKey(source);
  useEffect(() => {
    if (!ready || !source || retainedOf(studio).has(key)) return;
    let live = true;
    const timer = window.setTimeout(() => {
      const isCurrent = () => live && latest.current.ready && previewSourceKey(latest.current.current()) === key;
      const known = {
        modelPreview: async (asked: ModelSourceDto) => {
          const document = await studio.modelPreview(asked);
          if (document) retainedOf(studio).add(key);
          return document;
        },
        capture: studio.capture.bind(studio),
      };
      void retainModelPreview(known, source, () => latest.current.capture(), isCurrent).then((saved) => {
        if (saved) retainedOf(studio).add(key);
        if (saved) window.dispatchEvent(new CustomEvent(MODEL_PREVIEW_RETAINED, { detail: { studio, key } }));
      }).catch(() => { /* A preview failure never interrupts modeling or candidate completion. */ });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [studio, key, ready]);
}
