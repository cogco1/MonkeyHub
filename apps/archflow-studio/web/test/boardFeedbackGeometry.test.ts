import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer, type ViteDevServer } from "vite";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { SourceDocumentDto } from "../src/api/generated/index.ts";

type Geometry = typeof import("../src/workspaces/monkeyboard/boardFeedbackGeometry.ts");
let geometry: Geometry;
let vite: ViteDevServer;
before(async () => {
  vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  geometry = await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardFeedbackGeometry.ts") as Geometry;
});
after(async () => { await vite?.close(); });

function document(overrides: Partial<SourceDocumentDto> = {}): SourceDocumentDto {
  return { projectId: "project", runId: "drawing-run", assetSha256: "a".repeat(64), fileName: "Elevation.pdf",
    mimeType: "application/pdf", sizeBytes: 1200, pageCount: 2,
    pages: [{ pageIndex: 0, width: 1200, height: 800, rotation: 0 },
      { pageIndex: 1, width: 800, height: 1200, rotation: 90 }],
    revisionRef: "project://project/runs/drawing-run/records/revision-a.json", ...overrides };
}
const source = (pageIndex = 0) => ({ runId: "drawing-run", assetSha256: "a".repeat(64),
  revisionRef: document().revisionRef, pageIndex });

function element(type: ExcalidrawElement["type"], id: string, changes: Record<string, unknown> = {}): ExcalidrawElement {
  return { type, id, x: 0, y: 0, width: 100, height: 100, angle: 0, isDeleted: false,
    strokeColor: "#334455", backgroundColor: "transparent", strokeWidth: 2, strokeStyle: "solid",
    roughness: 0, opacity: 100, roundness: null, frameId: null, boundElements: null,
    ...(type === "image" ? { width: 1000, height: 500, fileId: "preview", scale: [1, 1], crop: null,
      status: "saved", customData: { sourceDocument: source() } } : {}),
    ...(["line", "arrow", "freedraw"].includes(type) ? { points: [[0, 0], [100, 100]],
      startArrowhead: null, endArrowhead: type === "arrow" ? "arrow" : null, elbowed: false } : {}),
    ...changes,
  } as unknown as ExcalidrawElement;
}
const selected = (elements: readonly ExcalidrawElement[]) => Object.fromEntries(elements.map((item) => [item.id, true]));
function convert(elements: ExcalidrawElement[], documents = [document()]) {
  return geometry.createBoardFeedback(elements, selected(elements), documents);
}
function fails(code: string, action: () => unknown) {
  assert.throws(action, (error: unknown) => error instanceof geometry.BoardFeedbackGeometryError && error.code === code);
}
function near(actual: readonly number[], expected: readonly number[], tolerance = 1e-10) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < tolerance, `${value} != ${expected[index]}`));
}

test("a translated and resized source maps marks to page coordinates without mutating the scene", () => {
  const image = element("image", "sheet", { x: 100, y: 50, width: 400, height: 200 });
  const line = element("line", "line", { x: 180, y: 70, width: 240, height: 140, points: [[0, 0], [240, 140]] });
  const elements = [image, line];
  const before = structuredClone(elements);
  const result = convert(elements);
  assert.deepEqual(result.annotations, [{ id: "board:sheet:line", kind: "line", points: [[0.2, 0.1], [0.8, 0.8]],
    color: "#334455", lineWidth: 0.01 }]);
  assert.deepEqual(result.annotationGroups, ["board:sheet:line"]);
  assert.deepEqual(result.source, source());
  assert.equal(result.image, image);
  assert.deepEqual(elements, before);
});

test("all image flips reverse the correct page axes", () => {
  const line = element("line", "line", { x: 180, y: 70, width: 240, height: 140, points: [[0, 0], [240, 140]] });
  for (const [scale, points] of [
    [[1, 1], [[0.2, 0.1], [0.8, 0.8]]], [[-1, 1], [[0.8, 0.1], [0.2, 0.8]]],
    [[1, -1], [[0.2, 0.9], [0.8, 0.2]]], [[-1, -1], [[0.8, 0.9], [0.2, 0.2]]],
  ]) {
    const image = element("image", "sheet", { x: 100, y: 50, width: 400, height: 200, scale });
    assert.deepEqual(convert([image, line]).annotations[0].points, points);
  }
});

test("image rotation and flip are undone while native PDF rotation is not applied twice", () => {
  const image = element("image", "sheet", { x: 100, y: 200, width: 400, height: 200,
    angle: Math.PI / 2, scale: [-1, 1], customData: { sourceDocument: source(1) } });
  const line = element("line", "line", { x: 400, y: 100, width: 200, height: 400, points: [[0, 0], [-200, 400]] });
  const result = convert([image, line]);
  assert.equal(result.page.rotation, 90);
  assert.equal(result.source.pageIndex, 1);
  assert.deepEqual(result.annotations[0].points, [[1, 0], [0, 1]]);
});

