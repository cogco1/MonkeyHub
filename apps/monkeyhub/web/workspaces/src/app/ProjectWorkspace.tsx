import type { RenderView } from "../workspaces/monkeyarch/viewer/renderView";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { asStudioApiError, StudioApiError } from "../api/client";
import { useConnection, useStudio } from "../api/ProjectRuntimeContext";
import type { ServerIdentity } from "../api/connection";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import type { PageSource } from "../workspaces/monkeyboard/boardScene";
import type { BoardPageRequest } from "../workspaces/monkeyboard/Board";
import type { BoardSketchRequest } from "../workspaces/monkeyboard/boardSketch";
import App, { type WorkspaceDesignContext } from "./App";
export type { WorkspaceDesignContext } from "./App";
import { BoardModeSwitch } from "./BoardModeSwitch";
import { ErrorPanel } from "./ErrorPanel";
import { LoadingOverlay } from "./LoadingOverlay";
import { failed, loading, ready, type Loadable } from "./loadable";
import { currentView, DesignTreeBar, type DesignTreeView } from "../features/designTree/DesignTreeBar";
import { useDesignTree, useSeenCandidates } from "../features/designTree/useDesignTree";

const Drawing = lazy(() => import("../workspaces/monkeydiagram/DrawingCanvas"));
const Publish = lazy(() => import("../workspaces/publish/PublishWorkspace"));
const Render = lazy(() => import("../workspaces/render/RenderWorkspace"));
// The shared canvas host (features/canvas) sets Excalidraw's local font path.
const Board = lazy(() => import("../workspaces/monkeyboard/Board"));
const DesignTreeSurface = lazy(() => import("../features/designTree/DesignTreeSurface"));

export interface ProjectWorkspaceProps {
  workspace: "arch" | "board" | "drawing" | "render" | "publish" | "tree";
  expectedProjectId?: string;
  candidateRunId?: string | null;
  /** The pin is a delivery or restore hint; a runtime that knows its Working Head shows the head. */
  candidateFollowsHead?: boolean;
  active?: boolean;
  refreshKey?: number;
  documentRequest?: { source: PageSource; requestId: number } | null;
  onWorkspaceChange(workspace: "arch" | "board" | "drawing" | "render" | "publish" | "tree"): void;
  onChatRequest?: () => void;
  onDesignContextChange?: (context: WorkspaceDesignContext | null) => void;
}

