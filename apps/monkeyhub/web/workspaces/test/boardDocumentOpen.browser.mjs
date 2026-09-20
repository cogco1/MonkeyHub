/** Opening one registered board page in the existing document editor, and coming back.
 *
 * The real Hub project workspace -> Board -> page editor path against an isolated Studio project: a
 * real double click on the real canvas, the real DocumentCanvas on the exact
 * page, real saved annotations, and the same board afterwards. Refused board and
 * page writes, doubled returns, a second task and a real local drawing cover
 * what the return must not take away. No scene injection, no second editor, no
 * model or agent request.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
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
const root = await mkdtemp(path.join(tmpdir(), "monkeyboard-document-open-"));
const projectDir = path.join(root, "demo-project");
const errors = [], requests = [];
/** Writes this run refuses, by route, so a refusal boundary can be exercised. */
const faults = { "/api/board": false, "/api/document-annotations": false };
const http = createHttpServer();
let api, vite, browser, page, closing = false, apiLog = "";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const active = (board, type) => board.elements.filter((element) => !element.isDeleted && (!type || element.type === type));

try {
  const fixture = spawnSync(python, ["-c", `
import base64, json, sys
from pathlib import Path
import archflow
from tests.support import make_empty_project
from tests.test_documents import two_page_pdf
assert Path(archflow.__file__).resolve() == Path(sys.argv[2], "archflow/__init__.py").resolve(), archflow.__file__
# Authored building description, no run and no retained model: the same starting
# point the model-sync test draws on, so a local drawing here needs no export.
make_empty_project(Path(sys.argv[1]))
print(json.dumps({"pdf": base64.b64encode(two_page_pdf()).decode()}))
`, root, repoRoot], { cwd: apiRoot, encoding: "utf8", env: pythonEnv });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const { pdf } = JSON.parse(fixture.stdout.trim());

  const apiPort = await new Promise((resolve) => {
    const probe = createHttpServer();
    probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
  api = spawn(python, ["-m", "archflow_studio_api.main", "--port", String(apiPort), "--project-dir", projectDir], {
    cwd: apiRoot, env: { ...pythonEnv, ARCHFLOW_STUDIO_CAD_EXPORT: "off", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  api.on("error", (error) => errors.push(error.message));
  api.stdout.on("data", (chunk) => { apiLog = (apiLog + chunk).slice(-8000); });
  api.stderr.on("data", (chunk) => { apiLog = (apiLog + chunk).slice(-8000); });
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  for (let attempt = 0; attempt < 150; attempt++) {
    if (await fetch(`${apiOrigin}/api/health`).then((response) => response.ok).catch(() => false)) break;
    assert.ok(attempt < 149 && api.exitCode === null, `Studio startup failed: ${apiLog}`);
    await delay(100);
  }
  async function call(method, route, body) {
    const response = await fetch(apiOrigin + route, { method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const value = await response.json();
    assert.ok(response.ok, `${method} ${route}: ${response.status} ${JSON.stringify(value)}`);
    return value;
  }
  const project = await call("GET", "/api/project");
  assert.equal(path.resolve(project.projectDir), path.resolve(projectDir));
  // Register the drawing before the board opens, so its first page arrives on mount.
  const document = await call("POST", "/api/documents", { projectId: project.projectId, runId: null,
    fileName: "board-pages.pdf", mimeType: "application/pdf", contentBase64: pdf });
  assert.equal(document.pageCount, 2);
  const secondPage = { runId: document.runId, assetSha256: document.assetSha256,
    revisionRef: document.revisionRef ?? null, pageIndex: 1 };

  const board = () => call("GET", "/api/board");
  async function savedWhere(predicate, message) {
    for (let attempt = 0; attempt < 120; attempt++) {
      const result = await board();
      if (predicate(result)) return result;
      await delay(100);
    }
    assert.fail(message);
  }

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", publicDir: "../.generated/public",
    cacheDir: path.join(root, "vite-cache"), define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [react(), workspaceFixture()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const entry = { method: request.method, path: request.url.split("?")[0], query: request.url };
    requests.push(entry);
    // A refused write, produced without touching the retained project.
    if (request.method === "PUT" && faults[entry.path]) {
      request.resume();
      entry.status = 503;
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify({ code: "TRANSPORT_ERROR", detail: "Injected write failure." }));
      return;
    }
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => { if (chunks.length) try { entry.body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { /* bytes */ } });
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, (answer) => {
      entry.status = answer.statusCode;
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    proxied.on("error", (error) => {
      if (!closing) errors.push(error.message);
      if (!response.headersSent) response.writeHead(502);
      response.end();
    });
    request.pipe(proxied);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, locale: "en-US" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    errors.push(`External request: ${route.request().url()}`); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));

  const zoomText = () => page.locator(".excalidraw .reset-zoom-button").innerText();
  const footer = () => page.locator(".monkeyboard-footer").innerText();
  // The board's own static canvas, sampled for the ink colour saved on the page.
  const inkPixels = () => page.evaluate(() => {
    const canvas = document.querySelector("canvas.excalidraw__canvas.static") ?? document.querySelector(".excalidraw canvas");
    const image = canvas.getContext("2d", { willReadFrequently: true }).getImageData(0, 0, canvas.width, canvas.height);
    let count = 0;
    for (let index = 0; index < image.data.length; index += 4) {
      const [red, green, blue, alpha] = image.data.slice(index, index + 4);
      if (alpha > 200 && red > 140 && red - green > 60 && red - blue > 60) count += 1;
    }
    return count;
  });

  // The Hub embeds this page beside its own rail; the board is one of its tools.
  await page.goto(`${origin}/?view=board&embedded=tool&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await page.evaluate(() => { window.__sameDocument = "board"; window.__boardCanvas = document.querySelector(".monkeyboard-canvas canvas"); });
  await savedWhere((value) => active(value, "image").length === 1, "The registered drawing's first page was not received");

  // Place the second page as well, so the double click has to name the exact one.
  await page.getByRole("button", { name: "Project documents", exact: true }).click();
  await page.getByLabel("board-pages.pdf Page", { exact: true }).selectOption({ label: "Page 2 / 2" });
  await page.getByRole("button", { name: "Add page", exact: true }).click();
  const placed = await savedWhere((value) => active(value, "image").length === 2, "The second page was not added");
  const secondElement = active(placed, "image").find((element) => element.customData.sourceDocument.pageIndex === 1);
  assert.deepEqual(secondElement.customData.sourceDocument, secondPage);
  await page.getByRole("button", { name: "Hide project documents", exact: true }).click();

  // Adding a page fits it on screen; zoom in on it and select it there.
  await page.locator(".excalidraw .zoom-in-button").click();
  await page.locator(".excalidraw .zoom-in-button").click();
  // Adding the page fitted it on screen, but hiding the documents panel widened
  // the canvas under it, so find the page rather than assume it is centred.
  const canvasBox = await page.locator(".monkeyboard-canvas").boundingBox();
  let centre = null;
  for (const across of [0.5, 0.42, 0.58, 0.34, 0.66]) {
    centre = [canvasBox.x + canvasBox.width * across, canvasBox.y + canvasBox.height / 2];
    await page.mouse.click(...centre);
    if (/board-pages\.pdf · 2\/2/.test(await footer())) break;
  }
  await page.locator(".monkeyboard-context").waitFor();
  assert.match(await footer(), /board-pages\.pdf · 2\/2/, "Clicking the placed page must select that exact page");
  const placedZoom = await zoomText();
  const inkBefore = await inkPixels();
  assert.equal(inkBefore, 0, "The unmarked drawing must not already show ink-coloured pixels");
  await page.locator(".monkeyboard-context button:not([disabled])").filter({ hasText: /^Edit this page$/ }).waitFor();

  // The double click itself: the same document, now showing the same page.
  await page.mouse.dblclick(...centre);
  await page.locator(".document-header").waitFor();
  const workspaceUrl = page.url();
  assert.equal(new URL(workspaceUrl).searchParams.get("view"), "board", "The internal page editor does not rewrite application navigation");
  assert.equal(await page.evaluate(() => window.__sameDocument), "board",
    "Opening the page must not reload the tab that was showing the board");
  assert.equal(await page.getByLabel("Source document", { exact: true }).inputValue(),
    secondPage.revisionRef ?? secondPage.assetSha256, "The editor opens on the drawing the board named");
  assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "1");
  assert.equal(await page.locator(".document-viewport").getAttribute("aria-label"), "Annotation canvas, page 2");
  await page.locator(".document-page__raster").waitFor();
  await page.locator(".document-viewport[data-ready=true]").waitFor();

  // Mark that page, then leave for the board: leaving is what saves the page.
  await page.getByLabel("Ink colour", { exact: true }).fill("#ff0000");
  await page.getByLabel("Ink width", { exact: true }).selectOption({ label: "Thick" });
  const pageBox = await page.locator(".document-page").boundingBox();
  await page.mouse.move(pageBox.x + pageBox.width * 0.2, pageBox.y + pageBox.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(pageBox.x + pageBox.width * 0.8, pageBox.y + pageBox.height * 0.7, { steps: 20 });
  await page.mouse.up();
  const pageAnnotations = (pageIndex) => call("GET", `/api/document-annotations?runId=${secondPage.runId}` +
    `&assetSha256=${secondPage.assetSha256}&pageIndex=${pageIndex}` +
    (secondPage.revisionRef === null ? "" : `&drawingRevisionRef=${encodeURIComponent(secondPage.revisionRef)}`));
  let saved = await pageAnnotations(1);
  for (let attempt = 0; attempt < 150 && saved.annotations.length === 0; attempt++) {
    await delay(100); saved = await pageAnnotations(1);
  }
  assert.equal(saved.annotations.length, 1, "The mark drawn in the editor was not saved");
  await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor();
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();

  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  const written = requests.filter((request) => request.method === "PUT" && request.path === "/api/document-annotations");
  assert.equal(written.length, 1, "Marking one page writes that one page");
  assert.equal(written[0].body.runId, secondPage.runId);
  assert.equal(written[0].body.assetSha256, secondPage.assetSha256);
  assert.equal(written[0].body.pageIndex, 1, "The marks belong to the page that was opened");
  assert.equal(written[0].body.drawingRevisionRef ?? null, secondPage.revisionRef);
  assert.equal(written[0].body.annotations.length, 1);
  assert.equal(saved.annotations[0].color, "#ff0000");
  assert.deepEqual((await pageAnnotations(0)).annotations, [], "The page that was not opened keeps its own empty draft");

  // Back on the board this tab left: same tab, same place, same selection.
  assert.equal(page.url(), workspaceUrl);
  assert.equal(await page.evaluate(() => window.__boardCanvas === document.querySelector(".monkeyboard-canvas canvas")), true,
    "The live Board canvas remains mounted while its page editor is open");
  assert.equal(await page.evaluate(() => window.__sameDocument), "board",
    "Returning to the board must not reload the tab either");
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-context").waitFor();
  assert.equal(await zoomText(), placedZoom, "The board is shown at the zoom it was left at");
  assert.match(await footer(), /board-pages\.pdf · 2\/2/, "The page that was open is still the selected page");
  await page.locator(".monkeyboard-context button:not([disabled])").filter({ hasText: /^Edit this page$/ }).waitFor();
  const restored = await board();
  assert.deepEqual(active(restored, "image").map((element) => element.customData.sourceDocument),
    active(placed, "image").map((element) => element.customData.sourceDocument),
    "Returning changes no page source on the board");

  // The saved marks are what the board now shows for that page.
  for (let attempt = 0; attempt < 100 && await inkPixels() === 0; attempt++) await delay(100);
  assert.ok(await inkPixels() > 0, "The board must show the marks its page editor saved");
  assert.ok(requests.some((request) => request.method === "GET" && request.path === "/api/document-annotations"
    && request.query.includes("pageIndex=1")), "The board reads the page's saved marks to draw it");

  const standaloneBox = canvasBox;
  const placedPoint = centre;

  // A refused board save keeps the operator on the board, with their marks.
  const writes = (route) => requests.filter((request) => request.method === "PUT" && request.path === route).length;
  const savedMarks = (value) => active(value).filter((element) => element.type === "arrow").length;
  const beforeRefusal = await board();
  assert.equal(savedMarks(beforeRefusal), 0);
  faults["/api/board"] = true;
  await page.locator(".excalidraw").focus(); await page.keyboard.press("Escape"); await page.keyboard.press("a");
  const arrowStart = [standaloneBox.x + standaloneBox.width * 0.78, standaloneBox.y + standaloneBox.height * 0.3];
  const arrowEnd = [standaloneBox.x + standaloneBox.width * 0.9, standaloneBox.y + standaloneBox.height * 0.42];
  assert.equal(await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.tagName, arrowStart), "CANVAS",
    "The refusal mark begins on the canvas, clear of Excalidraw's shape controls");
  await page.mouse.move(...arrowStart);
  await page.mouse.down();
  await page.mouse.move(...arrowEnd, { steps: 12 });
  await page.mouse.up();
  const boardAlert = page.locator(".monkeyboard-alert").filter({ hasText: "Changes have not been saved." });
  await boardAlert.waitFor();
  const refusedWrites = writes("/api/board");
  await page.mouse.dblclick(...placedPoint);
  await delay(700);
  assert.equal(new URL(page.url()).searchParams.get("view"), "board", "A refused board save must not leave the board");
  assert.equal(await page.locator(".document-header").isVisible(), false, "No page editor opens over an unsaved board");
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await boardAlert.waitFor();
  assert.equal(writes("/api/board"), refusedWrites,
    "A board whose last save was refused is not written again behind the operator");
  // The canvas is untouched by the refusal: the mark saves once the write is accepted.
  faults["/api/board"] = false;
  await boardAlert.getByRole("button", { name: "Retry", exact: true }).click();
  const afterRetry = await savedWhere((value) => savedMarks(value) === 1, "The mark drawn during a refused save was lost");
  assert.deepEqual(active(afterRetry, "image").map((element) => element.customData.sourceDocument),
    active(beforeRefusal, "image").map((element) => element.customData.sourceDocument));
  await boardAlert.waitFor({ state: "hidden" });

  // A refused page save keeps the operator in the editor, on the same page.
  await page.mouse.click(...placedPoint);
  await page.locator(".monkeyboard-context").waitFor();
  await page.mouse.dblclick(...placedPoint);
  await page.locator(".document-header").waitFor();
  const openedPage = await page.getByLabel("Page", { exact: true }).inputValue();
  assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), openedPage);
  await page.locator(".document-viewport[data-ready=true]").waitFor();
  const strokes = () => page.locator(".document-page__ink [data-stroke-id]").count();
  const strokesBefore = await strokes();
  faults["/api/document-annotations"] = true;
  const editorBox = await page.locator(".document-page").boundingBox();
  await page.mouse.move(editorBox.x + editorBox.width * 0.25, editorBox.y + editorBox.height * 0.6);
  await page.mouse.down();
  await page.mouse.move(editorBox.x + editorBox.width * 0.75, editorBox.y + editorBox.height * 0.35, { steps: 20 });
  await page.mouse.up();
  await page.locator(".document-error").waitFor();
  assert.equal(await strokes(), strokesBefore + 1, "The refused stroke stays on the page");
  const refusedPageWrites = writes("/api/document-annotations");
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  for (let attempt = 0; attempt < 100 && writes("/api/document-annotations") === refusedPageWrites; attempt++) await delay(100);
  assert.ok(writes("/api/document-annotations") > refusedPageWrites, "Leaving writes the page rather than skipping it");
  await delay(500);
  assert.equal(await page.locator(".document-header").count(), 1, "A refused page save must stay in the editor");
  assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), openedPage, "…on the page that was opened");
  assert.equal(await page.locator(".monkeyboard-canvas").isVisible(), false);
  assert.equal(await strokes(), strokesBefore + 1, "The unsaved marks are still on the page");
  await page.locator(".document-error").waitFor();

  // Two returns in the same tick leave once and write the page once.
  faults["/api/document-annotations"] = false;
  const acceptedFrom = writes("/api/document-annotations");
  await page.evaluate(() => {
    const entry = [...document.querySelectorAll("button")].find((button) => button.textContent.trim() === "MonkeyBoard · Board");
    entry.click(); entry.click();
  });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await delay(700);
  assert.equal(writes("/api/document-annotations"), acceptedFrom + 1, "A doubled return must not write the page twice");
  assert.equal(new URL(page.url()).searchParams.get("view"), "board");
  assert.equal(context.pages().length, 1);
  const kept = await pageAnnotations(Number(openedPage));
  assert.equal(kept.annotations.length, Number(openedPage) === 1 ? 2 : 1, "Both strokes reached the page that was open");

  // The same mounted Arch workspace keeps its unsynced geometry across Board visits.
  await page.getByTestId("workspace-arch").click();
  const viewport = page.locator(".viewport-canvas");
  await viewport.waitFor();
  const viewportBox = await viewport.boundingBox();
  await page.getByRole("button", { name: "Rectangle", exact: true }).click();
  // The ground plane, wherever this project's camera put it; the idle card sits
  // in the middle of the viewport and is not part of it.
  const sketchValue = page.locator(".sketch-entry__value input");
  for (const [across, down] of [[0.22, 0.78], [0.78, 0.78], [0.22, 0.26], [0.78, 0.26], [0.5, 0.88]]) {
    await page.mouse.click(viewportBox.x + viewportBox.width * across, viewportBox.y + viewportBox.height * down);
    if (await sketchValue.count() > 0) break;
  }
  await sketchValue.waitFor();
  await sketchValue.fill("3"); await sketchValue.press("Enter");
  await sketchValue.fill("2"); await sketchValue.press("Enter");
  const unsyncedStatus = page.locator(".model-tools__sync-status", { hasText: "Unsynced" });
  await unsyncedStatus.waitFor();

  await page.evaluate(() => { window.__archCanvas = document.querySelector(".viewport-canvas"); });
  await page.getByTestId("workspace-board").click();
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  assert.equal(context.pages().length, 1, "Workspace switching stays in the same project panel");
  assert.equal(await page.evaluate(() => window.__boardCanvas === document.querySelector(".monkeyboard-canvas canvas")), true);
  assert.equal(await page.locator(".viewport-canvas").count(), 1, "Hidden Arch remains mounted with its local draft");
  await page.getByTestId("workspace-arch").click();
  await unsyncedStatus.waitFor();
  assert.equal(await page.evaluate(() => window.__archCanvas === document.querySelector(".viewport-canvas")), true);
  assert.equal(await page.getByRole("button", { name: "Undo model", exact: true }).isEnabled(), true);

  // Undo still belongs to the same local draft after the workspace round trip.
  await page.getByRole("button", { name: "Undo model", exact: true }).click();
  await unsyncedStatus.waitFor({ state: "hidden" });
  await page.getByTestId("workspace-board").click();
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  assert.equal(context.pages().length, 1, "A synced page returns in this tab");
  assert.equal(new URL(page.url()).searchParams.get("view"), "board");
  await page.locator(".monkeyboard-context").waitFor();
  assert.match(await footer(), /board-pages\.pdf · 2\/2/,
    "…to the same board page it left");

  // Another page of this same retained document changes the editor's explicit source,
  // while reopening the previous page reads back marks saved while it was hidden.
  await page.getByRole("button", { name: "Project documents", exact: true }).click();
  await page.getByLabel("board-pages.pdf Page", { exact: true }).selectOption({ label: "Page 1 / 2" });
  await page.locator(".monkeyboard-source-link").click();
  await page.locator('.document-viewport[aria-label="Annotation canvas, page 1"][data-ready=true]').waitFor();
  assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "0");
  assert.equal(await page.locator(".document-page__ink [data-stroke-id]").count(), 0);
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  const previousPage = await pageAnnotations(1);
  const extra = { id: "board-feedback-return", kind: "line", points: [[0.1, 0.1], [0.7, 0.2]], color: "#ff0000", lineWidth: 0.004 };
  await call("PUT", "/api/document-annotations", { projectId: project.projectId, runId: secondPage.runId,
    assetSha256: secondPage.assetSha256, drawingRevisionRef: secondPage.revisionRef, pageIndex: 1,
    baseRevisionSha256: previousPage.revisionSha256, annotations: [...previousPage.annotations, extra], comment: previousPage.comment });
  await page.getByLabel("board-pages.pdf Page", { exact: true }).selectOption({ label: "Page 2 / 2" });
  await page.locator(".monkeyboard-source-link").click();
  await page.locator('.document-viewport[aria-label="Annotation canvas, page 2"][data-ready=true]').waitFor();
  await page.locator('.document-page__ink [data-stroke-id="board-feedback-return"]').waitFor();
  assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "1");
  assert.equal(await page.locator(".document-page__ink [data-stroke-id]").count(), previousPage.annotations.length + 1);

  assert.deepEqual(requests.filter((request) => /\/api\/(intents|proposals|jobs|model-annotations|candidates)/.test(request.path)), [],
    "Opening and leaving a page must never enter a model or agent path");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: "board double click opens the exact registered page in the existing document editor, keeps its saved marks and source binding, and returns to the same board viewport and selection without reloading the tab, inside a mounted project workspace; refused board and page writes retain unsaved edits; a doubled return writes once; Arch local geometry and the original Board canvas remain mounted across workspace switches",
    requests: requests.length }));
} catch (error) {
  console.error(`FAILED: ${error?.stack ?? error}`);
  if (page && !page.isClosed()) {
    if (process.env.BOARD_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_SCREENSHOT });
    console.error(await page.locator("body").innerText().catch(() => ""));
  }
  console.error(JSON.stringify({ errors, requests: requests.map(({ method, path: route, status }) => ({ method, route, status })), apiLog }));
  throw error;
} finally {
  closing = true;
  await browser?.close(); await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  if (api && api.exitCode === null) {
    const exited = new Promise((resolve) => api.once("exit", resolve)); api.kill(); await exited;
  }
  assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
  assert.ok(path.basename(root).startsWith("monkeyboard-document-open-"));
  await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
