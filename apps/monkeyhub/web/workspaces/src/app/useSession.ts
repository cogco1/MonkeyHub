/**
 * What the shell knows before anybody clicks anything: which project the API is
 * bound to, and the exact state it is answering with.
 *
 * The `stateDigest` held here is the one every write-shaped request carries —
 * a pick, a proposal. It is read from the server and never assembled locally,
 * so a client cannot propose against a state it invented.
 *
 * `STALE_BASE` is the one error this hook answers rather than merely reports:
 * the project moved under the tab, so the projection is read again and the fact
 * is said out loud. It is still an error and the panel that raised it still
 * shows it; re-projecting is not the same as recovering.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { StudioApiError, asStudioApiError, type StudioClient } from "../api/client";
import { useStudio, useConnection } from "../api/ProjectRuntimeContext";
import type { DesignHistoryDto, ModelSourceDto, ProjectBindingDto, StateProjectionDto, WorkingCopyDto, WorkingDraftDto } from "../api/generated";
import { editingBasePreferences } from "../features/settings/preferences";
import { failed, idle, loading, ready, type Loadable } from "./loadable";

const STALE_BASE = "STALE_BASE";

const RE_PROJECTED_NOTICE = "the project moved under you — re-projected";

export interface Session {
  readonly project: ProjectBindingDto;
  readonly projection: StateProjectionDto;
  /** Null keeps the server's default reference policy; a run is an explicit choice. */
  readonly sourceRunId: string | null;
  readonly workingCopies: readonly WorkingCopyDto[];
  readonly designHistory?: DesignHistoryDto | null;
  readonly workingDraft?: WorkingDraftDto | null;
  /** The exact model explicitly selected by a Stage/default-head action. */
  readonly stageModelSource?: ModelSourceDto | null;
  /**
   * Set when the run verifies but was based on an older published version: it
   * is shown read-only, and no proposal or candidate starts from it (#450).
   */
  readonly staleBase?: StaleBase | null;
}

/** A verified run based on an older published version than the project's HEAD. */
export interface StaleBase {
  readonly publishedVersion: number;
  readonly baseVersion: number;
}

/**
 * Whether a restored run can be edited, only viewed, or not opened at all.
 * A missing digest or a receipt mismatch is a broken run; a run whose state
 * verifies but whose base is an older published version is a valid design
 * that can still be inspected (#450).
 */
export function classifyEditingBase(projection: StateProjectionDto):
  { readonly kind: "editable" } | { readonly kind: "staleBase"; readonly staleBase: StaleBase } | { readonly kind: "unavailable" } {
  if (projection.stateDigest === null || projection.matchesReferenceReceipt !== true) return { kind: "unavailable" };
  const { baseVersion, baseSha256 } = projection.referenceRun, published = projection.published;
  if (!projection.sourceStageRef && (baseVersion !== published.version || baseSha256 !== published.stateSha256)) {
    // Only an older published base is a design to look at; any other mismatch contradicts HEAD.
    return baseVersion < published.version
      ? { kind: "staleBase", staleBase: { publishedVersion: published.version, baseVersion } } : { kind: "unavailable" };
  }
  return { kind: "editable" };
}

interface SessionSnapshot {
  /** Project identity is available before, and independently of, its 3D editing base. */
  readonly binding: ProjectBindingDto | null;
  readonly session: Loadable<Session>;
  readonly changingBase: boolean;
  readonly baseError: StudioApiError | null;
  readonly persistenceFailed: boolean;
}

/** The exact base a local Sync batch started from. */
export interface SyncedBaseSource {
  readonly projectId: string;
  readonly sourceRunId: string | null;
  readonly stateDigest: string;
  readonly sourceStageRef?: string | null;
}

