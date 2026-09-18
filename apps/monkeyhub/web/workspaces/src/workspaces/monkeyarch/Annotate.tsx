/**
 * Select + draw + say: an overlay on the model for the marks an architect
 * makes with their words.
 *
 * Four tools — circle ("this area"), arrow ("this way, this far"), keep and
 * remove marks. A stroke is drawn on this canvas; when it ends, its points
 * are sampled and each sample is read off the loaded model the way a click
 * is read: the object's own strings and the world point the ray met. The
 * camera is recorded with it. Nothing here decides what was hit — the DTO
 * carries the strokes and the file's strings, and the server names them
 * against the record and prints back what it read.
 *
 * Strokes are kept in canvas pixels of the viewpoint they were drawn in; the
 * camera that goes with them says which viewpoint that was.
 */

import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import type { GestureDto, GestureHitDto, ModelAnnotationsDto } from "../../api/generated";
import type { MessageKey } from "../../../../src/i18n/messages.en";
import { useT } from "../../i18n/useT";
import type { CameraState, SampleHit, Vec3, ViewportController } from "./viewer/ThreeDmViewport";

export type GestureTool = GestureDto["kind"];

export interface AnnotationStyle {
  readonly color: string;
  readonly lineWidth: 2 | 4 | 6;
}

export const GESTURE_TOOLS: ReadonlyArray<{
  kind: GestureTool;
  glyph: string;
  labelKey: MessageKey;
  titleKey: MessageKey;
}> = [
  {
    kind: "circle",
    glyph: "◯",
    labelKey: "stage.tools.circle.label",
    titleKey: "stage.tools.circle.title",
  },
  {
    kind: "arrow",
    glyph: "↗",
    labelKey: "stage.tools.arrow.label",
    titleKey: "stage.tools.arrow.title",
  },
  { kind: "freehand", glyph: "✎", labelKey: "stage.tools.freehand.label", titleKey: "stage.tools.freehand.title" },
  { kind: "line", glyph: "╱", labelKey: "stage.tools.line.label", titleKey: "stage.tools.line.title" },
  { kind: "ruler", glyph: "↔", labelKey: "stage.tools.ruler.label", titleKey: "stage.tools.ruler.title" },
  { kind: "arc", glyph: "⌒", labelKey: "stage.tools.arc.label", titleKey: "stage.tools.arc.title" },
  {
    kind: "keep",
    glyph: "✓",
    labelKey: "stage.tools.keep.label",
    titleKey: "stage.tools.keep.title",
  },
  {
    kind: "remove",
    glyph: "✗",
    labelKey: "stage.tools.remove.label",
    titleKey: "stage.tools.remove.title",
  },
];

/** A stroke is sampled every this many pixels along its length. */
const SAMPLE_PX = 8;
/** A circle's inside is sampled on this grid, so it names what it encloses. */
const INTERIOR_PX = 14;
/** No gesture reads the model more often than this, whatever its size. */
const MAX_SAMPLES = 400;
/** A pointer that moved less than this drew a mark, not a stroke. */
const MARK_SLOP_PX = 6;

type Point = readonly [number, number];

function colours() {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    accent: read("--accent", "#2f80ed"),
    held: read("--held", "#58b368"),
    violated: read("--violated", "#e5534b"),
  };
}

function resample(points: readonly Point[], step: number): Point[] {
  if (points.length === 0) return [];
  const out: Point[] = [points[0]];
  let carried = 0;
  for (let i = 1; i < points.length; i += 1) {
    const [ax, ay] = points[i - 1];
    const [bx, by] = points[i];
    const length = Math.hypot(bx - ax, by - ay);
    let along = step - carried;
    while (along <= length) {
      const t = along / length;
      out.push([ax + (bx - ax) * t, ay + (by - ay) * t]);
      along += step;
    }
    carried = length - (along - step);
  }
  const last = points[points.length - 1];
  const tail = out[out.length - 1];
  if (tail[0] !== last[0] || tail[1] !== last[1]) out.push(last);
  return out;
}

