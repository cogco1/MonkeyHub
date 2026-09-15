/**
 * Closed shapes inside a calibrated sketch frame, as the sketch proposals they
 * already are.
 *
 * A sketch frame is an ordinary Excalidraw frame whose `customData.sketch`
 * carries the one thing the repository has nowhere else: the affine from board
 * scene units to the building plane. Nothing here fabricates that number. Until
 * the architect states one known length inside the frame the conversion refuses,
 * because a drawing without a dimension has proportions and no metres.
 *
 * The conversion is pure and total: every element inside the frame either
 * becomes one footprint or is listed with the reason it was left on the board.
 * Arrows, text and open strokes are never canonical geometry — they stay where
 * they were drawn.
 */
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";

type Point = [number, number];

export interface SketchFrameData {
  kind: "sketch";
  version: 1;
  /** A FrameLevelDto.levelId; "ground" on a freshly modelled project. */
  levelId: string;
  /** Metres. */
  storeyHeight: number;
  /** Null until one known length inside the frame has been stated. */
  metresPerUnit: number | null;
  calibration: { elementId: string; metres: number } | null;
}
export type SketchSkipReason =
  /** A stroke whose ends are too far apart to be one outline. */
  | "open"
  /** An arrow, a text, an image, a nested frame: never canonical geometry. */
  | "unsupported"
  /** Too few points, no area, or a sliver under a millimetre. */
  | "degenerate"
  /** More points than one profile may carry. */
  | "tooManyPoints"
  /** The outline visits the same point twice, which the record refuses. */
  | "selfTouching"
  /** The outline crosses itself, which no solid can be pulled from. */
  | "selfIntersecting"
  /** Excalidraw still calls it a child of the frame, but it no longer is. */
  | "outsideFrame"
  /** A rotated round line, whose drawn centre this module cannot reproduce. */
  | "roundRotated";
export interface SketchFootprint { elementId: string; profile: Point[]; height: number; closed: true; baseLevel: string }
export interface SketchConversion { sketches: SketchFootprint[]; skipped: { elementId: string; reason: SketchSkipReason }[] }

/** What one calibrated frame hands up to the App, exactly as design feedback is handed up. */
export interface BoardSketchRequest {
  projectId: string;
  frameId: string;
  frameName: string;
  boardRevisionSha256: string | null;
  levelId: string;
  sketches: SketchFootprint[];
  skipped: SketchConversion["skipped"];
  /** At most 240 characters: frame name, count, level, scale, board revision prefix. */
  summary: string;
}

type Code = "BOARD_SKETCH_SCALE_REQUIRED" | "BOARD_SKETCH_EMPTY" | "BOARD_SKETCH_FRAME_INVALID"
  | "BOARD_SKETCH_CALIBRATION_INVALID" | "BOARD_SKETCH_ID_COLLISION" | "BOARD_SKETCH_TOO_MANY";
export class BoardSketchError extends Error {
  constructor(public readonly code: Code, message: string) { super(message); this.name = "BoardSketchError"; }
}

const MAX_PROFILE_POINTS = 512;
const MAX_FOOTPRINTS = 64;
const ELLIPSE_SAMPLES = 32;
/** Metres. Anything thinner than a millimetre is a slip of the hand, not a wall. */
const MIN_EXTENT = 0.001;
/** The record rounds a profile point to nine decimals before refusing repeats. */
const POINT_DECIMALS = 9;
/** archflow/project/refs.py: ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ */
const MAX_ELEMENT_ID = 100;
const FOOTPRINT_TYPES = ["rectangle", "diamond", "ellipse", "line", "freedraw"];

export function sketchFrameData(element: ExcalidrawElement): SketchFrameData | null {
  if (element.type !== "frame" || element.isDeleted) return null;
  const data = (element.customData as { sketch?: unknown } | undefined)?.sketch as Partial<SketchFrameData> | undefined;
  if (!data || data.kind !== "sketch" || data.version !== 1 || typeof data.levelId !== "string" || !data.levelId) return null;
  const storeyHeight = typeof data.storeyHeight === "number" && Number.isFinite(data.storeyHeight) && data.storeyHeight > 0 ? data.storeyHeight : 3;
  const metresPerUnit = typeof data.metresPerUnit === "number" && Number.isFinite(data.metresPerUnit) && data.metresPerUnit > 0 ? data.metresPerUnit : null;
  const calibration = data.calibration && typeof data.calibration.elementId === "string" && typeof data.calibration.metres === "number"
    ? { elementId: data.calibration.elementId, metres: data.calibration.metres } : null;
  return { kind: "sketch", version: 1, levelId: data.levelId, storeyHeight, metresPerUnit, calibration };
}

