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
export type SketchTool = "rectangle" | "circle" | "polygon" | "line" | "freehand" | "arc";
export type SketchVector = readonly [number, number, number];
/** A local drawing frame in the viewer's CAD Z-up coordinates. */
export interface SketchPlane {
  readonly origin: SketchVector;
  readonly xAxis: SketchVector;
  readonly yAxis: SketchVector;
  readonly normal: SketchVector;
}
export const WORK_PLANES = {
  xy: { origin: [0, 0, 0], xAxis: [1, 0, 0], yAxis: [0, 1, 0], normal: [0, 0, 1] },
  xz: { origin: [0, 0, 0], xAxis: [1, 0, 0], yAxis: [0, 0, 1], normal: [0, -1, 0] },
  yz: { origin: [0, 0, 0], xAxis: [0, 1, 0], yAxis: [0, 0, 1], normal: [1, 0, 0] },
} as const satisfies Record<string, SketchPlane>;

export function pointToPlane(point: SketchVector, plane: SketchPlane | null): PlanPoint {
  if (!plane) return [point[0], point[1]];
  const offset = point.map((value, index) => value - plane.origin[index]!);
  return [offset.reduce((sum, value, index) => sum + value * plane.xAxis[index]!, 0),
    offset.reduce((sum, value, index) => sum + value * plane.yAxis[index]!, 0)];
}

export function pointFromPlane(point: PlanPoint, plane: SketchPlane | null, base = 0): [number, number, number] {
  if (!plane) return [point[0], point[1], base];
  return plane.origin.map((value, index) => value + point[0] * plane.xAxis[index]! + point[1] * plane.yAxis[index]!) as [number, number, number];
}

/** What the pointer is doing in the middle of one action. */
export type SketchPhase = "idle" | "profile" | "height";

export interface SketchState {
  readonly tool: SketchTool | null;
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
  readonly plane: SketchPlane | null;
  /** Settled polygon corners, kept separate from the moving preview point. */
  readonly vertices: readonly PlanPoint[];
  readonly cursor: PlanPoint | null;
  readonly axisLock: "x" | "y" | null;
}

export const IDLE: SketchState = {
  tool: null, phase: "idle", base: 0, anchor: null, profile: [], height: 0, typed: "",
  plane: null, vertices: [], cursor: null, axisLock: null,
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
  return state.cursor ?? state.profile[2] ?? state.profile[0] ?? state.anchor;
}

/** A regular closed circle profile: the existing extrusion path owns its solid. */
export function circleOf(center: PlanPoint, radius: number, segments = 32): readonly PlanPoint[] {
  if (!(radius > 0) || !Number.isFinite(radius) || segments < 3) return [];
  return Array.from({ length: segments }, (_, index) => {
    const angle = index * 2 * Math.PI / segments;
    return [center[0] + radius * Math.cos(angle), center[1] + radius * Math.sin(angle)] as PlanPoint;
  });
}

/** Signed distance to the chord; moving along the chord does not change its bulge. */
export function arcBulge(start: PlanPoint, end: PlanPoint, point: PlanPoint): number {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  return length > 1e-9 ? ((point[1] - start[1]) * dx - (point[0] - start[0]) * dy) / length : 0;
}

/** An open, segmented circular arc through two ends and a signed chord bulge. */
export function arcOf(start: PlanPoint, end: PlanPoint, bulge: number): readonly PlanPoint[] {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (length <= 1e-6 || !Number.isFinite(bulge) || Math.abs(bulge) <= 1e-6) return [];
  const offset = bulge / 2 - length * length / (8 * bulge);
  const center: PlanPoint = [(start[0] + end[0]) / 2 - dy / length * offset,
    (start[1] + end[1]) / 2 + dx / length * offset];
  const radius = Math.hypot(start[0] - center[0], start[1] - center[1]);
  if (!Number.isFinite(radius)) return [];
  const angle = Math.atan2(start[1] - center[1], start[0] - center[0]);
  const sweep = -4 * Math.atan2(2 * bulge, length);
  return Array.from({ length: 33 }, (_, index): PlanPoint => index === 0 ? start : index === 32 ? end :
    [center[0] + radius * Math.cos(angle + sweep * index / 32),
      center[1] + radius * Math.sin(angle + sweep * index / 32)]);
}

export function sizedLine(start: PlanPoint, cursor: PlanPoint, length: number): PlanPoint {
  const dx = cursor[0] - start[0], dy = cursor[1] - start[1];
  const span = Math.hypot(dx, dy);
  return span > 1e-6 ? [start[0] + dx * length / span, start[1] + dy * length / span] : [start[0] + length, start[1]];
}

export function lockedPoint(point: PlanPoint, anchor: PlanPoint, axis: SketchState["axisLock"]): PlanPoint {
  return axis === "x" ? [point[0], anchor[1]] : axis === "y" ? [anchor[0], point[1]] : point;
}

/** One number makes a square; two numbers (3,2 or 3x2) set width and depth. */
export function typedDimensions(text: string): readonly [number, number] | null {
  const parts = text.trim().split(/\s*[,x×;]\s*/i).map(typedNumber);
  if (parts.length > 2 || parts.some((value) => value === null || value <= 0)) return null;
  return [parts[0]!, parts[1] ?? parts[0]!];
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
export function sizedRectangle(anchor: PlanPoint, corner: PlanPoint, side: number, depth = side): readonly PlanPoint[] {
  const dx = corner[0] - anchor[0] >= 0 ? 1 : -1;
  const dz = corner[1] - anchor[1] >= 0 ? 1 : -1;
  return rectangleOf(anchor, [anchor[0] + dx * side, anchor[1] + dz * depth]);
}

/** The action the shell would submit, or null while it is not finished. */
export interface FinishedSketch {
  readonly profile: readonly PlanPoint[];
  readonly height: number;
  readonly base: number;
  readonly plane?: SketchPlane;
  /** Omitted for retained closed profiles; false is an open, unfilled path. */
  readonly closed?: boolean;
}

export function finished(state: SketchState, allowFlat = false, closed = true): FinishedSketch | null {
  if (!closed) {
    if (state.phase !== "profile" || state.profile.length < 2 || state.profile.some((point) => !point.every(Number.isFinite)) ||
        !state.profile.some((point) => Math.hypot(point[0] - state.profile[0]![0], point[1] - state.profile[0]![1]) > 1e-6)) return null;
    return { profile: state.profile, height: 0, base: state.base, closed: false, ...(state.plane ? { plane: state.plane } : {}) };
  }
  if (state.phase !== "height" || !enclosesArea(state.profile) || !Number.isFinite(state.height) || (!allowFlat && Math.abs(state.height) <= 1e-6)) return null;
  return { profile: state.profile, height: state.height, base: state.base, ...(state.plane ? { plane: state.plane } : {}) };
}

/** Esc, and anything else that abandons the action: back to nothing drawn. */
export function cancelled(state: SketchState): SketchState {
  return { ...IDLE, tool: state.tool, plane: state.plane, base: state.base };
}