function insidePolygon([x, y]: Point, polygon: readonly Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    const crosses = yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

function interior(polygon: readonly Point[], step: number): Point[] {
  const xs = polygon.map((p) => p[0]);
  const ys = polygon.map((p) => p[1]);
  const out: Point[] = [];
  for (let y = Math.min(...ys); y <= Math.max(...ys); y += step) {
    for (let x = Math.min(...xs); x <= Math.max(...xs); x += step) {
      if (insidePolygon([x, y], polygon)) out.push([x, y]);
    }
  }
  return out;
}

function everyNth<T>(items: readonly T[], cap: number): T[] {
  if (items.length <= cap) return [...items];
  const stride = items.length / cap;
  const out: T[] = [];
  for (let i = 0; i < cap; i += 1) out.push(items[Math.floor(i * stride)]);
  return out;
}

function hitKey(hit: SampleHit): string {
  return hit.objectName ?? JSON.stringify(hit.userStrings);
}

function toHitDto(hit: SampleHit): GestureHitDto {
  return { objectName: hit.objectName, userStrings: hit.userStrings, world: hit.world };
}

function subtract(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function norm(v: Vec3): number {
  return Math.hypot(v[0], v[1], v[2]);
}

/** Persisted screen geometry. A straight tool never inherits a curved drag. */
export function gestureScreen(kind: GestureTool, points: readonly Point[]): Point[] {
  if ((kind === "arrow" || kind === "line" || kind === "ruler") && points.length > 1) {
    return [points[0], points[points.length - 1]];
  }
  return [...points];
}

/** Pointer-up is the authoritative endpoint for a straight annotation. */
export function completedStroke(
  kind: GestureTool,
  points: readonly Point[],
  pointerUp: Point,
): Point[] {
  if (points.length === 0) return [];
  if (kind === "arrow" || kind === "line" || kind === "ruler") {
    return [points[0], pointerUp];
  }
  const last = points[points.length - 1];
  return Math.hypot(last[0] - pointerUp[0], last[1] - pointerUp[1]) < 0.01
    ? [...points]
    : [...points, pointerUp];
}

/** A three-point arc needs area; collinear points deliberately make no mark. */
export function isValidArc(points: readonly Point[]): boolean {
  if (points.length !== 3) return false;
  const [a, b, c] = points;
  return Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1;
}

/** Pointer-up uses the actual endpoint, not an early pointer-move sample. */
export function arcBaseFromStroke(start: Point, pointerUp: Point): readonly [Point, Point] | null {
  return Math.hypot(pointerUp[0] - start[0], pointerUp[1] - start[1]) >= MARK_SLOP_PX
    ? [start, pointerUp]
    : null;
}

/** Points on the same circular sweep rendered for a three-point arc. */
export function arcScreenPoints(points: readonly Point[], step = SAMPLE_PX): Point[] {
  const circle = points.length === 3 ? threePointCircle(points[0], points[1], points[2]) : null;
  if (circle === null) return [];
  const turn = (from: number, to: number) => (to - from + Math.PI * 2) % (Math.PI * 2);
  const sweep = circle.anticlockwise ? -turn(circle.end, circle.start) : turn(circle.start, circle.end);
  // A nearly collinear third point can imply a very large circle. Bound
  // allocation before sampling, leaving space for both ends and the third point.
  const count = Math.max(1, Math.min(MAX_SAMPLES - 2, Math.ceil(Math.abs(sweep) * circle.radius / step)));
  const sampled = Array.from({ length: count + 1 }, (_, index): Point => {
    const angle = circle.start + sweep * (index / count);
    return [circle.center[0] + circle.radius * Math.cos(angle), circle.center[1] + circle.radius * Math.sin(angle)];
  });
  return [...sampled, points[2]];
}

/** The hit samples match each tool's meaning; arrows mean their tip alone. */
export function gestureSamplePoints(
  kind: GestureTool,
  points: readonly Point[],
  geometry: readonly Point[],
): Point[] | null {
  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return null;
  const travelled = Math.hypot(last[0] - first[0], last[1] - first[1]);
  if (kind === "keep" || kind === "remove") return [last];
  if (kind === "arrow") return travelled < MARK_SLOP_PX ? null : [last];
  if (kind === "circle") {
    return travelled < MARK_SLOP_PX && points.length < 8
      ? null
      : everyNth([...resample(points, SAMPLE_PX), ...interior(points, INTERIOR_PX)], MAX_SAMPLES);
  }
  if (kind === "arc") return isValidArc(geometry) ? everyNth(arcScreenPoints(geometry), MAX_SAMPLES) : null;
  if (kind === "freehand") return everyNth(resample(points, SAMPLE_PX), MAX_SAMPLES);
  return travelled < MARK_SLOP_PX ? null : everyNth(resample(geometry, SAMPLE_PX), MAX_SAMPLES);
}

function pointSegmentDistance(point: Point, start: Point, end: Point): number {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const length = dx * dx + dy * dy;
  const fraction = length === 0 ? 0 : Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length));
  return Math.hypot(point[0] - start[0] - fraction * dx, point[1] - start[1] - fraction * dy);
}

