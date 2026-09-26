import { useEffect, useRef } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import type { StudioClient } from "../../api/client";
import type { ModelSourceDto } from "../../api/generated";

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

/** Runs after the viewport is ready, never on the candidate execution chain. */
export function useRetainedModelPreview(source: ModelSourceDto | null, ready: boolean,
  capture: () => Promise<Blob | null>, current: () => ModelSourceDto | null) {
  const studio = useStudio();
  const latest = useRef({ capture, current, ready });
  latest.current = { capture, current, ready };
  const key = previewSourceKey(source);
  useEffect(() => {
    if (!ready || !source) return;
    let live = true;
    const timer = window.setTimeout(() => {
      const isCurrent = () => live && latest.current.ready && previewSourceKey(latest.current.current()) === key;
      void retainModelPreview(studio, source, () => latest.current.capture(), isCurrent).then((saved) => {
        if (saved) window.dispatchEvent(new CustomEvent(MODEL_PREVIEW_RETAINED, { detail: { studio, key } }));
      }).catch(() => { /* A preview failure never interrupts modeling or candidate completion. */ });
    }, 250);
    return () => { live = false; window.clearTimeout(timer); };
  }, [studio, key, ready]);
}
