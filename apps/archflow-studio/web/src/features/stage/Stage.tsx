/**
 * The model, and the few facts that sit over it.
 *
 * The viewport is the moved viewer, untouched. Around it: the camera tools, the last resolved pick, the
 * versions strip and the evidence tab. Everything here was handed in; the
 * stage decides nothing.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject, type PointerEvent as ReactPointerEvent } from "react";

import type { StudioApiError } from "../../api/client";
import type { DocumentAnnotationRefDto, DocumentVisualInputDto, ElevationRequestDto, GestureDto, ModelSourceDto, ProjectArtifactDto, WorkingCopyDto, WorkingCopyOptionDto } from "../../api/generated";
import { ErrorBoundary } from "../../app/ErrorBoundary";
import { ErrorPanel } from "../../app/ErrorPanel";
import { designObjectLabel } from "../../app/format";
import type { EvidenceTab } from "../../app/evidence";
import { LoadingOverlay } from "../../app/LoadingOverlay";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import type { SceneInspection } from "../../workspaces/monkeyarch/viewer/sceneInspection";
import type { ModelDisplayMode } from "../../workspaces/monkeyarch/viewer/modelDisplay";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
  type Vec3,
} from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import { Annotate, GESTURE_TOOLS, type AnnotationStyle, type GestureTool } from "../../workspaces/monkeyarch/Annotate";
import { VersionsStrip, type VersionGroup, type DesignHistoryControls } from "./VersionsStrip";
import { DocumentCanvas, type DocumentViewContext } from "../../workspaces/monkeydiagram/DocumentCanvas";
import type { ClientTimingSpan } from "../../app/clientTiming";
import { createDocumentAnnotationsController } from "../../workspaces/monkeydiagram/useDocumentAnnotations";
import type { ModelAnnotationsHandle } from "../../workspaces/monkeyarch/useModelAnnotations";
import { hostOrigin, requestStartModeling } from "../../../../../shared-web/src/hostBridge.js";
import { distanceBetween } from "../../workspaces/monkeyarch/viewer/featureEdges";
import {
  IDLE as SKETCH_IDLE,
  cancelled as cancelledSketch,
  circleOf,
  enclosesArea,
  finished as finishedSketch,
  heightAnchor,
  lockedPoint,
  pointFromPlane,
  pointToPlane,
  rectangleOf,
  sizedRectangle,
  snapPoint,
  typedNumber,
  typedDimensions,
  WORK_PLANES,
  type FinishedSketch,
  type PlanPoint,
  type SketchState,
  type SketchPlane,
  type SketchTool,
} from "./sketch";

export interface PickedFacts {
  readonly componentId: string | null;
  readonly elementId: string | null;
  readonly status: string;
  readonly sourceState: string;
  /** The element's numeric fields as the projection carries them, unit-less. */
  readonly fields: ReadonlyArray<readonly [string, number]>;
}

/**
 * The picture the stage opens on and the one thing that brings it back, as
 * far as the toolbar needs to know it: whether those bytes are the reference
 * run's own exports or the export the auto-load fell back to, and the run
 * they came from. Null when this project has no export anywhere.
 */
export interface HomeModel {
  readonly kind: "reference" | "fallback";
  readonly runId: string;
}

export interface EvidenceCounts {
  readonly honesty: number;
  readonly receipts: number;
  readonly events: number;
}

/** The evidence tab's own sentence, in the reviewer's words. */
export interface ReviewSummary {
  readonly changes: number;
  readonly checked: number;
  readonly needsReview: number;
}

export type CaptureState = "idle" | "busy" | "success" | "error";

