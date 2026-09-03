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

import type { GestureDto, GestureHitDto } from "../../api/generated";
import type { SampleHit, Vec3, ViewportController } from "../../viewer/ThreeDmViewport";

export type GestureTool = GestureDto["kind"];

export const GESTURE_TOOLS: ReadonlyArray<{
  kind: GestureTool;
  glyph: string;
  title: string;
}> = [
  { kind: "circle", glyph: "◯", title: "circle an area: this is what I mean" },
  { kind: "arrow", glyph: "↗", title: "draw an arrow: which way, and how far" },
  { kind: "keep", glyph: "✓", title: "mark what must not change" },
  { kind: "remove", glyph: "✗", title: "mark what should go" },
];

/** A stroke is sampled every this many pixels along its length. */
const SAMPLE_PX = 8;
/** A circle's inside is sampled on this grid, so it names what it encloses. */
const INTERIOR_PX = 14;
/** No gesture reads the model more often than this, whatever its size. */
const MAX_SAMPLES = 400;
/** A pointer that moved less than this drew a mark, not a stroke. */
const MARK_SLOP_PX = 6;

type Point = [number, number];

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

export function Annotate({
  viewportRef,
  tool,
  gestures,
  onGesture,
}: {
  viewportRef: RefObject<ViewportController | null>;
  /** The armed tool; null lets the pointer through to the orbit. */
  tool: GestureTool | null;
  /** The marks already made, redrawn in the pixels they were drawn in. */
  gestures: readonly GestureDto[];
  onGesture(gesture: GestureDto): void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [stroke, setStroke] = useState<Point[] | null>(null);
  const strokeRef = useRef<Point[] | null>(null);
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
    const same = (a: readonly number[], b: readonly number[]) =>
      a.every((value, index) => Math.abs(value - b[index]) < 1e-6);
    const check = () => {
      const camera = viewportRef.current?.camera();
      if (!camera) return;
      setMoved(
        !same(camera.position, drawnIn.position) ||
          !same(camera.target, drawnIn.target) ||
          Math.abs(camera.fov - drawnIn.fov) > 1e-6,
      );
    };
    check();
    const timer = window.setInterval(check, 300);
    return () => window.clearInterval(timer);
  }, [gestures, viewportRef]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
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
    const palette = colours();
    context.globalAlpha = moved ? 0.3 : 1;
    for (const gesture of gestures) drawGesture(context, gesture.kind, gesture.screen, palette);
    context.globalAlpha = 1;
    const live = strokeRef.current;
    if (tool && live && live.length > 0) drawGesture(context, tool, live, palette, true);
  }, [gestures, moved, tool]);

  useEffect(() => {
    draw();
  }, [draw, stroke]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [draw]);

  const local = (event: React.PointerEvent<HTMLCanvasElement>): Point => {
    const rect = event.currentTarget.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top];
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
    const travelled = Math.hypot(last[0] - first[0], last[1] - first[1]);

    let samples: Point[];
    if (tool === "keep" || tool === "remove") {
      samples = [last];
    } else if (tool === "circle") {
      if (travelled < MARK_SLOP_PX && points.length < 8) return;
      samples = everyNth([...resample(points, SAMPLE_PX), ...interior(points, INTERIOR_PX)], MAX_SAMPLES);
    } else {
      if (travelled < MARK_SLOP_PX) return;
      samples = everyNth(resample(points, SAMPLE_PX), MAX_SAMPLES);
    }

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
      screen: points.map(([x, y]) => [Math.round(x), Math.round(y)]),
      camera,
      hits: [...hits.values()].map(toHitDto),
    };
    if (tool === "arrow") {
      // The stroke carried onto the plane facing the camera through the first
      // thing it touched (or the orbit target): that is what "this far" is
      // in model units, and which way in the world it went.
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
          marks are held in the view they were drawn in · what they touched still goes with
          the sentence
        </p>
      )}
    <canvas
      ref={canvasRef}
      className="annotate"
      data-armed={tool !== null}
      aria-label={tool ? `drawing: ${tool}` : undefined}
      onPointerDown={(event) => {
        if (!tool) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const points = [local(event)];
        strokeRef.current = points;
        setStroke(points);
      }}
      onPointerMove={(event) => {
        const points = strokeRef.current;
        if (!tool || !points) return;
        const point = local(event);
        const tail = points[points.length - 1];
        if (Math.hypot(point[0] - tail[0], point[1] - tail[1]) < 2) return;
        const next = [...points, point];
        strokeRef.current = next;
        setStroke(next);
      }}
      onPointerUp={(event) => {
        const points = strokeRef.current;
        strokeRef.current = null;
        setStroke(null);
        if (!tool || !points) return;
        event.currentTarget.releasePointerCapture(event.pointerId);
        finish(points, event.currentTarget);
      }}
      onPointerCancel={() => {
        strokeRef.current = null;
        setStroke(null);
      }}
    />
    </>
  );
}

function drawGesture(
  context: CanvasRenderingContext2D,
  kind: GestureTool,
  points: ReadonlyArray<readonly [number, number]>,
  palette: { accent: string; held: string; violated: string },
  live = false,
): void {
  if (points.length === 0) return;
  const colour =
    kind === "keep" ? palette.held : kind === "remove" ? palette.violated : palette.accent;
  context.save();
  context.lineWidth = 2;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.strokeStyle = colour;
  context.fillStyle = colour;
  context.globalAlpha = live ? 0.7 : 1;
  const last = points[points.length - 1];
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
  context.beginPath();
  context.moveTo(points[0][0], points[0][1]);
  for (let i = 1; i < points.length; i += 1) context.lineTo(points[i][0], points[i][1]);
  if (kind === "circle") {
    context.setLineDash([6, 4]);
    if (!live) context.closePath();
    context.stroke();
    context.setLineDash([]);
    if (!live) {
      context.globalAlpha = 0.08;
      context.fill();
    }
    context.restore();
    return;
  }
  context.stroke();
  // The head sits on the last point, along the last stretch of the stroke.
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