export function newSketchFrameData(levelId: string): SketchFrameData {
  return { kind: "sketch", version: 1, levelId, storeyHeight: 3, metresPerUnit: null, calibration: null };
}

/** The frames on this board that carry a sketch. */
export function sketchFrameIds(elements: readonly ExcalidrawElement[]): Set<string> {
  return new Set(elements.filter((element) => sketchFrameData(element) !== null).map((element) => element.id));
}

/**
 * Whether this element is a sketch frame or one of the shapes inside it.
 *
 * Those shapes are the drawing the architect will send to 3D, not marks on
 * someone else's drawing, so a board-wide action that clears marks must leave
 * them alone.
 */
export function insideSketchFrame(element: ExcalidrawElement, frameIds: ReadonlySet<string>): boolean {
  return frameIds.has(element.id) || (element.frameId !== null && frameIds.has(element.frameId));
}

/** A base a document intent names; a sketch names none and takes the current one. */
export interface BoardDocumentBase { runId: string; sourceStageRef: string | null; stateDigest: string }
export type BoardHandoff =
  | { kind: "document"; base: BoardDocumentBase }
  | { kind: "sketch" }
  | { kind: "none" };

/**
 * Which board hand-off the task workspace should open, given whatever the shell
 * is still holding.
 *
 * The shell clears the other one whenever it takes a new hand-off, so both being
 * set means one of them outlived its journey. The sketch wins that case: it is
 * the one that would otherwise be dropped in silence, because a document intent
 * that has already been submitted still looks like a live instruction here.
 */
export function boardHandoff(
  documentIntent: { modelSource: { runId: string; stateDigest: string }; sourceStageRef: string | null } | null | undefined,
  sketchRequest: BoardSketchRequest | null | undefined,
): BoardHandoff {
  if (sketchRequest) return { kind: "sketch" };
  if (documentIntent) {
    return { kind: "document", base: { runId: documentIntent.modelSource.runId,
      sourceStageRef: documentIntent.sourceStageRef, stateDigest: documentIntent.modelSource.stateDigest } };
  }
  return { kind: "none" };
}

/**
 * Leaving the board for the conversation, in the same tab: a reload must not
 * reopen the board behind the answer the sketch is waiting for. The candidate
 * a host page named, and every other parameter, are kept.
 */
export function conversationUrl(currentUrl: string): string {
  const url = new URL(currentUrl);
  url.searchParams.delete("view");
  for (const key of ["documentRun", "documentSource", "documentPage", "documentRevision"]) url.searchParams.delete(key);
  return url.href;
}

function twoPointLength(line: ExcalidrawElement): number | null {
  if ((line.type !== "line" && line.type !== "arrow") || line.isDeleted) return null;
  const points = (line as ExcalidrawElement & { points: readonly (readonly number[])[] }).points;
  if (points.length !== 2 || points.some((p) => p.length !== 2 || !p.every((value) => Number.isFinite(value)))) return null;
  const length = Math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]);
  return length > 0 ? length : null;
}

/** A two-point line's length is rotation-invariant, so the scale never depends on how it was drawn. */
export function calibrateSketchFrame(data: SketchFrameData, line: ExcalidrawElement, metres: number): SketchFrameData {
  const length = twoPointLength(line);
  if (length === null) throw new BoardSketchError("BOARD_SKETCH_CALIBRATION_INVALID", "Select one straight two-point line inside the frame to state a known length.");
  if (!Number.isFinite(metres) || metres <= 0) throw new BoardSketchError("BOARD_SKETCH_CALIBRATION_INVALID", "The known length must be a positive number of metres.");
  return { ...data, metresPerUnit: metres / length, calibration: { elementId: line.id, metres } };
}

/**
 * The same board element always names the same model element, so re-sending
 * edits it. Only characters the record's identifier rule forbids are replaced —
 * case and the dot and underscore it allows are kept, because folding them
 * would make two different shapes claim one element and lose one of them.
 */
export function footprintElementId(element: ExcalidrawElement): string {
  const safe = element.id.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[^A-Za-z0-9]+/, "");
  return `board-${safe || "shape"}`.slice(0, MAX_ELEMENT_ID);
}

function rotate([x, y]: Point, [cx, cy]: Point, angle: number): Point {
  const cos = Math.cos(angle), sin = Math.sin(angle);
  return [cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos];
}

function finite(element: ExcalidrawElement): boolean {
  return [element.x, element.y, element.width, element.height, element.angle].every((value) => Number.isFinite(value))
    && element.width > 0 && element.height > 0;
}

