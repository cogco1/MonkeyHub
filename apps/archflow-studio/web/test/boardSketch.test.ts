import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer, type ViteDevServer } from "vite";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { SketchFrameData } from "../src/workspaces/monkeyboard/boardSketch.ts";

type Sketch = typeof import("../src/workspaces/monkeyboard/boardSketch.ts");
let sketch: Sketch;
let vite: ViteDevServer;
before(async () => {
  vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  sketch = await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardSketch.ts") as Sketch;
});
after(async () => { await vite?.close(); });

function element(type: ExcalidrawElement["type"], id: string, changes: Record<string, unknown> = {}): ExcalidrawElement {
  return { type, id, x: 0, y: 0, width: 100, height: 100, angle: 0, isDeleted: false,
    strokeColor: "#334455", backgroundColor: "transparent", strokeWidth: 2, strokeStyle: "solid",
    roughness: 0, opacity: 100, roundness: null, frameId: null, boundElements: null,
    ...(["line", "arrow", "freedraw"].includes(type) ? { points: [[0, 0], [100, 100]],
      startArrowhead: null, endArrowhead: type === "arrow" ? "arrow" : null, elbowed: false } : {}),
    ...changes,
  } as unknown as ExcalidrawElement;
}

const frameData = (over: Partial<SketchFrameData> = {}): SketchFrameData =>
  ({ kind: "sketch", version: 1, levelId: "ground", storeyHeight: 3, metresPerUnit: 0.01, calibration: null, ...over });
const frame = (over: Record<string, unknown> = {}) =>
  element("frame", "frame-1", { x: 100, y: 100, width: 800, height: 600, customData: { sketch: frameData() }, ...over });
const inFrame = (type: ExcalidrawElement["type"], id: string, changes: Record<string, unknown> = {}) =>
  element(type, id, { frameId: "frame-1", ...changes });

test("a rectangle becomes a counter-clockwise footprint in metres with y flipped", () => {
  // 200 x 100 units at (100, 600) inside a frame whose bottom edge is y=700, scale 1 unit = 0.01 m
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const { sketches } = sketch.sketchActionsFromFrame([frame(), rect], frame());
  assert.equal(sketches.length, 1);
  assert.equal(sketches[0].elementId, "board-r1");
  assert.equal(sketches[0].baseLevel, "ground");
  assert.equal(sketches[0].height, 3);
  assert.equal(sketches[0].closed, true);
  assert.deepEqual(sketches[0].profile.map((p) => p.map((v) => Math.round(v * 1000) / 1000)),
    [[0, 0], [2, 0], [2, 1], [0, 1]]);   // bottom-left of the frame is the origin; up is +Z
});

test("a rotated rectangle keeps its rotated corners", () => {
  const rect = inFrame("rectangle", "r2", { x: 400, y: 400, width: 100, height: 100, angle: Math.PI / 2 });
  const [only] = sketch.sketchActionsFromFrame([frame(), rect], frame()).sketches;
  const xs = only.profile.map((p) => p[0]), zs = only.profile.map((p) => p[1]);
  assert.ok(Math.abs(Math.max(...xs) - Math.min(...xs) - 1) < 1e-9);
  assert.ok(Math.abs(Math.max(...zs) - Math.min(...zs) - 1) < 1e-9);
});

test("an ellipse is sampled with 32 points", () => {
  const el = inFrame("ellipse", "e1", { x: 200, y: 200, width: 300, height: 100 });
  const [only] = sketch.sketchActionsFromFrame([frame(), el], frame()).sketches;
  assert.equal(only.profile.length, 32);
});

test("a closed freehand loop is simplified; an open one is skipped", () => {
  const loop = Array.from({ length: 200 }, (_, i) => { const a = i / 200 * 2 * Math.PI; return [50 + 50 * Math.cos(a), 50 + 50 * Math.sin(a)]; });
  const closed = inFrame("freedraw", "f1", { x: 300, y: 300, width: 100, height: 100, points: [...loop, loop[0]] });
  const open = inFrame("freedraw", "f2", { x: 500, y: 300, width: 100, height: 100, points: loop.slice(0, 120) });
  const result = sketch.sketchActionsFromFrame([frame(), closed, open], frame());
  assert.equal(result.sketches.length, 1);
  assert.ok(result.sketches[0].profile.length >= 3 && result.sketches[0].profile.length < 200);
  assert.deepEqual(result.skipped, [{ elementId: "f2", reason: "open" }]);
});

test("arrows, text and elements outside the frame are never footprints", () => {
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const arrow = inFrame("arrow", "a1", { points: [[0, 0], [100, 0]] });
  const text = inFrame("text", "t1", {});
  const outside = element("rectangle", "r9", { x: 2000, y: 2000, frameId: null });
  const result = sketch.sketchActionsFromFrame([frame(), rect, arrow, text, outside], frame());
  assert.deepEqual(result.sketches.map((row) => row.elementId), ["board-r1"]);
  assert.deepEqual(result.skipped.map((s) => s.elementId).sort(), ["a1", "t1"]);
  assert.deepEqual([...new Set(result.skipped.map((s) => s.reason))], ["unsupported"]);
});