export interface SessionHandle extends SessionSnapshot {
  /**
   * `persist: false` follows a position the server already holds (the Working
   * Head) without writing a new working position or a local editing choice.
   */
  reload(runId?: string | null, sourceStageRef?: string | null, branchId?: string, background?: boolean,
    alreadyReadProjection?: StateProjectionDto, persist?: boolean): Promise<Session | null>;
  /** The refusal the latest reload published, readable as soon as that reload answers null. */
  failure(): StudioApiError | null;
  /** Refresh version choices without re-projecting or selecting an editing base. */
  refreshWorkingCopies(): Promise<readonly WorkingCopyDto[] | null>;
  refreshWorkingDraft(): Promise<WorkingDraftDto | null>;
  /**
   * Save the architect's own completed Sync as the next editing base without
   * re-projecting this tab or changing what it shows. Answers false, writing
   * nothing, when the batch no longer starts from this tab's editing base or
   * the saved position names another explicit choice; that choice stands.
   */
  saveSyncedBase(runId: string, source: SyncedBaseSource): Promise<boolean>;
  /** Re-project when the error says the base moved. Answers whether it did. */
  recoverFromStaleBase(error: StudioApiError): boolean;
}

/** The hook's async transitions, also usable by isolated tests without a browser. */
export function createSessionController(studio: StudioClient, serverBaseUrl = "", capabilities: readonly string[] = [], persistEditingBase = true, expectedProjectId?: string,
  prefetchModel?: (source: ModelSourceDto) => void) {
  let snapshot: SessionSnapshot = {
    binding: null, session: idle, changingBase: false, baseError: null, persistenceFailed: false,
  };
  let boundProjectId = expectedProjectId;
  let request = 0;
  let workingCopiesRead = 0;
  const listeners = new Set<() => void>();
  const publish = (next: SessionSnapshot) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };

  const reload = async (requestedRunId?: string | null, sourceStageRef?: string | null, branchId?: string, background = false,
    alreadyReadProjection?: StateProjectionDto, persist = true): Promise<Session | null> => {
    const currentRequest = ++request;
    const previous = snapshot.session;
    let project: ProjectBindingDto | null = null;
    publish({ ...snapshot, changingBase: background ? snapshot.changingBase : true, baseError: null });
    try {
      // The server may now bind a different project. Never send the old run before
      // learning which project it would be read in. The saved position names no
      // run to ask with, so it is read beside the binding and checked against it.
      const draftRead = capabilities.includes("working-draft") ? studio.workingDraft() : null;
      draftRead?.catch(() => undefined);
      project = await studio.project();
      if (currentRequest !== request) return null;
      if (boundProjectId !== undefined && project.projectId !== boundProjectId) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail:
          "This workspace belongs to another project. Reconnect its original project runtime before continuing." });
      }
      boundProjectId ??= project.projectId;
      const sameProject = previous.status === "ready" && previous.value.project.projectId === project.projectId;
      publish({ ...snapshot, binding: project, session: sameProject ? previous : loading });
      let workingDraft = draftRead ? await draftRead : null;
      if (currentRequest !== request) return null;
      if (workingDraft && workingDraft.projectId !== project.projectId) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail: "The saved draft belongs to another project." });
      }
      const legacyChoice = !sameProject && requestedRunId === undefined && persistEditingBase &&
        (!workingDraft || workingDraft.revisionSha256 == null)
        ? editingBasePreferences.readChoice(serverBaseUrl, project.projectId) : null;
      const savedChoice = workingDraft ? workingDraft.localDraft ? {
        runId: workingDraft.localDraft.source.sourceRunId,
        sourceStageRef: workingDraft.localDraft.source.sourceStageRef,
        branchId: workingDraft.current?.branchId,
      } : workingDraft.current ?? legacyChoice : legacyChoice;
      const selectedBranch = branchId ?? (sameProject && previous.status === "ready"
        ? previous.value.designHistory?.branchId : savedChoice?.branchId ?? undefined);
      const [designHistory, copies] = await Promise.all([
        capabilities.includes("design-history") ? studio.designHistory(selectedBranch) : null,
        capabilities.includes("working-copies") ? studio.workingCopies() : null,
      ]);
      if (currentRequest !== request) return null;
      const workingCopies = copies?.workingCopies ?? [];
      const branch = designHistory?.branches.find((item) => item.branchId === designHistory.branchId);
      const defaultStage = designHistory?.stages.find((stage) => stage.stageRef === branch?.headStageRef);
      const useHead = designHistory !== null && (requestedRunId === null ||
        (!sameProject && requestedRunId === undefined && savedChoice === null));
      const runId = useHead ? defaultStage?.modelSource.runId ?? null : requestedRunId !== undefined ? requestedRunId
        : sameProject && previous.status === "ready" ? previous.value.sourceRunId : savedChoice?.runId ?? null;
      const stageRef = useHead ? defaultStage?.stageRef : sourceStageRef ?? savedChoice?.sourceStageRef ??
        (requestedRunId === undefined && sameProject && previous.status === "ready" ? previous.value.projection.sourceStageRef : undefined);
      const stageModelSource = useHead ? defaultStage?.modelSource ?? null : stageRef
        ? designHistory?.stages.find((stage) => stage.stageRef === stageRef)?.modelSource ?? null
        : requestedRunId === undefined && sameProject && previous.status === "ready" ? previous.value.stageModelSource ?? null : null;
      // The model this base shows is named already: its bytes need not wait for the state (#449).
      if (!background && stageModelSource) prefetchModel?.(stageModelSource);
      // Quiet candidate display already read this exact run. Reuse that value,
      // but still validate it against the fresh project binding below.
      const projection = (typeof requestedRunId === "string" ? alreadyReadProjection : undefined)
        ?? await studio.state(runId ?? undefined, stageRef);
      if (currentRequest !== request) return null;
      if (!sameProject && requestedRunId === undefined && workingDraft?.localDraft &&
        workingDraft.localDraft.source.stateDigest !== projection.stateDigest) {
        throw new StudioApiError({ status: 0, code: "WORKING_DRAFT_SOURCE_CHANGED",
          detail: "The recovered local draft no longer matches its exact source. Its saved commands have been kept; choose a valid source before continuing." });
      }
      if (projection.projectId !== project.projectId ||
          projection.published.version !== project.published.version ||
          projection.published.stateSha256 !== project.published.stateSha256 ||
          (runId !== null && projection.referenceRun.runId !== runId) ||
          (stageRef != null && projection.sourceStageRef !== stageRef)) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail:
          "The state response does not match the requested project, run and Stage. Retry to read the current binding." });
      }
      const base = runId === null ? { kind: "editable" as const } : classifyEditingBase(projection);
      if (base.kind === "unavailable") {
        throw new StudioApiError({ status: 0, code: "EDITING_BASE_UNAVAILABLE", detail:
          `Run ${runId} cannot currently be restored as an editing base. Its verified state must match its receipt and current published base. ` + projection.honesty.join(" ") });
      }
      const staleBase = base.kind === "staleBase" ? base.staleBase : null;
      // Only an explicit choice moves the saved position: finishing a generated
      // candidate never does (GH-234 Q2). A quiet background adoption of the
      // architect's own Sync selects it here unless the position already names it.
      // Viewing a run on an older published version never moves it (#450).
      if (!staleBase && workingDraft && persistEditingBase && persist &&
        ((requestedRunId !== undefined && (!background || workingDraft.current?.runId !== runId)) ||
          (workingDraft.revisionSha256 == null && legacyChoice !== null))) {
        workingDraft = await studio.selectWorkingDraft({ projectId: project.projectId,
          runId, branchId: projection.sourceStageRef ? designHistory?.branchId ?? null : null,
          baseRevisionSha256: workingDraft.revisionSha256 ?? null });
        if (currentRequest !== request) return null;
      }
      const next = { project, projection, sourceRunId: runId, workingCopies, designHistory, stageModelSource, workingDraft, staleBase };
      // Reading a model or refreshing a session never records consent. Only the
      // explicit continuation/default action reaches the existing preference writer.
      let persistenceFailed = snapshot.persistenceFailed;
      if (!staleBase && !workingDraft && requestedRunId !== undefined && persistEditingBase && persist) {
        try {
          persistenceFailed = !editingBasePreferences.writeChoice(serverBaseUrl, project.projectId, runId === null ? null : {
            runId, sourceStageRef: stageRef ?? null, branchId: designHistory?.branchId ?? null,
          });
        }
        catch { persistenceFailed = true; }
      }
      publish({ binding: project, session: ready(next), changingBase: false, baseError: null, persistenceFailed });
      return next;
    } catch (cause) {
      if (currentRequest !== request) return null;
      const error = asStudioApiError(cause);
      // A failed switch or background refresh keeps the same project's mounted
      // draft and reports the error. Identity mismatches always refuse the session;
      // a failed cold restoration never silently becomes the default.
      const retainPrevious = previous.status === "ready" && error.code !== "EDITING_PROJECT_CHANGED" &&
        ((requestedRunId !== undefined && project?.projectId === previous.value.project.projectId) ||
          (background && (project === null || project.projectId === previous.value.project.projectId)));
      publish({ ...snapshot, binding: retainPrevious || error.code === "EDITING_PROJECT_CHANGED" ? snapshot.binding : project,
        session: retainPrevious ? previous : failed(error), changingBase: false, baseError: error });
      return null;
    }
  };

  const refreshWorkingDraft = async (): Promise<WorkingDraftDto | null> => {
    if (!capabilities.includes("working-draft") || snapshot.session.status !== "ready") return null;
    const currentRequest = request;
    const projectId = snapshot.session.value.project.projectId;
    const workingDraft = await studio.workingDraft();
    if (currentRequest !== request || snapshot.session.status !== "ready") return null;
    if (workingDraft.projectId !== projectId) throw new StudioApiError({ status: 0,
      code: "EDITING_PROJECT_CHANGED", detail: "The saved draft belongs to another project." });
    publish({ ...snapshot, session: ready({ ...snapshot.session.value, workingDraft }) });
    return workingDraft;
  };

  /** This tab's session while its editing base is still exactly `source`. */
  const sessionOn = (source: SyncedBaseSource): Session | null => {
    if (snapshot.session.status !== "ready") return null;
    const value = snapshot.session.value;
    const { projection } = value;
    const baseRunId = projection.referenceRunSource === "none" ? null : projection.referenceRun.runId;
    return value.project.projectId === source.projectId && baseRunId === source.sourceRunId &&
      projection.stateDigest === source.stateDigest &&
      (projection.sourceStageRef ?? null) === (source.sourceStageRef ?? null) ? value : null;
  };
  const publishWorkingDraft = (projectId: string, workingDraft: WorkingDraftDto) => {
    if (snapshot.session.status === "ready" && snapshot.session.value.project.projectId === projectId) {
      publish({ ...snapshot, session: ready({ ...snapshot.session.value, workingDraft }) });
    }
  };

  const saveSyncedBase = async (runId: string, source: SyncedBaseSource): Promise<boolean> => {
    if (!persistEditingBase || !capabilities.includes("working-draft")) return false;
    for (let attempt = 1; ; attempt += 1) {
      if (!sessionOn(source)) return false;
      const workingDraft = await studio.workingDraft();
      // The architect moved this tab's base while the batch ran: that choice stands.
      const session = sessionOn(source);
      if (!session) return false;
      const projectId = session.project.projectId;
      if (workingDraft.projectId !== projectId) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail: "The saved draft belongs to another project." });
      }
      const saved = workingDraft.current?.runId ?? null;
      if (saved === runId) {
        publishWorkingDraft(projectId, workingDraft);
        return true;
      }
      // ...or chose another base in another window: that choice stands too.
      if (saved !== null && saved !== source.sourceRunId) {
        publishWorkingDraft(projectId, workingDraft);
        return false;
      }
      try {
        publishWorkingDraft(projectId, await studio.selectWorkingDraft({ projectId, runId,
          branchId: session.projection.sourceStageRef ? session.designHistory?.branchId ?? null : null,
          baseRevisionSha256: workingDraft.revisionSha256 ?? null }));
        return true;
      } catch (cause) {
        const error = asStudioApiError(cause);
        // An autosave may have written between the read and this selection.
        if (error.code !== "WORKING_DRAFT_STALE" || attempt >= 3) throw error;
      }
    }
  };

  const refreshWorkingCopies = async (): Promise<readonly WorkingCopyDto[] | null> => {
    if (!capabilities.includes("working-copies") || snapshot.session.status !== "ready" || snapshot.changingBase) return null;
    const currentRead = ++workingCopiesRead;
    const currentRequest = request;
    const projectId = snapshot.session.value.project.projectId;
    const isCurrent = () => currentRead === workingCopiesRead && currentRequest === request &&
      snapshot.session.status === "ready" && snapshot.session.value.project.projectId === projectId;
    try {
      const { workingCopies } = await studio.workingCopies();
      if (!isCurrent() || snapshot.session.status !== "ready") return null;
      if (workingCopies.some((copy) => copy.projectId !== projectId)) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail:
          "The version list belongs to another project. The current editing base has been kept." });
      }
      publish({ ...snapshot, session: ready({ ...snapshot.session.value, workingCopies }) });
      return workingCopies;
    } catch (cause) {
      if (!isCurrent()) return null;
      // A background list failure leaves the bound projection and existing
      // choices intact. Its caller may report it without discarding the view.
      throw asStudioApiError(cause);
    }
  };

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    reload,
    refreshWorkingCopies,
    refreshWorkingDraft,
    saveSyncedBase,
    cancel() { request += 1; workingCopiesRead += 1; },
  };
}

