import type { ExcalidrawElement, ExcalidrawImageElement } from "@excalidraw/excalidraw/element/types";
import type { DocumentGestureDto, DocumentPageDto, SourceDocumentDto } from "../../api/generated";
import { findSource, imageSource, type PageSource } from "./boardScene";

type Point = [number, number];
type ErrorCode = "BOARD_FEEDBACK_SOURCE_REQUIRED" | "BOARD_FEEDBACK_SOURCE_AMBIGUOUS"
  | "BOARD_FEEDBACK_SOURCE_UNAVAILABLE" | "BOARD_FEEDBACK_UNSUPPORTED"
  | "BOARD_FEEDBACK_OUTSIDE_PAGE" | "BOARD_FEEDBACK_INVALID_GEOMETRY";

export class BoardFeedbackGeometryError extends Error {
  constructor(readonly code: ErrorCode, message: string) {
    super(message);
    this.name = "BoardFeedbackGeometryError";
  }
}

export interface BoardFeedbackSelection {
  image: ExcalidrawImageElement;
  document: SourceDocumentDto;
  page: DocumentPageDto;
  source: PageSource;
  annotations: DocumentGestureDto[];
  annotationGroups: string[];
}

function fail(code: ErrorCode, message: string): never {
  throw new BoardFeedbackGeometryError(code, message);
}

function rotate([x, y]: Point, [cx, cy]: Point, angle: number): Point {
  const cos = Math.cos(angle), sin = Math.sin(angle);
  return [cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos];
}

/** Float roundoff at a rotated page edge is removed; actual outside points are refused. */
function inside([x, y]: Point): Point {
  const point: Point = [Math.round(x * 1e12) / 1e12, Math.round(y * 1e12) / 1e12];
  if (!point.every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
    fail("BOARD_FEEDBACK_OUTSIDE_PAGE", "A selected mark extends outside the source page. Move it fully onto the page before sending feedback.");
  }
  return [point[0] === 0 ? 0 : point[0], point[1] === 0 ? 0 : point[1]];
}

function checkedGeometry(element: ExcalidrawElement): void {
  if (![element.x, element.y, element.width, element.height, element.angle].every(Number.isFinite)
    || element.width < 0 || element.height < 0) {
    fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "A selected element has invalid position, size or rotation.");
  }
}

function strokeStyle(element: ExcalidrawElement, image: ExcalidrawImageElement) {
  if (element.strokeStyle !== "solid" || element.opacity !== 100
    || (element.backgroundColor !== "transparent" && element.backgroundColor !== "")) {
    fail("BOARD_FEEDBACK_UNSUPPORTED", "Feedback currently supports opaque outline marks with solid strokes. Remove the fill, transparency or dashed styling first.");
  }
  // The receiving page represents ink as centerlines with a uniform width.
  const lineWidth = element.strokeWidth / Math.min(image.width, image.height);
  const color = /^#[0-9a-f]{3}$/i.test(element.strokeColor)
    ? `#${element.strokeColor.slice(1).split("").map((digit) => digit + digit).join("")}` : element.strokeColor;
  if (!/^#[0-9a-f]{6}$/i.test(color) || !Number.isFinite(lineWidth) || lineWidth <= 0 || lineWidth > 1) {
    fail("BOARD_FEEDBACK_UNSUPPORTED", "A selected mark needs a visible hex-color stroke narrower than the page.");
  }
  return { color, lineWidth };
}

/** Match Excalidraw 0.18's rounded-rectangle quadratic centerline, not its bounding box. */
function rectanglePoints(element: ExcalidrawElement): Point[] {
  const w = element.width, h = element.height;
  if (element.roundness === null) return [[0, 0], [w, 0], [w, h], [0, h], [0, 0]];
  const type = element.roundness.type;
  if (![1, 2, 3].includes(type)) fail("BOARD_FEEDBACK_UNSUPPORTED", "This rectangle corner style is not supported for feedback.");
  const radius = type === 3 ? Math.min(Math.min(w, h) * 0.25, element.roundness.value ?? 32) : Math.min(w, h) * 0.25;
  if (!Number.isFinite(radius) || radius < 0) fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "The selected rectangle has an invalid corner radius.");
  const points: Point[] = [[radius, 0], [w - radius, 0]];
  const corner = (control: Point, end: Point) => {
    const start = points[points.length - 1];
    for (let i = 1; i <= 16; i += 1) {
      const t = i / 16, s = 1 - t;
      points.push([s * s * start[0] + 2 * s * t * control[0] + t * t * end[0],
        s * s * start[1] + 2 * s * t * control[1] + t * t * end[1]]);
    }
  };
  corner([w, 0], [w, radius]); points.push([w, h - radius]);
  corner([w, h], [w - radius, h]); points.push([radius, h]);
  corner([0, h], [0, h - radius]); points.push([0, radius]);
  corner([0, 0], [radius, 0]);
  return points;
}

