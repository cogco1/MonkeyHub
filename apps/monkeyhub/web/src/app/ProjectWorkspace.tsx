import type { RenderView } from "../workspaces/monkeyarch/viewer/renderView";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { asStudioApiError, StudioApiError } from "../api/project-runtime/client";
import { useConnection, useStudio } from "../api/project-runtime/ProjectRuntimeContext";
import type { ServerIdentity } from "../api/project-runtime/connection";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import type { PageSource } from "../workspaces/monkeyboard/boardScene";
import type { BoardPageRequest } from "../workspaces/monkeyboard/Board";
import { BoardRenderError, type BoardRenderChatRequest } from "../workspaces/monkeyboard/boardRender";
import type { BoardSketchRequest } from "../workspaces/monkeyboard/boardSketch";
import App, { type WorkspaceDesignContext } from "./App";
export type { WorkspaceDesignContext } from "./App";
import { BoardModeSwitch } from "./BoardModeSwitch";
import { ErrorPanel } from "./ErrorPanel";
import { LoadingOverlay } from "./LoadingOverlay";
import { failed, loading, ready, type Loadable } from "./loadable";
import { ProjectBar } from "../features/chrome/SurfaceChrome";
import { currentView, DesignTreeBar, type DesignTreeView } from "../features/designTree/DesignTreeBar";
import { useDesignTree, useSeenCandidates } from "../features/designTree/useDesignTree";
import { treeWords } from "../features/designTree/words";
import { useT } from "../i18n/useT";
import type { MessageKey } from "../i18n/messages.en";
import { createComparisonHost, type HostedComparison } from "../workspaces/monkeyarch/viewer/comparisonPair";

const Drawing = lazy(() => import("../workspaces/monkeydiagram/DrawingCanvas"));
const ModelComparison = lazy(() => import("../workspaces/monkeyarch/viewer/ModelComparison"));
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
  /** Options a chat Study card asked to see: the Design Tree opens on them, once per request (#302). */
  treeFocus?: { runIds: readonly string[]; request: number } | null;
  active?: boolean;
  refreshKey?: number;
  documentRequest?: { source: PageSource; requestId: number } | null;
  /**
   * A result the Hub asked to see beside the exact model it was made from (#284): read-only,
   * over the surface on screen, once per request id. Ids only grow: one no newer than the last
   * taken opens nothing, and a new id opens even the same run again. The run is a result to look
   * at, never a base to accept or continue. A request arriving while this project is not active
   * is dropped. Null withdraws it: the comparison it opened closes, still loading or on screen,
   * without onComparisonClose.
   */
  comparisonRequest?: { candidateRunId: string; requestId: number } | null;
  /** The person pressed Back in a comparison the Hub requested, so the Hub may clear it and restore its own surface. */
  onComparisonClose?: () => void;
  onWorkspaceChange(workspace: "arch" | "board" | "drawing" | "render" | "publish" | "tree"): void;
  onChatRequest?: () => void;
  /**
   * #253: an image discussion from this project's Board, for the host's conversation composer.
   * Only a request naming this workspace's own project is passed on; nothing is sent from here.
   * A host that cannot take it throws a BoardRenderError, which the Board's dialog shows.
   */
  onRenderChatRequest?: (request: BoardRenderChatRequest) => void;
  onDesignContextChange?: (context: WorkspaceDesignContext | null) => void;
  /** Where a chat message lands (#285): the position the Stage chip states, as it states it. */
  onPositionChange?: (position: WorkspacePosition | null) => void;
}

/** What the Stage chip says about the editing position, for the chat composer's target label (#285). */
export interface WorkspacePosition {
  /** Current's place on the Design Tree, such as "3 edits after S2"; null without a tree. */
  readonly current: string | null;
  /** The model open read-only in Modeling when it is not Current, by its tree name; the next message still changes Current. */
  readonly viewing: string | null;
  /** The Stage chip's own position words, "S2 · Current", for a header on top while the chip is not on screen (NA-2); null without a tree. */
  readonly chip: string | null;
}

