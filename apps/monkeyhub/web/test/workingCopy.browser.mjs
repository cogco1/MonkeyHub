/** Retained A/B models through the actual Hub ProjectWorkspace, with a real,
 * disposable Project Runtime. Run from web: node test/workingCopy.browser.mjs.
 * PYTHON and PLAYWRIGHT_MODULE optionally select installed test dependencies.
 *
 * #225 made the runtime working draft authoritative; #294 reads retained
 * Explorations as legacy Studies; #302 moved View/Continue into the Design Tree.
 * The six original regressions below keep their source/persistence guarantees
 * without private projects, editingBases, generated node labels or tree geometry.
 */
import assert from "node:assert/strict";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { readdir } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import { workspaceFixture } from "./workspaceFixture.mjs";
import { createRetainedModelFixture } from "./retainedModelFixture.mjs";
import { cmd } from "./commandLocator.mjs";

const fixture = await createRetainedModelFixture({ name: "working-copy" });
const { project, group, sources: { A, B }, document, webRoot, projectRoot, getJson } = fixture;
const errors = [], requests = [], captures = [], passed = [], consoleErrors = [];
const http = createHttpServer();
let annotationSize = null;
let browser, vite, page, closing = false, phase = "setup", allowedMutation = null;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sourceKey = (source) => JSON.stringify([source.runId, source.stateDigest, source.assetSha256]);
const state = () => page.evaluate(() => window.__retainedModel);
const inkCanvas = () => page.locator('.stage-model canvas.annotate[aria-hidden="true"]');
const activeCanvas = () => page.locator(".stage-model canvas.annotate[data-armed]");
const undo = () => cmd(page.locator("#annotation-tools"), "view.markup_tools.undo");
async function until(read, accepts, message, timeout = 30000) {
  const deadline = Date.now() + timeout;
  let value;
  do {
    assert.deepEqual(errors, [], `Browser/API errors in ${phase}`);
    value = await read();
    if (accepts(value)) return value;
    await delay(80);
  } while (Date.now() < deadline);
  assert.fail(`${message}: ${JSON.stringify(value)}`);
}
async function step(name, action) { phase = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
const annotationWrites = () => requests.filter((row) => row.path === "/api/model-annotations" && row.method === "PUT" && row.response);
// The working position moves through PUT /api/working-draft, or the Design Tree's Continue through the Runtime's Switch (2-6).
const positionWrites = () => requests.filter((row) => ((row.path === "/api/working-draft" && row.method === "PUT")
  || (row.path === "/api/working-design/switch" && row.method === "POST")) && row.response);
async function ready(source, readStart = 0) {
  await until(state, (value) => value?.status === "ready" && value.annotationsReady && value.loadingSha === null && sourceKey(value.loadedModelSource ?? {}) === sourceKey(source),
    `Exact ${source.runId}/${source.assetSha256} model did not load`, 60000);
  await until(() => requests.slice(readStart).findLast((row) => row.path === "/api/model-annotations" && row.method === "GET" && row.response &&
    sourceKey(row.response.modelSource) === sourceKey(source)), Boolean, "Exact source ink was not read");
  await until(() => getJson(`/api/model-assets/${source.assetSha256}/preview`, { runId: source.runId, stateDigest: source.stateDigest }),
    (value) => value && sourceKey(value.modelSource) === sourceKey(source), "The exact preview must finish before the next action", 60000);
}
async function openTracing() {
  if (await page.locator("#annotation-tools").isVisible()) return;
  const more = cmd(page, "view.menu.toggle", { arg: "model_tools_more", includeHidden: true });
  if (await more.getAttribute("aria-expanded") !== "true") await more.click();
  await cmd(page, "view.markup_tools.tracing_paper").click();
  await page.locator("#annotation-tools").waitFor();
}
async function view(source) {
  await page.getByTestId("workspace-tree").click();
  const tree = page.locator('[data-project-surface="tree"]');
  if (!await tree.getByRole("tree").isVisible()) await cmd(page, "view.design_tree.list").click();
  const id = source.runId === A.runId ? `stage:${fixture.stage.stageRef}` : `candidate:${B.runId}`;
  await cmd(tree, "view.design_tree.select_row", { arg: id }).click();
  await cmd(tree.locator(`.design-tree-inspector[data-node=${JSON.stringify(id)}]`), "candidate.compare.view").click();
  await ready(source);
  await openTracing();
  await equalCanvas();
}
async function equalCanvas() {
  // #337's read-only-source notice takes one row above B, but not Current A.
  // Compare their ink at equal *canvas* dimensions, not equal window heights.
  // The browser window adapts to the real measured chrome; no product CSS or
  // camera is modified, and exact pixel/pose assertions below stay unchanged.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const size = await inkCanvas().evaluate(canvas => [canvas.width, canvas.height]);
  annotationSize ??= size;
  const box = await inkCanvas().boundingBox(), viewport = page.viewportSize();
  assert.ok(box && viewport && size[0] > 200 && size[1] > 200);
  if (size[0] !== annotationSize[0] || size[1] !== annotationSize[1]) {
    await page.setViewportSize({ width: viewport.width + annotationSize[0] - size[0], height: viewport.height + annotationSize[1] - size[1] });
    await until(() => inkCanvas().evaluate(canvas => [canvas.width, canvas.height]),
      value => JSON.stringify(value) === JSON.stringify(annotationSize), "The annotation canvas must regain its measured size");
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
}
async function inkImage() { await equalCanvas(); await delay(380); return inkCanvas().evaluate((canvas) => canvas.toDataURL()); }
// GH-300/b3-tracing: each opening of the tracing paper is a new blank sheet. Strokes stay
// on the open sheet (one local Undo history) and the sheet is retained once, as one
// model-annotation revision of its exact source, when the person leaves it.
const paperInk = () => inkCanvas().evaluate((canvas) =>
  canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data.some((value, index) => index % 4 === 3 && value > 0));
async function exitPaper() {
  await cmd(page.locator("#annotation-tools"), "view.markup_tools.cancel").click();
  await until(() => page.locator(".stage-model canvas.annotate").count(), (count) => count === 0, "Leaving the paper must take its ink off the model");
  assert.equal(await page.locator("#annotation-tools").count(), 0);
}
async function blankPaper() {
  await openTracing(); await equalCanvas();
  assert.equal(await undo().isDisabled(), true, "A reopened paper has nothing to undo");
  assert.equal(await paperInk(), false, "A reopened paper starts blank");
}
async function strokeLine(y) {
  await cmd(page.locator("#annotation-tools"), "view.markup_tools.line").click();
  await until(() => activeCanvas().getAttribute("data-armed"), (value) => value === "true", "Line tool did not arm");
  const box = await activeCanvas().boundingBox();
  assert.ok(box && box.width > 200 && box.height > 200);
  await page.mouse.move(box.x + box.width * 0.34, box.y + box.height * y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.51, box.y + box.height * y + 13, { steps: 6 });
  await page.mouse.up();
  // Disarm without changing the stroke or its retained view.
  await cmd(page.locator("#annotation-tools"), "view.markup_tools.line").click();
}
async function drawPaper(source, baseline, ys) {
  const before = annotationWrites().length;
  await blankPaper();
  for (const y of ys) await strokeLine(y);
  await until(() => undo().isEnabled(), Boolean, "The paper's strokes must be undoable");
  await delay(400);
  assert.equal(annotationWrites().length, before, "Strokes stay on the open paper until it is left");
  const image = await inkImage();
  allowedMutation = { method: "PUT", path: "/api/model-annotations", source, revision: baseline.revisionSha256 };
  await exitPaper();
  await until(() => annotationWrites().length, (count) => count === before + 1, "Leaving the paper must retain it in exactly one source-bound write");
  const write = annotationWrites()[before];
  assert.equal(allowedMutation, null);
  assert.equal(write.body.annotations.length, ys.length, "The retained paper holds exactly this sheet's strokes");
  assert.ok(write.body.annotations.every((gesture) => gesture.kind === "line"));
  assert.deepEqual(write.response.modelSource, source);
  assert.deepEqual(write.response.annotations, write.body.annotations.map((gesture) => ({
    label: null, lengthModelUnits: null, worldDirection: null, worldEnd: null, worldStart: null, ...gesture,
  })));
  return { ...write, line: write.body.annotations[0], image };
}
async function openVersions() {
  const button = page.locator('.stage__versions-toggle[data-command="design.go_to_step.versions"]');
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
  await page.locator("#stage-versions-panel").waitFor();
}
const savedPapers = {};
const paperRow = (saved) => cmd(page.locator("#stage-versions-panel"), "view.markup_tools.restore_paper", { arg: saved.response.revisionSha256 });
async function restorePaper(saved) {
  const other = saved === savedPapers.A ? savedPapers.B : savedPapers.A;
  await openVersions();
  await until(() => paperRow(saved).count(), (count) => count === 1, "This model's retained paper must be in its history");
  assert.equal(await paperRow(other).count(), 0, "The other model's paper must not appear in this model's history");
  await until(() => paperRow(saved).isEnabled(), Boolean, "The retained paper must be restorable");
  await paperRow(saved).click();
  await page.locator("#annotation-tools").waitFor();
  await until(() => undo().isDisabled(), Boolean, "Reopening a retained paper is not itself an edit");
  assert.equal(await inkImage(), saved.image, "History restores the complete retained paper");
}
async function documentSnapshot() {
  const runs = (await readdir(path.join(projectRoot, "runs"), { withFileTypes: true })).filter((row) => row.isDirectory()).map((row) => row.name).sort();
  const result = [];
  for (const runId of runs) for (const dto of (await getJson("/api/documents", { runId })).documents) {
    const pages = [];
    for (const page of dto.pages) pages.push(await getJson("/api/document-annotations", {
      runId, assetSha256: dto.assetSha256, pageIndex: page.pageIndex, drawingRevisionRef: dto.revisionRef,
    }));
    result.push({ dto, pages });
  }
  return result;
}
const documentKey = (dto) => JSON.stringify([dto.runId, dto.assetSha256, dto.revisionRef ?? null]);
try {
  const [draftBefore, annotationsA, annotationsB, documentsBefore] = await Promise.all([
    getJson("/api/working-draft"), getJson("/api/model-annotations", A), getJson("/api/model-annotations", B), documentSnapshot(),
  ]);
  assert.equal(group.selectedOptionId, "B");
  assert.equal(draftBefore.current.runId, A.runId);
  assert.equal(document.modelSource, null);
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error",
    publicDir: ".generated/public", define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    cacheDir: path.join(fixture.root, "vite-cache"), plugins: [react(), workspaceFixture(), {
      name: "observe-retained-model", enforce: "pre", transform(source, id) {
        const file = id.split("?")[0].replaceAll("\\", "/");
        if (file === `${webRoot.replaceAll("\\", "/")}/test/workspace-fixture.tsx`) {
          return { code: `import "/@fs/${path.resolve(webRoot, "../../../packages/web-shared/src/base.css").replaceAll("\\", "/")}";\n${source}`, map: null };
        }
        if (file !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
        const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, marker + `\n(window as unknown as {__retainedModel: unknown}).__retainedModel = {
          loadedModelSource, editingModelSource, status: viewerStatus, loadingSha: artifactLoadingSha, annotationsReady: modelAnnotations.ready,
        };`), map: null };
      },
    }], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null, proxy: {} } });
  http.on("request", async (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const url = new URL(request.url, fixture.apiOrigin);
    const entry = { method: request.method, path: url.pathname, query: url.search };
    requests.push(entry);
    try {
      const chunks = []; for await (const chunk of request) chunks.push(chunk);
      const bytes = Buffer.concat(chunks);
      if (bytes.length) entry.body = JSON.parse(bytes.toString("utf8"));
      if (!["GET", "HEAD", "OPTIONS"].includes(entry.method)) {
        if (entry.method === "POST" && entry.path === "/api/captures") {
          assert.deepEqual(Object.keys(entry.body).sort(), ["modelSource", "pngBase64", "runId"]);
          assert.equal(entry.body.runId, entry.body.modelSource.runId);
          assert.ok([A, B].some((source) => sourceKey(source) === sourceKey(entry.body.modelSource)));
          assert.deepEqual(entry.body.modelSource, (await state()).loadedModelSource);
          assert.equal(Buffer.from(entry.body.pngBase64, "base64").toString("ascii", 1, 4), "PNG");
        } else if (entry.method === "POST" && entry.path === "/api/events/timing") {
          assert.equal(entry.body.projectId, project.projectId);
          assert.ok([A.runId, B.runId].includes(entry.body.runId));
          assert.ok(["model_load", "model_download", "model_parse", "model_install", "model_projection", "document_load", "document_render"].includes(entry.body.phase));
          if (entry.body.details?.asset_sha256) assert.ok([A.assetSha256, B.assetSha256, document.assetSha256].includes(entry.body.details.asset_sha256));
        } else if (entry.method === "PUT" && entry.path === "/api/board" && phase === "setup") {
          assert.equal(entry.body.projectId, project.projectId);
          const images = entry.body.elements.filter((element) => !element.isDeleted && element.type === "image");
          assert.equal(images.length, 1);
          assert.deepEqual(images[0].customData.sourceDocument, {
            runId: document.runId, assetSha256: document.assetSha256, revisionRef: document.revisionRef ?? null, pageIndex: 0,
          });
        } else {
          assert.ok(allowedMutation, `Unexpected write ${entry.method} ${entry.path}`);
          assert.equal(entry.method, allowedMutation.method); assert.equal(entry.path, allowedMutation.path);
          assert.equal(entry.body.projectId, project.projectId);
          assert.equal(entry.body.baseRevisionSha256, allowedMutation.revision);
          if (allowedMutation.source) assert.deepEqual(entry.body.modelSource, allowedMutation.source);
          else assert.equal(entry.body.runId, B.runId);
          allowedMutation = null;
        }
      }
      const upstream = httpRequest(url, { method: entry.method, headers: { ...request.headers, host: url.host } }, (answer) => {
        entry.status = answer.statusCode;
        if (answer.statusCode >= 400) errors.push(`${entry.method} ${entry.path}: ${answer.statusCode}`);
        const received = [];
        if (url.pathname !== "/api/events") {
          answer.on("data", (chunk) => received.push(chunk));
          answer.on("end", () => {
            try { entry.response = JSON.parse(Buffer.concat(received).toString("utf8")); } catch { /* binary asset */ }
            if (entry.path === "/api/captures" && entry.response) {
              try {
                assert.deepEqual(entry.response.document.modelSource, entry.body.modelSource);
                assert.deepEqual(entry.response.document.viewRecipe, { kind: "viewport-preview" });
                captures.push(entry.response.document);
              } catch (error) { errors.push(String(error)); }
            }
          });
        }
        response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
      });
      upstream.on("error", (error) => { if (!closing && !response.destroyed) { errors.push(String(error)); if (!response.headersSent) response.writeHead(502); response.end(); } });
      response.on("close", () => upstream.destroy());
      upstream.end(bytes);
    } catch (error) { errors.push(String(error)); response.writeHead(500); response.end(String(error)); }
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1540, height: 1060 }, deviceScaleFactor: 1, locale: "en-US" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => { errors.push(`External request: ${route.request().url()}`); return route.abort(); });
  page = await context.newPage(); page.setDefaultTimeout(30000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("response", (response) => {
    if (response.status() >= 400 && !new URL(response.url()).pathname.startsWith("/api/")) errors.push(`Asset ${response.status()}: ${response.url()}`);
  });
  await page.goto(`${origin}/?view=board&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await until(() => getJson("/api/board"), (value) => value.elements.some((element) => element.type === "image" && !element.isDeleted), "The exact drawing must reach Board");
  await cmd(page.locator(".monkeyboard-actions"), "view.menu.toggle", { arg: "board_more" }).click();
  await cmd(page.locator(".monkeyboard-actions"), "view.canvas.fit").click();
  await cmd(page.locator(".monkeyboard-actions"), "view.menu.toggle", { arg: "board_more" }).click();
  const box = await page.locator(".monkeyboard-canvas").boundingBox();
  assert.ok(box);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await cmd(page.locator(".monkeyboard-context"), "view.open_document.page").click();
  await page.locator('.document-viewport[data-ready="true"]').waitFor();
  let savedA, savedB;
  await step("selected B is read without moving runtime authority or the exact Board page", async () => {
    assert.equal(await page.getByLabel("Source document", { exact: true }).inputValue(), document.revisionRef ?? document.assetSha256);
    assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "0");
    assert.equal(await page.locator("#document-comment").inputValue(), "Keep this exact page and its saved ink");
    assert.deepEqual(await getJson("/api/working-draft"), draftBefore);
    assert.deepEqual(await getJson("/api/working-copies/retained-options"), group);
    const currentDocuments = await documentSnapshot();
    for (const before of documentsBefore) assert.deepEqual(currentDocuments.find((row) => documentKey(row.dto) === documentKey(before.dto)), before);
    assert.equal(annotationWrites().length + positionWrites().length, 0);
    await page.getByTestId("workspace-arch").click();
    await ready(A);
  });
  await step("View B loads exact B without Continue or moving the editing source", async () => {
    const start = requests.length;
    await view(B);
    const reads = requests.slice(start).filter((row) => /^\/api\/artifacts\/[^/]+\/bytes$/.test(row.path));
    assert.ok(reads.length);
    assert.deepEqual([...new Set(reads.map((row) => row.path))], [`/api/artifacts/${B.assetSha256}/bytes`]);
    assert.deepEqual((await state()).editingModelSource, A);
    assert.deepEqual(await getJson("/api/working-draft"), draftBefore);
    assert.equal(await undo().isDisabled(), true);
    assert.deepEqual(await getJson("/api/model-annotations", B), annotationsB);
  });
  await step("A and B papers are each retained once on exit, to their own exact source with the same camera", async () => {
    await view(A); assert.equal(await undo().isDisabled(), true);
    savedA = savedPapers.A = await drawPaper(A, annotationsA, [0.42, 0.5]);
    await blankPaper();
    await exitPaper();
    assert.equal(annotationWrites().length, 1, "Opening and leaving a blank paper does not overwrite A's retained paper");
    await view(B); assert.equal(await undo().isDisabled(), true);
    savedB = savedPapers.B = await drawPaper(B, annotationsB, [0.68]);
    assert.deepEqual(savedA.line.camera, savedB.line.camera);
    assert.deepEqual(savedA.line.screenSize, savedB.line.screenSize);
    assert.notEqual(savedA.line.id, savedB.line.id);
    assert.deepEqual(await getJson("/api/working-draft"), draftBefore);
  });
  await step("switching A/B opens blank papers, and each history restores only its own paper", async () => {
    for (const [source, saved] of [[A, savedA], [B, savedB]]) {
      await view(source);
      await blankPaper();
      assert.deepEqual(await getJson("/api/model-annotations", source), saved.response);
      assert.deepEqual(await getJson("/api/model-annotations/history", source), [saved.response]);
      await restorePaper(saved);
      await exitPaper();
    }
    assert.equal(annotationWrites().length, 2, "Switching, reopening and restoring unchanged papers write nothing");
    assert.equal(positionWrites().length, 0);
  });
  await step("only explicit Continue persists B as the runtime working position", async () => {
    const before = await getJson("/api/working-draft");
    allowedMutation = { method: "POST", path: "/api/working-design/switch", revision: before.revisionSha256 };
    await cmd(page.locator(".stage-chip__viewing"), "design.go_to_step.continue").click();
    await until(() => positionWrites().length, (count) => count === 1, "Continue must persist exactly one working position");
    await until(() => getJson("/api/working-draft"), (value) => value.current?.runId === B.runId, "Continue did not retain B");
    await ready(B);
    await until(state, (value) => sourceKey(value.editingModelSource ?? {}) === sourceKey(B), "Editing source must become exact B");
    await blankPaper();
    await exitPaper();
    assert.deepEqual(await getJson("/api/working-copies/retained-options"), group, "Tree Continue changes current, not the retained Exploration");
  });
  await step("reload keeps both retained papers in their own histories while existing drawings remain unchanged", async () => {
    const start = requests.length;
    await page.goto(`${origin}/?lang=en`, { waitUntil: "domcontentloaded" });
    await ready(B, start);
    const restoredRead = requests.slice(start).findLast((row) => row.path === "/api/model-annotations" && row.method === "GET" && row.response &&
      sourceKey(row.response.modelSource) === sourceKey(B));
    assert.deepEqual(restoredRead.response, savedB.response, "The new page must itself reread B's exact saved annotation revision");
    assert.deepEqual((await state()).editingModelSource, B);
    assert.deepEqual(await getJson("/api/model-annotations/history", A), [savedA.response]);
    assert.deepEqual(await getJson("/api/model-annotations/history", B), [savedB.response]);
    await blankPaper(); await exitPaper();
    assert.deepEqual([...new Set(requests.slice(start).filter((row) => /^\/api\/artifacts\/[^/]+\/bytes$/.test(row.path)).map((row) => row.path))], [`/api/artifacts/${B.assetSha256}/bytes`]);
    assert.ok(requests.slice(start).some((row) => row.path === "/api/state" && new URLSearchParams(row.query).get("run") === B.runId));
    await restorePaper(savedB);
    await until(() => inkCanvas().evaluate((canvas, point) => {
      const [x, y] = point;
      const pixels = canvas.getContext("2d").getImageData(Math.max(0, x - 3), Math.max(0, y - 3), 7, 7).data;
      return pixels.some((value, index) => index % 4 === 3 && value > 0);
    }, savedB.line.screen[0].map((value, index) => Math.round((value + savedB.line.screen[1][index]) / 2))),
    Boolean, "The specific B stroke midpoint must be painted after reload");
    await exitPaper();
    const after = await documentSnapshot();
    for (const before of documentsBefore) assert.deepEqual(after.find((row) => documentKey(row.dto) === documentKey(before.dto)), before);
    const additional = after.filter((row) => !documentsBefore.some((old) => documentKey(old.dto) === documentKey(row.dto)));
    assert.deepEqual(new Set(additional.map((row) => documentKey(row.dto))), new Set(captures.map(documentKey)), "Only verified exact-source viewport previews may be added");
    for (const row of additional) { assert.deepEqual(row.dto.viewRecipe, { kind: "viewport-preview" }); assert.ok(row.pages.every((page) => page.annotations.length === 0 && page.comment === "")); }
    assert.equal(annotationWrites().length, 2); assert.equal(positionWrites().length, 1);
    assert.equal(requests.filter((row) => row.path === "/api/document-annotations" && row.method !== "GET").length, 0);
    assert.deepEqual(errors, []);
  });
  console.log(JSON.stringify({ passed: passed.length, sources: { A, B }, annotationPuts: annotationWrites().length, workingPositionPuts: positionWrites().length,
    retainedPreviews: captures.length, originalDocumentsChecked: documentsBefore.length, requests: requests.length }, null, 2));
} catch (error) {
  console.error(`FAIL ${phase}: ${error.stack ?? error}`);
  const dom = page && !page.isClosed() ? await page.locator("body").innerText().catch(() => "unavailable") : "not opened";
  console.error(JSON.stringify({ errors, consoleErrors, dom: dom.slice(0, 8000),
    requests: requests.slice(-30).map(({ method, path, query, status, response }) => ({ method, path, query, status,
      failure: status >= 400 ? response : undefined })) }, null, 2));
  process.exitCode = 1;
}
finally {
  closing = true;
  await browser?.close();
  await vite?.close();
  http.closeAllConnections();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  await fixture.close();
}
