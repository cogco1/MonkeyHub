import type { DocumentGestureDto } from "../../api/generated";

export type PagePoint = [number, number];
export type PageView = { x: number; y: number; scale: number };

/** The page dimensions already include PDF CropBox/rotation or image orientation. */
export function toPagePoint(
  clientX: number, clientY: number, viewportRect: { left: number; top: number },
  view: PageView, pageWidth: number, pageHeight: number,
): PagePoint {
  const clamp = (value: number) => Math.max(0, Math.min(1, value));
  return [
    clamp((clientX - viewportRect.left - view.x) / (view.scale * pageWidth)),
    clamp((clientY - viewportRect.top - view.y) / (view.scale * pageHeight)),
  ];
}

export function zoomPageAt(view: PageView, anchor: PagePoint, nextScale: number): PageView {
  const ratio = nextScale / view.scale;
  return {
    x: anchor[0] - (anchor[0] - view.x) * ratio,
    y: anchor[1] - (anchor[1] - view.y) * ratio,
    scale: nextScale,
  };
}

type Contour = { points: PagePoint[]; closed?: boolean };
const TAU = Math.PI * 2;
const turn = (from: number, to: number) => ((to - from) % TAU + TAU) % TAU;
const scaled = (point: PagePoint, width: number, height: number): PagePoint => [point[0] * width, point[1] * height];

function ellipse(center: PagePoint, rx: number, ry: number): Contour {
  return {
    closed: true,
    points: Array.from({ length: 128 }, (_, index): PagePoint => {
      const angle = index * TAU / 128;
      return [center[0] + rx * Math.cos(angle), center[1] + ry * Math.sin(angle)];
    }),
  };
}

/** A bounded polyline on the circular sweep start -> through -> end. */
function arc(points: PagePoint[]): Contour[] {
  if (points.length !== 3) return points.length === 2 ? [{ points }] : [];
  const [start, end, through] = points;
  const bx = end[0] - start[0], by = end[1] - start[1];
  const cx = through[0] - start[0], cy = through[1] - start[1];
  const cross = bx * cy - by * cx;
  const b2 = bx * bx + by * by, c2 = cx * cx + cy * cy;
  if (Math.abs(cross) <= Number.EPSILON * Math.max(b2, c2)) return [];
  const center: PagePoint = [
    start[0] + (b2 * cy - c2 * by) / (2 * cross),
    start[1] + (bx * c2 - cx * b2) / (2 * cross),
  ];
  const radius = Math.hypot(start[0] - center[0], start[1] - center[1]);
  const angle = (point: PagePoint) => Math.atan2(point[1] - center[1], point[0] - center[0]);
  const a = angle(start), b = angle(end), c = angle(through);
  const positive = turn(a, c) <= turn(a, b);
  const sweep = (from: number, to: number) => positive ? turn(from, to) : -turn(to, from);
  const sampled: PagePoint[] = [start];
  for (const [from, to, endpoint] of [[a, c, through], [c, b, end]] as const) {
    const delta = sweep(from, to);
    const count = Math.max(1, Math.ceil(Math.abs(delta) / TAU * 256));
    for (let index = 1; index < count; index += 1) {
      const theta = from + delta * index / count;
      sampled.push([center[0] + radius * Math.cos(theta), center[1] + radius * Math.sin(theta)]);
    }
    sampled.push(endpoint);
  }
  return [{ points: sampled }];
}

