import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";

test("straight annotations persist and sample only their two endpoints", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { completedStroke, gestureSamplePoints, gestureScreen } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const curvedDrag = [[10, 10], [20, 80], [90, 30]] as const;
  assert.deepEqual(gestureScreen("arrow", curvedDrag), [[10, 10], [90, 30]]);
  assert.deepEqual(gestureScreen("line", curvedDrag), [[10, 10], [90, 30]]);
  assert.deepEqual(gestureScreen("ruler", curvedDrag), [[10, 10], [90, 30]]);
  assert.deepEqual(gestureScreen("freehand", curvedDrag), curvedDrag);
  // Dragging across A/B and releasing over C creates a C-only arrow target.
  const released = completedStroke("arrow", [[10, 10], [30, 10]], [90, 30]);
  assert.deepEqual(released, [[10, 10], [90, 30]]);
  assert.deepEqual(gestureSamplePoints("arrow", released, gestureScreen("arrow", released)), [[90, 30]]);
  // A tip over empty space does not fall back to the path it crossed.
  const emptyTip = completedStroke("arrow", [[10, 10], [80, 20]], [120, 20]);
  assert.deepEqual(gestureSamplePoints("arrow", emptyTip, gestureScreen("arrow", emptyTip)), [[120, 20]]);
  const circle = gestureSamplePoints("circle", [[10, 10], [40, 10], [40, 40], [10, 40]], [[10, 10], [40, 10], [40, 40], [10, 40]]);
  assert.ok(circle && circle.length > 1, "circle remains a region sample");
});

test("an arc retains three defining points and refuses a collinear third point", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { arcBaseFromStroke, arcScreenPoints, gestureScreen, isValidArc } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const arc = [[10, 10], [80, 10], [45, 40]] as const;
  assert.deepEqual(gestureScreen("arc", arc), arc);
  assert.equal(isValidArc(arc), true);
  assert.equal(isValidArc([[10, 10], [80, 10], [45, 10]]), false);
  assert.equal(isValidArc([[10, 10], [80, 10]]), false);
  // A slow drag may have an early 2 px move, but pointer-up establishes its real end.
  assert.deepEqual(arcBaseFromStroke([10, 10], [80, 10]), [[10, 10], [80, 10]]);
  assert.equal(arcBaseFromStroke([10, 10], [14, 10]), null);
  const sampled = arcScreenPoints(arc, 8);
  assert.ok(Math.hypot(sampled[0][0] - arc[0][0], sampled[0][1] - arc[0][1]) < 1e-6);
  assert.ok(sampled.some(([x, y]) => x === 45 && y === 40), "the third point belongs to the sampled sweep");
  const semicircle = arcScreenPoints([[0, 0], [100, 0], [50, 50]], 8);
  assert.ok(semicircle.length > 3);
  for (const [x, y] of semicircle) {
    assert.ok(Math.abs(Math.hypot(x - 50, y) - 50) < 1e-6, "every hit sample lies on the rendered circle, not a chord");
    assert.ok(y >= -1e-6, "the sweep passes through the third point's half-circle");
  }
  const largeArc = arcScreenPoints([[0, 0], [100, 0], [2000, 0.02]], 8);
  assert.ok(largeArc.length <= 400, "a near-collinear arc cannot allocate an unbounded sample array");
  assert.ok(largeArc.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y)));
});

test("an arc previews its endpoints before the third point and retains view-change fading", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { drawGesture } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const segments: unknown[] = [];
  const arcs: unknown[] = [];
  const strokeAlpha: number[] = [];
  const savedAlpha: number[] = [];
  const context = {
    globalAlpha: 0.3,
    save() { savedAlpha.push(this.globalAlpha); },
    restore() { this.globalAlpha = savedAlpha.pop()!; },
    beginPath() {},
    moveTo(x: number, y: number) { segments.push(["move", x, y]); },
    lineTo(x: number, y: number) { segments.push(["line", x, y]); },
    stroke() { strokeAlpha.push(this.globalAlpha); },
    arc(...args: unknown[]) { arcs.push(args); },
  };
  const palette = { accent: "#2f80ed", held: "#58b368", violated: "#e5534b" };
  drawGesture(context as never, "arc", [[0, 0], [100, 0]], palette, true);
  assert.deepEqual(segments, [["move", 0, 0], ["line", 100, 0]]);
  assert.equal(arcs.length, 0);
  assert.ok(Math.abs(strokeAlpha[0] - 0.21) < 1e-6);
  assert.equal(context.globalAlpha, 0.3);
  drawGesture(context as never, "arc", [[0, 0], [100, 0], [50, 50]], palette);
  assert.equal(arcs.length, 1);
  assert.equal(strokeAlpha[1], 0.3);
  assert.equal(context.globalAlpha, 0.3);
});