function segmentsNear(a: Point, b: Point, c: Point, d: Point, radius: number): boolean {
  const cross = (p: Point, q: Point, r: Point) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  // Proper crossing, then endpoint distances (also cover collinear segments).
  if (cross(a, b, c) * cross(a, b, d) < 0 && cross(c, d, a) * cross(c, d, b) < 0) return true;
  return Math.min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d), pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b)) <= radius;
}

/** Whole-stroke erasing follows the swept cursor, including a fast move's gap. */
export function annotationIntersectsEraser(gesture: GestureDto, from: Point, to: Point, radius = 10): boolean {
  const points = gesture.screen;
  if (points.length === 0) return false;
  const reach = radius + (gesture.lineWidth ?? 2) / 2;
  const last = points[points.length - 1];
  if (gesture.kind === "keep" || gesture.kind === "remove") return pointSegmentDistance(last, from, to) <= reach + 9;
  // arcScreenPoints appends the third defining point for model sampling. It
  // is already on the rendered arc; joining it to the endpoint adds a chord.
  const path = gesture.kind === "arc" ? arcScreenPoints(points, 3).slice(0, -1) : points;
  if (path.length === 1) return pointSegmentDistance(path[0], from, to) <= reach;
  for (let index = 1; index < path.length; index += 1) {
    if (segmentsNear(from, to, path[index - 1], path[index], reach)) return true;
  }
  if (gesture.kind === "circle" && segmentsNear(from, to, last, points[0], reach)) return true;
  if (gesture.kind === "arrow") {
    const start = points[0], angle = Math.atan2(last[1] - start[1], last[0] - start[0]);
    for (const delta of [-Math.PI / 6, Math.PI / 6]) {
      const tip: Point = [last[0] - 11 * Math.cos(angle + delta), last[1] - 11 * Math.sin(angle + delta)];
      if (segmentsNear(from, to, last, tip, reach)) return true;
    }
  }
  if (gesture.kind === "ruler" && points.length > 1) {
    const angle = Math.atan2(last[1] - points[0][1], last[0] - points[0][0]);
    const nx = -Math.sin(angle) * 7, ny = Math.cos(angle) * 7;
    for (const point of [points[0], last]) {
      if (segmentsNear(from, to, [point[0] - nx, point[1] - ny], [point[0] + nx, point[1] + ny], reach)) return true;
    }
  }
  return false;
}


function closeVector(left: readonly number[], right: readonly number[]): boolean {
  return left.length === right.length && left.every((value, index) => Math.abs(value - right[index]!) <= 1e-6);
}

/** A frozen review is one view. Mixed-view ink must be resolved before promotion. */
export function tracingPaperViewMatches(camera: CameraState, gestures: readonly GestureDto[]): boolean {
  return gestures.length > 0 && gestures.every((gesture) => {
    const drawn = gesture.camera as typeof gesture.camera & { projection?: CameraState["projection"]; zoom?: number };
    return closeVector(drawn.position, camera.position) && closeVector(drawn.target, camera.target)
      && closeVector(drawn.up, camera.up) && Math.abs(drawn.fov - camera.fov) <= 1e-6
      && drawn.projection === camera.projection
      && drawn.zoom != null && Math.abs(drawn.zoom - camera.zoom) <= 1e-6;
  });
}

/** Freeze camera, ink and pixels in this turn, not after an asynchronous save. */
export async function captureTracingPaperReview(
  viewport: Pick<ViewportController, "camera" | "viewportSize" | "capturePng">,
  gestures: readonly GestureDto[],
  save: () => Promise<ModelAnnotationsDto>,
) {
  const camera = structuredClone(viewport.camera());
  const screenSize = viewport.viewportSize();
  if (!camera || !screenSize || !tracingPaperViewMatches(camera, gestures)
      || gestures.some((mark) => !mark.screenSize || !closeVector(mark.screenSize, screenSize))) {
    throw new Error("Tracing Paper is not registered to this view. Restore its camera and size, or redraw legacy marks without projection/zoom.");
  }
  // Both operations start before yielding: navigation during save cannot replace
  // the pixels captured for the original model and annotation revision.
  const [saved, viewportPng] = await Promise.all([save(), viewport.capturePng()]);
  if (!saved.revisionSha256 || !viewportPng) throw new Error("The Tracing Paper review could not be captured and saved.");
  return { saved, camera, screenSize, viewportPng };
}

