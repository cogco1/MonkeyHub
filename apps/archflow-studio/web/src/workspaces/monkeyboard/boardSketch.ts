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
export type SketchSkipReason = "open" | "unsupported" | "degenerate" | "tooManyPoints";
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

type Code = "BOARD_SKETCH_SCALE_REQUIRED" | "BOARD_SKETCH_EMPTY" | "BOARD_SKETCH_FRAME_INVALID" | "BOARD_SKETCH_CALIBRATION_INVALID";
export class BoardSketchError extends Error {
  constructor(public readonly code: Code, message: string) { super(message); this.name = "BoardSketchError"; }
}

const MAX_PROFILE_POINTS = 512;
const MAX_FOOTPRINTS = 64;
const ELLIPSE_SAMPLES = 32;
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

/** The same board element always names the same model element, so re-sending edits it. */
export function footprintElementId(element: ExcalidrawElement): string {
  const safe = element.id.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
  return `board-${safe || "shape"}`.slice(0, MAX_ELEMENT_ID).replace(/-+$/g, "");
}

function rotate([x, y]: Point, [cx, cy]: Point, angle: number): Point {
  const cos = Math.cos(angle), sin = Math.sin(angle);
  return [cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos];
}

function finite(element: ExcalidrawElement): boolean {
  return [element.x, element.y, element.width, element.height, element.angle].every((value) => Number.isFinite(value))
    && element.width > 0 && element.height > 0;
}

/** Ramer–Douglas–Peucker: keep the corners the architect drew, drop the hand's noise. */
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

function signedArea(points: readonly Point[]): number {
  return points.reduce((sum, [x, y], i) => { const [nx, ny] = points[(i + 1) % points.length]; return sum + x * ny - nx * y; }, 0) / 2;
}

/** The closed outline of one element in scene coordinates, or null when it is not a footprint. */
export function closedOutline(element: ExcalidrawElement): Point[] | null {
  if (element.isDeleted || !finite(element)) return null;
  const center: Point = [element.x + element.width / 2, element.y + element.height / 2];
  const local = ([x, y]: Point): Point => rotate([element.x + x, element.y + y], center, element.angle);
  if (element.type === "rectangle" || element.type === "diamond") {
    // Rounded corners are ignored: the massing corners are what the architect meant.
    return ([[0, 0], [element.width, 0], [element.width, element.height], [0, element.height]] as Point[]).map(local);
  }
  if (element.type === "ellipse") {
    return Array.from({ length: ELLIPSE_SAMPLES }, (_, i) => { const a = i * 2 * Math.PI / ELLIPSE_SAMPLES;
      return local([element.width / 2 * (1 + Math.cos(a)), element.height / 2 * (1 + Math.sin(a))]); });
  }
  if (element.type !== "line" && element.type !== "freedraw") return null;
  const raw = (element as ExcalidrawElement & { points: readonly (readonly number[])[] }).points;
  if (raw.length < 3 || raw.some((p) => p.length !== 2 || !p.every((value) => Number.isFinite(value)))) return null;
  const xs = raw.map((p) => p[0]), ys = raw.map((p) => p[1]);
  const diagonal = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const first = raw[0], last = raw[raw.length - 1];
  if (Math.hypot(last[0] - first[0], last[1] - first[1]) > Math.max(6, 0.01 * diagonal)) return null;   // open
  // Excalidraw rotates strokes about their local point bounds (see boardFeedbackGeometry).
  const strokeCenter: Point = [element.x + (Math.min(...xs) + Math.max(...xs)) / 2, element.y + (Math.min(...ys) + Math.max(...ys)) / 2];
  const scene = raw.slice(0, -1).map(([x, y]) => rotate([element.x + x, element.y + y], strokeCenter, element.angle));
  return dedupe(simplify(scene, 0.005 * diagonal));
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
  const sketches: SketchFootprint[] = [], skipped: SketchConversion["skipped"] = [];
  for (const element of elements) {
    // The dimension line stays on the board as a mark, never as geometry.
    if (element.isDeleted || element.frameId !== frame.id || element.id === data.calibration?.elementId) continue;
    if (!FOOTPRINT_TYPES.includes(element.type)) { skipped.push({ elementId: element.id, reason: "unsupported" }); continue; }
    const outline = closedOutline(element);
    if (outline === null) { skipped.push({ elementId: element.id, reason: (element.type === "line" || element.type === "freedraw") && finite(element) ? "open" : "degenerate" }); continue; }
    let profile = dedupe(outline.map(toBuilding));
    if (profile.length < 3 || Math.abs(signedArea(profile)) <= 1e-9) { skipped.push({ elementId: element.id, reason: "degenerate" }); continue; }
    if (profile.length > MAX_PROFILE_POINTS) { skipped.push({ elementId: element.id, reason: "tooManyPoints" }); continue; }
    if (signedArea(profile) < 0) profile = profile.slice().reverse();
    const own = (element.customData as { sketch?: { height?: unknown } } | undefined)?.sketch?.height;
    const height = typeof own === "number" && Number.isFinite(own) && own > 0 ? own : data.storeyHeight;
    sketches.push({ elementId: footprintElementId(element), profile, height, closed: true, baseLevel: data.levelId });
  }
  if (sketches.length === 0) throw new BoardSketchError("BOARD_SKETCH_EMPTY", "Draw at least one closed shape inside the frame.");
  if (sketches.length > MAX_FOOTPRINTS) throw new BoardSketchError("BOARD_SKETCH_EMPTY", `Send at most ${MAX_FOOTPRINTS} footprints at once.`);
  return { sketches, skipped };
}

export function sketchSummary(frameName: string, conversion: SketchConversion, data: SketchFrameData, boardRevisionSha256: string | null): string {
  const units = data.metresPerUnit ? Math.round(1 / data.metresPerUnit) : 0;
  const text = `Board sketch «${frameName}»: ${conversion.sketches.length} footprint(s) on level ${data.levelId}, 1 m = ${units} board units, board ${boardRevisionSha256?.slice(0, 12) ?? "unsaved"}`;
  return text.length <= 240 ? text : `${text.slice(0, 237)}…`;
}
