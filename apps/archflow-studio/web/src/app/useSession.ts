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

import { useCallback, useEffect, useRef, useState } from "react";

import { StudioApiError, asStudioApiError, studio } from "../api/client";
import type { ProjectBindingDto, StateProjectionDto } from "../api/generated";
import { failed, idle, loading, ready, type Loadable } from "./loadable";

const STALE_BASE = "STALE_BASE";

const RE_PROJECTED_NOTICE = "the project moved under you — re-projected";

export interface Session {
  readonly project: ProjectBindingDto;
  readonly projection: StateProjectionDto;
}

export interface SessionHandle {
  readonly session: Loadable<Session>;
  readonly stateDigest: string | null;
  reload(): Promise<void>;
  /** Re-project when the error says the base moved. Answers whether it did. */
  recoverFromStaleBase(error: StudioApiError): boolean;
}

export function useSession(notice: (line: string) => void): SessionHandle {
  const [session, setSession] = useState<Loadable<Session>>(idle);
  const noticeRef = useRef(notice);
  noticeRef.current = notice;

  const reload = useCallback(async () => {
    setSession(loading);
    try {
      const project = await studio.project();
      const projection = await studio.state();
      setSession(ready({ project, projection }));
    } catch (cause) {
      setSession(failed(asStudioApiError(cause)));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

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
    session,
    stateDigest:
      session.status === "ready" ? session.value.projection.stateDigest : null,
    reload,
    recoverFromStaleBase,
  };
}
