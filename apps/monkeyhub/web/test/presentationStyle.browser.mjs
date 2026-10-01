/**
 * #562: Modeling's 展示 (Presentation) style in the real ProjectWorkspace, against the project
 * fixture, on a pavilion written the way MonkeyCAD's preview writes declared materials (#560): one
 * native material per declared name with its declared colour, and a bench whose component declares
 * none (no material, its layer's distinction colour, `archflow:material_status = undeclared`).
 *
 * It switches to 展示 from More and reads the real WebGL canvas: a face square to the sun shows the
 * declared colour itself (the sRGB decoding of #562), the model's shadow falls on its plinth and on the
 * ground, the bench is hatched and the legend names what is shown. The axonometric preset turns the view
 * orthographic. Render's "Use current view as source" then keeps that still bound to the exact model
 * source with its camera, shadows included. Choosing Modeling again gives back the exact camera, lights,
 * shadow switch and materials of before. A reload opens in the style chosen last; Original shows the
 * declared colour lit, not the pale colour the loader made of it.
 *
 * SCREENSHOT_DIR=<dir> keeps the screenshots and the retained still there; without it they go to a
 * temporary directory, which is left in place.
 */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { createProjectWorkspaceFixture } from "./projectWorkspaceFixture.mjs";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const output = process.env.SCREENSHOT_DIR ? path.resolve(process.env.SCREENSHOT_DIR) : await mkdtemp(path.join(tmpdir(), "presentation-style-"));
await mkdir(output, { recursive: true });

/** The declared materials, as a facets proposal would declare them, sRGB. */
const MATERIALS = { plinth: [233, 231, 225], timber: [196, 160, 112], infill: [214, 204, 176], roof: [104, 112, 115], glazing: [150, 200, 225] };
const BENCH_LAYER = [111, 178, 96];
const PAPER = [247, 246, 242];

/** A closed box, each face its own four vertices so it is shaded flat, as an OCCT tessellation is. */
function solid(rhino, corners) {
  const mesh = new rhino.Mesh();
  let base = 0;
  for (const face of [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [3, 0, 4, 7], [1, 2, 6, 5]]) {
    for (const index of face) mesh.vertices().add(...corners[index]);
    mesh.faces().addQuadFace(base, base + 1, base + 2, base + 3);
    base += 4;
  }
  mesh.normals().computeNormals();
  return mesh;
}
const box = (rhino, [x0, y0, z0], [x1, y1, z1]) => solid(rhino,
  [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]]);

