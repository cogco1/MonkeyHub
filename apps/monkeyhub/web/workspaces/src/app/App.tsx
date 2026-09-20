/**
 * The studio shell: one conversation beside one model, and everything the
 * server said one click behind.
 *
 * What this file does is hold the few pieces of state the surfaces share — the
 * transcript, the selection, the model in the viewer, the candidates this tab
 * launched — and pass every question to the API. What it deliberately does
 * not do:
 *
 *  - it imports nothing from archflow; local geometry is only a draft preview;
 *  - it derives no impact, no relation counts and no review-readiness result;
 *  - it writes nothing to the project directly and issues nothing;
 *  - its disposable modeling history lasts for the mounted task; Sync retains
 *    a candidate through the existing server, while page reload loses unsynced edits.
 *
 * Every failed call ends in a card showing the server's code and detail. The
 * one error handled rather than merely displayed is `STALE_BASE`: the project
 * moved, so the projection is read again and the fact is said in the
 * transcript.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  BLOCKED_NEEDS_HUMAN,
  MISSING_EDITABLE_CONTROL,
  STALE_CLARIFICATION,
  UNSUPPORTED_REQUEST,
  asStudioApiError,
  type StudioApiError,
} from "../api/client";
import { useStudio } from "../api/ProjectRuntimeContext";
import type { PageSource } from "../workspaces/monkeyboard/boardScene";
import type { ServerIdentity } from "../api/connection";
import type {
  ArtifactListDto,
  CatalogDto,
  DesignHistoryDto,
  DesignStageDto,
  ElevationRequestDto,
  DocumentAnnotationRefDto,
  DocumentVisualInputDto,
  CandidateDto,
  CompareDto,
  ModelGestureDto,
  ModelSourceDto,
  WorkingCopyOptionDto,
  PendingIntentDto,
  ProjectArtifactDto,
  ProposalDto,
  StateProjectionDto,
  ValidationDto,
} from "../api/generated";
import { renderTracingPaperSnapshotPng, captureTracingPaperReview, type GestureTool } from "../workspaces/monkeyarch/Annotate";
import { createModelAnnotationsController, useModelAnnotations } from "../workspaces/monkeyarch/useModelAnnotations";
import { createDocumentAnnotationsController } from "../workspaces/monkeydiagram/useDocumentAnnotations";
import type { DocumentViewContext } from "../workspaces/monkeydiagram/DocumentCanvas";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import type { BoardSketchRequest } from "../workspaces/monkeyboard/boardSketch";
import {
  canonicalRunSourceLabel,
  canonicalSourceLabel,
  candidateSourceLabel,
  receiptDocumentStrings,
  seatOf,
} from "../features/artifacts/artifactLabels";
import { isViewable, viewableArtifacts } from "../features/artifacts/artifactSelection";
import { useWorkModelExport } from "../features/artifacts/useWorkModelExport";
import { Conversation } from "../features/conversation/Conversation";
import type { Choice } from "../features/conversation/cards/QuestionCard";
import type { Selection } from "../features/conversation/Composer";
import { EvidenceDrawer } from "../features/evidence/EvidenceDrawer";
import { useStudioEvents } from "../features/events/EventStream";
import { honestyCount } from "../features/evidence/HonestyTab";
import { usePreferences } from "../features/settings/preferences";
import type { DirectModelAction, DirectModelTool } from "../features/stage/ModelEditPanel";
import type { PushPullTarget } from "../workspaces/monkeyarch/interactionSession";
import { applyDraftCommand, createModelDraft, currentDraft, drawnShapeFromSpec,
  redoDraft, specFromDrawnShape, undoDraft, snapshotsEquivalent, elevationOf, elevationFromProjection,
  type DraftCommand, type DraftObject, type DraftSnapshot, type ModelDraftHistory,
} from "../features/stage/modelDraft";
import { createModelDraftSyncAttempt, syncModelDraft,
  type ModelDraftSource, type ModelDraftSyncAttempt,
} from "../features/stage/syncModelDraft";

interface LocalModelSession {
  history: ModelDraftHistory;
  source: ModelDraftSource;
  synced: DraftSnapshot;
  pending: { snapshot: DraftSnapshot; attempt: ModelDraftSyncAttempt; interactionEpoch: number; viewRequest: number } | null;
  busy: boolean;
  error: string | null;
}

/** Local geometry the project has not been given yet; it lives only in this page. */
function unsynced(session: LocalModelSession): boolean {
  return session.pending !== null || !snapshotsEquivalent(currentDraft(session.history), session.synced);
}