/** Composite the exact saved ink over the WebGL capture; neither source is mutated. */
export async function renderTracingPaperSnapshotPng(viewportPng: Blob, gestures: readonly GestureDto[]): Promise<Blob> {
  const size = gestures[0]?.screenSize;
  if (!size || gestures.length === 0 || gestures.some((gesture) => !gesture.screenSize
      || gesture.screenSize[0] !== size[0] || gesture.screenSize[1] !== size[1])) {
    throw new Error("Tracing Paper marks do not share one saved viewport size.");
  }
  const bitmap = await createImageBitmap(viewportPng);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width; canvas.height = bitmap.height;
  const context = canvas.getContext("2d");
  if (!context) { bitmap.close(); throw new Error("The Tracing Paper snapshot canvas is unavailable."); }
  context.drawImage(bitmap, 0, 0); bitmap.close();
  context.save(); context.scale(canvas.width / size[0], canvas.height / size[1]);
  const palette = { accent: "#2f80ed", held: "#58b368", violated: "#e5534b" };
  for (const gesture of gestures) drawGesture(context, gesture.kind, gesture.screen, palette, false,
    { color: gesture.color ?? palette.accent, lineWidth: gesture.lineWidth === 4 || gesture.lineWidth === 6 ? gesture.lineWidth : 2 },
    gesture.label ?? null);
  context.restore();
  return new Promise((resolve, reject) => canvas.toBlob((blob) => blob && blob.size > 0 ? resolve(blob)
    : reject(new Error("The Tracing Paper snapshot did not produce a PNG.")), "image/png"));
}

