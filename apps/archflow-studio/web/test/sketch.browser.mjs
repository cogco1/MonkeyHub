import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "studio-sketch-axes-"));
const http = createHttpServer();
const errors = [];
let browser, vite, page;
const html = `<!doctype html><html><head><style>
#root, .stage { height: 100vh; }
#root .viewport-state { display: none; }
</style></head><body><div id="root"></div><script type="module">
import React, { createRef } from "react";
import { createRoot } from "react-dom/client";
import { Box3, PerspectiveCamera, Scene, Vector3 } from "three";
import { Stage } from "/src/features/stage/Stage.tsx";
import { UserPreferencesProvider } from "/src/features/settings/preferences.tsx";
import "/src/styles.css";
const add = Scene.prototype.add;
Scene.prototype.add = function (...objects) {
  for (const object of objects) {
    if (object.name !== "archflow-sketch-preview") continue;
    const bounds = new Box3().setFromObject(object);
    window.previewBounds = { min: bounds.min.toArray(), max: bounds.max.toArray() };
  }
  return add.apply(this, objects);
};
const viewportRef = window.viewport = createRef();
window.submitted = [];
const noop = () => {};
createRoot(document.getElementById("root")).render(
  React.createElement(UserPreferencesProvider, null, React.createElement(Stage, {
    viewportRef, embedded: true, status: "ready", message: "", picked: null,
    versions: [], workingCopies: [], loadedShas: [], loadingSha: null, designHistory: null,
    documentView: { open: false, mounted: false }, displayMode: "model", tool: null,
    gestures: [], home: null, hasModel: true, editingBaseRunId: null, loadedRunId: null,
    modelAnnotations: null, annotationsReady: false, documentModelSources: [],
    editingModelSource: null, viewedModelSource: null, artifactError: null, baseError: null,
    blend: null, captureState: "idle", onTool: noop, onInspection: noop, onStatus: noop,
    onRequestFile: noop, onOpenFile: noop, onSource: noop, onPick: noop,
    onSketch: async (action) => { window.submitted.push(action); },
  })),
);
window.projectPoint = (point) => {
  const state = viewportRef.current.camera();
  const bounds = document.querySelector("canvas").getBoundingClientRect();
  const camera = new PerspectiveCamera(state.fov, bounds.width / bounds.height, 0.01, 10000);
  camera.position.fromArray(state.position);
  camera.up.fromArray(state.up);
  camera.lookAt(new Vector3(...state.target));
  camera.updateMatrixWorld();
  const projected = new Vector3(...point).project(camera);
  return [bounds.left + (projected.x + 1) * bounds.width / 2,
    bounds.top + (1 - projected.y) * bounds.height / 2];
};
</script></body></html>`;