/** A plinth, four posts and two beams, an infill wall, a glazed panel, a roof falling to the front, and a bench. */
function pavilion(rhino, { projectId, runId }) {
  const model = new rhino.File3dm();
  const layer = (name, [r, g, b]) => {
    const row = new rhino.Layer();
    row.name = name;
    row.color = { r, g, b, a: 255 };
    return model.layers().add(row);
  };
  const materials = {};
  for (const [name, [r, g, b]] of Object.entries(MATERIALS)) {
    const material = new rhino.Material();
    material.name = name;
    material.diffuseColor = { r, g, b, a: 255 };
    if (name === "glazing") material.transparency = 0.6;
    material.setUserString("archflow:material_id", name);
    materials[name] = model.materials().add(material);
  }
  const parts = layer("pavilion", [128, 92, 160]), benches = layer("bench", BENCH_LAYER);
  const add = (name, mesh, material, layerIndex = parts) => {
    const attributes = new rhino.ObjectAttributes();
    attributes.name = name;
    attributes.layerIndex = layerIndex;
    attributes.setUserString("archflow:component", name.split("-")[0]);
    if (material) {
      attributes.materialSource = rhino.ObjectMaterialSource.MaterialFromObject;
      attributes.materialIndex = materials[material];
      attributes.setUserString("archflow:material", material);
    } else attributes.setUserString("archflow:material_status", "undeclared");
    model.objects().add(mesh, attributes);
  };
  add(`floor-${projectId}-${runId}`, box(rhino, [0, 0, 0], [7, 5, 0.3]), "plinth");
  for (const [x, y] of [[2.5, 1], [6.3, 1], [2.5, 3.8], [6.3, 3.8]]) add(`post-${x}-${y}`, box(rhino, [x, y, 0.3], [x + 0.2, y + 0.2, 3.4]), "timber");
  for (const y of [1, 3.8]) add(`beam-${y}`, box(rhino, [2.4, y, 3.1], [6.6, y + 0.2, 3.4]), "timber");
  add("infill-wall", box(rhino, [2.7, 3.85, 0.3], [6.3, 4.0, 3.1]), "infill");
  add("glazing-panel", box(rhino, [6.33, 1.2, 0.3], [6.37, 3.8, 3.1]), "glazing");
  const [x0, x1, y0, y1, low, high, thick] = [2.2, 6.9, 0.6, 4.4, 3.4, 4.0, 0.12];
  add("roof", solid(rhino, [[x0, y0, low], [x1, y0, low], [x1, y1, high], [x0, y1, high],
    [x0, y0, low + thick], [x1, y0, low + thick], [x1, y1, high + thick], [x0, y1, high + thick]]), "roof");
  add("bench", box(rhino, [0.8, 2.8, 0.3], [2.0, 3.4, 0.75]), null, benches);
  return model;
}

const runtime = { runtimeId: randomUUID(), projectId: "M", projectDir: "D:\\fixture\\M" };
const fixture = await createProjectWorkspaceFixture(new Map([[runtime.projectDir, runtime]]), [], { model: pavilion });
const http = createHttpServer(), errors = [], unexpected = [], retained = [];
let vite, browser;

