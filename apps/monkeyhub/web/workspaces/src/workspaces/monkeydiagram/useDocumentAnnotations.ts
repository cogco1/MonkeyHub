import { useEffect, useState, useSyncExternalStore } from "react";

import { StudioApiError, TRANSPORT_ERROR, UNSUPPORTED_REQUEST, asStudioApiError, type StudioClient } from "../../api/client";
import { useStudio } from "../../api/ProjectRuntimeContext";
import type { DocumentAnnotationRefDto, DocumentGestureDto, DocumentTracingCalibrationDto } from "../../api/generated";

export interface DocumentAnnotationsOptions {
  projectId: string;
  runId: string;
  assetSha256: string | null;
  pageIndex: number;
  revisionSha256?: string | null;
  drawingRevisionRef?: string | null;
}

interface Draft {
  annotations: readonly DocumentGestureDto[];
  comment: string;
  tracingCalibration: DocumentTracingCalibrationDto | null;
}

interface PageSnapshot extends Draft {
  ready: boolean;
  saving: boolean;
  dirty: boolean;
  error: StudioApiError | null;
  canUndo: boolean;
  canRedo: boolean;
  readOnly: boolean;
}

export interface DocumentAnnotationsHandle extends PageSnapshot {
  changeAnnotations(next: readonly DocumentGestureDto[]): void;
  setComment(text: string): void;
  setTracingCalibration(value: DocumentTracingCalibrationDto | null): void;
  undo(): void;
  redo(): void;
  save(): Promise<DocumentAnnotationRefDto>;
  reload(): Promise<void>;
}

interface QueuedSave {
  draft: Draft;
  waiters: {
    resolve(ref: DocumentAnnotationRefDto): void;
    reject(error: StudioApiError): void;
  }[];
}

function copyInk(annotations: readonly DocumentGestureDto[]): DocumentGestureDto[] {
  return annotations.map((gesture) => ({ ...gesture, points: gesture.points.map(([x, y]) => [x, y]) }));
}

function sameDraft(a: Draft | null, b: Draft): boolean {
  return a !== null && JSON.stringify(a) === JSON.stringify(b);
}

function unavailable(detail: string): StudioApiError {
  return new StudioApiError({ status: 0, code: UNSUPPORTED_REQUEST, detail });
}