test("a clockwise outline is reversed to counter-clockwise", () => {
  const cw = inFrame("line", "l1", { x: 100, y: 100, width: 100, height: 100, points: [[0, 0], [0, 100], [100, 100], [100, 0], [0, 0]] });
  const ccw = inFrame("line", "l2", { x: 400, y: 100, width: 100, height: 100, points: [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]] });
  const area = (profile: readonly (readonly [number, number])[]) =>
    profile.reduce((sum, [x, z], i) => { const [nx, nz] = profile[(i + 1) % profile.length]; return sum + x * nz - nx * z; }, 0);
  // The two outlines are drawn in opposite directions; both come back positive.
  for (const only of sketch.sketchActionsFromFrame([frame(), cw, ccw], frame()).sketches) {
    assert.ok(area(only.profile) > 0, `${only.elementId} is not counter-clockwise`);
  }
});

test("sending without a calibration is refused", () => {
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const uncalibrated = frame({ customData: { sketch: frameData({ metresPerUnit: null }) } });
  assert.throws(() => sketch.sketchActionsFromFrame([uncalibrated, rect], uncalibrated),
    (e: Error) => (e as { code?: string }).code === "BOARD_SKETCH_SCALE_REQUIRED");
});

test("an empty frame is refused rather than sent", () => {
  assert.throws(() => sketch.sketchActionsFromFrame([frame()], frame()),
    (e: Error) => (e as { code?: string }).code === "BOARD_SKETCH_EMPTY");
});

test("calibration derives metres per unit from a two-point line", () => {
  const line = inFrame("line", "dim", { x: 0, y: 0, points: [[0, 0], [300, 400]] });
  const data = sketch.calibrateSketchFrame(frameData({ metresPerUnit: null }), line, 10);
  assert.equal(data.metresPerUnit, 10 / 500);
  assert.deepEqual(data.calibration, { elementId: "dim", metres: 10 });
  assert.throws(() => sketch.calibrateSketchFrame(frameData(), line, 0));
  assert.throws(() => sketch.calibrateSketchFrame(frameData(), inFrame("line", "poly", { points: [[0, 0], [1, 1], [2, 0]] }), 1));
});

test("the calibration line itself is never a footprint", () => {
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const line = inFrame("line", "dim", { x: 100, y: 600, points: [[0, 0], [200, 0]] });
  const calibrated = frame({ customData: { sketch: frameData({ calibration: { elementId: "dim", metres: 2 } }) } });
  const result = sketch.sketchActionsFromFrame([calibrated, rect, line], calibrated);
  assert.deepEqual(result.sketches.map((row) => row.elementId), ["board-r1"]);
  assert.deepEqual(result.skipped, []);
});

test("footprint ids are stable and identifier-safe", () => {
  assert.equal(sketch.footprintElementId(element("rectangle", "AbC_12-x")), sketch.footprintElementId(element("rectangle", "AbC_12-x")));
  assert.match(sketch.footprintElementId(element("rectangle", "we!rd id")), /^board-[a-z0-9-]+$/);
  // archflow/project/refs.py: ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$
  assert.match(sketch.footprintElementId(element("rectangle", "x".repeat(400))), /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/);
});

test("the frame data round-trips and defaults a missing storey height", () => {
  assert.equal(sketch.sketchFrameData(element("rectangle", "r1", { customData: { sketch: frameData() } })), null);
  assert.equal(sketch.sketchFrameData(frame({ customData: {} })), null);
  assert.deepEqual(sketch.sketchFrameData(frame({ customData: { sketch: { ...frameData(), storeyHeight: -1 } } })), frameData());
  assert.deepEqual(sketch.newSketchFrameData("level-2"),
    { kind: "sketch", version: 1, levelId: "level-2", storeyHeight: 3, metresPerUnit: null, calibration: null });
});

test("a sketch frame and its shapes are not marks a board-wide clear may delete", () => {
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const otherFrame = element("frame", "frame-2", { x: 2000, y: 100, width: 400, height: 400 });
  const note = element("text", "t1", { frameId: "frame-2" });
  const loose = element("freedraw", "d1", {});
  const ids = sketch.sketchFrameIds([frame(), rect, otherFrame, note, loose]);
  assert.deepEqual([...ids], ["frame-1"]);
  assert.equal(sketch.insideSketchFrame(frame(), ids), true);
  assert.equal(sketch.insideSketchFrame(rect, ids), true);
  assert.equal(sketch.insideSketchFrame(note, ids), false);   // another frame's mark stays a mark
  assert.equal(sketch.insideSketchFrame(loose, ids), false);
  assert.equal(sketch.insideSketchFrame(rect, new Set<string>()), false);
});

test("leaving the board for the conversation drops the board view and keeps the rest", () => {
  assert.equal(sketch.conversationUrl("http://host/app?view=board&candidate=run-9&embedded=tool"),
    "http://host/app?candidate=run-9&embedded=tool");
  assert.equal(sketch.conversationUrl("http://host/app?view=documents&documentRun=r&documentSource=a&documentPage=1&documentRevision=x"),
    "http://host/app");
  assert.equal(sketch.conversationUrl("http://host/app"), "http://host/app");
});

test("the summary names frame, count, level, scale and board revision", () => {
  const rect = inFrame("rectangle", "r1", { x: 100, y: 600, width: 200, height: 100 });
  const conversion = sketch.sketchActionsFromFrame([frame(), rect], frame());
  const summary = sketch.sketchSummary("Sketch 1", conversion, frameData(), "a".repeat(64));
  assert.ok(summary.includes("Sketch 1") && summary.includes("1 ") && summary.includes("ground") && summary.includes("aaaaaaaaaaaa"));
  assert.ok(summary.length <= 240);
});