/** SVG drawing and erasing share these same visible contours. */
function contours(gesture: DocumentGestureDto, width: number, height: number): Contour[] {
  if (gesture.kind === "text") return [];
  const points = gesture.points.map((point) => scaled(point, width, height));
  if (points.length === 0) return [];
  const start = points[0], end = points[points.length - 1];
  const shortSide = Math.min(width, height);
  const lineWidth = gesture.lineWidth * shortSide;
  if (gesture.kind === "arc") return arc(points);
  if (gesture.kind === "circle") {
    if (points.length > 2) return [{ points, closed: true }];
    return [ellipse([(start[0] + end[0]) / 2, (start[1] + end[1]) / 2],
      Math.abs(end[0] - start[0]) / 2, Math.abs(end[1] - start[1]) / 2)];
  }
  if (gesture.kind === "keep" || gesture.kind === "remove") {
    const radius = Math.max(shortSide * 0.015, lineWidth * 3);
    const point = (x: number, y: number): PagePoint => [end[0] + x * radius, end[1] + y * radius];
    const mark = gesture.kind === "keep"
      ? [{ points: [point(-0.5, 0), point(-0.1, 0.4), point(0.55, -0.45)] }]
      : [{ points: [point(-0.4, -0.4), point(0.4, 0.4)] }, { points: [point(-0.4, 0.4), point(0.4, -0.4)] }];
    return [ellipse(end, radius, radius), ...mark];
  }
  if (gesture.kind === "freehand") return [{ points }];
  if (gesture.kind === "polyline") return [{ points, closed: gesture.closed ?? false }];
  const shaft: Contour = { points: [start, end] };
  if (gesture.kind === "line") return [shaft];
  const length = Math.hypot(end[0] - start[0], end[1] - start[1]);
  if (length === 0) return [shaft];
  const ux = (end[0] - start[0]) / length, uy = (end[1] - start[1]) / length;
  if (gesture.kind === "ruler") {
    const tick = Math.max(shortSide * 0.01, lineWidth * 2);
    return [shaft, ...[start, end].map(([x, y]): Contour => ({ points: [[x - uy * tick, y + ux * tick], [x + uy * tick, y - ux * tick]] }))];
  }
  const head = Math.min(length * 0.45, Math.max(shortSide * 0.02, lineWidth * 4));
  return [shaft, { points: [
    [end[0] - ux * head - uy * head * 0.5, end[1] - uy * head + ux * head * 0.5],
    end,
    [end[0] - ux * head + uy * head * 0.5, end[1] - uy * head - ux * head * 0.5],
  ] }];
}

export function inkPath(gesture: DocumentGestureDto, width: number, height: number): string {
  return contours(gesture, width, height).map(({ points, closed }) => {
    const first = points[0];
    const rest = points.length === 1 ? points : points.slice(1);
    return `M ${first[0]} ${first[1]} ${rest.map(([x, y]) => `L ${x} ${y}`).join(" ")}${closed ? " Z" : ""}`;
  }).join(" ");
}

function pointSegmentDistance(point: PagePoint, start: PagePoint, end: PagePoint): number {
  const dx = end[0] - start[0], dy = end[1] - start[1];
  const length2 = dx * dx + dy * dy;
  const t = length2 === 0 ? 0 : Math.max(0, Math.min(1, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length2));
  return Math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy);
}

function segmentDistance(a: PagePoint, b: PagePoint, c: PagePoint, d: PagePoint): number {
  const rx = b[0] - a[0], ry = b[1] - a[1], sx = d[0] - c[0], sy = d[1] - c[1];
  const determinant = rx * sy - ry * sx;
  if (determinant !== 0) {
    const dx = c[0] - a[0], dy = c[1] - a[1];
    const t = (dx * sy - dy * sx) / determinant;
    const u = (dx * ry - dy * rx) / determinant;
    if (t >= 0 && t <= 1 && u >= 0 && u <= 1) return 0;
  }
  return Math.min(pointSegmentDistance(a, c, d), pointSegmentDistance(b, c, d), pointSegmentDistance(c, a, b), pointSegmentDistance(d, a, b));
}

/** Width/height are displayed CSS dimensions; radiusPx is the eraser's CSS radius. */
export function eraseAt(
  annotations: readonly DocumentGestureDto[], from: PagePoint, to: PagePoint,
  width: number, height: number, radiusPx: number,
): DocumentGestureDto[] {
  const a = scaled(from, width, height), b = scaled(to, width, height);
  return annotations.filter((gesture) => {
    const reach = radiusPx + gesture.lineWidth * Math.min(width, height) / 2;
    return !contours(gesture, width, height).some(({ points, closed }) => {
      if (points.length === 1) return pointSegmentDistance(points[0], a, b) <= reach;
      const count = points.length - (closed ? 0 : 1);
      for (let index = 0; index < count; index += 1) {
        if (segmentDistance(a, b, points[index], points[(index + 1) % points.length]) <= reach) return true;
      }
      return false;
    });
  });
}
