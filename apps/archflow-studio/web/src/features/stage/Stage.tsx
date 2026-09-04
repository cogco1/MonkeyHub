/**
 * The model, and the few facts that sit over it.
 *
 * The viewport is the moved viewer, untouched. Around it: the source chip
 * (which file, whose label), the camera tools, the last resolved pick, the
 * versions strip and the evidence tab. Everything here was handed in; the
 * stage decides nothing.
 */

import type { ReactNode, RefObject } from "react";

import type { StudioApiError } from "../../api/client";
import type { GestureDto, ProjectArtifactDto } from "../../api/generated";
import { ErrorBoundary } from "../../app/ErrorBoundary";
import { ErrorPanel } from "../../app/ErrorPanel";
import type { EvidenceTab } from "../../app/evidence";
import { LoadingOverlay } from "../../app/LoadingOverlay";
import { useT } from "../../i18n/useT";
import type { SceneInspection } from "../../viewer/sceneInspection";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "../../viewer/ThreeDmViewport";
import { Annotate, GESTURE_TOOLS, type GestureTool } from "./Annotate";
import { SourceChip, type ViewState } from "./SourceChip";
import { VersionsStrip, type VersionGroup } from "./VersionsStrip";

export interface PickedFacts {
  readonly componentId: string | null;
  readonly elementId: string | null;
  readonly status: string;
  readonly sourceState: string;
  /** The element's numeric fields as the projection carries them, unit-less. */
  readonly fields: ReadonlyArray<readonly [string, number]>;
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
  loadingSha,
  loadedShas,
  evidenceCounts,
  review,
  drawer,
  tool,
  gestures,
  onTool,
  onGesture,
  onInspection,
  onStatus,
  onRequestFile,
  onSource,
  onPick,
  onOpenVersion,
  onOpenRun,
  onShowReference,
  referenceRunId,
  loadedRunId,
  onCompareVersion,
  blend,
  onBlend,
  onEndBlend,
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
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
  onOpenVersion(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Put every available export of one run on the stage at once. */
  onOpenRun(group: VersionGroup): void;
  /** Back to the whole reference run, from wherever the stage got to. */
  onShowReference(): void;
  /** The reference run, when it has exports to come back to; null when it has none. */
  referenceRunId: string | null;
  /** The run whose export is on screen, for the strip's comparisons. */
  loadedRunId: string | null;
  onCompareVersion(artifact: ProjectArtifactDto): void;
  /** A cross-fade in progress: before is the loaded run, after the candidate. */
  blend: { candidateId: string; against: string; t: number; meshes: number } | null;
  onBlend(t: number): void;
  onEndBlend(): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  const t = useT();
  const activeTool = GESTURE_TOOLS.find((item) => item.kind === tool);
  return (
    <section className="stage" aria-label={t("stage.ariaLabel")}>
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
        tool={tool}
        gestures={gestures}
        onGesture={onGesture}
      />
      {/* Parsing a run's exports is the longest wait in the app after the launch itself, and
          it is the same wait: the viewport's own status line is what the overlay says. */}
      {status === "loading" && <LoadingOverlay mode="stage" status={message} />}

      <div className="hud">
        <div className="hud__left">
          <SourceChip
            sourceLabel={sourceLabel}
            inspection={inspection}
            status={status}
            message={message}
            view={view}
          />
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
              <span className="quiet mono blend__meta">
                {blend.candidateId} · {t("stage.blend.meshes", { count: blend.meshes })} ·{" "}
                {t("stage.blend.afterTinted")}
              </span>
              <button type="button" className="btn btn--small" onClick={onEndBlend}>
                {t("stage.blend.done")}
              </button>
            </div>
          )}
          {picked && (
            <div
              className="picked"
              title={t("stage.picked.title", {
                status: picked.status,
                sourceState: picked.sourceState,
              })}
            >
              <span className="label">{t("stage.picked.label")}</span>
              <span className="mono">
                {picked.elementId ?? picked.componentId ?? t("stage.picked.none")}
              </span>
              {picked.status !== "resolved" && (
                <span className="picked__meta">{picked.status}</span>
              )}
              {picked.fields.map(([key, value]) => (
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
        <div className="viewtools">
          {GESTURE_TOOLS.map((item) => (
            <button
              key={item.kind}
              type="button"
              title={t(item.titleKey)}
              aria-pressed={tool === item.kind}
              onClick={() => onTool(tool === item.kind ? null : item.kind)}
            >
              {item.glyph} {t(item.labelKey)}
            </button>
          ))}
          {tool && activeTool && (
            <span className="viewtools__hint quiet">
              {t("stage.tools.drawingHint", { tool: t(activeTool.labelKey) })}
            </span>
          )}
          <span className="viewtools__sep" aria-hidden="true" />
          <button
            type="button"
            disabled={referenceRunId === null}
            title={
              referenceRunId === null
                ? t("stage.tools.referenceUnavailable")
                : t("stage.tools.referenceShow", { runId: referenceRunId })
            }
            onClick={onShowReference}
          >
            {t("stage.tools.reference")}
          </button>
          <button type="button" onClick={() => viewportRef.current?.fitView()}>
            {t("stage.tools.fit")}
          </button>
          <button type="button" onClick={() => viewportRef.current?.frontView()}>
            {t("stage.tools.front")}
          </button>
          <button type="button" onClick={() => viewportRef.current?.clear()}>
            {t("stage.tools.clear")}
          </button>
          <button type="button" onClick={onRequestFile}>
            {t("stage.tools.open3dm")}
          </button>
        </div>
      </div>

      <div className="stage__foot">
        <VersionsStrip
          groups={versions}
          loadingSha={loadingSha}
          loadedShas={loadedShas}
          loadedRunId={loadedRunId}
          onOpen={onOpenVersion}
          onOpenRun={onOpenRun}
          onCompare={onCompareVersion}
        />
        <span className="stage__spacer" />
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
        </button>
      </div>

      {drawer}
    </section>
  );
}