test("a ruler label is rendered beside its annotation and cannot alter older geometry", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { drawGesture } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const calls: string[] = [];
  const context = new Proxy({
    save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, arc() {}, closePath() {}, fill() {}, setLineDash() {},
    fillText(text: string) { calls.push(text); },
  }, { get(target, key) { return key in target ? target[key as keyof typeof target] : () => {}; }, set() { return true; } });
  drawGesture(context as never, "ruler", [[10, 10], [80, 10]], { accent: "#2f80ed", held: "#58b368", violated: "#e5534b" }, false, { color: "#e5534b", lineWidth: 2 }, "clearance 1200");
  assert.deepEqual(calls, ["clearance 1200"]);
});

test("a single point and short freehand stroke remain visible and retain model samples", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false, logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { drawGesture, completedStroke, gestureSamplePoints } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const point = [[10, 10]];
  assert.deepEqual(completedStroke("freehand", point, [10, 10]), point);
  assert.deepEqual(gestureSamplePoints("freehand", point, point), point);
  assert.deepEqual(gestureSamplePoints("freehand", [[10, 10], [11, 11]], [[10, 10], [11, 11]]), [[10, 10], [11, 11]]);
  const dots: unknown[] = [];
  let fills = 0;
  const context = { globalAlpha: 1, save() {}, restore() {}, beginPath() {}, arc(...args: unknown[]) { dots.push(args); }, fill() { fills++; } };
  drawGesture(context as never, "freehand", point, { accent: "blue", held: "green", violated: "red" }, false, { color: "blue", lineWidth: 4 });
  assert.deepEqual(dots, [[10, 10, 2, 0, Math.PI * 2]]);
  assert.equal(fills, 1);
});

test("whole-stroke erasing intersects actual ink across fast cursor moves", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false, logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { annotationIntersectsEraser } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const line = { kind: "freehand", screen: [[0, 20], [100, 20]], lineWidth: 2 };
  assert.equal(annotationIntersectsEraser(line, [50, 0], [50, 50], 1), true);
  assert.equal(annotationIntersectsEraser(line, [50, 30], [50, 50], 1), false);
  assert.equal(annotationIntersectsEraser({ ...line, screen: [[20, 20]] }, [0, 20], [40, 20], 1), true);
  const arc = { kind: "arc", screen: [[0, 0], [100, 0], [50, 50]], lineWidth: 2 };
  assert.equal(annotationIntersectsEraser(arc, [50, 50], [50, 50], 1), true);
  assert.equal(annotationIntersectsEraser(arc, [75, 25], [75, 25], 1), false, "the third-point sample must not add an eraser chord");
  assert.equal(annotationIntersectsEraser({ ...line, kind: "remove", screen: [[20, 20]] }, [30, 20], [30, 20], 1), true, "remove remains a visible semantic mark, erasable like other ink");
});


test("Tracing Paper framing includes orthographic zoom when the live gesture carries it", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false, logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { tracingPaperViewMatches } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/Annotate.tsx");
  const camera = { position: [1, 2, 3], target: [0, 0, 0], up: [0, 0, 1], fov: 50, projection: "orthographic", zoom: 2 };
  const gesture = { id: "g", kind: "circle", screen: [[0, 0]], hits: [], screenSize: [100, 100], camera: { ...camera } };
  assert.equal(tracingPaperViewMatches(camera, [gesture]), true);
  assert.equal(tracingPaperViewMatches({ ...camera, zoom: 3 }, [gesture]), false);
  assert.equal(tracingPaperViewMatches({ ...camera, projection: "perspective" }, [gesture]), false);
});
