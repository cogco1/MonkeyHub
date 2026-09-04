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

import { useCallback, useEffect, useState } from "react";

import { asStudioApiError } from "../api/client";
import { connection, type ServerIdentity } from "../api/connection";
import App from "./App";
import { ErrorPanel } from "./ErrorPanel";
import { failed, loading, ready, type Loadable } from "./loadable";

export function Connected() {
  const [server, setServer] = useState<Loadable<ServerIdentity>>(loading);

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
          <h1 className="refusal__title">This client is not talking to that server.</h1>
          <p className="refusal__lead">
            Every session starts with one question — <code>GET /api/protocol</code>{" "}
            — and this is the answer it got. Nothing else has been asked, and
            nothing has been read from any project.
          </p>
          <ErrorPanel error={server.error} what="the handshake" />
          <p className="refusal__where">
            {connection.baseUrl === ""
              ? "server: the origin this page was served from"
              : `server: ${connection.baseUrl}`}
          </p>
          <button type="button" className="btn" onClick={() => void probe()}>
            ask again
          </button>
        </div>
      </div>
    );
  }

  if (server.status !== "ready") {
    return (
      <div className="refusal">
        <div className="refusal__card">
          <p className="label">MonkeyArch</p>
          <p className="refusal__lead">reading the server…</p>
        </div>
      </div>
    );
  }

  return <App server={server.value} />;
}
