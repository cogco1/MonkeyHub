/**
 * What a drawing action is, before any of it is a request.
 *
 * Everything here is arithmetic on points the viewport handed back: the
 * rectangle two corners make, the point a snap moves to, and the state machine
 * one action walks through. It holds no model, keeps nothing after the action
 * and sends nothing; the shell drives it and the server is only reached when
 * an action is finished.
 */

export type PlanPoint = readonly [number, number];

/** What the pointer is doing in the middle of one action. */
export type SketchPhase = "idle" | "profile" | "height";

export interface SketchState {
  readonly tool: "rectangle" | null;
  readonly phase: SketchPhase;
  /** The work plane this action draws on, in CAD world units along +Z. */
  readonly base: number;
  /** The first corner, once it is set. */
  readonly anchor: PlanPoint | null;
  /** The profile as it stands: empty until two corners exist. */
  readonly profile: readonly PlanPoint[];
  /** How far it is being pulled; zero while the outline is still being drawn. */
  readonly height: number;
  /** A number typed during the action, which wins over the pointer. */
  readonly typed: string;
}

export const IDLE: SketchState = {
  tool: null, phase: "idle", base: 0, anchor: null, profile: [], height: 0, typed: "",
};

/** The four corners two opposite corners make, in order, on the work plane. */
export function rectangleOf(anchor: PlanPoint, corner: PlanPoint): readonly PlanPoint[] {
  const [ax, az] = anchor;
  const [cx, cz] = corner;
  return [[ax, az], [cx, az], [cx, cz], [ax, cz]];
}

/**
 * The corner a height is pulled from: the one the pointer just clicked.
 *
 * ``rectangleOf`` puts the moving corner third, so that is where the pointer
 * is when the outline is settled and the height begins. Reading the height on
 * a vertical plane through *this* point makes the switch free of any jump —
 * the same ray meets the plane exactly where the click was — and every level
 * above it is the level the pointer is actually at.
 */
export function heightAnchor(state: SketchState): PlanPoint | null {
  return state.profile[2] ?? state.profile[0] ?? state.anchor;
}

/** Whether a profile encloses anything at all; a flat one is not a face. */
export function enclosesArea(profile: readonly PlanPoint[], tolerance = 1e-6): boolean {
  if (profile.length < 3) return false;
  let twice = 0;
  for (let index = 0; index < profile.length; index += 1) {
    const [x, z] = profile[index]!;
    const [nx, nz] = profile[(index + 1) % profile.length]!;
    twice += x * nz - nx * z;
  }
  return Math.abs(twice) / 2 > tolerance;
}

export interface SnapCandidate {
  readonly point: PlanPoint;
  /** What it is, so the person can be told why the pointer moved. */
  readonly kind: "endpoint" | "midpoint" | "axis";
}

export interface SnapResult {
  readonly point: PlanPoint;
  readonly snapped: SnapCandidate | null;
}

/**
 * Move a point onto something real when it is close enough: an existing
 * endpoint, the midpoint between two of them, or the axis through the action's
 * own anchor. Distances are in world units, so the caller converts its own
 * pixel radius before asking.
 */
export function snapPoint(
  point: PlanPoint,
  {
    endpoints = [],
    anchor = null,
    radius,
  }: { endpoints?: readonly PlanPoint[]; anchor?: PlanPoint | null; radius: number },
): SnapResult {
  const near = (a: PlanPoint, b: PlanPoint) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const candidates: SnapCandidate[] = [];
  for (const end of endpoints) candidates.push({ point: end, kind: "endpoint" });
  for (let i = 0; i < endpoints.length; i += 1) {
    for (let j = i + 1; j < endpoints.length; j += 1) {
      const [ax, az] = endpoints[i]!;
      const [bx, bz] = endpoints[j]!;
      candidates.push({ point: [(ax + bx) / 2, (az + bz) / 2], kind: "midpoint" });
    }
  }
  const best = candidates
    .map((candidate) => ({ candidate, distance: near(candidate.point, point) }))
    .filter((row) => row.distance <= radius)
    .sort((a, b) => a.distance - b.distance || (a.candidate.kind === "endpoint" ? -1 : 1))[0];
  if (best) return { point: best.candidate.point, snapped: best.candidate };
  if (anchor) {
    // An axis lock is the weakest snap: it only straightens one coordinate.
    const dx = Math.abs(point[0] - anchor[0]);
    const dz = Math.abs(point[1] - anchor[1]);
    if (dx <= radius && dz > radius) return { point: [anchor[0], point[1]], snapped: { point: [anchor[0], point[1]], kind: "axis" } };
    if (dz <= radius && dx > radius) return { point: [point[0], anchor[1]], snapped: { point: [point[0], anchor[1]], kind: "axis" } };
  }
  return { point, snapped: null };
}

/** A typed number, or null when what was typed is not one yet. */
export function typedNumber(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : null;
}

/**
 * A square whose side is the typed number, keeping the direction the pointer
 * was going: typing during an action sizes it exactly instead of by eye.
 */
export function sizedRectangle(anchor: PlanPoint, corner: PlanPoint, side: number): readonly PlanPoint[] {
  const dx = corner[0] - anchor[0] >= 0 ? 1 : -1;
  const dz = corner[1] - anchor[1] >= 0 ? 1 : -1;
  return rectangleOf(anchor, [anchor[0] + dx * side, anchor[1] + dz * side]);
}

/** The action the shell would submit, or null while it is not finished. */
export interface FinishedSketch {
  readonly profile: readonly PlanPoint[];
  readonly height: number;
  readonly base: number;
}

export function finished(state: SketchState): FinishedSketch | null {
  if (state.phase !== "height" || !enclosesArea(state.profile) || !(state.height > 0)) return null;
  return { profile: state.profile, height: state.height, base: state.base };
}

/** Esc, and anything else that abandons the action: back to nothing drawn. */
export function cancelled(state: SketchState): SketchState {
  return { ...IDLE, tool: state.tool };
}