function createPage(studio: StudioClient, scope: DocumentAnnotationsOptions) {
  const readOnly = scope.assetSha256 === null || scope.revisionSha256 != null;
  const listeners = new Set<() => void>();
  let history: Draft[] = [{ annotations: [], comment: "", tracingCalibration: null }];
  let cursor = 0;
  let acknowledged: Draft | null = null;
  let revision: string | null = null;
  let ready = false;
  let paused = false;
  let error: StudioApiError | null = null;
  let queue: QueuedSave[] = [];
  let writing: Promise<void> | null = null;
  let reading: Promise<void> | null = null;
  let typing = false;
  let textTimer: ReturnType<typeof setTimeout> | undefined;
  const present = () => history[cursor];
  const state = (): PageSnapshot => ({
    ...present(), ready, readOnly, saving: writing !== null,
    dirty: ready && (queue.length > 0 || !sameDraft(acknowledged, present())),
    error, canUndo: !readOnly && cursor > 0, canRedo: !readOnly && cursor < history.length - 1,
  });
  let snapshot = state();
  const publish = () => {
    snapshot = state();
    listeners.forEach((listener) => listener());
  };
  const reference = (sha256: string): DocumentAnnotationRefDto => ({
    runId: scope.runId, assetSha256: scope.assetSha256!, pageIndex: scope.pageIndex,
    revisionSha256: sha256,
    ...(scope.drawingRevisionRef ? { drawingRevisionRef: scope.drawingRevisionRef } : {}),
  });
  const rejectWaiters = (reason: StudioApiError) => {
    for (const job of queue) {
      for (const waiter of job.waiters.splice(0)) waiter.reject(reason);
    }
  };

  function startQueue() {
    if (writing || reading || paused || !ready || readOnly || queue.length === 0) return;
    writing = Promise.resolve().then(async () => {
      while (queue.length > 0 && !paused) {
        const job = queue[0];
        try {
          const response = await studio.saveDocumentAnnotations({
            projectId: scope.projectId, runId: scope.runId, assetSha256: scope.assetSha256!,
            pageIndex: scope.pageIndex, baseRevisionSha256: revision,
            ...(scope.drawingRevisionRef ? { drawingRevisionRef: scope.drawingRevisionRef } : {}),
            annotations: copyInk(job.draft.annotations), comment: job.draft.comment,
            tracingCalibration: job.draft.tracingCalibration,
          });
          if (response.revisionSha256 === null) {
            throw new StudioApiError({
              status: 200, code: TRANSPORT_ERROR, detail: "The saved page did not return an annotation revision.",
            });
          }
          if ((response.drawingRevisionRef ?? null) !== (scope.drawingRevisionRef ?? null)) throw unavailable("The saved annotations belong to another drawing revision.");
          revision = response.revisionSha256;
          acknowledged = job.draft;
          queue.shift();
          for (const waiter of job.waiters) waiter.resolve(reference(revision));
          publish();
        } catch (cause) {
          error = asStudioApiError(cause);
          paused = true;
          rejectWaiters(error);
          publish();
        }
      }
    }).finally(() => {
      writing = null;
      publish();
      startQueue();
    });
    publish();
  }

  function enqueue(draft: Draft, force = false): QueuedSave | null {
    const last = queue[queue.length - 1];
    if (last && sameDraft(last.draft, draft)) return last;
    if (!force && !last && revision !== null && sameDraft(acknowledged, draft)) return null;
    const job: QueuedSave = { draft, waiters: [] };
    queue.push(job);
    publish();
    startQueue();
    return job;
  }

  function finishTyping(): boolean {
    if (textTimer !== undefined) clearTimeout(textTimer);
    textTimer = undefined;
    const wasTyping = typing;
    typing = false;
    return wasTyping;
  }

  function push(next: Draft): boolean {
    if (sameDraft(present(), next)) return false;
    history = history.slice(0, cursor + 1);
    history.push(next);
    cursor += 1;
    publish();
    return true;
  }

  function read(explicit: boolean): Promise<void> {
    if (reading) return reading;
    if (scope.assetSha256 === null) return Promise.resolve();
    paused = true;
    finishTyping();
    reading = Promise.resolve().then(async () => {
      // A GET cannot race an earlier PUT's acknowledgement and adopt an older CAS base.
      await writing;
      try {
        const response = await studio.documentAnnotations(
          scope.runId, scope.assetSha256!, scope.pageIndex, scope.revisionSha256, scope.drawingRevisionRef,
        );
        if ((response.drawingRevisionRef ?? null) !== (scope.drawingRevisionRef ?? null)) throw unavailable("The annotations belong to another drawing revision.");
        const next = { annotations: copyInk(response.annotations), comment: response.comment,
          tracingCalibration: response.tracingCalibration ?? null };
        finishTyping();
        rejectWaiters(unavailable("Reading the latest page interrupted this save; the local draft remains in Undo."));
        queue = [];
        if (explicit && ready) push(next);
        else { history = [next]; cursor = 0; }
        acknowledged = next;
        revision = response.revisionSha256;
        ready = true;
        paused = false;
        error = null;
      } catch (cause) {
        error = asStudioApiError(cause);
        paused = true;
        rejectWaiters(error);
      }
    }).finally(() => {
      reading = null;
      publish();
    });
    publish();
    return reading;
  }

  const load = () => ready ? Promise.resolve() : read(false);
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    load,
    reload: () => read(true),
    changeAnnotations(next: readonly DocumentGestureDto[]) {
      if (!ready || readOnly) return;
      const typed = finishTyping();
      if (push({ ...present(), annotations: copyInk(next) }) || typed) enqueue(present());
    },
    setComment(comment: string) {
      if (!ready || readOnly || present().comment === comment) return;
      const next = { ...present(), comment };
      if (typing) { history[cursor] = next; publish(); }
      else { push(next); typing = true; }
      if (textTimer !== undefined) clearTimeout(textTimer);
      textTimer = setTimeout(() => {
        textTimer = undefined;
        typing = false;
        enqueue(present());
      }, 500);
    },
    setTracingCalibration(value: DocumentTracingCalibrationDto | null) {
      if (!ready || readOnly) return;
      const typed = finishTyping();
      const tracingCalibration = value ? { ...value, origin: [...value.origin] as [number, number],
        axisPoint: [...value.axisPoint] as [number, number] } : null;
      if (push({ ...present(), tracingCalibration }) || typed) enqueue(present());
    },
    undo() {
      if (!ready || readOnly || cursor === 0) return;
      finishTyping();
      cursor -= 1;
      publish();
      enqueue(present());
    },
    redo() {
      if (!ready || readOnly || cursor === history.length - 1) return;
      finishTyping();
      cursor += 1;
      publish();
      enqueue(present());
    },
    async save(): Promise<DocumentAnnotationRefDto> {
      if (readOnly) throw unavailable("Select a current document page before saving; historical revisions are read-only.");
      if (reading && ready) throw unavailable("Wait for the page reload to finish before saving its annotations.");
      if (!ready) await load();
      if (!ready) throw error ?? unavailable("The document page has not loaded.");
      finishTyping();
      if (error?.status === 409) throw error;
      const retrying = error !== null;
      paused = false;
      error = null;
      const job = enqueue(present(), retrying);
      if (job === null) { publish(); return reference(revision!); }
      const saved = new Promise<DocumentAnnotationRefDto>((resolve, reject) => {
        job.waiters.push({ resolve, reject });
      });
      startQueue();
      return saved;
    },
  };
}

/** Per-hook page drafts; the transitions can also be exercised without a browser. */
export function createDocumentAnnotationsController(studio: StudioClient) {
  const pages = new Map<string, ReturnType<typeof createPage>>();
  return {
    page(options: DocumentAnnotationsOptions) {
      const key = JSON.stringify([
        options.projectId, options.runId, options.assetSha256, options.pageIndex, options.revisionSha256 ?? null, options.drawingRevisionRef ?? null,
      ]);
      let page = pages.get(key);
      if (!page) { page = createPage(studio, { ...options }); pages.set(key, page); }
      return page;
    },
  };
}

export function useDocumentAnnotations(
  options: DocumentAnnotationsOptions,
  existingController?: ReturnType<typeof createDocumentAnnotationsController>,
): DocumentAnnotationsHandle {
  const studio = useStudio();
  const [localController] = useState(() => createDocumentAnnotationsController(studio));
  const controller = existingController ?? localController;
  const page = controller.page(options);
  const snapshot = useSyncExternalStore(page.subscribe, page.getSnapshot, page.getSnapshot);
  useEffect(() => { void page.load(); }, [page]);
  return {
    ...snapshot, changeAnnotations: page.changeAnnotations, setComment: page.setComment, setTracingCalibration: page.setTracingCalibration,
    undo: page.undo, redo: page.redo, save: page.save, reload: page.reload,
  };
}