/** One mounted project: the Board, its page editor and the same local model draft. */
export function ProjectWorkspace({ workspace, expectedProjectId, candidateRunId = null, candidateFollowsHead = false, treeFocus = null, active = true, refreshKey = 0, documentRequest = null,
  comparisonRequest = null, onComparisonClose, onWorkspaceChange, onChatRequest, onRenderChatRequest, onDesignContextChange, onPositionChange }: ProjectWorkspaceProps) {
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
  /**
   * #284: one candidate beside the exact model it was made from, over the surface on screen. It
   * reads and never writes; every surface stays mounted behind it, so Back finds Modeling, its
   * model, camera and unsaved edits, and the tree, as they were. Moving to another surface closes it.
   */
  const [comparisons] = useState(createComparisonHost);
  const [comparison, setComparison] = useState<HostedComparison | null>(null);
  const comparisonOpener = useRef<HTMLElement | null>(null);
  const openComparison = useCallback((runId: string) => {
    // Only a click here hands focus back on return; a Hub request never moves it.
    const focused = document.activeElement;
    comparisonOpener.current = focused instanceof HTMLElement ? focused : null;
    setComparison(comparisons.open(runId));
  }, [comparisons]);
  const closeComparison = useCallback(() => { comparisons.close(); setComparison(null); }, [comparisons]);
  const returnFromComparison = useCallback(() => { comparisons.back(onComparisonClose); setComparison(null); }, [comparisons, onComparisonClose]);
  useEffect(() => {
    if (comparison) return;
    const opener = comparisonOpener.current;
    comparisonOpener.current = null;
    const now = document.activeElement;
    if (opener?.isConnected && (now === null || now === document.body)) opener.focus({ preventScroll: true });
  }, [comparison]);
  const comparedOver = useRef(workspace);
  useEffect(() => {
    if (comparedOver.current === workspace) return;
    comparedOver.current = workspace;
    closeComparison();
  }, [workspace, closeComparison]);
  // A comparison not yet on screen - its pair or its models still loading - when the project leaves is abandoned, never shown later.
  useEffect(() => { if (!active) { comparisons.leave(); setComparison(comparisons.shown()); } }, [active, comparisons]);
  useEffect(() => {
    if (comparisons.request(comparisonRequest, { active, ready: server.status === "ready" }) === "open") comparisonOpener.current = null;
    setComparison(comparisons.shown());
  }, [comparisonRequest, active, server.status, comparisons]);
  /**
   * The one View path (#284, #302): the run opens read-only in Modeling and the
   * chip names it. Modeling asks for it from where it already is on screen, such
   * as a Board page, so only the other surfaces switch to it.
   */
  const [viewRequest, setViewRequest] = useState(0);
  const viewRun = useCallback((view: DesignTreeView | null, fromModeling = false) => {
    closeComparison();
    setTreeView(view); setViewRequest((value) => value + 1);
    if (!fromModeling) onWorkspaceChange("arch");
  }, [onWorkspaceChange, closeComparison]);
  // The run Modeling edits from, as it reports it: a view that became the base is no longer only viewed.
  const [editingRunId, setEditingRunId] = useState<string | null>(null);
  // Modeling's Record edits and continue, for the tree's Continue refused by unrecorded edits (#302).
  const [recordEdits, setRecordEdits] = useState<(() => Promise<void>) | null>(null);
  const registerRecorder = useCallback((record: (() => Promise<void>) | null) => setRecordEdits(() => record), []);
  const designContextChanged = useCallback((context: WorkspaceDesignContext | null) => {
    if (context?.designContext) setEditingRunId(context.designContext.sourceRunId);
    onDesignContextChange?.(context);
  }, [onDesignContextChange]);
  const treeProject = server.status === "ready" ? boundProjectId.current : null;
  const seenCandidates = useSeenCandidates(treeProject);
  const designTree = useDesignTree({ studio, capabilities: server.status === "ready" ? server.value.capabilities : null,
    projectId: treeProject, active, refreshKey: refreshKey + attempt, onHeadMoved: () => setHeadMoves((value) => value + 1) });
  // A node the tree opened read-only, until it becomes the base or the view returns to Current.
  const viewing = treeView && !treeView.back && treeView.runId !== editingRunId ? treeView : null;
  // #285: the chat composer names the same position as the chip, in the chip's words.
  const t = useT();
  const currentAt = useMemo(() => designTree.tree ? treeWords(t, designTree.tree).currentAt() : "", [t, designTree.tree]);
  // NA-2: the chip's position words as DesignTreeBar states them, for the chat header while the chip is out of view.
  const chip = useMemo(() => {
    const tree = designTree.tree;
    if (!tree) return null;
    const stage = tree.currentStage ? treeWords(t, tree).stageName(tree.nodes.get(tree.currentStage)!) : t("designTree.chip.noStage");
    return t("designTree.chip.position", { stage, position: t("designTree.current") });
  }, [t, designTree.tree]);
  useEffect(() => {
    onPositionChange?.(server.status === "ready" ? { current: currentAt || null, viewing: viewing?.name || null, chip } : null);
  }, [onPositionChange, server.status, currentAt, viewing?.name, chip]);
  useEffect(() => () => onPositionChange?.(null), [onPositionChange]);
  // The node the tree opens on (#302): the chip's ready options, or a chat Study card's.
  const [treeNodeFocus, setTreeNodeFocus] = useState<{ node: string; request: number } | null>(null);
  const focusRequests = useRef(0);
  const showReady = useCallback((node: string) => {
    closeComparison();
    setTreeNodeFocus({ node, request: ++focusRequests.current });
    onWorkspaceChange("tree");
  }, [onWorkspaceChange, closeComparison]);
  // Names on the tree, for the comparison's captions only; the pair itself comes from retained records.
  const nameOfRun = useCallback((runId: string) => {
    const tree = designTree.tree;
    if (!tree) return null;
    const nodes = [...tree.nodes.values()];
    const node = nodes.find((item) => item.kind === "stage" && item.runId === runId) ?? nodes.find((item) => item.kind === "candidate" && item.runId === runId);
    return node ? treeWords(t, tree).title(node) : null;
  }, [designTree.tree, t]);
  const studyFocused = useRef(0);
  useEffect(() => {
    const tree = designTree.tree;
    if (!treeFocus || studyFocused.current === treeFocus.request || !tree) return;
    studyFocused.current = treeFocus.request;
    const node = [...tree.nodes.values()].find((item) => item.kind === "candidate" && item.runId !== null && treeFocus.runIds.includes(item.runId));
    if (node) setTreeNodeFocus({ node: node.id, request: ++focusRequests.current });
  }, [treeFocus, designTree.tree]);
  useEffect(() => {
    let live = true;
    setRefreshError(null);
    void (async () => {
      // Both answers are needed; the handshake's refusal is the one said first.
      const projectRead = studio.project();
      projectRead.catch(() => undefined);
      const identity = await connection.probe();
      const project = await projectRead;
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
  // #253: the Board stays on screen; the conversation composer receives the draft. A request
  // naming another project is refused back to the Board's dialog rather than dropped.
  const renderChat = useMemo(() => onRenderChatRequest && ((request: BoardRenderChatRequest) => {
    if (boundProjectId.current === undefined || request.projectId !== boundProjectId.current) {
      throw new BoardRenderError("PROJECT_CHANGED", "The Board's request names a project this workspace is not bound to.");
    }
    onRenderChatRequest(request);
  }), [onRenderChatRequest]);
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
  // What is on screen: the comparison covers the surface it was opened over, which stays mounted.
  const shown = comparison ? null : workspace;
  const modelShown = modelVisible && !comparison;
  return <div className="project-workspace" style={{ height: "100%", minHeight: 0 }}>
    {/* #337: one bar over every surface. The surface on screen puts its menus on the left; the project's
        position stays at the right end, where the Stage chip opens the Design Tree. #300: Board and its
        Layout mode share one rail entry, and this switch moves between the two mounted surfaces. */}
    <ProjectBar label={t("workspace.surfaceBar")}
      lead={((shown === "board" && !pageOpen) || shown === "publish") && <BoardModeSwitch mode={workspace === "publish" ? "layout" : "board"}
        onChange={(mode) => onWorkspaceChange(mode === "layout" ? "publish" : "board")} />}
      position={designTree.available && <DesignTreeBar data={designTree} seen={seenCandidates.seen} open={shown === "tree"} viewing={viewing}
        onToggle={() => { if (comparison) { closeComparison(); onWorkspaceChange("tree"); }
          else onWorkspaceChange(workspace === "tree" ? treeReturn.current : "tree"); }}
        onBackToCurrent={() => viewRun(currentView(designTree))} onShowReady={showReady}
        onRecordEdits={recordEdits} />}>
      {refreshError && <ErrorPanel error={refreshError} what="GET /api/protocol" />}
      {(archVisited || modelVisible) && <div data-project-surface="arch" hidden={!modelShown} inert={!active || !modelShown}
        style={{ height: "100%", minHeight: 0, display: modelShown ? "block" : "none" }}>
        <App server={server.value} expectedProjectId={boundProjectId.current} initialRunId={treeView?.runId ?? candidateRunId}
          initialRunAsset={treeView?.assetSha256 ?? null} initialRunRequest={viewRequest}
          initialRunFollowsHead={treeView ? false : candidateFollowsHead} documentSource={pageOpen ? visit.source : null}
          initialDocumentIntent={documentIntent} initialSketchRequest={sketchRequest}
          active={active && modelShown} refreshKey={refreshKey + attempt + headMoves} onReturnToBoard={openBoard} onOpenBoard={openBoard} onChatRequest={onChatRequest}
          onDesignContextChange={designContextChanged} onRenderReader={registerRenderReader}
          onView={(view) => viewRun({ ...view, back: false }, true)} onRecorder={registerRecorder}
          onOpenTree={designTree.available ? () => onWorkspaceChange("tree") : undefined} />
      </div>}
      {(publishVisited || workspace === "publish") && <div data-project-surface="publish" hidden={shown !== "publish"} inert={!active || shown !== "publish"}
        style={{ height: "100%", minHeight: 0, display: shown === "publish" ? "block" : "none" }}>
        <Suspense fallback={<LoadingOverlay mode="boot" status="Publish" />}>
          <Publish projectId={boundProjectId.current!} active={active && shown === "publish"} refreshKey={refreshKey + attempt} boardRequest={publishRequest} />
        </Suspense>
      </div>}
      {(renderVisited || workspace === "render") && <div data-project-surface="render" hidden={shown !== "render"} inert={!active || shown !== "render"}
        style={{ height: "100%", minHeight: 0, display: shown === "render" ? "block" : "none" }}>
        <Suspense fallback={<LoadingOverlay mode="boot" status="Render" />}>
          <Render readModelView={readRenderView} onModeling={() => onWorkspaceChange("arch")} projectId={boundProjectId.current!} active={active && shown === "render"} refreshKey={refreshKey + attempt}
            onBoard={(source) => { setVisit(null); setBoardPage({ source, requestId: crypto.randomUUID() }); setBoardRefresh((value) => value + 1); onWorkspaceChange("board"); }} />
        </Suspense>
      </div>}
      {(drawingVisited || workspace === "drawing") && <div data-project-surface="drawing" hidden={shown !== "drawing"} inert={!active || shown !== "drawing"}
        style={{ height: "100%", minHeight: 0, display: shown === "drawing" ? "block" : "none" }}>
        {/* #337: the Drawing puts its recipe transfer in its own menus, and a file to confirm in its row. */}
        <Suspense fallback={<LoadingOverlay mode="boot" status="Drawing" />}>
          <Drawing projectId={boundProjectId.current!} active={active && shown === "drawing"} refreshKey={refreshKey + attempt} />
        </Suspense>
      </div>}
      {(boardVisited || workspace === "board") && <div data-project-surface="board" hidden={shown !== "board" || pageOpen} inert={!active || shown !== "board" || pageOpen}
        style={{ height: "100%", minHeight: 0, display: shown === "board" && !pageOpen ? "block" : "none" }}>
        <Suspense fallback={<LoadingOverlay mode="boot" status="MonkeyBoard" />}>
          <Board onPublish={(revision, ids) => { setPublishRequest({ revision, ids, requestId: crypto.randomUUID() }); onWorkspaceChange("publish"); }} expectedProjectId={boundProjectId.current} refreshKey={refreshKey + attempt + boardRefresh} active={active && shown === "board" && !pageOpen} onSubmit={submitFeedback} onSketch={submitSketch} onOpenDocument={setVisit} onRenderChatRequest={renderChat} pageRequest={boardPage} />
        </Suspense>
      </div>}
      {(treeVisited || workspace === "tree") && <div data-project-surface="tree" hidden={shown !== "tree"} inert={!active || shown !== "tree"}
        style={{ height: "100%", minHeight: 0, display: shown === "tree" ? "block" : "none" }}>
        <Suspense fallback={<LoadingOverlay mode="boot" status="Design tree" />}>
          <DesignTreeSurface data={designTree} markSeen={seenCandidates.markSeen} active={active && shown === "tree"} returnTo={treeReturn.current}
            onLeave={() => onWorkspaceChange(treeReturn.current)} onView={viewRun} onCompare={openComparison} onRecordEdits={recordEdits} focus={treeNodeFocus} />
        </Suspense>
      </div>}
      {comparison && <div data-project-surface="compare" inert={!active} style={{ height: "100%", minHeight: 0 }}>
        <Suspense fallback={<LoadingOverlay mode="boot" status="Compare" />}>
          <ModelComparison key={comparison.key} projectId={boundProjectId.current!} candidateRunId={comparison.runId} active={active} nameOf={nameOfRun}
            backLabel={comparison.origin === "tree" ? t("modelCompare.back", { surface: workspace === "tree" ? t("designTree.title")
              : t(`designTree.surface.${workspace}` as MessageKey) }) : t("modelCompare.backPlain")}
            focusOnOpen={comparison.origin === "tree"} onReturn={returnFromComparison} onPhase={(phase) => comparisons.report(comparison.key, phase)} />
        </Suspense>
      </div>}
    </ProjectBar>
  </div>;
}
