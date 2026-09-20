import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import type { DocumentGestureDto } from "../src/api/generated/types.gen.ts";

const gesture = (kind: DocumentGestureDto["kind"], points: DocumentGestureDto["points"], id: string = kind, lineWidth = 0.002): DocumentGestureDto => ({
  id, kind, points, color: "#2f80ed", lineWidth,
});

test("closed editable outlines render and erase their closing edge; open paths do not", async t => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { eraseAt, inkPath } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentInk.ts");
  const closed = { ...gesture("polyline", [[0.1, 0.1], [0.8, 0.1], [0.8, 0.8]]), closed: true };
  const open = { ...closed, closed: false };
  assert.match(inkPath(closed, 800, 400), / Z$/);
  assert.doesNotMatch(inkPath(open, 800, 400), / Z$/);
  assert.deepEqual(eraseAt([closed], [0.45, 0.45], [0.45, 0.45], 800, 400, 2), []);
  assert.deepEqual(eraseAt([open], [0.45, 0.45], [0.45, 0.45], 800, 400, 2), [open]);
});

test("page ink uses visible rectangular page coordinates and pointer-centred zoom", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { toPagePoint, zoomPageAt } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentInk.ts");
  const rect = { left: 80, top: 120 };
  const view = { x: -40, y: 30, scale: 0.5 };
  // A rotated, cropped 500 x 600 page remains rectangular in normalized space.
  assert.deepEqual(toPagePoint(165, 330, rect, view, 500, 600), [0.5, 0.6]);
  const zoomed = zoomPageAt(view, [85, 210], 1.75);
  assert.deepEqual(toPagePoint(165, 330, rect, zoomed, 500, 600), [0.5, 0.6]);
  assert.deepEqual(zoomPageAt(zoomed, [85, 210], 0.5), view);
  assert.deepEqual(toPagePoint(-100, 2000, rect, view, 500, 600), [0, 1]);
});

test("eraser sweeps remove whole strokes without changing missed strokes", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { eraseAt } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentInk.ts");
  const hit = gesture("freehand", [[0.1, 0.5], [0.5, 0.5], [0.9, 0.5]], "hit");
  const missed = gesture("line", [[0.1, 0.1], [0.9, 0.1]], "missed");
  const annotations = [hit, missed];
  // Both eraser endpoints miss; its swept segment crosses the stroke.
  const remaining = eraseAt(annotations, [0.5, 0.3], [0.5, 0.7], 1000, 500, 2);
  assert.deepEqual(remaining, [missed]);
  assert.equal(remaining[0], missed);
  assert.deepEqual(annotations, [hit, missed]);
  assert.deepEqual(eraseAt([hit], [0.1, 0.9], [0.9, 0.9], 1000, 500, 3), [hit]);
  const dot = gesture("freehand", [[0.5, 0.5]]);
  assert.deepEqual(eraseAt([dot], [0.5, 0.5], [0.5, 0.5], 1000, 500, 1), []);
});

test("circle and arrow hits follow their visible outlines, with short-side line widths", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { inkPath, eraseAt } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentInk.ts");
  const circle = gesture("circle", [[0.2, 0.2], [0.8, 0.8]]);
  assert.match(inkPath(circle, 1000, 500), /^M 800 250 L /);
  assert.ok(inkPath(circle, 1000, 500).endsWith(" Z"));
  assert.deepEqual(eraseAt([circle], [0.5, 0.5], [0.5, 0.5], 1000, 500, 2), [circle]);
  assert.deepEqual(eraseAt([circle], [0.8, 0.5], [0.8, 0.5], 1000, 500, 2), []);
  const arrow = gesture("arrow", [[0.2, 0.5], [0.8, 0.5]]);
  assert.equal(inkPath(arrow, 1000, 500), "M 200 250 L 800 250 M 790 255 L 800 250 L 790 245");
  assert.deepEqual(eraseAt([arrow], [0.79, 0.51], [0.79, 0.51], 1000, 500, 0), []);
  const thick = gesture("line", [[0.2, 0.5], [0.8, 0.5]], "thick", 0.02);
  // 0.02 of the shorter 500 px side is a 10 px stroke, not 20 px.
  assert.deepEqual(eraseAt([thick], [0.5, 0.511], [0.5, 0.511], 1000, 500, 0), [thick]);
  assert.deepEqual(eraseAt([thick], [0.5, 0.509], [0.5, 0.509], 1000, 500, 0), []);
  assert.deepEqual(eraseAt([thick], [0.5, 0.509], [0.5, 0.509], 2000, 1000, 0), []);
});

test("retained closed circles, three-point arcs, ruler ticks and semantic marks remain erasable", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { inkPath, eraseAt } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentInk.ts");
  const polygon = gesture("circle", [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8]]);
  assert.equal(inkPath(polygon, 100, 100), "M 20 20 L 80 20 L 80 80 Z");
  assert.deepEqual(eraseAt([polygon], [0.5, 0.5], [0.5, 0.5], 100, 100, 0), []);
  const arc = gesture("arc", [[0.2, 0.5], [0.8, 0.5], [0.5, 0.8]]);
  assert.deepEqual(eraseAt([arc], [0.5, 0.8], [0.5, 0.8], 100, 100, 0), []);
  assert.deepEqual(eraseAt([arc], [0.5, 0.5], [0.5, 0.5], 100, 100, 1), [arc]);
  assert.equal(inkPath(gesture("arc", [[0.2, 0.5], [0.8, 0.5], [0.5, 0.5]]), 100, 100), "");
  const ruler = gesture("ruler", [[0.2, 0.5], [0.8, 0.5]]);
  assert.deepEqual(eraseAt([ruler], [0.2, 0.509], [0.2, 0.509], 100, 100, 0), []);
  for (const kind of ["keep", "remove"] as const) {
    const mark = gesture(kind, [[0.5, 0.5]]);
    assert.ok(inkPath(mark, 1000, 500).includes(" Z M "));
    assert.deepEqual(eraseAt([mark], [0.5075, 0.5], [0.5075, 0.5], 1000, 500, 0), []);
  }
});