try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error",
    cacheDir: path.join(output, "vite-cache"), publicDir: ".generated/public",
    plugins: [workspaceFixture(), {
      name: "presentation-probe", enforce: "pre",
      transform(source, id) {
        // The Hub's own tokens and base styles, so the page and its screenshots look as they do in the Hub.
        if (id.replaceAll("\\", "/").endsWith("/test/workspace-fixture.tsx")) {
          const base = path.resolve(webRoot, "../../../packages/web-shared/src/base.css").replaceAll("\\", "/");
          return { code: `import "/@fs/${base}";\n${source}`, map: null };
        }
        if (!id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) return;
        const marker = "  const pickAt = useCallback(";
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, "  (window as any).readRuntime = () => runtimeRef.current;\n" + marker), map: null };
      },
    }, react()],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { errors.push(`API escaped fixture: ${request.method} ${request.url}`); response.writeHead(405); response.end(); }
    else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : { channel: "chrome" }), args: ["--enable-unsafe-swiftshader", "--no-sandbox"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 }, locale: "en-US", deviceScaleFactor: 1 });
  await context.addInitScript(() => {
    class QuietEventSource { constructor() { this.readyState = 1; } addEventListener() {} removeEventListener() {} close() {} }
    window.EventSource = QuietEventSource;
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    const bound = new URL(`/api/runtime/projects/${runtime.runtimeId}/studio${url.pathname}${url.search}`, origin);
    if (route.request().method() === "POST" && url.pathname === "/api/render/views") retained.push(route.request().postDataJSON());
    try {
      // A write reaches the runtime with the Idempotency-Key the Hub's relay adds to it.
      const relayed = Object.create(route);
      relayed.request = () => { const request = route.request(), key = randomUUID();
        return Object.assign(Object.create(request), { headers: () => ({ "idempotency-key": key, ...request.headers() }) }); };
      if (await fixture.handle(relayed, bound)) return;
    } catch (error) { unexpected.push(`${route.request().method()} ${url.pathname}: ${error.message.split("\n")[0]}`); }
    await route.fulfill({ status: 404, json: { code: "FIXTURE_UNSERVED", detail: `${url.pathname} is not part of this fixture.` } });
  });

  const shown = () => page.waitForFunction(() => {
    const runtime = window.readRuntime?.();
    let found = false;
    runtime?.model?.traverse((object) => { if (object.name === "roof") found = true; });
    return found;
  }, null, { polling: "raf" });
  const moreMenu = page.locator('button[aria-controls="stage-more-menu"]');
  const chooseStyle = async (style) => {
    if (await moreMenu.getAttribute("aria-expanded") !== "true") await moreMenu.click();
    await page.locator("#stage-more-menu select[data-display-style]").selectOption(style);
    await moreMenu.click();
    await page.locator("#stage-more-menu").waitFor({ state: "detached" });
    await settle();
  };
  const settle = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  /** The live canvas at world points: the mean colour of a 3×3 patch around each, as the person sees it. */
  const sample = (points) => page.evaluate((points) => {
    const runtime = window.readRuntime(), canvas = runtime.renderer.domElement;
    runtime.render();
    const copy = document.createElement("canvas");
    copy.width = canvas.width; copy.height = canvas.height;
    const context = copy.getContext("2d");
    context.drawImage(canvas, 0, 0);
    return points.map(([x, y, z]) => {
      const ndc = runtime.camera.position.clone().set(x, y, z).project(runtime.camera);
      const px = Math.round((ndc.x + 1) / 2 * canvas.width), py = Math.round((1 - ndc.y) / 2 * canvas.height);
      const data = context.getImageData(px - 1, py - 1, 3, 3).data, mean = [0, 0, 0];
      for (let index = 0; index < data.length; index += 4) for (let channel = 0; channel < 3; channel++) mean[channel] += data[index + channel] / 9;
      return { ndc: [ndc.x, ndc.y], rgb: mean.map(Math.round) };
    });
  }, points);
  /** Light and dark along a short horizontal run through a point: a hatch has both. */
  const run = (point) => page.evaluate(([x, y, z]) => {
    const runtime = window.readRuntime(), canvas = runtime.renderer.domElement;
    runtime.render();
    const copy = document.createElement("canvas");
    copy.width = canvas.width; copy.height = canvas.height;
    const context = copy.getContext("2d");
    context.drawImage(canvas, 0, 0);
    const ndc = runtime.camera.position.clone().set(x, y, z).project(runtime.camera);
    const px = Math.round((ndc.x + 1) / 2 * canvas.width), py = Math.round((1 - ndc.y) / 2 * canvas.height);
    const data = context.getImageData(px - 10, py, 21, 1).data, light = [];
    for (let index = 0; index < data.length; index += 4) light.push(data[index] + data[index + 1] + data[index + 2]);
    return light;
  }, point);
  /** A PNG's pixels at normalised device coordinates: the mean of a 3×3 patch around each. */
  const pixelsOf = (png, points) => page.evaluate(async ({ base64, points }) => {
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const image = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = document.createElement("canvas");
    canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    return points.map(([x, y]) => {
      const px = Math.round((x + 1) / 2 * image.width), py = Math.round((1 - y) / 2 * image.height);
      const data = context.getImageData(px - 1, py - 1, 3, 3).data, mean = [0, 0, 0];
      for (let index = 0; index < data.length; index += 4) for (let channel = 0; channel < 3; channel++) mean[channel] += data[index + channel] / 9;
      return mean.map(Math.round);
    });
  }, { base64: png.toString("base64"), points });
  const close = (actual, expected, tolerance, label) =>
    assert.ok(actual.every((value, index) => Math.abs(value - expected[index]) <= tolerance), `${label}: ${actual} against ${expected}`);
  const darker = (rgb, than, by, label) => {
    const sum = (values) => values.reduce((total, value) => total + value, 0);
    assert.ok(sum(rgb) <= sum(than) * (1 - by), `${label}: ${rgb} is not ${by * 100}% darker than ${than}`);
  };
  const state = () => page.evaluate(() => {
    const runtime = window.readRuntime();
    const pose = (camera) => ({ position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), up: camera.up.toArray(),
      zoom: camera.zoom, near: camera.near, far: camera.far, ...(camera.isPerspectiveCamera ? { fov: camera.fov } : {}) });
    const worn = {};
    runtime.model.traverse((object) => {
      if (object.isMesh) worn[object.name] = { style: object.material.userData.displayStyle ?? "file", type: object.material.type,
        hatched: object.material.isUndeclaredSurface === true, colour: object.material.color?.getHexString("srgb"),
        casts: object.castShadow, receives: object.receiveShadow };
    });
    return {
      style: runtime.style, projection: runtime.camera.isOrthographicCamera ? "orthographic" : "perspective",
      target: runtime.controls.target.toArray(), perspective: pose(runtime.perspectiveCamera), orthographic: pose(runtime.orthographicCamera),
      lights: { native: runtime.nativeLights.visible, modeling: runtime.modelingLights.visible, presentation: runtime.presentationStage.visible },
      shadows: runtime.renderer.shadowMap.enabled, ground: runtime.shadowGround.visible, grid: runtime.grid.visible,
      background: runtime.background.getHexString("srgb"), worn,
    };
  });

  await page.goto(`${origin}/?lang=en`);
  await shown();
  await settle();
  const modeling = await state();
  assert.equal(modeling.style, "modeling", "Modeling is the default");
  assert.equal(modeling.projection, "perspective");
  assert.equal(modeling.shadows, false);
  assert.equal(modeling.worn.roof.style, "modeling");
  await page.screenshot({ path: path.join(output, "modeling.png") });

  // 展示 from More: the declared colours, matte and lit by one sun with its shadows; the bench hatched; a legend.
  await chooseStyle("presentation");
  const presenting = await state();
  assert.deepEqual([presenting.style, presenting.shadows, presenting.ground, presenting.grid, presenting.background],
    ["presentation", true, true, false, "f7f6f2"], "paper, the sun's shadow and its ground");
  assert.deepEqual(presenting.lights, { native: false, modeling: false, presentation: true });
  for (const [name, colour] of [["roof", "687073"], ["infill-wall", "d6ccb0"], ["floor-M-home-M", "e9e7e1"], ["glazing-panel", "96c8e1"]]) {
    assert.equal(presenting.worn[name].colour, colour, `${name} wears its declared colour`);
    assert.equal(presenting.worn[name].type, "MeshLambertMaterial", `${name} is matte`);
    assert.equal(presenting.worn[name].hatched, false);
  }
  assert.deepEqual([presenting.worn.bench.hatched, presenting.worn.bench.colour], [true, "6fb260"],
    "the bench declares no material: its distinction colour, hatched");
  assert.equal(presenting.worn["glazing-panel"].casts, false, "glass casts no shadow");
  assert.equal(presenting.worn.roof.casts, true);
  assert.deepEqual(presenting.perspective, modeling.perspective, "choosing the style does not move the view");
  const legend = page.locator("[data-presentation-legend]");
  assert.deepEqual(await legend.locator("li[data-material]").evaluateAll((items) => items.map((item) => item.getAttribute("data-material"))),
    ["glazing", "infill", "plinth", "roof", "timber"]);
  assert.equal(await legend.locator("li[data-undeclared]").textContent(), "No declared material · 1");
  assert.equal(await legend.getAttribute("open"), null, "the legend starts folded, so the picture stays clear");
  assert.match(await legend.locator("summary").textContent(), /^Materials · 5\s*1$/, "folded, it still counts the hatched objects");
  assert.equal(await legend.locator("summary [title]").getAttribute("title"), "No declared material · 1");
  await legend.locator("summary").click();
  await page.screenshot({ path: path.join(output, "presentation-legend.png") });
  await legend.locator("summary").click();
  assert.equal(await page.locator("#stage-more-menu").count(), 0);
  await moreMenu.click();
  assert.match(await page.locator("#stage-more-menu label.stage-menu__field").getAttribute("title"), /declare.*shadows.*hatched/s,
    "the selector says what Presentation shows");
  await moreMenu.click();

  // The axonometric preset: orthographic, the whole model framed.
  await page.locator('[data-presentation-view="axonometric"]').click();
  await settle();
  const axonometric = await state();
  assert.equal(axonometric.projection, "orthographic");
  await page.screenshot({ path: path.join(output, "presentation-axonometric.png") });
  const [litPlinth, roofTop, underRoof, shadowedGround, openPaper] = await sample([
    [0.8, 0.8, 0.3], [4.55, 2.5, 3.82], [4.5, 2.4, 0.3], [7.45, 5.6, 0], [-1.5, -1.5, 0]]);
  close(litPlinth.rgb, MATERIALS.plinth, 3, "a plinth face square to the sun shows the declared colour itself");
  close(roofTop.rgb, MATERIALS.roof, 6, "the roof, nearly square to the sun, its declared colour");
  darker(underRoof.rgb, litPlinth.rgb, 0.12, "the roof's shadow on the plinth");
  darker(shadowedGround.rgb, PAPER, 0.08, "the model's shadow on the ground");
  close(openPaper.rgb, PAPER, 2, "open paper is the paper");
  const hatch = await run([1.4, 3.1, 0.75]);
  assert.ok(Math.max(...hatch) - Math.min(...hatch) > 0.1 * Math.max(...hatch), `the bench's hatch has lines: ${hatch}`);
  for (const view of ["front", "side"]) {
    await page.locator(`[data-presentation-view="${view}"]`).click();
    await settle();
    assert.equal((await state()).projection, "orthographic", view);
    await page.screenshot({ path: path.join(output, `presentation-${view}.png`) });
  }
  await page.locator('[data-presentation-view="axonometric"]').click();
  await settle();

  // Render keeps the view as a still bound to the exact model source and its camera, shadows included.
  const home = [...fixture.projects.get("M").assets.values()].map((row) => row.dto).find((dto) => dto.runId === "home-M");
  await page.evaluate(() => window.__workspaceFixture.setWorkspace("render"));
  const capture = page.getByRole("button", { name: "Use current view as source", exact: true });
  await capture.waitFor();
  try {
    await page.waitForFunction(() => [...document.querySelectorAll("button")].some((button) =>
      button.textContent === "Use current view as source" && !button.disabled));
  } catch (error) {
    await page.screenshot({ path: path.join(output, "render-preview-failure.png") });
    throw new Error(`${error.message}: ${await page.locator(".render-model-preview").innerText()}`);
  }
  // Render's live preview draws the borrowed scene as Modeling shows it: the declared colour, the sun's shadow, the paper.
  await page.waitForFunction(() => (document.querySelector(".render-model-canvas canvas")?.width ?? 0) > 100);
  await new Promise((resolve) => setTimeout(resolve, 300));
  await page.screenshot({ path: path.join(output, "render-preview.png") });
  const preview = await pixelsOf(await page.locator(".render-model-canvas canvas").screenshot(), [litPlinth.ndc, shadowedGround.ndc, openPaper.ndc]);
  close(preview[0], MATERIALS.plinth, 4, "the preview shows the declared colour");
  darker(preview[1], PAPER, 0.08, "the preview shows the model's shadow");
  close(preview[2], PAPER, 3, "the preview's paper");
  await capture.click();
  const source = page.getByRole("combobox", { name: "Source image", exact: true });
  for (const deadline = Date.now() + 30_000; !(await source.inputValue()); ) {
    assert.ok(Date.now() < deadline, "the kept still becomes Render's source");
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.equal(retained.length, 1, "one still is kept");
  const [still] = retained;
  assert.deepEqual(still.modelSource, home.modelSource, "bound to the exact model source shown");
  assert.equal(still.camera.projection, "orthographic");
  assert.deepEqual(still.camera.target.map((value) => Number(value.toFixed(9))), axonometric.target.map((value) => Number(value.toFixed(9))),
    "with the camera it was drawn through, for the saved-view actions (#544)");
  assert.equal(Math.max(...still.screenSize), 2048);
  const stillPng = Buffer.from(still.pngBase64, "base64");
  await writeFile(path.join(output, "retained-still.png"), stillPng);
  const document = fixture.documents.get("M").at(-1).dto;
  assert.deepEqual([document.modelSource, document.viewRecipe.kind], [home.modelSource, "model-view"], "Render lists it as a model view of that source");
  const pixels = await pixelsOf(stillPng, [litPlinth.ndc, underRoof.ndc, shadowedGround.ndc, openPaper.ndc]);
  close(pixels[0], MATERIALS.plinth, 3, "the still shows the declared colour");
  darker(pixels[1], pixels[0], 0.12, "the still keeps the shadow on the plinth");
  darker(pixels[2], PAPER, 0.08, "the still keeps the shadow on the ground");
  close(pixels[3], PAPER, 2, "the still's paper");

  // Back in Modeling, choosing Modeling gives back exactly the camera, light, shadow switch and materials of before.
  await page.evaluate(() => window.__workspaceFixture.setWorkspace("arch"));
  await shown();
  await chooseStyle("modeling");
  const restored = await state();
  assert.deepEqual(restored, modeling, "Modeling as it was, to the last camera value");
  assert.equal(await page.locator("[data-presentation]").count(), 0, "no presets or legend outside Presentation");

  // A refreshed page opens in the style chosen last.
  await chooseStyle("presentation");
  await page.reload();
  await shown();
  await settle();
  assert.equal((await state()).style, "presentation", "a reload keeps 展示");
  await moreMenu.click();
  assert.equal(await page.locator("#stage-more-menu select[data-display-style]").inputValue(), "presentation");
  await moreMenu.click();
  await page.locator("[data-presentation-legend]").waitFor();

  // Original: the file's own look, lit by its own lights, in the colour the file saved rather than the pale one the loader made.
  await chooseStyle("original");
  const original = await state();
  assert.equal(original.worn.roof.style, "file");
  assert.equal(original.worn.roof.colour, "687073", "the file's material, decoded");
  assert.deepEqual([original.shadows, original.ground, original.lights.presentation], [false, false, false]);
  await page.locator('button[aria-controls="stage-more-menu"]').click();
  await page.locator("#stage-more-menu").getByRole("button", { name: "Isometric", exact: true }).click();
  await moreMenu.click();
  await settle();
  const [originalPlinth] = await sample([[0.8, 0.8, 0.3]]);
  assert.ok(originalPlinth.rgb.every((value, index) => value <= MATERIALS.plinth[index] + 12),
    `Original lights the plinth's colour, not a paler one: ${originalPlinth.rgb}`);
  await page.screenshot({ path: path.join(output, "original-isometric.png") });
  await chooseStyle("modeling");
  await page.screenshot({ path: path.join(output, "modeling-isometric.png") });
  await chooseStyle("presentation");
  await page.screenshot({ path: path.join(output, "presentation-isometric.png") });

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ screenshots: output, unserved: [...new Set(unexpected)], litPlinth: litPlinth.rgb, roofTop: roofTop.rgb,
    underRoof: underRoof.rgb, shadowedGround: shadowedGround.rgb, previewPixels: preview, stillPixels: pixels, originalPlinth: originalPlinth.rgb }));
  console.log("Presentation: declared colours, shadows, hatch, presets, a still bound to its model source, exact restore and reload PASS");
} finally {
  await browser?.close(); await vite?.close(); await new Promise((resolve) => http.close(resolve));
}
