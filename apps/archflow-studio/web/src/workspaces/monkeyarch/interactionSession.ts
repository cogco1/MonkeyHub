import { IDLE, type SketchState } from "../../features/stage/sketch";
import type { LocalHit } from "./viewer/preselection";

/** One disposable hand interaction, shared by Stage and its viewport. */
export interface InteractionSession {
  sketch: SketchState;
  hover: LocalHit | null;
  pointer: { x: number; y: number } | null;
  press: { x: number; y: number; dragging: boolean } | null;
  frame: { id: number; kind: "sketch" | "hover"; paint: () => void } | null;
  readonly phase: "inactive" | "hovering" | "armed" | "anchored" | "dragging" | "value-override";
}

export function createInteractionSession(): InteractionSession {
  return {
    sketch: IDLE, hover: null, pointer: null, press: null, frame: null,
    get phase() {
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

export function cancelInteractionFrame(session: InteractionSession, kind?: "sketch" | "hover"): void {
  if (!session.frame || (kind && session.frame.kind !== kind)) return;
  cancelAnimationFrame(session.frame.id);
  session.frame = null;
}

/** Both consumers read their latest session at paint time, never a queued snapshot. */
export function scheduleInteractionFrame(session: InteractionSession, kind: "sketch" | "hover", paint: () => void): void {
  if (session.frame?.kind === kind) { session.frame.paint = paint; return; }
  cancelInteractionFrame(session);
  const frame = { id: 0, kind, paint };
  frame.id = requestAnimationFrame(() => {
    session.frame = null;
    frame.paint();
  });
  session.frame = frame;
}