try {
  vite = await createServer({
    root: webRoot, configFile: false, cacheDir, publicDir: ".generated/public", logLevel: "silent",
    plugins: [react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
  });
  http.on("request", async (request, response) => {
    if (request.url === "/sketch-test") {
      response.setHeader("Content-Type", "text/html");
      response.end(await vite.transformIndexHtml(request.url, html));
    } else if (request.url.startsWith("/api/")) {
      errors.push(`Unexpected API call: ${request.url}`);
      response.writeHead(500).end();
    } else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
    "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: "en-US" });
  page.setDefaultTimeout(20000);
  page.on("pageerror", (error) => { errors.push(error.message); console.error(error.message); });
  page.on("requestfailed", (request) => console.error(request.url(), request.failure()?.errorText));
  await page.goto(`${origin}/sketch-test`);
  await page.waitForFunction(() => window.viewport?.current?.camera() &&
    document.querySelector("canvas")?.getBoundingClientRect().height > 500);
  await page.evaluate(() => {
    const preview = window.viewport.current.sketchPreview;
    window.viewport.current.sketchPreview = (spec) => {
      window.previewSpec = spec;
      return preview(spec);
    };
  });
  const planePoint = await page.evaluate(() => {
    const screen = window.projectPoint([2, 3, 1.2]);
    return window.viewport.current.pointOnWorkPlane(...screen, 1.2);
  });
  [2, 3, 1.2].forEach((expected, index) => assert.ok(Math.abs(planePoint[index] - expected) < 1e-6));
  await page.evaluate(() => window.viewport.current.sketchPreview({
    profile: [[1, 2], [4, 2], [4, 6], [1, 6]], base: 1.2, height: 2.4,
  }));
  const bounds = await page.evaluate(() => window.previewBounds);
  [1, 2, 1.2].forEach((expected, index) => assert.ok(Math.abs(bounds.min[index] - expected) < 1e-6));
  [4, 6, 3.6].forEach((expected, index) => assert.ok(Math.abs(bounds.max[index] - expected) < 1e-6));
  await page.evaluate(() => window.viewport.current.sketchPreview(null));
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  const anchor = await page.evaluate(() => window.projectPoint([0, 0, 0]));
  const corner = await page.evaluate(() => window.projectPoint([3, 2, 0]));
  await page.evaluate(() => { window.previewBounds = null; });
  await page.mouse.click(...anchor);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "profile");
  await page.mouse.move(...corner);
  await page.waitForFunction(() => window.previewBounds !== null);
  await page.mouse.click(...corner);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "height");
  // The pointer has not moved, so the height must still read nothing: the
  // plane the height is taken on stands through the corner just clicked, and
  // the same ray meets it exactly there. A jump here is the bug where a 2.4 m
  // pull came out at 2.16.
  await page.mouse.move(...corner);
  await page.waitForFunction(() => window.previewSpec?.height !== undefined);
  const settled = await page.evaluate(() => window.previewSpec.height);
  assert.ok(Math.abs(settled) < 0.02, `the height jumped to ${settled} before the pointer moved`);
  // Pulled up from that same corner, 2.4 m above it reads 2.4 m.
  const top = await page.evaluate(() => window.projectPoint([3, 2, 2.4]));
  await page.mouse.move(...top);
  await page.waitForFunction(() => window.previewBounds?.max[2] > 0.1);
  if (process.env.SKETCH_SCREENSHOT) await page.screenshot({ path: process.env.SKETCH_SCREENSHOT });
  await page.mouse.click(...top);
  await page.waitForFunction(() => window.submitted.length === 1);
  const [action] = await page.evaluate(() => window.submitted);
  const expectedProfile = [[0, 0], [3, 0], [3, 2], [0, 2]];
  action.profile.forEach((point, index) => point.forEach((value, axis) =>
    assert.ok(Math.abs(value - expectedProfile[index][axis]) < 0.02)));
  assert.ok(Math.abs(action.height - 2.4) < 0.02,
    `the pulled height must be the height the pointer is at; got ${action.height}`);
  const previewTop = await page.evaluate(() => window.previewBounds.max[2]);
  assert.ok(Math.abs(previewTop - 2.4) < 0.02,
    `the preview must stand where the action says it does; got ${previewTop}`);
  assert.equal(action.base, 0);
  assert.deepEqual(errors, []);
  console.log("PASS Z-up work plane, preview bounds, pointer rectangle and height submission; no API calls");
} catch (error) {
  if (page && !page.isClosed()) {
    if (process.env.SKETCH_SCREENSHOT) await page.screenshot({ path: process.env.SKETCH_SCREENSHOT });
    console.error(await page.evaluate(() => {
      const anchor = window.projectPoint([0, 0, 0]);
      return { phase: document.querySelector(".stage-sketch")?.dataset.phase,
        preview: window.previewSpec, bounds: window.previewBounds, submitted: window.submitted,
        anchor, hit: document.elementFromPoint(...anchor)?.outerHTML.slice(0, 400) };
    }));
  }
  throw error;
} finally {
  await browser?.close();
  await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("studio-sketch-axes-"));
  await rm(cacheDir, { recursive: true, force: true });
}
