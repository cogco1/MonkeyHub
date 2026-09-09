/**
 * The handshake, and what stands in for the shell when it fails.
 *
 * The shell used to mount straight away and discover the server through its
 * first two calls. That was fine while the only server was the one the
 * launcher had just started beside it. It is not fine for a client that can be
 * pointed anywhere: a server that speaks another protocol major would answer
 * every call with something this client could parse and would misread.
 *
 * So one question comes first — `GET /api/protocol` — and the shell mounts
 * only when it has been answered by a server this client speaks to. A refusal
 * is a screen with the server's own code and detail on it, not a blank stage:
 * the operator who pointed this client somewhere is the person who can fix it,
 * and they can only fix what they can read.
 */

import { lazy, Suspense, useCallback, useEffect, useState } from "react";

import { asStudioApiError } from "../api/client";
import { connection, type ServerIdentity } from "../api/connection";
import { useT } from "../i18n/useT";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import { documentUrl } from "../workspaces/monkeyboard/boardScene";
import App from "./App";
import { ErrorPanel } from "./ErrorPanel";
import { failed, loading, ready, type Loadable } from "./loadable";
import { LoadingOverlay } from "./LoadingOverlay";

const Board = lazy(async () => {
  // The canvas otherwise falls back to public font hosts. Keep this project
  // surface on the same server, plus an explicitly configured Studio API.
  (window as Window & { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH =
    new URL("excalidraw/", document.baseURI).href;
  document.title = "MonkeyBoard";
  const policy = document.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  const apiOrigin = new URL(connection.baseUrl || window.location.origin, window.location.origin).origin;
  // The policy stays active after an in-document handoff. Rhino3dm's existing
  // loader needs dynamic JS compilation when the Studio viewport mounts.
  policy.content = `default-src 'self'; script-src 'self' 'wasm-unsafe-eval' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' ${apiOrigin}; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'`;
  document.head.append(policy);
  return import("../workspaces/monkeyboard/Board");
});

export function Connected() {
  const [server, setServer] = useState<Loadable<ServerIdentity>>(loading);
  const [documentIntent, setDocumentIntent] = useState<BoardDesignRequest | null>(null);
  const t = useT();

  const submitBoardFeedback = useCallback((request: BoardDesignRequest) => {
    window.history.replaceState(null, "", documentUrl(window.location.href, request.source));
    document.title = "MonkeyArch";
    setDocumentIntent(request);
  }, []);

  const probe = useCallback(() => {
    setServer(loading);
    return connection.probe().then(
      (identity) => setServer(ready(identity)),
      (cause) => setServer(failed(asStudioApiError(cause))),
    );
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  if (server.status === "failed") {
    return (
      <div className="refusal" role="alert">
        <div className="refusal__card">
          <p className="label">MonkeyArch</p>
          <h1 className="refusal__title">{t("shell.refusal.title")}</h1>
          <p className="refusal__lead">
            {t("shell.refusal.leadBeforeProtocol")} {" "}
            <code lang="en">GET /api/protocol</code>{" "}
            {t("shell.refusal.leadAfterProtocol")}
          </p>
          <ErrorPanel error={server.error} what={t("shell.handshake")} />
          <p className="refusal__where">
            {connection.baseUrl === ""
              ? t("shell.serverOrigin")
              : `${t("shell.serverLabel")}: ${connection.baseUrl}`}
          </p>
          <button type="button" className="btn" onClick={() => void probe()}>
            {t("shell.askAgain")}
          </button>
        </div>
      </div>
    );
  }

  if (server.status !== "ready") {
    // The handshake continues the launcher's exact-status convention rather than
    // leaving a blank frame between the launch surface and the mounted shell.
    return (
      <LoadingOverlay
        mode="boot"
        status={
          <>
            {t("loading.askingServer")} ·{" "}
            <code lang="en">GET /api/protocol</code>
          </>
        }
      />
    );
  }

  return new URLSearchParams(window.location.search).get("view") === "board"
    ? <Suspense fallback={<LoadingOverlay mode="boot" status="MonkeyBoard" />}><Board onSubmit={submitBoardFeedback} /></Suspense>
    : <App server={server.value} initialDocumentIntent={documentIntent ?? undefined} />;
}
