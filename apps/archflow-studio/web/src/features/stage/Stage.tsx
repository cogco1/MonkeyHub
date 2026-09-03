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
import type { ProjectArtifactDto } from "../../api/generated";
import { ErrorBoundary } from "../../app/ErrorBoundary";
import { ErrorPanel } from "../../app/ErrorPanel";
import type { EvidenceTab } from "../../app/evidence";
import type { SceneInspection } from "../../viewer/sceneInspection";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "../../viewer/ThreeDmViewport";
import { SourceChip, type ViewState } from "./SourceChip";
import { VersionsStrip, type VersionCard } from "./VersionsStrip";

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
  loadedSha,
  evidenceCounts,
  review,
  drawer,
  onInspection,
  onStatus,
  onRequestFile,
  onSource,
  onPick,
  onOpenVersion,
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
  versions: readonly VersionCard[];
  loadingSha: string | null;
  loadedSha: string | null;
  evidenceCounts: EvidenceCounts;
  review: ReviewSummary;
  /** The drawer, when it overlays the stage rather than standing beside it. */
  drawer: ReactNode;
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
  onOpenVersion(artifact: ProjectArtifactDto, sourceLabel: string): void;
  onEvidence(tab: EvidenceTab): void;
}) {
  return (
    <section className="stage" aria-label="model">
      {/* A machine with no WebGL context throws while the renderer is built;
          behind its own boundary that costs the canvas and nothing else. */}
      <ErrorBoundary label="viewer">
        <ThreeDmViewport
          ref={viewportRef}
          onInspection={onInspection}
          onStatus={onStatus}
          onRequestFile={onRequestFile}
          onSource={onSource}
          onPick={onPick}
        />
      </ErrorBoundary>

      <div className="hud">
        <div className="hud__left">
          <SourceChip
            sourceLabel={sourceLabel}
            inspection={inspection}
            status={status}
            message={message}
            view={view}
          />
          {picked && (
            <div className="picked" title={`${picked.status} · source ${picked.sourceState}`}>
              <span className="label">picked</span>
              <span className="mono">
                {picked.elementId ?? picked.componentId ?? "nothing resolvable"}
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
          <button type="button" onClick={() => viewportRef.current?.fitView()}>
            fit
          </button>
          <button type="button" onClick={() => viewportRef.current?.frontView()}>
            front
          </button>
          <button type="button" onClick={() => viewportRef.current?.clear()}>
            clear
          </button>
          <button type="button" onClick={onRequestFile}>
            open .3dm
          </button>
        </div>
      </div>

      <div className="stage__foot">
        <VersionsStrip
          versions={versions}
          loadingSha={loadingSha}
          loadedSha={loadedSha}
          onOpen={onOpenVersion}
        />
        <span className="stage__spacer" />
        <button
          type="button"
          className="drawer-tab"
          onClick={() => onEvidence("honesty")}
        >
          Evidence
          <span className="drawer-tab__count">
            {review.changes} {review.changes === 1 ? "change" : "changes"} ·{" "}
            {review.checked} checked · {review.needsReview}{" "}
            {review.needsReview === 1 ? "needs" : "need"} review
          </span>
          <span className="drawer-tab__count mono" title="what the drawer holds">
            honesty {evidenceCounts.honesty} · events {evidenceCounts.events}
          </span>
        </button>
      </div>

      {drawer}
    </section>
  );
}
