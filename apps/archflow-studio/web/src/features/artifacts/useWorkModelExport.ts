/**
 * Asking for an editable copy of the model on screen, and what came of it.
 *
 * The source is never chosen for the architect: it is the exact STEP of the
 * delivery actually being viewed, found through the receipt that model belongs
 * to. A view showing several seats, or a registered model with no export of
 * its own, has no single source and says so instead.
 *
 * There is one Rhino on the machine, so there is one export at a time: while
 * one runs the control says so wherever the architect moves next, rather than
 * looking ready and having the click dropped. What a request answers with is
 * what gets saved - an older row naming the same source is not this export's
 * file - and everything is kept under the source's own identity, because the
 * same bytes can be exported by more than one run.
 */

import { useCallback, useMemo, useRef, useState } from "react";

import type { ProjectArtifactDto } from "../../api/generated";
import { workModelOf, workModelSourceOf, type ViewedModel, type WorkModelSourceRefusal } from "./artifactSelection";

/** Why the control cannot act right now; `busy-elsewhere` is the one Rhino. */
export type WorkModelRefusal = WorkModelSourceRefusal | "busy-elsewhere";

export interface WorkModelControls {
  /** The exact STEP an editable copy would be made from, when there is one. */
  source: ProjectArtifactDto | null;
  refusal: WorkModelRefusal | null;
  /** The editable copy to save: this request's answer, or one made earlier. */
  exported: ProjectArtifactDto | null;
  busy: boolean;
  /** The server's own words for the last refusal of this source. */
  error: string | null;
  onExport(): void;
}

/** The source an export belongs to: one run's own copy of those exact bytes. */
export function workModelKeyOf(artifact: ProjectArtifactDto): string | null {
  return artifact.sha256 === null ? null : `${artifact.runId}:${artifact.sha256}`;
}

export function useWorkModelExport({
  rows,
  viewed,
  exportWorkModel,
  onExported,
}: {
  /** Every artifact the project listing knows, or null while it is unread. */
  rows: readonly ProjectArtifactDto[] | null;
  viewed: ViewedModel;
  /** The request itself; it answers with the artifact it produced. */
  exportWorkModel(sha256: string, runId: string): Promise<ProjectArtifactDto>;
  /** Called after a success so the listing catches up with the new file. */
  onExported?(): void | Promise<void>;
}): WorkModelControls {
  const [busy, setBusy] = useState<string | null>(null);
  const busyRef = useRef<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [exports, setExports] = useState<Record<string, ProjectArtifactDto>>({});

  const found = useMemo(
    () => (rows === null ? { refusal: "nothing-loaded" as const } : workModelSourceOf(rows, viewed)),
    [rows, viewed],
  );
  const source = "source" in found ? found.source : null;
  const key = source === null ? null : workModelKeyOf(source);

  const request = useCallback(async (artifact: ProjectArtifactDto) => {
    const identity = workModelKeyOf(artifact);
    if (identity === null || busyRef.current !== null) return;
    busyRef.current = identity;
    setBusy(identity);
    setErrors((current) => {
      if (!(identity in current)) return current;
      const next = { ...current };
      delete next[identity];
      return next;
    });
    try {
      const exported = await exportWorkModel(artifact.sha256 as string, artifact.runId);
      setExports((current) => ({ ...current, [identity]: exported }));
      await onExported?.();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setErrors((current) => ({ ...current, [identity]: message }));
    } finally {
      busyRef.current = null;
      setBusy(null);
    }
  }, [exportWorkModel, onExported]);

  return useMemo(() => ({
    source,
    refusal: ("refusal" in found ? found.refusal : null)
      ?? (busy !== null && busy !== key ? ("busy-elsewhere" as const) : null),
    exported: key === null || source === null
      ? null
      : exports[key] ?? (rows === null ? null : workModelOf(rows, source)),
    busy: busy !== null,
    error: key === null ? null : errors[key] ?? null,
    onExport: () => { if (source !== null) void request(source); },
  }), [busy, errors, exports, found, key, request, rows, source]);
}
