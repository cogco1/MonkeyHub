import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  return {
    ...await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardNavigation.ts") as typeof import("../src/workspaces/monkeyboard/boardNavigation.ts"),
    ...await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardScene.ts") as typeof import("../src/workspaces/monkeyboard/boardScene.ts"),
  };
}

const source = (overrides: Record<string, unknown> = {}) => ({
  runId: "drawing-run", assetSha256: "a".repeat(64),
  revisionRef: "project://p/runs/drawing-run/records/rev-1.json", pageIndex: 1, ...overrides,
});

const page = (overrides: Record<string, unknown> = {}) => ({
  id: "page-1", type: "image", x: 100, y: 50, width: 200, height: 120, angle: 0,
  customData: { sourceDocument: source() }, ...overrides,
});

test("a double click names the exact page under it, not a neighbouring one", async (t) => {
  const { pageSourceAt } = await harness(t);
  const second = page({ id: "page-2", x: 400, customData: { sourceDocument: source({ pageIndex: 2 }) } });
  const elements = [page(), second];
  assert.deepEqual(pageSourceAt(elements, { x: 150, y: 100 }), source());
  assert.deepEqual(pageSourceAt(elements, { x: 450, y: 100 }), source({ pageIndex: 2 }));
  // Corners belong to the page; the gap between two pages belongs to neither.
  assert.deepEqual(pageSourceAt(elements, { x: 100, y: 50 }), source());
  assert.equal(pageSourceAt(elements, { x: 350, y: 100 }), null);
  assert.equal(pageSourceAt(elements, { x: 150, y: 400 }), null);
});

test("marks, frames, deleted pages and unbound images never name a page", async (t) => {
  const { pageSourceAt } = await harness(t);
  const over = { id: "mark", type: "freedraw", x: 100, y: 50, width: 200, height: 120, angle: 0 };
  const frame = { id: "frame", type: "frame", x: 90, y: 40, width: 220, height: 140, angle: 0 };
  const unbound = { id: "loose", type: "image", x: 100, y: 50, width: 200, height: 120, angle: 0, customData: null };
  assert.deepEqual(pageSourceAt([page(), over, frame], { x: 150, y: 100 }), source());
  assert.equal(pageSourceAt([over, frame], { x: 150, y: 100 }), null);
  assert.equal(pageSourceAt([unbound], { x: 150, y: 100 }), null);
  assert.equal(pageSourceAt([page({ isDeleted: true })], { x: 150, y: 100 }), null);
});

test("the topmost page wins where two pages overlap", async (t) => {
  const { pageSourceAt } = await harness(t);
  const under = page({ id: "under" });
  const over = page({ id: "over", customData: { sourceDocument: source({ pageIndex: 7 }) } });
  assert.deepEqual(pageSourceAt([under, over], { x: 150, y: 100 }), source({ pageIndex: 7 }));
  assert.deepEqual(pageSourceAt([over, under], { x: 150, y: 100 }), source());
});

test("a rotated page keeps its own rectangle", async (t) => {
  const { pageSourceAt } = await harness(t);
  // A quarter turn about the centre (200, 110) swaps the page's extents.
  const rotated = [page({ angle: Math.PI / 2 })];
  assert.deepEqual(pageSourceAt(rotated, { x: 200, y: 30 }), source());
  assert.equal(pageSourceAt(rotated, { x: 110, y: 110 }), null);
  assert.deepEqual(pageSourceAt([page()], { x: 110, y: 110 }), source());
});

test("the place on the board survives the visit, minus what the board no longer has", async (t) => {
  const { captureBoardView, boardViewAppState } = await harness(t);
  const view = captureBoardView({
    scrollX: -420.5, scrollY: 88, zoom: { value: 2.75 },
    selectedElementIds: { "page-1": true, gone: true, unselected: false },
    selectedGroupIds: { "group-a": true, "group-gone": true },
  });
  assert.deepEqual(view, { scrollX: -420.5, scrollY: 88, zoom: 2.75,
    selectedElementIds: { "page-1": true, gone: true }, selectedGroupIds: { "group-a": true, "group-gone": true } });
  const restored = boardViewAppState(view, [page({ groupIds: ["group-a"] })]);
  assert.deepEqual(restored, { scrollX: -420.5, scrollY: 88, zoom: { value: 2.75 },
    selectedElementIds: { "page-1": true }, selectedGroupIds: { "group-a": true } });
  // A page removed or replaced while its editor was open is not reselected.
  assert.deepEqual(boardViewAppState(view, [page({ isDeleted: true })]),
    { scrollX: -420.5, scrollY: 88, zoom: { value: 2.75 }, selectedElementIds: {}, selectedGroupIds: {} });
});