/** Looking at another run (or a local file) never selects it as an editing base. */
export function editingDigestForView(
  session: Loadable<Session>, changingBase: boolean,
  viewedRunId: string | null, localFile: boolean, modelLoading: boolean,
): string | null {
  if (changingBase || modelLoading || localFile || session.status !== "ready") return null;
  if (viewedRunId !== null && viewedRunId !== session.value.projection.referenceRun.runId) return null;
  return session.value.projection.stateDigest;
}

export function useSession(notice: (line: string) => void, capabilities: readonly string[] = [], initialBase?: {
  runId: string | null; sourceStageRef: string | null;
}, persistEditingBase = true, expectedProjectId?: string, prefetchModel?: (source: ModelSourceDto) => void): SessionHandle {
  const studio = useStudio();
  const connection = useConnection();
  const prefetchRef = useRef(prefetchModel);
  prefetchRef.current = prefetchModel;
  const [controller] = useState(() => createSessionController(studio, connection.baseUrl, capabilities, persistEditingBase, expectedProjectId,
    (source) => prefetchRef.current?.(source)));
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  const { reload } = controller;
  const failure = useCallback(() => controller.getSnapshot().baseError, [controller]);
  const noticeRef = useRef(notice);
  noticeRef.current = notice;
  const initialBaseRef = useRef(initialBase);

  useEffect(() => {
    void reload(initialBaseRef.current?.runId, initialBaseRef.current?.sourceStageRef);
    return () => controller.cancel();
  }, [controller, reload]);

  const recoverFromStaleBase = useCallback(
    (error: StudioApiError) => {
      if (error.code !== STALE_BASE) return false;
      noticeRef.current(RE_PROJECTED_NOTICE);
      void reload();
      return true;
    },
    [reload],
  );

  return {
    ...snapshot,
    reload,
    failure,
    refreshWorkingCopies: controller.refreshWorkingCopies,
    refreshWorkingDraft: controller.refreshWorkingDraft,
    saveSyncedBase: controller.saveSyncedBase,
    recoverFromStaleBase,
  };
}
