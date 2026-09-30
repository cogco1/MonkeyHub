import { useEffect, useState, useSyncExternalStore } from "react";

import { StudioApiError, TRANSPORT_ERROR, UNSUPPORTED_REQUEST, asStudioApiError, type StudioClient } from "../../api/client";
import { useStudio } from "../../api/ProjectRuntimeContext";
import type { ModelAnnotationsDto, ModelGestureDto, ModelSourceDto } from "../../api/generated";

export interface ModelAnnotationsOptions {
  projectId: string;
  modelSource: ModelSourceDto | null;
}

interface Draft {
  annotations: readonly ModelGestureDto[];
  comment: string;
}

interface ModelSnapshot extends Draft {
  ready: boolean;
  saving: boolean;
  dirty: boolean;
  error: StudioApiError | null;
  canUndo: boolean;
  canRedo: boolean;
}

export interface ModelAnnotationsHandle extends ModelSnapshot {
  changeAnnotations(next: readonly ModelGestureDto[]): void;
  undo(): void;
  redo(): void;
  save(): Promise<ModelAnnotationsDto>;
  reload(): Promise<void>;
}

interface QueuedSave {
  draft: Draft;
  waiters: {
    resolve(snapshot: ModelAnnotationsDto): void;
    reject(error: StudioApiError): void;
  }[];
}

function copyInk(annotations: readonly ModelGestureDto[]): ModelGestureDto[] {
  // Camera, screen, model hits and world vectors all belong to that stroke's
  // snapshot. Copying only the outer array would let later edits alter history.
  return structuredClone([...annotations]);
}

function sameDraft(left: Draft | null, right: Draft): boolean {
  return left !== null && left.comment === right.comment && JSON.stringify(left.annotations) === JSON.stringify(right.annotations);
}

function unavailable(detail: string): StudioApiError {
  return new StudioApiError({ status: 0, code: UNSUPPORTED_REQUEST, detail });
}