/**
 * Transfer an explicit selection only. Frame membership and bound labels are
 * expanded; visual proximity never chooses a drawing or silently adds marks.
 * Geometry is page-local after undoing the Board image transform. Page metadata
 * already includes PDF rotation/CropBox and image EXIF orientation.
 */
export function createBoardFeedback(
  elements: readonly ExcalidrawElement[],
  selectedElementIds: Readonly<Record<string, boolean>>,
  documents: readonly SourceDocumentDto[],
): BoardFeedbackSelection {
  const visible = elements.filter((element) => !element.isDeleted);
  const byId = new Map(visible.map((element) => [element.id, element]));
  const selected = new Set<string>();
  const include = (element: ExcalidrawElement) => {
    if (selected.has(element.id)) return;
    selected.add(element.id);
    if (element.type === "frame") visible.filter((item) => item.frameId === element.id).forEach(include);
    for (const bound of element.boundElements ?? []) {
      const text = bound.type === "text" ? byId.get(bound.id) : undefined;
      if (text) include(text);
    }
  };
  for (const [id, active] of Object.entries(selectedElementIds)) {
    if (!active) continue;
    const element = byId.get(id);
    if (!element) fail("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", "The selection changed. Select the source page and marks again.");
    include(element);
  }
  const chosen = visible.filter((element) => selected.has(element.id));
  const images = chosen.filter((element): element is ExcalidrawImageElement => element.type === "image");
  if (images.length === 0) fail("BOARD_FEEDBACK_SOURCE_REQUIRED", "Select one registered source image, or its frame, together with the marks to send.");
  if (images.length !== 1) fail("BOARD_FEEDBACK_SOURCE_AMBIGUOUS", "Select only one source image. Feedback cannot guess which drawing or revision to change.");
  const image = images[0];
  const source = imageSource(image);
  const document = source && findSource(documents, source);
  const page = document?.pages.find((item) => item.pageIndex === source?.pageIndex);
  if (!source || !document || !page) fail("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", "The selected image no longer resolves to its registered source page and drawing revision.");
  checkedGeometry(image);
  if (image.crop != null) fail("BOARD_FEEDBACK_UNSUPPORTED", "Cropped Board images are not supported for feedback yet. Restore the full source image first.");
  if (image.width <= 0 || image.height <= 0 || !Array.isArray(image.scale)
    || image.scale.length !== 2 || !image.scale.every((value) => value === 1 || value === -1)
    || ![page.width, page.height].every((value) => Number.isFinite(value) && value > 0)) {
    fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "The source page has invalid dimensions or image scale.");
  }
  const center: Point = [image.x + image.width / 2, image.y + image.height / 2];
  const pagePoint = (world: Point): Point => {
    const [x, y] = rotate(world, center, -image.angle);
    return [0.5 + (x - center[0]) / image.width * image.scale[0],
      0.5 + (y - center[1]) / image.height * image.scale[1]];
  };
  const annotations: DocumentGestureDto[] = [];
  const annotationGroups: string[] = [];
  for (const element of chosen) {
    if (element.id === image.id || element.type === "frame") continue;
    if (!["ellipse", "rectangle", "line", "arrow", "freedraw"].includes(element.type)) {
      fail("BOARD_FEEDBACK_UNSUPPORTED", element.type === "text"
        ? "Selected text cannot be transferred without changing its layout. Put that text in the feedback message, then unbind or remove it from the shape, or deselect the shape containing the bound text."
        : `Selected ${element.type} elements are not supported for feedback.`);
    }
    checkedGeometry(element);
    const style = strokeStyle(element, image);
    const id = `board:${encodeURIComponent(image.id)}:${encodeURIComponent(element.id)}`;
    annotationGroups.push(id);
    const append = (kind: DocumentGestureDto["kind"], points: Point[], suffix = "") => {
      if (id.length + suffix.length > 128 || points.length === 0 || points.length > 20000) {
        fail("BOARD_FEEDBACK_UNSUPPORTED", "A selected mark exceeds the page annotation size limit.");
      }
      annotations.push({ id: id + suffix, kind, points: points.map(inside), ...style });
    };
    if (element.type === "ellipse" || element.type === "rectangle") {
      if (element.width === 0 || element.height === 0) fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "A selected outline has zero width or height.");
      const elementCenter: Point = [element.x + element.width / 2, element.y + element.height / 2];
      const convert = ([x, y]: Point) => pagePoint(rotate([x + element.x, y + element.y], elementCenter, element.angle));
      if (element.type === "ellipse") {
        const mid = convert([element.width / 2, element.height / 2]);
        const right = convert([element.width, element.height / 2]);
        const down = convert([element.width / 2, element.height]);
        const rx = Math.hypot(right[0] - mid[0], down[0] - mid[0]);
        const ry = Math.hypot(right[1] - mid[1], down[1] - mid[1]);
        inside([mid[0] - rx, mid[1] - ry]); inside([mid[0] + rx, mid[1] + ry]);
        append("circle", Array.from({ length: 128 }, (_, i) => {
          const angle = i * Math.PI / 64;
          return convert([element.width / 2 * (1 + Math.cos(angle)), element.height / 2 * (1 + Math.sin(angle))]);
        }));
      } else {
        // Require the entire rectangle bounds to fit, including rounded corners.
        [[0, 0], [element.width, 0], [element.width, element.height], [0, element.height]]
          .forEach((point) => inside(convert(point as Point)));
        append("freehand", rectanglePoints(element).map(convert));
      }
      continue;
    }
    if (element.type !== "line" && element.type !== "arrow" && element.type !== "freedraw") continue;
    if (element.points.length > 20000) fail("BOARD_FEEDBACK_UNSUPPORTED", "A selected stroke exceeds the page annotation point limit.");
    if (element.points.length === 0 || element.points.some((point) => point.length !== 2 || !point.every(Number.isFinite))) {
      fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "A selected stroke has invalid points.");
    }
    // Excalidraw rotates strokes about their local point bounds, which need not
    // start at (0,0) and must not be confused with x + width/2 for negative points.
    const xs = element.points.map((point) => point[0]), ys = element.points.map((point) => point[1]);
    const elementCenter: Point = [element.x + (Math.min(...xs) + Math.max(...xs)) / 2,
      element.y + (Math.min(...ys) + Math.max(...ys)) / 2];
    const points = element.points.map(([x, y]) => pagePoint(rotate([x + element.x, y + element.y], elementCenter, element.angle)));
    if (element.type === "freedraw") { append("freehand", points); continue; }
    if (element.points.length < 2) fail("BOARD_FEEDBACK_INVALID_GEOMETRY", "A selected line needs at least two points.");
    if ((element.points.length > 2 && element.roundness !== null) || ("elbowed" in element && element.elbowed)
      || element.roughness !== 0) {
      fail("BOARD_FEEDBACK_UNSUPPORTED", "Use a sharp, straight-segment line or arrow with roughness set to zero for feedback. Curved, elbowed and sketch-style arrows are not supported yet.");
    }
    if ([element.startArrowhead, element.endArrowhead].some((head) => head !== null && head !== "arrow")) {
      fail("BOARD_FEEDBACK_UNSUPPORTED", "Use the plain arrow head for feedback; this endpoint symbol cannot be transferred faithfully.");
    }
    const hasHead = element.startArrowhead !== null || element.endArrowhead !== null;
    if (!hasHead) append(points.length === 2 ? "line" : "freehand", points);
    else {
      append(points.length === 2 ? "line" : "freehand", points, ":shaft");
      if (element.startArrowhead) append("arrow", [points[1], points[0]], ":start");
      if (element.endArrowhead) append("arrow", points.slice(-2), ":end");
    }
  }
  if (annotations.length > 2000) fail("BOARD_FEEDBACK_UNSUPPORTED", "Select fewer marks; one page supports at most 2000 annotations.");
  return { image, document, page, source, annotations, annotationGroups };
}