import type { FinishedSketch } from "../features/stage/sketch";
import { type ViewState } from "../features/stage/SourceChip";
import {
  Stage,
  type CaptureState,
  type HomeModel,
  type PickedFacts,
} from "../features/stage/Stage";
import type { VersionExport, VersionGroup } from "../features/stage/VersionsStrip";
import { useT } from "../i18n/useT";
import type { SceneInspection } from "../workspaces/monkeyarch/viewer/sceneInspection";
import {
  LOCAL_SOURCE_LABEL,
  type GhostSpec,
  type GhostTarget,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "../workspaces/monkeyarch/viewer/ThreeDmViewport";
import { semanticObjectNames } from "../workspaces/monkeyarch/viewer/modelDisplay";
import { AppShell } from "./AppShell";
import { ErrorPanel } from "./ErrorPanel";
import { EVIDENCE_PINNED_KEY, type EvidenceTab } from "./evidence";
import { failed, idle, loading, ready, type Loadable } from "./loadable";
import { LoadingOverlay } from "./LoadingOverlay";
import { editingDigestForView, useSession } from "./useSession";
import { useTranscript, type SystemTextPart } from "./transcript";
import { useCandidateRuns } from "./useCandidateRuns";
import { finishEditTiming, startClientTiming, type ClientTimingSpan, type EditTimingTicket } from "./clientTiming";

/** The three refusing outcomes of an intent, and the two that end an exchange. */
const TERMINAL_OUTCOMES = [MISSING_EDITABLE_CONTROL, UNSUPPORTED_REQUEST];

function sameModelSource(left: ModelSourceDto | null | undefined, right: ModelSourceDto | null | undefined): boolean {
  return left?.runId === right?.runId && left?.stateDigest === right?.stateDigest &&
    left?.assetSha256 === right?.assetSha256;
}

function systemText(parts: readonly SystemTextPart[]): {
  readonly text: string;
  readonly parts: readonly SystemTextPart[];
} {
  return {
    text: parts.map((part) => part.text).join(""),
    parts,
  };
}

function readPinned(): boolean {
  try {
    return window.localStorage.getItem(EVIDENCE_PINNED_KEY) === "true";
  } catch {
    return false;
  }
}

function writePinned(pinned: boolean): void {
  try {
    window.localStorage.setItem(EVIDENCE_PINNED_KEY, String(pinned));
  } catch {
    // a browser that refuses site data keeps the default; nothing to say
  }
}

/** The element as the projection names it, for the viewer to find its objects. */
function ghostTarget(
  projection: StateProjectionDto,
  catalog: CatalogDto,
  elementId: string,
): GhostTarget | null {
  const element = projection.elements.find((row) => row.elementId === elementId);
  return element
    ? {
        objectNames: semanticObjectNames(
          catalog.objects,
          catalog.components,
          element.componentId,
          elementId,
        ),
      }
    : null;
}

/**
 * The ghost a proposal is drawn as: the target's objects, stretched along Z
 * when the field is a height, with the closure's elements as a faint cloud.
 * Nothing here is a claim about geometry — it is the proposal's two numbers
 * and the record's element ids, drawn approximately and labelled so.
 */
function ghostSpecFor(
  projection: StateProjectionDto,
  catalog: CatalogDto | null,
  proposal: ProposalDto,
): GhostSpec | null {
  if (proposal.change.kind === "edit_components") return null;
  if (proposal.target.elementId === null || catalog === null) return null;
  const target = ghostTarget(projection, catalog, proposal.target.elementId);
  if (target === null) return null;
  const isHeight = proposal.target.key === "height";
  const old = Number(proposal.change.old);
  const next = Number(proposal.change.new);
  const factor = isHeight && old > 0 && Number.isFinite(next) ? next / old : 1;
  const affected = proposal.impact.propagated
    .filter((ref) => ref.startsWith("entity:"))
    .map((ref) => ghostTarget(projection, catalog, ref.slice("entity:".length)))
    .filter((item): item is GhostTarget => item !== null);
  return { target, factor, scaleAxis: isHeight ? "z" : null, affected };
}

/** Home, with the bytes it is made of; the toolbar sees only the first two. */
interface HomeArtifacts extends HomeModel {
  readonly artifacts: readonly ProjectArtifactDto[];
  /** The reference run, which a fallback has to name to explain itself. */
  readonly referenceRunId: string;
}

export type WorkspaceDesignContext = {
  projectId: string;
  designContext: { sourceRunId: string | null; stateDigest: string; sourceStageRef?: string | null;
    targetComponentId?: string; elementId?: string } | null;
  unavailableReason: "unsaved" | "loading" | "unavailable" | null;
};

export default function App({ server, expectedProjectId, initialDocumentIntent, initialSketchRequest, initialRunId, documentSource = null, active = true, refreshKey = 0, onReturnToBoard, onOpenBoard, onChatRequest, onDesignContextChange }: {
  server: ServerIdentity; initialDocumentIntent?: BoardDesignRequest;
  expectedProjectId?: string;
  /** One calibrated board sketch frame, to be run as a sketch proposal once the session is ready. */
  initialSketchRequest?: BoardSketchRequest;
  /** The exact run this page was opened on, such as a candidate named by the host. */
  initialRunId?: string | null;
  /** Go back to the board this tab opened its page from, once the page is saved. */
  onReturnToBoard?: () => void;
  documentSource?: PageSource | null;
  active?: boolean;
  refreshKey?: number;
  onOpenBoard?: () => void;
  onChatRequest?: () => void;
  onDesignContextChange?: (context: WorkspaceDesignContext | null) => void;
}) {
  const studio = useStudio();
  const t = useT();
  const viewportOpened = useRef(active);
  viewportOpened.current ||= active;
  const { developerMode, language } = usePreferences();
  const transcript = useTranscript();
  const { append, remove: removeEntry, noteJobStatus: noteTranscriptStatus } = transcript;
  const pushNotice = useCallback(
    (line: string) => {
      append({ kind: "system", text: line });
    },
    [append],
  );
  const { binding, session, changingBase, baseError, reload, refreshWorkingCopies, recoverFromStaleBase } = useSession(pushNotice, server.capabilities,
    initialDocumentIntent ? { runId: initialDocumentIntent.modelSource.runId, sourceStageRef: initialDocumentIntent.sourceStageRef }
      : undefined, true, expectedProjectId);
  const [documentIntentStatus, setDocumentIntentStatus] = useState<"pending" | "switching" | "ready" | "done">(initialDocumentIntent ? "pending" : "done");
  const documentIntentStarted = useRef(false);
  const documentIntentSubmitted = useRef(false);
  const sketchSubmitted = useRef(false);
  const sketchPrepared = useRef<"no" | "running" | "done">("no");
  const sketchRetried = useRef(false);
  // One more pass after the preparation settles, because the session it produced
  // may already have been published by then.
  const [sketchPass, setSketchPass] = useState(0);
  const sourceRunId = session.status === "ready" ? session.value.sourceRunId : null;
  const workingCopies = session.status === "ready" ? session.value.workingCopies : [];
  const designHistoryEnabled = server.capabilities.includes("design-history");
  const designHistory = session.status === "ready" ? session.value.designHistory ?? null : null;
  const stageModelSource = session.status === "ready" ? session.value.stageModelSource ?? null : null;
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [drawingBusy, setDrawingBusy] = useState(false);
  const [drawingError, setDrawingError] = useState<StudioApiError | null>(null);
  const [documentController] = useState(() => createDocumentAnnotationsController(studio));
  const documentSaveRef = useRef<(() => Promise<void>) | null>(null);
  const bindDocumentSave = useCallback((save: (() => Promise<void>) | null) => { documentSaveRef.current = save; }, []);
  // The page's own marks are written before the board is shown again; a refused
  // save keeps the operator in the editor with the error, and on this page.
  // One leave at a time: a second click while the page is being written would
  // show the board before the first save answered.
  const leavingToBoard = useRef(false);
  const leaveToBoard = useCallback(() => {
    if (!onReturnToBoard || leavingToBoard.current) return;
    leavingToBoard.current = true;
    void (async () => {
      try {
        await documentSaveRef.current?.();
        setDocumentView((current) => ({ ...current, open: false }));
        onReturnToBoard();
      }
      catch (cause) { setHistoryError(asStudioApiError(cause).detail); }
      finally { leavingToBoard.current = false; }
    })();
  }, [onReturnToBoard]);
  const [documentView, setDocumentView] = useState<DocumentViewContext>({
    open: false, mounted: false, runId: null, sourceSha: null, revisionRef: null, pageIndex: 0,
  });
  useEffect(() => {
    if (!documentSource) {
      setDocumentView((current) => ({ ...current, open: false }));
      return;
    }
    let live = true;
    void (async () => {
      try {
        await documentSaveRef.current?.();
        if (live) setDocumentView({ open: true, mounted: true, runId: documentSource.runId,
          sourceSha: documentSource.assetSha256, revisionRef: documentSource.revisionRef,
          pageIndex: documentSource.pageIndex });
      } catch (cause) { if (live) setHistoryError(asStudioApiError(cause).detail); }
    })();
    return () => { live = false; };
  }, [documentSource]);

  const [selection, setSelection] = useState<Selection | null>(null);
  const [picked, setPicked] = useState<PickedFacts | null>(null);
  const pickRequestRef = useRef(0);
  const [draft, setDraft] = useState("");
  // The one clarification this tab is in the middle of, as the server described
  // it. A ref rather than state because it is not drawn: the cards show what
  // the server said, and this holds the token and its document source guard.
  // There is deliberately no second copy of the conversation here — the
  // transcript is history, and the pending intent is the server's.
  const pendingIntentRef = useRef<(PendingIntentDto & {
    documentContext?: { projectId: string; modelSource: ModelSourceDto };
  }) | null>(null);

  const [artifacts, setArtifacts] = useState<Loadable<ArtifactListDto>>(idle);
  const artifactsReadRef = useRef(0);
  const artifactsReadAbort = useRef<AbortController | null>(null);
  const artifactProjectRef = useRef<string | null>(null);
  const [artifactLoadingSha, setArtifactLoadingSha] = useState<string | null>(
    null,
  );
  const [artifactLoadPhase, setArtifactLoadPhase] = useState<"download" | "parse" | null>(null);
  const [artifactError, setArtifactError] = useState<StudioApiError | null>(
    null,
  );

  const viewportRef = useRef<ViewportController>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [inspection, setInspection] = useState<SceneInspection | null>(null);
  const [viewerMessage, setViewerMessage] = useState("");
  const [viewerStatus, setViewerStatus] = useState<ViewportStatus>("idle");
  const viewerStatusRef = useRef<ViewportStatus>("idle");
  const [sourceLabel, setSourceLabel] = useState<string | null>(null);
  // The listing rows of the artifacts currently in the viewer, kept beside
  // their source label: one seat's export, or every seat of a run shown at
  // once. They are what the loaded picture can be asked about — the bytes
  // carry document strings the loader does not surface, and the receipts that
  // certified those bytes do.
  const [loadedArtifacts, setLoadedArtifacts] = useState<
    readonly ProjectArtifactDto[]
  >([]);
  const loadedArtifactsRef = useRef(loadedArtifacts);
  loadedArtifactsRef.current = loadedArtifacts;
  // The artifacts whose bytes were handed to the viewer and which the viewer
  // has not yet accepted. Committed only when the viewer reports the label,
  // because a refused file leaves the previous model on screen.
  const pendingArtifacts = useRef<readonly ProjectArtifactDto[]>([]);
  const modelLoadRequest = useRef(0);
  const modelDownloadAbort = useRef<AbortController | null>(null);
  const previewContext = useRef({ key: "", revision: 0 });
  const autoShowRef = useRef<{
    candidateId: string | null; context: number; viewRequest: number; started?: boolean;
    timing?: EditTimingTicket | null;
  } | null>(null);
  const monitorDiagnostics = server.capabilities.includes("operation-diagnostics");
  const activeEditTiming = useRef<EditTimingTicket | null>(null);
  const proposalTimings = useRef(new Map<string, EditTimingTicket>());
  const candidateTimings = useRef(new Map<string, EditTimingTicket>());
  const drawingTiming = useRef<ClientTimingSpan | null>(null);
  const [drawingDisplayTiming, setDrawingDisplayTiming] = useState<ClientTimingSpan | null>(null);
  const manualLoadRef = useRef(false);
  const localEditingRef = useRef(false);
  const modelInteractionEpoch = useRef(0);
  // The first seat on screen answers for the picture wherever one row is
  // wanted: the run it belongs to, the receipt a pick is resolved against,
  // the seat a cross-fade is loaded beside.
  const loadedArtifact = loadedArtifacts[0] ?? null;
  const viewingAnotherBase = sourceLabel === LOCAL_SOURCE_LABEL ||
    (loadedArtifact !== null && session.status === "ready" &&
      loadedArtifact.runId !== session.value.projection.referenceRun.runId);
  const modelLoading = artifactLoadingSha !== null || viewerStatus === "loading";
  const stateDigest = editingDigestForView(
    session, changingBase, loadedArtifact?.runId ?? null,
    sourceLabel === LOCAL_SOURCE_LABEL, modelLoading,
  );
  // Every digest on screen, for the strip to say which of its buttons is the
  // picture: one seat's, or all of a run's.
  const loadedShas = loadedArtifacts
    .map((artifact) => artifact.sha256)
    .filter((sha): sha is string => sha !== null);


  const [proposalBusy, setProposalBusy] = useState(false);
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [modelSyncBusy, setModelSyncBusy] = useState(false);
  const [modelRunPending, setModelRunPending] = useState<string | null>(null);
  const [selectingWorkingCopy, setSelectingWorkingCopy] = useState(false);
  // The proposal drawn as a ghost over the loaded model, if any. The viewer
  // drops the drawing itself whenever a model loads or is cleared; this is the
  // shell's record of which proposal the drawing was of.
  const [ghostProposalId, setGhostProposalId] = useState<string | null>(null);
  // Refinements on the wire, one per proposal entry. A move while one is in
  // flight is kept as the value to send next; only the last one is sent.
  const [refiningEntryId, setRefiningEntryId] = useState<string | null>(null);
  // Select + draw + say: the armed tool and the marks made on this picture,
  // sent with the next sentence and cleared when a proposal answers it.
  const [tool, setTool] = useState<GestureTool | null>(null);
  // A cross-fade in the viewer: the loaded export as 'before', a candidate's
  // export of the same seat as 'after', and where the slider stands.
  const [blendState, setBlendState] = useState<{
    candidateId: string;
    against: string;
    t: number;
    meshes: number;
  } | null>(null);
  const comparisonRequest = useRef(0);
  const clearComparison = useCallback(() => {
    comparisonRequest.current += 1;
    viewportRef.current?.clearSecondary();
    setBlendState(null);
  }, []);
  const [captureState, setCaptureState] = useState<CaptureState>("idle");
  const [capturePath, setCapturePath] = useState<string | null>(null);
  const refineInFlight = useRef<string | null>(null);
  const refinePending = useRef<Map<string, number>>(new Map());
  const candidateEntries = useMemo(() => transcript.entries.filter(
    (entry) => entry.kind === "candidate",
  ), [transcript.entries]);
  const candidateRuns = useCandidateRuns(candidateEntries, (candidateId, status) => {
    noteJobStatus(candidateId, status);
    if (status === "failed" || status === "cancelled") {
      finishEditTiming(candidateTimings.current.get(candidateId), status);
      setModelRunPending((current) => current === candidateId ? null : current);
    }
  }, {
    timingFor: (id) => candidateTimings.current.get(id)?.candidate ?? null,
    onReadFailure: (id) => {
      finishEditTiming(candidateTimings.current.get(id), "failed");
      setModelRunPending((current) => current === id ? null : current);
      for (const session of localModels.current.values()) {
        if (session.pending?.attempt.accepted?.candidateId !== id) continue;
        session.busy = false;
        session.error = t("stage.sync.readFailed");
      }
      setModelSyncBusy([...localModels.current.values()].some(session => session.busy));
      refreshLocalModel();
    },
  });
  const candidates = useMemo(() => Object.fromEntries(Object.entries(candidateRuns.runs).flatMap(([id, run]) =>
    run.candidate.status === "ready" ? [[id, run.candidate.value]] : [])) as Record<string, CandidateDto>, [candidateRuns.runs]);
  const validations = useMemo(() => Object.fromEntries(Object.entries(candidateRuns.runs).flatMap(([id, run]) =>
    run.validation.status === "ready" ? [[id, run.validation.value]] : [])) as Record<string, ValidationDto>, [candidateRuns.runs]);
  useEffect(() => {
    const candidateId = autoShowRef.current?.candidateId;
    if (!candidateId) return;
    const run = candidateRuns.runs[candidateId];
    if (run?.job.status === "ready" && run.job.value.status === "failed") {
      setArtifactError(asStudioApiError(new Error(run.job.value.error ?? "Model generation failed.")));
      autoShowRef.current = null;
    } else if (run?.job.status === "failed") setArtifactError(run.job.error);
    else if (run?.candidate.status === "failed") setArtifactError(run.candidate.error);
  }, [candidateRuns.runs]);
  // Which candidates already have a verdict entry; a ref so the job reporter
  // never reads a stale transcript.
  const verdictsRef = useRef<Set<string>>(new Set());

  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [evidencePinned, setEvidencePinned] = useState(readPinned);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>("honesty");
  const [hasNewVersions, setHasNewVersions] = useState(false);
  const knownVersionSources = useRef<{ projectId: string; keys: Set<string> } | null>(null);
  const [versionRefreshRequest, setVersionRefreshRequest] = useState(0);
  const versionRefreshHandled = useRef(0);
  const eventLines = useStudioEvents(server.capabilities.includes("events"), (event) => {
    if (event.type === "model_asset.registered" || event.type === "working_copy.option_added" ||
        event.type === "candidate.succeeded") setVersionRefreshRequest((current) => current + 1);
    if (event.candidateId && (event.type === "candidate.succeeded" || event.type === "candidate.failed")) {
      candidateRuns.refresh(event.candidateId);
    }
  });
  const [conversationOpen, setConversationOpen] = useState(initialDocumentIntent !== undefined);
  useEffect(() => {
    documentIntentStarted.current = false;
    documentIntentSubmitted.current = false;
    setDocumentIntentStatus(initialDocumentIntent ? "pending" : "done");
    if (initialDocumentIntent) setConversationOpen(true);
  }, [initialDocumentIntent]);
  useEffect(() => {
    sketchSubmitted.current = false;
    sketchPrepared.current = "no";
    sketchRetried.current = false;
    if (initialSketchRequest) { setSketchPass((value) => value + 1); setConversationOpen(true); }
  }, [initialSketchRequest]);

  const projection: StateProjectionDto | null =
    session.status === "ready" ? session.value.projection : null;
  const project = session.status === "ready" ? session.value.project : null;
  artifactProjectRef.current = project?.projectId ?? null;
  const contextKey = JSON.stringify([project?.projectId, sourceRunId, projection?.stateDigest, projection?.sourceStageRef, changingBase, session.status]);
  if (previewContext.current.key !== contextKey) {
    previewContext.current = { key: contextKey, revision: previewContext.current.revision + 1 };
  }
  const beginCandidatePreview = useCallback((timing?: EditTimingTicket | null) => {
    finishEditTiming(autoShowRef.current?.timing, "cancelled");
    manualLoadRef.current = false;
    const preview = { candidateId: null as string | null,
      context: previewContext.current.revision, viewRequest: modelLoadRequest.current, timing };
    autoShowRef.current = preview;
    return preview;
  }, []);
  useEffect(() => {
    return () => {
      finishEditTiming(activeEditTiming.current, "cancelled");
      for (const ticket of proposalTimings.current.values()) finishEditTiming(ticket, "cancelled");
      for (const ticket of candidateTimings.current.values()) finishEditTiming(ticket, "cancelled");
      drawingTiming.current?.finish("cancelled");
    };
  }, [contextKey]);
  // A new editing base starts a new exchange, without discarding the draft or history.
  useEffect(() => {
    pickRequestRef.current += 1;
    pendingIntentRef.current = null;
    setSelection(null);
    setPicked(null);
    setTool(null);
    setGhostProposalId(null);
    viewportRef.current?.ghost(null);
    refinePending.current.clear();
  }, [project?.projectId, projection?.stateDigest, projection?.sourceStageRef]);
  const [viewerProjection, setViewerProjection] = useState<StateProjectionDto | null>(null);
  const modelSources = useMemo(() => {
    const options = workingCopies.flatMap((copy) => copy.options.map((option) => ({
      label: `${copy.label} · ${option.label}`, modelSource: option.modelSource,
    })));
    if (artifacts.status === "ready") {
      const rows = viewableArtifacts(artifacts.value.artifacts);
      for (const artifact of rows) {
        if (artifact.representation !== "composed" && rows.some((row) => row.runId === artifact.runId && row.representation === "composed")) continue;
        if (!artifact.modelSource || options.some((row) => row.modelSource.runId === artifact.runId &&
          row.modelSource.assetSha256 === artifact.sha256)) continue;
        options.push({ label: artifact.fileName, modelSource: artifact.modelSource });
      }
    }
    return options;
  }, [workingCopies, artifacts]);
  useEffect(() => {
    if (!project || artifacts.status !== "ready" || artifacts.value.projectId !== project.projectId) return;
    const keys = modelSources.map(({ modelSource }) => JSON.stringify([
      modelSource.runId, modelSource.stateDigest, modelSource.assetSha256,
    ]));
    const previous = knownVersionSources.current;
    if (previous?.projectId !== project.projectId) {
      knownVersionSources.current = { projectId: project.projectId, keys: new Set(keys) };
      setHasNewVersions(false);
      return;
    }
    if (keys.some((key) => !previous.keys.has(key))) setHasNewVersions(true);
    for (const key of keys) previous.keys.add(key);
  }, [artifacts, modelSources, project]);
  const documentEditingRef = useRef<{ projectId: string | null; modelSource: ModelSourceDto | null }>({
    projectId: null, modelSource: null,
  });
  const loadedModelSource = useMemo<ModelSourceDto | null>(() => {
    if (loadedArtifacts.length !== 1 || !loadedArtifact?.sha256) return null;
    return loadedArtifact.modelSource ?? modelSources.find((row) => row.modelSource.runId === loadedArtifact.runId &&
      row.modelSource.assetSha256 === loadedArtifact.sha256)?.modelSource ?? null;
  }, [loadedArtifact, loadedArtifacts.length, modelSources]);
  useEffect(() => {
    setDrawingError(null);
  }, [contextKey, loadedModelSource?.runId, loadedModelSource?.stateDigest, loadedModelSource?.assetSha256, viewerStatus]);
  // What is on screen and can be worked on.
  //
  // Looking at a candidate is not editing it, and this changes neither: it is
  // the exact source the viewer is showing, read off the model it loaded. A
  // pick is resolved against *that* state, and a change made by pointing at it
  // continues from it — because the object under the ray belongs to the picture
  // on screen and to no other run. Nothing here accepts, issues or moves HEAD;
  // it asks nobody to press Continue first to be allowed to point at something.
  //
  // A local file, or a model with no retained source, has no run to continue
  // from and stays outside this: those keep the existing boundary.
  const viewedSource = loadedModelSource;

  const editingSources = modelSources.filter((row) =>
    row.modelSource.runId === projection?.referenceRun.runId && row.modelSource.stateDigest === projection.stateDigest &&
    artifacts.status === "ready" && viewableArtifacts(artifacts.value.artifacts).some((artifact) =>
      artifact.runId === row.modelSource.runId && artifact.sha256 === row.modelSource.assetSha256 &&
      (artifact.representation === "composed" || !artifacts.value.artifacts.some((item) =>
        item.runId === artifact.runId && item.representation === "composed"))),
  );
  const sourceAvailableForEditing = (source: ModelSourceDto | null) => source !== null &&
    source.runId === projection?.referenceRun.runId && source.stateDigest === projection.stateDigest &&
    artifacts.status === "ready" && viewableArtifacts(artifacts.value.artifacts).some((artifact) =>
      artifact.runId === source.runId && artifact.sha256 === source.assetSha256);
  // Viewing another file is independent of the complete source chosen for editing.
  const selectedEditingSource = workingCopies.flatMap((copy) => copy.options.filter((option) => option.id === copy.selectedOptionId))
    .find((option) => sourceAvailableForEditing(option.modelSource))?.modelSource ?? null;
  const retainedEditingSource = documentEditingRef.current.projectId === project?.projectId && sourceAvailableForEditing(documentEditingRef.current.modelSource)
    ? documentEditingRef.current.modelSource : selectedEditingSource ?? (sourceAvailableForEditing(loadedModelSource) ? loadedModelSource : null);
  const editingModelSource = stageModelSource ?? retainedEditingSource ?? (
    new Set(editingSources.map((row) => row.modelSource.assetSha256)).size === 1
      ? editingSources[0].modelSource : null
  );
  const editingModelSourceReady = artifacts.status === "ready" && (stageModelSource === null || sourceAvailableForEditing(stageModelSource)) && (editingModelSource !== null ||
    !modelSources.some((row) => row.modelSource.runId === projection?.referenceRun.runId));
  const [acceptedHistory, setAcceptedHistory] = useState<{
    history: DesignHistoryDto; sources: readonly ModelSourceDto[];
  } | null>(null);
  const acceptedModelSources = useMemo(() => {
    if (designHistory?.projectId !== project?.projectId) return [];
    return acceptedHistory?.history === designHistory ? acceptedHistory.sources
      : designHistory?.stages.map((stage) => stage.modelSource) ?? [];
  }, [acceptedHistory, designHistory, project?.projectId]);
  useEffect(() => {
    if (!designHistoryEnabled || !project || !designHistory) return;
    let current = true;
    const controller = new AbortController();
    void (async () => {
      const histories = await Promise.all(designHistory.branches.map((branch) => branch.branchId === designHistory.branchId
        ? designHistory : studio.designHistory(branch.branchId, controller.signal)));
      if (!current) return;
      if (histories.some((history) => history.projectId !== project.projectId)) {
        throw new Error("The design history belongs to another project.");
      }
      const accepted = histories.flatMap((history) => history.stages.map((stage) => stage.modelSource));
      setAcceptedHistory({ history: designHistory, sources: accepted });
    })()
      .catch((cause) => { if (current) setHistoryError(asStudioApiError(cause).detail); });
    return () => { current = false; controller.abort(); };
  }, [designHistoryEnabled, designHistory, project?.projectId]);
  const retainedCandidates = useMemo(() => {
    if (artifacts.status !== "ready" || artifacts.value.projectId !== project?.projectId ||
        acceptedHistory?.history !== designHistory) return [];
    return modelSources.flatMap((source) => {
      if (acceptedModelSources.some((accepted) => accepted.runId === source.modelSource.runId)) return [];
      const artifact = artifacts.value.artifacts.find((row) => sameModelSource(row.modelSource, source.modelSource));
      return artifact?.sourceStageRef ? [{ ...source, sourceStageRef: artifact.sourceStageRef }] : [];
    });
  }, [artifacts, modelSources, acceptedModelSources, acceptedHistory, designHistory, project?.projectId]);
  documentEditingRef.current = { projectId: project?.projectId ?? null, modelSource: editingModelSource };
  const pendingDocument = pendingIntentRef.current;
  const documentContinuation = pendingDocument?.continuationToken && pendingDocument.documentContext?.projectId === project?.projectId &&
    sameModelSource(pendingDocument.documentContext?.modelSource, editingModelSource) && pendingDocument.stateDigest === editingModelSource?.stateDigest
    ? pendingDocument : null;
  const currentViewSourceRef = useRef(loadedModelSource);
  currentViewSourceRef.current = loadedModelSource;
  const [modelAnnotationsController] = useState(() => createModelAnnotationsController(studio));
  const persistentAnnotations = server.capabilities.includes("model-annotations") && loadedModelSource !== null;
  const modelAnnotations = useModelAnnotations({
    projectId: project?.projectId ?? "", modelSource: persistentAnnotations ? loadedModelSource : null,
  }, modelAnnotationsController);
  const gestures = modelAnnotations.annotations;
  const tracingPaperSignature = useMemo(() => JSON.stringify(gestures), [gestures]);
  const tracingPaperSending = useRef(false);
  const [tracingPaperSend, setTracingPaperSend] = useState<{ busy: boolean; sent: boolean; error: string | null }>({ busy: false, sent: false, error: null });
  useEffect(() => { setTracingPaperSend({ busy: false, sent: false, error: null }); },
    [loadedModelSource?.runId, loadedModelSource?.stateDigest, loadedModelSource?.assetSha256, tracingPaperSignature]);
  const editGestures = (change: (current: readonly ModelGestureDto[]) => readonly ModelGestureDto[]) => {
    modelAnnotations.changeAnnotations(change(gestures));
  };
  const viewedProjection = useMemo(() => {
    const runId = loadedArtifact?.runId;
    if (runId === undefined) return null;
    if (projection?.catalog?.inspectionRun === runId) return projection;
    return viewerProjection?.catalog?.inspectionRun === runId ? viewerProjection : null;
  }, [loadedArtifact?.runId, projection, viewerProjection]);
  const semanticCatalog = viewedProjection?.catalog ?? null;

  // Disposable local drafts stay attached to the exact source, including when
  // a user browses another version. Sync never swaps the viewport underneath them.
  const localModels = useRef(new Map<string, LocalModelSession>());
  const [localRevision, setLocalRevision] = useState(0);
  // An authored project can have a verified state before its first export.
  // Its projection's synthetic reference id is not a retained run. A local
  // file or an unresolved loaded export must never borrow this fallback.
  const draftProjection = viewedProjection ?? (
    loadedArtifact === null && sourceLabel === null && stateDigest !== null &&
    projection?.projectId === project?.projectId ? projection : null);
  const draftSource = draftProjection?.stateDigest && project && sourceLabel !== LOCAL_SOURCE_LABEL
    ? { projectId: project.projectId, stateDigest: draftProjection.stateDigest,
        sourceRunId: viewedProjection ? viewedProjection.referenceRun.runId : sourceRunId,
        sourceStageRef: draftProjection.sourceStageRef ?? null } : null;
  const draftKey = draftSource ? `${draftSource.projectId}:${draftSource.sourceRunId}:${draftSource.stateDigest}:${draftSource.sourceStageRef ?? ""}` : null;
  const localModel = draftKey ? localModels.current.get(draftKey) ?? null : null;
  const draftSnapshot = localModel ? currentDraft(localModel.history) : null;
  const hasLocalGeometry = !!localModel && !!draftSnapshot && [...draftSnapshot.objects.values()].some(object =>
    object.spec !== null && !object.deleted && object !== localModel.history.snapshots[0]!.objects.get(object.elementId));
  // Undo can return to the initial picture while Sync still owns a later
  // snapshot, or after that snapshot was saved. This empty picture is still
  // local work; neither export discovery nor another preview may replace it.
  localEditingRef.current = localModel !== null && (localModel.history.index > 0 ||
    localModel.pending !== null || !snapshotsEquivalent(draftSnapshot!, localModel.synced));
  // Chat continues the verified editing base. Browsing another retained model
  // never selects it, and local geometry cannot impersonate a saved revision.
  const unsavedChatDraft = !!localModel && unsynced(localModel) || [...localModels.current.values()].some((model) =>
    model.source.projectId === project?.projectId && model.source.stateDigest === projection?.stateDigest &&
    model.source.sourceRunId === (projection?.referenceRunSource === "none" ? null : projection?.referenceRun.runId) && unsynced(model));
  const chatContext = useMemo<WorkspaceDesignContext | null>(() => {
    const projectId = project?.projectId ?? binding?.projectId;
    if (!projectId) return null;
    const unavailableReason = unsavedChatDraft ? "unsaved" : changingBase || session.status === "loading" ? "loading"
      : baseError || !projection?.stateDigest || sourceLabel === LOCAL_SOURCE_LABEL ? "unavailable" : null;
    const designContext: WorkspaceDesignContext["designContext"] = unavailableReason || !projection?.stateDigest ? null : {
      sourceRunId: projection.referenceRunSource === "none" ? null : projection.referenceRun.runId,
      stateDigest: projection.stateDigest, sourceStageRef: projection.sourceStageRef,
    };
    // The catalog fast path also labels verified exported objects "local";
    // only ids present in this exact saved projection can cross into chat.
    const retainedPick = picked?.status === "resolved" && picked.sourceState === "current" ||
      picked?.status === "local" && picked.sourceState === designContext?.stateDigest;
    if (designContext && retainedPick && picked &&
        viewedProjection?.referenceRun.runId === designContext.sourceRunId && viewedProjection.stateDigest === designContext.stateDigest && picked.componentId && picked.elementId &&
        projection?.elements.some((element) => element.elementId === picked.elementId && element.componentId === picked.componentId)) {
      designContext.targetComponentId = picked.componentId;
      designContext.elementId = picked.elementId;
    }
    return { projectId, designContext, unavailableReason };
  }, [project?.projectId, binding?.projectId, projection, changingBase, session.status, baseError, sourceLabel,
    unsavedChatDraft, picked, viewedProjection]);
  useEffect(() => {
    onDesignContextChange?.(chatContext);
    return () => onDesignContextChange?.(null);
  }, [onDesignContextChange, chatContext]);
  // The Hub keeps this model workspace mounted while the Board is visible.
  const returnToBoard = onReturnToBoard && documentView.open ? leaveToBoard : undefined;
  const refreshLocalModel = useCallback(() => setLocalRevision(value => value + 1), []);
  const ensureLocalModel = useCallback((): LocalModelSession => {
    if (!draftKey || !draftSource || !draftProjection) throw new Error(t("stage.sketch.noComponent"));
    const retained = localModels.current.get(draftKey);
    if (retained) return retained;
    const objects: DraftObject[] = draftProjection.elements.map(element => ({
      elementId: element.elementId, componentId: element.componentId,
      spec: element.drawnShape ? specFromDrawnShape(element.drawnShape) : null,
      originalObjectNames: semanticCatalog?.elements.find(row => row.elementId === element.elementId)?.objectNames ?? [],
      created: false, parameterBoundFields: element.drawnShape?.parameterBoundFields,
      elevation: elevationFromProjection(element.elevation),
    }));
    const history = createModelDraft(objects, draftProjection.levels ?? []);
    const session: LocalModelSession = { history, source: draftSource, synced: currentDraft(history),
      pending: null, busy: false, error: null };
    localModels.current.set(draftKey, session);
    return session;
  }, [draftKey, draftSource?.stateDigest, draftProjection, semanticCatalog, t]);
  useEffect(() => {
    if (!localModel || !draftSnapshot) { viewportRef.current?.draftPreview(null); return; }
    const initial = localModel.history.snapshots[0]!.objects;
    const changed = [...draftSnapshot.objects.values()].filter(object => object !== initial.get(object.elementId));
    viewportRef.current?.draftPreview({
      objects: changed.flatMap(object => object.spec && !object.deleted ? [{ elementId: object.elementId, spec: object.spec }] : []),
      hiddenObjectNames: changed.flatMap(object => object.originalObjectNames),
    });
  }, [draftKey, draftSnapshot, localRevision, sourceLabel]);
  const commitLocalCommand = useCallback((command: DraftCommand) => {
    const session = ensureLocalModel();
    session.history = applyDraftCommand(session.history, command);
    modelInteractionEpoch.current += 1;
    if (!session.busy && session.pending && !session.pending.attempt.finalProposalId) session.pending = null;
    autoShowRef.current = null;
    setModelRunPending(null);
    session.error = null;
    refreshLocalModel();
  }, [ensureLocalModel, refreshLocalModel]);


  useEffect(() => {
    const runId = loadedArtifact?.runId;
    if (changingBase || selectingWorkingCopy || modelLoading || runId === undefined || projection?.catalog?.inspectionRun === runId) {
      setViewerProjection(null);
      return;
    }
    if (viewerProjection?.catalog?.inspectionRun === runId) return;
    let current = true;
    const controller = new AbortController();
    setViewerProjection(null);
    void studio
      .state(runId, undefined, controller.signal)
      .then((answer) => {
        if (current) {
          setViewerProjection(answer.catalog?.inspectionRun === runId ? answer : null);
        }
      })
      .catch(() => {
        if (current) setViewerProjection(null);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [changingBase, selectingWorkingCopy, modelLoading, loadedArtifact?.runId, projection?.catalog]);

  useEffect(() => {
    setCaptureState("idle");
    setCapturePath(null);
  }, [loadedArtifact?.runId, sourceLabel]);

  const sendTracingPaperToBoard = useCallback(async () => {
    const viewport = viewportRef.current;
    if (!viewport || tracingPaperSending.current || !project || !loadedModelSource || !persistentAnnotations || !modelAnnotations.ready || gestures.length === 0) return;
    tracingPaperSending.current = true;
    setTracingPaperSend({ busy: true, sent: false, error: null });
    const modelSource = { ...loadedModelSource };
    const acceptedStage = designHistory?.stages.find((stage) => sameModelSource(stage.modelSource, modelSource));
    const sourceStageRef = acceptedStage?.stageRef ?? (sameModelSource(loadedArtifact?.modelSource, modelSource)
      ? loadedArtifact?.sourceStageRef ?? null : null);
    try {
      const capture = await captureTracingPaperReview(viewport, gestures, modelAnnotations.save);
      const png = await renderTracingPaperSnapshotPng(capture.viewportPng, capture.saved.annotations);
      const review = await studio.createTracingPaperReview({
        projectId: project.projectId, modelSource, sourceStageRef,
        annotationRevisionSha256: capture.saved.revisionSha256!, camera: capture.camera, screenSize: capture.screenSize,
      }, png);
      if (sameModelSource(currentViewSourceRef.current, modelSource)) {
        setTracingPaperSend({ busy: false, sent: true, error: null });
      }
      append({ kind: "system", text: `Tracing Paper review sent to Board · ${review.fileName}` });
    } catch (cause) {
      if (sameModelSource(currentViewSourceRef.current, modelSource)) {
        setTracingPaperSend({ busy: false, sent: false, error: asStudioApiError(cause).detail });
      }
    } finally {
      tracingPaperSending.current = false;
    }
  }, [append, designHistory, gestures, loadedArtifact, loadedModelSource, modelAnnotations, persistentAnnotations, project]);

  const captureViewport = useCallback(async () => {
    const runId = loadedArtifact?.runId;
    if (runId === undefined || blendState !== null) return;
    setCaptureState("busy");
    setCapturePath(null);
    try {
      const png = await viewportRef.current?.capturePng();
      if (png === null || png === undefined) {
        throw new Error("The viewport has no project model to capture.");
      }
      const saved = await studio.capture(runId, png);
      setCapturePath(saved.relativePath);
      setCaptureState("success");
    } catch (cause) {
      const error = asStudioApiError(cause);
      setCaptureState("error");
      append({
        kind: "system",
        ...systemText([
          { kind: "technical", text: error.code },
          { kind: "prose", text: ` · ${error.detail}` },
        ]),
      });
    }
  }, [append, blendState, loadedArtifact?.runId]);

  /** A semantic target lights only the exact object names in this run's catalog. */
  const selectSemanticTarget = useCallback((componentId: string, elementId: string | null) => {
    pickRequestRef.current += 1;
    setSelection({ componentId, elementId });
    setPicked(null);
    const objectNames =
      semanticCatalog === null
        ? []
        : semanticObjectNames(
            semanticCatalog.objects,
            semanticCatalog.components,
            componentId,
            elementId,
          );
    viewportRef.current?.highlight({ objectNames });
  }, [semanticCatalog]);

  useEffect(() => {
    if (selection === null) return;
    if (selection.elementId && draftSnapshot?.objects.get(selection.elementId)?.spec &&
        viewportRef.current?.highlight({ draftElementId: selection.elementId })) return;
    if (semanticCatalog === null) return;
    viewportRef.current?.highlight({
      objectNames: semanticObjectNames(
        semanticCatalog.objects,
        semanticCatalog.components,
        selection.componentId,
        selection.elementId,
      ),
    });
  }, [selection, semanticCatalog, draftSnapshot]);

  // The binding, said once per projection the tab reads.
  const announcedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!projection || !project) return;
    const key = `${projection.recordDigest}:${projection.stateDigest ?? "-"}`;
    if (announcedRef.current === key) return;
    announcedRef.current = key;
    const parts: SystemTextPart[] = [
      { kind: "prose", text: "Bound to " },
      { kind: "technical", text: project.projectId },
      { kind: "prose", text: " at issue " },
      { kind: "technical", text: String(project.published.version) },
      { kind: "prose", text: " · reference run " },
      { kind: "technical", text: projection.referenceRun.runId },
      { kind: "prose", text: " (" },
      { kind: "technical", text: projection.referenceRunSource },
      { kind: "prose", text: ")" },
    ];
    if (projection.matchesReferenceReceipt === true) {
      parts.push({ kind: "prose", text: " · receipt reproduced" });
    } else if (projection.matchesReferenceReceipt === false) {
      parts.push({ kind: "prose", text: " · receipt not reproduced" });
    }
    if (projection.stateDigest === null) {
      parts.push({
        kind: "prose",
        text: " · the kernel refused the bound view; nothing can be proposed",
      });
    }
    append({
      kind: "system",
      ...systemText(parts),
    });
  }, [append, project, projection]);

  const loadArtifacts = useCallback(async (background = false) => {
    const request = ++artifactsReadRef.current;
    artifactsReadAbort.current?.abort();
    const controller = new AbortController();
    artifactsReadAbort.current = controller;
    const projectId = artifactProjectRef.current;
    const isCurrent = () => request === artifactsReadRef.current && projectId === artifactProjectRef.current;
    if (!background) setArtifacts(loading);
    let lastError: StudioApiError | null = null;
    for (let attempt = 0; attempt < (background ? 3 : 1); attempt += 1) {
      if (!isCurrent()) return;
      try {
        const answer = await studio.artifacts(controller.signal);
        if (!isCurrent()) return;
        if (answer.projectId !== projectId) throw new Error("The model list belongs to another project.");
        setArtifacts(ready(answer));
        if (lastError) {
          const recovered = lastError;
          setArtifactError((current) => current === recovered ? null : current);
        }
        return;
      } catch (cause) {
        if (!isCurrent()) return;
        lastError = asStudioApiError(cause);
        if (!background) { setArtifacts(failed(lastError)); return; }
        setArtifactError(lastError);
        if (attempt < 2) await new Promise<void>((resolve) => window.setTimeout(resolve, 500 * (attempt + 1)));
      }
    }
  }, []);

  useEffect(() => () => {
    artifactsReadRef.current += 1;
    comparisonRequest.current += 1;
    modelLoadRequest.current += 1;
    artifactsReadAbort.current?.abort();
    modelDownloadAbort.current?.abort();
    pendingArtifacts.current = [];
    setArtifactLoadingSha(null);
    setArtifactLoadPhase(null);
  }, [project?.projectId]);

  useEffect(() => {
    if (session.status === "ready") void loadArtifacts();
  }, [session.status, project?.projectId, loadArtifacts]);

  useEffect(() => {
    // Events can arrive during startup or an explicit base switch. Keep the
    // request pending until both lists describe a ready session.
    if (session.status !== "ready" || changingBase || artifacts.status !== "ready" ||
        artifacts.value.projectId !== project?.projectId || versionRefreshHandled.current === versionRefreshRequest) return;
    versionRefreshHandled.current = versionRefreshRequest;
    void loadArtifacts(true);
    void refreshWorkingCopies().catch(() => { /* The existing choices remain usable. */ });
  }, [versionRefreshRequest, session.status, changingBase, artifacts.status, project?.projectId, loadArtifacts, refreshWorkingCopies]);

  const openLocalFile = useCallback((file: File) => {
    finishEditTiming(activeEditTiming.current, "cancelled");
    drawingTiming.current?.finish("cancelled");
    modelLoadRequest.current += 1;
    modelDownloadAbort.current?.abort();
    pendingArtifacts.current = [];
    setArtifactLoadingSha(null);
    setArtifactLoadPhase(null);
    manualLoadRef.current = true;
    void viewportRef.current?.openFile(file);
  }, []);

  const monitorLoads = server.capabilities.includes("operation-timing");
  const startModelLoadTiming = useCallback((projectId: string | null, runId: string, sourceRef: string | null) => {
    const startedAt = new Date().toISOString();
    const started = performance.now();
    return (status: "succeeded" | "failed" | "cancelled") => {
      if (!monitorLoads || projectId === null) return;
      // Reporting cannot delay the viewer or repeat a failed download/parse.
      void studio.recordModelLoad({
        eventId: crypto.randomUUID(), projectId, runId, sourceRef, startedAt,
        endedAt: new Date().toISOString(), durationMs: Math.round(performance.now() - started), status,
      }).catch(() => undefined);
    };
  }, [monitorLoads]);

  /**
   * Put one certified artifact in the viewer, under the chip that says where
   * it came from. Canonical exports and a candidate's own export take the
   * same route — same digest-addressed bytes, same viewer, different label.
   */
  const loadArtifactIntoViewer = useCallback(
    async (artifact: ProjectArtifactDto, label: string, preserveCamera = false, stillCurrent?: () => boolean, parentTiming?: ClientTimingSpan, background = false): Promise<boolean> => {
      if (!parentTiming) finishEditTiming(autoShowRef.current?.timing, "cancelled");
      const request = ++modelLoadRequest.current;
      modelDownloadAbort.current?.abort();
      const controller = new AbortController();
      modelDownloadAbort.current = controller;
      const projectId = artifactProjectRef.current;
      const isCurrent = () => request === modelLoadRequest.current &&
        projectId === artifactProjectRef.current && (stillCurrent?.() ?? true);
      setArtifactError(null);
      if (!artifact.sha256) {
        setArtifactError(
          asStudioApiError(
            new Error(
              "this receipt certifies no sha256, so there are no bytes to " +
                "address; the row says so and the studio will not guess one.",
            ),
          ),
        );
        return false;
      }
      if (!isViewable(artifact)) {
        // The exact STEP is the delivery, and the viewer cannot parse it. It
        // is saved from its card; the picture on the stage is the 3dm preview.
        setArtifactError(
          asStudioApiError(
            new Error(
              `${artifact.fileName} is the exact ${artifact.format.toUpperCase()} file; ` +
                "the viewer shows the 3dm preview of the same model. Save the " +
                "exact file to open it in CAD.",
            ),
          ),
        );
        return false;
      }
      if (!background) {
        setArtifactLoadingSha(artifact.sha256);
        setArtifactLoadPhase("download");
      }
      const timing = monitorDiagnostics && projectId ? startClientTiming(studio, "model_load",
        { projectId, runId: artifact.runId, sourceRef: artifact.receiptRef }, parentTiming?.trace,
        { asset_sha256: artifact.sha256, blocking: !background }) : null;
      const download = timing ? startClientTiming(studio, "model_download", timing.binding, timing.trace,
        { asset_sha256: artifact.sha256, request_kind: "artifact_bytes", blocking: !background }) : null;
      let parse: ClientTimingSpan | null = null;
      const viewportTiming: { current: ClientTimingSpan | null } = { current: null };
      const finishTiming = timing ? (status: "succeeded" | "failed" | "cancelled") => timing.finish(status)
        : startModelLoadTiming(projectId, artifact.runId, artifact.receiptRef);
      let succeeded = false;
      try {
        const file = await studio.artifactFile(
          artifact.sha256,
          artifact.fileName,
          download?.trace,
          controller.signal,
        );
        download?.finish(isCurrent() ? "succeeded" : "cancelled", { input_bytes: file.size });
        if (!isCurrent()) return false;
        pendingArtifacts.current = [artifact];
        if (!background) setArtifactLoadPhase("parse");
        const previous = loadedArtifactsRef.current;
        const viewport = viewportRef.current;
        if (!viewport) throw new Error("The 3D viewport is not ready yet; try again in a moment.");
        parse = timing ? startClientTiming(studio, "model_parse", timing.binding, timing.trace,
          { asset_sha256: artifact.sha256, input_bytes: file.size, blocking: !background }) : null;
        await viewport.openFile(file, label, {
          isCurrent, background,
          onLoadPhase: (phase) => {
            parse?.finish("succeeded");
            viewportTiming.current?.finish("succeeded");
            viewportTiming.current = timing ? startClientTiming(studio, phase === "install" ? "model_install" : "model_projection",
              timing.binding, timing.trace, { asset_sha256: artifact.sha256!, blocking: !background }) : null;
          },
          preserveCamera: preserveCamera && previous.length > 0 && artifact.lengthUnit !== null &&
            previous.every((row) => row.lengthUnit === artifact.lengthUnit),
        });
        succeeded = isCurrent() && viewerStatusRef.current === "ready";
        return succeeded;
      } catch (cause) {
        if (isCurrent()) setArtifactError(asStudioApiError(cause));
        return false;
      } finally {
        const status = !isCurrent() ? "cancelled" : succeeded ? "succeeded" : "failed";
        download?.finish(status);
        parse?.finish(status);
        viewportTiming.current?.finish(status);
        finishTiming(status);
        if (request === modelLoadRequest.current) {
          pendingArtifacts.current = [];
          setArtifactLoadingSha(null);
          setArtifactLoadPhase(null);
        }
      }
    },
    [monitorDiagnostics, startModelLoadTiming],
  );

  /**
   * Put a whole run on the stage: every seat it exported, in the listing's
   * order, as one picture. The seats are certified separately and shown
   * together; the chip names the run and the seats rather than one digest,
   * because there is no one file on screen to address.
   */
  const loadRunIntoViewer = useCallback(
    async (rows: readonly ProjectArtifactDto[], label: string, preserveCamera = false, stillCurrent?: () => boolean) => {
      setArtifactError(null);
      // The previews: one per seat, never the exact STEP beside them.
      const previews = viewableArtifacts(rows);
      const complete = previews.find((row) => row.representation === "composed" && row.modelSource != null);
      const servable = complete ? [complete] : previews;
      if (servable.length === 0) return false;
      if (servable.length === 1) {
        return loadArtifactIntoViewer(servable[0], label, preserveCamera, stillCurrent);
      }
      const request = ++modelLoadRequest.current;
      modelDownloadAbort.current?.abort();
      const controller = new AbortController();
      modelDownloadAbort.current = controller;
      const projectId = artifactProjectRef.current;
      const isCurrent = () => request === modelLoadRequest.current && projectId === artifactProjectRef.current && (stillCurrent?.() ?? true);
      setArtifactLoadingSha(servable[0].sha256);
      setArtifactLoadPhase("download");
      const finishTiming = startModelLoadTiming(projectId, servable[0].runId, null);
      let succeeded = false;
      try {
        // Every seat, or none: a picture missing a seat that nobody was told
        // about would read as the run being smaller than it is.
        const files = await Promise.all(
          servable.map((row) => studio.artifactFile(row.sha256, row.fileName, undefined, controller.signal)),
        );
        if (!isCurrent()) return false;
        pendingArtifacts.current = servable;
        setArtifactLoadPhase("parse");
        await viewportRef.current?.openFiles(files, label, { preserveCamera, isCurrent });
        succeeded = isCurrent() && viewerStatusRef.current === "ready";
        return succeeded;
      } catch (cause) {
        if (isCurrent()) setArtifactError(asStudioApiError(cause));
        return false;
      } finally {
        finishTiming(!isCurrent() ? "cancelled" : succeeded ? "succeeded" : "failed");
        if (request === modelLoadRequest.current) {
          pendingArtifacts.current = [];
          setArtifactLoadingSha(null);
          setArtifactLoadPhase(null);
        }
      }
    },
    [loadArtifactIntoViewer, startModelLoadTiming],
  );

  /**
   * The chip a run wears on the stage: the candidate's own, one seat's
   * digest, or the run and the seats it put there.
   */
  const runSourceLabel = useCallback(
    (runId: string, rows: readonly ProjectArtifactDto[]): string => {
      const launched = transcript.entries.some(
        (entry) => entry.kind === "candidate" && entry.candidateId === runId,
      );
      if (launched) return candidateSourceLabel(runId);
      return rows.length === 1
        ? canonicalSourceLabel(rows[0])
        : canonicalRunSourceLabel(runId, rows.map(seatOf));
    },
    [transcript.entries],
  );

  /** Every export of the reference run the viewer can show, as listed. */
  const referenceExports = useMemo<readonly ProjectArtifactDto[]>(() => {
    if (artifacts.status !== "ready" || projection === null || artifacts.value.projectId !== projection.projectId) return [];
    const complete = editingModelSource ?? artifacts.value.artifacts.find((row) => row.runId === projection.referenceRun.runId && row.representation === "composed" &&
      row.modelSource?.stateDigest === projection.stateDigest)?.modelSource;
    if (complete) return viewableArtifacts(artifacts.value.artifacts.filter((row) =>
      row.runId === complete.runId && row.sha256 === complete.assetSha256,
    ));
    return viewableArtifacts(
      artifacts.value.artifacts.filter(
        (row) => row.runId === projection.referenceRun.runId,
      ),
    );
  }, [artifacts, projection, editingModelSource]);

  const openWorkingOption = useCallback((option: WorkingCopyOptionDto) => {
    if (artifacts.status !== "ready") return;
    const artifact = artifacts.value.artifacts.find((row) => row.runId === option.modelSource.runId &&
      row.sha256 === option.modelSource.assetSha256 && row.available && isViewable(row));
    if (!artifact) {
      setArtifactError(asStudioApiError(new Error("The selected model's exact file is unavailable.")));
      return;
    }
    manualLoadRef.current = true;
    void loadArtifactIntoViewer(artifact, option.label, true);
  }, [artifacts, loadArtifactIntoViewer]);

  // The listing can lag behind the candidate already downloaded and shown.
  // Keep that exact model visible while the independent listing catches up.
  const chosenModelAlreadyShown = viewerStatus === "ready" && loadedModelSource !== null &&
    loadedModelSource.runId === sourceRunId && loadedModelSource.stateDigest === projection?.stateDigest;
  const missingChosenModel = sourceRunId !== null && !chosenModelAlreadyShown &&
    (artifacts.status === "failed" || (artifacts.status === "ready" &&
      artifacts.value.projectId === project?.projectId && referenceExports.length === 0));

  /**
   * Home: the picture the stage opens on, and the one thing that brings it
   * back. Every export of the reference run when it left any; failing that,
   * the last export the listing names (the server lists runs by id; nothing
   * here claims it is the newest), which is the fallback the conversation is
   * told about. Derived once, here, so the auto-load, the toolbar button and
   * the Reference card cannot disagree about what going back means.
   */
  const homeArtifacts = useMemo<HomeArtifacts | null>(() => {
    if (artifacts.status !== "ready" || projection === null || artifacts.value.projectId !== projection.projectId) return null;
    if (referenceExports.length > 0) {
      return {
        kind: "reference",
        runId: projection.referenceRun.runId,
        artifacts: referenceExports,
        referenceRunId: projection.referenceRun.runId,
      };
    }
    // A restored explicit choice must never show an unrelated fallback export.
    if (sourceRunId !== null) return null;
    const rows = viewableArtifacts(artifacts.value.artifacts);
    if (rows.length === 0) return null;
    const pick = rows[rows.length - 1];
    return {
      kind: "fallback",
      runId: pick.runId,
      artifacts: [pick],
      referenceRunId: projection.referenceRun.runId,
    };
  }, [artifacts, projection, referenceExports, sourceRunId]);

  /**
   * Back home, from wherever the stage got to — one seat of another run, a
   * candidate's export, a local file, or nothing at all after clear. The
   * sentence written to the conversation is the auto-load's own, because this
   * is the same act: the reference run's seats, or the fallback export named
   * with the run it came from.
   *
   * `manual` is false for the auto-load alone, which is nobody's choice: a
   * verdict may still put its exact model on screen over it.
   */
  const showHome = useCallback(
    (manual: boolean) => {
      if (homeArtifacts === null) return;
      manualLoadRef.current = manual;
      const rows = homeArtifacts.artifacts;
      if (homeArtifacts.kind === "reference") {
        const seats = rows.map(seatOf);
        append({
          kind: "system",
          ...systemText([
            {
              kind: "prose",
              text:
                rows.length === 1
                  ? "Showing the reference run's export · "
                  : "Showing the reference run's exports · ",
            },
            {
              kind: "technical",
              text: rows.length === 1 ? rows[0].fileName : seats.join(" + "),
            },
          ]),
        });
        void loadRunIntoViewer(rows, runSourceLabel(homeArtifacts.runId, rows));
        return;
      }
      const pick = rows[0];
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "The reference run " },
          { kind: "technical", text: homeArtifacts.referenceRunId },
          {
            kind: "prose",
            text: " left no export; showing the last export listed, ",
          },
          { kind: "technical", text: pick.fileName },
          { kind: "prose", text: " from run " },
          { kind: "technical", text: pick.runId },
        ]),
      });
      void loadArtifactIntoViewer(pick, canonicalSourceLabel(pick));
    },
    [
      append,
      homeArtifacts,
      loadArtifactIntoViewer,
      loadRunIntoViewer,
      runSourceLabel,
    ],
  );

  // The first ten seconds: a bound project shows its own certified model
  // without being asked — home, and the conversation is told which of the two
  // home turned out to be. A file from this machine stays a secondary door;
  // it is the one with no receipt.
  const autoLoadedRef = useRef(false);
  const displayedProjectRef = useRef<string | null>(null);
  useEffect(() => {
    const projectId = project?.projectId ?? null;
    if (session.status !== "ready" || missingChosenModel || displayedProjectRef.current !== projectId) {
      autoLoadedRef.current = false;
      installedCandidate.current = null;
      setLoadedArtifacts([]);
      setSourceLabel(null);
    }
    displayedProjectRef.current = projectId;
  }, [session.status, project?.projectId, missingChosenModel]);
  useEffect(() => {
    if (!active || documentIntentStatus !== "done" || initialRunId) return;
    if (autoLoadedRef.current) return;
    // The first export may arrive while the architect is still drawing.
    // Local Sync owns its adoption, with the same input guard as later runs.
    if (localEditingRef.current) return;
    if (homeArtifacts === null) return;
    if (loadedArtifacts.length > 0 || pendingArtifacts.current.length > 0) return;
    autoLoadedRef.current = true;
    showHome(false);
  }, [documentIntentStatus, homeArtifacts, loadedArtifacts, showHome, initialRunId, active]);

  /** The viewer says which file it holds; that is when the shell writes it down. */
  const noteSource = useCallback((label: string | null) => {
    pickRequestRef.current += 1;
    if (!pendingIntentRef.current?.documentContext) pendingIntentRef.current = null;
    setSourceLabel(label);
    setLoadedArtifacts(
      label === null || label === LOCAL_SOURCE_LABEL
        ? []
        : pendingArtifacts.current,
    );
    pendingArtifacts.current = [];
    setPicked(null);
    // The mark belonged to the picture going away, as the picked chip did.
    viewportRef.current?.highlight(null);
    // A new picture, or none: whatever ghost was drawn belonged to the old one,
    // and so did the marks and the cross-fade.
    setGhostProposalId(null);
    modelAnnotationsController.resetUnbound(documentEditingRef.current.projectId ?? "");
    setTool(null);
    clearComparison();
  }, [clearComparison]);

  const resolvePick = useCallback(
    async (pick: ViewportPick) => {
      const request = ++pickRequestRef.current;
      // The old pick is over the moment a new click is made. It goes before the
      // new one is asked for, not after it answers: between the click on B and
      // the server's answer, Delete must be able to remove nothing at all -
      // least of all A, which is what the last answer happened to be about.
      setPicked(null);
      // What the ray met, lit at once: the click has an answer on the model
      // before the server has said what it is. A resolved pick widens the mark
      // to every object of the element below; an unresolved one leaves it here.
      viewportRef.current?.highlight({ object: pick.object });
      // Local draft ids come only from our draft layer. Exported object names
      // are matched to the already loaded exact-run catalog, without a round trip.
      const binding = pick.draftElementId ? null : semanticCatalog?.objects.find(row => row.name === pick.objectName);
      const documents = pick.documentUserStrings ?? receiptDocumentStrings(loadedArtifact);
      const knownExport = binding?.status === "bound" &&
        pick.userStrings["archflow:component"] === binding.componentId &&
        pick.userStrings["archflow:object_ref"] === `cad-object:${binding.name}` &&
        documents?.["archflow:design_state_digest"] === viewedProjection?.stateDigest &&
        documents?.["archflow:run_id"] === viewedProjection?.referenceRun.runId;
      const localId = pick.draftElementId ?? (knownExport ? binding.elementId : null);
      const localObject = localId ? draftSnapshot?.objects.get(localId) : null;
      const element = localId ? draftProjection?.elements.find(row => row.elementId === localId) : null;
      const componentId = localObject?.componentId ?? element?.componentId;
      if (localId && componentId && draftKey && !localObject?.deleted) {
        setPicked({ elementId: localId, componentId, status: "local", sourceState: draftProjection!.stateDigest!,
          fields: element ? Object.entries(element.numericFields) : [] });
        setSelection({ componentId, elementId: localId });
        return;
      }
      // The state this click is resolved against is the one the picture came
      // from: the viewed source when the viewer is showing a retained run, and
      // the editing projection when it is showing that. A picture with neither
      // — a local file, or an export with no retained source — has no state a
      // click can be resolved against, and says so instead of guessing one.
      const against = viewedSource !== null
        ? { stateDigest: viewedSource.stateDigest, runId: viewedSource.runId }
        : stateDigest !== null ? { stateDigest, runId: sourceRunId } : null;
      if (against === null) {
        const why = sourceLabel === LOCAL_SOURCE_LABEL
          ? "this picture is a file, not a run of this project · a pick is resolved against a retained state"
          : "a pick is resolved against a state; the projection has not loaded yet";
        append({ kind: "system", text: why, parts: [{ kind: "prose", text: why }] });
        return;
      }
      try {
        const resolution = await studio.resolvePick({
          stateDigest: against.stateDigest,
          sourceRunId: against.runId,
          userStrings: pick.userStrings,
          documentUserStrings:
            pick.documentUserStrings ?? receiptDocumentStrings(loadedArtifact),
          objectName: pick.objectName,
        });
        if (request !== pickRequestRef.current) return;
        const subject =
          resolution.elementId ?? resolution.componentId ?? "nothing resolvable";
        append({
          kind: "system",
          ...systemText([
            { kind: "prose", text: "You picked " },
            { kind: "technical", text: subject },
            { kind: "prose", text: " in the model · " },
            { kind: "technical", text: resolution.status },
            { kind: "prose", text: " · source " },
            { kind: "technical", text: resolution.sourceState },
            ...(resolution.detail
              ? ([
                  { kind: "prose", text: " · " },
                  { kind: "technical", text: resolution.detail },
                ] satisfies SystemTextPart[])
              : []),
          ]),
        });
        const element = resolution.elementId
          ? viewedProjection?.elements.find(
              (row) => row.elementId === resolution.elementId,
            )
          : undefined;
        setPicked({
          componentId: resolution.componentId,
          elementId: resolution.elementId,
          status: resolution.status,
          sourceState: resolution.sourceState,
          fields: element
            ? Object.entries(element.numericFields).map(
                ([key, value]) => [key, value] as const,
              )
            : [],
        });
        // The server named it, so the mark becomes the exact object-name set
        // in this run's catalog, not only the face the ray met. When the loaded
        // picture carries none of them, the one hit object stays lit —
        // never nothing, which would read as the click having missed.
        if (resolution.elementId !== null || resolution.componentId !== null) {
          const objectNames =
            resolution.componentId === null || semanticCatalog === null
              ? []
              : semanticObjectNames(
                  semanticCatalog.objects,
                  semanticCatalog.components,
                  resolution.componentId,
                  resolution.elementId,
                );
          const lit =
            viewportRef.current?.highlight({ objectNames }) ?? 0;
          if (lit === 0) viewportRef.current?.highlight({ object: pick.object });
        }
        if (resolution.status === "resolved" && resolution.componentId) {
          setSelection({
            componentId: resolution.componentId,
            elementId: resolution.elementId,
          });
        }
      } catch (cause) {
        if (request !== pickRequestRef.current) return;
        const error = asStudioApiError(cause);
        recoverFromStaleBase(error);
        append({ kind: "refusal", error, what: "POST /api/pick/resolve" });
      }
    },
    [append, loadedArtifact, viewedProjection, recoverFromStaleBase, semanticCatalog, sourceLabel,
     sourceRunId, stateDigest, viewedSource, draftSnapshot, draftKey, draftProjection],
  );

  // One proposal in flight at a time. The busy flag renders the button; this
  // ref is what stops a second send that arrives before React has re-rendered
  // with it — Enter and the form's own submission can both fire for one key.
  const proposingRef = useRef(false);
  /**
   * Take the target the server resolved, whatever this tab was pointing at.
   *
   * The selection and the pending target move together or not at all. That is
   * the whole of "no — the columns" working: the correction is not a hint the
   * next request may or may not act on, it is the state of this tab from the
   * moment the answer arrives.
   */
  const adoptTarget = useCallback(
    (pending: PendingIntentDto) => {
      const componentId = pending.targetComponentId;
      if (componentId === null) return;
      const elementId = pending.elementId ?? null;
      const unchanged =
        selection !== null &&
        selection.componentId === componentId &&
        selection.elementId === elementId;
      selectSemanticTarget(componentId, elementId);
      if (unchanged) return;
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "Now talking about " },
          { kind: "technical", text: elementId ?? componentId },
          { kind: "prose", text: " · the target the server resolved" },
        ]),
      });
    },
    [append, selectSemanticTarget, selection],
  );
  const propose = useCallback(
    async (utterance: string, override?: Selection | null, documentAnnotations: readonly DocumentAnnotationRefDto[] = [], documentModelSource?: ModelSourceDto, documentVisuals: DocumentVisualInputDto[] = []) => {
      const withDocuments = documentAnnotations.length > 0;
      if (withDocuments) {
        if (!server.capabilities.includes("document-visual-input")) throw asStudioApiError(new Error(t("document.visualUnavailable")));
        const current = documentEditingRef.current;
        if (!documentModelSource || current.projectId !== project?.projectId ||
            current.modelSource?.runId !== documentModelSource.runId ||
            current.modelSource.stateDigest !== documentModelSource.stateDigest ||
            current.modelSource.assetSha256 !== documentModelSource.assetSha256) {
          throw asStudioApiError(new Error("The drawing's linked model no longer matches the editing base. Continue from its model before submitting."));
        }
      }
      const requestModelSource = withDocuments ? documentModelSource! : editingModelSource;
      const documentContext = withDocuments && project ? { projectId: project.projectId, modelSource: documentModelSource! }
        : documentContinuation?.documentContext;
      const requestStateDigest = withDocuments ? documentModelSource!.stateDigest : documentContinuation?.stateDigest ?? stateDigest;
      if (requestStateDigest === null || project === null) return;
      const requestedView = loadedModelSource;
      const requestedLoad = modelLoadRequest.current;
      const canContinueIntent = () => {
        const current = currentViewSourceRef.current;
        return documentEditingRef.current.projectId === project.projectId &&
          sameModelSource(documentEditingRef.current.modelSource, requestModelSource) &&
          (documentContext !== undefined || (requestedLoad === modelLoadRequest.current && current?.runId === requestedView?.runId && current?.stateDigest === requestedView?.stateDigest &&
          current?.assetSha256 === requestedView?.assetSha256));
      };
      const canUpdateView = () => canContinueIntent() && requestedLoad === modelLoadRequest.current &&
        sameModelSource(currentViewSourceRef.current, requestedView) && sameModelSource(currentViewSourceRef.current, requestModelSource);
      if (proposingRef.current) return;
      proposingRef.current = true;
      finishEditTiming(activeEditTiming.current, "cancelled");
      const timing: EditTimingTicket | null = monitorDiagnostics ? {
        root: startClientTiming(studio, "design_edit", { projectId: project.projectId,
          runId: requestModelSource?.runId ?? sourceRunId ?? (projection?.referenceRunSource === "none" ? null : projection?.referenceRun.runId ?? null),
          sourceRef: projection?.sourceStageRef }),
        intentMs: 0, betweenActionsMs: 0,
      } : null;
      if (timing) timing.intent = startClientTiming(studio, "intent_wait", timing.root.binding, timing.root.trace);
      activeEditTiming.current = timing;
      // What this request is asked against, and which exchange it belongs to.
      // The token is the whole of the continuity: no transcript is sent, and
      // the pending intent it names carries the original sentence, the target
      // resolved so far and everything already ruled out.
      const viewMatchesRequest = sameModelSource(loadedModelSource, requestModelSource);
      const asked = withDocuments ? null : override === undefined ? (viewMatchesRequest ? selection : null) : override;
      const sentGestures = withDocuments || !viewMatchesRequest ? [] : gestures.map(({ id: _id, ...gesture }) => gesture);
      const continuationToken = withDocuments ? null : pendingIntentRef.current?.continuationToken ?? null;
      // The architect's sentence, exactly as said; the marks on their own line.
      append({ kind: "you", text: utterance });
      if (sentGestures.length > 0) {
        append({
          kind: "system",
          ...systemText([
            { kind: "prose", text: "with " },
            { kind: "technical", text: String(sentGestures.length) },
            {
              kind: "prose",
              text: sentGestures.length === 1 ? " mark on the model: " : " marks on the model: ",
            },
            {
              kind: "technical",
              text: sentGestures.map((gesture) => gesture.kind).join(", "),
            },
          ]),
        });
      }
      setProposalBusy(true);
      // The waiting half, on screen: who is reading what, and for how long.
      // The answer - card, question or refusal - replaces this line.
      const readingId = append({
        kind: "reading",
        subject: withDocuments ? t("document.reading") :
          selection?.elementId ??
          selection?.componentId ??
          (gestures.some((gesture) => gesture.kind === "circle")
            ? t("reading.subjectCircled")
            : t("reading.subjectRecord")),
        recordSize: t("reading.recordSize", {
          components: projection?.counts.components ?? "?",
          elements: projection?.elements.length ?? "?",
        }),
        provider: project.intentProvider,
        startedAt: Date.now(),
      });
      try {
        // The sentence goes to the intent compiler: the process's agent reads
        // it against the record sheet and compiles it into the grammar, or
        // asks. With no agent configured the server takes the sentence as
        // already typed. Either way the proposal that comes back is the
        // record's, and the agent's reading travels beside it, kept apart.
        const answer = await studio.compileIntent({
          sourceStageRef: projection?.sourceStageRef,
          stateDigest: requestStateDigest,
          sourceRunId: requestModelSource?.runId ?? sourceRunId,
          ...(requestModelSource ? { modelSource: requestModelSource } : {}),
          targetComponentId: asked?.componentId ?? null,
          elementId: asked?.elementId ?? null,
          utterance,
          projectId: project.projectId,
          gestures: [...sentGestures],
          ...(withDocuments ? { documentAnnotations: [...documentAnnotations], documentVisuals } : {}),
          continuationToken,
        }, timing?.intent?.trace);
        if (timing) {
          timing.intentMs = timing.intent!.finish("succeeded");
          timing.intentFinishedAt = performance.now();
          if (canUpdateView()) proposalTimings.current.set(answer.proposal.proposalId, timing);
          else finishEditTiming(timing, "cancelled");
        }
        // COMPILED, the one outcome that is a proposal: the exchange is over
        // and the token that got here is spent.
        if (canContinueIntent()) pendingIntentRef.current = null;
        // The marks were included in the request and its retained compilation.
        // The proposal expresses their effect; don't repeat every hit id in chat.
        append({
          kind: "proposal",
          proposal: answer.proposal,
          agent: answer.agent,
          refinements: 0,
        });
        // The reply remains part of its original exchange. Viewing another
        // model while it was computed must not paint that reply onto the new one.
        if (!canUpdateView()) return;
        // The fast stage's picture: a ghost of this proposal over the loaded
        // model, drawn the moment the typed change exists. With no model on
        // screen there is nothing to draw over, and nothing is claimed.
        const spec = projection
          ? ghostSpecFor(projection, semanticCatalog, answer.proposal)
          : null;
        const copied =
          spec && sourceLabel !== null ? (viewportRef.current?.ghost(spec) ?? 0) : 0;
        if (copied > 0) {
          setGhostProposalId(answer.proposal.proposalId);
        } else {
          viewportRef.current?.ghost(null);
          setGhostProposalId(null);
          if (spec && sourceLabel !== null) {
            append({
              kind: "system",
              ...systemText([
                {
                  kind: "prose",
                  text: "nothing to preview here: the loaded file carries no objects of ",
                },
                {
                  kind: "technical",
                  text:
                    answer.proposal.target.elementId ?? answer.proposal.target.componentId,
                },
              ]),
            });
          }
        }
        const { target } = answer.proposal;
        selectSemanticTarget(target.componentId, target.elementId);
        if (!withDocuments) setDraft("");
        // The marks were said; a new sentence starts clean. A question keeps
        // them, so the reply is made with the same marks.
        if (!withDocuments) {
          if (!persistentAnnotations) modelAnnotations.changeAnnotations([]);
          setTool(null);
        }
      } catch (cause) {
        finishEditTiming(timing, canContinueIntent() ? "failed" : "cancelled");
        const error = asStudioApiError(cause);
        const updateView = canUpdateView();
        // No proposal came back, so no card claims the ghost that may still
        // stand from the last one: the picture goes back to the loaded model.
        if (updateView) {
          viewportRef.current?.ghost(null);
          setGhostProposalId(null);
        }
        const pending = error.pendingIntent;
        if (error.code === STALE_CLARIFICATION) {
          // The pending intent was opened against a state the project has
          // left. It is void — an answer given about the old record is not
          // applied to the new one — so this tab drops it rather than
          // continuing an exchange nobody checked.
          if (canContinueIntent()) pendingIntentRef.current = null;
          append({ kind: "refusal", error, what: "POST /api/intents" });
        } else if (pending !== null) {
          // A refusal that belongs to a clarification moves this tab with it:
          // the target the server resolved is the target the next sentence is
          // about, and the two are never allowed to disagree. A terminal
          // outcome leaves no token, so the card that shows it asks nothing.
          if (canContinueIntent()) {
            if (updateView) adoptTarget(pending);
            pendingIntentRef.current = pending.continuationToken === null ? null : { ...pending, ...(documentContext ? { documentContext } : {}) };
          }
          append({
            kind: TERMINAL_OUTCOMES.includes(error.code) ? "terminal" : "question",
            error,
            utterance,
          });
        } else if (error.code === BLOCKED_NEEDS_HUMAN) {
          append({ kind: "question", error, utterance });
        } else {
          if (updateView) recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/intents" });
        }
      } finally {
        removeEntry(readingId);
        proposingRef.current = false;
        setProposalBusy(false);
      }
    },
    [
      adoptTarget,
      append,
      gestures,
      modelAnnotations,
      persistentAnnotations,
      loadedModelSource,
      editingModelSource,
      documentContinuation,
      project,
      projection,
      recoverFromStaleBase,
      removeEntry,
      selectSemanticTarget,
      semanticCatalog,
      selection,
      sourceLabel,
      sourceRunId,
      stateDigest,
      server.capabilities,
      monitorDiagnostics,
      t,
    ],
  );

  /**
   * Answer a question with one of the server's own candidates.
   *
   * One click does all of it: the selection moves to what was chosen and the
   * original request is asked again against it, carrying the pending intent's
   * token. Nothing is retyped, and the request that follows cannot be about
   * the thing that was just ruled out.
   */
  const chooseCandidate = useCallback(
    (choice: Choice) => {
      if (stateDigest === null && documentContinuation === null) return;
      const pending = pendingIntentRef.current;
      if (pending === null) return;
      const next: Selection = {
        componentId: choice.componentId,
        elementId: choice.elementId,
      };
      if (sameModelSource(loadedModelSource, editingModelSource)) selectSemanticTarget(next.componentId, next.elementId);
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "Now talking about " },
          { kind: "technical", text: choice.elementId ?? choice.componentId },
          {
            kind: "prose",
            text: " · chosen from the answers the server offered",
          },
        ]),
      });
      void propose(pending.originalUtterance, next);
    },
    [append, documentContinuation, editingModelSource, loadedModelSource, propose, selectSemanticTarget, stateDigest],
  );

  const changeEditingBase = useCallback(async (runId: string | null, modelSource?: ModelSourceDto, sourceStageRef?: string, branchId?: string, keepDocument = false) => {
    if (changingBase || selectingWorkingCopy || proposalBusy || candidateBusy || refiningEntryId !== null) return null;
    pickRequestRef.current += 1;
    const selectedSource = modelSource ?? (loadedModelSource?.runId === runId ? loadedModelSource : null);
    const group = selectedSource ? workingCopies.find((copy) => copy.options.some((option) =>
      option.modelSource.runId === selectedSource.runId && option.modelSource.stateDigest === selectedSource.stateDigest &&
      option.modelSource.assetSha256 === selectedSource.assetSha256,
    )) : undefined;
    let next;
    setSelectingWorkingCopy(true);
    try {
      await documentSaveRef.current?.();
      if (group && selectedSource) {
        const option = group.options.find((row) => sameModelSource(row.modelSource, selectedSource))!;
        await studio.selectWorkingCopy(group.groupId, {
          projectId: group.projectId, baseRevisionSha256: group.revisionSha256, optionId: option.id,
        });
      }
      next = await reload(runId, sourceStageRef, branchId);
    } catch (cause) {
      setArtifactError(asStudioApiError(cause));
      return null;
    } finally {
      setSelectingWorkingCopy(false);
    }
    if (next !== null) {
      if (!keepDocument) setDocumentView((current) => ({ ...current, runId: next.projection.referenceRunSource === "none" ? null : next.projection.referenceRun.runId, sourceSha: null, revisionRef: null, pageIndex: 0 }));
      pendingIntentRef.current = null;
      if (selectedSource && selectedSource.runId === next.projection.referenceRun.runId && selectedSource.stateDigest === next.projection.stateDigest) {
        documentEditingRef.current = { projectId: next.project.projectId, modelSource: selectedSource };
      }
      manualLoadRef.current = true;
      viewportRef.current?.showOriginal();
      clearComparison();
      if (selectedSource && artifacts.status === "ready" &&
          (loadedArtifact?.runId !== selectedSource.runId || loadedArtifact.sha256 !== selectedSource.assetSha256)) {
        const artifact = artifacts.value.artifacts.find((row) => row.runId === selectedSource.runId && row.sha256 === selectedSource.assetSha256);
        if (artifact) await loadArtifactIntoViewer(artifact, modelSources.find((row) => row.modelSource.assetSha256 === selectedSource.assetSha256)?.label ?? artifact.fileName, true);
      }
      if (runId === null) {
        const complete = next.stageModelSource ?? next.workingCopies.flatMap((copy) => copy.options.filter((option) => option.id === copy.selectedOptionId)).find((option) =>
          option.modelSource.runId === next.projection.referenceRun.runId && option.modelSource.stateDigest === next.projection.stateDigest,
        )?.modelSource ?? (artifacts.status === "ready" ? artifacts.value.artifacts.find((row) =>
          row.runId === next.projection.referenceRun.runId && row.representation === "composed" && row.modelSource?.stateDigest === next.projection.stateDigest,
        )?.modelSource : null);
        documentEditingRef.current = { projectId: next.project.projectId, modelSource: complete ?? null };
        const rows = artifacts.status === "ready" ? viewableArtifacts(
          artifacts.value.artifacts.filter(
            (row) => row.runId === next.projection.referenceRun.runId && (!complete || row.sha256 === complete.assetSha256),
          ),
        ) : [];
        if (rows.length > 0) {
          void loadRunIntoViewer(rows, runSourceLabel(next.projection.referenceRun.runId, rows), true);
        } else {
          viewportRef.current?.clear();
        }
      }
    }
    return next;
  }, [artifacts, candidateBusy, changingBase, clearComparison, loadedArtifact, loadedModelSource, loadArtifactIntoViewer, loadRunIntoViewer, modelSources, proposalBusy, refiningEntryId, reload, runSourceLabel, selectingWorkingCopy, workingCopies]);

  const refreshed = useRef(refreshKey);
  useEffect(() => {
    if (refreshed.current === refreshKey || session.status !== "ready") return;
    refreshed.current = refreshKey;
    void reload(undefined, undefined, undefined, true);
    void loadArtifacts();
  }, [refreshKey, session.status, reload, loadArtifacts]);

  const candidateSelection = useMemo(() => ({ runId: initialRunId }), [initialRunId]);
  const installedCandidate = useRef<typeof candidateSelection | null>(null);
  const candidateRequestKey = JSON.stringify([initialRunId, refreshKey]);
  const candidateRequest = useRef(candidateRequestKey);
  candidateRequest.current = candidateRequestKey;
  useEffect(() => {
    if (!active || !initialRunId || session.status !== "ready" || !project || !viewportRef.current) return;
    const projectId = project.projectId;
    const request = candidateRequestKey;
    const controller = new AbortController();
    let live = true;
    const isCurrent = () => live && candidateRequest.current === request && artifactProjectRef.current === projectId;
    setArtifactError(null);
    // A new chat result owns a fresh listing read; the previous candidate's list
    // may predate this export and workspace events may not have arrived yet.
    void studio.artifacts(controller.signal).then(async (listing) => {
      if (!isCurrent()) return;
      if (listing.projectId !== projectId) throw new Error("The candidate model list belongs to another project.");
      const rows = viewableArtifacts(listing.artifacts.filter((artifact) => artifact.runId === initialRunId));
      setArtifacts(ready(listing));
      if (!rows.length) throw new Error("The selected candidate has no available registered model export.");
      // Refreshing the runtime only rereads an already opened choice. Its mounted
      // camera, local edits and any later manual version selection stay in place.
      if (installedCandidate.current === candidateSelection) return;
      // Opening a chat result only changes the view. Continuing it is a separate explicit action.
      manualLoadRef.current = true;
      if (await loadRunIntoViewer(rows, candidateSourceLabel(initialRunId), false, isCurrent) && isCurrent()) {
        installedCandidate.current = candidateSelection;
      }
    }).catch((cause) => { if (isCurrent()) setArtifactError(asStudioApiError(cause)); });
    return () => { live = false; controller.abort(); };
  }, [initialRunId, candidateSelection, candidateRequestKey, session.status, project?.projectId, studio, loadRunIntoViewer, active]);

  useEffect(() => {
    if (!initialDocumentIntent || documentIntentStarted.current || documentIntentStatus !== "pending" ||
        changingBase) return;
    // The drawing can stay open even when its initial editing base is refused.
    // Preserve the instruction now; a later manual recovery must not replay it.
    if (session.status === "failed") {
      documentIntentStarted.current = true;
      setDocumentIntentStatus("done");
      setDraft(initialDocumentIntent.utterance);
      append({ kind: "refusal", error: session.error, what: "MonkeyBoard" });
      return;
    }
    if (session.status !== "ready" || artifacts.status !== "ready") return;
    documentIntentStarted.current = true;
    setDocumentIntentStatus("switching");
    void (async () => {
      if (project?.projectId !== initialDocumentIntent.projectId) {
        throw new Error("The board request belongs to another project. Its marks are saved; this design instruction has not been submitted.");
      }
      const next = await changeEditingBase(initialDocumentIntent.modelSource.runId, initialDocumentIntent.modelSource,
        initialDocumentIntent.sourceStageRef ?? undefined, undefined, true);
      if (!next || next.project.projectId !== initialDocumentIntent.projectId ||
          next.projection.stateDigest !== initialDocumentIntent.modelSource.stateDigest ||
          next.projection.referenceRun.runId !== initialDocumentIntent.modelSource.runId ||
          (initialDocumentIntent.sourceStageRef !== null && next.projection.sourceStageRef !== initialDocumentIntent.sourceStageRef)) {
        throw new Error("The drawing's exact model and Stage could not be restored. Its marks are saved; this design instruction has not been submitted.");
      }
      setDocumentIntentStatus("ready");
    })().catch((cause) => {
      setDocumentIntentStatus("done");
      setDraft(initialDocumentIntent.utterance);
      append({ kind: "refusal", error: asStudioApiError(cause), what: "MonkeyBoard" });
    });
  }, [append, artifacts.status, changeEditingBase, changingBase, documentIntentStatus, initialDocumentIntent, project?.projectId, session]);

  useEffect(() => {
    if (!initialDocumentIntent || documentIntentStatus !== "ready" || documentIntentSubmitted.current || changingBase) return;
    documentIntentSubmitted.current = true;
    setDocumentIntentStatus("done");
    if (project?.projectId !== initialDocumentIntent.projectId ||
        !sameModelSource(editingModelSource, initialDocumentIntent.modelSource) ||
        projection?.stateDigest !== initialDocumentIntent.modelSource.stateDigest ||
        (initialDocumentIntent.sourceStageRef !== null && projection?.sourceStageRef !== initialDocumentIntent.sourceStageRef)) {
      setDraft(initialDocumentIntent.utterance);
      append({ kind: "refusal", error: asStudioApiError(new Error("The drawing's linked model changed before submission. Its marks are saved; this design instruction has not been submitted.")), what: "MonkeyBoard" });
      return;
    }
    void propose(initialDocumentIntent.utterance, null, initialDocumentIntent.documentAnnotations,
      initialDocumentIntent.modelSource, initialDocumentIntent.documentVisuals).catch((cause) => {
      setDraft(initialDocumentIntent.utterance);
      append({ kind: "refusal", error: asStudioApiError(cause), what: "MonkeyBoard" });
    });
  }, [append, changingBase, documentIntentStatus, editingModelSource, initialDocumentIntent, project?.projectId, projection, propose]);

  const openDesignStage = async (stage: DesignStageDto, branchId = designHistory?.branchId) => {
    await changeEditingBase(stage.modelSource.runId, stage.modelSource, stage.stageRef, branchId);
  };
  const updateDesignHistory = async (operation: () => Promise<DesignStageDto>) => {
    if (historyBusy) return;
    setHistoryBusy(true); setHistoryError(null);
    try { await openDesignStage(await operation()); }
    catch (cause) { setHistoryError(asStudioApiError(cause).detail); }
    finally { setHistoryBusy(false); }
  };
  const selectDesignBranch = async (branchId: string) => {
    setHistoryBusy(true); setHistoryError(null);
    try {
      const history = await studio.designHistory(branchId);
      const head = history.branches.find((branch) => branch.branchId === branchId)?.headStageRef;
      const stage = history.stages.find((item) => item.stageRef === head);
      if (stage) await openDesignStage(stage, branchId);
    } catch (cause) { setHistoryError(asStudioApiError(cause).detail); }
    finally { setHistoryBusy(false); }
  };
  const forkDesignBranch = async (stage: DesignStageDto, branchId: string) => {
    if (!project || !designHistory) return;
    setHistoryBusy(true); setHistoryError(null);
    try {
      await studio.forkBranch({ projectId: project.projectId, branchId, parentBranch: designHistory.branchId, stageRef: stage.stageRef });
      await openDesignStage(stage, branchId);
    } catch (cause) { setHistoryError(asStudioApiError(cause).detail); }
    finally { setHistoryBusy(false); }
  };
  const generateElevation = async (view: ElevationRequestDto["view"]) => {
    if (!project || !loadedModelSource || drawingBusy) return;
    const stage = designHistory?.stages.find((item) => sameModelSource(item.modelSource, loadedModelSource));
    const currentContext = previewContext.current.revision;
    const currentViewRequest = modelLoadRequest.current;
    drawingTiming.current?.finish("cancelled");
    const timing = monitorDiagnostics ? startClientTiming(studio, "drawing_wait", {
      projectId: project.projectId, runId: loadedModelSource.runId, sourceRef: stage?.stageRef,
    }) : null;
    drawingTiming.current = timing;
    setDrawingBusy(true); setDrawingError(null);
    try {
      await documentSaveRef.current?.();
      const result = await studio.elevation({ projectId: project.projectId, view,
        ...(stage ? { sourceStageRef: stage.stageRef } : { modelSource: loadedModelSource }) }, timing?.trace);
      if (previewContext.current.revision !== currentContext || modelLoadRequest.current !== currentViewRequest) {
        timing?.finish("cancelled"); return;
      }
      setDrawingDisplayTiming(timing);
      setDocumentView({ open: true, mounted: true, runId: result.runId, sourceSha: result.assetSha256,
        revisionRef: result.revisionRef ?? null, pageIndex: 0 });
    } catch (cause) {
      timing?.finish(previewContext.current.revision === currentContext && modelLoadRequest.current === currentViewRequest ? "failed" : "cancelled");
      if (previewContext.current.revision === currentContext && modelLoadRequest.current === currentViewRequest) {
        setDrawingError(asStudioApiError(cause));
      }
    }
    finally { setDrawingBusy(false); }
  };
  const combineDesignCandidates = async (candidateIds: string[]) => {
    if (!project || historyBusy || candidateIds.length < 2) return;
    const preview = beginCandidatePreview();
    setHistoryBusy(true); setHistoryError(null);
    try {
      const result = await studio.combineCandidates({ projectId: project.projectId, candidateIds });
      if (autoShowRef.current === preview) preview.candidateId = result.candidateId;
      append({ kind: "candidate", candidateId: result.candidateId, jobId: result.jobId, proposalId: null, status: result.status });
    } catch (cause) { if (autoShowRef.current === preview) autoShowRef.current = null; setHistoryError(asStudioApiError(cause).detail); }
    finally { setHistoryBusy(false); }
  };

  /**
   * The hand moves a proposal's number. The sentence is the grammar's own —
   * ``set <key> to <n>`` with the proposal's keep clause carried — so the
   * server answers without an agent, and the answer replaces the entry's
   * proposal in place. The ghost is redrawn from the proposal that came back,
   * never from the slider: the picture is always the server's number.
   */
  const refine = useCallback(
    async (entryId: string, value: number) => {
      const entry = transcript.entries.find((row) => row.id === entryId);
      if (!entry || entry.kind !== "proposal") return;
      if (stateDigest === null || project === null) return;
      if (refineInFlight.current !== null) {
        refinePending.current.set(entryId, value);
        return;
      }
      const { proposal } = entry;
      if (proposal.change.kind === "edit_components" || proposal.target.key === null) return;
      if (proposal.baseStateDigest !== stateDigest) return;
      const keep =
        proposal.protected.length > 0 ? ` keep ${proposal.protected.join(", ")}` : "";
      const utterance = `set ${proposal.target.key} to ${Number(value.toFixed(6))}${keep}`;
      refineInFlight.current = entryId;
      setRefiningEntryId(entryId);
      try {
        const answer = await studio.compileIntent({
          sourceStageRef: projection?.sourceStageRef,
          stateDigest,
          sourceRunId,
          targetComponentId: proposal.target.componentId,
          elementId: proposal.target.elementId,
          utterance,
          projectId: project.projectId,
        });
        transcript.replaceProposal(entryId, answer.proposal, answer.agent);
        // Drawn under the same condition as the first proposal — a model on
        // screen — not only when a ghost already stood: the first attempt may
        // have found no objects in the file loaded then, and a later file may
        // carry them.
        const spec = projection
          ? ghostSpecFor(projection, semanticCatalog, answer.proposal)
          : null;
        const copied =
          spec && sourceLabel !== null ? (viewportRef.current?.ghost(spec) ?? 0) : 0;
        setGhostProposalId(copied > 0 ? answer.proposal.proposalId : null);
      } catch (cause) {
        const error = asStudioApiError(cause);
        // A refinement is a sentence already in the grammar against a target
        // the record just answered about, so it starts no clarification and
        // carries no token. It can still be refused, and a refusal that ends
        // the matter shows the card that ends it.
        if (TERMINAL_OUTCOMES.includes(error.code)) {
          append({ kind: "terminal", error, utterance });
        } else if (error.code === BLOCKED_NEEDS_HUMAN) {
          append({ kind: "question", error, utterance });
        } else {
          recoverFromStaleBase(error);
          append({ kind: "refusal", error, what: "POST /api/intents" });
        }
      } finally {
        refineInFlight.current = null;
        setRefiningEntryId(null);
      }
      const next = refinePending.current.get(entryId);
      if (next !== undefined) {
        refinePending.current.delete(entryId);
        void refine(entryId, next);
      }
    },
    [
      append,
      project,
      projection,
      recoverFromStaleBase,
      semanticCatalog,
      sourceLabel,
      sourceRunId,
      stateDigest,
      transcript,
    ],
  );

  /**
   * Before / After / Why: this card's run against the run on screen, from
   * the inspection records both retained. The answer is a card in the
   * conversation; the counts are the server's.
   */
  const compareVersions = useCallback(
    async (artifact: ProjectArtifactDto) => {
      const against = loadedArtifact?.runId ?? null;
      if (against === null || artifact.runId === against) return;
      try {
        const comparison = await studio.compare(artifact.runId, against);
        append({ kind: "compare", comparison });
      } catch (cause) {
        append({
          kind: "refusal",
          error: asStudioApiError(cause),
          what: `GET /api/candidates/${artifact.runId}/compare`,
        });
      }
    },
    [append, loadedArtifact],
  );

  /**
   * Compare in the model: the candidate's export of the seat on screen is
   * loaded beside the loaded one and cross-faded. When the candidate left no
   * export of that seat, the conversation says so rather than showing another.
   */
  const compareInModel = useCallback(
    async (comparison: CompareDto) => {
      if (modelLoading) return;
      const shown = loadedArtifact;
      if (shown === null || shown.runId !== comparison.against) {
        append({
          kind: "system",
          ...systemText([
            { kind: "prose", text: "to cross-fade, load an export of " },
            { kind: "technical", text: comparison.against },
            {
              kind: "prose",
              text: " first; the comparison was counted against it",
            },
          ]),
        });
        return;
      }
      const rows = artifacts.status === "ready" ? artifacts.value.artifacts : [];
      const twin = viewableArtifacts(rows).find(
        (row) =>
          row.runId === comparison.candidateId &&
          row.stageId === shown.stageId,
      );
      if (!twin || !twin.sha256) {
        append({
          kind: "system",
          ...systemText([
            { kind: "technical", text: comparison.candidateId },
            { kind: "prose", text: " left no servable export of " },
            { kind: "technical", text: shown.stageId ?? "this seat" },
            { kind: "prose", text: "; nothing to cross-fade" },
          ]),
        });
        return;
      }
      clearComparison();
      const request = comparisonRequest.current;
      const requestedModel = modelLoadRequest.current;
      const shownArtifacts = loadedArtifactsRef.current;
      const isCurrent = () => request === comparisonRequest.current && requestedModel === modelLoadRequest.current &&
        shownArtifacts === loadedArtifactsRef.current;
      try {
        const file = await studio.artifactFile(twin.sha256, twin.fileName);
        if (!isCurrent()) return;
        const meshes = (await viewportRef.current?.loadSecondary(file)) ?? 0;
        if (!isCurrent()) return;
        setBlendState({
          candidateId: comparison.candidateId,
          against: comparison.against,
          t: 0.5,
          meshes,
        });
      } catch (cause) {
        if (!isCurrent()) return;
        append({
          kind: "refusal",
          error: asStudioApiError(cause),
          what: `GET /api/artifacts/${twin.sha256}/bytes`,
        });
      }
    },
    [append, artifacts, clearComparison, loadedArtifact, modelLoading],
  );

  const runCandidate = useCallback(
    async (proposalId: string) => {
      setCandidateBusy(true);
      // The approximation has done its work: from here the picture is the
      // loaded model until the exact geometry arrives.
      viewportRef.current?.ghost(null);
      setGhostProposalId(null);
      const priorTiming = proposalTimings.current.get(proposalId);
      proposalTimings.current.delete(proposalId);
      const timing: EditTimingTicket | null = priorTiming && !priorTiming.root.closed ? priorTiming
        : monitorDiagnostics && project && projection ? {
          root: startClientTiming(studio, "design_edit", { projectId: project.projectId,
            runId: sourceRunId ?? (projection.referenceRunSource === "none" ? null : projection.referenceRun.runId), sourceRef: projection.sourceStageRef }),
          intentMs: 0, betweenActionsMs: 0,
        } : null;
      if (timing) {
        timing.betweenActionsMs = timing.intentFinishedAt === undefined ? 0 : Math.round(performance.now() - timing.intentFinishedAt);
        timing.candidate = startClientTiming(studio, "candidate_wait", timing.root.binding, timing.root.trace);
        activeEditTiming.current = timing;
      }
      const preview = beginCandidatePreview(timing);
      try {
        const accepted = await studio.startCandidate(proposalId, timing?.candidate?.trace);
        setModelRunPending(accepted.candidateId);
        if (timing) candidateTimings.current.set(accepted.candidateId, timing);
        if (autoShowRef.current === preview) preview.candidateId = accepted.candidateId;
        append({
          kind: "candidate",
          candidateId: accepted.candidateId,
          jobId: accepted.jobId,
          proposalId,
          status: accepted.status,
        });
      } catch (cause) {
        finishEditTiming(timing, "failed");
        if (autoShowRef.current === preview) autoShowRef.current = null;
        const error = asStudioApiError(cause);
        setArtifactError(error);
        recoverFromStaleBase(error);
        append({
          kind: "refusal",
          error,
          what: `POST /api/proposals/${proposalId}/candidate`,
        });
      } finally {
        setCandidateBusy(false);
      }
    },
    [append, beginCandidatePreview, monitorDiagnostics, project, projection, recoverFromStaleBase, sourceRunId],
  );

  // A board sketch is submitted exactly once, through the routes a typed sketch
  // already uses: no board geometry is truth until the exact-base candidate has
  // run. An unmodelled project is prepared first, because a project with no
  // state digest has nothing a proposal could be based on. Every way this can
  // end early says so in the conversation: a sketch that vanishes without a
  // word is indistinguishable from a broken button.
  useEffect(() => {
    if (!initialSketchRequest || sketchSubmitted.current) return;
    const refuse = (cause: unknown, what = t("board.sketch.what")) => {
      sketchSubmitted.current = true;
      append({ kind: "refusal", error: asStudioApiError(cause), what });
    };
    // A session that failed is not something to wait through: it has its own
    // error on screen, and the sketch has to say it was not submitted.
    if (session.status === "failed") { refuse(session.error); return; }
    if (session.status !== "ready" || changingBase || proposalBusy) return;
    // The home model auto-loads on this same ready transition and raises a view
    // request of its own. Waiting for the artifact list to settle first keeps
    // the candidate this sketch starts from being cancelled as "not shown".
    if (artifacts.status !== "ready" && artifacts.status !== "failed") return;
    if (project === null) return;
    if (project.projectId !== initialSketchRequest.projectId) { refuse(new Error(t("board.sketch.otherProject"))); return; }
    const sketchStateDigest = projection?.stateDigest ?? null;
    if (sketchStateDigest === null) {
      if (sketchPrepared.current === "running") return;                                          // in flight
      if (sketchPrepared.current === "done") { refuse(new Error(t("board.sketch.unmodelled"))); return; }
      sketchPrepared.current = "running";
      append({ kind: "system", ...systemText([{ kind: "prose", text: t("board.sketch.preparing") }]) });
      void (async () => {
        // The server refuses to seed a project that already carries design
        // records, and a record it cannot bind answers with no digest at all.
        // Both answer 200, so only the flag and the re-read say what happened.
        const prepared = await studio.prepareModeling(project.projectId);
        if (!prepared.initialized) { sketchPrepared.current = "done"; refuse(new Error(t("board.sketch.unmodelled"))); return; }
        // reload answers null both when it failed and when a newer read replaced
        // it, so its answer decides nothing. The next pass reads the session that
        // actually settled; only a digest still absent there is a refusal, and a
        // session that failed outright is refused at the top of this effect.
        await reload();
        sketchPrepared.current = "done";
        setSketchPass((pass) => pass + 1);
      })().catch((cause) => { sketchPrepared.current = "done"; refuse(cause, t("board.sketch.prepareFailed")); });
      return;
    }
    sketchSubmitted.current = true;
    // The record's own root, not whichever component happens to hold the first
    // element: a footprint id is stable across sends, so the component it is
    // authored under must not change when the element order does.
    const roots = (projection?.catalog?.components ?? []).filter((row) => row.parentId === null);
    const componentId = roots.length === 1 ? roots[0].componentId : projection?.elements[0]?.componentId ?? "model";
    append({ kind: "you", text: t("board.sketch.you", { summary: initialSketchRequest.summary }) });
    setProposalBusy(true);
    void (async () => {
      try {
        const proposal = await studio.sketchBatch({
          projectId: project.projectId, stateDigest: sketchStateDigest, sourceRunId: sourceRunId ?? undefined, keep: [],
          summary: initialSketchRequest.summary,
          sketches: initialSketchRequest.sketches.map((row) => ({ ...row, componentId })),
        });
        append({ kind: "proposal", proposal, agent: null, refinements: 0 });
        selectSemanticTarget(proposal.target.componentId, proposal.target.elementId);
        await runCandidate(proposal.proposalId);
      } catch (cause) {
        const error = asStudioApiError(cause);
        // The base moved under the sketch. recoverFromStaleBase re-projects, so
        // the same frame is offered once more against the base it answers with;
        // a second stale base is the operator's to resolve on the board.
        if (recoverFromStaleBase(error) && !sketchRetried.current) {
          sketchRetried.current = true;
          sketchSubmitted.current = false;
        }
        append({ kind: "refusal", error, what: t("board.sketch.what") });
      } finally { setProposalBusy(false); }
    })();
  }, [append, artifacts.status, changingBase, initialSketchRequest, project, projection, proposalBusy,
      recoverFromStaleBase, reload, runCandidate, selectSemanticTarget, session, sketchPass, sourceRunId, t]);

  // Completed gestures update local geometry and history synchronously. Only
  // the explicit Sync action below crosses the proposal/candidate boundary.
  const sketchSnapPoints = useMemo<readonly (readonly [number, number])[]>(() => [], []);
  const runSketch = useCallback(async (action: FinishedSketch, gestureCurrent: () => boolean = () => true) => {
    if (!gestureCurrent() || changingBase || modelLoading) return;
    try {
      const componentId = selection?.componentId ?? draftProjection?.elements[0]?.componentId;
      if (!componentId) throw new Error(t("stage.sketch.noComponent"));
      const elementId = `drawn-${crypto.randomUUID()}`;
      commitLocalCommand({ kind: "sketch", elementId, componentId, action });
      setPicked({ elementId, componentId, status: "local", sourceState: draftSource?.stateDigest ?? "", fields: [] });
      setSelection({ componentId, elementId });
      setArtifactError(null);
    } catch (cause) { setArtifactError(asStudioApiError(cause)); }
  }, [changingBase, modelLoading, selection?.componentId, draftProjection, commitLocalCommand, t, draftSource?.stateDigest]);

  const [directTool, setDirectTool] = useState<DirectModelTool | null>(null);
  const [directError, setDirectError] = useState<string | null>(null);
  const directToolEpoch = useRef(0);
  const chooseDirectTool = useCallback((next: "select" | DirectModelTool) => {
    directToolEpoch.current += 1;
    setDirectTool(next === "select" ? null : next);
    setDirectError(null);
  }, []);
  const pickedShape = (picked?.status === "resolved" || picked?.status === "local") && picked.elementId
    ? viewedProjection?.elements.find((row) => row.elementId === picked.elementId) : null;
  const localPickedObject = picked?.elementId ? draftSnapshot?.objects.get(picked.elementId) : null;
  const elevationObjects = useMemo<readonly DraftObject[]>(() => draftSnapshot ? [...draftSnapshot.objects.values()] :
    (draftProjection?.elements ?? []).map(element => ({ elementId: element.elementId, componentId: element.componentId,
      spec: element.drawnShape ? specFromDrawnShape(element.drawnShape) : null,
      elevation: elevationFromProjection(element.elevation), parameterBoundFields: element.drawnShape?.parameterBoundFields,
      created: false, originalObjectNames: [] })), [draftSnapshot, draftProjection]);
  const elevationObject = (picked?.status === "resolved" || picked?.status === "local")
    ? elevationObjects.find(object => object.elementId === picked.elementId && elevationOf(object)) : null;
  const pushPullTarget = useMemo<PushPullTarget | null>(() => {
    if (!picked?.elementId) return null;
    if (localPickedObject) return localPickedObject.spec && !localPickedObject.deleted && localPickedObject.spec.closed !== false
      ? { elementId: picked.elementId, shape: drawnShapeFromSpec(localPickedObject.spec, localPickedObject.parameterBoundFields) } : null;
    return pickedShape?.drawnShape ? { elementId: picked.elementId, shape: pickedShape.drawnShape } : null;
  }, [picked, localPickedObject, pickedShape, loadedModelSource, project?.projectId]);
  // What this tab has moved through, in order, as run ids. It is navigation,
  // not a second copy of the design: which run is current is still the
  // session's projection, and every run named here stays in the project
  // whether or not this list still points at it.
  const [modelHistory, setModelHistory] = useState<{ runs: readonly string[]; index: number }>(
    { runs: [], index: -1 },
  );
  const navigatingHistory = useRef<string | null>(null);
  // The run on screen, which is what a step back has to return to: a change
  // made while looking at a candidate was made *from* that candidate, and undo
  // means the picture before it. A local file or an authored-only projection
  // has no retained run to add to this history.
  const baseRunId = loadedArtifact?.runId ?? sourceRunId ?? null;
  useEffect(() => {
    if (baseRunId === null) return;
    // Read once, outside the update: whether this base is one an undo or a redo
    // moved to is a fact about the action that just happened, and the updater
    // itself stays a pure function of the history it is given.
    const navigatedTo = navigatingHistory.current;
    navigatingHistory.current = null;
    setModelHistory((current) => {
      const known = current.runs.indexOf(baseRunId);
      if (navigatedTo === baseRunId && known !== -1) return { ...current, index: known };
      if (current.runs[current.index] === baseRunId) return current;
      // A new edit from here: what was undone stops being reachable forwards.
      // The runs themselves are untouched — they are still in the project and
      // still in the versions list; only this tab's way back to them is gone.
      const kept = current.runs.slice(0, current.index + 1).filter((runId) => runId !== baseRunId);
      return { runs: [...kept, baseRunId], index: kept.length };
    });
  }, [baseRunId]);

  const modelNavigationBusy = changingBase || selectingWorkingCopy || modelLoading;
  const [parameterLockBusy, setParameterLockBusy] = useState(false);
  const [parameterLockError, setParameterLockError] = useState<string | null>(null);
  const lockZh = language === "zh-CN";
  const parameterLockReason = !projection?.stateDigest || !project || baseError || sourceLabel === LOCAL_SOURCE_LABEL
    ? (lockZh ? "请先打开可编辑的项目模型。" : "Open an editable project model first.")
    : unsavedChatDraft ? (lockZh ? "请先同步当前模型修改。" : "Sync the current model edits first.")
    : modelNavigationBusy || candidateBusy || modelRunPending !== null || proposalBusy || modelSyncBusy
      ? (lockZh ? "请等待当前模型操作完成。" : "Wait for the current model operation to finish.")
      : loadedArtifact && (!viewedProjection || loadedArtifacts.some((artifact) => artifact.runId !== projection.referenceRun.runId) ||
          viewedProjection.stateDigest !== projection.stateDigest)
        ? (lockZh ? "请先选择从当前查看的版本继续编辑。" : "Choose to continue editing from the viewed version first.") : null;
  const parameterLockContext = useRef({ key: contextKey, reason: parameterLockReason });
  parameterLockContext.current = { key: contextKey, reason: parameterLockReason };
  useEffect(() => { setParameterLockError(null); }, [contextKey]);
  const applyParameterLocks = useCallback(async (parameterKeys: string[], action: "lock" | "unlock") => {
    if (parameterLockBusy || parameterLockReason || !project || !projection?.stateDigest || parameterKeys.length === 0) return;
    const key = contextKey;
    const revision = previewContext.current.revision, viewRequest = modelLoadRequest.current, interactionEpoch = modelInteractionEpoch.current;
    const stillCurrent = () => parameterLockContext.current.key === key && previewContext.current.revision === revision &&
      modelLoadRequest.current === viewRequest && modelInteractionEpoch.current === interactionEpoch;
    setParameterLockBusy(true); setParameterLockError(null);
    try {
      const proposal = await studio.parameterLocks({ projectId: project.projectId, stateDigest: projection.stateDigest,
        sourceRunId: projection.referenceRunSource === "none" ? undefined : projection.referenceRun.runId,
        sourceStageRef: projection.sourceStageRef ?? undefined, parameterKeys, action });
      // A delayed proposal must not start a candidate in a different edit context.
      if (!stillCurrent() || parameterLockContext.current.reason) return;
      append({ kind: "proposal", proposal, agent: null, refinements: 0 });
      await runCandidate(proposal.proposalId);
    } catch (cause) {
      if (!stillCurrent()) return;
      const error = asStudioApiError(cause);
      setParameterLockError(error.detail);
      recoverFromStaleBase(error);
      append({ kind: "refusal", error, what: "POST /api/proposals/parameter-locks" });
    } finally { setParameterLockBusy(false); }
  }, [parameterLockBusy, parameterLockReason, project, projection, contextKey, studio, append, runCandidate, recoverFromStaleBase]);
  const canUndoModel = sourceLabel !== LOCAL_SOURCE_LABEL && (localModel ? localModel.history.index > 0 : modelHistory.index > 0) && !modelNavigationBusy;
  const canRedoModel = sourceLabel !== LOCAL_SOURCE_LABEL && (localModel ? localModel.history.index + 1 < localModel.history.snapshots.length :
    modelHistory.index >= 0 && modelHistory.index < modelHistory.runs.length - 1) && !modelNavigationBusy;

  /** Show one retained run as both the picture and the base edits continue from. */
  const showRunAsBase = useCallback(async (runId: string) => {
    navigatingHistory.current = runId;
    const next = await changeEditingBase(runId);
    if (next === null) {
      navigatingHistory.current = null;
      return false;
    }
    const rows = artifacts.status === "ready"
      ? viewableArtifacts(artifacts.value.artifacts.filter((row) => row.runId === runId))
      : [];
    if (rows.length > 0) {
      manualLoadRef.current = true;
      await loadRunIntoViewer(rows, runSourceLabel(runId, rows), true);
    }
    return true;
  }, [artifacts, changeEditingBase, loadRunIntoViewer, runSourceLabel]);

  const undoModel = useCallback(async () => {
    if (!canUndoModel) return;
    if (localModel) {
      localModel.history = undoDraft(localModel.history);
      if (!localModel.busy && localModel.pending && !localModel.pending.attempt.finalProposalId) localModel.pending = null;
      localModel.error = null; refreshLocalModel(); setPicked(null); setSelection(null); return;
    }
    const runId = modelHistory.runs[modelHistory.index - 1];
    if (runId === undefined) return;
    append({ kind: "system", ...systemText([
      { kind: "prose", text: "Undo · back to " }, { kind: "technical", text: runId },
      { kind: "prose", text: " · the run you left is still there" },
    ]) });
    await showRunAsBase(runId);
  }, [append, canUndoModel, modelHistory, showRunAsBase, localModel, refreshLocalModel]);

  const redoModel = useCallback(async () => {
    if (!canRedoModel) return;
    if (localModel) {
      localModel.history = redoDraft(localModel.history);
      if (!localModel.busy && localModel.pending && !localModel.pending.attempt.finalProposalId) localModel.pending = null;
      localModel.error = null; refreshLocalModel(); setPicked(null); setSelection(null); return;
    }
    const runId = modelHistory.runs[modelHistory.index + 1];
    if (runId === undefined) return;
    append({ kind: "system", ...systemText([
      { kind: "prose", text: "Redo · forward to " }, { kind: "technical", text: runId },
    ]) });
    await showRunAsBase(runId);
  }, [append, canRedoModel, modelHistory, showRunAsBase, localModel, refreshLocalModel]);

  const deletableElementId = picked?.status === "resolved" || picked?.status === "local" ? picked.elementId : null;
  const canDeleteModel = deletableElementId !== null && draftKey !== null && !modelNavigationBusy;
  const deleteSelected = useCallback(() => {
    if (!canDeleteModel || !deletableElementId) return;
    try {
      commitLocalCommand({ kind: "delete", elementId: deletableElementId });
      setPicked(null); setSelection(null); setDirectError(null);
      viewportRef.current?.highlight(null);
    } catch (cause) { setDirectError(asStudioApiError(cause).detail); }
  }, [canDeleteModel, deletableElementId, commitLocalCommand]);

  const applyDirectModelAction = useCallback((action: DirectModelAction) => {
    if (!canDeleteModel || !deletableElementId) return;
    if (action.target && action.target !== pushPullTarget) return;
    try {
      const normal = action.kind === "pushPull" ? action.normal ?? viewportRef.current?.workPlaneFromSelection()?.normal : null;
      if (action.kind === "pushPull" && !normal) throw new Error("Select a face in the model before using Push/Pull.");
      // The action queue captures only values; target is a transient gesture guard.
      const captured = action.kind === "pushPull" ? { kind: "pushPull" as const, distance: action.distance, normal: normal! }
        : action.kind === "rotate" ? { kind: action.kind, angleDegrees: action.angleDegrees, axis: action.axis }
        : action.kind === "scale" ? { kind: action.kind, scale: action.scale }
        : { kind: action.kind, translation: action.translation };
      const copyElementId = action.kind === "copy" ? `drawn-${crypto.randomUUID()}` : undefined;
      commitLocalCommand({ kind: "direct", elementId: deletableElementId, action: captured, copyElementId });
      if (copyElementId && picked?.componentId) {
        setPicked({ ...picked, elementId: copyElementId, status: "local" });
        setSelection({ componentId: picked.componentId, elementId: copyElementId });
      }
      setDirectError(null); setArtifactError(null);
    } catch (cause) { setDirectError(asStudioApiError(cause).detail); }
  }, [canDeleteModel, deletableElementId, pushPullTarget, commitLocalCommand, picked]);

  const syncLocalModel = useCallback(async () => {
    if (!localModel || localModel.busy || modelSyncBusy) return;
    const session = localModel;
    const snapshot = currentDraft(session.history);
    if (session.pending && !session.pending.attempt.finalProposalId &&
        !snapshotsEquivalent(snapshot, session.pending.snapshot)) session.pending = null;
    if (!session.pending && snapshotsEquivalent(snapshot, session.synced)) return;
    session.pending ??= { snapshot, attempt: createModelDraftSyncAttempt(),
      interactionEpoch: modelInteractionEpoch.current, viewRequest: modelLoadRequest.current };
    const pending = session.pending;
    session.busy = true; session.error = null;
    setModelSyncBusy(true); refreshLocalModel();
    if (pending.attempt.accepted) {
      candidateRuns.refresh(pending.attempt.accepted.candidateId);
      return;
    }
    try {
      const frame = await studio.frame(session.source.sourceRunId ?? undefined);
      const accepted = await syncModelDraft(pending.snapshot, session.source, frame, pending.attempt, studio);
      if (accepted) append({ kind: "candidate", proposalId: pending.attempt.finalProposalId!,
        candidateId: accepted.candidateId, jobId: accepted.jobId, status: accepted.status });
      if (!accepted) { session.synced = pending.snapshot; session.pending = null; }
    } catch (cause) {
      session.error = asStudioApiError(cause).detail;
    } finally {
      if (!pending.attempt.accepted) { session.busy = false; setModelSyncBusy(false); }
      refreshLocalModel();
    }
  }, [localModel, modelSyncBusy, append, refreshLocalModel, candidateRuns.refresh]);
  useEffect(() => {
    let changed = false;
    for (const session of localModels.current.values()) {
      const pending = session.pending;
      const accepted = pending?.attempt.accepted;
      if (!session.busy || !pending || !accepted) continue;
      const run = candidateRuns.runs[accepted.candidateId];
      if (run?.job.status !== "ready") continue;
      const status = run.job.value.status;
      if (status !== "succeeded" && status !== "failed" && status !== "cancelled") continue;
      if (status === "succeeded") {
        const candidate = candidates[accepted.candidateId];
        if (!candidate) continue;
        session.synced = pending.snapshot;
        // A quiet, completed batch naturally becomes the next editing base.
        // Bytes and parsing run behind the old interactive model. Any input
        // since Sync cancels adoption, including an unfinished next gesture.
        const stable = () => modelInteractionEpoch.current === pending.interactionEpoch &&
          currentDraft(session.history) === pending.snapshot;
        if (stable() && localModel === session && modelLoadRequest.current === pending.viewRequest) {
          const model = viewableArtifacts(candidate.artifacts).find(row => row.representation === "composed") ??
            viewableArtifacts(candidate.artifacts)[0];
          if (model) void (async () => {
            try {
              const nextProjection = await studio.state(model.runId);
              if (!stable() || modelLoadRequest.current !== pending.viewRequest) return;
              const shown = await loadArtifactIntoViewer(model, candidateSourceLabel(accepted.candidateId), true,
                stable, undefined, true);
              if (!shown) return;
              if (draftKey && localModels.current.get(draftKey) === session && stable()) localModels.current.delete(draftKey);
              setViewerProjection(nextProjection);
              refreshLocalModel();
              await reload(model.runId, undefined, undefined, true, nextProjection);
            } catch (cause) { session.error = asStudioApiError(cause).detail; refreshLocalModel(); }
          })();
        }
      } else session.error = run.job.value.error ?? `Sync ${status}`;
      session.pending = null; session.busy = false; changed = true;
    }
    if (changed) {
      setModelSyncBusy([...localModels.current.values()].some(session => session.busy));
      refreshLocalModel();
    }
  }, [candidateRuns.runs, candidates, draftKey, localModel, loadArtifactIntoViewer, reload, refreshLocalModel]);

  // A different model on screen is a different set of objects. What was *picked*
  // belonged to the picture that went away, so it stops being picked, its mark
  // is taken off, and any pick answer still on its way is dropped rather than
  // landing on the new picture.
  //
  // The selection is not cleared with it. A selection is what the conversation
  // is about — a component and an element of the record — and it stays true
  // while the architect looks at another run. Only the picked object, which is
  // this picture's own, goes; and an action on the model asks for a pick on the
  // picture it is acting on rather than inheriting one from a picture nobody is
  // looking at any more.
  const shownRunRef = useRef<string | null>(null);
  useEffect(() => {
    const runId = loadedArtifact?.runId ?? null;
    if (shownRunRef.current === runId) return;
    const first = shownRunRef.current === null;
    shownRunRef.current = runId;
    if (first) return;
    pickRequestRef.current += 1;
    setPicked(null);
    viewportRef.current?.highlight(null);
  }, [loadedArtifact?.runId]);

  /** Clear the pick and invalidate its pending resolution without cancelling model work. */
  const clearModelSelection = useCallback(() => {
    pickRequestRef.current += 1;
    setSelection(null);
    setPicked(null);
    setDirectTool(null);
    setDirectError(null);
    viewportRef.current?.highlight(null);
  }, []);

  // The shell observes jobs independently of whichever cards are visible.
  const entriesRef = useRef(transcript.entries);
  entriesRef.current = transcript.entries;

  const noteJobStatus = useCallback(
    (candidateId: string, status: string) => {
      noteTranscriptStatus(candidateId, status);
      if (status === "succeeded" && !verdictsRef.current.has(candidateId)) {
        verdictsRef.current.add(candidateId);
        // The card's Protected line quotes what the sentence asked to keep;
        // that is the proposal's, found through the candidate it became.
        const candidateEntry = entriesRef.current.find(
          (entry) => entry.kind === "candidate" && entry.candidateId === candidateId,
        );
        const proposalEntry =
          candidateEntry && candidateEntry.kind === "candidate"
            ? entriesRef.current.find(
                (entry) =>
                  entry.kind === "proposal" &&
                  entry.proposal.proposalId === candidateEntry.proposalId,
              )
            : undefined;
        append({
          kind: "verdict",
          candidateId,
          protectedRefs:
            proposalEntry && proposalEntry.kind === "proposal"
              ? proposalEntry.proposal.protected
              : [],
        });
        void loadArtifacts(true);
      }
    },
    [append, loadArtifacts, noteTranscriptStatus],
  );

  // Show the completed model without waiting for validation. Only the latest
  // requested candidate may replace its unchanged launch view and context.
  useEffect(() => {
    const preview = autoShowRef.current;
    if (!preview?.candidateId || preview.started) return;
    if (localEditingRef.current) {
      autoShowRef.current = null;
      setModelRunPending(null);
      return;
    }
    const candidateId = preview.candidateId;
    const candidate = candidates[candidateId];
    if (!candidate) return;
    const rows = viewableArtifacts(candidate.artifacts);
    if (rows.length === 0) {
      finishEditTiming(preview.timing, "failed");
      setModelRunPending((current) => current === candidateId ? null : current);
      setArtifactError(asStudioApiError(new Error("The finished candidate has no viewable model.")));
      autoShowRef.current = null;
      return;
    }
    const twin =
      rows.find((row) => row.representation === "composed" && row.modelSource != null) ??
      rows.find((row) => loadedArtifact !== null && row.stageId === loadedArtifact.stageId) ??
      rows[0];
    preview.started = true;
    if (preview.context !== previewContext.current.revision ||
        preview.viewRequest !== modelLoadRequest.current || manualLoadRef.current) {
      finishEditTiming(preview.timing, "cancelled");
      setModelRunPending((current) => current === candidateId ? null : current);
      append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "the candidate's model is ready · " },
          { kind: "technical", text: twin.fileName },
          { kind: "prose", text: " · not shown: you chose " },
          { kind: "technical", text: loadedArtifact?.fileName ?? sourceLabel ?? "another view" },
          {
            kind: "prose",
            text: " to look at; its card can show it",
          },
        ]),
      });
      return;
    }
    void (async () => {
      try { await documentSaveRef.current?.(); }
      catch (cause) { setArtifactError(asStudioApiError(cause)); return false; }
      if (autoShowRef.current !== preview || preview.context !== previewContext.current.revision || manualLoadRef.current) return false;
      return loadArtifactIntoViewer(twin, candidateSourceLabel(candidateId), loadedArtifact !== null,
        () => autoShowRef.current === preview && preview.context === previewContext.current.revision, preview.timing?.candidate);
    })().then(async (shown) => {
      finishEditTiming(preview.timing, shown ? "succeeded" :
        autoShowRef.current !== preview || preview.context !== previewContext.current.revision || manualLoadRef.current ? "cancelled" : "failed");
      if (shown && preview.context === previewContext.current.revision) {
        if (designHistoryEnabled) await reload(twin.runId).then((next) => {
          if (next) setDocumentView((current) => ({ ...current, runId: twin.runId, sourceSha: null, revisionRef: null, pageIndex: 0 }));
        });
        append({
        kind: "system",
        ...systemText([
          { kind: "prose", text: "the candidate's model is on screen · " },
          { kind: "technical", text: twin.fileName },
          { kind: "prose", text: " · not accepted" },
        ]),
        });
      }
      if (autoShowRef.current === preview) autoShowRef.current = null;
    }).catch((cause) => {
      setArtifactError(asStudioApiError(cause));
    }).finally(() => {
      setModelRunPending((current) => current === candidateId ? null : current);
    });
  }, [append, candidates, designHistoryEnabled, loadArtifactIntoViewer, loadedArtifact, sourceLabel, reload]);

  // Which candidate the drawer shows: the one whose card was clicked, else
  // the latest this tab launched. A card's "receipts" opens its own run.
  const [evidenceCandidateId, setEvidenceCandidateId] = useState<string | null>(null);
  const openEvidence = useCallback((tab: EvidenceTab, candidateId?: string) => {
    if (candidateId !== undefined) setEvidenceCandidateId(candidateId);
    setEvidenceTab(tab);
    setEvidenceOpen(true);
  }, []);

  const pinEvidence = useCallback((pinned: boolean) => {
    setEvidencePinned(pinned);
    writePinned(pinned);
    if (pinned) setEvidenceOpen(true);
  }, []);

  // ---- derived views ---------------------------------------------------

  const selectedCandidateId =
    evidenceCandidateId !== null &&
    candidateEntries.some((entry) => entry.candidateId === evidenceCandidateId)
      ? evidenceCandidateId
      : candidateEntries.length > 0
        ? candidateEntries[candidateEntries.length - 1].candidateId
        : null;
  const selectedCandidate =
    selectedCandidateId === null ? null : (candidates[selectedCandidateId] ?? null);
  const selectedValidation =
    selectedCandidateId === null
      ? null
      : (validations[selectedCandidateId] ?? null);

  const utteranceOf = useMemo(() => {
    const byProposal = new Map<string, string>();
    for (const entry of transcript.entries) {
      if (entry.kind === "proposal") {
        byProposal.set(entry.proposal.proposalId, entry.proposal.utterance);
      }
    }
    return byProposal;
  }, [transcript.entries]);

  // One card per run. The reference run first, then this tab's candidates
  // newest first, then every other run newest first (the run id carries its
  // stamp). Each card's exports are its seats' files; nothing here decides a
  // verdict — the word on a candidate is the one the server gave.
  // The editable copy of the model on screen. The source is the exact STEP of
  // the delivery actually being viewed - never the editing base, the newest
  // run, or one seat of a picture showing several - and the export, its
  // answer and its refusals live in one place with the rules the rest of the
  // app reads them by.
  const workModelControls = useWorkModelExport({
    rows: artifacts.status === "ready" ? artifacts.value.artifacts : null,
    viewed: { runId: loadedArtifact?.runId ?? null, shas: loadedShas },
    exportWorkModel: studio.exportWorkModel,
    onExported: () => loadArtifacts(true),
  });

  const versions = useMemo<VersionGroup[]>(() => {
    if (artifacts.status !== "ready" || projection === null) return [];
    const launched = new Map(
      candidateEntries.map((entry) => [entry.candidateId, entry.proposalId]),
    );
    const byRun = new Map<string, ProjectArtifactDto[]>();
    for (const artifact of artifacts.value.artifacts) {
      byRun.set(artifact.runId, [...(byRun.get(artifact.runId) ?? []), artifact]);
    }
    const groups = [...byRun.entries()].map(([runId, rows]): VersionGroup => {
      const exports = rows.map(
        (artifact): VersionExport => ({
          artifact,
          seat: seatOf(artifact),
          sourceLabel:
            launched.has(runId)
              ? candidateSourceLabel(runId)
              : canonicalSourceLabel(artifact),
        }),
      );
      if (runId === projection.referenceRun.runId) {
        return {
          runId,
          label: "Reference",
          // The run's base, not the published position: a reference run can
          // stand on an issue the project has already left.
          title: `based on issue ${projection.referenceRun.baseVersion}`,
          detail: null,
          exports,
        };
      }
      const proposalId = launched.get(runId);
      if (proposalId !== undefined) {
        const validation = validations[runId];
        return {
          runId,
          label: "Candidate",
          title: (proposalId === null ? t("program.title") : utteranceOf.get(proposalId)) ?? runId,
          detail: validation
            ? validation.reviewReady
              ? "ready for review"
              : `blocked: ${validation.blockedBy.join(", ")}`
            : "verdict not read yet",
          exports,
        };
      }
      return {
        runId,
        label: "Run",
        title: runId.slice(-13),
        detail: "not launched from this tab",
        exports,
      };
    });
    const rank = { Reference: 0, Candidate: 1, Run: 2 } as const;
    const launchedOrder = candidateEntries.map((entry) => entry.candidateId);
    return groups.sort((a, b) => {
      if (rank[a.label] !== rank[b.label]) return rank[a.label] - rank[b.label];
      if (a.label === "Candidate") {
        return launchedOrder.indexOf(b.runId) - launchedOrder.indexOf(a.runId);
      }
      return b.runId.localeCompare(a.runId);
    });
  }, [artifacts, candidateEntries, projection, t, utteranceOf, validations]);

  // An agent can resolve the subject from the architect's words and the record.
  // A pick or circle supplies context, but is not a prerequisite for speaking.
  const hasSubject =
    documentContinuation !== null || selection !== null || gestures.some((gesture) => gesture.kind === "circle") ||
    project?.intentProvider === "codex" || project?.intentProvider === "anthropic";
  const disabledReason =
    changingBase
      ? t("stage.base.loading")
      : viewingAnotherBase && documentContinuation === null
        ? t("stage.base.viewOnly")
      : modelLoading
        ? t("stage.base.loading")
      : session.status === "failed"
      ? t("shell.bindingRefused", { code: session.error.code })
      : projection === null
        ? t("shell.readingProjection")
        : projection.stateDigest === null
          ? t("shell.boundViewRefused")
          : !hasSubject
            ? t("shell.pickFirst")
            : null;
  // CURRENT / GHOST PREVIEW / VALIDATED — what the picture is, with the
  // server's word as its detail. A candidate's export is VALIDATED only once
  // its verdict was read; before that it is a candidate export, and says so.
  const loadedValidation =
    loadedArtifact && candidates[loadedArtifact.runId]
      ? (validations[loadedArtifact.runId] ?? null)
      : null;
  const view: ViewState | null =
    sourceLabel === null
      ? null
      : ghostProposalId !== null
        ? { state: "ghost", label: "Ghost preview", detail: "approximate" }
        : loadedArtifact && candidates[loadedArtifact.runId]
          ? loadedValidation
            ? loadedValidation.reviewReady
              ? {
                  state: "validated",
                  label: "Review-ready",
                  detail: "ready for review",
                }
              : {
                  // The strongest word on the picture is the verdict's, in the
                  // verdict's colour: a blocked candidate is checked, not validated.
                  state: "blocked",
                  label: "Checked",
                  detail: `blocked: ${loadedValidation.blockedBy.join(", ")}`,
                }
            : { state: "current", label: "Candidate export", detail: "verdict not read yet" }
          : { state: "current", label: "Current", detail: null };

  // The sentence a candidate was made from, as this tab heard it: for the
  // drawer's title and for naming a queued candidate's blocker by its words.
  const sentenceOfCandidate = useCallback(
    (candidateId: string | null): string | null => {
      if (candidateId === null) return null;
      const entry = candidateEntries.find(
        (row) => row.kind === "candidate" && row.candidateId === candidateId,
      );
      return entry && entry.kind === "candidate"
        ? (entry.proposalId === null ? t("program.title") : utteranceOf.get(entry.proposalId) ?? null)
        : null;
    },
    [candidateEntries, t, utteranceOf],
  );
  const selectedSentence = sentenceOfCandidate(selectedCandidateId);

  const evidenceCounts = {
    honesty: honestyCount(projection, selectedCandidate, selectedValidation),
    receipts: candidateEntries.length,
    events: eventLines.length,
  };
  // The review summary on the evidence tab, in the reviewer's words: every
  // candidate this tab launched is a change; a change is checked once its
  // verdict was read; it needs review when the verdict refused or the run
  // failed. Nothing here is decided — each number is a count of server words.
  const review = candidateEntries.reduce(
    (sum, entry) => {
      const verdict = validations[entry.candidateId];
      if (verdict) {
        return {
          ...sum,
          checked: sum.checked + 1,
          needsReview: sum.needsReview + (verdict.reviewReady ? 0 : 1),
        };
      }
      return {
        ...sum,
        needsReview: sum.needsReview + (entry.status === "failed" ? 1 : 0),
      };
    },
    { changes: candidateEntries.length, checked: 0, needsReview: 0 },
  );

  const drawer = developerMode ? (
    <EvidenceDrawer
      open={evidenceOpen}
      pinned={evidencePinned}
      tab={evidenceTab}
      counts={evidenceCounts}
      server={server}
      projection={projection}
      candidate={selectedCandidate}
      validation={selectedValidation}
      sentence={selectedSentence}
      notices={[]}
      eventLines={eventLines}
      onTab={setEvidenceTab}
      onClose={() => setEvidenceOpen(false)}
      onPin={pinEvidence}
    />
  ) : null;

  // The tab is still starting up until the API has answered for the binding. This carries
  // the launcher's exact-status convention into the browser and ends when the shell has a
  // project to name.
  // Registered source pages need a project binding, not a restored 3D editing base.
  const canOpenDocuments = documentView.open && binding !== null &&
    !(session.status === "failed" && session.error.code === "EDITING_PROJECT_CHANGED");
  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");

  if (!viewportOpened.current) return null;

  if ((session.status === "failed" || missingChosenModel) && !canOpenDocuments) {
    const error = session.status === "failed" ? session.error
      : artifacts.status === "failed" ? artifacts.error : baseError;
    return (
      <div className="refusal">
        <div className="refusal__card">
          <p className="label">MonkeyArch</p>
          <h1 className="refusal__title">{t("stage.base.restoreFailed")}</h1>
          <p className="refusal__lead">
            {missingChosenModel ? t("stage.base.modelUnavailable") : t("stage.base.restoreHelp")}
          </p>
          {initialDocumentIntent && (documentIntentStatus !== "done" || draft === initialDocumentIntent.utterance) && <p>{initialDocumentIntent.utterance}</p>}
          {developerMode && sourceRunId !== null && <p className="mono">{sourceRunId}</p>}
          {error && <ErrorPanel error={error} />}
          <button type="button" className="btn" disabled={changingBase}
            onClick={() => { if (missingChosenModel) void loadArtifacts(); else void reload(); }}>
            {t("stage.base.retry")}
          </button>
          <button type="button" className="btn" disabled={changingBase}
            onClick={() => void changeEditingBase(null)}>
            {t("stage.base.default")}
          </button>
          {binding && documentView.mounted && <button type="button" className="btn"
            onClick={() => setDocumentView((current) => ({ ...current, open: true }))}>
            {t("workspace.monkeydiagram")}
          </button>}
          {returnToBoard && <button type="button" className="btn" onClick={returnToBoard}>
            {t("workspace.monkeyboard")}
          </button>}
        </div>
      </div>
    );
  }

  return (
    <>
      {booting && (
        <LoadingOverlay
          mode="boot"
          status={
            session.status === "idle"
              ? t("loading.startingSession")
              : (
                  <>
                    {t("loading.readingBinding")} ·{" "}
                    <code lang="en">GET /api/project</code>
                  </>
                )
          }
        />
      )}
      <input
        ref={fileInputRef}
        className="visually-hidden"
        type="file"
        accept=".3dm"
        onChange={(event) => {
          const file = event.target.files?.item(0);
          if (file) openLocalFile(file);
          event.target.value = "";
        }}
      />
      <AppShell
        conversation={conversationOpen ? (
          <Conversation
            onClose={() => setConversationOpen(false)}
            entries={transcript.entries}
            candidateRuns={candidateRuns.runs}
            sessionError={null}
            projection={projection}
            currentStateDigest={stateDigest}
            selection={selection}
            disabledReason={disabledReason}
            busy={proposalBusy}
            runBusy={candidateBusy || changingBase}
            loadingSha={artifactLoadingSha}
            ghostProposalId={ghostProposalId}
            refiningEntryId={refiningEntryId}
            gestures={gestures}
            onRemoveGesture={(index) =>
              editGestures((current) => current.filter((_, i) => i !== index))
            }
            intentProvider={project?.intentProvider ?? null}
            draft={draft}
            onDraft={setDraft}
            onSubmit={(utterance) => void propose(utterance)}
            onSelect={(componentId, elementId) => {
              selectSemanticTarget(componentId, elementId);
              append({
                kind: "system",
                ...systemText([
                  { kind: "prose", text: "Talking about " },
                  { kind: "technical", text: elementId ?? componentId },
                  { kind: "prose", text: " · chosen from the record" },
                ]),
              });
            }}
            callbacks={{
              onRun: (proposalId) => void runCandidate(proposalId),
              // An accepted form is a shape to type a number into, so it goes
              // into the composer; sending it verbatim would earn the same
              // question back. A candidate is an answer, so it is sent — see
              // onChoose, which moves the selection with it.
              onReply: setDraft,
              onChoose: chooseCandidate,
              onAdjust: (sentence) => {
                // Adjust puts the sentence in the composer to edit, never the
                // internal component-edit payload.
                // Text already there is not lost silently, and the ghost of
                // the proposal being adjusted comes off the model.
                if (draft.trim() !== "" && draft !== sentence) {
                  append({
                    kind: "system",
                    ...systemText([
                      { kind: "prose", text: "the unsent text “" },
                      { kind: "user", text: draft },
                      {
                        kind: "prose",
                        text: "” was replaced by the proposal's sentence",
                      },
                    ]),
                  });
                }
                setDraft(sentence);
                viewportRef.current?.ghost(null);
                setGhostProposalId(null);
              },
              onRefine: refine,
              onCompareInModel: (comparison) => void compareInModel(comparison),
              labelOf: sentenceOfCandidate,
              onRetryCandidate: candidateRuns.refresh,
              onPreview: (artifact, label) => {
                manualLoadRef.current = true;
                void loadArtifactIntoViewer(artifact, label);
              },
              onEvidence: openEvidence,
            }}
          />
        ) : null}
        stage={
          <Stage
            key={binding?.projectId ?? "unbound"}
            active={active}
            onOpenBoard={onOpenBoard}
            onChatRequest={onChatRequest}
            hasModel={sourceLabel !== null || hasLocalGeometry}
            onSketch={runSketch}
            parameterLocks={{ parameters: projection?.parameters ?? [], contextKey, busy: parameterLockBusy || modelRunPending !== null,
              disabledReason: parameterLockReason, error: parameterLockError, onApply: (keys, action) => void applyParameterLocks(keys, action) }}
            sketchBusy={modelNavigationBusy}
            snapPoints={sketchSnapPoints}
            model={{
              onDelete: () => void deleteSelected(),
              canDelete: canDeleteModel,
              deleting: false,
              subject: deletableElementId,
              onUndo: () => void undoModel(),
              canUndo: canUndoModel,
              onRedo: () => void redoModel(),
              canRedo: canRedoModel,
              onClearSelection: clearModelSelection,
              hasSelection: selection !== null || picked !== null,
              onTool: chooseDirectTool,
              directTool, busy: modelNavigationBusy, error: directError,
              interactionBlocked: modelNavigationBusy,
              onInteraction: () => { modelInteractionEpoch.current += 1; },
              sync: { dirty: !!localModel && unsynced(localModel),
                busy: localModel?.busy ?? false, error: localModel?.error ?? null, onSync: () => void syncLocalModel() },
              pushPullTarget, pushPullReason: pickedShape?.drawnShapeReason,
              onApply: (action) => void applyDirectModelAction(action),
              elevation: elevationObject && draftKey ? { object: elevationObject, objects: elevationObjects,
                levels: draftSnapshot?.levels ?? draftProjection?.levels ?? [], onApply: command => {
                  if (modelNavigationBusy) return false;
                  try { commitLocalCommand(command); setDirectError(null); return true; }
                  catch (cause) { setDirectError(asStudioApiError(cause).detail); return false; }
                } } : null,
            }}
            viewportRef={viewportRef}
            message={artifactLoadPhase === "download" ? t("candidate.loadingBytes") : modelRunPending !== null ? t("stage.sketch.busy") : viewerMessage}
            status={artifactLoadingSha !== null ? "loading" : viewerStatus}
            artifactError={artifactError}
            tool={tool}
            gestures={gestures}
            onTool={setTool}
            onGesture={(gesture) => editGestures((current) => [...current, { ...gesture, id: crypto.randomUUID() }])}
            onUndoGesture={modelAnnotations.undo}
            onRedoGesture={modelAnnotations.redo}
            canUndoGesture={modelAnnotations.canUndo}
            canRedoGesture={modelAnnotations.canRedo}
            modelAnnotations={persistentAnnotations ? modelAnnotations : null}
            annotationsReady={modelAnnotations.ready && !modelLoading &&
              (loadedArtifacts.length > 0 || sourceLabel === LOCAL_SOURCE_LABEL)}
            tracingPaperReview={{ enabled: persistentAnnotations && modelAnnotations.ready && gestures.length > 0 && !modelLoading && loadedModelSource !== null,
              busy: tracingPaperSend.busy, sent: tracingPaperSend.sent, error: tracingPaperSend.error,
              onSend: () => void sendTracingPaperToBoard() }}
            onEraseGestures={(indices) => editGestures((current) => current.filter((_, index) => !indices.includes(index)))}
            documentProjectId={binding?.projectId ?? null}
            documentView={documentView}
            onReturnToBoard={returnToBoard}
            documentTiming={drawingDisplayTiming ?? undefined}
            drawing={server.capabilities.includes("drawing-elevations") ? { busy: drawingBusy, error: drawingError, available: loadedModelSource !== null && !modelLoading && !changingBase,
              dismissError: () => setDrawingError(null), generate: (view) => { void generateElevation(view); } } : undefined}
            documentAnnotationsController={documentController}
            onDocumentBeforeLeave={bindDocumentSave}
            onDocumentView={(next) => {
              void (async () => {
                try { await documentSaveRef.current?.(); setDocumentView(next); }
                catch (cause) { setHistoryError(asStudioApiError(cause).detail); }
              })();
            }}
            documentModelSources={modelSources}
            viewedModelSource={loadedModelSource}
            editingModelSource={editingModelSource}
            onContinueModelSource={async (source) => {
              const next = await changeEditingBase(source.runId, source, undefined, undefined, true);
              if (next === null) throw asStudioApiError(new Error("The editing base could not be changed. Retry after resolving the reported error."));
            }}
            documentVisualInputAvailable={server.capabilities.includes("document-visual-input")}
            onDocumentSubmit={(utterance, refs, source, visuals) => {
              setConversationOpen(true);
              return propose(utterance, undefined, refs, source, visuals);
            }}
            picked={picked}
            versions={versions}
            hasNewVersions={hasNewVersions}
            onVersionsOpen={() => {
              setHasNewVersions(false);
              setVersionRefreshRequest((current) => current + 1);
            }}
            workingCopies={workingCopies}
            designHistory={designHistoryEnabled ? {
              history: designHistory, currentStageRef: projection?.sourceStageRef ?? null,
              acceptedModelSources,
              currentModelSource: loadedModelSource,
              candidates: retainedCandidates,
              busy: historyBusy || changingBase || selectingWorkingCopy || modelLoading || proposalBusy || candidateBusy,
              error: historyError,
              onInitialize: () => { if (project && loadedModelSource) void updateDesignHistory(() => studio.initializeStage({ projectId: project.projectId, modelSource: loadedModelSource, branchId: "main", label: "S0" })); },
              onStage: (stage) => { void openDesignStage(stage); },
              onBranch: (branchId) => { void selectDesignBranch(branchId); },
              onCandidate: (source) => { void changeEditingBase(source.runId, source); },
              onAccept: (candidateId) => {
                const branch = designHistory?.branches.find((item) => item.branchId === designHistory.branchId);
                if (project && branch) void updateDesignHistory(async () => {
                  const timing = monitorDiagnostics ? startClientTiming(studio, "stage_wait", {
                    projectId: project.projectId, runId: candidateId, sourceRef: branch.headStageRef,
                  }) : null;
                  try {
                    const stage = await studio.acceptCandidate(candidateId, {
                      projectId: project.projectId, branchId: branch.branchId, expectedHeadStageRef: branch.headStageRef,
                    }, timing?.trace);
                    timing?.finish("succeeded"); return stage;
                  } catch (cause) { timing?.finish("failed"); throw cause; }
                });
              },
              onFork: (stage, name) => { void forkDesignBranch(stage, name); },
              onCombine: (ids) => { void combineDesignCandidates(ids); },
            } : undefined}
            onOpenWorkingOption={openWorkingOption}
            loadingSha={artifactLoadingSha}
            loadedShas={loadedShas}
            editingBaseRunId={projection?.referenceRunSource === "none" ? null : projection?.referenceRun.runId ?? null}
            editingBaseLabel={modelSources.find((row) => sameModelSource(row.modelSource, editingModelSource))?.label ??
              sentenceOfCandidate(projection?.referenceRun.runId ?? null)}
            explicitBase={sourceRunId !== null}
            changingBase={changingBase || selectingWorkingCopy}
            baseError={baseError}
            baseActionBusy={session.status !== "ready" || missingChosenModel || proposalBusy || candidateBusy || refiningEntryId !== null || selectingWorkingCopy}
            onContinue={(runId) => void changeEditingBase(runId)}
            onDefaultBase={() => void changeEditingBase(null)}
            evidenceCounts={evidenceCounts}
            review={review}
            drawer={developerMode && evidencePinned ? null : drawer}
            onInspection={setInspection}
            onStatus={(status, message) => {
              viewerStatusRef.current = status;
              setViewerStatus(status);
              setViewerMessage(message);
            }}
            onRequestFile={() => fileInputRef.current?.click()}
            onOpenFile={openLocalFile}
            onSource={noteSource}
            onPick={(pick) => pick === null ? clearModelSelection() : void resolvePick(pick)}
            onOpenVersion={(artifact, label) => {
              manualLoadRef.current = true;
              void loadArtifactIntoViewer(artifact, label);
            }}
            onOpenRun={(group) => {
              // The Reference card's `show run` is the toolbar's button by
              // another name whenever home is the reference run's exports;
              // one act, one sentence about it.
              if (
                homeArtifacts !== null &&
                homeArtifacts.kind === "reference" &&
                group.runId === homeArtifacts.runId
              ) {
                showHome(true);
                return;
              }
              const rows = group.exports
                .map((item) => item.artifact)
                .filter((artifact) => artifact.available && artifact.sha256 !== null);
              if (rows.length === 0) return;
              manualLoadRef.current = true;
              append({
                kind: "system",
                ...systemText([
                  {
                    kind: "prose",
                    text: rows.length === 1 ? "Showing " : "Showing every seat of ",
                  },
                  { kind: "technical", text: group.runId },
                  { kind: "prose", text: " · " },
                  {
                    kind: "technical",
                    text:
                      rows.length === 1
                        ? rows[0].fileName
                        : rows.map(seatOf).join(" + "),
                  },
                ]),
              });
              void loadRunIntoViewer(rows, runSourceLabel(group.runId, rows));
            }}
            onShowHome={() => { showHome(true); }}
            home={homeArtifacts}
            loadedRunId={loadedArtifact?.runId ?? null}
            onCompareVersion={(artifact) => void compareVersions(artifact)}
            blend={blendState}
            onBlend={(t) => {
              viewportRef.current?.blend(t);
              setBlendState((current) => (current ? { ...current, t } : current));
            }}
            onEndBlend={clearComparison}
            captureState={captureState}
            capturePath={capturePath}
            onCapture={captureViewport}
            workModel={workModelControls}
            onEvidence={openEvidence}
          />
        }
        pinnedDrawer={developerMode && evidencePinned ? drawer : null}
      />

    </>
  );
}