function createModel(studio: StudioClient, scope: ModelAnnotationsOptions) {
  // A caller can switch its selection while GET/PUT is pending. This source
  // and its history keep the exact original binding throughout that request.
  const projectId = scope.projectId;
  const modelSource = scope.modelSource === null ? null : { ...scope.modelSource };
  const listeners = new Set<() => void>();
  let history: Draft[] = [{ annotations: [], comment: "" }];
  let cursor = 0;
  let acknowledged: ModelAnnotationsDto | null = null;
  let acknowledgedDraft: Draft | null = null;
  let ready = modelSource === null;
  let paused = false;
  let error: StudioApiError | null = null;
  let queue: QueuedSave[] = [];
  let writing: Promise<void> | null = null;
  let reading: Promise<void> | null = null;
  const present = () => history[cursor];
  const checkSource = (response: ModelAnnotationsDto) => {
    if (response.projectId !== projectId || response.modelSource.runId !== modelSource?.runId ||
        response.modelSource.stateDigest !== modelSource.stateDigest || response.modelSource.assetSha256 !== modelSource.assetSha256) {
      throw new StudioApiError({ status: 200, code: TRANSPORT_ERROR, detail: "The annotation response belongs to another model source." });
    }
  };
  const state = (): ModelSnapshot => ({
    ...present(), ready, saving: writing !== null,
    dirty: ready && (modelSource === null ? !sameDraft(history[0], present()) : queue.length > 0 || !sameDraft(acknowledgedDraft, present())),
    error, canUndo: ready && cursor > 0, canRedo: ready && cursor < history.length - 1,
  });
  let snapshot = state();
  const publish = () => {
    snapshot = state();
    listeners.forEach((listener) => listener());
  };
  const rejectWaiters = (reason: StudioApiError) => {
    for (const job of queue) {
      for (const waiter of job.waiters.splice(0)) waiter.reject(reason);
    }
  };

  function startQueue() {
    if (modelSource === null || writing || reading || paused || !ready || queue.length === 0) return;
    writing = Promise.resolve().then(async () => {
      while (queue.length > 0 && !paused) {
        const job = queue[0];
        try {
          const response = await studio.saveModelAnnotations({
            projectId, modelSource: { ...modelSource }, baseRevisionSha256: acknowledged?.revisionSha256 ?? null,
            annotations: copyInk(job.draft.annotations), comment: job.draft.comment,
          });
          checkSource(response);
          if (response.revisionSha256 === null) {
            throw new StudioApiError({ status: 200, code: TRANSPORT_ERROR, detail: "The saved model annotations did not return a revision." });
          }
          acknowledged = structuredClone(response);
          acknowledgedDraft = job.draft;
          queue.shift();
          for (const waiter of job.waiters) waiter.resolve(structuredClone(response));
          // An earlier ACK updates the CAS base, never the newer local present.
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
    if (modelSource === null) return null;
    const last = queue[queue.length - 1];
    if (last && sameDraft(last.draft, draft)) return last;
    if (!force && !last && acknowledged?.revisionSha256 && sameDraft(acknowledgedDraft, draft)) return null;
    const job: QueuedSave = { draft, waiters: [] };
    queue.push(job);
    publish();
    startQueue();
    return job;
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
    if (modelSource === null) return Promise.resolve();
    paused = true;
    reading = Promise.resolve().then(async () => {
      // Finish the one in-flight write before reading a newer CAS base.
      await writing;
      try {
        const response = await studio.modelAnnotations({ ...modelSource });
        checkSource(response);
        const next = { annotations: copyInk(response.annotations), comment: response.comment };
        rejectWaiters(unavailable("Reloading the model annotations interrupted this save; the local draft remains in Undo."));
        queue = [];
        if (explicit && ready) push(next);
        else { history = [next]; cursor = 0; }
        acknowledged = structuredClone(response);
        acknowledgedDraft = next;
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
    changeAnnotations(next: readonly ModelGestureDto[]) {
      if (!ready) return;
      if (push({ ...present(), annotations: copyInk(next) })) enqueue(present());
    },
    undo() {
      if (!ready || cursor === 0) return;
      cursor -= 1;
      publish();
      enqueue(present());
    },
    redo() {
      if (!ready || cursor === history.length - 1) return;
      cursor += 1;
      publish();
      enqueue(present());
    },
    async save(): Promise<ModelAnnotationsDto> {
      if (modelSource === null) throw unavailable("These model annotations are kept locally in memory; select a bound model source before saving.");
      if (reading && ready) throw unavailable("Wait for the model annotations to reload before saving.");
      if (!ready) await load();
      if (!ready) throw error ?? unavailable("The model annotations have not loaded.");
      if (error?.status === 409) throw error;
      const retrying = error !== null;
      paused = false;
      error = null;
      const job = enqueue(present(), retrying);
      if (job === null) { publish(); return structuredClone(acknowledged!); }
      const saved = new Promise<ModelAnnotationsDto>((resolve, reject) => {
        job.waiters.push({ resolve, reject });
      });
      startQueue();
      return saved;
    },
  };
}

export function createModelAnnotationsController(studio: StudioClient) {
  const models = new Map<string, ReturnType<typeof createModel>>();
  return {
    /** An accepted new file starts a fresh local history, not an undoable erase. */
    resetUnbound(projectId: string) {
      models.delete(JSON.stringify([projectId, null, null, null]));
    },
    model(options: ModelAnnotationsOptions) {
      const source = options.modelSource;
      const key = JSON.stringify([options.projectId, source?.runId ?? null, source?.stateDigest ?? null, source?.assetSha256 ?? null]);
      let model = models.get(key);
      if (!model) { model = createModel(studio, options); models.set(key, model); }
      return model;
    },
  };
}

export function useModelAnnotations(
  options: ModelAnnotationsOptions,
  existingController?: ReturnType<typeof createModelAnnotationsController>,
): ModelAnnotationsHandle {
  const studio = useStudio();
  const [localController] = useState(() => createModelAnnotationsController(studio));
  const controller = existingController ?? localController;
  const model = controller.model(options);
  const snapshot = useSyncExternalStore(model.subscribe, model.getSnapshot, model.getSnapshot);
  useEffect(() => { void model.load(); }, [model]);
  return { ...snapshot, changeAnnotations: model.changeAnnotations, undo: model.undo, redo: model.redo, save: model.save, reload: model.reload };
}