export function Annotate({
  viewportRef,
  tool,
  gestures,
  onGesture,
  style,
  cancelToken,
  eraser = false,
  onErase,
}: {
  viewportRef: RefObject<ViewportController | null>;
  /** The armed tool; null lets the pointer through to the orbit. */
  tool: GestureTool | null;
  /** The marks already made, redrawn in the pixels they were drawn in. */
  gestures: readonly GestureDto[];
  onGesture(gesture: GestureDto): void;
  style: AnnotationStyle;
  /** Changes only clear in-progress ink; committed annotations remain. */
  cancelToken: number;
  eraser?: boolean;
  /** One completed drag is one undoable deletion; cancel never calls this. */
  onErase?(indices: readonly number[]): void;
}) {
  const t = useT();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const savedCanvasRef = useRef<HTMLCanvasElement>(null);
  const strokeRef = useRef<Point[] | null>(null);
  const pointerRef = useRef<number | null>(null);
  const erasedRef = useRef(new Set<number>());
  const erasePointRef = useRef<Point | null>(null);
  const paintFrameRef = useRef<number | null>(null);
  const savedDirtyRef = useRef(true);
  const [temporaryOrbit, setTemporaryOrbit] = useState(false);
  const arcBaseRef = useRef<readonly [Point, Point] | null>(null);
  const [arcBase, setArcBase] = useState<readonly [Point, Point] | null>(null);
  const [rulerLabel, setRulerLabel] = useState("");
  // Marks are held in the view they were drawn in. When the camera leaves
  // that view the ink no longer sits on what it meant, so it fades and the
  // stage says why; the meaning - the hits and the camera - was kept at
  // draw time and still travels with the sentence.
  const [moved, setMoved] = useState(false);
  useEffect(() => {
    if (gestures.length === 0) {
      setMoved(false);
      return undefined;
    }
    const drawnIn = gestures[0].camera;
    const drawnSize = gestures[0].screenSize;
    const same = (a: readonly number[], b: readonly number[]) =>
      a.every((value, index) => Math.abs(value - b[index]) < 1e-6);
    const check = () => {
      const camera = viewportRef.current?.camera();
      if (!camera) return;
      const rect = canvasRef.current?.getBoundingClientRect();
      const framing = drawnIn as typeof drawnIn & { projection?: CameraState["projection"]; zoom?: number };
      setMoved(
        !same(camera.position, drawnIn.position) ||
          !same(camera.target, drawnIn.target) ||
          !same(camera.up, drawnIn.up) ||
          Math.abs(camera.fov - drawnIn.fov) > 1e-6 ||
          (framing.projection !== undefined && framing.projection !== camera.projection) ||
          (framing.zoom !== undefined && Math.abs(framing.zoom - camera.zoom) > 1e-6) ||
          (drawnSize !== undefined && drawnSize !== null && rect !== undefined &&
            (Math.round(rect.width) !== drawnSize[0] || Math.round(rect.height) !== drawnSize[1])),
      );
    };
    check();
    const timer = window.setInterval(check, 300);
    return () => window.clearInterval(timer);
  }, [gestures, viewportRef]);

  const contextFor = (canvas: HTMLCanvasElement | null) => {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const scale = window.devicePixelRatio || 1;
    // The bitmap follows the CSS box, never the other way round: the box is
    // the stage's, fixed by the stylesheet, so this cannot feed back.
    const width = Math.max(1, Math.round(rect.width * scale));
    const height = Math.max(1, Math.round(rect.height * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    return context;
  };

  const draw = useCallback(() => {
    const palette = colours();
    if (savedDirtyRef.current) {
      const saved = contextFor(savedCanvasRef.current);
      if (saved) {
        saved.globalAlpha = moved ? 0.3 : 1;
        gestures.forEach((gesture, index) => {
          if (!erasedRef.current.has(index)) drawGesture(saved, gesture.kind, gesture.screen, palette, false, {
            color: gesture.color ?? palette.accent,
            lineWidth: gesture.lineWidth === 4 || gesture.lineWidth === 6 ? gesture.lineWidth : 2,
          }, gesture.label ?? null);
        });
      }
      savedDirtyRef.current = false;
    }
    const context = contextFor(canvasRef.current);
    if (!context) return;
    const live = strokeRef.current;
    if (!eraser && tool && live && live.length > 0) drawGesture(context, tool, live, palette, true, style, tool === "ruler" ? rulerLabel : null);
  }, [gestures, moved, rulerLabel, style, tool, eraser]);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const schedulePaint = useCallback(() => {
    if (paintFrameRef.current !== null) return;
    paintFrameRef.current = requestAnimationFrame(() => {
      paintFrameRef.current = null;
      drawRef.current();
    });
  }, []);

  useEffect(() => {
    savedDirtyRef.current = true;
    schedulePaint();
  }, [draw, schedulePaint]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const observer = new ResizeObserver(() => { savedDirtyRef.current = true; schedulePaint(); });
    observer.observe(canvas);
    const resize = () => { savedDirtyRef.current = true; schedulePaint(); };
    window.addEventListener("resize", resize);
    return () => { observer.disconnect(); window.removeEventListener("resize", resize); };
  }, [schedulePaint]);

  const cancel = useCallback(() => {
    strokeRef.current = null;
    arcBaseRef.current = null;
    const pointer = pointerRef.current;
    pointerRef.current = null;
    const canvas = canvasRef.current;
    if (pointer !== null && canvas?.hasPointerCapture?.(pointer)) canvas.releasePointerCapture(pointer);
    erasedRef.current.clear();
    erasePointRef.current = null;
    savedDirtyRef.current = true;
    setArcBase(null);
    schedulePaint();
  }, [schedulePaint]);

  useEffect(() => cancel(), [cancel, cancelToken, tool, eraser]);
  useEffect(() => {
    // Pointer capture is missing in some embedded browsers. Their window
    // events still finish/cancel the active stroke when it leaves the overlay.
    const forwardUncaptured = (event: PointerEvent) => {
      const canvas = canvasRef.current;
      if (!canvas || pointerRef.current !== event.pointerId || event.target === canvas || canvas.hasPointerCapture?.(event.pointerId)) return;
      canvas.dispatchEvent(new PointerEvent(event.type, event));
    };
    window.addEventListener("pointermove", forwardUncaptured);
    window.addEventListener("pointerup", forwardUncaptured);
    window.addEventListener("pointercancel", forwardUncaptured);
    return () => {
      window.removeEventListener("pointermove", forwardUncaptured);
      window.removeEventListener("pointerup", forwardUncaptured);
      window.removeEventListener("pointercancel", forwardUncaptured);
    };
  }, []);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (canvasRef.current?.closest("[inert], [aria-hidden='true']")) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
      if (event.key === "Escape") cancel();
      // Only ink in hand owns temporary orbit. An idle annotation overlay
      // must leave Space to the modeling workspace's selection tool.
      if (event.code === "Space" && !event.repeat && (tool !== null || eraser)) {
        event.preventDefault();
        cancel();
        setTemporaryOrbit(true);
      }
    };
    const onKeyUp = (event: KeyboardEvent) => { if (event.code === "Space") setTemporaryOrbit(false); };
    const onBlur = () => { cancel(); setTemporaryOrbit(false); };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      if (paintFrameRef.current !== null) cancelAnimationFrame(paintFrameRef.current);
      paintFrameRef.current = null;
    };
  }, [cancel, tool, eraser]);

  const local = (event: React.PointerEvent<HTMLCanvasElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
  };

  const eraseTo = (point: Point) => {
    const previous = erasePointRef.current ?? point;
    gestures.forEach((gesture, index) => {
      if (!erasedRef.current.has(index) && annotationIntersectsEraser(gesture, previous, point)) {
        erasedRef.current.add(index);
        savedDirtyRef.current = true;
      }
    });
    erasePointRef.current = point;
    schedulePaint();
  };

  const finish = (points: Point[], canvas: HTMLCanvasElement) => {
    const viewport = viewportRef.current;
    if (!tool || !viewport || points.length === 0) return;
    const camera = viewport.camera();
    if (!camera) return;
    const rect = canvas.getBoundingClientRect();
    const sample = ([x, y]: Point) => viewport.sampleAt(x + rect.left, y + rect.top);
    const first = points[0];
    const last = points[points.length - 1];

    const geometry = gestureScreen(tool, points);
    const samples = gestureSamplePoints(tool, points, geometry);
    if (samples === null) return;

    const hits = new Map<string, SampleHit>();
    for (const point of samples) {
      const hit = sample(point);
      if (hit) {
        const key = hitKey(hit);
        if (!hits.has(key)) hits.set(key, hit);
      }
    }
    const gesture: GestureDto = {
      kind: tool,
      screen: geometry.map(([x, y]) => [Math.round(x), Math.round(y)]),
      camera,
      hits: [...hits.values()].map(toHitDto),
      color: style.color,
      lineWidth: style.lineWidth,
      screenSize: [Math.round(rect.width), Math.round(rect.height)],
      ...(tool === "ruler" && rulerLabel.trim() !== "" ? { label: rulerLabel.trim() } : {}),
    };
    if (tool === "arrow") {
      // The arrow's tip is the one thing it names. Its ray supplies the plane
      // for the start/end direction; an empty tip deliberately has no fallback
      // to a component the stroke merely crossed.
      const through = hits.size > 0 ? [...hits.values()][0].world : null;
      const start = viewport.unprojectOnPlane(first[0] + rect.left, first[1] + rect.top, through);
      const end = viewport.unprojectOnPlane(last[0] + rect.left, last[1] + rect.top, through);
      if (start && end) {
        const delta = subtract(end, start);
        const length = norm(delta);
        gesture.worldStart = start;
        gesture.worldEnd = end;
        gesture.worldDirection =
          length > 0 ? [delta[0] / length, delta[1] / length, delta[2] / length] : null;
        gesture.lengthModelUnits = length;
      }
    }
    onGesture(gesture);
  };

  return (
    <>
      {moved && gestures.length > 0 && (
        <p className="annotate__note quiet">
          {t("stage.annotate.note")}
        </p>
      )}
      {!eraser && tool === "ruler" && (
        <label className="annotate__label">
          {t("stage.tools.ruler.value")}
          <input value={rulerLabel} maxLength={120} onChange={(event) => setRulerLabel(event.currentTarget.value)} placeholder={t("stage.tools.ruler.placeholder")} />
        </label>
      )}
      {!eraser && tool === "arc" && arcBase && <p className="annotate__note quiet">{t("stage.tools.arc.nextPoint")}</p>}
      <canvas ref={savedCanvasRef} className="annotate" aria-hidden="true" style={{ pointerEvents: "none" }} />
      <canvas
        ref={canvasRef}
        className="annotate"
        data-armed={(eraser || tool !== null) && !temporaryOrbit}
        data-eraser={eraser}
        tabIndex={eraser || tool !== null ? 0 : -1}
        style={{ cursor: eraser ? "cell" : undefined }}
        aria-label={
          eraser ? "Erase annotations" : tool
            ? t("stage.annotate.drawingAria", {
                tool: t(GESTURE_TOOLS.find((item) => item.kind === tool)?.labelKey ?? "stage.tools.circle.label"),
              })
            : undefined
        }
        onPointerDown={(event) => {
          if (event.button === 1 || event.button === 2) {
            // OrbitControls owns the neighbouring WebGL canvas. Transfer only
            // the initial press; it captures the real pointer for move/up.
            const viewport = event.currentTarget.parentElement?.querySelector<HTMLCanvasElement>(".viewport-canvas");
            if (viewport) {
              event.preventDefault();
              viewport.dispatchEvent(new PointerEvent("pointerdown", event.nativeEvent));
            }
            return;
          }
          if ((!tool && !eraser) || event.button !== 0 || pointerRef.current !== null) return;
          event.preventDefault();
          event.currentTarget.focus({ preventScroll: true });
          pointerRef.current = event.pointerId;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          if (eraser) {
            eraseTo(local(event));
            return;
          }
          if (tool === "arc" && arcBaseRef.current) {
            const points = [...arcBaseRef.current, local(event)];
            strokeRef.current = points;
            schedulePaint();
            return;
          }
          const points = [local(event)];
          strokeRef.current = points;
          schedulePaint();
        }}
        onPointerMove={(event) => {
          if ((!tool && !eraser) || (pointerRef.current !== event.pointerId && !(!eraser && tool === "arc" && arcBaseRef.current && pointerRef.current === null))) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const coalesced = event.nativeEvent.getCoalescedEvents?.() ?? [];
          const samples = coalesced.length > 0 ? [...coalesced, event.nativeEvent] : [event.nativeEvent];
          for (const sample of samples) {
            const point: Point = [sample.clientX - rect.left, sample.clientY - rect.top];
            if (eraser) { eraseTo(point); continue; }
            const points = strokeRef.current;
            if (tool === "arc" && arcBaseRef.current) {
              strokeRef.current = [...arcBaseRef.current, point];
            } else if (points) {
              const tail = points[points.length - 1];
              if (tail[0] === point[0] && tail[1] === point[1]) continue;
              if (tool === "arrow" || tool === "line" || tool === "ruler" || tool === "arc") points.splice(1, points.length - 1, point);
              else points.push(point);
            }
          }
          schedulePaint();
        }}
        onPointerUp={(event) => {
          if (pointerRef.current !== event.pointerId) return;
          pointerRef.current = null;
          if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
          if (eraser) {
            eraseTo(local(event));
            const indices = [...erasedRef.current].sort((a, b) => a - b);
            erasedRef.current.clear();
            erasePointRef.current = null;
            savedDirtyRef.current = true;
            if (indices.length > 0) onErase?.(indices);
            schedulePaint();
            return;
          }
          const points = strokeRef.current;
          strokeRef.current = null;
          schedulePaint();
          if (!tool || !points) return;
          if (tool === "arc" && arcBaseRef.current === null) {
            const base = arcBaseFromStroke(points[0], local(event));
            if (base === null) return;
            arcBaseRef.current = base;
            setArcBase(base);
            strokeRef.current = [...base];
            return;
          }
          if (tool === "arc") {
            arcBaseRef.current = null;
            setArcBase(null);
          }
          const pointerUp = local(event);
          finish(
            tool === "arc"
              ? [...points.slice(0, 2), pointerUp]
              : completedStroke(tool, points, pointerUp),
            event.currentTarget,
          );
        }}
        onPointerCancel={() => {
          cancel();
        }}
        onLostPointerCapture={(event) => {
          if (pointerRef.current === event.pointerId) cancel();
        }}
      />
    </>
  );
}