test("rotated rectangles keep their contour rather than their world bounding box", () => {
  const image = element("image", "sheet", { width: 400, height: 400 });
  const rectangle = element("rectangle", "box", { x: 100, y: 100, width: 100, height: 50, angle: Math.PI / 2 });
  assert.deepEqual(convert([image, rectangle]).annotations[0].points,
    [[0.4375, 0.1875], [0.4375, 0.4375], [0.3125, 0.4375], [0.3125, 0.1875], [0.4375, 0.1875]]);
  const rounded = element("rectangle", "round", { x: 100, y: 100, width: 200, height: 100, roundness: { type: 3 } });
  const points = convert([image, rounded]).annotations[0].points;
  assert.equal(points.length, 69);
  assert.deepEqual(points[0], [0.3125, 0.25]);
  assert.deepEqual(points.at(-1), points[0]);
  assert.ok(!points.some(([x, y]) => x === 0.25 && y === 0.25), "a rounded corner cannot become the sharp bounding-box corner");
});

test("rotated ellipses retain their full affine outline and test extrema between sampled points", () => {
  const image = element("image", "sheet", { width: 400, height: 400 });
  const ellipse = element("ellipse", "circle", { x: 100, y: 150, width: 200, height: 100, angle: Math.PI / 4 });
  const mark = convert([image, ellipse]).annotations[0];
  assert.equal(mark.kind, "circle");
  assert.equal(mark.points.length, 128);
  near(mark.points[0], [(200 + 100 / Math.sqrt(2)) / 400, (200 + 100 / Math.sqrt(2)) / 400]);
  near(mark.points[32], [(200 - 50 / Math.sqrt(2)) / 400, (200 + 50 / Math.sqrt(2)) / 400]);
  // The exact ellipse extends slightly beyond x=0 although a 128-point
  // polygon approximation would miss that extremum.
  const centerX = Math.sqrt(6250) - 0.00001;
  const outside = element("ellipse", "outside", { x: centerX - 100, y: 150, width: 200, height: 100, angle: Math.PI / 4 });
  fails("BOARD_FEEDBACK_OUTSIDE_PAGE", () => convert([image, outside]));
});

test("negative local stroke points rotate around their actual point bounds", () => {
  const image = element("image", "sheet", { width: 500, height: 500 });
  const points = [[0, 0], [-100, 0], [-100, 50]];
  for (const type of ["freedraw", "line"] as const) {
    const stroke = element(type, type, { x: 200, y: 200, width: 100, height: 50, angle: Math.PI / 2, points });
    assert.deepEqual(convert([image, stroke]).annotations[0].points, [[0.35, 0.55], [0.35, 0.35], [0.25, 0.35]]);
  }
});

test("polyline arrows keep every bend and the direction of both arrowheads", () => {
  const image = element("image", "sheet");
  const arrow = element("arrow", "arrow", { x: 100, y: 100, width: 200, height: 100,
    points: [[0, 0], [100, 100], [200, 100]], startArrowhead: "arrow" });
  const result = convert([image, arrow]);
  assert.deepEqual(result.annotationGroups, ["board:sheet:arrow"]);
  assert.deepEqual(result.annotations.map(({ id, kind, points }) => ({ id, kind, points })), [
    { id: "board:sheet:arrow:shaft", kind: "freehand", points: [[0.1, 0.2], [0.2, 0.4], [0.3, 0.4]] },
    { id: "board:sheet:arrow:start", kind: "arrow", points: [[0.2, 0.4], [0.1, 0.2]] },
    { id: "board:sheet:arrow:end", kind: "arrow", points: [[0.2, 0.4], [0.3, 0.4]] },
  ]);
  const straight = element("arrow", "straight", { x: 100, y: 100, roundness: { type: 2 } });
  assert.equal(convert([image, straight]).annotations[1].kind, "arrow", "the default two-point rounded arrow remains usable");
});

test("selecting one frame includes its unique source and children without importing unrelated marks", () => {
  const frame = element("frame", "frame");
  const image = element("image", "sheet", { frameId: "frame" });
  const circle = element("ellipse", "circle", { x: 100, y: 100, frameId: "frame" });
  const unrelated = element("rectangle", "unrelated", { x: 5000, y: 5000 });
  const result = geometry.createBoardFeedback([frame, image, circle, unrelated], { frame: true }, [document()]);
  assert.equal(result.image.id, "sheet");
  assert.deepEqual(result.annotationGroups, ["board:sheet:circle"]);
  const second = element("image", "second", { frameId: "frame" });
  fails("BOARD_FEEDBACK_SOURCE_AMBIGUOUS", () => geometry.createBoardFeedback([frame, image, second], { frame: true }, [document()]));
});

