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

import { StudioApiError, asStudioApiError, studio } from "../api/client";
import { connection } from "../api/connection";
import type { ProjectBindingDto, StateProjectionDto } from "../api/generated";
import { editingBasePreferences } from "../features/settings/preferences";
import { failed, idle, loading, ready, type Loadable } from "./loadable";

const STALE_BASE = "STALE_BASE";

const RE_PROJECTED_NOTICE = "the project moved under you — re-projected";

export interface Session {
  readonly project: ProjectBindingDto;
  readonly projection: StateProjectionDto;
  /** Null keeps the server's default reference policy; a run is an explicit choice. */
  readonly sourceRunId: string | null;
}

interface SessionSnapshot {
  readonly session: Loadable<Session>;
  readonly changingBase: boolean;
  readonly baseError: StudioApiError | null;
  readonly persistenceFailed: boolean;
}

export interface SessionHandle extends SessionSnapshot {
  reload(runId?: string | null): Promise<Session | null>;
  /** Re-project when the error says the base moved. Answers whether it did. */
  recoverFromStaleBase(error: StudioApiError): boolean;
}

/** The hook's async transitions, also usable by isolated tests without a browser. */
export function createSessionController(serverBaseUrl = connection.baseUrl) {
  let snapshot: SessionSnapshot = {
    session: idle, changingBase: false, baseError: null, persistenceFailed: false,
  };
  let request = 0;
  const listeners = new Set<() => void>();
  const publish = (next: SessionSnapshot) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };

  const reload = async (requestedRunId?: string | null): Promise<Session | null> => {
    const currentRequest = ++request;
    const previous = snapshot.session;
    let project: ProjectBindingDto | null = null;
    publish({ ...snapshot, changingBase: true, baseError: null });
    try {
      // The server may now bind a different project. Never send the old run before
      // learning which project it would be read in.
      project = await studio.project();
      if (currentRequest !== request) return null;
      const sameProject = previous.status === "ready" && previous.value.project.projectId === project.projectId;
      if (!sameProject) publish({ ...snapshot, session: loading });
      if (previous.status === "ready" && !sameProject && requestedRunId != null) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail:
          "The server now binds another project. Retry to read that project's own editing choice." });
      }
      const runId = requestedRunId !== undefined ? requestedRunId
        : sameProject && previous.status === "ready" ? previous.value.sourceRunId
          : editingBasePreferences.read(serverBaseUrl, project.projectId);
      const projection = await studio.state(runId ?? undefined);
      if (currentRequest !== request) return null;
      if (projection.projectId !== project.projectId ||
          projection.published.version !== project.published.version ||
          projection.published.stateSha256 !== project.published.stateSha256 ||
          (runId !== null && projection.referenceRun.runId !== runId)) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED", detail:
          "The state response does not match the requested project and run. Retry to read the current binding." });
      }
      if (runId !== null && (projection.stateDigest === null ||
          projection.matchesReferenceReceipt !== true ||
          projection.referenceRun.baseVersion !== projection.published.version ||
          projection.referenceRun.baseSha256 !== projection.published.stateSha256)) {
        throw new StudioApiError({ status: 0, code: "EDITING_BASE_UNAVAILABLE", detail:
          `Run ${runId} cannot currently be restored as an editing base. Its verified state must match its receipt and current published base. ` + projection.honesty.join(" ") });
      }
      const next = { project, projection, sourceRunId: runId };
      // Reading a model or refreshing a session never records consent. Only the
      // explicit continuation/default action reaches the existing preference writer.
      let persistenceFailed = snapshot.persistenceFailed;
      if (requestedRunId !== undefined) {
        try { persistenceFailed = !editingBasePreferences.write(serverBaseUrl, project.projectId, runId); }
        catch { persistenceFailed = true; }
      }
      publish({ session: ready(next), changingBase: false, baseError: null, persistenceFailed });
      return next;
    } catch (cause) {
      if (currentRequest !== request) return null;
      const error = asStudioApiError(cause);
      // A failed explicit switch can leave the old, same-project choice intact.
      // A failed restoration/revalidation cannot silently become the default.
      const retainPrevious = requestedRunId !== undefined && previous.status === "ready" &&
        project?.projectId === previous.value.project.projectId && error.code !== "EDITING_PROJECT_CHANGED";
      publish({ ...snapshot, session: retainPrevious ? previous : failed(error), changingBase: false, baseError: error });
      return null;
    }
  };

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    reload,
    cancel() { request += 1; },
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

export function useSession(notice: (line: string) => void): SessionHandle {
  const [controller] = useState(() => createSessionController());
  const snapshot = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  const { reload } = controller;
  const noticeRef = useRef(notice);
  noticeRef.current = notice;

  useEffect(() => {
    void reload();
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
    recoverFromStaleBase,
  };
}