/** One mounted project: the Board, its page editor and the same local model draft. */
export function ProjectWorkspace({ workspace, expectedProjectId, candidateRunId = null, candidateFollowsHead = false, active = true, refreshKey = 0, documentRequest = null,
  onWorkspaceChange, onChatRequest, onDesignContextChange }: ProjectWorkspaceProps) {
  const renderReader = useRef<(() => RenderView | null) | null>(null);
  const registerRenderReader = useCallback((reader: (() => RenderView | null) | null) => { renderReader.current = reader; }, []);
  const readRenderView = useCallback(() => renderReader.current?.() ?? null, []);
  const connection = useConnection();
  const studio = useStudio();
  const boundProjectId = useRef(expectedProjectId);
  const [server, setServer] = useState<Loadable<ServerIdentity>>(loading);
  const [refreshError, setRefreshError] = useState<ReturnType<typeof asStudioApiError> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [visit, setVisit] = useState<{ source: PageSource } | null>(null);
  const openedDocumentRequest = useRef<number | null>(null);
  const [documentIntent, setDocumentIntent] = useState<BoardDesignRequest | undefined>();
  const [sketchRequest, setSketchRequest] = useState<BoardSketchRequest | undefined>();
  const [drawingVisited, setDrawingVisited] = useState(workspace === "drawing");
  const [boardVisited, setBoardVisited] = useState(workspace === "board");
  const [archVisited, setArchVisited] = useState((workspace !== "render" && workspace !== "publish" && workspace !== "tree"));
  const [publishRequest, setPublishRequest] = useState<{ revision: string; ids: string[]; requestId: string } | null>(null);
  const [publishVisited, setPublishVisited] = useState(workspace === "publish");
  const [renderVisited, setRenderVisited] = useState(workspace === "render");
  const [boardRefresh, setBoardRefresh] = useState(0);
  const [boardPage, setBoardPage] = useState<BoardPageRequest | null>(null);
  const pageOpen = workspace === "board" && visit !== null;
  const modelVisible = workspace === "arch" || pageOpen;
  useEffect(() => { if (modelVisible) setArchVisited(true); }, [modelVisible]);
  useEffect(() => {
    if (workspace === "board") setBoardVisited(true);
    if (workspace === "drawing") setDrawingVisited(true);
    if (workspace === "render") setRenderVisited(true);
    if (workspace === "publish") setPublishVisited(true);
    if ((workspace !== "render" && workspace !== "publish" && workspace !== "tree")) setArchVisited(true);
  }, [workspace]);
  useEffect(() => {
    if (workspace === "arch") setVisit(null);
  }, [workspace]);
  // The Design Tree (#284): its facts, its surface, and what it opened read-only in Modeling.
  const [treeVisited, setTreeVisited] = useState(workspace === "tree");
  const treeReturn = useRef<Exclude<ProjectWorkspaceProps["workspace"], "tree">>(workspace === "tree" ? "arch" : workspace);
  const [treeView, setTreeView] = useState<DesignTreeView | null>(null);
  const [headMoves, setHeadMoves] = useState(0);
  useEffect(() => { if (workspace === "tree") setTreeVisited(true); else treeReturn.current = workspace; }, [workspace]);
  // A newer host pin, such as a delivered result, replaces what the tree opened.
  useEffect(() => { setTreeView(null); }, [candidateRunId]);
  const treeProject = server.status === "ready" ? boundProjectId.current : null;
  const seenCandidates = useSeenCandidates(treeProject);
  const designTree = useDesignTree({ studio, capabilities: server.status === "ready" ? server.value.capabilities : null,
    projectId: treeProject, active, refreshKey: refreshKey + attempt, onHeadMoved: () => setHeadMoves((value) => value + 1) });
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
  useEffect(() => {
    if (!active || server.status !== "ready" || !documentRequest ||
        openedDocumentRequest.current === documentRequest.requestId) return;
    openedDocumentRequest.current = documentRequest.requestId;
    setVisit({ source: documentRequest.source });
    onWorkspaceChange("board");
  }, [active, server.status, documentRequest, onWorkspaceChange]);

  if (server.status === "failed") return <div className="project-workspace"><div className="refusal" role="alert">
    <ErrorPanel error={server.error} what="GET /api/protocol" />
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>Retry</button>
  </div></div>;
  if (server.status !== "ready") return <div className="project-workspace"><LoadingOverlay mode="boot" status="Project Runtime" /></div>;
  return <div className="project-workspace" style={{ height: "100%", minHeight: 0 }}>
    {/* The project's position over every surface: the Stage chip opens the Design Tree. */}
    {designTree.available && <DesignTreeBar data={designTree} seen={seenCandidates.seen} open={workspace === "tree"}
      viewing={treeView && !treeView.back ? treeView : null}
      onToggle={() => onWorkspaceChange(workspace === "tree" ? treeReturn.current : "tree")}
      onBackToCurrent={() => { setTreeView(currentView(designTree)); onWorkspaceChange("arch"); }} />}
    {refreshError && <ErrorPanel error={refreshError} what="GET /api/protocol" />}
    {(archVisited || modelVisible) && <div data-project-surface="arch" hidden={!modelVisible} inert={!active || !modelVisible}
      style={{ height: "100%", minHeight: 0, display: modelVisible ? "block" : "none" }}>
      <App server={server.value} expectedProjectId={boundProjectId.current} initialRunId={treeView?.runId ?? candidateRunId}
        initialRunFollowsHead={treeView ? false : candidateFollowsHead} documentSource={pageOpen ? visit.source : null}
        initialDocumentIntent={documentIntent} initialSketchRequest={sketchRequest}
        active={active && modelVisible} refreshKey={refreshKey + attempt + headMoves} onReturnToBoard={openBoard} onOpenBoard={openBoard} onChatRequest={onChatRequest}
        onDesignContextChange={onDesignContextChange} onRenderReader={registerRenderReader} />
    </div>}
    {/* #300: Board and its Layout mode share one rail entry; this switch moves between the two
        mounted surfaces, and view=publish links still land on Layout. */}
    {((workspace === "board" && !pageOpen) || workspace === "publish") && <BoardModeSwitch mode={workspace === "publish" ? "layout" : "board"}
      onChange={(mode) => onWorkspaceChange(mode === "layout" ? "publish" : "board")} />}
    {(publishVisited || workspace === "publish") && <div data-project-surface="publish" hidden={workspace !== "publish"} inert={!active || workspace !== "publish"}
      style={{ height: "100%", minHeight: 0, display: workspace === "publish" ? "block" : "none" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="Publish" />}>
        <Publish projectId={boundProjectId.current!} active={active && workspace === "publish"} refreshKey={refreshKey + attempt} boardRequest={publishRequest} />
      </Suspense>
    </div>}
    {(renderVisited || workspace === "render") && <div data-project-surface="render" hidden={workspace !== "render"} inert={!active || workspace !== "render"}
      style={{ height: "100%", minHeight: 0, display: workspace === "render" ? "block" : "none" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="Render" />}>
        <Render readModelView={readRenderView} onModeling={() => onWorkspaceChange("arch")} projectId={boundProjectId.current!} active={active && workspace === "render"} refreshKey={refreshKey + attempt}
          onBoard={(source) => { setVisit(null); setBoardPage({ source, requestId: crypto.randomUUID() }); setBoardRefresh((value) => value + 1); onWorkspaceChange("board"); }} />
      </Suspense>
    </div>}
    {(drawingVisited || workspace === "drawing") && <div data-project-surface="drawing" hidden={workspace !== "drawing"} inert={!active || workspace !== "drawing"}
      style={{ height: "100%", minHeight: 0, display: workspace === "drawing" ? "block" : "none" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="Drawing" />}>
        <Drawing projectId={boundProjectId.current!} active={active && workspace === "drawing"} refreshKey={refreshKey + attempt} />
      </Suspense>
    </div>}
    {(boardVisited || workspace === "board") && <div data-project-surface="board" hidden={workspace !== "board" || pageOpen} inert={!active || workspace !== "board" || pageOpen}
      style={{ height: "100%", minHeight: 0, display: workspace === "board" && !pageOpen ? "block" : "none" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="MonkeyBoard" />}>
        <Board onPublish={(revision, ids) => { setPublishRequest({ revision, ids, requestId: crypto.randomUUID() }); onWorkspaceChange("publish"); }} expectedProjectId={boundProjectId.current} refreshKey={refreshKey + attempt + boardRefresh} active={active && workspace === "board" && !pageOpen} onSubmit={submitFeedback} onSketch={submitSketch} onOpenDocument={setVisit} pageRequest={boardPage} />
      </Suspense>
    </div>}
    {(treeVisited || workspace === "tree") && <div data-project-surface="tree" hidden={workspace !== "tree"} inert={!active || workspace !== "tree"}
      style={{ height: "100%", minHeight: 0, display: workspace === "tree" ? "block" : "none" }}>
      <Suspense fallback={<LoadingOverlay mode="boot" status="Design tree" />}>
        <DesignTreeSurface data={designTree} markSeen={seenCandidates.markSeen} active={active && workspace === "tree"} returnTo={treeReturn.current}
          onLeave={() => onWorkspaceChange(treeReturn.current)} onView={(view) => { setTreeView(view); onWorkspaceChange("arch"); }} />
      </Suspense>
    </div>}
  </div>;
}
