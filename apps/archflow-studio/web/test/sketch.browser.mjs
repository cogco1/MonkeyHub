import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "studio-sketch-axes-"));
const http = createHttpServer();
const errors = [];
let browser, vite, page;
// A repeatable building-mass array exercises the real model loader, edge snap
// scan and renderer: 400 separate meshes, 4,800 triangles, no project writes.
const rhino = await rhino3dm();
const model = new rhino.File3dm();
for (let row = 0; row < 20; row++) for (let column = 0; column < 20; column++) {
  const mesh = new rhino.Mesh();
  const x = column * 3, y = row * 3, height = 2 + (row + column) % 5;
  for (const point of [[x,y,0], [x+2,y,0], [x+2,y+2,0], [x,y+2,0],
    [x,y,height], [x+2,y,height], [x+2,y+2,height], [x,y+2,height]]) mesh.vertices().add(...point);
  for (const face of [[0,3,2,1], [4,5,6,7], [0,1,5,4], [1,2,6,5], [2,3,7,6], [3,0,4,7]]) mesh.faces().addQuadFace(...face);
  mesh.normals().computeNormals();
  const attributes = new rhino.ObjectAttributes();
  attributes.name = `mass-${row}-${column}`;
  attributes.colorSource = rhino.ObjectColorSource.ColorFromObject;
  attributes.objectColor = { r: 180, g: 184, b: 191, a: 255 };
  attributes.setUserString("archflow:component", "fixture-massing");
  attributes.setUserString("archflow:object_ref", `cad-object:mass-${row}-${column}`);
  model.objects().add(mesh, attributes);
  attributes.delete();
  mesh.delete();
}
const modelBytes = Buffer.from(model.toByteArray());
model.delete();
const html = `<!doctype html><html><head><style>
#root, .stage { height: 100vh; }
#root .viewport-state { display: none; }
</style></head><body><div id="root"></div><script type="module">
import React, { createRef } from "react";
import { createRoot } from "react-dom/client";
import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, PerspectiveCamera, Scene, SphereGeometry, Vector3 } from "three";
import { Preselection } from "/src/workspaces/monkeyarch/viewer/preselection.ts";
import { Stage } from "/src/features/stage/Stage.tsx";
import { UserPreferencesProvider } from "/src/features/settings/preferences.tsx";
import "/src/styles.css";
const add = Scene.prototype.add;
window.previewGroups = [];
window.previewUpdates = 0;
window.previewRenders = 0;
window.stageCommits = 0;
window.previewTimes = [];
window.pointerTimes = [];
window.hoverRenders = 0;
window.hoverTimes = [];
window.picks = [];
Scene.prototype.add = function (...objects) {
  for (const object of objects) {
    if (object.name === "archflow-preselection") {
      window.hoverGroup = object;
      object.disposals = 0;
      for (const child of object.children) {
        child.geometry.addEventListener("dispose", () => object.disposals++);
        child.material.addEventListener("dispose", () => object.disposals++);
      }
      this.onAfterRender = () => {
        if (!window.hoverGroup?.visible) return;
        window.hoverRenders++;
        if (window.pointerStart !== undefined) window.hoverTimes.push(performance.now() - window.pointerStart);
        delete window.pointerStart;
      };
    }
    if (object.name !== "archflow-sketch-preview") continue;
    window.previewGroup = object;
    window.previewGroups.push(object);
    object.disposals = 0;
    for (const child of object.children) {
      child.geometry.addEventListener("dispose", () => object.disposals++);
      child.material.addEventListener("dispose", () => object.disposals++);
    }
    this.onBeforeRender = () => { window.previewRenders++; };
  }
  return add.apply(this, objects);
};
const viewportRef = window.viewport = createRef();
window.submitted = [];
window.submissionCurrent = [];
window.toolChanges = [];
const noop = () => {};
window.testRoot = createRoot(document.getElementById("root"));
window.testRoot.render(
  React.createElement(UserPreferencesProvider, null, React.createElement(React.Profiler,
    { id: "stage", onRender: () => window.stageCommits++ }, React.createElement(Stage, {
    viewportRef, embedded: true, status: "ready", message: "", picked: null,
    versions: [], workingCopies: [], loadedShas: [], loadingSha: null, designHistory: null,
    documentView: { open: false, mounted: false }, displayMode: "model", tool: null,
    gestures: [], home: null, hasModel: true, editingBaseRunId: null, loadedRunId: null,
    modelAnnotations: null, annotationsReady: false, documentModelSources: [],
    editingModelSource: null, viewedModelSource: null, artifactError: null, baseError: null,
    blend: null, captureState: "idle", onTool: noop, onInspection: (value) => { window.inspection = value; }, onStatus: noop,
    onRequestFile: noop, onOpenFile: noop, onSource: noop, onPick: (pick) => { window.picks.push(pick); },
    onSketch: async (action, stillCurrent) => { window.submitted.push(action); window.submissionCurrent.push(stillCurrent); },
    model: { onDelete: noop, canDelete: true, deleting: false, subject: "Fixture", onUndo: noop, canUndo: true,
      onRedo: noop, canRedo: true, onClearSelection: () => viewportRef.current.highlight(null), hasSelection: true,
      onTool: (tool) => { window.toolChanges.push(tool); } },
  }))),
);
window.readPreviewBounds = () => {
  if (!window.previewGroup?.parent) return;
  const bounds = new Box3().setFromObject(window.previewGroup);
  window.previewBounds = { min: bounds.min.toArray(), max: bounds.max.toArray() };
};
window.objectBounds = (object) => new Box3().setFromObject(object);
window.hiddenNearHit = () => {
  const hit = window.interaction.hover;
  const hidden = new Mesh(new BoxGeometry(0.01, 0.01, 0.01), new MeshBasicMaterial());
  hidden.position.copy(hit.object.worldToLocal(hit.point.clone()));
  hidden.visible = false; hit.object.add(hidden);
  return hidden;
};
window.curvedFaceBenchmark = () => {
  const geometry = new SphereGeometry(5, 320, 160);
  const mesh = new Mesh(geometry, new MeshBasicMaterial());
  const model = new Group(); model.add(mesh, new Mesh(geometry, mesh.material));
  const getAttribute = geometry.getAttribute;
  let positionReads = 0;
  geometry.getAttribute = function (name) {
    if (name === "position") positionReads++;
    return getAttribute.call(this, name);
  };
  const start = performance.now();
  const layer = new Preselection(model, "#60616c");
  const preparationMs = performance.now() - start;
  geometry.getAttribute = getAttribute;
  const measurements = [];
  const hit = { object: mesh, mesh, objectName: null, userStrings: {}, point: new Vector3(), normal: null };
  // Warm the existing outline extraction separately: this measures new face
  // misses, not the pre-existing feature-edge or raycast path.
  layer.update({ ...hit, faceIndex: 798 }, null);
  const buffer = layer.group.children[1].geometry.attributes.position;
  for (const seed of [800,802,804,806,808,810,812,814]) {
    const start = performance.now();
    layer.update({ ...hit, faceIndex: seed }, null);
    measurements.push({ seed, ms: performance.now() - start, triangles: layer.group.children[1].geometry.drawRange.count / 3 });
  }
  const reused = layer.group.children[1].geometry.attributes.position === buffer;
  layer.dispose(); geometry.dispose(); mesh.material.dispose();
  return { triangles: geometry.index.count / 3, preparationMs, positionReads, measurements, reused };
};
window.previewIdentity = () => [window.previewGroup, ...window.previewGroup.children.flatMap(
  (child) => [child, child.geometry, child.material, child.geometry.attributes.position, child.geometry.attributes.normal],
)];
window.moveBurst = (points) => {
  const overlay = document.querySelector(".stage-sketch");
  for (const [clientX, clientY] of points) overlay.dispatchEvent(new PointerEvent("pointermove",
    { bubbles: true, clientX, clientY, pointerId: 1 }));
};
document.addEventListener("pointermove", () => { window.pointerStart = performance.now(); }, true);
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
    plugins: [{ name: "interaction-session-probe", enforce: "pre", transform(source, id) {
      if (id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) {
        const marker = "  const pickAt = useCallback(";
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, "(window as any).draftRuntime = () => runtimeRef.current;\n" + marker), map: null };
      }
      if (!id.replaceAll("\\", "/").endsWith("/features/stage/Stage.tsx")) return;
      const marker = "  const interaction = useRef(createInteractionSession());";
      assert.equal(source.split(marker).length, 2);
      return { code: source.replace(marker, marker + "\n  (window as any).interaction = interaction.current;"), map: null };
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
  });
  http.on("request", async (request, response) => {
    if (request.url === "/sketch-test") {
      response.setHeader("Content-Type", "text/html");
      response.end(await vite.transformIndexHtml(request.url, html));
    } else if (request.url === "/fixture.3dm") {
      response.end(modelBytes);
    } else if (request.url.startsWith("/api/")) {
      errors.push(`Unexpected API call: ${request.url}`);
      response.writeHead(500).end();
    } else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
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
      window.previewUpdates++;
      const start = performance.now();
      const result = preview(spec);
      window.previewTimes.push(performance.now() - start);
      if (window.pointerStart !== undefined) {
        window.pointerTimes.push(performance.now() - window.pointerStart);
        delete window.pointerStart;
      }
      window.readPreviewBounds();
      return result;
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
  // Open paths use the real Stage events and reusable viewport geometry.
  const project = (point) => page.evaluate((value) => window.projectPoint(value), point);
  const start = await project([0, 0, 0]), lineEnd = await project([3, 0, 0]), bend = await project([3, 2, 0]);
  const drawingInput = page.locator(".sketch-entry input");
  await page.keyboard.press("l");
  assert.equal(await page.getByRole("button", { name: "Line", exact: true }).getAttribute("aria-pressed"), "true");
  await page.mouse.click(...start);
  await page.mouse.move(...lineEnd);
  await drawingInput.fill("3"); await drawingInput.press("Enter");
  await page.mouse.move(...bend); await page.mouse.click(...bend);
  await page.mouse.move(...await project([4, 3, 0]));
  const linePerf = await page.evaluate(async (point) => {
    await new Promise(requestAnimationFrame);
    const identities = window.previewIdentity();
    const commits = window.stageCommits, updates = window.previewUpdates;
    for (let index = 0; index < 20; index++) {
      window.moveBurst([point, [point[0] + 1, point[1]], [point[0] + 2, point[1]]]);
      await new Promise(requestAnimationFrame);
    }
    return { commits: window.stageCommits - commits, updates: window.previewUpdates - updates,
      reused: identities.every((item, index) => item === window.previewIdentity()[index]),
      closed: window.previewSpec.closed, surface: window.previewGroup.children[1].visible,
      edges: window.previewGroup.children[0].geometry.drawRange.count };
  }, await project([4, 3, 0]));
  assert.deepEqual(linePerf, { commits: 0, updates: 20, reused: true, closed: false, surface: false, edges: 6 });
  await drawingInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 1);
  const openLine = await page.evaluate(() => window.submitted[0]);
  assert.equal(openLine.closed, false); assert.equal(openLine.height, 0);
  assert.equal(openLine.profile.length, 3, "Enter keeps settled vertices and drops the moving tail");
  assert.ok(Math.abs(openLine.profile[1][0] - 3) < 1e-6);
  assert.equal(await page.evaluate(() => window.submissionCurrent[0]()), true);

  await page.keyboard.press("a");
  assert.equal(await page.evaluate(() => window.submissionCurrent[0]()), false, "switching tools invalidates a late proposal callback");
  await page.mouse.click(...start); await page.mouse.move(...await project([4, 0, 0]));
  await drawingInput.fill("4"); await drawingInput.press("Enter");
  await page.mouse.move(...await project([1, 2, 0]));
  await page.waitForFunction(() => window.previewSpec?.profile.length === 33);
  const bulge = await page.evaluate(() => window.previewSpec.profile[16]);
  assert.ok(Math.abs(bulge[0] - 2) < 0.02 && Math.abs(bulge[1] - 2) < 0.02,
    "the pointer sets perpendicular distance, not an arbitrary third point");
  await drawingInput.fill("-0.75");
  const preciseArc = await page.evaluate(() => window.previewSpec.profile);
  await page.mouse.move(...await project([3, 3, 0]));
  assert.deepEqual(await page.evaluate(() => window.previewSpec.profile), preciseArc);
  await drawingInput.fill("-"); await drawingInput.press("Enter");
  assert.equal(await page.evaluate(() => window.submitted.length), 1, "an intermediate sign cannot submit the previous arc");
  await drawingInput.fill("-0.75"); await drawingInput.press("Enter"); await page.keyboard.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 2);
  const openArc = await page.evaluate(() => window.submitted[1]);
  assert.equal(openArc.closed, false); assert.equal(openArc.height, 0); assert.equal(openArc.profile.length, 33);
  assert.ok(Math.abs(openArc.profile[16][1] + 0.75) < 1e-6);
  assert.notDeepEqual(openArc.profile[0], openArc.profile.at(-1));

  await page.getByRole("button", { name: "Line tools", exact: true }).click();
  await page.getByRole("button", { name: "Freehand", exact: true }).click();
  await page.mouse.move(...start); await page.mouse.down();
  await page.mouse.move(...await project([1, 0.5, 0]), { steps: 5 });
  await page.mouse.move(...await project([2, -0.5, 0]), { steps: 5 });
  assert.equal(await page.evaluate(() => window.submitted.length), 2, "dragging never completes a model action");
  const strokePerf = await page.evaluate(async (end) => {
    await new Promise(requestAnimationFrame);
    const commits = window.stageCommits, group = window.previewGroup;
    const overlay = document.querySelector(".stage-sketch");
    for (let index = 0; index < 12; index++) overlay.dispatchEvent(new PointerEvent("pointermove", {
      bubbles: true, buttons: 1, pointerId: 1, clientX: end[0] + index * 2, clientY: end[1],
    }));
    await new Promise(requestAnimationFrame);
    return { commits: window.stageCommits - commits, sameGroup: group === window.previewGroup,
      closed: window.previewSpec.closed, surface: window.previewGroup.children[1].visible };
  }, await project([2, -0.5, 0]));
  assert.deepEqual(strokePerf, { commits: 0, sameGroup: true, closed: false, surface: false });
  await page.mouse.move(...await project([3, 1, 0])); await page.mouse.up();
  await page.waitForFunction(() => window.submitted.length === 3);
  const freehand = await page.evaluate(() => window.submitted[2]);
  assert.equal(freehand.closed, false); assert.equal(freehand.height, 0); assert.ok(freehand.profile.length > 8);
  assert.ok(Math.abs(freehand.profile.at(-1)[0] - 3) < 0.02);
  assert.equal(await page.locator(".stage-sketch").getAttribute("data-phase"), "idle");

  // Ending at the start switches to the existing face/height gesture without
  // repeating its first point or turning the release click into a submission.
  await page.mouse.move(...start); await page.mouse.down();
  for (const point of [[3, 0, 0], [2, 2, 0], [0, 0, 0]]) await page.mouse.move(...await project(point), { steps: 8 });
  await page.mouse.up();
  assert.equal(await page.locator(".stage-sketch").getAttribute("data-phase"), "height");
  const closedStroke = await page.evaluate(() => window.interaction.sketch.profile);
  assert.notDeepEqual(closedStroke[0], closedStroke.at(-1));
  assert.equal(await page.evaluate(() => window.submitted.length), 3);
  await page.mouse.move(...await project([0, 0, 1]));
  await page.waitForFunction(() => Math.abs(window.previewSpec?.height - 1) < 0.03);
  await page.keyboard.press("Escape");

  // A capture cancellation and a tool switch also cancel queued path paints.
  await page.mouse.move(...start); await page.mouse.down();
  await page.mouse.move(...lineEnd); await page.keyboard.press("Escape"); await page.mouse.up();
  assert.equal(await page.evaluate(() => window.submitted.length), 3);
  await page.getByRole("button", { name: "Line", exact: true }).click();
  await page.mouse.click(...start); await page.mouse.move(...lineEnd);
  await page.getByRole("button", { name: "2-point arc", exact: true }).click();
  assert.equal(await page.evaluate(() => window.previewGroup?.parent === null), true);
  assert.equal(await page.evaluate(() => window.submitted.length), 3);
  await page.mouse.click(...start); await page.mouse.click(...lineEnd);
  const arcCrown = await project([1, 1, 0]);
  await page.mouse.move(...arcCrown); await page.mouse.click(...arcCrown);
  await page.waitForFunction(() => window.submitted.length === 4);
  assert.equal(await page.evaluate(() => window.submitted[3].closed), false, "a third click finishes the open arc");
  await page.getByRole("button", { name: "Line", exact: true }).click();
  await page.mouse.click(...start); await page.mouse.dblclick(...lineEnd);
  await page.waitForFunction(() => window.submitted.length === 5);
  assert.equal(await page.evaluate(() => window.submitted[4].profile.length), 2, "double-click ends one line without adding another start or duplicate point");
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.evaluate(() => { window.submitted = []; window.submissionCurrent = []; });
  console.log("PASS open Line / signed 2-point arc / held Freehand: exact numbers, no implicit face, zero pointer React commits, one completion and cancellation");
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  const anchor = await page.evaluate(() => window.projectPoint([0, 0, 0]));
  const corner = await page.evaluate(() => window.projectPoint([3, 2, 0]));
  await page.evaluate(() => { window.previewBounds = null; });
  await page.mouse.click(...anchor);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "profile");
  assert.equal(await page.getByRole("button", { name: "Undo model", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Redo model", exact: true }).isDisabled(), true);
  await page.mouse.move(...corner);
  await page.waitForFunction(() => window.previewBounds !== null);
  const burstFrames = async (point, frames = 20) => page.evaluate(async ({ point, frames }) => {
    // Let the current snap label settle before measuring React commits.
    await new Promise(requestAnimationFrame);
    const identity = window.previewIdentity();
    const before = { updates: window.previewUpdates, renders: window.previewRenders,
      commits: window.stageCommits, groups: window.previewGroups.length };
    for (let frame = 0; frame < frames; frame++) {
      window.moveBurst(Array.from({ length: 50 }, (_, index) =>
        [point[0] + index / 500, point[1] + index / 500]));
      await new Promise(requestAnimationFrame);
    }
    return { updates: window.previewUpdates - before.updates, renders: window.previewRenders - before.renders,
      commits: window.stageCommits - before.commits, groups: window.previewGroups.length - before.groups,
      reused: identity.every((item, index) => item === window.previewIdentity()[index]),
      disposals: window.previewGroup.disposals };
  }, { point, frames });
  assert.deepEqual(await burstFrames(corner), { updates: 20, renders: 20, commits: 0, groups: 0, reused: true, disposals: 0 });
  const themeChange = await page.evaluate(async () => {
    const identity = window.previewIdentity();
    document.documentElement.dataset.theme = "dark";
    await new Promise(requestAnimationFrame);
    const dark = window.previewGroup.children.map((child) => child.material.color.getHexString());
    document.documentElement.dataset.theme = "light";
    await new Promise(requestAnimationFrame);
    return { changed: window.previewGroup.children.every((child, index) => child.material.color.getHexString() !== dark[index]),
      reused: identity.every((item, index) => item === window.previewIdentity()[index]) };
  });
  assert.deepEqual(themeChange, { changed: true, reused: true });
  await page.evaluate(() => { window.profileIdentity = window.previewIdentity(); });
  // Settle before the scheduled frame: the click must use the latest session.
  await page.evaluate(([clientX, clientY]) => {
    window.moveBurst([[clientX, clientY]]);
    document.querySelector(".stage-sketch").dispatchEvent(new MouseEvent("click", { bubbles: true, clientX, clientY }));
  }, corner);
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
  assert.deepEqual(await burstFrames(top), { updates: 20, renders: 20, commits: 0, groups: 0, reused: true, disposals: 0 });
  assert.equal(await page.evaluate(() => window.profileIdentity.every((item, index) => item === window.previewIdentity()[index])), true,
    "height uses the profile's objects, geometry, materials and attributes");
  await page.mouse.move(...top);
  if (process.env.SKETCH_SCREENSHOT) await page.screenshot({ path: process.env.SKETCH_SCREENSHOT });
  await page.evaluate(([clientX, clientY]) => {
    window.moveBurst([[clientX, clientY]]);
    document.querySelector(".stage-sketch").dispatchEvent(new MouseEvent("click", { bubbles: true, clientX, clientY }));
  }, top);
  await page.waitForFunction(() => window.submitted.length === 1);
  assert.equal(await page.evaluate(() => window.previewGroup.disposals), 6, "completion disposes three geometries/materials once");
  assert.equal(await page.getByRole("button", { name: "Undo model", exact: true }).isDisabled(), false);
  assert.equal(await page.getByRole("button", { name: "Redo model", exact: true }).isDisabled(), false);
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
  // The ordinary drawing keys arm the same real tools their buttons use.
  await page.keyboard.press("Space");
  await page.waitForFunction(() => document.querySelector(".stage-sketch") === null);
  await page.keyboard.press("c");
  await page.getByRole("button", { name: "Circle", exact: true }).waitFor();
  const circleCenter = await page.evaluate(() => window.projectPoint([4, 0, 0]));
  const circleEdge = await page.evaluate(() => window.projectPoint([5.5, 0, 0]));
  await page.mouse.click(...circleCenter);
  await page.mouse.move(...circleEdge);
  await page.mouse.click(...circleEdge);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "height");
  const sizeInput = page.locator(".sketch-entry input");
  await sizeInput.fill("2");
  await sizeInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 2);
  const circle = await page.evaluate(() => window.submitted[1]);
  assert.equal(circle.profile.length, 32);
  assert.equal(circle.height, 2);
  assert.ok(Math.abs(circle.profile[0][0] - 5.5) < 0.02);

  // Typed width/depth and an explicit zero create a face, not a zero-height
  // pointer accident, through the very same completion callback.
  await page.keyboard.press("r");
  await page.mouse.click(...anchor);
  await sizeInput.fill("4,2");
  await sizeInput.press("Enter");
  await sizeInput.fill("0");
  await sizeInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 3);
  const face = await page.evaluate(() => window.submitted[2]);
  assert.equal(face.height, 0);
  assert.ok(Math.abs(face.profile[2][0] - 4) < 0.02);
  assert.ok(Math.abs(face.profile[2][1] - 2) < 0.02);

  await page.keyboard.press("l");
  for (const point of [[0, 0, 0], [3, 0, 0], [2, 2, 0], [0, 0, 0]]) {
    const screen = await page.evaluate((value) => window.projectPoint(value), point);
    await page.mouse.click(...screen);
  }
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "height");
  await sizeInput.fill("1.5");
  await sizeInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 4);
  const polygon = await page.evaluate(() => window.submitted[3]);
  assert.equal(polygon.profile.length, 3);
  assert.equal(polygon.height, 1.5);

  // A vertical rectangle remains local 2D plus its explicit world frame.
  await page.getByRole("combobox", { name: "Drawing plane" }).selectOption("xz");
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  const verticalCorner = await page.evaluate(() => window.projectPoint([2, 0, 3]));
  await page.mouse.click(...anchor);
  await page.mouse.move(...verticalCorner);
  await page.mouse.click(...verticalCorner);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "height");
  await sizeInput.fill("-1.25");
  await sizeInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 5);
  const vertical = await page.evaluate(() => window.submitted[4]);
  assert.deepEqual(vertical.plane.xAxis, [1, 0, 0]);
  assert.deepEqual(vertical.plane.yAxis, [0, 0, 1]);
  assert.equal(vertical.height, -1.25);
  assert.ok(Math.abs(vertical.profile[2][0] - 2) < 0.02);
  assert.ok(Math.abs(vertical.profile[2][1] - 3) < 0.02);

  // T reads a true 3D distance, and its temporary line keeps both elevations.
  await page.keyboard.press("t");
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "from");
  await page.evaluate(() => {
    window.originalSnap = window.viewport.current.snapOnModel;
    window.measurePoint = [1, 2, 3];
    window.viewport.current.snapOnModel = () => ({ point: window.measurePoint, kind: "endpoint", objectName: "fixture" });
  });
  await page.mouse.click(...anchor);
  await page.evaluate(() => { window.measurePoint = [1, 2, 6]; });
  await page.mouse.move(anchor[0] + 20, anchor[1] - 20);
  await page.mouse.click(anchor[0] + 20, anchor[1] - 20);
  await page.getByText("3.000 m", { exact: false }).waitFor();
  const measured = await page.evaluate(() => window.previewBounds);
  [1, 2, 3].forEach((expected, index) => assert.ok(Math.abs(measured.min[index] - expected) < 1e-6));
  [1, 2, 6].forEach((expected, index) => assert.ok(Math.abs(measured.max[index] - expected) < 1e-6));
  assert.equal(await page.evaluate(() => window.submitted.length), 5, "measurement submits no model action");
  await page.evaluate(() => { window.viewport.current.snapOnModel = window.originalSnap; });
  await page.keyboard.press("Space");
  for (const key of ["p", "m", "q", "s"]) await page.keyboard.press(key);
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  assert.deepEqual(await page.evaluate(() => window.toolChanges.filter((tool) => tool !== "select").slice(-5)), ["pushPull", "move", "rotate", "scale", "copy"]);
  assert.equal(await page.getByRole("button", { name: "Undo model", exact: true }).isEnabled(), true);
  assert.equal(await page.getByRole("button", { name: "Redo model", exact: true }).isEnabled(), true);
  await page.keyboard.press("r");
  await page.mouse.click(...anchor);
  await page.waitForFunction(() => document.querySelector(".stage-sketch")?.dataset.phase === "profile");
  const cameraBefore = await page.evaluate(() => window.viewport.current.camera());
  for (const button of ["middle", "right"]) {
    await page.mouse.move(anchor[0] + 50, anchor[1] + 50);
    await page.mouse.down({ button });
    await page.mouse.move(anchor[0] + 105, anchor[1] + 85, { steps: 4 });
    await page.mouse.up({ button });
    assert.equal(await page.locator(".stage-sketch").getAttribute("data-phase"), "profile", "navigation preserves the drawing action");
    assert.equal(await page.evaluate(() => window.submitted.length), 5, "navigation never submits a shape");
  }
  const cameraAfter = await page.evaluate(() => window.viewport.current.camera());
  assert.notDeepEqual(cameraAfter.position, cameraBefore.position, "the camera really moved while drawing");

  // Cancel with a frame still queued: no late preview and no submission.
  const cancel = await page.evaluate(async () => {
    window.moveBurst([[600, 450]]);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    const calls = window.previewUpdates;
    await new Promise(requestAnimationFrame);
    return { lateCalls: window.previewUpdates - calls, parent: !!window.previewGroup.parent,
      disposals: window.previewGroup.disposals, submissions: window.submitted.length };
  });
  assert.deepEqual(cancel, { lateCalls: 0, parent: false, disposals: 6, submissions: 5 });

  await page.getByRole("combobox", { name: "Drawing plane" }).selectOption("xy");
  const startPoint = await page.evaluate(() => window.projectPoint([0, 0, 0]));
  await page.mouse.click(...startPoint);
  const negativeCorner = await page.evaluate(() => window.projectPoint([-3, -2, 0]));
  await page.mouse.move(...negativeCorner);
  // A typed input rerender must not overwrite the latest ref with an old UI
  // profile; a negative pointer direction remains negative after exact sizing.
  await sizeInput.fill("4,2");
  await sizeInput.press("Enter");
  await sizeInput.fill("-2");
  await sizeInput.press("Enter");
  await page.waitForFunction(() => window.submitted.length === 6);
  const negative = await page.evaluate(() => window.submitted[5]);
  [-4, -2].forEach((value, axis) => assert.ok(Math.abs(negative.profile[2][axis] - value) < 1e-6));
  assert.equal(negative.height, -2);

  // Axis controls read the moving session too. A UI-only change cannot restore
  // an old cursor; both arrow and Shift constraints remain in plane coordinates.
  await page.mouse.click(...startPoint);
  const axisContinuation = await page.evaluate(() => {
    const samples=[];
    for(const y of [.1,.9,.7,.9,1.3,.9]) {
      window.moveBurst([window.projectPoint([-4,y,0])]);
      samples.push({point:window.interaction.sketch.cursor,kind:window.interaction.planeSnap?.kind??null});
    }
    return samples;
  });
  for(const held of axisContinuation.slice(0,4)) {
    assert.equal(held.kind,"axis");assert.ok(Math.abs(held.point[1])<1e-6);
  }
  assert.equal(axisContinuation[4].kind,null,"the existing axis inference releases outside its hold radius");
  assert.equal(axisContinuation[5].kind,null,"returning outside acquisition cannot silently re-lock an axis");
  await page.mouse.move(...negativeCorner);
  await page.locator(".sketch-entry input").evaluate((input) => input.blur());
  await page.keyboard.press("ArrowRight");
  await page.mouse.move(negativeCorner[0] + 5, negativeCorner[1] + 5);
  await page.waitForFunction(() => Math.abs(window.previewSpec?.profile[2]?.[1]) < 1e-6);
  await page.keyboard.press("ArrowRight");
  await page.keyboard.down("Shift");
  await page.mouse.move(...negativeCorner);
  await page.waitForFunction(() => {
    const point = window.previewSpec?.profile[2];
    return point && (Math.abs(point[0]) < 1e-6 || Math.abs(point[1]) < 1e-6);
  });
  await page.keyboard.up("Shift");
  // Tool and plane changes also cancel the pending frame before clearing.
  for (const change of ["tool", "plane"]) {
    await page.mouse.move(...negativeCorner);
    const result = await page.evaluate(async (change) => {
      window.moveBurst([[650, 400]]);
      if (change === "tool") document.querySelector('button[aria-label="Circle"]').click();
      else {
        const select = document.querySelector('select[aria-label="Drawing plane"]');
        select.value = "yz";
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const updates = window.previewUpdates;
      await new Promise(requestAnimationFrame);
      return { late: window.previewUpdates - updates, parent: !!window.previewGroup.parent };
    }, change);
    assert.deepEqual(result, { late: 0, parent: false });
    if (change === "tool") await page.mouse.click(...startPoint);
  }
  assert.equal(await page.evaluate(() => window.submitted.length), 6);

  // Capacity changes are allowed for growing topology, never ordinary motion.
  const topology = await page.evaluate(() => {
    const viewport = window.viewport.current;
    const circle = (n) => Array.from({ length: n }, (_, index) =>
      [2 * Math.cos(index / n * 2 * Math.PI), 2 * Math.sin(index / n * 2 * Math.PI)]);
    viewport.sketchPreview({ profile: circle(32), base: 1, height: 2 });
    const group = window.previewGroup;
    const objects = group.children.map((child) => [child, child.geometry, child.material]);
    viewport.sketchPreview({ profile: [], base: 1, height: 0 });
    const hiddenWithoutDisposal = !group.visible && group.disposals === 0;
    viewport.sketchPreview({ profile: circle(40), base: 1, height: -2 });
    const identity = window.previewIdentity();
    const afterGrowth = group.disposals;
    viewport.sketchPreview({ profile: circle(3), base: 1, height: 0 });
    const result = { hiddenWithoutDisposal, sameObjects: objects.every((parts, index) =>
      parts[0] === group.children[index] && parts[1] === group.children[index].geometry && parts[2] === group.children[index].material),
      sameBuffersAfterShrink: identity.every((item, index) => item === window.previewIdentity()[index]),
      afterGrowth, afterShrink: group.disposals, bounds: window.previewBounds,
      drawCounts: group.children.map((child) => child.geometry.drawRange.count) };
    viewport.sketchPreview(null);
    return result;
  });
  assert.equal(topology.hiddenWithoutDisposal, true);
  assert.equal(topology.sameObjects, true);
  assert.equal(topology.sameBuffersAfterShrink, true);
  assert.equal(topology.afterGrowth, 3);
  assert.equal(topology.afterShrink, 3);
  assert.deepEqual(topology.drawCounts, [6, 3, 0]);
  assert.equal(topology.bounds.min[2], 1);
  assert.equal(topology.bounds.max[2], 1);

  // Measure the real pointer -> snap -> RAF -> preview path on loaded meshes.
  await page.keyboard.press("Escape");
  await page.getByRole("combobox", { name: "Drawing plane" }).selectOption("xy");
  await page.getByRole("button", { name: "Line", exact: true }).click();
  await page.mouse.click(...await project([0, 0, 0]));
  await page.mouse.move(...await project([2, 1, 0]));
  await page.evaluate(async () => {
    const bytes = await (await fetch("/fixture.3dm")).blob();
    await window.viewport.current.openFile(new File([bytes], "fixture.3dm"));
  });
  assert.deepEqual(await page.evaluate(() => ({ meshes: window.inspection.meshCount, triangles: window.inspection.triangleCount })),
    { meshes: 400, triangles: 4800 });
  assert.deepEqual(await page.evaluate(() => ({ tool: window.interaction.sketch.tool, phase: window.interaction.sketch.phase,
    anchor: window.interaction.sketch.anchor, press: window.interaction.press, preview: window.previewSpec })),
    { tool: "line", phase: "idle", anchor: null, press: null, preview: null }, "source replacement invalidates the old path and its scheduled paint");
  await page.getByRole("button", { name: "Select", exact: true }).click();
  const hoverPoint = await page.evaluate(() => window.projectPoint([31, 29, 0]));
  await page.mouse.move(...hoverPoint);
  await page.waitForFunction(() => window.hoverGroup?.visible);
  const hover = await page.evaluate(() => {
    const hit = window.interaction.hover;
    window.hoverMaterial = hit.mesh.material;
    window.hoverIdentity = hit.userStrings;
    return { phase: window.interaction.phase, faceVertices: window.hoverGroup.children[1].geometry.drawRange.count,
      claims: hit.userStrings, immutable: Object.isFrozen(hit.userStrings), picks: window.picks.length };
  });
  assert.equal(hover.phase, "hovering");
  assert.equal(hover.faceVertices, 6, "a box face includes both triangles, not just one half");
  assert.equal(hover.claims["archflow:component"], "fixture-massing");
  assert.equal(hover.immutable, true);
  assert.equal(hover.picks, 0, "hover never triggers the click/semantic resolution callback");
  const hoverPerformance = await page.evaluate(async (point) => {
    const canvas = document.querySelector("canvas");
    const identity = window.hoverGroup.children.flatMap((child) => [child, child.geometry, child.material, child.geometry.attributes.position]);
    const commits = window.stageCommits, renders = window.hoverRenders;
    const intervals = [];
    window.hoverTimes = [];
    let previous = await new Promise(requestAnimationFrame);
    for (let frame = 0; frame < 120; frame++) {
      for (let move = 0; move < 4; move++) canvas.dispatchEvent(new PointerEvent("pointermove",
        { bubbles: true, clientX: point[0], clientY: point[1], pointerId: 1 }));
      const now = await new Promise(requestAnimationFrame);
      intervals.push(now - previous); previous = now;
    }
    const p95 = (values) => [...values].sort((a,b) => a-b)[Math.floor((values.length - 1) * 0.95)];
    return { moves: 480, renders: window.hoverRenders - renders, commits: window.stageCommits - commits,
      reused: identity.every((item, index) => item === window.hoverGroup.children.flatMap(
        (child) => [child, child.geometry, child.material, child.geometry.attributes.position])[index]),
      originalMaterial: window.interaction.hover.mesh.material === window.hoverMaterial,
      sameClaims: window.interaction.hover.userStrings === window.hoverIdentity,
      frameIntervalP95Ms: p95(intervals), pointerToAfterRenderP95Ms: p95(window.hoverTimes) };
  }, hoverPoint);
  assert.equal(hoverPerformance.renders, 120);
  assert.equal(hoverPerformance.commits, 0);
  assert.equal(hoverPerformance.reused, true);
  assert.equal(hoverPerformance.originalMaterial, true, "hover never recolours the real model");
  assert.equal(hoverPerformance.sameClaims, true, "loaded identity is indexed once");
  console.log("MEASURE local hover / headless Chrome / 400 meshes:", JSON.stringify(hoverPerformance));
  const curvedFaces = await page.evaluate(() => window.curvedFaceBenchmark());
  assert.equal(curvedFaces.triangles, 101760);
  assert.equal(curvedFaces.positionReads, 1, "two instances sharing a geometry build one face index");
  assert.equal(curvedFaces.reused, true, "crossing new sphere faces reuses the display buffer");
  assert.ok(curvedFaces.measurements.every((row) => row.triangles === 2));
  console.log("MEASURE curved face lookup + buffer update (excludes raycast/snap/render):", JSON.stringify(curvedFaces));
  const hiddenSnap = await page.evaluate((point) => {
    const before = window.viewport.current.snapOnModel(...point);
    const hidden = window.hiddenNearHit();
    const after = window.viewport.current.snapOnModel(...point);
    hidden.removeFromParent(); hidden.geometry.dispose(); hidden.material.dispose();
    return { before, after };
  }, hoverPoint);
  assert.deepEqual(hiddenSnap.after, hiddenSnap.before, "hidden children cannot supply snap edges or points");
  const ghostClear = await page.evaluate(async (point) => {
    const name = window.interaction.hover.objectName;
    document.querySelector("canvas").dispatchEvent(new PointerEvent("pointermove",
      { bubbles: true, clientX: point[0], clientY: point[1] }));
    const meshes = window.viewport.current.ghost({ target: { objectNames: [name] }, factor: 1, scaleAxis: null, affected: [] });
    const renders = window.hoverRenders;
    await new Promise(requestAnimationFrame);
    return { meshes, visible: window.hoverGroup.visible, hover: window.interaction.hover, late: window.hoverRenders - renders };
  }, hoverPoint);
  assert.deepEqual(ghostClear, { meshes: 1, visible: false, hover: null, late: 0 });
  await page.evaluate(() => window.viewport.current.ghost(null));
  await page.mouse.move(hoverPoint[0] + 0.1, hoverPoint[1]);
  await page.waitForFunction(() => window.hoverGroup.visible);
  // The actual snap path drives the endpoint marker and its incident edge.
  const endpoint = await page.evaluate(() => {
    const hit = window.interaction.hover;
    const box = window.objectBounds(hit.object);
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const point = window.projectPoint([x,y,z]);
      for (const dx of [-1, 0, 1]) for (const dy of [-1, 0, 1]) {
        const screen = [point[0] + dx, point[1] + dy];
        if (window.viewport.current.snapOnModel(...screen)?.kind === "endpoint") return screen;
      }
    }
    return null;
  });
  assert.ok(endpoint, "fixture supplies a visible endpoint");
  await page.mouse.move(...endpoint);
  await page.waitForFunction(() => window.hoverGroup?.children[2].visible && window.hoverGroup.children[3].visible);
  if (process.env.INTERACTION_SCREENSHOT_DIR) {
    await mkdir(process.env.INTERACTION_SCREENSHOT_DIR, { recursive: true });
    await page.screenshot({ path: path.join(process.env.INTERACTION_SCREENSHOT_DIR, "hover.png") });
  }
  // Click feedback is synchronous even when the consumer does no resolving.
  await page.mouse.click(...hoverPoint);
  const clicked = await page.evaluate(() => ({
    picks: window.picks.length, marked: window.picks.at(-1).object.material !== window.hoverMaterial,
    hover: window.hoverGroup.visible, phase: window.interaction.phase,
  }));
  assert.deepEqual(clicked, { picks: 1, marked: true, hover: false, phase: "inactive" });
  if (process.env.INTERACTION_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.INTERACTION_SCREENSHOT_DIR, "selected.png") });
  await page.mouse.move(hoverPoint[0] + 0.1, hoverPoint[1]);
  await page.waitForFunction(() => window.hoverGroup.visible);
  await page.keyboard.press("Escape");
  assert.equal(await page.evaluate(() => window.hoverGroup.visible), false);
  const cancelledHover = await page.evaluate(async (point) => {
    const canvas = document.querySelector("canvas");
    canvas.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: point[0], clientY: point[1] }));
    document.querySelector('button[aria-label="Rectangle"]').click();
    const renders = window.hoverRenders;
    await new Promise(requestAnimationFrame);
    return { late: window.hoverRenders - renders, visible: window.hoverGroup.visible, phase: window.interaction.phase };
  }, hoverPoint);
  assert.deepEqual(cancelledHover, { late: 0, visible: false, phase: "armed" });
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.mouse.move(hoverPoint[0] + 0.2, hoverPoint[1]);
  await page.waitForFunction(() => window.hoverGroup.visible);
  await page.evaluate(() => window.viewport.current.setLayerVisibility(0, false));
  assert.equal(await page.evaluate(() => window.hoverGroup.visible), false);
  await page.evaluate(() => window.viewport.current.setLayerVisibility(0, true));
  await page.mouse.move(hoverPoint[0] + 0.3, hoverPoint[1]);
  await page.waitForFunction(() => window.hoverGroup.visible);
  const replaced = await page.evaluate(async () => {
    const oldHit = window.interaction.hover, oldGroup = window.hoverGroup;
    const bytes = await (await fetch("/fixture.3dm")).blob();
    await window.viewport.current.openFile(new File([bytes], "replacement.3dm"));
    return { hover: window.interaction.hover, detached: oldGroup.parent === null, disposals: oldGroup.disposals,
      staleMark: window.viewport.current.highlight({ object: oldHit.object }) };
  });
  assert.deepEqual(replaced, { hover: null, detached: true, disposals: 8, staleMark: 0 });
  const draftLifecycle = await page.evaluate(async () => {
    const viewport = window.viewport.current, runtime = window.draftRuntime();
    const visible = runtime.model.getObjectByName("mass-0-0"), hidden = runtime.model.getObjectByName("mass-0-1");
    hidden.visible = false;
    const a = { elementId: "local-solid", spec: { profile: [[25,25],[27,25],[27,27],[25,27]], base: 10, height: 2 } };
    const b = { elementId: "local-line", spec: { profile: [[25,28],[27,28]], base: 12, height: 0, closed: false } };
    const draft = { objects: [a,b], hiddenObjectNames: [visible.name,hidden.name] };
    viewport.draftPreview(draft);
    const solid = runtime.draftObjects.get(a.elementId).object, line = runtime.draftObjects.get(b.elementId).object;
    const ids = [solid,...solid.children.flatMap(o=>[o,o.geometry,o.geometry.getAttribute("position")])];
    const materials = solid.children.map(o=>o.material);
    const marked = viewport.highlight({ draftElementId: a.elementId });
    viewport.highlight(null);
    const restoredMaterials = solid.children.every((o,i)=>o.material===materials[i]);
    viewport.draftPreview(structuredClone(draft));
    const same = ids.every((value,i)=>value===[solid,...solid.children.flatMap(o=>[o,o.geometry,o.geometry.getAttribute("position")])][i]);
    viewport.draftPreview({ ...draft, objects: [{...a,spec:{...a.spec,height:3}},b] });
    const updated = ids.every((value,i)=>value===[solid,...solid.children.flatMap(o=>[o,o.geometry,o.geometry.getAttribute("position")])][i]);
    let disposed = 0;
    for(const object of [solid,line]) for(const child of object.children) {
      child.geometry.addEventListener("dispose",()=>disposed++); child.material.addEventListener("dispose",()=>disposed++);
    }
    const hiddenDuring = [visible.visible,hidden.visible];
    viewport.draftPreview(null);
    const detached = !solid.parent && !line.parent;
    const sourceRestored = [visible.visible,hidden.visible]; hidden.visible=true;
    viewport.draftPreview(null);
    return { marked,restoredMaterials,same,updated,hiddenDuring,sourceRestored,detached,disposed,
      remaining:runtime.draftObjects.size, stale:viewport.highlight({object:solid}),
      lineSurface:line.children[1].visible };
  });
  assert.deepEqual(draftLifecycle, { marked:1,restoredMaterials:true,same:true,updated:true,hiddenDuring:[false,false],
    sourceRestored:[true,false],detached:true,disposed:12,remaining:0,stale:0,lineSurface:false });
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  const buildingAnchor = await page.evaluate(() => window.projectPoint([27, 27, 0]));
  const buildingCorner = await page.evaluate(() => window.projectPoint([31, 29, 0]));
  await page.mouse.click(...buildingAnchor);
  await page.mouse.move(...buildingCorner);
  await page.waitForFunction(() => window.previewGroup?.parent && window.previewGroup.visible);
  const performanceResult = await page.evaluate(async (point) => {
    const percentile = (values, fraction) => [...values].sort((a,b) => a-b)[Math.floor((values.length - 1) * fraction)];
    const summary = (values) => ({ p50: percentile(values, 0.5), p95: percentile(values, 0.95), max: Math.max(...values) });
    for (let warm = 0; warm < 10; warm++) {
      window.moveBurst([point]); await new Promise(requestAnimationFrame);
    }
    window.previewTimes = []; window.pointerTimes = [];
    const identity = window.previewIdentity();
    const updates = window.previewUpdates, renders = window.previewRenders;
    const frameIntervals = [], eventCosts = [];
    let previous = await new Promise(requestAnimationFrame);
    for (let frame = 0; frame < 120; frame++) {
      for (let event = 0; event < 4; event++) {
        const start = performance.now();
        window.moveBurst([[point[0] + Math.sin(frame / 15), point[1] + Math.cos(frame / 15)]]);
        eventCosts.push(performance.now() - start);
      }
      const now = await new Promise(requestAnimationFrame);
      frameIntervals.push(now - previous); previous = now;
    }
    return { scene: { meshes: 400, triangles: 4800 }, pointerMoves: 480,
      updates: window.previewUpdates - updates, renders: window.previewRenders - renders,
      reused: identity.every((item, index) => item === window.previewIdentity()[index]),
      frameIntervalMs: summary(frameIntervals), pointerToRenderReturnMs: summary(window.pointerTimes),
      pointerHandlerMs: summary(eventCosts), previewUpdateAndRenderMs: summary(window.previewTimes) };
  }, buildingCorner);
  assert.equal(performanceResult.updates, 120);
  assert.equal(performanceResult.renders, 120);
  assert.equal(performanceResult.reused, true);
  console.log("MEASURE headless Chrome / synthetic building array (render return is not GPU presentation):", JSON.stringify(performanceResult));

  // Hold the same visible corner across a hit/miss silhouette boundary.
  // A single isolated draft keeps this geometry assertion independent of the
  // density of the loaded building array and exercises the production snap API.
  const heldCorner = await page.evaluate(() => {
    const v = window.viewport.current;
    v.draftPreview({ objects: [{ elementId: "snap-corner", spec: {
      profile: [[-20,-20],[-4,-20],[-4,-4],[-20,-4]], base: 0, height: 2,
    } }], hiddenObjectNames: [] });
    v.standardView("top");
    const corner = window.projectPoint([-20,-20,2]);
    const sample = (dx,dy) => v.snapOnModel(corner[0]+dx,corner[1]+dy);
    const captured = sample(0.05,-0.05);
    const held = [-1,1,-2,2,-16].map(dx=>sample(dx,-0.05));
    const released = sample(-23,-2);
    const reacquired = sample(-16,-0.05);
    v.draftPreview(null);
    const removed = sample(2,-2);
    return { captured, held, released, reacquired, removed };
  });
  assert.equal(heldCorner.captured?.kind, "endpoint", JSON.stringify(heldCorner));
  assert.ok(heldCorner.held.every(s=>s?.kind==="endpoint" && s.point.join()===heldCorner.captured.point.join()),
    "corner must survive tiny moves across the mesh silhouette");
  assert.equal(heldCorner.released, null, "leaving the 21px release radius frees the cursor");
  assert.equal(heldCorner.reacquired, null, "a released target cannot reacquire off the model");
  assert.equal(heldCorner.removed, null, "removed drafts cannot retain a snap");
  const heldEdge = await page.evaluate(() => {
    const v = window.viewport.current;
    const draft = { objects: [{ elementId: "snap-edge", spec: {
      profile: [[-20,-20],[-4,-20]], base: 0, height: 0, closed: false,
    } }], hiddenObjectNames: [] };
    v.draftPreview(draft);
    const a = window.projectPoint([-20,-20,0]), b = window.projectPoint([-4,-20,0]);
    const sample = (t,dy=1) => v.snapOnModel(a[0]+(b[0]-a[0])*t,a[1]+dy);
    const start = sample(.25), slide = sample(.30,16), release = sample(.30,23);
    const noReacquire = sample(.30,16);
    const recaptured = sample(.30), midpoint = sample(.50,0), endpoint = sample(1,0);
    v.standardView("top");
    const cameraCleared = window.interaction.modelSnap;
    sample(.25);
    const feature = window.interaction.modelSnap.feature;
    feature.visible = false;
    const hidden = sample(.25);
    v.draftPreview(null);
    return { start,slide,release,noReacquire,recaptured,midpoint,endpoint,cameraCleared,hidden };
  });
  assert.equal(heldEdge.start?.kind, "edge", JSON.stringify(heldEdge));
  assert.equal(heldEdge.slide?.kind, "edge");
  assert.ok(heldEdge.slide.point[0] > heldEdge.start.point[0], "a retained edge must slide along the segment");
  assert.ok(Math.abs(heldEdge.slide.point[1]+20)<1e-6);
  assert.equal(heldEdge.release,null);
  assert.equal(heldEdge.noReacquire,null);
  assert.equal(heldEdge.midpoint?.kind,"midpoint");
  assert.equal(heldEdge.endpoint?.kind,"endpoint");
  assert.equal(heldEdge.cameraCleared,null);
  assert.equal(heldEdge.hidden,null);
  console.log("PASS corner/edge retention, release, sliding, midpoint/endpoint approach and camera/hidden/draft invalidation");

  // Unmount while a frame is pending releases once and never paints afterward.
  const unmounted = await page.evaluate(async () => {
    window.moveBurst([[600, 450]]);
    window.testRoot.unmount();
    const updates = window.previewUpdates;
    await new Promise(requestAnimationFrame);
    return { late: window.previewUpdates - updates, disposals: window.previewGroup.disposals, submissions: window.submitted.length };
  });
  assert.deepEqual(unmounted, { late: 0, disposals: 6, submissions: 6 });
  assert.deepEqual(errors, []);
  console.log("PASS local object/face/edge hover, immediate click feedback, hidden geometry and ghost cleanup; sketch buffer reuse, RAF coalescing, latest-ref completion, typed values, planes/axes, theme, cancellation/tool switch/unmount, rectangle/circle/polygon and measurement; zero API calls");
} catch (error) {
  if (page && !page.isClosed()) {
    if (process.env.SKETCH_SCREENSHOT) await page.screenshot({ path: process.env.SKETCH_SCREENSHOT });
    console.error(await page.evaluate(() => {
      if (!window.projectPoint) return { loaded: false };
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