test("no image, multiple images and a mismatched registration never infer a target", () => {
  const image = element("image", "sheet");
  const line = element("line", "line");
  fails("BOARD_FEEDBACK_SOURCE_REQUIRED", () => convert([line]));
  fails("BOARD_FEEDBACK_SOURCE_AMBIGUOUS", () => convert([image, element("image", "duplicate")]));
  fails("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", () => convert([element("image", "unbound", { customData: {} }), line]));
  const anotherRevision = document({ revisionRef: "project://project/runs/drawing-run/records/revision-b.json" });
  fails("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", () => convert([image, line], [anotherRevision]));
  fails("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", () => convert([image, line], [document({ pages: [] })]));
  fails("BOARD_FEEDBACK_SOURCE_UNAVAILABLE", () => geometry.createBoardFeedback([image], { missing: true }, [document()]));
  assert.deepEqual(convert([image]).annotations, [], "a whole-page written request does not require invented marks");
});

test("outside marks are rejected rather than clamped into a different annotation", () => {
  const image = element("image", "sheet");
  for (const mark of [
    element("line", "crossing", { x: -10, y: 10, points: [[0, 0], [100, 100]] }),
    element("freedraw", "outside", { x: 1100, y: 100 }),
    element("ellipse", "partly-outside", { x: 950, y: 50, width: 100, height: 100 }),
    element("rectangle", "partly-outside", { x: 10, y: 450, width: 100, height: 100 }),
  ]) fails("BOARD_FEEDBACK_OUTSIDE_PAGE", () => convert([image, mark]));
  const edge = element("line", "edge", { x: 0, y: 0, width: 1000, height: 0, points: [[0, 0], [1000, 0]] });
  assert.deepEqual(convert([image, edge]).annotations[0].points, [[0, 0], [1, 0]]);
});

test("cropped images, unsupported selected content and unsupported line styles have explicit failures", () => {
  const image = element("image", "sheet");
  fails("BOARD_FEEDBACK_UNSUPPORTED", () => convert([element("image", "crop", {
    crop: { x: 20, y: 0, width: 900, height: 500, naturalWidth: 1000, naturalHeight: 500 },
  })]));
  for (const mark of [
    element("diamond", "diamond"),
    element("arrow", "curved", { points: [[0, 0], [50, 100], [100, 0]], roundness: { type: 2 } }),
    element("arrow", "elbow", { elbowed: true }), element("arrow", "symbol", { endArrowhead: "dot" }),
    element("line", "rough", { roughness: 1 }), element("line", "dashed", { strokeStyle: "dashed" }),
    element("ellipse", "filled", { backgroundColor: "#ffffff" }),
  ]) fails("BOARD_FEEDBACK_UNSUPPORTED", () => convert([image, mark]));
  fails("BOARD_FEEDBACK_TEXT_UNSUPPORTED", () => convert([image, element("text", "text", { text: "Move this wall" })]));
});

test("a selected container cannot silently lose its bound text", () => {
  const image = element("image", "sheet");
  const box = element("rectangle", "box", { x: 100, y: 100, boundElements: [{ id: "label", type: "text" }] });
  const label = element("text", "label", { x: 110, y: 110, text: "Move", containerId: "box" });
  fails("BOARD_FEEDBACK_TEXT_UNSUPPORTED", () => geometry.createBoardFeedback([image, box, label], { sheet: true, box: true }, [document()]));
});

test("annotation groups are stable and escape image/element id separators", () => {
  const image = element("image", "sheet:one");
  const line = element("line", "note:one", { x: 100, y: 100, strokeColor: "#abc" });
  const first = convert([image, line]);
  const second = convert([image, { ...line, x: 200 }]);
  assert.deepEqual(first.annotationGroups, ["board:sheet%3Aone:note%3Aone"]);
  assert.equal(first.annotations[0].id, second.annotations[0].id);
  assert.equal(first.annotations[0].color, "#aabbcc");
  assert.notDeepEqual(first.annotations[0].points, second.annotations[0].points);
  const other = convert([element("image", "sheet"), element("line", "one:note:one")]);
  assert.notEqual(first.annotationGroups[0], other.annotationGroups[0]);
});

test("malformed geometry fails before producing invalid page DTOs", () => {
  const image = element("image", "sheet");
  for (const malformed of [
    element("image", "zero", { width: 0 }), element("image", "nan", { angle: NaN }),
    element("image", "scale", { scale: [0.5, 1] }),
  ]) fails("BOARD_FEEDBACK_INVALID_GEOMETRY", () => convert([malformed]));
  for (const malformed of [
    element("freedraw", "bad-point", { points: [[0, Infinity]] }),
    element("ellipse", "degenerate", { width: 0 }), element("line", "empty", { points: [] }),
  ]) fails("BOARD_FEEDBACK_INVALID_GEOMETRY", () => convert([image, malformed]));
  fails("BOARD_FEEDBACK_UNSUPPORTED", () => convert([image, element("line", "bad-width", { strokeWidth: 0 })]));
  fails("BOARD_FEEDBACK_UNSUPPORTED", () => convert([image, element("line", "x".repeat(128))]));
});
