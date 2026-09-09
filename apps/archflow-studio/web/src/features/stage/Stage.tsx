/**
 * The model, and the few facts that sit over it.
 *
 * The viewport is the moved viewer, untouched. Around it: the source chip
 * (which file, whose label), the camera tools, the last resolved pick, the
 * versions strip and the evidence tab. Everything here was handed in; the
 * stage decides nothing.
 */

import { useState, type CSSProperties, type ReactNode, type RefObject } from "react";

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
} from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import { Annotate, GESTURE_TOOLS, type AnnotationStyle, type GestureTool } from "../../workspaces/monkeyarch/Annotate";
import { SourceChip, type ViewState } from "./SourceChip";
import { VersionsStrip, type VersionGroup, type DesignHistoryControls } from "./VersionsStrip";
import { DocumentCanvas, type DocumentViewContext } from "../../workspaces/monkeydiagram/DocumentCanvas";
import { createDocumentAnnotationsController } from "../../workspaces/monkeydiagram/useDocumentAnnotations";
import type { ModelAnnotationsHandle } from "../../workspaces/monkeyarch/useModelAnnotations";

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
  viewportRef,
  sourceLabel,
  message,
  status,
  inspection,
  artifactError,
  view,
  picked,
  versions,
  hasNewVersions = false,
  onVersionsOpen,
  workingCopies,
  onOpenWorkingOption,
  designHistory,
  documentView,
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
  onSource,
  onPick,
  onOpenVersion,
  onOpenRun,
  onShowHome,
  home,
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
  onEvidence,
}: {
  viewportRef: RefObject<ViewportController | null>;
  sourceLabel: string | null;
  message: string;
  status: ViewportStatus;
  inspection: SceneInspection | null;
  artifactError: StudioApiError | null;
  /** CURRENT / GHOST PREVIEW / VALIDATED, with the server's word as detail. */
  view: ViewState | null;
  picked: PickedFacts | null;
  versions: readonly VersionGroup[];
  hasNewVersions?: boolean;
  onVersionsOpen?(): void;
  workingCopies: readonly WorkingCopyDto[];
  onOpenWorkingOption(option: WorkingCopyOptionDto): void;
  designHistory?: DesignHistoryControls;
  documentView: DocumentViewContext;
  onDocumentView(next: DocumentViewContext): void;
  documentAnnotationsController: ReturnType<typeof createDocumentAnnotationsController>;
  onDocumentBeforeLeave(save: (() => Promise<void>) | null): void;
  drawing?: { busy: boolean; available: boolean; error: string | null; generate(view: ElevationRequestDto["view"]): void };
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
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
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
  onEvidence(tab: EvidenceTab): void;
}) {
  const t = useT();
  const { developerMode } = usePreferences();
  const [annotationStyle, setAnnotationStyle] = useState<AnnotationStyle>({ color: "#e5534b", lineWidth: 2 });
  const [elevationView, setElevationView] = useState<NonNullable<ElevationRequestDto["view"]>>("front");
  const [annotationCancel, setAnnotationCancel] = useState(0);
  const documentOpen = documentView.open;
  const documentMounted = documentView.mounted;
  const documentRunId = documentView.runId ?? editingBaseRunId;
  const [eraser, setEraser] = useState(false);
  const [annotationToolsOpen, setAnnotationToolsOpen] = useState(false);
  const [viewToolsOpen, setViewToolsOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
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
    {modelAnnotations && <div className="stage-source-line__save" data-model-annotations-status={modelAnnotations.error ? "error" :
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
      <div className="stage-mode-switch" role="group" aria-label={t("workspace.switcher")}>
        <button type="button" aria-pressed={!documentOpen} onClick={() => onDocumentView({ ...documentView, open: false })}>{t("workspace.monkeyarch")}</button>
        <button type="button" aria-pressed={documentOpen} onClick={() => { setAnnotationCancel((value) => value + 1); onDocumentView({ ...documentView, mounted: true, open: true }); }}>{t("workspace.monkeydiagram")}</button>
        {designHistory && <span className="stage-current-context">{designHistory.history?.branchId ?? "main"} · {contextLabel}</span>}
        {drawing && <><select aria-label="立面方向" value={elevationView} disabled={drawing.busy}
          onChange={(event) => setElevationView(event.target.value as NonNullable<ElevationRequestDto["view"]>)}>
          <option value="front">正立面</option><option value="back">背立面</option><option value="left">左立面</option><option value="right">右立面</option>
        </select><button disabled={!drawing.available || drawing.busy} onClick={() => drawing.generate(elevationView)}>{drawing.busy ? "正在出图…" : "生成立面"}</button></>}
      </div>
      {drawing?.error && <p className="stage-drawing-error" role="alert">{drawing.error}</p>}
      <div className={`stage-model${documentOpen ? " stage-model--hidden" : ""}`} inert={documentOpen} aria-hidden={documentOpen}
        onKeyDown={(event) => {
          if (!(event.ctrlKey || event.metaKey) || (event.target as HTMLElement).closest("input, textarea, select, [contenteditable]")) return;
          if (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y") {
            event.preventDefault(); setAnnotationCancel((value) => value + 1);
            if (event.shiftKey || event.key.toLowerCase() === "y") onRedoGesture(); else onUndoGesture();
          }
        }}>
      {/* A machine with no WebGL context throws while the renderer is built;
          behind its own boundary that costs the canvas and nothing else. */}
      <ErrorBoundary label={t("stage.viewer.label")}>
        <ThreeDmViewport
          ref={viewportRef}
          onInspection={onInspection}
          onStatus={onStatus}
          onRequestFile={onRequestFile}
          onSource={onSource}
          onPick={onPick}
        />
      </ErrorBoundary>
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
          <div className="stage-source-line" data-source-match={sameSource ? "same" : "different"}>
          <SourceChip
            sourceLabel={sourceLabel}
            inspection={inspection}
            status={status}
            message={message}
            view={view}
          />
          {sessionStatus}
          </div>
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
          {picked && (
            <div
              className="picked"
              title={developerMode ? t("stage.picked.title", {
                status: picked.status,
                sourceState: picked.sourceState,
              }) : undefined}
            >
              <span className="label">{t("stage.picked.label")}</span>
              {developerMode && <span className="mono">
                {picked.elementId ?? picked.componentId ?? t("stage.picked.none")}
              </span>}
              {!developerMode && <span>
                {designObjectLabel(picked.elementId ?? picked.componentId) ?? t("stage.picked.unresolved")}
              </span>}
              {!developerMode && picked.status !== "resolved" && (
                <span className="picked__meta">{t("stage.picked.unresolved")}</span>
              )}
              {developerMode && picked.status !== "resolved" && (
                <span className="picked__meta">{picked.status}</span>
              )}
              {developerMode && picked.fields.map(([key, value]) => (
                <span key={key} className="mono picked__field">
                  {key} {value}
                </span>
              ))}
            </div>
          )}
          {artifactError && (
            <ErrorPanel
              error={artifactError}
              what="GET /api/artifacts/{sha256}/bytes"
            />
          )}
        </div>
        <div className="viewtools-wrap">
          <div className="viewtools">
            <button type="button" aria-expanded={annotationToolsOpen} aria-controls="annotation-tools"
              onClick={() => { setAnnotationToolsOpen((open) => !open); setViewToolsOpen(false); }}>
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
              onClick={() => { setViewToolsOpen((open) => !open); setAnnotationToolsOpen(false); }}>{t("stage.tools.viewOptions")}</button>
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
          </div>}
          {captureFeedback && <span className="viewtools__feedback" role="status" aria-live="polite" aria-atomic="true">{captureFeedback}</span>}
        </div>
      </div>


      {framePanel}
      {optionsPanel}
      {programPanel}
      {drawer}
      </div>
      <div className="stage__foot">
        <div className="stage__versions">
          <button type="button" className="btn stage__versions-toggle" aria-expanded={versionsOpen} aria-controls="stage-versions-panel"
            onClick={() => { if (!versionsOpen) onVersionsOpen?.(); setVersionsOpen((open) => !open); }}>
            {t("stage.versions.open")} <span className="quiet">{versionCount}</span>
            {contextLabel && <span className="stage__versions-current">{contextLabel}</span>}
            {hasNewVersions && <span className="stage__versions-new" role="status">{t("stage.versions.new")}</span>}
          </button>
          {versionsOpen && <div id="stage-versions-panel" className="stage__versions-panel" role="region" aria-label={t("stage.versions.ariaLabel")}>
            <div className="stage__versions-head"><strong>{t("stage.versions.ariaLabel")}</strong>
              <button type="button" className="btn btn--small" onClick={() => setVersionsOpen(false)}>{t("stage.versions.close")}</button>
            </div>
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
        {documentProjectId && documentRunId ? <DocumentCanvas key={`${documentProjectId}:${documentRunId}:${documentView.sourceSha}:${documentView.revisionRef}`}
          projectId={documentProjectId} runId={documentRunId} controller={documentAnnotationsController}
          modelSources={documentModelSources} editingModelSource={editingModelSource}
          onContinueModelSource={onContinueModelSource}
          initialSourceSha={documentView.sourceSha} initialPageIndex={documentView.pageIndex}
          initialRevisionRef={documentView.revisionRef}
          sourceStageRef={designHistory?.currentStageRef}
          onBeforeLeave={onDocumentBeforeLeave}
          busy={baseActionBusy || changingBase} onSubmit={onDocumentSubmit} documentVisualInputAvailable={documentVisualInputAvailable} />
          : <div className="document-workspace document-empty">{t("document.noRun")}</div>}
      </div>}
    </section>
  );
}