export function Stage({
  embedded = false,
  viewportRef,
  message,
  status,
  artifactError,
  picked,
  versions,
  hasNewVersions = false,
  onVersionsOpen,
  workingCopies,
  onOpenWorkingOption,
  designHistory,
  documentView,
  documentTiming,
  onDocumentView,
  documentAnnotationsController,
  onDocumentBeforeLeave,
  drawing,
  loadingSha,
  loadedShas,
  evidenceCounts,
  review,
  drawer,
  framePanel,
  optionsPanel,
  displayMode,
  onDisplayMode,
  programPanel,
  programOpen,
  onToggleProgram,
  tool,
  gestures,
  onTool,
  onGesture,
  onUndoGesture,
  onRedoGesture,
  canUndoGesture,
  canRedoGesture,
  onEraseGestures,
  modelAnnotations,
  annotationsReady,
  documentProjectId,
  documentModelSources,
  editingModelSource,
  viewedModelSource,
  onContinueModelSource,
  onDocumentSubmit,
  documentVisualInputAvailable,
  onInspection,
  onStatus,
  onRequestFile,
  onOpenFile,
  onSource,
  onPick,
  onOpenVersion,
  onOpenRun,
  onShowHome,
  home,
  hasModel,
  onSketch,
  sketchBusy = false,
  snapPoints = [],
  model,
  loadedRunId,
  editingBaseRunId,
  editingBaseLabel,
  explicitBase,
  changingBase,
  baseError,
  baseActionBusy,
  onContinue,
  onDefaultBase,
  onCompareVersion,
  blend,
  onBlend,
  onEndBlend,
  captureState,
  capturePath,
  onCapture,
  workModel,
  onEvidence,
}: {
  viewportRef: RefObject<ViewportController | null>;
  message: string;
  status: ViewportStatus;
  artifactError: StudioApiError | null;
  picked: PickedFacts | null;
  versions: readonly VersionGroup[];
  hasNewVersions?: boolean;
  onVersionsOpen?(): void;
  workingCopies: readonly WorkingCopyDto[];
  onOpenWorkingOption(option: WorkingCopyOptionDto): void;
  designHistory?: DesignHistoryControls;
  documentView: DocumentViewContext;
  documentTiming?: ClientTimingSpan;
  onDocumentView(next: DocumentViewContext): void;
  documentAnnotationsController: ReturnType<typeof createDocumentAnnotationsController>;
  onDocumentBeforeLeave(save: (() => Promise<void>) | null): void;
  drawing?: { busy: boolean; available: boolean; error: StudioApiError | null; dismissError(): void; generate(view: ElevationRequestDto["view"]): void };
  loadingSha: string | null;
  /** The digests on screen: one seat's, or every seat of a run. */
  loadedShas: readonly string[];
  evidenceCounts: EvidenceCounts;
  review: ReviewSummary;
  /** The drawer, when it overlays the stage rather than standing beside it. */
  drawer: ReactNode;
  /** The frame panel, mounted over the stage while the toolbar button is on. */
  framePanel: ReactNode;
  optionsPanel: ReactNode;
  /** The one global projection shown over the original model. */
  displayMode: ModelDisplayMode;
  onDisplayMode(mode: ModelDisplayMode): void;
  /** The program sheet, mounted the same way and beside it. */
  programPanel: ReactNode;
  programOpen: boolean;
  onToggleProgram(): void;
  /** The armed drawing tool; null is the orbit. */
  tool: GestureTool | null;
  /** The marks made on this picture, not yet sent with a sentence. */
  gestures: readonly GestureDto[];
  onTool(tool: GestureTool | null): void;
  onGesture(gesture: GestureDto): void;
  onUndoGesture(): void;
  onRedoGesture(): void;
  canUndoGesture: boolean;
  canRedoGesture: boolean;
  onEraseGestures(indices: readonly number[]): void;
  modelAnnotations: ModelAnnotationsHandle | null;
  annotationsReady: boolean;
  documentProjectId: string | null;
  documentModelSources: readonly { label: string; modelSource: ModelSourceDto }[];
  editingModelSource: ModelSourceDto | null;
  viewedModelSource: ModelSourceDto | null;
  onContinueModelSource(source: ModelSourceDto): Promise<void>;
  onDocumentSubmit(utterance: string, refs: readonly DocumentAnnotationRefDto[], modelSource: ModelSourceDto, visuals: DocumentVisualInputDto[]): Promise<void>;
  documentVisualInputAvailable: boolean;
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  onOpenFile(file: File): void;
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick | null): void;
  onOpenVersion(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Put every available export of one run on the stage at once. */
  onOpenRun(group: VersionGroup): void;
  /** Back home, from wherever the stage got to. */
  onShowHome(): void;
  /**
   * What home is, so the button can say which model it brings back: the
   * reference run's exports, or the fallback the auto-load announced. Null
   * only when nothing in this project can be shown.
   */
  home: HomeModel | null;
  /** The run whose export is on screen, for the strip's comparisons. */
  loadedRunId: string | null;
  editingBaseRunId: string | null;
  editingBaseLabel: string | null;
  explicitBase: boolean;
  changingBase: boolean;
  baseError: StudioApiError | null;
  baseActionBusy: boolean;
  onContinue(runId: string): void;
  onDefaultBase(): void;
  onCompareVersion(artifact: ProjectArtifactDto): void;
  /** A cross-fade in progress: before is the loaded run, after the candidate. */
  blend: { candidateId: string; against: string; t: number; meshes: number } | null;
  onBlend(t: number): void;
  onEndBlend(): void;
  captureState: CaptureState;
  /** Relative project path returned after the server stores the PNG. */
  capturePath: string | null;
  onCapture(): Promise<void>;
  /**
   * The editable copy of the model on screen: what it would be made from, what
   * has already been made, and how it last went. ``source`` is the exact STEP
   * of the very delivery being viewed, or a word saying why there is not
   * exactly one - the export never picks a seat on the architect's behalf.
   */
  workModel?: {
    source: ProjectArtifactDto | null;
    refusal: "nothing-loaded" | "not-an-export" | "several-seats" | "busy-elsewhere" | null;
    exported: ProjectArtifactDto | null;
    busy: boolean;
    error: string | null;
    onExport(): void;
  };
  onEvidence(tab: EvidenceTab): void;
  /** Whether anything is on screen at all, as the viewer reported its source. */
  hasModel: boolean;
  /**
   * Submit one finished drawing action. Called once per completed action and
   * never while the pointer is moving; the preview above is local.
   */
  onSketch?(action: FinishedSketch): Promise<void>;
  /** True while a drawn action is on its way, so a second one cannot start. */
  sketchBusy?: boolean;
  /** Existing plan points a drawing may snap to, in CAD world (x, y). */
  snapPoints?: readonly PlanPoint[];
  /**
   * The keys that act on the model itself, and what they may do right now.
   *
   * These are the model's own actions, not the ink's: the annotation undo
   * beside them takes back a stroke on the picture and never a change to the
   * building. The shell decides what each one means and whether it is
   * available; this component only reads the keyboard.
   */
  model?: {
    onDelete(): void;
    canDelete: boolean;
    deleting: boolean;
    /** What Delete would remove, so the hint can name it rather than imply it. */
    subject: string | null;
    onUndo(): void;
    canUndo: boolean;
    onRedo(): void;
    canRedo: boolean;
    onClearSelection(): void;
    hasSelection: boolean;
    onTool?(tool: "select" | "pushPull" | "move" | "rotate" | "scale" | "copy"): void;
    toolPanel?: ReactNode;
  };
  /** A host page already shows the workspace entries and the project's position. */
  embedded?: boolean;
}) {
  const t = useT();
  const { developerMode } = usePreferences();
  const [annotationStyle, setAnnotationStyle] = useState<AnnotationStyle>({ color: "#e5534b", lineWidth: 2 });
  const [elevationView, setElevationView] = useState<NonNullable<ElevationRequestDto["view"]>>("front");
  const [annotationCancel, setAnnotationCancel] = useState(0);
  const documentOpen = documentView.open;
  useEffect(() => { if (!documentOpen) documentTiming?.finish("cancelled"); }, [documentOpen, documentTiming]);
  const documentMounted = documentView.mounted;
  const documentRunId = documentView.runId ?? editingBaseRunId;
  const [eraser, setEraser] = useState(false);
  const [annotationToolsOpen, setAnnotationToolsOpen] = useState(false);
  const [viewToolsOpen, setViewToolsOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  // One drawing action at a time, entirely local until it is finished.
  const [sketch, setSketch] = useState<SketchState>(SKETCH_IDLE);
  const [workPlaneName, setWorkPlaneName] = useState<"xy" | "xz" | "yz" | "face">("xy");
  const [snapNote, setSnapNote] = useState<string | null>(null);
  // A measurement is looking, not changing: two points off the model itself,
  // the distance between them, and nothing retained anywhere.
  const [measuring, setMeasuring] = useState(false);
  const [measure, setMeasure] = useState<{ from: Vec3 | null; to: Vec3 | null; kinds: [string | null, string | null] }>(
    { from: null, to: null, kinds: [null, null] },
  );
  const measureRef = useRef(measure);
  measureRef.current = measure;
  const showMeasure = useCallback((next: typeof measure) => {
    setMeasure(next);
    // The same temporary layer the drawing preview uses; it draws the line and
    // is thrown away with it.
    if (!next.from || !next.to) { viewportRef.current?.sketchPreview(null); return; }
    const delta = next.to.map((value, index) => value - next.from![index]!) as Vec3;
    const length = Math.hypot(...delta);
    if (length <= 1e-9) { viewportRef.current?.sketchPreview(null); return; }
    const xAxis = delta.map((value) => value / length) as Vec3;
    // Put the line in its own plane so endpoints at different elevations
    // remain exactly where they were picked, rather than flattening to XY.
    const reference: Vec3 = Math.abs(xAxis[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
    const cross: Vec3 = [xAxis[1] * reference[2] - xAxis[2] * reference[1],
      xAxis[2] * reference[0] - xAxis[0] * reference[2], xAxis[0] * reference[1] - xAxis[1] * reference[0]];
    const normal = cross.map((value) => value / Math.hypot(...cross)) as Vec3;
    const yAxis: Vec3 = [normal[1] * xAxis[2] - normal[2] * xAxis[1],
      normal[2] * xAxis[0] - normal[0] * xAxis[2], normal[0] * xAxis[1] - normal[1] * xAxis[0]];
    viewportRef.current?.sketchPreview({ profile: [[0, 0], [length, 0]], base: 0, height: 0,
      plane: { origin: next.from, xAxis, yAxis, normal } });
  }, [viewportRef]);
  const stopMeasuring = useCallback(() => {
    showMeasure({ from: null, to: null, kinds: [null, null] });
    setSnapNote(null);
  }, [showMeasure]);
  const sketchRef = useRef<SketchState>(SKETCH_IDLE);
  sketchRef.current = sketch;
  // How far a snap reaches, in world units: a fifth of what the last drawn
  // rectangle spans, so it stays usable at any size the project is drawn at.
  const snapRadius = useCallback(() => {
    const spans = sketch.profile.length > 1
      ? Math.max(...sketch.profile.map(([x]) => x)) - Math.min(...sketch.profile.map(([x]) => x))
      : 0;
    return Math.max(0.2, spans / 5);
  }, [sketch.profile]);
  const showSketch = useCallback((next: SketchState) => {
    sketchRef.current = next;
    setSketch(next);
    viewportRef.current?.sketchPreview(
      next.profile.length > 1 ? { profile: next.profile, base: next.base, height: next.height, ...(next.plane ? { plane: next.plane } : {}) } : null,
    );
  }, [viewportRef]);
  const stopSketching = useCallback(() => {
    setSnapNote(null);
    showSketch(cancelledSketch(sketchRef.current));
  }, [showSketch]);
  const submitSketch = useCallback((allowFlat = false) => {
    const action = finishedSketch(sketchRef.current, allowFlat);
    if (action === null || !onSketch) return;
    stopSketching();
    void onSketch(action);
  }, [onSketch, stopSketching]);
  const chooseDrawingTool = useCallback((next: SketchTool | null) => {
    if (sketchBusy) return;
    setAnnotationToolsOpen(false); setViewToolsOpen(false); setVersionsOpen(false);
    onTool(null); setEraser(false); setMeasuring(false); stopMeasuring();
    model?.onTool?.("select");
    showSketch({ ...cancelledSketch(sketchRef.current), tool: next });
  }, [onTool, model?.onTool, showSketch, sketchBusy, stopMeasuring]);
  const chooseMeasure = useCallback(() => {
    chooseDrawingTool(null);
    setMeasuring(true);
  }, [chooseDrawingTool]);
  const choosePlane = useCallback((name: "xy" | "xz" | "yz" | "face") => {
    const plane: SketchPlane | null = name === "face"
      ? viewportRef.current?.workPlaneFromSelection() ?? null : WORK_PLANES[name];
    if (!plane) return;
    setWorkPlaneName(name); setSnapNote(null);
    showSketch({ ...cancelledSketch(sketchRef.current), plane: name === "xy" ? null : plane, base: 0 });
  }, [showSketch, viewportRef]);
  const closePolygon = useCallback(() => {
    const current = sketchRef.current;
    if (current.tool !== "polygon" || current.phase !== "profile" || !enclosesArea(current.vertices)) return;
    showSketch({ ...current, phase: "height", profile: current.vertices, cursor: current.vertices.at(-1) ?? current.anchor, typed: "" });
  }, [showSketch]);
  // Esc belongs to the whole action, wherever the focus is.
  useEffect(() => {
    if (!measuring) return;
    const listen = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      // Esc ends the measurement. Nothing was changed to undo.
      if (measureRef.current.from === null) setMeasuring(false);
      stopMeasuring();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [measuring, stopMeasuring]);
  useEffect(() => {
    if (sketch.tool === null) return;
    const listen = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (sketchRef.current.phase === "idle") showSketch({ ...cancelledSketch(sketchRef.current), tool: null });
      else stopSketching();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [sketch.tool, showSketch, stopSketching]);
  useEffect(() => () => { viewportRef.current?.sketchPreview(null); }, [viewportRef]);

  // The keys an architect already has in their fingers, and who owns each one.
  //
  // There is one listener, because one press must reach one owner. Ownership is
  // decided in order, and the first owner that is *currently doing something*
  // takes the key:
  //
  //   1. text being written keeps its own editing keys;
  //   2. the drawings workspace, while it is open, owns its own shortcuts, and
  //      this view touches neither the model nor the ink behind it;
  //   3. an action in progress - a rectangle being placed or pulled, a
  //      measurement half taken - owns Esc, and nothing may delete or step the
  //      model out from under it. An *armed* tool with nothing in progress owns
  //      nothing: after a rectangle is committed the tool stays armed for the
  //      next one, and Ctrl+Z there has to undo the volume just made;
  //   4. the ink owns undo, redo and Delete while it is what is being worked
  //      on - an annotation tool armed, or the eraser - through the same
  //      handlers its own buttons call. Having strokes to take back does not
  //      make it the owner: a mark drawn earlier must not swallow the Ctrl+Z
  //      that belongs to the volume just made. Its buttons stay for that;
  //   5. otherwise the model: its own history, its own Delete, its own Esc.
  const modelKeysRef = useRef(model);
  modelKeysRef.current = model;
  const modelKeysAvailable = model !== undefined;
  // What is *in progress*, which is not the same as what is armed.
  const actionInProgressRef = useRef(false);
  const actionInProgress = sketch.phase !== "idle" || (measuring && measure.from !== null);
  actionInProgressRef.current = actionInProgress;
  const inkRef = useRef({
    undo: onUndoGesture, redo: onRedoGesture, canUndo: false, canRedo: false, busy: false,
  });
  inkRef.current = {
    undo: onUndoGesture,
    redo: onRedoGesture,
    canUndo: canUndoGesture,
    canRedo: canRedoGesture,
    // What makes the ink the owner is that it is the mode in hand: a tool
    // armed, or the eraser. Not that strokes exist — those are undone from the
    // ink's own buttons, and a mark left on the model from an earlier round
    // does not get to take the keyboard away from the model for good.
    busy: annotationsReady && (tool !== null || eraser),
  };
  const documentOpenRef = useRef(documentOpen);
  documentOpenRef.current = documentOpen;
  const cancelInkRef = useRef(setAnnotationCancel);
  cancelInkRef.current = setAnnotationCancel;
  useEffect(() => {
    const listen = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.repeat || documentOpenRef.current) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || target.closest("input, textarea, select, [contenteditable]"))) return;
      const key = event.key.toLowerCase();
      const current = sketchRef.current;
      if (current.phase === "profile" && (key === "arrowright" || key === "arrowleft" || key === "arrowup" || key === "arrowdown")) {
        event.preventDefault();
        const axis = key === "arrowright" || key === "arrowleft" ? "x" : "y";
        showSketch({ ...current, axisLock: current.axisLock === axis ? null : axis });
        return;
      }
      if (key === "enter" && current.tool === "polygon" && current.phase === "profile") {
        event.preventDefault(); closePolygon(); return;
      }
      if (key === " " && !(target instanceof HTMLElement && target.closest("button, a"))) {
        event.preventDefault(); chooseDrawingTool(null); return;
      }
      if (inkRef.current.busy || actionInProgressRef.current) return;
      if (onSketch && (key === "r" || key === "c" || key === "l")) {
        event.preventDefault(); chooseDrawingTool(key === "r" ? "rectangle" : key === "c" ? "circle" : "polygon"); return;
      }
      if (key === "t") { event.preventDefault(); chooseMeasure(); return; }
      const tool = ({ p: "pushPull", m: "move", q: "rotate", s: "scale" } as const)[key as "p" | "m" | "q" | "s"];
      if (tool && modelKeysRef.current?.onTool) {
        event.preventDefault(); chooseDrawingTool(null); modelKeysRef.current.onTool(tool);
      }
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [chooseDrawingTool, chooseMeasure, closePolygon, onSketch, showSketch]);
  useEffect(() => {
    if (!modelKeysAvailable) return;
    const listen = (event: KeyboardEvent) => {
      const keys = modelKeysRef.current;
      if (!keys || event.defaultPrevented) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable ||
          target.closest("input, textarea, select, [contenteditable]") !== null)) {
        // Someone is writing. Delete, Ctrl+Z and the rest belong to the text.
        return;
      }
      // The drawings workspace owns its own keys while it is open.
      if (documentOpenRef.current) return;
      const control = event.ctrlKey || event.metaKey;
      const inProgress = actionInProgressRef.current;
      if (event.key === "Escape") {
        // An action in progress owns Esc; its own handler cancels it. Only when
        // none is running does Esc mean "nothing is picked any more".
        if (inProgress || !keys.hasSelection) return;
        event.preventDefault();
        keys.onClearSelection();
        return;
      }
      if (!control && (event.key === "Delete" || event.key === "Backspace")) {
        // An action in progress, or the ink in hand, owns the key: with an
        // annotation tool or the eraser armed, Delete is not the model's even
        // when something on the model is picked.
        if (inProgress || inkRef.current.busy) return;
        event.preventDefault();
        // A held key repeats; one press is one deletion.
        if (event.repeat || !keys.canDelete) return;
        keys.onDelete();
        return;
      }
      if (!control) return;
      const key = event.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      const redo = key === "y" || event.shiftKey;
      const ink = inkRef.current;
      if (ink.busy) {
        // The ink is the mode in hand, so exactly one of the two undos happens
        // and it is this one. With no tool armed the model has the key, even
        // when marks from an earlier round are still on the picture.
        event.preventDefault();
        if (event.repeat) return;
        cancelInkRef.current((value) => value + 1);
        if (redo) { if (ink.canRedo) ink.redo(); return; }
        if (ink.canUndo) ink.undo();
        return;
      }
      if (inProgress) return;
      event.preventDefault();
      if (event.repeat) return;
      if (redo) { if (keys.canRedo) keys.onRedo(); return; }
      if (keys.canUndo) keys.onUndo();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
    // The handler reads the current owners through refs, so it is bound once
    // per mount rather than re-bound on every render of the shell.
  }, [modelKeysAvailable]);

  const transferNavigation = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 1 && event.button !== 2) return false;
    // The same transfer as annotation drawing: OrbitControls captures the
    // real pointer after its initial press and owns the remaining gesture.
    const canvas = event.currentTarget.parentElement?.querySelector<HTMLCanvasElement>(".viewport-canvas");
    if (canvas) { event.preventDefault(); canvas.dispatchEvent(new PointerEvent("pointerdown", event.nativeEvent)); }
    return true;
  };

  // Only a host that named its own origin in this page's URL is one to talk to.
  const [host] = useState(() => (embedded ? hostOrigin() : null));
  const activeTool = GESTURE_TOOLS.find((item) => item.kind === tool);
  const captureFeedback =
    captureState === "busy"
      ? t("stage.tools.screenshotBusy")
      : captureState === "success" && capturePath !== null
        ? t("stage.tools.screenshotSaved", { path: capturePath })
        : captureState === "error"
          ? t("stage.tools.screenshotFailed")
          : "";
  const sameSource = editingModelSource !== null && viewedModelSource !== null &&
    editingModelSource.runId === viewedModelSource.runId &&
    editingModelSource.stateDigest === viewedModelSource.stateDigest &&
    editingModelSource.assetSha256 === viewedModelSource.assetSha256;
  const editingLabel = editingBaseLabel ?? versions.find((group) => group.runId === editingBaseRunId)?.exports[0]?.artifact.fileName ?? editingBaseRunId;
  const versionCount = designHistory ? designHistory.history?.stages.length ?? 0 : new Set([
    ...versions.flatMap((group) => group.exports.filter(({ artifact }) => artifact.format === "3dm" && artifact.sha256 !== null)
      .map(({ artifact }) => `${group.runId}:${artifact.sha256}`)),
    ...workingCopies.flatMap((copy) => copy.options.map((option) => `${option.modelSource.runId}:${option.modelSource.assetSha256}`)),
  ]).size;
  const loadedOptionLabel = workingCopies.flatMap((copy) => copy.options)
    .find((option) => option.modelSource.runId === loadedRunId && loadedShas.includes(option.modelSource.assetSha256))?.label;
  const acceptedStage = designHistory?.history?.stages.find((stage) => stage.modelSource.runId === loadedRunId && loadedShas.includes(stage.modelSource.assetSha256));
  const contextLabel = designHistory ? acceptedStage?.label ?? (designHistory.candidates.some((candidate) => candidate.modelSource.runId === loadedRunId)
    ? `${designHistory.history?.stages.find((stage) => stage.stageRef === designHistory.currentStageRef)?.label ?? "历史 Stage"} · 候选未提交` : "尚未确认 Stage") : loadedOptionLabel;
  const sessionStatus = <>
    {editingBaseRunId !== null && (
      <div className="editing-base" data-source-match={sameSource ? "same" : "different"}>
        <span role="status" aria-live="polite">
          {changingBase ? t("stage.base.loading") : sameSource ? t("stage.base.sameSource") : t("stage.base.current")}
          {!sameSource && <strong className="editing-base__name" title={editingLabel ?? undefined}> {editingLabel}</strong>}
        </span>
        {loadedRunId !== null && !sameSource && (
          <button
            type="button"
            className="btn btn--small"
            disabled={changingBase || baseActionBusy || loadingSha !== null || status === "loading" || blend !== null}
            title={t("stage.base.continueTitle")}
            onClick={() => viewedModelSource ? void onContinueModelSource(viewedModelSource) : onContinue(loadedRunId)}
          >
            {t("stage.base.continue")}
          </button>
        )}
        {explicitBase && (
          <button type="button" className="btn btn--small" disabled={changingBase || baseActionBusy || loadingSha !== null || status === "loading"} onClick={onDefaultBase}>
            {t("stage.base.default")}
          </button>
        )}
        {baseError && <ErrorPanel error={baseError} what="GET /api/state" />}
      </div>
    )}
    {modelAnnotations && <div className="stage-annotations-status" data-model-annotations-status={modelAnnotations.error ? "error" :
      !modelAnnotations.ready ? "loading" : modelAnnotations.saving || modelAnnotations.dirty ? "saving" : "saved"}>
      {!modelAnnotations.error && <span role="status">{t(!modelAnnotations.ready ? "stage.annotations.loading" :
        modelAnnotations.saving || modelAnnotations.dirty ? "stage.annotations.saving" : "stage.annotations.saved")}</span>}
      {modelAnnotations.error && <>
        <ErrorPanel error={modelAnnotations.error} what="/api/model-annotations" />
        <button type="button" className="btn btn--small" onClick={() => void (!modelAnnotations.ready || modelAnnotations.error?.status === 409
          ? modelAnnotations.reload() : modelAnnotations.save()).catch(() => undefined)}>
          {t(!modelAnnotations.ready || modelAnnotations.error.status === 409 ? "stage.annotations.reload" : "stage.annotations.retry")}
        </button>
      </>}
    </div>}
  </>;
  return (
    <section className="stage" aria-label={t("stage.ariaLabel")}>
      {(!embedded || picked !== null) && <div className="stage-mode-switch" role="group" aria-label={t("workspace.switcher")} data-embedded={String(embedded)}>
        {/* A host page carries these entries in its own rail; this page would
            only repeat them, and its board entry would leave the host. */}
        {!embedded && <>
        <button type="button" aria-pressed={!documentOpen} onClick={() => onDocumentView({ ...documentView, open: false })}>{t("workspace.monkeyarch")}</button>
        <button type="button" aria-pressed={documentOpen} onClick={() => { setAnnotationCancel((value) => value + 1); onDocumentView({ ...documentView, mounted: true, open: true }); }}>{t("workspace.monkeydiagram")}</button>
        <button type="button" onClick={() => {
          const target = new URL(window.location.href);
          target.searchParams.set("view", "board");
          window.open(target.href, "_blank", "noopener");
        }}>{t("workspace.monkeyboard")}</button></>}
        {picked && (
          <div
            className="picked"
            title={developerMode ? t("stage.picked.title", {
              status: picked.status,
              sourceState: picked.sourceState,
            }) : designObjectLabel(picked.elementId ?? picked.componentId) ?? undefined}
          >
            <span className="label">{t("stage.picked.label")}</span>
            <span className="picked__name">
              {developerMode
                ? picked.elementId ?? picked.componentId ?? t("stage.picked.none")
                : designObjectLabel(picked.elementId ?? picked.componentId) ?? t("stage.picked.unresolved")}
            </span>
            {developerMode && picked.status !== "resolved" && <span className="picked__meta">{picked.status}</span>}
          </div>
        )}
      </div>}
      {drawing?.error && <div className="stage-drawing-error">
        <div>
          <p role="alert">{t(drawing.error.code === "DRAWING_COMPLETE_SOURCE_UNAVAILABLE" ? "stage.drawing.completeSourceUnavailable"
            : drawing.error.code === "DRAWING_SOURCE_MISMATCH" ? "stage.drawing.sourceMismatch" : "stage.drawing.failed")}</p>
          <details key={`${drawing.error.code}:${drawing.error.detail}`}><summary>{t("stage.drawing.details")}</summary>
            <p className="mono" lang="en" translate="no">{drawing.error.code}: {drawing.error.detail}</p>
          </details>
        </div>
        <button type="button" className="btn btn--small" onClick={drawing.dismissError} aria-label={t("stage.drawing.dismiss")}>{t("common.close")}</button>
      </div>}
      <div className="stage-workspace">
      <div className={`stage-model${documentOpen ? " stage-model--hidden" : ""}`} inert={documentOpen} aria-hidden={documentOpen}
        /* Undo and redo are decided in one place - the keyboard effect above -
           so that one Ctrl+Z reaches exactly one owner. The ink's undo is still
           the ink's: that effect calls these same handlers when the ink is what
           is being worked on, and leaves the key alone when it is not. */>
      {/* A machine with no WebGL context throws while the renderer is built;
          behind its own boundary that costs the canvas and nothing else. */}
      <ErrorBoundary label={t("stage.viewer.label")}>
        <ThreeDmViewport
          ref={viewportRef}
          onInspection={onInspection}
          onStatus={onStatus}
          onRequestFile={onRequestFile}
          onOpenFile={onOpenFile}
          onSource={onSource}
          onPick={onPick}
          idle={hasModel ? undefined : (
            <div className="stage-empty">
              <svg className="stage-empty__icon" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" aria-hidden="true">
                <path d="m16 3 12 7v13l-12 7L4 23V10Z M4 10l12 7 12-7 M16 17v13" />
              </svg>
              <h2>{t("stage.empty.title")}</h2>
              <p>{t(embedded && host !== null ? "stage.empty.bodyEmbedded" : "stage.empty.body")}</p>
              <div className="stage-empty__actions">
                {home !== null && <button type="button" className="btn"
                  title={home.kind === "reference" ? t("stage.tools.referenceShow", { runId: home.runId })
                      : t("stage.tools.homeShow", { runId: home.runId })}
                  onClick={onShowHome}>
                  {home?.kind === "fallback" ? t("stage.empty.openHome") : t("stage.empty.openReference")}
                </button>}
                {/* The versions strip is this page's own; embedded, the host
                    states the project's position instead of repeating it. */}
                {!embedded && versionCount > 0 && <button type="button" className="btn"
                  onClick={() => { onVersionsOpen?.(); setVersionsOpen(true); }}>
                  {t("stage.empty.chooseVersion", { count: versionCount })}
                </button>}
                {embedded && host !== null && <button type="button" className="btn"
                  onClick={() => { requestStartModeling(host); }}>
                  {t("stage.empty.startInChat")}
                </button>}
                <button type="button" className="btn" onClick={onRequestFile}>{t("stage.empty.openLocal")}</button>
              </div>
              <p className="stage-empty__hint">{t("stage.empty.hint")}</p>
            </div>
          )}
        />
      </ErrorBoundary>
      {measuring && (
        /* Two points off the loaded model, and the distance between them. It
           reads geometry and writes nothing: no candidate, no record. */
        <div
          className="stage-sketch"
          data-phase={measure.from === null ? "from" : "to"}
          onPointerDown={transferNavigation}
          onPointerMove={(event) => {
            if (measure.from === null) {
              const at = viewportRef.current?.snapOnModel(event.clientX, event.clientY);
              setSnapNote(at && at.kind !== "surface" ? at.kind : null);
              return;
            }
            const at = viewportRef.current?.snapOnModel(event.clientX, event.clientY);
            if (!at) return;
            setSnapNote(at.kind !== "surface" ? at.kind : null);
            showMeasure({ ...measure, to: at.point, kinds: [measure.kinds[0], at.kind] });
          }}
          onClick={(event) => {
            const at = viewportRef.current?.snapOnModel(event.clientX, event.clientY);
            if (!at) return;
            if (measure.from === null) showMeasure({ from: at.point, to: null, kinds: [at.kind, null] });
            else showMeasure({ ...measure, to: at.point, kinds: [measure.kinds[0], at.kind] });
          }}
        />
      )}
      {sketch.tool !== null && (
        /* Pointer work for one action. Everything it draws is the viewport's
           temporary preview; the project is only reached when it finishes. */
        <div
          className="stage-sketch"
          data-phase={sketch.phase}
          onPointerMove={(event) => {
            const sketch = sketchRef.current;
            const onModel = viewportRef.current?.snapOnModel(event.clientX, event.clientY);
            const world = onModel && onModel.kind !== "surface"
              ? onModel.point
              : sketch.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, sketch.plane)
                : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, sketch.base);
            if (sketch.phase === "profile" && sketch.anchor !== null) {
              if (!world) return;
              // A corner or a middle of a real edge wins; otherwise the plane
              // point, still locked to the action's own axes.
              const moved = onModel && onModel.kind !== "surface"
                ? { point: pointToPlane(world, sketch.plane), snapped: { kind: onModel.kind } }
                : snapPoint(pointToPlane(world, sketch.plane), { endpoints: sketch.plane ? sketch.vertices : [...snapPoints, ...sketch.vertices], anchor: sketch.vertices.at(-1) ?? sketch.anchor, radius: snapRadius() });
              const anchor = sketch.vertices.at(-1) ?? sketch.anchor;
              const shiftAxis = event.shiftKey ? Math.abs(moved.point[0] - anchor[0]) >= Math.abs(moved.point[1] - anchor[1]) ? "x" : "y" : null;
              const point = lockedPoint(moved.point, anchor, sketch.axisLock ?? shiftAxis);
              setSnapNote(moved.snapped ? moved.snapped.kind : null);
              const profile = sketch.tool === "circle" ? circleOf(sketch.anchor, Math.hypot(point[0] - sketch.anchor[0], point[1] - sketch.anchor[1]))
                : sketch.tool === "polygon" ? [...sketch.vertices, point] : rectangleOf(sketch.anchor, point);
              showSketch({ ...sketch, profile, cursor: point });
            } else if (sketch.phase === "height") {
              // The plane a height is read on stands through the corner the
              // pointer is already at — the one that was just clicked, which
              // is the profile's third point. Through the *first* corner it
              // would be a different vertical plane, and the same ray would
              // meet it at a different level: the height would jump the moment
              // the phase changed and then read short all the way up.
              const at = heightAnchor(sketch);
              const origin = at && pointFromPlane(at, sketch.plane, sketch.base);
              const normal: Vec3 = sketch.plane ? [...sketch.plane.normal] : [0, 0, 1];
              const raised = origin && (sketch.plane
                ? viewportRef.current?.pointAlongAxis(event.clientX, event.clientY, origin, normal)
                : viewportRef.current?.unprojectOnPlane(event.clientX, event.clientY, origin, true));
              if (!raised) return;
              const height = normal.reduce((sum, value, index) => sum + value * (raised[index]! - origin![index]!), 0);
              showSketch({ ...sketch, height });
            }
          }}
          onPointerDown={(event) => {
            if (transferNavigation(event)) return;
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onClick={(event) => {
            if (sketchBusy) return;
            const sketch = sketchRef.current;
            const onModel = viewportRef.current?.snapOnModel(event.clientX, event.clientY);
            const world = onModel && onModel.kind !== "surface"
              ? onModel.point
              : sketch.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, sketch.plane)
                : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, sketch.base);
            if (sketch.phase === "idle" || sketch.anchor === null) {
              if (!world) return;
              const start = onModel && onModel.kind !== "surface"
                ? { point: pointToPlane(world, sketch.plane), snapped: { kind: onModel.kind } }
                : snapPoint(pointToPlane(world, sketch.plane), { endpoints: sketch.plane ? [] : snapPoints, radius: snapRadius() });
              setSnapNote(start.snapped ? start.snapped.kind : null);
              showSketch({ ...sketch, phase: "profile", anchor: start.point, vertices: [start.point], cursor: start.point, profile: [], height: 0, typed: "" });
            } else if (sketch.phase === "profile") {
              if (sketch.tool === "polygon") {
                if (!world) return;
                const point = sketch.cursor ?? pointToPlane(world, sketch.plane);
                if (sketch.vertices.length >= 3 && Math.hypot(point[0] - sketch.anchor[0], point[1] - sketch.anchor[1]) <= snapRadius()) {
                  closePolygon(); return;
                }
                const previous = sketch.vertices.at(-1)!;
                if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) <= 1e-6) return;
                const vertices = [...sketch.vertices, point];
                showSketch({ ...sketch, vertices, profile: vertices, typed: "" });
                return;
              }
              if (!enclosesArea(sketch.profile)) return;
              showSketch({ ...sketch, phase: "height", typed: "" });
            } else {
              submitSketch();
            }
          }}
          onDoubleClick={(event) => { event.preventDefault(); closePolygon(); }}
        />
      )}
      <Annotate
        viewportRef={viewportRef}
        tool={annotationsReady ? tool : null}
        gestures={gestures}
        onGesture={onGesture}
        style={annotationStyle}
        cancelToken={annotationCancel}
        eraser={annotationsReady && eraser}
        onErase={onEraseGestures}
      />
      {/* The shield preserves the stage's loading boundary while the translucent matte
          backing leaves the previous picture legible as context. */}
      {status === "loading" && <LoadingOverlay mode="stage" status={message} />}

      <div className="hud">
        <div className="hud__left">
          {blend && (
            <div className="blend" aria-label={t("stage.blend.ariaLabel")}>
              <span className="label">{t("stage.blend.before")}</span>
              <input
                type="range"
                className="blend__slider"
                min={0}
                max={1}
                step={0.01}
                value={blend.t}
                aria-label={t("stage.blend.sliderAria")}
                onChange={(event) => onBlend(Number(event.currentTarget.value))}
              />
              <span className="label">{t("stage.blend.after")}</span>
              <span className={`quiet${developerMode ? " mono blend__meta" : ""}`}>
                {developerMode
                  ? <>{blend.candidateId} · {t("stage.blend.meshes", { count: blend.meshes })} · {t("stage.blend.afterTinted")}</>
                  : t("stage.blend.afterTinted")}
              </span>
              <button type="button" className="btn btn--small" onClick={onEndBlend}>
                {t("stage.blend.done")}
              </button>
            </div>
          )}
          {artifactError && (
            <ErrorPanel
              error={artifactError}
            />
          )}
        </div>
        <div className="viewtools-wrap">
          <div className="viewtools">
            <button type="button" title={t("stage.sketch.selectTitle")} aria-pressed={sketch.tool === null && !measuring}
              onClick={() => chooseDrawingTool(null)}>{t("stage.sketch.select")}</button>
            {onSketch && <>
              {(["rectangle", "circle", "polygon"] as const).map((kind) => <button key={kind} type="button"
                aria-pressed={sketch.tool === kind} title={t(`stage.sketch.${kind}Title`)} disabled={sketchBusy}
                onClick={() => chooseDrawingTool(sketch.tool === kind ? null : kind)}>{t(`stage.sketch.${kind}`)}</button>)}
              <select aria-label={t("stage.sketch.workPlane")} value={workPlaneName} disabled={sketchBusy}
                onChange={(event) => choosePlane(event.target.value as typeof workPlaneName)}>
                <option value="xy">{t("stage.sketch.planeXY")}</option>
                <option value="xz">{t("stage.sketch.planeXZ")}</option>
                <option value="yz">{t("stage.sketch.planeYZ")}</option>
                <option value="face" disabled={!model?.hasSelection}>{t("stage.sketch.planeFace")}</option>
              </select>
            </>}
            {model?.onTool && (["pushPull", "move", "rotate", "scale", "copy"] as const).map((kind) => <button
              key={kind} type="button" disabled={!model.hasSelection || sketchBusy} title={t(`stage.model.${kind}Title`)}
              onClick={() => { chooseDrawingTool(null); model.onTool?.(kind); }}>{t(`stage.model.${kind}`)}</button>)}
            {model && <>
              <button type="button" disabled={!model.canUndo || sketchBusy || actionInProgress} title="Ctrl+Z" onClick={model.onUndo}>{t("stage.model.undo")}</button>
              <button type="button" disabled={!model.canRedo || sketchBusy || actionInProgress} title="Ctrl+Shift+Z / Ctrl+Y" onClick={model.onRedo}>{t("stage.model.redo")}</button>
            </>}
            <button type="button" aria-pressed={measuring} title={t("stage.measure.title")}
              onClick={() => measuring ? chooseDrawingTool(null) : chooseMeasure()}>
              {t("stage.measure.label")}
            </button>
            <button type="button" aria-expanded={annotationToolsOpen} aria-controls="annotation-tools"
              onClick={() => { setAnnotationToolsOpen((open) => !open); setViewToolsOpen(false); setVersionsOpen(false); }}>
              {t("stage.tools.annotate")}{activeTool && !eraser ? ` · ${t(activeTool.labelKey)}` : ""}
            </button>
            <button type="button" aria-pressed={eraser} disabled={!annotationsReady} title={t("document.tool.eraser")}
              onClick={() => { setAnnotationCancel((value) => value + 1); setEraser((value) => !value); }}>{t("document.tool.eraser")}</button>
            <button type="button" disabled={!canUndoGesture} title={t("stage.tools.undo.title")} onClick={onUndoGesture}>{t("stage.tools.undo.label")}</button>
            <button type="button" disabled={!canRedoGesture} title={t("document.redo")} onClick={onRedoGesture}>{t("document.redo")}</button>
            {(tool !== null || eraser) && <button type="button" title={t("stage.tools.cancel.title")} onClick={() => { setAnnotationCancel((value) => value + 1); setEraser(false); onTool(null); }}>{t("stage.tools.cancel.label")}</button>}
            <span className="viewtools__sep" aria-hidden="true" />
            <button type="button" onClick={() => viewportRef.current?.fitView()}>{t("stage.tools.fit")}</button>
            <button type="button" onClick={() => viewportRef.current?.frontView()}>{t("stage.tools.front")}</button>
            <button type="button" aria-expanded={viewToolsOpen} aria-controls="view-tools"
              onClick={() => { setViewToolsOpen((open) => !open); setAnnotationToolsOpen(false); setVersionsOpen(false); }}>{t("stage.tools.viewOptions")}</button>
          </div>
          {annotationToolsOpen && <div id="annotation-tools" className="viewtools viewtools--panel" role="group" aria-label={t("stage.tools.annotate")}>
            {GESTURE_TOOLS.map((item) => (
              <button key={item.kind} type="button" disabled={!annotationsReady} title={t(item.titleKey)}
                aria-pressed={!eraser && tool === item.kind}
                onClick={() => { setEraser(false); onTool(!eraser && tool === item.kind ? null : item.kind); }}>
                {item.glyph} {t(item.labelKey)}
              </button>
            ))}
            <span className="viewtools__sep" aria-hidden="true" />
            <span className="annotation-style" aria-label={t("stage.tools.colour")}>
              {["#e5534b", "#2f80ed", "#f2c94c", "#ffffff"].map((color) => (
                <button key={color} type="button" className="annotation-style__colour" aria-label={color} aria-pressed={annotationStyle.color === color} onClick={() => setAnnotationStyle((current) => ({ ...current, color }))} style={{ "--annotation-colour": color } as CSSProperties} />
              ))}
            </span>
            <span className="annotation-style" aria-label={t("stage.tools.lineWidth")}>
              {([2, 4, 6] as const).map((lineWidth) => (
                <button key={lineWidth} type="button" className="annotation-style__width" aria-label={`${lineWidth}px`} aria-pressed={annotationStyle.lineWidth === lineWidth} onClick={() => setAnnotationStyle((current) => ({ ...current, lineWidth }))}><span style={{ height: lineWidth }} /></button>
              ))}
            </span>
            {tool && activeTool && <span className="viewtools__hint quiet">{t("stage.tools.drawingHint", { tool: t(activeTool.labelKey) })}</span>}
          </div>}
          {viewToolsOpen && <div id="view-tools" className="viewtools viewtools--panel" role="group" aria-label={t("stage.tools.viewOptions")}>
          {(["top", "front", "right", "iso"] as const).map((view) => <button type="button" key={view}
            onClick={() => viewportRef.current?.standardView(view)}>{t(`stage.view.${view}`)}</button>)}
          <button
            type="button"
            aria-pressed={displayMode === "model"}
            title={t("stage.tools.modelShow")}
            onClick={() => onDisplayMode("model")}
          >
            {t("stage.tools.model")}
          </button>
          {/* The frame: what every element on this picture is placed
              against. A panel, not a camera tool, but this is the row an
              architect reaches for when the model is the question. */}
          <button
            type="button"
            aria-pressed={displayMode === "framework"}
            title={t("frame.openTitle")}
            onClick={() => onDisplayMode("framework")}
          >
            {t("frame.open")}
          </button>
          {/* The massing projection: what the building *is*, over the same
              original picture the frame reads against. */}
          <button
            type="button"
            aria-pressed={displayMode === "massing"}
            title={t("options.openTitle")}
            onClick={() => onDisplayMode("massing")}
          >
            {t("options.open")}
          </button>
          {/* The brief, beside the frame: the two documents an architect
              reads the model against. */}
          <button
            type="button"
            aria-pressed={programOpen}
            disabled={changingBase}
            title={t("program.openTitle")}
            onClick={onToggleProgram}
          >
            {t("program.open")}
          </button>
          {drawing && <>
            <span className="viewtools__sep" aria-hidden="true" />
            <select aria-label={t("stage.drawing.direction")} value={elevationView} disabled={drawing.busy}
              onChange={(event) => { setElevationView(event.target.value as NonNullable<ElevationRequestDto["view"]>); drawing.dismissError(); }}>
              <option value="front">{t("stage.drawing.front")}</option><option value="back">{t("stage.drawing.back")}</option><option value="left">{t("stage.drawing.left")}</option><option value="right">{t("stage.drawing.right")}</option>
            </select>
            <button disabled={!drawing.available || drawing.busy} onClick={() => drawing.generate(elevationView)}>{t(drawing.busy ? "stage.drawing.busy" : "stage.drawing.generate")}</button>
          </>}
          <span className="viewtools__sep" aria-hidden="true" />
          {/* One button, home: the reference run's exports when it left
              any, else the export the stage actually opened on — named for
              what it brings back, disabled only when there is nothing. */}
          <button
            type="button"
            disabled={home === null}
            title={
              home === null
                ? t("stage.tools.homeUnavailable")
                : home.kind === "reference"
                  ? t("stage.tools.referenceShow", { runId: home.runId })
                  : t("stage.tools.homeShow", { runId: home.runId })
            }
            onClick={onShowHome}
          >
            {home?.kind === "fallback"
              ? t("stage.tools.home")
              : t("stage.tools.reference")}
          </button>
          <button
            type="button"
            disabled={
              loadedRunId === null ||
              blend !== null ||
              captureState === "busy"
            }
            title={
              loadedRunId === null
                ? t("stage.tools.screenshotUnavailable")
                : blend !== null
                  ? t("stage.tools.screenshotBlendUnavailable")
                  : t("stage.tools.screenshotTitle")
            }
            onClick={() => void onCapture()}
          >
            {captureState === "busy"
              ? t("stage.tools.screenshotBusy")
              : t("stage.tools.screenshot")}
          </button>
          <button type="button" onClick={() => viewportRef.current?.clear()}>
            {t("stage.tools.clear")}
          </button>
          <button type="button" onClick={onRequestFile}>
            {t("stage.tools.open3dm")}
          </button>
          {/* The editable copy of what is on screen. It is made from that
              model's own exact STEP by the Rhino on this machine, so it is
              offered only when the picture is one delivery, and the title says
              which file - or why there is no single one to make. */}
          {workModel && (
            <button
              type="button"
              data-work-model-export
              disabled={workModel.source === null || workModel.busy}
              title={
                workModel.refusal === "busy-elsewhere"
                  ? t("stage.tools.workModelBusyElsewhere")
                  : workModel.refusal === "several-seats"
                  ? t("stage.tools.workModelSeveralSeats")
                  : workModel.refusal === "not-an-export"
                    ? t("stage.tools.workModelNotAnExport")
                    : workModel.refusal === "nothing-loaded"
                      ? t("stage.tools.workModelNothingLoaded")
                      : t("stage.tools.workModelTitle", { fileName: workModel.source?.fileName ?? "" })
              }
              onClick={workModel.onExport}
            >
              {workModel.busy ? t("stage.tools.workModelBusy") : t("stage.tools.workModel")}
            </button>
          )}
          {workModel?.exported?.sha256 && (
            <a
              className="vcard__save"
              data-work-model-save
              href={`/api/artifacts/${workModel.exported.sha256}/bytes`}
              download={workModel.exported.fileName}
              title={t("stage.versions.saveTitle", { fileName: workModel.exported.fileName })}
            >
              {t("stage.tools.workModelSave")}
            </a>
          )}
          {workModel?.error && (
            <p className="quiet" role="alert" data-work-model-error>{workModel.error}</p>
          )}
          </div>}
          {measuring && (
            <div className="sketch-entry" role="group" aria-label={t("stage.measure.label")}>
              <span className="sketch-entry__step" role="status">
                {measure.from === null ? t("stage.measure.first")
                  : measure.to === null ? t("stage.measure.second")
                    : t("stage.measure.distance", {
                        value: distanceBetween(
                          [measure.from[0], measure.from[1], measure.from[2]],
                          [measure.to[0], measure.to[1], measure.to[2]],
                        ).toFixed(3),
                      })}
                {snapNote !== null && <span className="quiet"> · {t("stage.sketch.snapped", { kind: snapNote })}</span>}
              </span>
              <span className="quiet sketch-entry__hint">{t("stage.measure.hint")}</span>
            </div>
          )}
          {sketch.tool !== null && (
            <div className="sketch-entry" role="group" aria-label={t(`stage.sketch.${sketch.tool}`)}>
              <span className="sketch-entry__step" role="status">
                {sketchBusy ? t("stage.sketch.busy")
                  : sketch.phase === "height" ? t("stage.sketch.pull")
                    : sketch.tool === "circle" ? sketch.anchor === null ? t("stage.sketch.center") : t("stage.sketch.radiusHint")
                      : sketch.tool === "polygon" ? t("stage.sketch.polygonHint")
                        : sketch.anchor === null ? t("stage.sketch.firstCorner") : t("stage.sketch.secondCorner")}
                {snapNote !== null && <span className="quiet"> · {t("stage.sketch.snapped", { kind: snapNote })}</span>}
              </span>
              {sketch.anchor !== null && !sketchBusy && (
                <label className="sketch-entry__value">
                  {sketch.phase === "height" ? t("stage.sketch.height") : sketch.tool === "circle" ? t("stage.sketch.radius")
                    : sketch.tool === "polygon" ? t("stage.sketch.segment") : t("stage.sketch.side")}
                  <input
                    autoFocus
                    inputMode="decimal"
                    value={sketch.typed}
                    onChange={(event) => setSketch((current) => ({ ...current, typed: event.target.value }))}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return;
                      event.preventDefault();
                      const value = typedNumber(sketch.typed);
                      if (sketch.phase === "height") {
                        if (value === null) return;
                        // One confirmation submits once: the action is taken
                        // off the pointer before anything is sent.
                        const settled = { ...sketchRef.current, height: value, typed: "" };
                        sketchRef.current = settled;
                        showSketch(settled);
                        submitSketch(value === 0);
                      } else {
                        const corner = sketch.cursor ?? sketch.profile[2] ?? sketch.anchor!;
                        if (sketch.tool === "polygon") {
                          if (!sketch.typed.trim()) { closePolygon(); return; }
                          if (value === null || value <= 0) return;
                          const from = sketch.vertices.at(-1)!;
                          const dx = corner[0] - from[0], dy = corner[1] - from[1];
                          const length = Math.hypot(dx, dy);
                          const point: PlanPoint = length > 1e-6 ? [from[0] + dx * value / length, from[1] + dy * value / length] : [from[0] + value, from[1]];
                          const vertices = [...sketch.vertices, point];
                          showSketch({ ...sketch, vertices, profile: vertices, cursor: point, typed: "" });
                          return;
                        }
                        const dimensions = typedDimensions(sketch.typed);
                        if (!dimensions || (sketch.tool === "circle" && (value === null || value <= 0))) return;
                        const profile = sketch.tool === "circle" ? circleOf(sketch.anchor!, value!)
                          : sizedRectangle(sketch.anchor!, corner, dimensions[0], dimensions[1]);
                        showSketch({
                          ...sketch, phase: "height", typed: "",
                          profile, cursor: sketch.tool === "circle" ? [sketch.anchor![0] + value!, sketch.anchor![1]] : profile[2]!,
                        });
                      }
                    }}
                  />
                </label>
              )}
              {sketch.phase === "profile" && <>
                <button type="button" aria-pressed={sketch.axisLock === "x"} title={t("stage.sketch.axisHint")}
                  onClick={() => showSketch({ ...sketch, axisLock: sketch.axisLock === "x" ? null : "x" })}>{t("stage.sketch.axisX")}</button>
                <button type="button" aria-pressed={sketch.axisLock === "y"} title={t("stage.sketch.axisHint")}
                  onClick={() => showSketch({ ...sketch, axisLock: sketch.axisLock === "y" ? null : "y" })}>{t("stage.sketch.axisY")}</button>
                {sketch.tool === "polygon" && <button type="button" disabled={!enclosesArea(sketch.vertices)}
                  onClick={closePolygon}>{t("stage.sketch.closePolygon")}</button>}
              </>}
              {sketch.phase === "height" && <button type="button" disabled={sketchBusy} onClick={() => {
                showSketch({ ...sketchRef.current, height: 0 }); submitSketch(true);
              }}>{t("stage.sketch.makeFace")}</button>}
              <span className="quiet sketch-entry__hint">{t("stage.sketch.cancel")}</span>
            </div>
          )}
          {model?.toolPanel}
          {captureFeedback && <span className="viewtools__feedback" role="status" aria-live="polite" aria-atomic="true">{captureFeedback}</span>}
        </div>
      </div>


      {framePanel}
      {optionsPanel}
      {programPanel}
      {drawer}
      </div>
      <div className="stage__foot">
        {/* Embedded, the host states the project's published version and Stage
            beside the conversation; a second permanent strip here would be a
            second answer to the same question. */}
        {!embedded && <div className="stage__versions">
          <button type="button" className="btn stage__versions-toggle" aria-expanded={versionsOpen} aria-controls="stage-versions-panel"
            onClick={() => { if (!versionsOpen) onVersionsOpen?.(); setVersionsOpen((open) => !open); setAnnotationToolsOpen(false); setViewToolsOpen(false); }}>
            {t("stage.versions.open")} <span className="quiet">{versionCount}</span>
            {contextLabel && <span className="stage__versions-current">{contextLabel}</span>}
            {hasNewVersions && <span className="stage__versions-new" role="status">{t("stage.versions.new")}</span>}
          </button>
          {versionsOpen && <div id="stage-versions-panel" className="stage__versions-panel" role="region" aria-label={t("stage.versions.ariaLabel")}>
            <div className="stage__versions-head"><strong>{t("stage.versions.ariaLabel")}</strong>
              <button type="button" className="btn btn--small" onClick={() => setVersionsOpen(false)}>{t("stage.versions.close")}</button>
            </div>
            <div className="stage__versions-session">{sessionStatus}</div>
            <VersionsStrip
              design={designHistory}
              workingCopies={workingCopies} onOpenWorkingOption={onOpenWorkingOption}
              groups={versions} loadingSha={loadingSha} loadedShas={loadedShas} loadedRunId={loadedRunId}
              onOpen={onOpenVersion} onOpenRun={onOpenRun} onCompare={onCompareVersion}
            />
          </div>}
        </div>}
        {developerMode && <><span className="stage__spacer" />
        <button
          type="button"
          className="drawer-tab"
          onClick={() => onEvidence("honesty")}
        >
          {t("nav.evidence")}
          <span className="drawer-tab__count">
            {t(
              review.changes === 1
                ? "stage.review.changeOne"
                : "stage.review.changeMany",
              { count: review.changes },
            )}{" "}
            · {t("stage.review.checked", { count: review.checked })} ·{" "}
            {t(
              review.needsReview === 1
                ? "stage.review.needsOne"
                : "stage.review.needsMany",
              { count: review.needsReview },
            )}
          </span>
          <span
            className="drawer-tab__count mono"
            title={t("stage.review.drawerTitle")}
          >
            {t("evidence.tabs.honesty")} {evidenceCounts.honesty} ·{" "}
            {t("evidence.tabs.events")} {evidenceCounts.events}
          </span>
        </button></>}
      </div>

      {documentMounted && <div style={{ visibility: documentOpen ? "visible" : "hidden" }} inert={!documentOpen} aria-hidden={!documentOpen}>
        {documentProjectId && documentRunId ? <DocumentCanvas key={`${documentProjectId}:${documentRunId}:${documentView.sourceSha}:${documentView.revisionRef}`}
          projectId={documentProjectId} runId={documentRunId} controller={documentAnnotationsController}
          modelSources={documentModelSources} editingModelSource={editingModelSource}
          onContinueModelSource={onContinueModelSource}
          initialSourceSha={documentView.sourceSha} initialPageIndex={documentView.pageIndex}
          initialRevisionRef={documentView.revisionRef}
          timing={documentTiming}
          sourceStageRef={designHistory?.currentStageRef}
          onBeforeLeave={onDocumentBeforeLeave}
          busy={baseActionBusy || changingBase} onSubmit={onDocumentSubmit} documentVisualInputAvailable={documentVisualInputAvailable} />
          : <div className="document-workspace document-empty">{t("document.noRun")}</div>}
      </div>}
      </div>
    </section>
  );
}