/** Ramer–Douglas–Peucker on an open path: keep the corners, drop the hand's noise. */
function simplify(points: Point[], epsilon: number): Point[] {
  if (points.length <= 2) return points;
  const [first, last] = [points[0], points[points.length - 1]];
  let index = -1, largest = 0;
  const dx = last[0] - first[0], dy = last[1] - first[1], norm = Math.hypot(dx, dy) || 1;
  for (let i = 1; i < points.length - 1; i += 1) {
    const d = Math.abs(dy * points[i][0] - dx * points[i][1] + last[0] * first[1] - last[1] * first[0]) / norm;
    if (d > largest) { largest = d; index = i; }
  }
  if (largest <= epsilon || index < 0) return [first, last];
  return [...simplify(points.slice(0, index + 1), epsilon).slice(0, -1), ...simplify(points.slice(index), epsilon)];
}

function dedupe(points: Point[]): Point[] {
  const out: Point[] = [];
  for (const p of points) { const q = out[out.length - 1]; if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-9) out.push(p); }
  if (out.length > 1 && Math.hypot(out[0][0] - out[out.length - 1][0], out[0][1] - out[out.length - 1][1]) <= 1e-9) out.pop();
  return out;
}

/**
 * A closed ring has no first and last point to draw a baseline between, and a
 * baseline of zero length makes every distance zero — which is how a carefully
 * closed stroke (Excalidraw snaps its last point onto its first) would collapse
 * to a single point. So the ring is cut at the vertex furthest from its start
 * and the two halves are simplified as the open paths they then are.
 */
function simplifyRing(ring: Point[], epsilon: number): Point[] {
  if (ring.length <= 3) return ring;
  let far = 1, furthest = -1;
  for (let i = 1; i < ring.length; i += 1) {
    const distance = Math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1]);
    if (distance > furthest) { furthest = distance; far = i; }
  }
  const head = simplify(ring.slice(0, far + 1), epsilon);
  const tail = simplify([...ring.slice(far), ring[0]], epsilon);
  return dedupe([...head, ...tail.slice(1, -1)]);
}

function signedArea(points: readonly Point[]): number {
  return points.reduce((sum, [x, y], i) => { const [nx, ny] = points[(i + 1) % points.length]; return sum + x * ny - nx * y; }, 0) / 2;
}

/** Two segments that share no endpoint must not meet; a crossed outline encloses nothing. */
function crosses(a1: Point, a2: Point, b1: Point, b2: Point): boolean {
  const side = (p: Point, q: Point, r: Point) => {
    const value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    return value > 1e-12 ? 1 : value < -1e-12 ? -1 : 0;
  };
  const between = (p: Point, q: Point, r: Point) =>
    Math.min(p[0], q[0]) - 1e-12 <= r[0] && r[0] <= Math.max(p[0], q[0]) + 1e-12 &&
    Math.min(p[1], q[1]) - 1e-12 <= r[1] && r[1] <= Math.max(p[1], q[1]) + 1e-12;
  const d1 = side(b1, b2, a1), d2 = side(b1, b2, a2), d3 = side(a1, a2, b1), d4 = side(a1, a2, b2);
  if (d1 !== d2 && d3 !== d4) return true;
  return (d1 === 0 && between(b1, b2, a1)) || (d2 === 0 && between(b1, b2, a2))
    || (d3 === 0 && between(a1, a2, b1)) || (d4 === 0 && between(a1, a2, b2));
}

function selfIntersects(profile: readonly Point[]): boolean {
  const n = profile.length;
  for (let i = 0; i < n; i += 1) {
    for (let j = i + 2; j < n; j += 1) {
      if (i === 0 && j === n - 1) continue;   // the closing edge is adjacent to the first
      if (crosses(profile[i], profile[(i + 1) % n], profile[j], profile[(j + 1) % n])) return true;
    }
  }
  return false;
}

/** The record's own repeat rule, applied before a whole batch is refused for one shape. */
function visitsAPointTwice(profile: readonly Point[]): boolean {
  const round = (value: number) => Number(value.toFixed(POINT_DECIMALS));
  const seen = new Set(profile.map((p) => `${round(p[0])},${round(p[1])}`));
  return seen.size !== profile.length;
}

