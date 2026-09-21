import { useConnection } from "../../api/ProjectRuntimeContext";
/**
 * The model, and the few facts that sit over it.
 *
 * The viewport is the moved viewer, untouched. Around it: the camera tools, the last resolved pick, the
 * versions strip and the evidence tab. Everything here was handed in; the
 * stage decides nothing.
 */

import { createRef, useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject, type PointerEvent as ReactPointerEvent } from "react";

import type { StudioApiError } from "../../api/client";
import type { DocumentAnnotationRefDto, DocumentVisualInputDto, GestureDto, ModelSourceDto, ProjectArtifactDto, WorkingCopyDto, WorkingCopyOptionDto } from "../../api/generated";
import { ErrorBoundary } from "../../app/ErrorBoundary";
import { ErrorPanel } from "../../app/ErrorPanel";
import { designObjectLabel } from "../../app/format";
import type { EvidenceTab } from "../../app/evidence";
import { LoadingOverlay } from "../../app/LoadingOverlay";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import type { SceneInspection } from "../../workspaces/monkeyarch/viewer/sceneInspection";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
  type Vec3,
  type ModelSnap,
} from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import { Annotate, GESTURE_TOOLS, type AnnotationStyle, type GestureTool } from "../../workspaces/monkeyarch/Annotate";
import { VersionsStrip, type VersionGroup, type DesignHistoryControls } from "./VersionsStrip";
import { DocumentCanvas, type DocumentViewContext } from "../../workspaces/monkeydiagram/DocumentCanvas";
import { createDocumentAnnotationsController } from "../../workspaces/monkeydiagram/useDocumentAnnotations";
import type { ModelAnnotationsHandle } from "../../workspaces/monkeyarch/useModelAnnotations";
import { distanceBetween, type SnapConstraint } from "../../workspaces/monkeyarch/viewer/featureEdges";
import { cancelInteractionFrame, createInteractionSession, scheduleInteractionFrame } from "../../workspaces/monkeyarch/interactionSession";
import type { PushPullTarget, ScaleMode } from "../../workspaces/monkeyarch/interactionSession";
import { ModelEditPanel, type DirectModelAction, type DirectModelTool } from "./ModelEditPanel";
import { ParameterLocksPanel, type ParameterLockControls } from "./ParameterLocksPanel";
import { ElevationPanel, type ElevationControls } from "./ElevationPanel";
import { ModelToolButton } from "./ModelToolButton";
import { preparePushPull } from "./pushPull";
import type { NormalDragController } from "../../workspaces/monkeyarch/viewer/normalDrag";
import { constrainedTranslation, type TranslationConstraint } from "../../workspaces/monkeyarch/viewer/translationGizmo";
import { draftTransformCenter, previewDirectModel, specFromDrawnShape } from "./modelDraft";
import {
  IDLE as SKETCH_IDLE,
  cancelled as cancelledSketch,
  arcBulge,
  arcOf,
  circleOf,
  enclosesArea,
  finished as finishedSketch,
  heightAnchor,
  lockedPoint,
  pointFromPlane,
  pointToPlane,
  rectangleOf,
  sizedRectangle,
  sizedLine,
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

function sketchControls(state: SketchState) {
  return {
    tool: state.tool, phase: state.phase, typed: state.typed, axisLock: state.axisLock,
    hasAnchor: state.anchor !== null, canClose: enclosesArea(state.vertices),
    hasChord: state.vertices.length >= 2,
  };
}

export function Stage({
  active = true,
  onOpenBoard,
  onSendToRender,
  onChatRequest,
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
  onDocumentView,
  onReturnToBoard,
  documentAnnotationsController,
  onDocumentBeforeLeave,
  loadingSha,
  loadedShas,
  evidenceCounts,
  review,
  drawer,
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
  tracingPaperReview,
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
  parameterLocks,
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
  onDocumentView(next: DocumentViewContext): void;
  /** Present while this tab is showing a page opened from its own board. */
  onReturnToBoard?(): void;
  documentAnnotationsController: ReturnType<typeof createDocumentAnnotationsController>;
  onDocumentBeforeLeave(save: (() => Promise<void>) | null): void;
  loadingSha: string | null;
  /** The digests on screen: one seat's, or every seat of a run. */
  loadedShas: readonly string[];
  evidenceCounts: EvidenceCounts;
  review: ReviewSummary;
  /** The drawer, when it overlays the stage rather than standing beside it. */
  drawer: ReactNode;
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
  tracingPaperReview?: { enabled: boolean; busy: boolean; sent: boolean; error: string | null; onSend(): void };
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
  onSketch?(action: FinishedSketch, stillCurrent?: () => boolean): Promise<void>;
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
    onInteraction?(): void;
    sync?: { dirty: boolean; busy: boolean; error: string | null; autosave?: "saved" | "saving"; onSync(): void };
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
    directTool?: DirectModelTool | null;
    busy?: boolean;
    interactionBlocked?: boolean;
    error?: string | null;
    pushPullTarget?: PushPullTarget | null;
    pushPullReason?: string | null;
    onApply?(action: DirectModelAction): void;
    elevation?: ElevationControls | null;
  };
  parameterLocks?: ParameterLockControls;
  /** A host page already shows the workspace entries and the project's position. */
  active?: boolean;
  onOpenBoard?: () => void;
  onSendToRender?: () => void;
  onChatRequest?: () => void;
}) {
  const t = useT();
  const connection = useConnection();
  const activeRef = useRef(active);
  activeRef.current = active;
  const { developerMode, language } = usePreferences();
  const zh = language === "zh-CN";
  const [annotationStyle, setAnnotationStyle] = useState<AnnotationStyle>({ color: "#e5534b", lineWidth: 2 });
  const [annotationCancel, setAnnotationCancel] = useState(0);
  const documentOpen = documentView.open;
  const showElevationReference = useCallback((value: number | null) => viewportRef.current?.elevationGuide(value), [viewportRef]);
  useEffect(() => {
    const facts = model?.elevation?.object.elevation;
    showElevationReference(!documentOpen && !model?.directTool && facts?.baseReference ? facts.base - facts.baseReference.offset : null);
    return () => showElevationReference(null);
  }, [model?.elevation?.object, model?.directTool, documentOpen, showElevationReference]);
  const documentMounted = documentView.mounted;
  const documentRunId = documentView.runId ?? editingBaseRunId;
  const [eraser, setEraser] = useState(false);
  const [annotationToolsOpen, setAnnotationToolsOpen] = useState(false);
  const [viewToolsOpen, setViewToolsOpen] = useState(false);
  const [parameterLocksOpen, setParameterLocksOpen] = useState(false);
  const [lineToolsOpen, setLineToolsOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const stageElement = useRef<HTMLElement>(null);
  const workspaceElement = useRef<HTMLDivElement>(null);
  const toolsElement = useRef<HTMLDivElement>(null);
  const footerElement = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const stage = stageElement.current, workspace = workspaceElement.current;
    const tools = toolsElement.current, footer = footerElement.current;
    if (!stage || !workspace || !tools || !footer) return;
    // Keep the floating history above the tools, including wrapped rows and
    // larger fonts, without resizing the model canvas or changing its camera.
    const properties = [
      ["--stage-toolbar-height", tools],
      ["--stage-footer-height", footer],
      ["--stage-workspace-height", workspace],
    ] as const;
    const measure = () => {
      for (const [name, element] of properties) {
        const value = `${Math.ceil(element.getBoundingClientRect().height)}px`;
        if (stage.style.getPropertyValue(name) !== value) stage.style.setProperty(name, value);
      }
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    for (const [, element] of properties) observer?.observe(element);
    return () => {
      observer?.disconnect();
      for (const [name] of properties) stage.style.removeProperty(name);
    };
  }, []);
  // One drawing action at a time, entirely local until it is finished.
  const [sketch, setSketch] = useState(() => sketchControls(SKETCH_IDLE));
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
    viewportRef.current?.clearSnap();
    showMeasure({ from: null, to: null, kinds: [null, null] });
    setSnapNote(null);
  }, [showMeasure, viewportRef]);
  const interaction = useRef(createInteractionSession());
  const sketchEpoch = useRef(0);
  const modelKeysRef = useRef(model);
  modelKeysRef.current = model;
  const pushPullInput = useRef<HTMLInputElement>(null);
  const pushPullNeedsFace = useRef(false);
  const [pushPullActive, setPushPullActive] = useState(false);
  const [pushPullGuide, setPushPullGuide] = useState<NormalDragController | null>(null);
  const [pushPullError, setPushPullError] = useState<string | null>(null);
  const pushPullErrorRef = useRef<string | null>(null);
  const reportPushPullError = useCallback((message: string | null) => {
    if (pushPullErrorRef.current === message) return;
    pushPullErrorRef.current = message;
    setPushPullError(message);
  }, []);
  const stopPushPull = useCallback(() => {
    cancelInteractionFrame(interaction.current, "pushPull");
    if (interaction.current.pushPull) viewportRef.current?.sketchPreview(null);
    interaction.current.pushPull = null;
    viewportRef.current?.clearSnap();
    setPushPullActive(false);
    setPushPullGuide(null);
    reportPushPullError(null);
  }, [reportPushPullError, viewportRef]);
  // Keep the gesture bound to the source visible when it was armed. A render
  // may publish a new base before passive effects run; completion must refuse
  // the old gesture synchronously, not rely on a later visual cleanup.
  const directSourceKey = JSON.stringify([editingBaseRunId, loadedRunId,
    editingModelSource?.runId, editingModelSource?.stateDigest, editingModelSource?.assetSha256,
    viewedModelSource?.runId, viewedModelSource?.stateDigest, viewedModelSource?.assetSha256]);
  const directAvailable = !documentOpen && !changingBase && !baseActionBusy && loadingSha === null &&
    status !== "loading" && status !== "error" && !model?.interactionBlocked && !model?.busy;
  const directContextRef = useRef({ sourceKey: directSourceKey, available: directAvailable });
  directContextRef.current = { sourceKey: directSourceKey, available: directAvailable };
  const [moveInputs] = useState(() => [createRef<HTMLInputElement>(), createRef<HTMLInputElement>(), createRef<HTMLInputElement>()]);
  const [movePhase, setMovePhase] = useState<"anchor" | "target" | null>(null);
  const [moveError, setMoveError] = useState<string | null>(null);
  const [moveConstraint, setMoveConstraint] = useState<TranslationConstraint | null>(null);
  const stopMove = useCallback(() => {
    cancelInteractionFrame(interaction.current, "move");
    if (interaction.current.move) {
      viewportRef.current?.translationGizmo(null);
      viewportRef.current?.sketchPreview(null);
    }
    interaction.current.move = null;
    viewportRef.current?.clearSnap();
    interaction.current.planeSnap = null;
    setMovePhase(null); setMoveError(null); setMoveConstraint(null);
  }, [viewportRef]);
  const rotateInput = useRef<HTMLInputElement>(null);
  const [rotateAxis, setRotateAxis] = useState<"x" | "y" | "z">("z");
  const [rotatePhase, setRotatePhase] = useState<"reference" | "angle" | null>(null);
  const [rotateError, setRotateError] = useState<string | null>(null);
  const stopRotate = useCallback(() => {
    cancelInteractionFrame(interaction.current, "rotate");
    if (interaction.current.rotate) viewportRef.current?.sketchPreview(null);
    interaction.current.rotate = null;
    setRotatePhase(null); setRotateError(null);
  }, [viewportRef]);
  const [scaleInputs] = useState(() => [createRef<HTMLInputElement>(), createRef<HTMLInputElement>(), createRef<HTMLInputElement>()]);
  const [scaleMode, setScaleMode] = useState<ScaleMode>("uniform");
  const [scalePhase, setScalePhase] = useState<"reference" | "factor" | null>(null);
  const [scaleError, setScaleError] = useState<string | null>(null);
  const stopScale = useCallback(() => {
    cancelInteractionFrame(interaction.current, "scale");
    if (interaction.current.scale) viewportRef.current?.sketchPreview(null);
    interaction.current.scale = null;
    setScalePhase(null); setScaleError(null);
  }, [viewportRef]);
  const closeDirectTool = useCallback(() => {
    stopPushPull();
    stopMove();
    stopRotate();
    stopScale();
    modelKeysRef.current?.onTool?.("select");
  }, [stopPushPull, stopMove, stopRotate, stopScale]);
  const paintScale = useCallback(() => {
    const current = interaction.current.scale;
    if (!current) return;
    const factors = current.typed === null ? current.scale : current.typed.map(Number) as Vec3;
    scaleInputs.forEach((input, i) => {
      const value = current.typed?.[i] ?? (factors === null ? "" : String(Number(factors[i]!.toFixed(4))));
      // Preserve the browser's partial numeric entry (for example a leading -).
      if (input.current && input.current.value !== value) input.current.value = value;
    });
    if (factors === null) { viewportRef.current?.sketchPreview(null); return; }
    try {
      if (current.typed?.some(value => !value.trim())) throw new Error("Enter finite, nonzero scale factors.");
      viewportRef.current?.sketchPreview(previewDirectModel({ spec: current.spec,
        parameterBoundFields: current.target.shape.parameterBoundFields }, { kind: "scale", scale: factors }));
      setScaleError(null);
    } catch (error) {
      viewportRef.current?.sketchPreview(null);
      setScaleError(error instanceof Error ? error.message : String(error));
    }
  }, [scaleInputs, viewportRef]);
  const commitScale = useCallback(() => {
    const current = interaction.current.scale, keys = modelKeysRef.current;
    if (!current || !keys?.onApply || keys.busy || keys.directTool !== "scale" || keys.pushPullTarget !== current.target) return;
    if (!current.reference && current.typed === null) return;
    const factors = current.typed === null ? current.scale : current.typed.map(Number) as Vec3;
    if (!factors || factors.some(value => !Number.isFinite(value) || Math.abs(value) < 1e-9) ||
        factors.every(value => Math.abs(value - 1) < 1e-9) || current.typed?.some(value => !value.trim())) return;
    try {
      previewDirectModel({ spec: current.spec, parameterBoundFields: current.target.shape.parameterBoundFields }, { kind: "scale", scale: factors });
      stopScale();
      keys.onApply({ kind: "scale", scale: factors, target: current.target });
      keys.onTool?.("select");
    } catch (error) { setScaleError(error instanceof Error ? error.message : String(error)); }
  }, [stopScale]);
  const changeScaleMode = useCallback((mode: ScaleMode) => {
    const current = interaction.current.scale;
    setScaleMode(mode);
    if (!current) return;
    cancelInteractionFrame(interaction.current, "scale");
    const basis = mode === "uniform" ? viewportRef.current?.workPlaneFromSelection() ?? current.spec.plane!
      : mode === "z" ? WORK_PLANES.yz : WORK_PLANES.xy;
    current.mode = mode; current.plane = { ...basis, origin: draftTransformCenter(current.spec) };
    current.reference = null; current.scale = [1, 1, 1];
    if (mode === "uniform" && current.typed) current.typed = [current.typed[0], current.typed[0], current.typed[0]];
    interaction.current.pointer = null;
    setScalePhase("reference"); setScaleError(null);
    if (current.typed !== null) paintScale();
    else { viewportRef.current?.sketchPreview(null); scaleInputs.forEach(input => { if (input.current) input.current.value = "1"; }); }
  }, [paintScale, scaleInputs, viewportRef]);
  useEffect(() => {
    stopScale(); setScaleMode("uniform");
    if (model?.directTool !== "scale" || documentOpen || model.interactionBlocked) return;
    const target = model.pushPullTarget;
    if (!target) { setScaleError("Select a drawn solid or face before scaling."); return; }
    const spec = specFromDrawnShape(target.shape);
    const basis = viewportRef.current?.workPlaneFromSelection() ?? spec.plane!;
    interaction.current.scale = { target, spec, mode: "uniform", plane: { ...basis, origin: draftTransformCenter(spec) },
      reference: null, centerTolerance: 1e-9, scale: [1, 1, 1], typed: null };
    interaction.current.pointer = null;
    scaleInputs.forEach(input => { if (input.current) input.current.value = "1"; });
    setScalePhase("reference");
    return stopScale;
  }, [model?.directTool, model?.pushPullTarget, model?.interactionBlocked, documentOpen, scaleInputs, stopScale, viewportRef]);
  useEffect(() => {
    if (model?.directTool !== "scale") return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
      if (event.defaultPrevented || event.repeat || documentOpen) return;
      if (event.key === "Escape") { event.preventDefault(); closeDirectTool(); return; }
      if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]") ||
          (target.closest("button, a") && !target.closest('button[data-model-tool="scale"]')))) return;
      event.preventDefault(); commitScale();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [model?.directTool, documentOpen, closeDirectTool, commitScale]);
  const paintRotate = useCallback(() => {
    const current = interaction.current.rotate;
    if (!current) return;
    const angleDegrees = current.typed === null ? current.angleDegrees : Number(current.typed);
    if (current.typed === null && rotateInput.current) rotateInput.current.value = angleDegrees === null ? "" : String(Number(angleDegrees.toFixed(4)));
    if (angleDegrees === null) { viewportRef.current?.sketchPreview(null); return; }
    try {
      if (current.typed !== null && !current.typed.trim()) throw new Error("Enter a finite rotation angle.");
      viewportRef.current?.sketchPreview(previewDirectModel({ spec: current.spec,
        parameterBoundFields: current.target.shape.parameterBoundFields }, { kind: "rotate", angleDegrees, axis: [...current.plane.normal] }));
      setRotateError(null);
    } catch (error) {
      viewportRef.current?.sketchPreview(null);
      setRotateError(error instanceof Error ? error.message : String(error));
    }
  }, [viewportRef]);
  const commitRotate = useCallback(() => {
    const current = interaction.current.rotate, keys = modelKeysRef.current;
    if (!current || !keys?.onApply || keys.busy || keys.directTool !== "rotate" || keys.pushPullTarget !== current.target) return;
    if (!current.reference && current.typed === null) return;
    const angleDegrees = current.typed === null ? current.angleDegrees : Number(current.typed);
    if (angleDegrees === null || !Number.isFinite(angleDegrees) || Math.abs(angleDegrees) < 1e-9 || (current.typed !== null && !current.typed.trim())) return;
    try {
      const action = { kind: "rotate" as const, angleDegrees, axis: [...current.plane.normal] as Vec3 };
      previewDirectModel({ spec: current.spec, parameterBoundFields: current.target.shape.parameterBoundFields }, action);
      stopRotate();
      keys.onApply({ ...action, target: current.target });
      keys.onTool?.("select");
    } catch (error) { setRotateError(error instanceof Error ? error.message : String(error)); }
  }, [stopRotate]);
  const changeRotateAxis = useCallback((axis: "x" | "y" | "z") => {
    const current = interaction.current.rotate;
    setRotateAxis(axis);
    if (!current) return;
    cancelInteractionFrame(interaction.current, "rotate");
    // U cross V must point along the positive chosen world axis. The drawing
    // XZ plane faces -Y, so rotation around +Y uses -Z for its V direction.
    const basis = axis === "x" ? WORK_PLANES.yz : axis === "z" ? WORK_PLANES.xy
      : { xAxis: [1, 0, 0] as const, yAxis: [0, 0, -1] as const, normal: [0, 1, 0] as const };
    current.plane = { ...basis, origin: draftTransformCenter(current.spec) };
    current.reference = null; current.angleDegrees = 0;
    interaction.current.pointer = null;
    setRotatePhase("reference"); setRotateError(null);
    if (current.typed !== null) paintRotate();
    else { viewportRef.current?.sketchPreview(null); if (rotateInput.current) rotateInput.current.value = "0"; }
  }, [paintRotate, viewportRef]);
  useEffect(() => {
    stopRotate(); setRotateAxis("z");
    if (model?.directTool !== "rotate" || documentOpen || model.interactionBlocked) return;
    const target = model.pushPullTarget;
    if (!target) { setRotateError("Select a drawn solid or face before rotating."); return; }
    const spec = specFromDrawnShape(target.shape);
    interaction.current.rotate = { target, spec, plane: { ...WORK_PLANES.xy, origin: draftTransformCenter(spec) }, reference: null, centerTolerance: 1e-9, angleDegrees: 0, typed: null };
    interaction.current.pointer = null;
    if (rotateInput.current) rotateInput.current.value = "0";
    setRotatePhase("reference");
    return stopRotate;
  }, [model?.directTool, model?.pushPullTarget, model?.interactionBlocked, documentOpen, stopRotate]);
  useEffect(() => {
    if (model?.directTool !== "rotate") return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
      if (event.defaultPrevented || event.repeat || documentOpen) return;
      if (event.key === "Escape") { event.preventDefault(); closeDirectTool(); return; }
      if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]") ||
          (target.closest("button, a") && !target.closest('button[data-model-tool="rotate"]')))) return;
      event.preventDefault(); commitRotate();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [model?.directTool, documentOpen, closeDirectTool, commitRotate]);
  const paintMove = useCallback(() => {
    const current = interaction.current.move;
    if (!current || !current.constraint) return;
    const values = current.typed ? current.typed.map(Number) : current.translation;
    try {
      if (current.typed?.some(value => !value.trim())) throw new Error("Enter finite distances in metres.");
      const translation = constrainedTranslation(values, current.constraint);
      // Keep disabled coordinates visibly zero, including after switching a
      // handle; typed values and the visible preview have the same constraint.
      moveInputs.forEach((input, i) => {
        if (input.current && (!current.typed || !current.constraint!.includes("XYZ"[i]!)))
          input.current.value = String(Number(translation[i]!.toFixed(4)));
      });
      viewportRef.current?.sketchPreview(previewDirectModel({ spec: current.spec,
        parameterBoundFields: current.target.shape.parameterBoundFields }, { kind: current.tool, translation }));
      viewportRef.current?.translationGizmo({ origin: current.origin, translation, constraint: current.constraint });
      setMoveError(null);
    } catch (error) {
      viewportRef.current?.sketchPreview(null);
      setMoveError(error instanceof Error ? error.message : String(error));
    }
  }, [moveInputs, viewportRef]);
  const commitMove = useCallback(() => {
    const current = interaction.current.move, keys = modelKeysRef.current;
    if (!current || !current.constraint || !keys?.onApply) return;
    if (!directContextRef.current.available || current.sourceKey !== directContextRef.current.sourceKey ||
        keys.directTool !== current.tool || keys.pushPullTarget !== current.target) {
      closeDirectTool(); return;
    }
    if (current.typed?.some(value => !value.trim())) return;
    try {
      const translation = constrainedTranslation(current.typed ? current.typed.map(Number) : current.translation, current.constraint);
      if (current.tool === "move" && Math.hypot(...translation) < 1e-9) return;
      previewDirectModel({ spec: current.spec, parameterBoundFields: current.target.shape.parameterBoundFields }, { kind: current.tool, translation });
      // Clear the gesture before handing over: Enter, submit or a later mouse
      // release cannot apply the same action twice.
      stopMove();
      keys.onApply({ kind: current.tool, translation, target: current.target });
      keys.onTool?.("select");
    } catch (error) { setMoveError(error instanceof Error ? error.message : String(error)); }
  }, [closeDirectTool, stopMove]);
  const changeMoveConstraint = useCallback((constraint: TranslationConstraint) => {
    const current = interaction.current.move;
    if (!current) return;
    cancelInteractionFrame(interaction.current, "move");
    viewportRef.current?.translationPointer("end", 0, 0);
    current.constraint = constraint; current.pointerId = null;
    current.translation = [0, 0, 0]; current.typed = null;
    viewportRef.current?.clearSnap();
    setMoveConstraint(constraint); setMovePhase("target");
    paintMove();
  }, [paintMove, viewportRef]);
  useLayoutEffect(() => {
    stopMove();
    if ((model?.directTool !== "move" && model?.directTool !== "copy") || !directAvailable) return;
    const target = model.pushPullTarget;
    if (!target) { setMoveError("Select a drawn solid or face before moving or copying."); return; }
    const spec = specFromDrawnShape(target.shape), origin = draftTransformCenter(spec);
    interaction.current.move = { target, tool: model.directTool, sourceKey: directSourceKey, spec, origin, constraint: null,
      pointerId: null, translation: [0, 0, 0], typed: null };
    interaction.current.pointer = null;
    moveInputs.forEach(input => { if (input.current) input.current.value = "0"; });
    viewportRef.current?.translationGizmo({ origin, translation: [0, 0, 0], constraint: null });
    setMovePhase("anchor");
    return stopMove;
  }, [model?.directTool, model?.pushPullTarget, directAvailable, directSourceKey, moveInputs, stopMove, viewportRef]);
  useEffect(() => {
    if (model?.directTool !== "move" && model?.directTool !== "copy") return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
      if (event.defaultPrevented || event.repeat || documentOpen) return;
      if (event.key === "Escape") { event.preventDefault(); closeDirectTool(); return; }
      if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]") ||
          (target.closest("button, a") && !target.closest('button[data-model-tool="move"], button[data-model-tool="copy"]')))) return;
      event.preventDefault(); commitMove();
    };
    const blur = () => { if (interaction.current.move?.pointerId != null) closeDirectTool(); };
    window.addEventListener("keydown", listen); window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", listen); window.removeEventListener("blur", blur); };
  }, [model?.directTool, documentOpen, closeDirectTool, commitMove]);
  const paintPushPull = useCallback(() => {
    const current = interaction.current.pushPull;
    if (!current) return;
    if (!directContextRef.current.available || current.sourceKey !== directContextRef.current.sourceKey || !current.constraint.isCurrent()) {
      closeDirectTool(); return;
    }
    const value = current.typed === null ? current.distance : Number(current.typed);
    if (current.typed === null && pushPullInput.current) pushPullInput.current.value = String(Number(value.toFixed(4)));
    try {
      viewportRef.current?.sketchPreview(current.prepared.preview(value));
      reportPushPullError(null);
    } catch (error) {
      viewportRef.current?.sketchPreview(null);
      reportPushPullError(error instanceof Error ? error.message : String(error));
    }
  }, [closeDirectTool, reportPushPullError, viewportRef]);
  const commitPushPull = useCallback((distance?: number) => {
    const current = interaction.current.pushPull;
    const keys = modelKeysRef.current;
    if (!current || !keys?.onApply) return;
    if (!directContextRef.current.available || current.sourceKey !== directContextRef.current.sourceKey || !current.constraint.isCurrent()
        || keys.directTool !== "pushPull" || current.target !== keys.pushPullTarget) { closeDirectTool(); return; }
    if (current.typed !== null && !current.typed.trim()) return;
    const value = distance ?? (current.typed === null ? current.distance : Number(current.typed));
    try {
      if (!Number.isFinite(value) || Math.abs(value) < 1e-9 || !current.prepared.preview(value)) return;
      // Dispose synchronously: a second click/Enter cannot submit this gesture again.
      stopPushPull();
      pushPullNeedsFace.current = true;
      keys.onApply({ kind: "pushPull", distance: value, normal: current.face.normal, target: current.target });
    } catch (error) {
      reportPushPullError(error instanceof Error ? error.message : String(error));
    }
  }, [closeDirectTool, reportPushPullError, stopPushPull]);
  useLayoutEffect(() => {
    stopPushPull();
    if (model?.directTool !== "pushPull") pushPullNeedsFace.current = false;
    if (pushPullNeedsFace.current) return;
    if (model?.directTool !== "pushPull" || !model.pushPullTarget || !directAvailable) return;
    const face = viewportRef.current?.workPlaneFromSelection();
    if (!face) return;
    try {
      const constraint = viewportRef.current?.beginNormalDrag([...face.origin], [...face.normal]);
      if (!constraint) return;
      const capturedFace = { ...face, origin: [...face.origin] as [number, number, number], normal: constraint.normal };
      const prepared = preparePushPull(model.pushPullTarget.shape, capturedFace.normal);
      interaction.current.pushPull = { target: model.pushPullTarget, sourceKey: directSourceKey,
        face: capturedFace, constraint, prepared, distance: 0, typed: null };
      setPushPullGuide(constraint);
      if (pushPullInput.current) pushPullInput.current.value = "0";
      setPushPullActive(true);
    } catch (error) {
      reportPushPullError(error instanceof Error ? error.message : String(error));
    }
    return stopPushPull;
  }, [model?.directTool, model?.pushPullTarget, directAvailable, directSourceKey, reportPushPullError, stopPushPull, viewportRef]);
  useEffect(() => {
    if (model?.directTool !== "pushPull") return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
      if (event.defaultPrevented || event.repeat) return;
      if (event.key === "Escape") { event.preventDefault(); closeDirectTool(); return; }
      if (event.key !== "Enter" || event.ctrlKey || event.metaKey || event.altKey || documentOpen) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.closest("input, textarea, select, [contenteditable]") ||
          (target.closest("button, a") && !target.closest('button[data-model-tool="pushPull"]')))) return;
      event.preventDefault(); commitPushPull();
    };
    const blur = () => { if (interaction.current.pushPull) closeDirectTool(); };
    window.addEventListener("keydown", listen); window.addEventListener("blur", blur);
    return () => { window.removeEventListener("keydown", listen); window.removeEventListener("blur", blur); };
  }, [closeDirectTool, commitPushPull, documentOpen, model?.directTool]);
  // How far a snap reaches, in world units: a fifth of what the last drawn
  // rectangle spans, so it stays usable at any size the project is drawn at.
  const snapRadius = useCallback(() => {
    const sketch = interaction.current.sketch;
    const spans = sketch.profile.length > 1
      ? Math.max(...sketch.profile.map(([x]) => x)) - Math.min(...sketch.profile.map(([x]) => x))
      : 0;
    return Math.max(0.2, spans / 5);
  }, []);
  const snapForSketch = useCallback((x: number, y: number, shift: boolean) => {
    const current = interaction.current.sketch;
    if (current.typed.trim()) { viewportRef.current?.clearSnap(); return null; }
    const plane = current.plane ?? { ...WORK_PLANES.xy, origin: [0, 0, current.base] as Vec3 };
    let constraint: SnapConstraint = { kind: "plane", origin: plane.origin, direction: plane.normal };
    const anchor = current.vertices.at(-1) ?? current.anchor;
    if (current.phase === "height") {
      const at = heightAnchor(current);
      if (at) constraint = { kind: "axis", origin: pointFromPlane(at, current.plane, current.base), direction: plane.normal };
    } else if (anchor) {
      const raw = viewportRef.current?.pointOnSketchPlane(x, y, plane);
      const point = raw && pointToPlane(raw, current.plane);
      const axis = current.axisLock ?? (shift && point ? Math.abs(point[0] - anchor[0]) >= Math.abs(point[1] - anchor[1]) ? "x" : "y" : null);
      if (axis) constraint = { kind: "axis", origin: pointFromPlane(anchor, current.plane, current.base), direction: axis === "x" ? plane.xAxis : plane.yAxis };
    }
    return viewportRef.current?.snapOnModel(x, y, 14, { constraint }) ?? null;
  }, [viewportRef]);
  const planCursor = useCallback((current: SketchState, world: Vec3, onModel: ModelSnap | null, shift: boolean) => {
    const anchor = current.vertices.at(-1) ?? current.anchor;
    const moved = onModel ? { point: pointToPlane(world, current.plane), snapped: { kind: onModel.kind } }
      : snapPoint(pointToPlane(world, current.plane), { endpoints: current.plane ? current.vertices : [...snapPoints, ...current.vertices],
        anchor, radius: snapRadius(), previous: interaction.current.planeSnap });
    interaction.current.planeSnap = moved.snapped?.kind === "axis" ? moved.snapped : null;
    const shiftAxis = shift && anchor ? Math.abs(moved.point[0] - anchor[0]) >= Math.abs(moved.point[1] - anchor[1]) ? "x" : "y" : null;
    return { ...moved, point: anchor ? lockedPoint(moved.point, anchor, current.axisLock ?? shiftAxis) : moved.point };
  }, [snapPoints, snapRadius]);
  const sketchAtPoint = (current: SketchState, point: PlanPoint): SketchState => {
    if (!current.anchor) return current;
    const profile = current.tool === "circle" ? circleOf(current.anchor, Math.hypot(point[0] - current.anchor[0], point[1] - current.anchor[1]))
      : current.tool === "arc" ? current.vertices.length < 2 ? [current.anchor, point]
        : arcOf(current.anchor, current.vertices[1]!, arcBulge(current.anchor, current.vertices[1]!, point))
      : current.tool === "polygon" || current.tool === "line" ? [...current.vertices, point] : rectangleOf(current.anchor, point);
    return { ...current, profile, cursor: point };
  };
  const paintSketch = useCallback(() => {
    const next = interaction.current.sketch;
    viewportRef.current?.sketchPreview(
      next.phase !== "idle" ? { profile: next.profile, base: next.base, height: next.height,
        closed: next.phase === "height" || (next.tool !== "line" && next.tool !== "freehand" && next.tool !== "arc"),
        ...(next.plane ? { plane: next.plane } : {}) } : null,
    );
  }, [viewportRef]);
  const cancelSketchFrame = useCallback(() => {
    cancelInteractionFrame(interaction.current);
  }, []);
  const showSketch = useCallback((next: SketchState, pointerMove = false) => {
    // Geometry lives in this one disposable session. React only sees controls,
    // and must never copy an older UI snapshot back over the latest pointer.
    if (!pointerMove) {
      viewportRef.current?.clearSnap();
      interaction.current.planeSnap = null;
    }
    interaction.current.sketch = next;
    if (next.phase === "idle") interaction.current.press = null;
    if (pointerMove) {
      scheduleInteractionFrame(interaction.current, "sketch", paintSketch);
    } else {
      cancelSketchFrame();
      setSketch(sketchControls(next));
      paintSketch();
    }
  }, [cancelSketchFrame, paintSketch, viewportRef]);
  const stopSketching = useCallback(() => {
    sketchEpoch.current += 1;
    setSnapNote(null);
    showSketch(cancelledSketch(interaction.current.sketch));
  }, [showSketch]);
  const submitSketch = useCallback((allowFlat = false, closed = true) => {
    const action = finishedSketch(interaction.current.sketch, allowFlat, closed);
    if (action === null || !onSketch) return;
    stopSketching();
    const epoch = sketchEpoch.current;
    void onSketch(action, () => sketchEpoch.current === epoch);
  }, [onSketch, stopSketching]);
  const chooseDrawingTool = useCallback((next: SketchTool | null) => {
    sketchEpoch.current += 1;
    setLineToolsOpen(false);
    stopPushPull();
    stopMove();
    stopRotate();
    stopScale();
    setAnnotationToolsOpen(false); setViewToolsOpen(false); setVersionsOpen(false); setParameterLocksOpen(false);
    onTool(null); setEraser(false); setMeasuring(false); stopMeasuring();
    model?.onTool?.("select");
    showSketch({ ...cancelledSketch(interaction.current.sketch), tool: next });
  }, [onTool, model?.onTool, showSketch, stopMeasuring, stopPushPull, stopMove, stopRotate, stopScale]);
  const chooseMeasure = useCallback(() => {
    chooseDrawingTool(null);
    setMeasuring(true);
  }, [chooseDrawingTool]);
  const choosePlane = useCallback((name: "xy" | "xz" | "yz" | "face") => {
    sketchEpoch.current += 1;
    closeDirectTool();
    const plane: SketchPlane | null = name === "face"
      ? viewportRef.current?.workPlaneFromSelection() ?? null : WORK_PLANES[name];
    if (!plane) return;
    setWorkPlaneName(name); setSnapNote(null);
    showSketch({ ...cancelledSketch(interaction.current.sketch), plane: name === "xy" ? null : plane, base: 0 });
  }, [closeDirectTool, showSketch, viewportRef]);
  const closePolygon = useCallback(() => {
    const current = interaction.current.sketch;
    if (!(current.tool === "polygon" || current.tool === "line" || current.tool === "freehand") || current.phase !== "profile" || !enclosesArea(current.vertices)) return;
    showSketch({ ...current, phase: "height", profile: current.vertices,
      cursor: current.tool === "polygon" ? current.vertices.at(-1) ?? current.anchor : current.anchor, typed: "" });
  }, [showSketch]);
  const finishPath = useCallback(() => {
    const current = interaction.current.sketch;
    if (current.phase !== "profile") return;
    if (current.tool === "line" || current.tool === "freehand") {
      showSketch({ ...current, profile: current.vertices });
    } else if (current.tool !== "arc" || current.vertices.length < 2 || current.profile.length < 3 ||
        (current.typed.trim() && typedNumber(current.typed) === null)) return;
    submitSketch(true, false);
  }, [showSketch, submitSketch]);
  // Esc belongs to the whole action, wherever the focus is.
  useEffect(() => {
    if (!measuring) return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
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
      if (!activeRef.current) return;
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (interaction.current.sketch.phase === "idle") { sketchEpoch.current += 1; showSketch({ ...cancelledSketch(interaction.current.sketch), tool: null }); }
      else stopSketching();
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [sketch.tool, showSketch, stopSketching]);
  useEffect(() => () => {
    sketchEpoch.current += 1;
    cancelSketchFrame();
    viewportRef.current?.sketchPreview(null);
  }, [cancelSketchFrame, viewportRef]);

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
  const modelKeysAvailable = model !== undefined;
  // What is *in progress*, which is not the same as what is armed.
  const actionInProgressRef = useRef(false);
  const actionInProgress = sketch.phase !== "idle" || (measuring && measure.from !== null) || pushPullActive || movePhase !== null || rotatePhase !== null || scalePhase !== null;
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
      if (!activeRef.current) return;
      // Disabling Sync can leave focus on body; those global tool shortcuts
      // still count as input before a background model may replace this one.
      if (!(event.target instanceof Element) || !event.target.closest(".stage")) modelKeysRef.current?.onInteraction?.();
      if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.repeat || documentOpenRef.current) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || target.closest("input, textarea, select, [contenteditable]"))) return;
      const key = event.key.toLowerCase();
      const current = interaction.current.sketch;
      if (current.phase === "profile" && (key === "arrowright" || key === "arrowleft" || key === "arrowup" || key === "arrowdown")) {
        event.preventDefault();
        const axis = key === "arrowright" || key === "arrowleft" ? "x" : "y";
        showSketch({ ...current, axisLock: current.axisLock === axis ? null : axis });
        return;
      }
      if (key === "enter" && current.tool === "polygon" && current.phase === "profile") {
        event.preventDefault(); closePolygon(); return;
      }
      if (key === "enter" && (current.tool === "line" || current.tool === "arc") && current.phase === "profile") {
        event.preventDefault(); finishPath(); return;
      }
      if (key === " " && !(target instanceof HTMLElement && target.closest("button, a"))) {
        event.preventDefault(); chooseDrawingTool(null); return;
      }
      if (inkRef.current.busy || (actionInProgressRef.current && !interaction.current.pushPull)) return;
      if (onSketch && (key === "r" || key === "c" || key === "l" || key === "a")) {
        event.preventDefault(); chooseDrawingTool(key === "r" ? "rectangle" : key === "c" ? "circle" : key === "a" ? "arc" : "line"); return;
      }
      if (key === "t") { event.preventDefault(); chooseMeasure(); return; }
      const tool = ({ p: "pushPull", m: "move", q: "rotate", s: "scale" } as const)[key as "p" | "m" | "q" | "s"];
      if (tool && modelKeysRef.current?.onTool) {
        event.preventDefault(); chooseDrawingTool(null); modelKeysRef.current.onTool(tool);
      }
    };
    window.addEventListener("keydown", listen);
    return () => window.removeEventListener("keydown", listen);
  }, [chooseDrawingTool, chooseMeasure, closePolygon, finishPath, onSketch, showSketch]);
  useEffect(() => {
    if (!modelKeysAvailable) return;
    const listen = (event: KeyboardEvent) => {
      if (!activeRef.current) return;
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
  const contextLabel = designHistory?.workingDraft?.current?.runId === loadedRunId ? "当前工作草稿" : designHistory ? acceptedStage?.label ?? (designHistory.candidates.some((candidate) => candidate.modelSource.runId === loadedRunId)
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
    <section ref={stageElement} className="stage" data-footer="true" aria-label={t("stage.ariaLabel")}
      onPointerDownCapture={() => modelKeysRef.current?.onInteraction?.()}
      onKeyDownCapture={() => modelKeysRef.current?.onInteraction?.()}>
      {(picked !== null || onReturnToBoard || onSendToRender) && <div className="stage-mode-switch" role="group" aria-label={t("workspace.switcher")}>
        {onSendToRender && <button type="button" onClick={onSendToRender}>{zh ? "发送到渲染" : "Send to Render"}</button>}
        {(onReturnToBoard || onOpenBoard) && <button type="button" onClick={onReturnToBoard ?? onOpenBoard}>{t("workspace.monkeyboard")}</button>}
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
      <div ref={workspaceElement} className="stage-workspace">
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
          interaction={interaction}
          hoverEnabled={sketch.tool === null && !measuring && !pushPullActive && movePhase === null && rotatePhase === null && scalePhase === null && tool === null && !documentOpen && !sketchBusy}
          onInspection={onInspection}
          onStatus={onStatus}
          onRequestFile={onRequestFile}
          onOpenFile={onOpenFile}
          onSource={(label) => {
            // A new picture invalidates anchors and face planes. Keep the
            // chosen tool armed so another shape can follow a completed one.
            stopPushPull();
            stopMove();
            stopRotate();
            stopScale();
            sketchEpoch.current += 1;
            showSketch({ ...SKETCH_IDLE, tool: interaction.current.sketch.tool }); setWorkPlaneName("xy");
            stopMeasuring(); setMeasuring(false);
            onSource(label);
          }}
          onPick={(pick) => { pushPullNeedsFace.current = false; onPick(pick); }}
          idle={hasModel ? undefined : (
            <div className="stage-empty">
              <svg className="stage-empty__icon" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" aria-hidden="true">
                <path d="m16 3 12 7v13l-12 7L4 23V10Z M4 10l12 7 12-7 M16 17v13" />
              </svg>
              <h2>{t("stage.empty.title")}</h2>
              <p>{t(Boolean(onChatRequest) ? "stage.empty.bodyEmbedded" : "stage.empty.body")}</p>
              <div className="stage-empty__actions">
                {home !== null && <button type="button" className="btn"
                  title={home.kind === "reference" ? t("stage.tools.referenceShow", { runId: home.runId })
                      : t("stage.tools.homeShow", { runId: home.runId })}
                  onClick={onShowHome}>
                  {home?.kind === "fallback" ? t("stage.empty.openHome") : t("stage.empty.openReference")}
                </button>}
                {versionCount > 0 && <button type="button" className="btn"
                  onClick={() => { onVersionsOpen?.(); setVersionsOpen(true); }}>
                  {t("stage.empty.chooseVersion", { count: versionCount })}
                </button>}
                {Boolean(onChatRequest) && <button type="button" className="btn"
                  onClick={onChatRequest}>
                  {t("stage.empty.startInChat")}
                </button>}
                <button type="button" className="btn" onClick={onRequestFile}>{t("stage.empty.openLocal")}</button>
              </div>
              <p className="stage-empty__hint">{t("stage.empty.hint")}</p>
            </div>
          )}
        />
      </ErrorBoundary>
      {!documentOpen && !model?.directTool && !sketch.tool && !tool && model?.elevation &&
        <ElevationPanel key={model.elevation.object.elementId} controls={model.elevation}
          busy={model.busy ?? false} error={model.error ?? null} onReference={showElevationReference} />}
      {scalePhase !== null && <div className="stage-sketch stage-scale" data-phase={scalePhase}
        onPointerDown={(event) => {
          if (event.button === 1 || event.button === 2) { closeDirectTool(); transferNavigation(event); }
        }}
        onPointerMove={(event) => {
          const current = interaction.current.scale;
          if (!current || current.typed !== null || event.buttons) return;
          interaction.current.pointer = { x: event.clientX, y: event.clientY };
          if (!current.reference) return;
          const world = viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, current.plane);
          const delta = world?.map((value, i) => value - current.plane.origin[i]!) as Vec3 | undefined;
          const axis = current.mode === "uniform" ? null : { x: 0, y: 1, z: 2 }[current.mode];
          const referenceLength = axis === null ? Math.hypot(...current.reference) : current.reference[axis]!;
          const distance = !delta ? null : axis === null
            ? delta.reduce((sum, value, i) => sum + value * current.reference![i]!, 0) / referenceLength : delta[axis]!;
          if (distance === null || Math.abs(distance) <= current.centerTolerance) current.scale = null;
          else {
            const factor = distance / referenceLength;
            current.scale = axis === null ? [factor, factor, factor] : [1, 1, 1];
            if (axis !== null) current.scale[axis] = factor;
          }
          scheduleInteractionFrame(interaction.current, "scale", paintScale);
        }}
        onClick={(event) => {
          if (event.button !== 0) return;
          const current = interaction.current.scale;
          if (!current) return;
          if (current.typed === null && !current.reference) {
            const pointer = interaction.current.pointer ?? { x: event.clientX, y: event.clientY };
            const world = viewportRef.current?.pointOnSketchPlane(pointer.x, pointer.y, current.plane);
            if (!world) return;
            const reference = world.map((value, i) => value - current.plane.origin[i]!) as Vec3;
            const neighbours = [[pointer.x + 1, pointer.y], [pointer.x, pointer.y + 1]]
              .map(([x, y]) => viewportRef.current?.pointOnSketchPlane(x!, y!, current.plane));
            current.centerTolerance = Math.max(1e-9, ...neighbours.map(neighbour => neighbour
              ? Math.hypot(...neighbour.map((value, i) => value - world[i]!)) : 0));
            const referenceLength = current.mode === "uniform" ? Math.hypot(...reference) : Math.abs(reference[{ x: 0, y: 1, z: 2 }[current.mode]]!);
            if (referenceLength <= current.centerTolerance) return;
            current.reference = reference;
            setScalePhase("factor"); return;
          }
          // Use the latest precise pointer factor, including before RAF.
          commitScale();
        }} />}
      {rotatePhase !== null && <div className="stage-sketch stage-rotate" data-phase={rotatePhase}
        onPointerDown={(event) => {
          if (event.button === 1 || event.button === 2) { closeDirectTool(); transferNavigation(event); }
        }}
        onPointerMove={(event) => {
          const current = interaction.current.rotate;
          if (!current || current.typed !== null || event.buttons) return;
          interaction.current.pointer = { x: event.clientX, y: event.clientY };
          if (!current.reference) return;
          const world = viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, current.plane);
          const point = world ? pointToPlane(world, current.plane) : null;
          current.angleDegrees = !point || Math.hypot(...point) <= current.centerTolerance ? null : Math.atan2(
            current.reference[0] * point[1] - current.reference[1] * point[0],
            current.reference[0] * point[0] + current.reference[1] * point[1]) * 180 / Math.PI;
          scheduleInteractionFrame(interaction.current, "rotate", paintRotate);
        }}
        onClick={(event) => {
          if (event.button !== 0) return;
          const current = interaction.current.rotate;
          if (!current) return;
          if (current.typed === null && !current.reference) {
            const pointer = interaction.current.pointer ?? { x: event.clientX, y: event.clientY };
            const world = viewportRef.current?.pointOnSketchPlane(pointer.x, pointer.y, current.plane);
            if (!world) return;
            const point = pointToPlane(world, current.plane);
            // Cache a one-pixel centre tolerance when capturing the reference.
            // Browser pointer coordinates are float-quantized; a microscopic
            // world epsilon turns their centre error into an arbitrary angle.
            const neighbours = [[pointer.x + 1, pointer.y], [pointer.x, pointer.y + 1]]
              .map(([x, y]) => viewportRef.current?.pointOnSketchPlane(x!, y!, current.plane));
            current.centerTolerance = Math.max(1e-9, ...neighbours.map(neighbour => neighbour
              ? Math.hypot(...neighbour.map((value, i) => value - world[i]!)) : 0));
            if (Math.hypot(...point) <= current.centerTolerance) return;
            current.reference = point;
            setRotatePhase("angle"); return;
          }
          // Confirm the precise pointer angle already captured before RAF.
          commitRotate();
        }} />}
      {movePhase !== null && <div className="stage-sketch stage-move" data-phase={movePhase}
        data-constraint={moveConstraint ?? "none"} style={{ touchAction: "none" }}
        aria-label={zh ? "拖动坐标轴或平面；Enter 应用，Esc 取消" : "Drag an axis or plane; Enter applies, Esc cancels"}
        onPointerDown={(event) => {
          if (event.button === 1 || event.button === 2) { closeDirectTool(); transferNavigation(event); return; }
          const current = interaction.current.move;
          if (event.button !== 0 || !current || current.pointerId !== null) return;
          const sample = viewportRef.current?.translationPointer("start", event.clientX, event.clientY);
          if (!sample) return;
          event.preventDefault();
          current.pointerId = event.pointerId; current.constraint = sample.constraint;
          current.translation = sample.translation; current.typed = null;
          event.currentTarget.setPointerCapture(event.pointerId);
          setMoveConstraint(sample.constraint); setMovePhase("target");
          paintMove();
        }}
        onPointerMove={(event) => {
          const current = interaction.current.move;
          if (!current) return;
          if (current.pointerId === null) { viewportRef.current?.translationPointer("hover", event.clientX, event.clientY); return; }
          if (event.pointerId !== current.pointerId || current.typed !== null) return;
          const sample = viewportRef.current?.translationPointer("move", event.clientX, event.clientY);
          if (!sample) return;
          const axis = sample.constraint.length === 1;
          const direction = ["X", "Y", "Z"].map(name => Number(axis ? sample.constraint === name : !sample.constraint.includes(name))) as Vec3;
          const snap = viewportRef.current?.snapOnModel(event.clientX, event.clientY, 14, {
            constraint: { kind: axis ? "axis" : "plane", origin: current.origin, direction },
            excludeObjectName: `draft:${current.target.elementId}`,
          });
          current.translation = snap ? constrainedTranslation(snap.point.map((value, i) => value - current.origin[i]!), sample.constraint) : sample.translation;
          scheduleInteractionFrame(interaction.current, "move", paintMove);
        }}
        onPointerUp={(event) => {
          const current = interaction.current.move;
          if (!current || event.pointerId !== current.pointerId) return;
          // Release only ends pointer capture. Keep the preview available for
          // numeric correction; explicit Enter/Apply commits the typed action.
          current.pointerId = null;
          viewportRef.current?.translationPointer("end", event.clientX, event.clientY);
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          cancelInteractionFrame(interaction.current, "move"); paintMove();
        }}
        onLostPointerCapture={(event) => {
          if (interaction.current.move?.pointerId === event.pointerId) closeDirectTool();
        }}
        onPointerCancel={(event) => {
          if (interaction.current.move?.pointerId === event.pointerId) closeDirectTool();
        }} />}
      {pushPullActive && <div className="stage-sketch stage-pushpull" data-phase="pushPull"
        onPointerDown={(event) => {
          if (event.button === 1 || event.button === 2) { closeDirectTool(); transferNavigation(event); }
        }}
        onPointerMove={(event) => {
          const current = interaction.current.pushPull;
          if (!current || current.typed !== null || event.buttons) return;
          if (!current.constraint.isCurrent()) { closeDirectTool(); return; }
          const snap = !current.constraint.numericOnly && viewportRef.current?.snapOnModel(event.clientX, event.clientY, 14, {
            constraint: { kind: "axis", origin: current.face.origin, direction: current.constraint.normal },
            excludeObjectName: `draft:${current.target.elementId}`,
          });
          const distance = snap ? current.constraint.normal.reduce((sum, value, i) => sum + value * (snap.point[i]! - current.face.origin[i]!), 0)
            : current.constraint.distance(event.clientX, event.clientY);
          if (distance === null) return;
          current.distance = distance;
          scheduleInteractionFrame(interaction.current, "pushPull", paintPushPull);
        }}
        onPointerCancel={closeDirectTool}
        onLostPointerCapture={closeDirectTool}
        onClick={(event) => { if (event.button === 0) commitPushPull(); }}>
        {pushPullGuide?.numericOnly && <div role="status" style={{ position: "absolute", top: 12, left: 12,
          pointerEvents: "none", padding: "6px 10px", background: "var(--panel)", color: "var(--text)" }}>
          {zh ? "法线接近视线方向 · 请输入正负距离" : "Face normal is nearly end-on · Enter a signed distance"}
        </div>}
        {pushPullGuide && <svg aria-hidden="true" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none", overflow: "hidden" }}>
          {!pushPullGuide.numericOnly && <>
            <line x1={pushPullGuide.guide.negative[0]} y1={pushPullGuide.guide.negative[1]}
              x2={pushPullGuide.guide.positive[0]} y2={pushPullGuide.guide.positive[1]}
              stroke="var(--accent)" strokeWidth="2" strokeDasharray="5 4" />
            <text x={pushPullGuide.guide.positive[0] + 5} y={pushPullGuide.guide.positive[1]} fill="var(--accent)" fontSize="18">+</text>
            <text x={pushPullGuide.guide.negative[0] + 5} y={pushPullGuide.guide.negative[1]} fill="var(--accent)" fontSize="18">−</text>
          </>}
          <circle cx={pushPullGuide.guide.origin[0]} cy={pushPullGuide.guide.origin[1]} r="5" fill="none" stroke="var(--accent)" strokeWidth="2" />
        </svg>}
      </div>}
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
            const sketch = interaction.current.sketch;
            if (sketch.tool === "freehand" && sketch.phase !== "height") {
              if (sketch.phase !== "profile" || !interaction.current.press || !(event.buttons & 1)) return;
              let vertices = [...sketch.vertices];
              for (const sample of event.nativeEvent.getCoalescedEvents?.().length ? event.nativeEvent.getCoalescedEvents() : [event]) {
                const last = interaction.current.pointer;
                if (last && Math.hypot(sample.clientX - last.x, sample.clientY - last.y) < 2) continue;
                const world = sketch.plane ? viewportRef.current?.pointOnSketchPlane(sample.clientX, sample.clientY, sketch.plane)
                  : viewportRef.current?.pointOnWorkPlane(sample.clientX, sample.clientY, sketch.base);
                if (!world) continue;
                if (vertices.length >= 512) vertices = vertices.filter((_, index) => index % 2 === 0);
                vertices.push(pointToPlane(world, sketch.plane));
                interaction.current.pointer = { x: sample.clientX, y: sample.clientY };
              }
              showSketch({ ...sketch, vertices, profile: vertices, cursor: vertices.at(-1) ?? sketch.anchor }, true);
              return;
            }
            if (sketch.tool === "arc" && sketch.vertices.length >= 2 && sketch.typed.trim()) return;
            const onModel = snapForSketch(event.clientX, event.clientY, event.shiftKey);
            const world = onModel
              ? onModel.point
              : sketch.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, sketch.plane)
                : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, sketch.base);
            if (sketch.phase === "profile" && sketch.anchor !== null) {
              if (!world) return;
              const moved = planCursor(sketch, world, onModel, event.shiftKey);
              if (sketch.tool !== "line" && sketch.tool !== "arc") setSnapNote(moved.snapped ? moved.snapped.kind : null);
              showSketch(sketchAtPoint(sketch, moved.point), true);
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
              const raised = onModel?.point ?? (origin && (sketch.plane
                ? viewportRef.current?.pointAlongAxis(event.clientX, event.clientY, origin, normal)
                : viewportRef.current?.unprojectOnPlane(event.clientX, event.clientY, origin, true)));
              if (!raised) return;
              const height = normal.reduce((sum, value, index) => sum + value * (raised[index]! - origin![index]!), 0);
              showSketch({ ...sketch, height }, true);
            }
          }}
          onPointerDown={(event) => {
            if (transferNavigation(event)) return;
            if (event.button !== 0 || sketchBusy) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            const current = interaction.current.sketch;
            if (current.tool !== "freehand" || current.phase === "height") return;
            const world = current.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, current.plane)
              : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, current.base);
            if (!world) return;
            const start = pointToPlane(world, current.plane);
            interaction.current.press = { x: event.clientX, y: event.clientY, dragging: true };
            interaction.current.pointer = { x: event.clientX, y: event.clientY };
            showSketch({ ...current, phase: "profile", anchor: start, vertices: [start], profile: [], cursor: start, typed: "" });
          }}
          onPointerUp={(event) => {
            const current = interaction.current.sketch;
            const press = interaction.current.press;
            if (current.tool !== "freehand" || current.phase !== "profile" || !press || event.button !== 0) return;
            interaction.current.press = null;
            const world = current.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, current.plane)
              : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, current.base);
            let vertices = [...current.vertices];
            if (world) {
              const point = pointToPlane(world, current.plane), last = vertices.at(-1)!;
              if (Math.hypot(point[0] - last[0], point[1] - last[1]) > 1e-6) {
                if (vertices.length >= 512) vertices = vertices.filter((_, index) => index % 2 === 0);
                vertices.push(point);
              }
            }
            if (vertices.length > 3 && Math.hypot(event.clientX - press.x, event.clientY - press.y) <= 10 && enclosesArea(vertices.slice(0, -1))) {
              vertices = vertices.slice(0, -1);
              showSketch({ ...current, vertices, profile: vertices });
              closePolygon();
            } else {
              showSketch({ ...current, vertices, profile: vertices });
              finishPath();
            }
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={() => { if (interaction.current.sketch.tool === "freehand") stopSketching(); }}
          onLostPointerCapture={() => {
            if (interaction.current.sketch.tool === "freehand" && interaction.current.press) stopSketching();
          }}
          onClick={(event) => {
            if (sketchBusy || event.detail > 1) return;
            let sketch = interaction.current.sketch;
            if (sketch.tool === "freehand") { if (sketch.phase === "height") submitSketch(); return; }
            const onModel = snapForSketch(event.clientX, event.clientY, event.shiftKey);
            const world = onModel
              ? onModel.point
              : sketch.plane ? viewportRef.current?.pointOnSketchPlane(event.clientX, event.clientY, sketch.plane)
                : viewportRef.current?.pointOnWorkPlane(event.clientX, event.clientY, sketch.base);
            if (sketch.phase === "profile" && world && !sketch.typed.trim()) {
              // A modifier/lock can change between the last move and this click.
              // Commit the same freshly constrained point the marker just used.
              sketch = sketchAtPoint(sketch, planCursor(sketch, world, onModel, event.shiftKey).point);
              interaction.current.sketch = sketch;
            }
            if (sketch.phase === "idle" || sketch.anchor === null) {
              if (!world) return;
              const start = planCursor(sketch, world, onModel, event.shiftKey);
              setSnapNote(start.snapped ? start.snapped.kind : null);
              showSketch({ ...sketch, phase: "profile", anchor: start.point, vertices: [start.point], cursor: start.point, profile: [], height: 0, typed: "" });
            } else if (sketch.phase === "profile") {
              if (sketch.tool === "arc") {
                if (sketch.vertices.length >= 2) { finishPath(); return; }
                if (!world) return;
                const point = sketch.cursor ?? pointToPlane(world, sketch.plane);
                if (Math.hypot(point[0] - sketch.anchor![0], point[1] - sketch.anchor![1]) <= 1e-6) return;
                showSketch({ ...sketch, vertices: [sketch.anchor!, point], profile: [sketch.anchor!, point], cursor: point, typed: "" });
                return;
              }
              if (sketch.tool === "polygon" || sketch.tool === "line") {
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
          onDoubleClick={(event) => { event.preventDefault(); interaction.current.sketch.tool === "line" ? finishPath() : closePolygon(); }}
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
        <div ref={toolsElement} className="viewtools-wrap">
          <div className="viewtools model-tools">
            <div className="model-tools__group" role="group" aria-label={zh ? "选择与绘制" : "Select and draw"}>
            <ModelToolButton icon="select" label={t("stage.sketch.select")} shortcut="Space" aria-pressed={sketch.tool === null && !measuring && !model?.directTool && !tool && !eraser}
              onClick={() => chooseDrawingTool(null)} />
            {onSketch && <>
              <div className="model-tools__line" onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setLineToolsOpen(false);
              }} onKeyDown={(event) => {
                if (event.key === "Escape" && lineToolsOpen) { event.preventDefault(); event.stopPropagation(); setLineToolsOpen(false); }
              }}>
                <ModelToolButton icon="line" label={t("stage.sketch.line")} shortcut="L" aria-pressed={sketch.tool === "line"}
                  onClick={() => chooseDrawingTool(sketch.tool === "line" ? null : "line")} />
                <ModelToolButton icon="chevron" label={t("stage.sketch.lineTools")} aria-expanded={lineToolsOpen}
                  aria-pressed={sketch.tool === "freehand" || sketch.tool === "polygon"} onClick={() => setLineToolsOpen(!lineToolsOpen)} />
                {lineToolsOpen && <div className="model-tools__flyout" role="group" aria-label={t("stage.sketch.lineTools")}>
                  {(["freehand", "polygon"] as const).map((kind) => <ModelToolButton key={kind} icon={kind}
                    label={t(`stage.sketch.${kind}`)} aria-pressed={sketch.tool === kind}
                    onClick={() => chooseDrawingTool(sketch.tool === kind ? null : kind)} />)}
                </div>}
              </div>
              {(["arc", "rectangle", "circle"] as const).map((kind) => <ModelToolButton key={kind} icon={kind}
                label={t(`stage.sketch.${kind}`)} shortcut={{ rectangle: "R", circle: "C", arc: "A" }[kind]}
                aria-pressed={sketch.tool === kind}
                onClick={() => chooseDrawingTool(sketch.tool === kind ? null : kind)} />)}
              <select className="model-tools__plane" aria-label={t("stage.sketch.workPlane")} title={t("stage.sketch.workPlane")} value={workPlaneName} disabled={sketchBusy}
                onChange={(event) => choosePlane(event.target.value as typeof workPlaneName)}>
                <option value="xy">XY</option>
                <option value="xz">XZ</option>
                <option value="yz">YZ</option>
                <option value="face" disabled={!model?.hasSelection}>{zh ? "选中面" : "Face"}</option>
              </select>
            </>}
            </div>
            <div className="model-tools__group" role="group" aria-label={zh ? "修改模型" : "Edit model"}>
            {model?.onTool && (["pushPull", "move", "rotate", "scale", "copy"] as const).map((kind) => <ModelToolButton
              key={kind} icon={kind} data-model-tool={kind} label={t(`stage.model.${kind}`)} shortcut={{ pushPull: "P", move: "M", rotate: "Q", scale: "S", copy: "" }[kind]}
              aria-pressed={model.directTool === kind} disabled={sketchBusy || (kind !== "pushPull" && !model.hasSelection)}
              onClick={() => { chooseDrawingTool(null); model.onTool?.(model.directTool === kind ? "select" : kind); }} />)}
            {model && <>
              <ModelToolButton icon="undo" label={t("stage.model.undo")} disabled={!model.canUndo || sketchBusy || actionInProgress} shortcut="Ctrl+Z" onClick={model.onUndo} />
              <ModelToolButton icon="redo" label={t("stage.model.redo")} disabled={!model.canRedo || sketchBusy || actionInProgress} shortcut="Ctrl+Shift+Z" onClick={model.onRedo} />
            </>}
            </div>
            <div className="model-tools__group" role="group" aria-label={zh ? "查看与批注" : "View and annotate"}>
            <ModelToolButton icon="measure" label={t("stage.measure.label")} shortcut="T" aria-pressed={measuring}
              onClick={() => measuring ? chooseDrawingTool(null) : chooseMeasure()} />
            <ModelToolButton icon="annotate" label={t("stage.tools.annotate")} aria-expanded={annotationToolsOpen} aria-controls="annotation-tools"
              onClick={() => { setAnnotationToolsOpen((open) => !open); setViewToolsOpen(false); setVersionsOpen(false); setParameterLocksOpen(false); }} />
            <ModelToolButton icon="fit" label={t("stage.tools.fit")} onClick={() => viewportRef.current?.fitView()} />
            <ModelToolButton icon="front" label={t("stage.tools.front")} onClick={() => viewportRef.current?.frontView()} />
            <ModelToolButton icon="more" label={t("stage.tools.viewOptions")} aria-expanded={viewToolsOpen} aria-controls="view-tools"
              onClick={() => { setViewToolsOpen((open) => !open); setAnnotationToolsOpen(false); setVersionsOpen(false); setParameterLocksOpen(false); }} />
            </div>
            {model?.sync && <div className="model-tools__group model-tools__sync">
              <ModelToolButton icon="sync" label={t("stage.sync.label")} disabled={!model.sync.dirty || model.sync.busy}
                onClick={model.sync.onSync} />
              {(model.sync.dirty || model.sync.busy || model.sync.error) && <span className="model-tools__sync-status"
                role={model.sync.error ? "alert" : "status"}>
                {model.sync.error ?? (model.sync.busy ? t("stage.sync.busy") : model.sync.autosave
                  ? t(model.sync.autosave === "saved" ? "stage.sync.autosaved" : "stage.sync.autosaving") : t("stage.sync.dirty"))}
              </span>}
            </div>}
          </div>
          {annotationToolsOpen && <div id="annotation-tools" className="viewtools viewtools--panel" role="group" aria-label={t("stage.tools.annotate")}>
            <ModelToolButton icon="erase" label={t("document.tool.eraser")} aria-pressed={eraser} disabled={!annotationsReady}
              onClick={() => { const next = !eraser; chooseDrawingTool(null); setAnnotationToolsOpen(true); setAnnotationCancel((value) => value + 1); setEraser(next); }} />
            <ModelToolButton icon="undo" label={t("stage.tools.undo.label")} disabled={!canUndoGesture} onClick={onUndoGesture} />
            <ModelToolButton icon="redo" label={t("document.redo")} disabled={!canRedoGesture} onClick={onRedoGesture} />
            {(tool !== null || eraser) && <ModelToolButton icon="close" label={t("stage.tools.cancel.label")}
              onClick={() => { setAnnotationCancel((value) => value + 1); setEraser(false); onTool(null); }} />}
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
            {tracingPaperReview && <>
              <span className="viewtools__sep" aria-hidden="true" />
              <button type="button" disabled={!tracingPaperReview.enabled || tracingPaperReview.busy}
                onClick={tracingPaperReview.onSend}>{t(tracingPaperReview.busy ? "stage.tracingPaper.sending" : "stage.tracingPaper.send")}</button>
              {tracingPaperReview.sent && <span className="quiet" role="status">{t("stage.tracingPaper.sent")}</span>}
              {tracingPaperReview.error && <span className="quiet" role="alert">{tracingPaperReview.error}</span>}
            </>}
          </div>}
          {viewToolsOpen && <div id="view-tools" className="viewtools viewtools--panel" role="group" aria-label={t("stage.tools.viewOptions")}>
          {parameterLocks && <button type="button" aria-expanded={parameterLocksOpen}
            onClick={() => { chooseDrawingTool(null); setParameterLocksOpen(true); }}>
            {zh ? "参数锁" : "Parameter locks"}</button>}
          <button type="button" disabled={!model?.hasSelection} onClick={() => viewportRef.current?.fitSelection()}>{t("stage.tools.fitSelected")}</button>
          {(["top", "front", "right", "iso", "perspective"] as const).map((view) => <button type="button" key={view}
            onClick={() => viewportRef.current?.standardView(view)}>{t(`stage.view.${view}`)}</button>)}
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
              href={connection.url(`/api/artifacts/${workModel.exported.sha256}/bytes`)}
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
                    : sketch.tool === "circle" ? !sketch.hasAnchor ? t("stage.sketch.center") : t("stage.sketch.radiusHint")
                      : sketch.tool === "line" ? t("stage.sketch.lineHint")
                      : sketch.tool === "freehand" ? t("stage.sketch.freehandHint")
                      : sketch.tool === "arc" ? !sketch.hasAnchor ? t("stage.sketch.arcStart") : sketch.hasChord ? t("stage.sketch.arcBulgeHint") : t("stage.sketch.arcEnd")
                      : sketch.tool === "polygon" ? t("stage.sketch.polygonHint")
                        : !sketch.hasAnchor ? t("stage.sketch.firstCorner") : t("stage.sketch.secondCorner")}
                {snapNote !== null && <span className="quiet"> · {t("stage.sketch.snapped", { kind: snapNote })}</span>}
              </span>
              {sketch.hasAnchor && !sketchBusy && (sketch.tool !== "freehand" || sketch.phase === "height") && (
                <label className="sketch-entry__value">
                  {sketch.phase === "height" ? t("stage.sketch.height") : sketch.tool === "circle" ? t("stage.sketch.radius")
                    : sketch.tool === "arc" ? t(sketch.hasChord ? "stage.sketch.bulge" : "stage.sketch.chord")
                    : sketch.tool === "polygon" || sketch.tool === "line" ? t("stage.sketch.segment") : t("stage.sketch.side")}
                  <input
                    autoFocus
                    inputMode="decimal"
                    value={sketch.typed}
                    onChange={(event) => {
                      const current = interaction.current.sketch;
                      const typed = event.target.value;
                      const value = typedNumber(typed);
                      showSketch({ ...current, typed, ...(current.tool === "arc" && current.vertices.length >= 2 && value !== null
                        ? { profile: arcOf(current.anchor!, current.vertices[1]!, value) } : {}) });
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return;
                      event.preventDefault();
                      const sketch = interaction.current.sketch;
                      const value = typedNumber(sketch.typed);
                      if (sketch.phase === "height") {
                        if (value === null) return;
                        // One confirmation submits once: the action is taken
                        // off the pointer before anything is sent.
                        const settled = { ...interaction.current.sketch, height: value, typed: "" };
                        showSketch(settled);
                        submitSketch(value === 0);
                      } else {
                        const corner = sketch.cursor ?? sketch.profile[2] ?? sketch.anchor!;
                        if (sketch.tool === "arc") {
                          if (sketch.vertices.length >= 2) {
                            if (sketch.typed.trim()) {
                              if (value === null) return;
                              showSketch({ ...sketch, profile: arcOf(sketch.anchor!, sketch.vertices[1]!, value) });
                            }
                            finishPath(); return;
                          }
                          if (value === null || value <= 0) return;
                          const point = sizedLine(sketch.anchor!, corner, value);
                          showSketch({ ...sketch, vertices: [sketch.anchor!, point], profile: [sketch.anchor!, point], cursor: point, typed: "" });
                          return;
                        }
                        if (sketch.tool === "polygon" || sketch.tool === "line") {
                          if (!sketch.typed.trim()) { sketch.tool === "line" ? finishPath() : closePolygon(); return; }
                          if (value === null || value <= 0) return;
                          const from = sketch.vertices.at(-1)!;
                          const point = sizedLine(from, corner, value);
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
              {sketch.phase === "profile" && sketch.tool !== "freehand" && !(sketch.tool === "arc" && sketch.hasChord) && <>
                <button type="button" aria-pressed={sketch.axisLock === "x"} title={t("stage.sketch.axisHint")}
                  onClick={() => showSketch({ ...interaction.current.sketch, axisLock: interaction.current.sketch.axisLock === "x" ? null : "x" })}>{t("stage.sketch.axisX")}</button>
                <button type="button" aria-pressed={sketch.axisLock === "y"} title={t("stage.sketch.axisHint")}
                  onClick={() => showSketch({ ...interaction.current.sketch, axisLock: interaction.current.sketch.axisLock === "y" ? null : "y" })}>{t("stage.sketch.axisY")}</button>
                {sketch.tool === "polygon" && <button type="button" disabled={!sketch.canClose}
                  onClick={closePolygon}>{t("stage.sketch.closePolygon")}</button>}
              </>}
              {sketch.phase === "height" && <button type="button" disabled={sketchBusy} onClick={() => {
                showSketch({ ...interaction.current.sketch, height: 0 }); submitSketch(true);
              }}>{t("stage.sketch.makeFace")}</button>}
              <span className="quiet sketch-entry__hint">{t("stage.sketch.cancel")}</span>
            </div>
          )}
          {parameterLocksOpen && parameterLocks && <ParameterLocksPanel key={parameterLocks.contextKey} controls={parameterLocks}
            onClose={() => { setParameterLocksOpen(false); setViewToolsOpen(true);
              requestAnimationFrame(() => toolsElement.current?.querySelector<HTMLButtonElement>('[aria-controls="view-tools"]')?.focus()); }} />}
          {model?.directTool && model.onApply && <ModelEditPanel key={model.directTool} tool={model.directTool}
            subject={model.subject} busy={model.busy ?? false} error={scaleError ?? rotateError ?? moveError ?? pushPullError ?? model.error ?? null}
            onApply={model.onApply} onClose={closeDirectTool}
            scale={{ inputs: scaleInputs, mode: scaleMode, onMode: changeScaleMode,
              values: interaction.current.scale?.typed ?? interaction.current.scale?.scale?.map(String) ?? ["1", "1", "1"],
              hint: zh ? "绕对象中心缩放。XYZ 等比，X/Y/Z 沿轴；点参考位置，再沿参考方向移动。倍率可覆盖，负值镜像；单击或 Enter 完成，Esc 取消。"
                : "Scale around the object centre. XYZ is uniform; X/Y/Z scale one axis. Pick a reference, then move along its direction. Values override; negative factors mirror. Click or Enter to apply, Esc to cancel.",
              onChange: (index, value) => {
                const current = interaction.current.scale;
                if (!current) return;
                current.typed ??= (current.scale ?? [1, 1, 1]).map(String) as [string, string, string];
                if (current.mode === "uniform") current.typed = [value, value, value];
                else current.typed[index] = value;
                cancelInteractionFrame(interaction.current, "scale"); paintScale();
              }, onCommit: commitScale }}
            rotate={{ inputRef: rotateInput, axis: rotateAxis, onAxis: changeRotateAxis,
              hint: zh ? "绕对象中心旋转。先点参考方向，再移动鼠标；输入角度可覆盖。单击或 Enter 完成，Esc 取消。"
                : "Rotate around the object centre. Pick a reference direction, then move the pointer; type an angle to override. Click or Enter to apply, Esc to cancel.",
              onChange: (value) => {
                const current = interaction.current.rotate;
                if (!current) return;
                current.typed = value;
                cancelInteractionFrame(interaction.current, "rotate"); paintRotate();
              }, onCommit: commitRotate }}
            move={{ inputs: moveInputs, constraint: moveConstraint, onConstraint: changeMoveConstraint,
              hint: zh ? "世界坐标：拖动箭头或平面，松开后可输入精确位移。Enter/应用确认，Esc 取消。切换约束重置预览；正对视线的轴请用数值输入。"
                : "World axes: drag an arrow or plane, then refine the distance. Enter/Apply confirms; Esc cancels. Changing constraint resets the preview. Use numbers for end-on axes.",
              onChange: (index, value) => {
                const current = interaction.current.move;
                if (!current?.constraint || !current.constraint.includes("XYZ"[index]!)) return;
                current.typed ??= current.translation.map(String) as [string, string, string];
                current.typed[index] = value;
                viewportRef.current?.clearSnap();
                cancelInteractionFrame(interaction.current, "move"); paintMove();
              }, onCommit: commitMove }}
            pushPull={{ inputRef: pushPullInput, active: pushPullActive,
              hint: pushPullActive
                ? (pushPullGuide?.numericOnly
                  ? (zh ? "此面法线接近视线方向，请输入正负距离。Enter 确认，Esc 取消。" : "Face normal is nearly end-on: enter a signed distance. Enter applies, Esc cancels.")
                  : (zh ? "沿所选面的法线推拉：+ 向外，− 向内。输入精确距离覆盖；单击或 Enter 确认，Esc 取消。" : "Along the picked face normal: + outward, − inward. Type to override; click or Enter applies, Esc cancels."))
                : model.pushPullReason || (zh ? "先选择一个面，再移动鼠标或输入距离。" : "Select a face, then move the pointer or enter a distance."),
              onChange: (value) => {
                const current = interaction.current.pushPull;
                if (!current) return;
                current.typed = value;
                viewportRef.current?.clearSnap();
                cancelInteractionFrame(interaction.current, "pushPull");
                paintPushPull();
              }, onCommit: commitPushPull }} />}
          {captureFeedback && <span className="viewtools__feedback" role="status" aria-live="polite" aria-atomic="true">{captureFeedback}</span>}
        </div>
      </div>


      {drawer}
      </div>
      <div ref={footerElement} className="stage__foot">
        <div className="stage__versions">
          <button type="button" className="btn stage__versions-toggle" aria-expanded={versionsOpen} aria-controls="stage-versions-panel"
            onClick={() => { if (!versionsOpen) onVersionsOpen?.(); setVersionsOpen((open) => !open); setAnnotationToolsOpen(false); setViewToolsOpen(false); setParameterLocksOpen(false); }}>
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
        </div>
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
        {documentProjectId ? <DocumentCanvas key={`${documentProjectId}:${documentRunId}:${documentView.sourceSha}:${documentView.revisionRef}`}
          projectId={documentProjectId} runId={documentRunId} controller={documentAnnotationsController}
          modelSources={documentModelSources} editingModelSource={editingModelSource}
          viewedModelSource={viewedModelSource} active={active && documentOpen && loadingSha === null && !changingBase}
          onOpenGeneratedDocument={(result) => onDocumentView({ open: true, mounted: true, runId: result.runId,
            sourceSha: result.assetSha256, revisionRef: result.revisionRef ?? null, pageIndex: 0 })}
          onContinueModelSource={onContinueModelSource}
          initialSourceSha={documentView.sourceSha} initialPageIndex={documentView.pageIndex}
          initialRevisionRef={documentView.revisionRef}
          sourceStageRef={designHistory?.currentStageRef}
          onBeforeLeave={onDocumentBeforeLeave}
          busy={baseActionBusy || changingBase} onSubmit={onDocumentSubmit} documentVisualInputAvailable={documentVisualInputAvailable} />
          : <div className="document-workspace document-empty">{t("document.noRun")}</div>}
      </div>}
      </div>
    </section>
  );
}
