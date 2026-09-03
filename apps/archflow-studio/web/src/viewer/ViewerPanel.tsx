/**
 * The chrome around the viewport: the source chip, the camera buttons, and the
 * two lines of fact under the canvas.
 *
 * This component owns no state and decides nothing. Every value it renders was
 * handed to it, and every button forwards to the controller the shell holds —
 * the file in the viewport, the label it wears and the artifact it answers for
 * are the shell's to keep, because the pick that follows a click is resolved
 * against them.
 *
 * The one judgement encoded here is where a refused download is shown. Bytes
 * are fetched for the canonical listing and for a candidate's own export alike,
 * so the refusal belongs beside the chip it failed to set rather than inside
 * one of the two lists that can ask for it.
 */

import type { RefObject } from "react";

import type { StudioApiError } from "../api/client";
import { ErrorBoundary } from "../app/ErrorBoundary";
import { ErrorPanel } from "../app/ErrorPanel";
import type { SceneInspection } from "./sceneInspection";
import {
  ThreeDmViewport,
  type ViewportController,
  type ViewportPick,
  type ViewportStatus,
} from "./ThreeDmViewport";

export function ViewerPanel({
  viewportRef,
  sourceLabel,
  message,
  status,
  inspection,
  artifactError,
  onInspection,
  onStatus,
  onRequestFile,
  onSource,
  onPick,
}: {
  viewportRef: RefObject<ViewportController | null>;
  sourceLabel: string | null;
  message: string;
  status: ViewportStatus;
  inspection: SceneInspection | null;
  artifactError: StudioApiError | null;
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
}) {
  return (
    <div className="viewer">
      <div className="viewer__bar">
        <span
          className={`chip ${
            sourceLabel === null ? "chip--neutral" : "chip--source"
          }`}
        >
          {sourceLabel ?? "NO MODEL LOADED"}
        </span>
        <span className="viewer__status">{message}</span>
        <button
          type="button"
          className="button button--small"
          onClick={() => viewportRef.current?.fitView()}
        >
          fit
        </button>
        <button
          type="button"
          className="button button--small"
          onClick={() => viewportRef.current?.frontView()}
        >
          front
        </button>
        <button
          type="button"
          className="button button--small"
          onClick={() => viewportRef.current?.clear()}
        >
          clear
        </button>
      </div>
      {artifactError && (
        <ErrorPanel
          error={artifactError}
          what="GET /api/artifacts/{sha256}/bytes"
        />
      )}
      {/* The viewport is the one part of this shell that can fail for a reason
          that has nothing to do with the project: a machine with no WebGL
          context throws while the renderer is being built. Behind its own
          boundary that costs the canvas, and the chip, the facts and every
          panel around it survive to say what is still true. */}
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
      {inspection && (
        <p className="viewer__facts mono">
          {inspection.fileName} · {inspection.meshCount} meshes ·{" "}
          {inspection.objectCount} objects · {inspection.loadDurationMs} ms
          {status === "error" ? " · load failed" : ""}
        </p>
      )}
    </div>
  );
}
