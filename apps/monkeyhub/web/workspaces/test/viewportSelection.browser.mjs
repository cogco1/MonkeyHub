/** Actual WebGL selection/highlight/layout changes must never refit the view. */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "viewport-selection-"));
const http = createHttpServer();
const errors = [];
let browser, vite;
const rhino = await rhino3dm();
const model = new rhino.File3dm();
for (let column = 0; column < 3; column++) {
  const mesh = new rhino.Mesh(), x = column * 4;
  for (const point of [[x,0,0],[x+2,0,0],[x+2,2,0],[x,2,0],[x,0,2],[x+2,0,2],[x+2,2,2],[x,2,2]]) mesh.vertices().add(...point);
  for (const face of [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]) mesh.faces().addQuadFace(...face);
  mesh.normals().computeNormals();
  const attributes = new rhino.ObjectAttributes();
  attributes.name = `mass-${column}`;
  attributes.setUserString("archflow:component", "fixture");
  attributes.setUserString("archflow:object_ref", `cad-object:mass-${column}`);
  model.objects().add(mesh, attributes); attributes.delete(); mesh.delete();
}
const bytes = Buffer.from(model.toByteArray()); model.delete();
const html = `<!doctype html><html><head><style>
body { margin: 0; } #pane { height: calc(100vh - 100px); } .viewport-host { position: relative; width: 100%; height: 100%; }
.viewport-canvas { display: block; } .viewport-state { position: absolute; inset: 0; pointer-events: none; }
</style></head><body><div id="root"></div><script type="module">
import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { PerspectiveCamera, Vector3 } from "three";
import { ThreeDmViewport } from "/src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx";
import { createInteractionSession } from "/src/workspaces/monkeyarch/interactionSession.ts";
window.picks = []; window.resolved = 0; window.status = "idle";
function Harness() {
  const viewport = useRef(null), interaction = useRef(createInteractionSession());
  const [narrow, setNarrow] = useState(false);
  window.viewport = viewport;
  window.setNarrow = setNarrow;
  const pick = (value) => {
    window.picks.push(value?.objectName ?? null);
    setNarrow(Boolean(value));
    // A delayed semantic readback may repaint selection and reveal an inspector.
    setTimeout(() => { viewport.current.highlight(value ? { objectNames: [value.objectName] } : null); window.resolved++; }, 30);
  };
  return React.createElement("div", { id: "pane", style: { width: narrow ? "460px" : "1000px" } },
    React.createElement(ThreeDmViewport, { ref: viewport, interaction, hoverEnabled: true,
      onInspection: () => {}, onSource: () => {}, onRequestFile: () => {},
      onStatus: (value) => { window.status = value; }, onPick: pick }));
}
createRoot(document.getElementById("root")).render(React.createElement(Harness));
window.load = async (preserveCamera = false) => {
  const response = await fetch("/fixture.3dm");
  await window.viewport.current.openFile(new File([await response.arrayBuffer()], "fixture.3dm"), "FIXTURE", { preserveCamera });
};
window.projectPoint = (point) => {
  const s = window.viewport.current.camera(), r = document.querySelector("canvas").getBoundingClientRect();
  const c = new PerspectiveCamera(s.fov, r.width/r.height, 0.01, 10000);
  c.position.fromArray(s.position); c.up.fromArray(s.up); c.lookAt(new Vector3(...s.target)); c.updateMatrixWorld();
  const p = new Vector3(...point).project(c);
  return [r.left + (p.x + 1)*r.width/2, r.top + (1-p.y)*r.height/2];
};
</script></body></html>`;
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, cacheDir, publicDir: "../.generated/public",
    logLevel: "silent", plugins: [react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", async (request, response) => {
    if (request.url === "/selection-test") {
      response.setHeader("Content-Type", "text/html"); response.end(await vite.transformIndexHtml(request.url, html));
    } else if (request.url === "/fixture.3dm") response.end(bytes);
    else if (request.url.startsWith("/api/")) { errors.push(`Unexpected project call ${request.url}`); response.writeHead(500).end(); }
    else vite.middlewares(request, response);
  });
  await new Promise(resolve => http.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, args: ["--enable-unsafe-swiftshader"] });
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  page.on("pageerror", error => errors.push(error.message));
  page.setDefaultTimeout(15000);
  const snapshot = () => page.evaluate(() => window.viewport.current.camera());
  await page.goto(`http://127.0.0.1:${http.address().port}/selection-test`);
  await page.waitForFunction(() => window.viewport?.current?.camera());
  await page.evaluate(() => window.load());
  await page.waitForFunction(() => window.status === "ready");
  const fitted = await snapshot();
  assert.ok(fitted.target.every((value, axis) => Math.abs(value - [5, 1, 1][axis]) < 1e-9), "initial model load still fits its actual bounds");
  await page.evaluate(() => window.setNarrow(true));
  await page.waitForFunction(() => document.querySelector("canvas").width === 460);
  await page.waitForTimeout(80);
  assert.deepEqual(await snapshot(), fitted, "opening an inspector immediately after Fit must not refit");
  await page.evaluate(() => window.viewport.current.fitView());
  const narrowFit = await snapshot();
  assert.notDeepEqual(narrowFit.position, fitted.position, "explicit Fit adapts to a narrower panel");

  for (const column of [0,1,2,0,2,1]) {
    const before = await snapshot();
    const count = await page.evaluate(() => window.resolved);
    const point = await page.evaluate(c => window.projectPoint([c*4+1, 1, 2]), column);
    await page.mouse.click(...point);
    await page.waitForFunction(n => window.resolved > n, count);
    assert.equal(await page.evaluate(() => window.picks.at(-1)), `mass-${column}`);
    assert.deepEqual(await snapshot(), before, "local picking and delayed semantic highlight preserve the view");
  }
  let before = await snapshot();
  await page.mouse.click(8, 8);
  await page.waitForFunction(() => window.picks.at(-1) === null && document.querySelector("canvas").width === 1000);
  await page.waitForTimeout(80);
  assert.deepEqual(await snapshot(), before, "clearing selection and closing its panel preserves the view");
  // Real orbit input, followed by workspace changes and asynchronous model readback.
  await page.mouse.move(260, 260); await page.mouse.down(); await page.mouse.move(310, 290, { steps: 5 }); await page.mouse.up();
  before = await snapshot();
  await page.evaluate(() => window.setNarrow(true));
  await page.waitForFunction(() => document.querySelector("canvas").width === 460);
  await page.evaluate(() => window.load(true));
  assert.deepEqual(await snapshot(), before, "resize and preserve-camera model replacement keep an orbited view");
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.waitForTimeout(80);
  assert.deepEqual(await snapshot(), before, "window resizing does not change the camera");

  // The actual preset must switch the renderer/controls camera, not just its pose.
  for (const previousView of ["perspective", "top", "front", "right"]) {
    await page.evaluate(view => window.viewport.current.standardView(view), previousView);
    if (previousView !== "perspective") {
      const standard = await snapshot();
      assert.equal(standard.projection, "orthographic", `${previousView} must use parallel projection`);
      const offset = standard.position.map((value, axis) => value - standard.target[axis]);
      const length = Math.hypot(...offset);
      const expected = { top: [0, 0, 1], front: [0, -1, 0], right: [1, 0, 0] }[previousView];
      assert.ok(offset.every((value, axis) => Math.abs(value / length - expected[axis]) < 1e-6),
        `${previousView} must look along its world axis`);
    }
    await page.evaluate(() => window.viewport.current.standardView("iso"));
    const iso = await snapshot();
    assert.equal(iso.projection, "orthographic", `Iso after ${previousView} must remove perspective`);
    const direction = iso.position.map((value, axis) => value - iso.target[axis]);
    assert.ok(direction[0] > 0 && direction[1] < 0 && direction[2] > 0);
    assert.ok(Math.abs(direction[0] + direction[1]) < 1e-8 && Math.abs(direction[0] - direction[2]) < 1e-8,
      "Iso must use the same scale along all three world axes");
  }
  before = await snapshot();
  await page.evaluate(() => window.load(true));
  assert.deepEqual(await snapshot(), before, "model refresh preserves the isometric projection");
  await page.evaluate(() => window.viewport.current.fitView());
  assert.equal((await snapshot()).projection, "orthographic", "Fit keeps the isometric projection");
  await page.evaluate(() => window.viewport.current.standardView("perspective"));
  assert.equal((await snapshot()).projection, "perspective", "the explicit perspective preset still works");
  assert.deepEqual(errors, []);
  if (process.env.BROWSER_OUTPUT) {
    await mkdir(process.env.BROWSER_OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(process.env.BROWSER_OUTPUT, "viewport-selection.png") });
    await writeFile(path.join(process.env.BROWSER_OUTPUT, "viewport-report.json"), JSON.stringify({
      source: process.env.SOURCE_SHA, picked: await page.evaluate(() => window.picks), errors,
      assertions: ["initial Fit", "layout after Fit", "explicit Fit", "six picks with async highlight", "clear and close panel", "orbit/resize/readback", "window resize", "standard views", "orthographic Iso", "Iso refresh and Fit", "explicit perspective"],
    }, null, 2));
  }
  console.log("viewport selection, asynchronous highlight, resize, Fit, standard views and orthographic Iso: PASS");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise(resolve => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
