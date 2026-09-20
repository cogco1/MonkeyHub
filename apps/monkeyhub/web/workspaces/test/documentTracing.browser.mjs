/** Correct an original image in the Board's existing page editor, then continue its real 3D candidate.
 * The PNG is authored below solely for this test; no external image, agent or private project is used.
 * Board/document navigation's broader refusal and unsynced-draft cases also run in boardDocumentOpen.browser.mjs.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.resolve(webRoot, "../../../archflow-studio/api");
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1",
  PYTHONPATH: [repoRoot, apiRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
const root = await mkdtemp(path.join(tmpdir(), "monkeyboard-document-tracing-"));
const projectDir = path.join(root, "demo-project");
const http = createHttpServer(), requests = [], errors = [];
const faults = { save: false, proposal: false, candidate: false };
let api, vite, browser, page, apiLog = "", closing = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const activeImages = board => board.elements.filter(element => !element.isDeleted && element.type === "image");
const near = (actual, expected, message, tolerance = 1e-6) => assert.ok(Math.abs(actual - expected) <= tolerance,
  `${message}: ${actual} != ${expected}`);
async function until(read, predicate, message, attempts = 160) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = await read(); if (predicate(last)) return last;
    await delay(100);
  }
  assert.fail(`${message}: ${JSON.stringify(last)}`);
}

try {
  const fixture = spawnSync(python, ["-c", `
import base64, io, json, sys
from pathlib import Path
from PIL import Image, ImageDraw
import archflow
from tests.support import make_empty_project
assert Path(archflow.__file__).resolve() == Path(sys.argv[2], "archflow/__init__.py").resolve()
make_empty_project(Path(sys.argv[1]))
# Original six-corner concept sketch, with an intentionally irregular stroke.
image = Image.new("RGB", (800, 600), "#fffdf6")
draw = ImageDraw.Draw(image)
points = [(120, 480), (600, 360), (512, 150), (320, 150), (320, 258), (160, 210)]
draw.line(points + [points[0]], fill="#333333", width=4, joint="curve")
draw.line([(x+2, y-2) for x,y in points] + [(122,478)], fill="#888077", width=1)
draw.line([(170,445),(350,400)], fill="#666666", width=2)
draw.text((270,480), "6 m baseline / original test sketch", fill="#555555")
data = io.BytesIO(); image.save(data, "PNG")
print(json.dumps({"png": base64.b64encode(data.getvalue()).decode()}))
`, root, repoRoot], { cwd: apiRoot, env: pythonEnv, encoding: "utf8" });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const { png } = JSON.parse(fixture.stdout.trim());
  const headBefore = await readFile(path.join(projectDir, "HEAD"), "utf8");
  const apiPort = await new Promise(resolve => {
    const probe = createHttpServer(); probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address(); probe.close(() => resolve(port));
    });
  });
  api = spawn(python, ["-m", "archflow_studio_api.main", "--port", String(apiPort), "--project-dir", projectDir], {
    cwd: apiRoot, env: { ...pythonEnv, ARCHFLOW_STUDIO_CAD_EXPORT: "occt", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  api.on("error", error => errors.push(error.message));
  api.stdout.on("data", chunk => { apiLog = (apiLog + chunk).slice(-8000); });
  api.stderr.on("data", chunk => { apiLog = (apiLog + chunk).slice(-8000); });
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  await until(async () => {
    assert.equal(api.exitCode, null, apiLog);
    return fetch(`${apiOrigin}/api/health`).then(response => response.ok).catch(() => false);
  }, Boolean, "Runtime startup failed");
  async function call(method, route, body) {
    const response = await fetch(apiOrigin + route, { method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const result = await response.json();
    assert.ok(response.ok, `${method} ${route}: ${response.status} ${JSON.stringify(result)}`);
    return result;
  }
  const project = await call("GET", "/api/project");
  assert.equal(path.resolve(project.projectDir), path.resolve(projectDir));
  const document = await call("POST", "/api/documents", { projectId: project.projectId, runId: null,
    fileName: "original-concept.png", mimeType: "image/png", contentBase64: png });
  assert.equal(document.pageCount, 1);
  const source = { runId: document.runId, assetSha256: document.assetSha256,
    drawingRevisionRef: document.revisionRef ?? null, pageIndex: 0 };
  const annotationRoute = "/api/document-annotations?" + new URLSearchParams({ runId: source.runId,
    assetSha256: source.assetSha256, pageIndex: "0", ...(source.drawingRevisionRef ? { drawingRevisionRef: source.drawingRevisionRef } : {}) });
  const annotations = () => call("GET", annotationRoute);
  const board = () => call("GET", "/api/board");

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error",
    publicDir: "../.generated/public", cacheDir: path.join(root, "vite-cache"),
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [react(), workspaceFixture(), {
      name: "document-tracing-host-appearance",
      // The production Hub supplies these shared tokens to its embedded workspace.
      transform(code, id) {
        if (id.replaceAll("\\", "/").endsWith("/test/workspace-fixture.tsx"))
          return `import "../../../../shared-web/src/base.css";\n${code}`;
      },
    }],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const entry = { method: request.method, path: request.url.split("?")[0], query: request.url };
    requests.push(entry);
    const failed = (entry.method === "PUT" && entry.path === "/api/document-annotations" && faults.save)
      || (entry.method === "POST" && entry.path === "/api/proposals/sketch" && faults.proposal)
      || (entry.method === "POST" && /^\/api\/proposals\/[^/]+\/candidate$/.test(entry.path) && faults.candidate);
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => { if (chunks.length) try { entry.body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { /* source bytes */ } });
    if (failed) {
      request.resume(); entry.status = 503;
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify({ code: "TRANSPORT_ERROR", detail: `Injected ${faults.save ? "save" : faults.proposal ? "proposal" : "candidate"} failure.` }));
      return;
    }
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, answer => {
      entry.status = answer.statusCode;
      if (answer.headers["content-type"]?.includes("application/json")) {
        const output = []; answer.on("data", chunk => output.push(chunk));
        answer.on("end", () => { try { entry.result = JSON.parse(Buffer.concat(output).toString("utf8")); } catch { /* streaming */ } });
      }
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    proxied.on("error", error => { if (!closing) errors.push(error.message); if (!response.headersSent) response.writeHead(502); response.end(); });
    request.pipe(proxied);
  });
  await new Promise(resolve => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: "en-US" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route(url => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, route => {
    errors.push(`External request: ${route.request().url()}`); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  page.on("pageerror", error => errors.push(error.message));
  const toolbar = () => page.getByRole("toolbar", { name: "Page annotation tools", exact: true });
  const tracing = () => page.getByRole("region", { name: "Trace to 3D", exact: true });
  const generate = () => page.getByRole("button", { name: "Generate 3D candidate", exact: true });
  const saved = async () => { await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor(); return annotations(); };
  const point = async ([u, v]) => { const box = await page.locator(".document-page").boundingBox(); return [box.x + u * box.width, box.y + v * box.height]; };
  async function line(from, to) {
    await toolbar().getByRole("button", { name: "Line", exact: true }).click();
    await page.mouse.move(...await point(from)); await page.mouse.down();
    await page.mouse.move(...await point(to), { steps: 8 }); await page.mouse.up();
  }
  async function drag(from, to) {
    await toolbar().getByRole("button", { name: "Edit line", exact: true }).click();
    await page.mouse.move(...await point(from)); await page.mouse.down();
    await page.mouse.move(...await point(to), { steps: 12 }); await page.mouse.up();
  }
  async function editor() {
    await page.locator(".document-viewport[data-ready=true]").waitFor();
    assert.equal(await page.getByLabel("Source document", { exact: true }).inputValue(), source.assetSha256);
    assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "0");
    await tracing().waitFor();
    assert.match(await tracing().innerText(), /No image model has interpreted this page/);
  }

  await page.goto(`${origin}/?view=board&embedded=tool&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  const placed = await until(board, value => activeImages(value).length === 1, "Image not registered on Board");
  const boardBox = await page.locator(".monkeyboard-canvas").boundingBox();
  let centre;
  for (const across of [0.5, 0.42, 0.58, 0.34, 0.66]) {
    centre = [boardBox.x + boardBox.width * across, boardBox.y + boardBox.height / 2];
    await page.mouse.click(...centre);
    if (/original-concept\.png/.test(await page.locator(".monkeyboard-footer").innerText())) break;
  }
  await page.locator(".monkeyboard-context").waitFor();
  const zoom = await page.locator(".excalidraw .reset-zoom-button").innerText();
  await page.evaluate(() => { window.__tracingBoardCanvas = document.querySelector(".monkeyboard-canvas canvas"); });
  await page.mouse.dblclick(...centre); await editor();
  console.log("tracing: original PNG opened from Board");
  await line([0.15, 0.8], [0.75, 0.6]);
  await toolbar().getByRole("button", { name: "Outline", exact: true }).click();
  for (const vertex of [[0.15, 0.8], [0.75, 0.6], [0.64, 0.25], [0.4, 0.25], [0.4, 0.43], [0.2, 0.35]])
    await page.mouse.click(...await point(vertex));
  await toolbar().getByRole("button", { name: "Close outline", exact: true }).click();
  const initial = await saved();
  assert.equal(initial.annotations.length, 2);
  const outlineId = initial.annotations.find(mark => mark.closed).id;
  const outline = initial.annotations.find(mark => mark.id === outlineId);
  assert.equal(outline.kind, "polyline"); assert.equal(outline.points.length, 6);
  assert.notDeepEqual(outline.points[0], outline.points.at(-1));
  await drag(outline.points[2], [0.68, 0.22]);
  const moved = await saved();
  assert.notDeepEqual(moved.annotations.find(mark => mark.id === outlineId).points, outline.points);
  await toolbar().getByRole("button", { name: "Undo", exact: true }).click();
  assert.deepEqual((await saved()).annotations.find(mark => mark.id === outlineId), outline, "Undo restores corrected path identity and all vertices");
  await toolbar().getByRole("button", { name: "Redo", exact: true }).click();
  assert.deepEqual((await saved()).annotations, moved.annotations);
  await page.getByRole("combobox", { name: /^Scale and direction line/ }).selectOption({ label: "Line 1" });
  await page.getByLabel("Known length (m)", { exact: true }).fill("6");
  await page.getByRole("button", { name: "Set scale and direction", exact: true }).click();
  await page.getByLabel("Mass height (m)", { exact: true }).fill("2.4");
  const calibrated = await saved();
  assert.equal(calibrated.tracingCalibration.distance, 6);
  assert.deepEqual(calibrated.tracingCalibration.origin, calibrated.annotations[0].points[0]);
  assert.deepEqual(calibrated.tracingCalibration.axisPoint, calibrated.annotations[0].points[1]);
  await page.getByRole("checkbox", { name: "Outline 2 · mass", exact: true }).check();

  // Saved control points and calibration survive leaving and a fresh document-editor mount.
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  await page.locator(".monkeyboard-context").waitFor();
  assert.equal(await page.locator(".excalidraw .reset-zoom-button").innerText(), zoom);
  assert.match(await page.locator(".monkeyboard-footer").innerText(), /original-concept\.png/);
  assert.equal(await page.evaluate(() => window.__tracingBoardCanvas === document.querySelector(".monkeyboard-canvas canvas")), true);
  assert.deepEqual(activeImages(await board()).map(row => [row.id, row.x, row.y, row.customData]),
    activeImages(placed).map(row => [row.id, row.x, row.y, row.customData]));
  await page.locator(".monkeyboard-context button:not([disabled])").filter({ hasText: /^Edit this page$/ }).click();
  await editor();
  assert.deepEqual(await annotations(), calibrated);
  await page.locator(".document-calibration").waitFor();
  await page.getByRole("checkbox", { name: "Outline 2 · mass", exact: true }).check();
  await page.getByLabel("Mass height (m)", { exact: true }).fill("2.4");

  console.log("tracing: corrected vectors and calibration saved and reopened");
  const proposalPosts = () => requests.filter(row => row.method === "POST" && row.path === "/api/proposals/sketch");
  const candidatePosts = () => requests.filter(row => row.method === "POST" && /^\/api\/proposals\/[^/]+\/candidate$/.test(row.path));
  async function submitCandidate() {
    const count = candidatePosts().length;
    await generate().click();
    const post = await until(() => candidatePosts().at(count), row => row?.result?.jobId,
      "The document never submitted a candidate", 300);
    const job = await until(() => call("GET", `/api/jobs/${post.result.jobId}`), row => ["succeeded", "failed"].includes(row.status),
      "Candidate did not complete", 1800);
    assert.equal(job.status, "succeeded", JSON.stringify(job));
    return { job, proposal: proposalPosts().find(row => row.result?.proposalId === job.proposalId).result,
      state: await call("GET", `/api/state?run=${job.candidateId}`), candidate: await call("GET", `/api/candidates/${job.candidateId}`) };
  }
  const first = await submitCandidate();
  console.log("tracing: first OCCT candidate succeeded");
  const [entity] = first.proposal.change.edits.entities;
  const corrected = calibrated.annotations.find(mark => mark.id === outlineId);
  assert.deepEqual({ drawingRevisionRef: null, ...entity.fields.sourceDocumentTrace }, { ...source, revisionSha256: calibrated.revisionSha256,
    annotationId: outlineId, calibration: calibrated.tracingCalibration });
  assert.equal(entity.fields.producer, "prism");
  const profile = entity.fields.params.profile;
  const { origin: originPoint, axisPoint, distance } = calibrated.tracingCalibration;
  const axis = [(axisPoint[0] - originPoint[0]) * 800, -(axisPoint[1] - originPoint[1]) * 600];
  const squared = axis[0] ** 2 + axis[1] ** 2;
  corrected.points.forEach(([u, v], index) => {
    const delta = [(u - originPoint[0]) * 800, -(v - originPoint[1]) * 600];
    near(profile[index][0], (delta[0] * axis[0] + delta[1] * axis[1]) * distance / squared, `World X corner ${index}`);
    near(profile[index][1], (-delta[0] * axis[1] + delta[1] * axis[0]) * distance / squared, `World Y corner ${index}`);
  });
  near(profile[1][0], 6, "Baseline defines six metres"); near(profile[1][1], 0, "Baseline points toward +X");
  const stateElement = first.state.elements.find(row => row.elementId === entity.entity_id);
  assert.ok(stateElement.drawnShape, "The candidate retains its editable profile, placement and height");
  assert.deepEqual(stateElement.drawnShape.profile, profile);
  near(stateElement.drawnShape.height, 2.4, "Retained editable height");
  const model = first.candidate.artifacts.find(row => row.format === "3dm" && row.available);
  assert.ok(model, JSON.stringify(first.candidate.artifacts));
  assert.ok(first.candidate.artifacts.some(row => row.format === "step" && row.available && row.representation === "exact"));
  const modelPath = path.join(root, "candidate.3dm");
  const bytes = await fetch(`${apiOrigin}/api/artifacts/${model.sha256}/bytes?runId=${first.job.candidateId}`);
  assert.ok(bytes.ok); await writeFile(modelPath, new Uint8Array(await bytes.arrayBuffer()));
  const inspection = spawnSync(python, ["-c", `
import json, sys, rhino3dm
model = rhino3dm.File3dm.Read(sys.argv[1])
objects = []
for obj in model.Objects:
    if obj.Attributes.Name == "obj-" + sys.argv[2]:
        box = obj.Geometry.GetBoundingBox()
        objects.append({"min": [box.Min.X,box.Min.Y,box.Min.Z], "max": [box.Max.X,box.Max.Y,box.Max.Z]})
print(json.dumps({"unit": str(model.Settings.ModelUnitSystem), "objects": objects}))
`, modelPath, entity.entity_id], { cwd: apiRoot, env: pythonEnv, encoding: "utf8" });
  assert.equal(inspection.status, 0, inspection.stderr || inspection.stdout);
  const geometry = JSON.parse(inspection.stdout.trim());
  assert.match(geometry.unit, /Meters/); assert.ok(geometry.objects.length > 0, `3DM has no traced object: ${inspection.stdout}`);
  const low = [0, 1, 2].map(axis => Math.min(...geometry.objects.map(box => box.min[axis])));
  const high = [0, 1, 2].map(axis => Math.max(...geometry.objects.map(box => box.max[axis])));
  for (const dimension of [0, 1]) {
    near(low[dimension], Math.min(...profile.map(point => point[dimension])), `3DM min ${dimension}`, 1e-5);
    near(high[dimension], Math.max(...profile.map(point => point[dimension])), `3DM max ${dimension}`, 1e-5);
  }
  near(high[2] - low[2], 2.4, "3DM height", 1e-5);

  // Candidate preview completes behind the same page, so continuing binds the next run to it.
  await until(() => requests, rows => rows.some(row => row.method === "GET" && row.path === "/api/state"
    && row.query.includes(first.job.candidateId) && row.status === 200), "The model never adopted its completed candidate", 300);
  await until(() => generate().isEnabled(), Boolean, "The editor stayed busy after the candidate completed");
  await editor();
  const pageIdentity = await page.locator(".document-viewport").getAttribute("aria-label");
  const strokePath = () => page.locator(`.document-page__ink [data-stroke-id="${outlineId}"]`).getAttribute("d");
  const retainedBeforeFailure = await annotations();
  faults.save = true;
  await drag(corrected.points[2], [0.70, 0.20]);
  await page.locator(".document-error").waitFor();
  const localPath = await strokePath(), proposalsBeforeSaveFailure = proposalPosts().length;
  await generate().click();
  await tracing().getByRole("alert").filter({ hasText: "Injected save failure" }).waitFor();
  assert.equal(proposalPosts().length, proposalsBeforeSaveFailure, "Failed ink save cannot create a proposal");
  assert.deepEqual(await annotations(), retainedBeforeFailure);
  assert.equal(await strokePath(), localPath, "Unsaved vertex correction remains visible after save refusal");
  assert.equal(await page.locator(".document-viewport").getAttribute("aria-label"), pageIdentity);
  faults.save = false;
  await page.getByRole("button", { name: "Save page", exact: true }).click();
  const secondPage = await saved();
  assert.notEqual(secondPage.revisionSha256, calibrated.revisionSha256);
  assert.deepEqual(secondPage.tracingCalibration, calibrated.tracingCalibration);

  for (const boundary of ["proposal", "candidate"]) {
    faults[boundary] = true;
    const candidatesBefore = candidatePosts().length;
    await generate().click();
    await tracing().getByRole("alert").filter({ hasText: `Injected ${boundary} failure` }).waitFor();
    if (boundary === "proposal") assert.equal(candidatePosts().length, candidatesBefore, "A refused proposal never starts a candidate");
    assert.equal(await strokePath(), localPath, `${boundary} refusal preserves the corrected path`);
    assert.deepEqual(await annotations(), secondPage, `${boundary} refusal preserves the saved revision and calibration`);
    assert.equal(await page.locator(".document-viewport").getAttribute("aria-label"), pageIdentity);
    assert.equal(await readFile(path.join(projectDir, "HEAD"), "utf8"), headBefore);
    faults[boundary] = false;
  }
  console.log("tracing: save, proposal and candidate refusals preserve the page and HEAD");
  await page.getByRole("checkbox", { name: "Line 1 · model curve", exact: true }).check();
  await page.getByLabel("Mass height (m)", { exact: true }).fill("3.1");
  const second = await submitCandidate();
  assert.equal(second.proposal.sourceRunId, first.job.candidateId, "Regeneration continues the viewed candidate");
  const updatedEntity = second.proposal.change.edits.entities.find(row => row.entity_id === entity.entity_id);
  assert.ok(updatedEntity, "Updating this outline must keep its stable editable element ID");
  assert.equal(updatedEntity.fields.sourceDocumentTrace.revisionSha256, secondPage.revisionSha256);
  assert.deepEqual(updatedEntity.fields.sourceDocumentTrace.calibration, calibrated.tracingCalibration);
  assert.equal(second.state.elements.filter(row => row.elementId === entity.entity_id).length, 1, "Regeneration updates, rather than duplicates, the traced element");
  assert.ok(second.proposal.change.changes.some(row => row.action === "update"), "The candidate records an update to the existing contour");
  assert.notDeepEqual(updatedEntity.fields.params.profile, profile);
  near(second.state.elements.find(row => row.elementId === entity.entity_id).drawnShape.height, 3.1, "Regenerated height remains editable");
  const curve = second.proposal.change.edits.entities.find(row => row.fields.producer === "curve");
  assert.ok(curve, "An explicitly selected open line becomes a model curve alongside the mass");
  assert.equal(curve.fields.sourceDocumentTrace.annotationId, calibrated.annotations[0].id);
  assert.ok(second.state.elements.some(row => row.elementId === curve.entity_id));
  for (const untouched of first.state.elements.filter(row => row.elementId !== entity.entity_id))
    assert.deepEqual(second.state.elements.find(row => row.elementId === untouched.elementId), untouched,
      `Regeneration preserves the unselected model element ${untouched.elementId}`);
  assert.deepEqual((await call("GET", `/api/state?run=${first.job.candidateId}`)).elements.find(row => row.elementId === entity.entity_id).drawnShape,
    stateElement.drawnShape, "The earlier candidate remains an unchanged revision");

  // A fresh browser page re-reads retained vectors and calibration; it does not reuse the editor's React draft.
  await until(() => generate().isEnabled(), Boolean, "The second candidate left the page busy");
  await tracing().getByRole("button", { name: "View model and progress", exact: true }).click();
  await page.locator(".stage-model").waitFor({ state: "visible" });
  assert.equal(await page.locator(".stage-model").getAttribute("aria-hidden"), "false");
  if (process.env.BOARD_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_SCREENSHOT.replace(/\.png$/, "-model.png") });
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  await page.locator(".monkeyboard-context").waitFor();
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await page.mouse.dblclick(...centre); await editor();
  await page.locator(`.document-page__ink [data-stroke-id="${outlineId}"]`).waitFor();
  await page.locator(".document-calibration").waitFor();
  assert.deepEqual(await annotations(), secondPage);
  assert.equal(await strokePath(), localPath);
  assert.equal(await readFile(path.join(projectDir, "HEAD"), "utf8"), headBefore, "A candidate cannot issue HEAD");
  assert.equal(context.pages().length, 1);
  assert.equal(requests.filter(row => row.method === "POST" && row.path === "/api/intents").length, 0,
    "Manual tracing does not claim an image model was called");
  assert.deepEqual(errors, []);
  if (process.env.BOARD_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_SCREENSHOT });
  console.log(JSON.stringify({ passed: "original PNG, Board editor, closed vector correction and undo, saved scale/direction, exact source revision, real OCCT candidates, editable state and actual 3DM metre coordinates; save/proposal/candidate refusals retain the page; corrected paths update stable element IDs and a selected open curve; fresh page reload; HEAD unchanged", candidateIds: [first.job.candidateId, second.job.candidateId] }));
} catch (error) {
  console.error(`FAILED: ${error?.stack ?? error}`);
  if (page && !page.isClosed()) {
    if (process.env.BOARD_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_SCREENSHOT });
    console.error(await page.locator("body").innerText().catch(() => ""));
  }
  console.error(JSON.stringify({ errors, requests: requests.map(({ method, path: route, status, result }) => ({ method, route, status,
    ...(status >= 400 ? { result } : {}) })), apiLog }));
  throw error;
} finally {
  closing = true;
  await browser?.close(); await vite?.close();
  if (http.listening) await new Promise(resolve => http.close(resolve));
  if (api && api.exitCode === null) { const exited = new Promise(resolve => api.once("exit", resolve)); api.kill(); await exited; }
  assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
  assert.ok(path.basename(root).startsWith("monkeyboard-document-tracing-"));
  await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