function extent(profile: readonly Point[]): number {
  const xs = profile.map((p) => p[0]), ys = profile.map((p) => p[1]);
  return Math.min(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
}

function shortestEdge(profile: readonly Point[]): number {
  return profile.reduce((least, [x, y], i) => {
    const [nx, ny] = profile[(i + 1) % profile.length];
    return Math.min(least, Math.hypot(nx - x, ny - y));
  }, Infinity);
}

type Outline = { points: Point[]; reason?: undefined } | { points?: undefined; reason: SketchSkipReason };

/** The closed outline of one element in scene coordinates, or the reason it is not one. */
function outlineOf(element: ExcalidrawElement): Outline {
  if (element.isDeleted || !finite(element)) return { reason: "degenerate" };
  const center: Point = [element.x + element.width / 2, element.y + element.height / 2];
  const local = ([x, y]: Point): Point => rotate([element.x + x, element.y + y], center, element.angle);
  if (element.type === "rectangle") {
    // Rounded corners are ignored: the massing corners are what the architect meant.
    return { points: ([[0, 0], [element.width, 0], [element.width, element.height], [0, element.height]] as Point[]).map(local) };
  }
  if (element.type === "diamond") {
    // A diamond is the rhombus through its edge midpoints, not its bounding box:
    // sending the box would give the massing twice the area that was drawn.
    return { points: ([[element.width / 2, 0], [element.width, element.height / 2],
      [element.width / 2, element.height], [0, element.height / 2]] as Point[]).map(local) };
  }
  if (element.type === "ellipse") {
    return { points: Array.from({ length: ELLIPSE_SAMPLES }, (_, i) => { const a = i * 2 * Math.PI / ELLIPSE_SAMPLES;
      return local([element.width / 2 * (1 + Math.cos(a)), element.height / 2 * (1 + Math.sin(a))]); }) };
  }
  if (element.type !== "line" && element.type !== "freedraw") return { reason: "unsupported" };
  // Excalidraw rotates a round line about its bezier bounds, which this module
  // cannot reproduce from the element alone; a rotated one would land tens of
  // centimetres out. Lines drawn on the board are sharp by default.
  if (element.type === "line" && element.roundness !== null && element.angle !== 0) return { reason: "roundRotated" };
  const raw = (element as ExcalidrawElement & { points: readonly (readonly number[])[] }).points;
  if (raw.length < 3 || raw.some((p) => p.length !== 2 || !p.every((value) => Number.isFinite(value)))) return { reason: "degenerate" };
  const xs = raw.map((p) => p[0]), ys = raw.map((p) => p[1]);
  const diagonal = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const first = raw[0], last = raw[raw.length - 1];
  if (Math.hypot(last[0] - first[0], last[1] - first[1]) > Math.max(6, 0.01 * diagonal)) return { reason: "open" };
  // Excalidraw rotates strokes about their local point bounds (see boardFeedbackGeometry).
  const strokeCenter: Point = [element.x + (Math.min(...xs) + Math.max(...xs)) / 2, element.y + (Math.min(...ys) + Math.max(...ys)) / 2];
  const scene = raw.slice(0, -1).map(([x, y]) => rotate([element.x + x, element.y + y], strokeCenter, element.angle));
  // Dedupe before simplifying: a stroke closed onto its own first point would
  // otherwise give the ring a zero-length baseline and collapse to one point.
  const ring = dedupe(scene);
  if (ring.length < 3) return { reason: "degenerate" };
  const simplified = simplifyRing(ring, 0.005 * diagonal);
  return simplified.length < 3 ? { reason: "degenerate" } : { points: simplified };
}


/**
 * Every closed shape inside `frame`, in project metres on the default sketch
 * plane: X along the frame's bottom edge, the profile's second coordinate the
 * building's own z, with the frame's bottom-left corner as the origin.
 */
export function sketchActionsFromFrame(elements: readonly ExcalidrawElement[], frame: ExcalidrawElement): SketchConversion {
  const data = sketchFrameData(frame);
  if (!data || !finite(frame)) throw new BoardSketchError("BOARD_SKETCH_FRAME_INVALID", "Select a sketch frame.");
  if (data.metresPerUnit === null) throw new BoardSketchError("BOARD_SKETCH_SCALE_REQUIRED", "State one known length inside the frame before sending it to 3D.");
  const m = data.metresPerUnit, baseline = frame.y + frame.height;
  const toBuilding = ([sx, sy]: Point): Point => [(sx - frame.x) * m, (baseline - sy) * m];
  // Excalidraw keeps frameId while any part of a shape still overlaps the frame,
  // and an arrow-key nudge never revisits membership at all. Only a shape that
  // is wholly inside the frame is a footprint of it. Containment is measured in
  // the same scene axes `toBuilding` maps from — Excalidraw gives a frame no
  // rotation handle, and accepting one here would accept by one frame and map
  // by another.
  const insideFrame = ([sx, sy]: Point): boolean =>
    sx >= frame.x - 1e-6 && sx <= frame.x + frame.width + 1e-6
    && sy >= frame.y - 1e-6 && sy <= frame.y + frame.height + 1e-6;
  const sketches: SketchFootprint[] = [], skipped: SketchConversion["skipped"] = [];
  const claimed = new Map<string, string>();
  for (const element of elements) {
    // The dimension line stays on the board as a mark, never as geometry.
    if (element.isDeleted || element.frameId !== frame.id || element.id === data.calibration?.elementId) continue;
    const skip = (reason: SketchSkipReason) => { skipped.push({ elementId: element.id, reason }); };
    if (!FOOTPRINT_TYPES.includes(element.type)) { skip("unsupported"); continue; }
    const outline = outlineOf(element);
    if (outline.points === undefined) { skip(outline.reason); continue; }
    if (!outline.points.every(insideFrame)) { skip("outsideFrame"); continue; }
    let profile = dedupe(outline.points.map(toBuilding));
    if (profile.length < 3 || Math.abs(signedArea(profile)) <= 1e-9) { skip("degenerate"); continue; }
    if (profile.length > MAX_PROFILE_POINTS) { skip("tooManyPoints"); continue; }
    // A millimetre is the finest a drawn massing can mean; below it OCCT is
    // asked for a solid with no thickness and answers with an invalid shape.
    if (extent(profile) < MIN_EXTENT || shortestEdge(profile) < MIN_EXTENT) { skip("degenerate"); continue; }
    // One pinched outline would otherwise refuse the whole batch at the server,
    // taking every other footprint down with it.
    if (visitsAPointTwice(profile)) { skip("selfTouching"); continue; }
    if (selfIntersects(profile)) { skip("selfIntersecting"); continue; }
    if (signedArea(profile) < 0) profile = profile.slice().reverse();
    const elementId = footprintElementId(element);
    const owner = claimed.get(elementId);
    if (owner !== undefined) {
      throw new BoardSketchError("BOARD_SKETCH_ID_COLLISION",
        `Two shapes in this frame would author the same element «${elementId}»; one of them would be lost. Redraw one of them.`);
    }
    claimed.set(elementId, element.id);
    const own = (element.customData as { sketch?: { height?: unknown } } | undefined)?.sketch?.height;
    const height = typeof own === "number" && Number.isFinite(own) && own > 0 ? own : data.storeyHeight;
    sketches.push({ elementId, profile, height, closed: true, baseLevel: data.levelId });
  }
  if (sketches.length === 0) throw new BoardSketchError("BOARD_SKETCH_EMPTY", "Draw at least one closed shape inside the frame.");
  if (sketches.length > MAX_FOOTPRINTS) throw new BoardSketchError("BOARD_SKETCH_TOO_MANY", `Send at most ${MAX_FOOTPRINTS} footprints at once.`);
  return { sketches, skipped };
}

/** What was left on the board, in the words the proposal itself can carry. */
const SKIPPED_WORD: Record<SketchSkipReason, string> = {
  open: "open", unsupported: "not geometry", degenerate: "too small", tooManyPoints: "too many points",
  selfTouching: "self-touching", selfIntersecting: "self-crossing", outsideFrame: "outside the frame",
  roundRotated: "rotated and round",
};

export function sketchSummary(frameName: string, conversion: SketchConversion, data: SketchFrameData, boardRevisionSha256: string | null): string {
  // A coarse scale rounds to "0 board units" once one unit is two metres wide.
  const units = data.metresPerUnit ? 1 / data.metresPerUnit : 0;
  const scale = units >= 10 ? String(Math.round(units)) : units.toFixed(1);
  // The board is left the moment this is sent, so what it would have said about
  // the shapes it kept has to travel with the proposal instead.
  const reasons = [...new Set(conversion.skipped.map((row) => SKIPPED_WORD[row.reason]))].join(", ");
  const left = conversion.skipped.length === 0 ? "" : `, ${conversion.skipped.length} left on the board (${reasons})`;
  const text = `Board sketch «${frameName}»: ${conversion.sketches.length} footprint(s) on level ${data.levelId}, 1 m = ${scale} board units${left}, board ${boardRevisionSha256?.slice(0, 12) ?? "unsaved"}`;
  // The record counts characters, and half a surrogate pair is not one.
  const characters = Array.from(text);
  return characters.length <= 240 ? text : `${characters.slice(0, 239).join("")}…`;
}