export function drawGesture(
  context: CanvasRenderingContext2D,
  kind: GestureTool,
  points: ReadonlyArray<readonly [number, number]>,
  palette: { accent: string; held: string; violated: string },
  live = false,
  style: AnnotationStyle = { color: palette.accent, lineWidth: 2 },
  label: string | null = null,
): void {
  if (points.length === 0) return;
  const colour =
    kind === "keep" ? palette.held : kind === "remove" ? palette.violated : palette.accent;
  context.save();
  context.lineWidth = style.lineWidth;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.strokeStyle = kind === "keep" || kind === "remove" ? colour : style.color;
  context.fillStyle = kind === "keep" || kind === "remove" ? colour : style.color;
  context.globalAlpha *= live ? 0.7 : 1;
  const last = points[points.length - 1];
  if (kind === "freehand" && points.length === 1) {
    context.beginPath();
    context.arc(last[0], last[1], style.lineWidth / 2, 0, Math.PI * 2);
    context.fill();
    context.restore();
    return;
  }
  if (kind === "keep" || kind === "remove") {
    context.beginPath();
    context.arc(last[0], last[1], 9, 0, Math.PI * 2);
    context.stroke();
    context.font = "bold 13px Roboto, system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(kind === "keep" ? "✓" : "✗", last[0], last[1] + 0.5);
    context.restore();
    return;
  }
  if (kind === "arc") {
    if (live && points.length === 2) {
      context.beginPath();
      context.moveTo(points[0][0], points[0][1]);
      context.lineTo(points[1][0], points[1][1]);
      context.stroke();
      context.restore();
      return;
    }
    if (points.length !== 3) { context.restore(); return; }
    const circle = threePointCircle(points[0], points[1], points[2]);
    if (circle === null) { context.restore(); return; }
    context.beginPath();
    context.arc(circle.center[0], circle.center[1], circle.radius, circle.start, circle.end, circle.anticlockwise);
    context.stroke();
    context.restore();
    return;
  }
  if (kind === "ruler") {
    const [start, end] = points;
    if (!end) { context.restore(); return; }
    const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
    const normal: Point = [-Math.sin(angle), Math.cos(angle)];
    context.beginPath();
    context.moveTo(start[0], start[1]); context.lineTo(end[0], end[1]);
    for (const point of [start, end]) {
      context.moveTo(point[0] - normal[0] * 7, point[1] - normal[1] * 7);
      context.lineTo(point[0] + normal[0] * 7, point[1] + normal[1] * 7);
    }
    context.stroke();
    if (label && label.trim() !== "") {
      const middle: Point = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
      context.font = "600 12px system-ui, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "bottom";
      context.fillText(label, middle[0] + normal[0] * 10, middle[1] + normal[1] * 10);
    }
    context.restore();
    return;
  }
  context.beginPath();
  context.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) context.lineTo(points[i][0], points[i][1]);
  if (kind === "circle") {
    context.setLineDash([6, 4]);
    if (!live) context.closePath();
    context.stroke();
    context.setLineDash([]);
    if (!live) {
      context.globalAlpha *= 0.08;
      context.fill();
    }
    context.restore();
    return;
  }
  context.stroke();
  if (kind !== "arrow") { context.restore(); return; }
  // The head follows the one start-to-end segment, never a curved drag tail.
  let back = points.length - 2;
  while (back > 0 && Math.hypot(last[0] - points[back][0], last[1] - points[back][1]) < 12) {
    back -= 1;
  }
  const from = points[Math.max(0, back)];
  const angle = Math.atan2(last[1] - from[1], last[0] - from[0]);
  const size = 11;
  context.beginPath();
  context.moveTo(last[0], last[1]);
  context.lineTo(
    last[0] - size * Math.cos(angle - Math.PI / 6),
    last[1] - size * Math.sin(angle - Math.PI / 6),
  );
  context.lineTo(
    last[0] - size * Math.cos(angle + Math.PI / 6),
    last[1] - size * Math.sin(angle + Math.PI / 6),
  );
  context.closePath();
  context.fill();
  context.restore();
}

/** The exact circle through start, end and third point, including its sweep. */
export function threePointCircle(start: Point, end: Point, through: Point): {
  center: Point; radius: number; start: number; end: number; anticlockwise: boolean;
} | null {
  const [ax, ay] = start; const [bx, by] = end; const [cx, cy] = through;
  const determinant = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(determinant) < 1e-6) return null;
  const a2 = ax * ax + ay * ay; const b2 = bx * bx + by * by; const c2 = cx * cx + cy * cy;
  const center: Point = [
    (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / determinant,
    (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / determinant,
  ];
  const angle = (point: Point) => Math.atan2(point[1] - center[1], point[0] - center[0]);
  const startAngle = angle(start); const endAngle = angle(end); const throughAngle = angle(through);
  const turn = (from: number, to: number) => (to - from + Math.PI * 2) % (Math.PI * 2);
  const throughOnClockwise = turn(startAngle, throughAngle) <= turn(startAngle, endAngle);
  return {
    center, radius: Math.hypot(ax - center[0], ay - center[1]), start: startAngle,
    end: endAngle, anticlockwise: !throughOnClockwise,
  };
}
