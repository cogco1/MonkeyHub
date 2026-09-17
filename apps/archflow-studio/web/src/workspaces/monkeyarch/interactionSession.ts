import { IDLE, type SketchState } from "../../features/stage/sketch";
import type { LocalHit } from "./viewer/preselection";
import type { DrawnShapeDto } from "../../api/generated";
import type { NormalDragController } from "./viewer/normalDrag";
import type { PreparedPushPull } from "../../features/stage/pushPull";
import type { PlanPoint, SketchPlane, SnapCandidate } from "../../features/stage/sketch";
import type { ModelSnap, SketchPreview } from "./viewer/ThreeDmViewport";
import type { TranslationConstraint } from "./viewer/translationGizmo";

export interface PushPullTarget {
  readonly elementId: string;
  readonly shape: DrawnShapeDto;
}

export interface PushPullGesture {
  readonly target: PushPullTarget;
  readonly sourceKey: string;
  readonly constraint: NormalDragController;
  readonly face: SketchPlane;
  readonly prepared: PreparedPushPull;
  distance: number;
  typed: string | null;
}


export interface MoveGesture {
  readonly target: PushPullTarget;
  readonly tool: "move" | "copy";
  readonly sourceKey: string;
  readonly spec: SketchPreview;
  origin: [number, number, number];
  constraint: TranslationConstraint | null;
  pointerId: number | null;
  translation: [number, number, number];
  typed: [string, string, string] | null;
}

export interface RotateGesture {
  readonly target: PushPullTarget;
  readonly spec: SketchPreview;
  plane: SketchPlane;
  reference: PlanPoint | null;
  centerTolerance: number;
  angleDegrees: number | null;
  typed: string | null;
}

export type ScaleMode = "uniform" | "x" | "y" | "z";
export interface ScaleGesture {
  readonly target: PushPullTarget;
  readonly spec: SketchPreview;
  mode: ScaleMode;
  plane: SketchPlane;
  reference: [number, number, number] | null;
  centerTolerance: number;
  scale: [number, number, number] | null;
  typed: [string, string, string] | null;
}

/** One disposable hand interaction, shared by Stage and its viewport. */
export interface InteractionSession {
  sketch: SketchState;
  pushPull: PushPullGesture | null;
  move: MoveGesture | null;
  rotate: RotateGesture | null;
  scale: ScaleGesture | null;
  hover: LocalHit | null;
  modelSnap: { hit: LocalHit; feature: LocalHit["mesh"]; snap: ModelSnap } | null;
  planeSnap: SnapCandidate | null;
  pointer: { x: number; y: number } | null;
  press: { x: number; y: number; dragging: boolean } | null;
  frame: { id: number; kind: "sketch" | "hover" | "pushPull" | "move" | "rotate" | "scale"; paint: () => void } | null;
  readonly phase: "inactive" | "hovering" | "armed" | "anchored" | "dragging" | "value-override";
}

export function createInteractionSession(): InteractionSession {
  return {
    sketch: IDLE, pushPull: null, move: null, rotate: null, scale: null, hover: null, modelSnap: null, planeSnap: null, pointer: null, press: null, frame: null,
    get phase() {
      if (this.scale) return this.scale.typed !== null ? "value-override" : this.scale.reference ? "dragging" : "armed";
      if (this.rotate) return this.rotate.typed !== null ? "value-override" : this.rotate.reference ? "dragging" : "armed";
      if (this.move) return this.move.typed !== null ? "value-override" : this.move.pointerId !== null ? "dragging" : this.move.constraint ? "anchored" : "armed";
      if (this.pushPull) return this.pushPull.typed !== null ? "value-override" : this.pushPull.distance === 0 ? "anchored" : "dragging";
      if (this.sketch.tool !== null) {
        if (this.sketch.typed.trim()) return "value-override";
        if (this.sketch.phase === "idle") return "armed";
        return this.sketch.cursor === this.sketch.anchor ? "anchored" : "dragging";
      }
      if (this.press) return this.press.dragging ? "dragging" : "anchored";
      return this.hover ? "hovering" : "inactive";
    },
  };
}

export function cancelInteractionFrame(session: InteractionSession, kind?: "sketch" | "hover" | "pushPull" | "move" | "rotate" | "scale"): void {
  if (!session.frame || (kind && session.frame.kind !== kind)) return;
  cancelAnimationFrame(session.frame.id);
  session.frame = null;
}

/** Both consumers read their latest session at paint time, never a queued snapshot. */
export function scheduleInteractionFrame(session: InteractionSession, kind: "sketch" | "hover" | "pushPull" | "move" | "rotate" | "scale", paint: () => void): void {
  if (session.frame?.kind === kind) { session.frame.paint = paint; return; }
  cancelInteractionFrame(session);
  const frame = { id: 0, kind, paint };
  frame.id = requestAnimationFrame(() => {
    session.frame = null;
    frame.paint();
  });
  session.frame = frame;
}
