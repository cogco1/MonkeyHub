import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { asStudioApiError, StudioApiError } from "../api/client";
import { useConnection, useStudio } from "../api/ProjectRuntimeContext";
import type { ServerIdentity } from "../api/connection";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import type { BoardDocumentOpen } from "../workspaces/monkeyboard/boardNavigation";
import type { BoardSketchRequest } from "../workspaces/monkeyboard/boardSketch";
import App, { type WorkspaceDesignContext } from "./App";
export type { WorkspaceDesignContext } from "./App";
import { ErrorPanel } from "./ErrorPanel";
import { LoadingOverlay } from "./LoadingOverlay";
import { failed, loading, ready, type Loadable } from "./loadable";

const Board = lazy(async () => {
  (window as Window & { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH =
    new URL("excalidraw/", document.baseURI).href;
  return import("../workspaces/monkeyboard/Board");
});

export interface ProjectWorkspaceProps {
  workspace: "arch" | "board";
  expectedProjectId?: string;
  candidateRunId?: string | null;
  active?: boolean;
  refreshKey?: number;
  onWorkspaceChange(workspace: "arch" | "board"): void;
  onChatRequest?: () => void;
  onDesignContextChange?: (context: WorkspaceDesignContext | null) => void;
}

/** One mounted project: the Board, its page editor and the same local model draft. */
export function ProjectWorkspace({ workspace, expectedProjectId, candidateRunId = null, active = true, refreshKey = 0,
  onWorkspaceChange, onChatRequest, onDesignContextChange }: ProjectWorkspaceProps) {
  const connection = useConnection();
  const studio = useStudio();
  const boundProjectId = useRef(expectedProjectId);
  const [server, setServer] = useState<Loadable<ServerIdentity>>(loading);
  const [refreshError, setRefreshError] = useState<ReturnType<typeof asStudioApiError> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [visit, setVisit] = useState<BoardDocumentOpen | null>(null);
  const [documentIntent, setDocumentIntent] = useState<BoardDesignRequest | undefined>();
  const [sketchRequest, setSketchRequest] = useState<BoardSketchRequest | undefined>();
  const [boardVisited, setBoardVisited] = useState(workspace === "board");
  const pageOpen = workspace === "board" && visit !== null;
  const modelVisible = workspace === "arch" || pageOpen;
  useEffect(() => {
    if (workspace === "board") setBoardVisited(true);
  }, [workspace]);
  useEffect(() => {
    if (workspace === "arch") setVisit(null);
  }, [workspace]);
  useEffect(() => {
    let live = true;
    setRefreshError(null);
    void (async () => {
      const identity = await connection.probe();
      const project = await studio.project();
      if (!live) return;
      if ((expectedProjectId !== undefined && project.projectId !== expectedProjectId) ||
          (boundProjectId.current !== undefined && project.projectId !== boundProjectId.current)) {
        throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED",
          detail: "This runtime no longer serves the project bound to this workspace." });
      }
      boundProjectId.current ??= project.projectId;
      return identity;
    })().then((identity) => { if (live && identity) setServer(ready(identity)); },
      (cause) => { if (live) { const error = asStudioApiError(cause); setRefreshError(error);
        setServer((previous) => previous.status === "ready" ? previous : failed(error)); } });
    return () => { live = false; };
  }, [connection, studio, expectedProjectId, attempt, refreshKey]);
  const openBoard = useCallback(() => {
    setVisit(null);
    onWorkspaceChange("board");
  }, [onWorkspaceChange]);
  const submitFeedback = useCallback((request: BoardDesignRequest) => {
    setSketchRequest(undefined); setDocumentIntent(request); setVisit(null);
    onWorkspaceChange("arch");
  }, [onWorkspaceChange]);
  const submitSketch = useCallback((request: BoardSketchRequest) => {
    setDocumentIntent(undefined); setSketchRequest(request); setVisit(null);
    onWorkspaceChange("arch");
  }, [onWorkspaceChange]);

  if (server.status === "failed") return <div className="project-workspace"><div className="refusal" role="alert">
    <ErrorPanel error={server.error} what="GET /api/protocol" />
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button>
  </div></div>;
  if (server.status !== "ready") return <div className="project-workspace"><LoadingOverlay mode="boot" status="Project Runtime" /></div>;
  return <div className="project-workspace" style={{ height: "100%", minHeight: 0 }}>
    {refreshError && <ErrorPanel error={refreshError} what="GET /api/protocol" />}
    <div data-project-surface="arch" hidden={!modelVisible} inert={!active || !modelVisible}
      style={{ height: "100%", minHeight: 0, display: modelVisible ? "block" : "none" }}>
      <App server={server.value} expectedProjectId={boundProjectId.current} initialRunId={candidateRunId} documentSource={pageOpen ? visit.source : null}
        initialDocumentIntent={documentIntent} initialSketchRequest={sketchRequest}
        active={active && modelVisible} refreshKey={refreshKey + attempt} onReturnToBoard={openBoard} onOpenBoard={openBoard} onChatRequest={onChatRequest}
        onDesignContextChange={onDesignContextChange} />
    </div>
    {(boardVisited || workspace === "board") && <div data-project-surface="board" hidden={modelVisible} inert={!active || modelVisible}
      style={{ height: "100%", minHeight: 0, display: modelVisible ? "none" : "block" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="MonkeyBoard" />}>
        <Board expectedProjectId={boundProjectId.current} refreshKey={refreshKey + attempt} active={active && !modelVisible} onSubmit={submitFeedback} onSketch={submitSketch} onOpenDocument={setVisit} />
      </Suspense>
    </div>}
  </div>;
}
