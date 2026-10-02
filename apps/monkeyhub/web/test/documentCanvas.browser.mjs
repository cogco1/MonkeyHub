/** Full document-page regression through the current Hub workspace -> Board ->
 * exact-page editor. All bytes and project writes belong to this disposable
 * fixture; no existing app, project, provider or document directory is used.
 * Run after npm run sync: node test/documentCanvas.browser.mjs (real Chrome).
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../..");
const apiRoot = path.join(repoRoot, "services/project-runtime");
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1",
  PYTHONPATH: [repoRoot, path.join(apiRoot, "src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
const root = await mkdtemp(path.join(tmpdir(), "monkeyhub-document-canvas-"));
const fixtureRoot = root;
const projectDir = path.join(root, "demo-project");
const screenshotPath = process.env.DOCUMENT_SCREENSHOT;
const nonce = randomUUID();
const runId = "run-001";
const http = createHttpServer();
let api, vite, browser, context, page, appUrl, apiUrl, modelSource, earlierDocument;
let closing = false, apiLog = "", assetSha;
let currentStep = "fixture startup";
const passed = [], errors = [], requests = [];
const heldReads = new Set();
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function apiCall(method, route, body) {
  const response = await fetch(apiUrl + route, { method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  assert.ok(response.ok, `${method} ${route}: ${response.status} ${JSON.stringify(result)}`);
  return result;
}

async function setup() {
  const fixture = spawnSync(python, ["-c", `
import hashlib, json, sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject
from tools.dev import source_roots
source_roots.put_first(Path(sys.argv[2]))
import archflow
from tests.support import make_project, retain_rhino_receipt, runner_state_digest
assert Path(archflow.__file__).resolve() == Path(sys.argv[2], "packages/archflow/src/archflow/__init__.py").resolve()
root = Path(sys.argv[1])
repository, _ = make_project(root)
digest = runner_state_digest(repository, "run-001")
# Reuse the retained native-source fixture used by the annotation API suite.
model = Path("tests/fixtures/model-source-a.3dm").read_bytes()
with patch("tests.support.RHINO_DESIGN_STATE_DIGEST", digest):
    retain_rhino_receipt(repository, repository.load_run("run-001"),
                        stage_id="document-source", file_name="source.3dm", payload_bytes=model)
# The second page's 600x500 CropBox rotates to a visible 500x600 surface.
# Generate with declared Runtime dependencies, without importing TestClient.
writer = PdfWriter()
for width, height in ((400, 300), (800, 600)):
    page = writer.add_blank_page(width=width, height=height)
    content = DecodedStreamObject()
    content.set_data(b"0 0 1 RG 4 w 120 80 220 160 re S\\n")
    page[NameObject("/Contents")] = writer._add_object(content)
writer.pages[1].cropbox = RectangleObject([100, 50, 700, 550])
writer.pages[1].rotate(90)
writer.write(root / "two-page-crop-rotation.pdf")
image = Image.new("RGB", (120, 80), "blue")
image.save(root / "reference.png")
exif = Image.Exif(); exif[274] = 6
image.save(root / "reference-exif.jpg", exif=exif)
print(json.dumps({"runId": "run-001", "stateDigest": digest, "assetSha256": hashlib.sha256(model).hexdigest()}))
`, root, repoRoot], { cwd: apiRoot, env: pythonEnv, encoding: "utf8" });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  modelSource = JSON.parse(fixture.stdout.trim());
  const apiPort = await new Promise(resolve => {
    const probe = createHttpServer();
    probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
  apiUrl = `http://127.0.0.1:${apiPort}`;
  api = spawn(python, ["-m", "project_runtime.main", "--port", String(apiPort), "--project-dir", projectDir], {
    cwd: apiRoot, env: { ...pythonEnv, ARCHFLOW_STUDIO_CAD_EXPORT: "off", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic",
      ARCHFLOW_STUDIO_CACHE_DIR: path.join(root, "cache") }, stdio: ["ignore", "pipe", "pipe"],
  });
  api.on("error", error => errors.push(error.message));
  api.stdout.on("data", chunk => { apiLog = (apiLog + chunk).slice(-8000); });
  api.stderr.on("data", chunk => { apiLog = (apiLog + chunk).slice(-8000); });
  for (let attempt = 0; attempt < 3000; attempt++) {
    if (await fetch(`${apiUrl}/api/health`).then(response => response.ok).catch(() => false)) break;
    assert.ok(attempt < 2999 && api.exitCode === null && errors.length === 0, `Runtime startup failed: ${apiLog} ${errors}`);
    await delay(100);
  }
  const project = await apiCall("GET", "/api/project");
  assert.equal(project.projectId, "demo-project");
  assert.equal(path.resolve(project.projectDir), path.resolve(projectDir), "Only this invocation's disposable project may be written");
  earlierDocument = await apiCall("POST", "/api/documents", { projectId: project.projectId, runId,
    fileName: "earlier-source.pdf", mimeType: "application/pdf",
    contentBase64: (await readFile(path.join(root, "two-page-crop-rotation.pdf"))).toString("base64"), modelSource });
  await apiCall("PUT", "/api/document-annotations", { projectId: project.projectId, runId,
    assetSha256: earlierDocument.assetSha256, pageIndex: 0, baseRevisionSha256: null,
    annotations: [{ id: "earlier-source-mark", kind: "line", points: [[0.1, 0.2], [0.8, 0.2]], color: "#2277dd", lineWidth: 0.004 }],
    comment: "Keep this earlier source unchanged." });

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", publicDir: ".generated/public", cacheDir: path.join(root, "vite-cache"),
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [react(), workspaceFixture()],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, answer => {
      // SSE has no natural end. A closed browser response must release its
      // upstream reader so the owned Runtime can drain and exit normally.
      response.once("close", () => answer.destroy());
      if (response.destroyed) { answer.destroy(); return; }
      answer.on("error", error => { if (!closing && !response.destroyed) errors.push(error.message); response.destroy(); });
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    response.once("close", () => proxied.destroy());
    proxied.on("error", error => {
      if (!closing && !response.destroyed) {
        errors.push(error.message);
        if (!response.headersSent) response.writeHead(502);
        response.end();
      }
    });
    request.pipe(proxied);
  });
  await new Promise(resolve => http.listen(0, "127.0.0.1", resolve));
  appUrl = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  context = await browser.newContext({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 2, locale: "zh-CN" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route(url => ["http:", "https:"].includes(url.protocol) && url.origin !== appUrl, route => {
    errors.push(`External request: ${route.request().url()}`); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); watch(page);
}

async function openBoard() {
  await page.goto(`${appUrl}/?view=board&embedded=tool&lang=zh-CN`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  await page.getByRole("button", { name: "项目资料", exact: true }).click();
}
async function openBoardPage(fileName) {
  const source = page.locator(".monkeyboard-source").filter({ has: page.getByRole("heading", { name: fileName, exact: true }) });
  await source.locator(".monkeyboard-source-link").click();
  await page.locator('.document-workspace input[type="file"]').waitFor({ state: "attached" });
  assert.equal(new URL(page.url()).searchParams.get("view"), "board", "The page opens within the current Board workspace");
}

function watch(target) {
  target.setDefaultTimeout(30_000);
  target.on("pageerror", (error) => errors.push(String(error)));
  target.on("request", (request) => {
    if (request.url().includes("/api/") && ["PUT", "POST"].includes(request.method())) {
      requests.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
    }
  });
}
async function step(name, action) {
  currentStep = name;
  await action();
  passed.push(name);
  console.log(`PASS ${name}`);
}
const near = (actual, expected, tolerance = 0.003) => assert.ok(Math.abs(actual - expected) <= tolerance,
  `expected ${actual} within ${tolerance} of ${expected}`);
async function until(read, accepts, label) {
  const end = Date.now() + 15_000;
  let last;
  do {
    last = await read();
    if (accepts(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 60));
  } while (Date.now() < end);
  assert.fail(`${label}: ${JSON.stringify(last)}`);
}
const tool = (name) => page.locator(".document-tools").getByRole("button", { name, exact: true });
const count = () => page.locator("[data-saved-ink] [data-stroke-id]").count();
const paths = () => page.locator("[data-saved-ink] [data-stroke-id]").evaluateAll((nodes) => nodes.map((node) => ({
  id: node.dataset.strokeId, d: node.getAttribute("d"), width: node.getAttribute("stroke-width"),
})));
async function ready(width, height) {
  if (width && height) await page.waitForFunction(([w, h]) => document.querySelector(".document-page__ink")?.getAttribute("viewBox") === `0 0 ${w} ${h}`, [width, height]);
  await page.locator('.document-viewport[data-ready="true"]').waitFor();
  await until(() => tool("画笔").isEnabled(), Boolean, "page annotation state loaded");
}
async function rect() {
  const box = await page.locator(".document-page").boundingBox();
  assert.ok(box, "visible document page has bounds");
  return box;
}
async function coords([x, y]) {
  const box = await rect();
  return [box.x + x * box.width, box.y + y * box.height];
}
async function draw(points, { button = "left", expectedCount = null } = {}) {
  assert.equal(await page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), assetSha,
    "the uploaded source must remain selected before drawing");
  const before = await count();
  const [x, y] = await coords(points[0]);
  await page.mouse.move(x, y); await page.mouse.down({ button });
  for (const point of points.slice(1)) {
    const [mx, my] = await coords(point); await page.mouse.move(mx, my);
  }
  await page.mouse.up({ button });
  if (expectedCount !== null) await until(count, (value) => value === expectedCount, "stroke count");
  else if (button === "left") await until(count, (value) => value === before + 1, "new stroke committed");
}
async function snapshot(pageIndex, revisionSha256) {
  const query = new URLSearchParams({ runId, assetSha256: assetSha, pageIndex: String(pageIndex) });
  if (revisionSha256) query.set("revisionSha256", revisionSha256);
  const response = await context.request.get(`${apiUrl}/api/document-annotations?${query}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function saved(pageIndex, expectedCount) {
  await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
  return until(() => snapshot(pageIndex), (value) => value.revisionSha256 && value.annotations.length === expectedCount,
    `page ${pageIndex + 1} persisted ${expectedCount} strokes`);
}
async function selectPage(index, width, height) {
  await page.getByRole("combobox", { name: "页码", exact: true }).selectOption(String(index));
  await ready(width, height);
}

try {
  await setup();
  let page0Snapshot;
  let page0Paths;
  let page1Snapshot;
  let originalSource;
  let originalAnnotations;
  await step("Board page editor opens a unique two-page PDF from the actual file input", async () => {
    await openBoard();
    // Hold every pre-upload editor list: startup can change the editing model
    // and ask again. Even the newest pre-upload response must arrive late.
    // Preserve actual response bytes; only their delivery is delayed.
    let initialList = null, releaseList, uploadStarted = false;
    let heldLists = 0, deliveredLists = 0;
    const released = new Promise((resolve) => { releaseList = resolve; });
    heldReads.add(releaseList);
    await page.route(/\/api\/documents(?:\?|$)/, async (route) => {
      if (route.request().method() === "POST") uploadStarted = true;
      if (route.request().method() !== "GET" || uploadStarted || new URL(route.request().url()).searchParams.get("runId") !== runId) {
        await route.continue(); return;
      }
      heldLists += 1;
      const response = await route.fetch();
      initialList = await response.json();
      await released;
      await route.fulfill({ response });
      deliveredLists += 1;
    });
    await openBoardPage(earlierDocument.fileName);
    const originalList = await until(() => initialList, Boolean, "initial editor list captured before upload");
    assert.ok(originalList.documents.length > 0, "the isolated fixture must include an earlier source for the list/upload regression");
    originalSource = originalList.documents.find(item => item.assetSha256 === earlierDocument.assetSha256);
    assert.ok(originalSource, "The delayed response contains this fixture's earlier registered source");
    if (originalSource) {
      const query = new URLSearchParams({ runId, assetSha256: originalSource.assetSha256, pageIndex: "0" });
      originalAnnotations = await (await context.request.get(`${apiUrl}/api/document-annotations?${query}`)).json();
    }
    const source = await readFile(`${fixtureRoot}/two-page-crop-rotation.pdf`);
    const upload = page.waitForResponse((response) => response.url().endsWith("/api/documents") && response.request().method() === "POST");
    await page.locator('.document-workspace input[type="file"]').setInputFiles({
      name: `document-functional-${nonce}.pdf`, mimeType: "application/pdf",
      buffer: Buffer.concat([source, Buffer.from(`\n% document-functional-${nonce}\n`)]),
    });
    const response = await upload;
    assert.equal(response.status(), 201);
    const document = await response.json();
    assetSha = document.assetSha256;
    assert.notEqual(assetSha, originalSource.assetSha256, "The upload has a distinct content identity");
    assert.equal(document.runId, runId);
    assert.deepEqual(document.pages.map(({ width, height, rotation }) => [width, height, rotation]), [[400, 300, 0], [500, 600, 90]]);
    await until(() => page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), (value) => value === assetSha, "uploaded source selected");
    assert.equal(uploadStarted, true);
    releaseList();
    await until(() => deliveredLists, value => value === heldLists && value > 0, "every held pre-upload list was delivered");
    heldReads.delete(releaseList);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), assetSha,
      "late initial list cannot revert the just-uploaded source");
    const sourceSelect = page.getByRole("combobox", { name: "源文件", exact: true });
    const originalShas = originalList.documents.map((item) => item.assetSha256);
    const available = await until(() => sourceSelect.locator("option").evaluateAll((options) => options.map((option) => ({
      value: option.value, disabled: option.disabled,
    }))), (options) => originalShas.every((sha) => options.some((option) => option.value === sha)),
    "all earlier source options are restored after the upload");
    assert.ok(available.some((option) => option.value === assetSha), "the uploaded source remains in the refreshed list");
    assert.ok(available.filter((option) => originalShas.includes(option.value)).every((option) => !option.disabled),
      "earlier source options remain selectable");
    assert.equal(await sourceSelect.inputValue(), assetSha, "refresh preserves the uploaded selection");
    await sourceSelect.selectOption(originalSource.assetSha256);
    await ready(originalSource.pages[0].width, originalSource.pages[0].height);
    assert.equal(await count(), originalAnnotations.annotations.length, "an earlier source still opens its retained ink");
    await sourceSelect.selectOption(assetSha);
    await ready(400, 300);
    assert.equal(await count(), 0);
    assert.equal(requests.filter((request) => request.url.includes("document-annotations")).length, 0,
      "switching to the earlier source and back does not save or alter either page");
    const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
    near(raster.width / raster.height, 4 / 3, 0.005);
  });

  await step("single points, short strokes and curves persist page coordinates and SVG geometry", async () => {
    await tool("画笔").click();
    await draw([[0.15, 0.18]]);
    const shortStart = await coords([0.3, 0.18]);
    await page.mouse.move(...shortStart); await page.mouse.down();
    await page.mouse.move(shortStart[0] + 2, shortStart[1] + 1); await page.mouse.up();
    await until(count, (value) => value === 2, "short stroke retained");
    const curve = Array.from({ length: 25 }, (_, index) => [0.15 + index * 0.02, 0.42 + Math.sin(index / 3) * 0.08]);
    await draw(curve);
    page0Snapshot = await saved(0, 3);
    const writes = requests.filter((request) => request.url.includes("document-annotations"));
    assert.equal(writes.length, 3);
    assert.ok(writes.every((request) => request.body.assetSha256 === assetSha), "every stroke saves against the uploaded source");
    if (originalSource) {
      const query = new URLSearchParams({ runId, assetSha256: originalSource.assetSha256, pageIndex: "0" });
      const unchanged = await (await context.request.get(`${apiUrl}/api/document-annotations?${query}`)).json();
      assert.deepEqual(unchanged, originalAnnotations, "the earlier source remains unchanged");
    }
    assert.equal(page0Snapshot.annotations[0].points.length, 1);
    assert.equal(page0Snapshot.annotations[1].points.length, 2);
    assert.ok(page0Snapshot.annotations[2].points.length >= 20);
    near(page0Snapshot.annotations[0].points[0][0], 0.15);
    near(page0Snapshot.annotations[0].points[0][1], 0.18);
    assert.ok(page0Snapshot.annotations.every((mark) => mark.kind === "freehand" && mark.lineWidth === 0.004));
    page0Paths = await paths();
    for (let index = 0; index < page0Paths.length; index += 1) {
      const points = page0Snapshot.annotations[index].points;
      const expected = `M ${points[0][0] * 400} ${points[0][1] * 300}`;
      assert.ok(page0Paths[index].d.startsWith(expected));
      assert.equal(Number(page0Paths[index].width), 1.2);
    }
  });

  await step("pointer-centred Ctrl-wheel zoom and viewport resize preserve page ink", async () => {
    const before = await rect();
    const anchor = [before.x + before.width * 0.52, before.y + before.height * 0.55];
    await page.mouse.move(...anchor); await page.keyboard.down("Control");
    await page.mouse.wheel(0, -160); await page.keyboard.up("Control");
    const after = await until(rect, (box) => box.width > before.width * 1.1, "zoom applied");
    near((anchor[0] - after.x) / after.width, 0.52, 0.002);
    near((anchor[1] - after.y) / after.height, 0.55, 0.002);
    assert.deepEqual(await paths(), page0Paths);
    await page.setViewportSize({ width: 1650, height: 1080 });
    assert.deepEqual(await paths(), page0Paths);
    await tool("适合窗口").click();
    await ready(400, 300);
  });

  await step("Space pan remains active through keyup, with subsequent ink at the displayed page point", async () => {
    const before = await rect();
    const start = await coords([0.5, 0.5]);
    await page.locator(".document-viewport").focus();
    await page.keyboard.down("Space"); await page.mouse.move(...start); await page.mouse.down();
    await page.mouse.move(start[0] + 35, start[1] + 22);
    await page.keyboard.up("Space");
    assert.equal(await page.locator(".document-viewport").getAttribute("data-tool"), "pan");
    await page.mouse.up();
    const after = await rect();
    near(after.x - before.x, 35, 0.1); near(after.y - before.y, 22, 0.1);
    assert.equal(await page.locator(".document-viewport").getAttribute("data-tool"), "freehand");
    await draw([[0.72, 0.7], [0.8, 0.7]]);
    const state = await saved(0, 4);
    near(state.annotations[3].points[0][0], 0.72); near(state.annotations[3].points[0][1], 0.7);
  });

  await step("same-frame middle pan move/up publishes the final view before the next stroke", async () => {
    await page.evaluate(() => document.querySelector(".document-viewport").addEventListener("pointerdown", (event) => { window.__documentTestPointerId = event.pointerId; }, { once: true }));
    const before = await rect(), start = await coords([0.5, 0.5]);
    await page.mouse.move(...start); await page.mouse.down({ button: "middle" });
    await page.evaluate(([x, y]) => {
      const node = document.querySelector(".document-viewport"), pointerId = window.__documentTestPointerId;
      node.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, pointerId, pointerType: "mouse", clientX: x, clientY: y, buttons: 4, button: 1 }));
      node.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId, pointerType: "mouse", clientX: x, clientY: y, buttons: 0, button: 1 }));
    }, [start[0] - 24, start[1] + 16]);
    await page.mouse.up({ button: "middle" });
    const after = await rect();
    near(after.x - before.x, -24, 0.1); near(after.y - before.y, 16, 0.1);
    await draw([[0.72, 0.82], [0.8, 0.82]]);
    const state = await saved(0, 5);
    near(state.annotations[4].points[0][0], 0.72); near(state.annotations[4].points[0][1], 0.82);
    page0Paths = await paths(); page0Snapshot = state;
  });

  await step("CropBox and 90-degree page rotation use a separate 500-by-600 ink surface", async () => {
    await selectPage(1, 500, 600);
    assert.equal(await count(), 0);
    await tool("直线").click(); await draw([[0.2, 0.3], [0.8, 0.3]]);
    await tool("箭头").click(); await draw([[0.2, 0.5], [0.7, 0.65]]);
    await tool("圈选").click(); await draw([[0.25, 0.72], [0.55, 0.9]]);
    page1Snapshot = await saved(1, 3);
    assert.deepEqual(page1Snapshot.annotations.map((mark) => mark.kind), ["line", "arrow", "circle"]);
    const first = page1Snapshot.annotations[0];
    near(first.points[0][0], 0.2); near(first.points[0][1], 0.3);
    const displayed = await paths();
    assert.equal(displayed[0].d, `M ${first.points[0][0] * 500} ${first.points[0][1] * 600} L ${first.points[1][0] * 500} ${first.points[1][1] * 600}`);
    assert.equal(Number(displayed[0].width), 2);
    const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
    near(raster.width / raster.height, 5 / 6, 0.005);
    await selectPage(0, 400, 300); assert.deepEqual(await paths(), page0Paths);
    await selectPage(1, 500, 600); assert.equal(await count(), 3);
  });

  await step("eraser fast sweep deletes one whole stroke and one Undo/Redo restores/deletes it", async () => {
    await tool("橡皮擦").click();
    assert.equal(await tool("橡皮擦").getAttribute("aria-pressed"), "true");
    assert.equal(await tool("画笔").getAttribute("aria-pressed"), "false");
    await draw([[0.5, 0.22], [0.5, 0.38]], { expectedCount: 2 });
    const remaining = await saved(1, 2);
    assert.deepEqual(remaining.annotations.map((mark) => mark.id), page1Snapshot.annotations.slice(1).map((mark) => mark.id));
    await tool("撤销").click(); await until(count, (value) => value === 3, "single undo restores erased stroke");
    assert.deepEqual((await saved(1, 3)).annotations, page1Snapshot.annotations);
    await tool("重做").click(); await until(count, (value) => value === 2, "single redo repeats erase");
    await saved(1, 2);
    await tool("撤销").click(); await until(count, (value) => value === 3, "restored page for subsequent checks");
    await saved(1, 3);
  });

  await step("pointercancel discards the live stroke and capture commits an outside-page release", async () => {
    await tool("画笔").click();
    await page.evaluate(() => document.querySelector(".document-viewport").addEventListener("pointerdown", (event) => { window.__documentTestPointerId = event.pointerId; }, { once: true }));
    const start = await coords([0.12, 0.12]);
    await page.mouse.move(...start); await page.mouse.down(); await page.mouse.move(start[0] + 12, start[1] + 8);
    await page.evaluate(() => document.querySelector(".document-viewport").dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerId: window.__documentTestPointerId })));
    await page.mouse.up();
    assert.equal(await count(), 3);
    assert.equal(await page.locator("[data-live-ink]").getAttribute("d"), "");
    const second = await coords([0.85, 0.15]);
    await page.evaluate(() => document.querySelector(".document-viewport").addEventListener("pointerdown", (event) => { window.__documentTestPointerId = event.pointerId; }, { once: true }));
    await page.mouse.move(...second); await page.mouse.down();
    assert.equal(await page.locator(".document-viewport").evaluate((node) => node.hasPointerCapture(window.__documentTestPointerId)), true);
    await page.mouse.move(1640, 1060); await page.mouse.up();
    await until(count, (value) => value === 4, "captured outside release committed");
    const state = await saved(1, 4);
    assert.ok(state.annotations[3].points.flat().every((value) => value >= 0 && value <= 1));
    assert.deepEqual(state.annotations[3].points.at(-1), [1, 1]);
  });

  await step("without pointer capture, window move/up completes the stroke exactly once", async () => {
    await page.locator(".document-viewport").evaluate((node) => { node.__originalCapture = node.setPointerCapture; node.setPointerCapture = undefined; });
    try {
      const start = await coords([0.15, 0.2]);
      await page.mouse.move(...start); await page.mouse.down();
      await page.mouse.move(1640, 1060); await page.mouse.up();
      await until(count, (value) => value === 5, "window fallback release committed");
      page1Snapshot = await saved(1, 5);
      assert.deepEqual(page1Snapshot.annotations[4].points.at(-1), [1, 1]);
      assert.equal(await page.locator(".document-viewport").getAttribute("data-active"), "false");
    } finally {
      await page.locator(".document-viewport").evaluate((node) => { node.setPointerCapture = node.__originalCapture; delete node.__originalCapture; });
    }
  });

  const comment = `检查旋转裁切页上的箭头与入口标记 ${nonce}`;
  let submittedRef;
  await step("page comment and intent submit the exact persisted document revision without 3D gestures", async () => {
    // Current product requires an explicit link to the exact editing source.
    // The fixture retains that source; selecting it does not invoke a provider.
    const modelChoice = page.getByRole("combobox", { name: "选择模型", exact: true });
    const key = JSON.stringify([modelSource.runId, modelSource.stateDigest, modelSource.assetSha256]);
    await until(() => modelChoice.locator("option").evaluateAll(options => options.map(option => option.value)),
      options => options.includes(key), "the exact retained model is available to link");
    await modelChoice.selectOption(key);
    await page.getByRole("button", { name: "关联所选模型", exact: true }).click();
    await page.locator('.document-model-source[data-model-source-status="ready"]').waitFor();
    // GH-302: there is no Save button; the comment saves itself after its typing pause.
    assert.equal(await page.getByRole("button", { name: "保存本页", exact: true }).count(), 0);
    await page.locator("#document-comment").fill(comment);
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    page1Snapshot = await until(() => snapshot(1), (value) => value.comment === comment, "comment autosaved");
    if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true });
    const intentResponse = page.waitForResponse((response) => response.url().endsWith("/api/intents") && response.request().method() === "POST");
    await page.getByRole("button", { name: "提交本页意见", exact: true }).click();
    const response = await intentResponse;
    assert.equal(response.status(), 422, "The deterministic provider asks for the absent model target");
    const result = await response.json();
    assert.equal(result.code, "BLOCKED_NEEDS_HUMAN");
    assert.equal(result.outcome, "NEEDS_CLARIFICATION");
    assert.equal(result.pendingIntent.reasonCode, "TARGET_UNRESOLVED");
    assert.equal(result.pendingIntent.originalUtterance, comment);
    assert.ok(result.pendingIntent.continuationToken);
    const body = response.request().postDataJSON();
    assert.equal(body.utterance, comment);
    assert.deepEqual(body.modelSource, modelSource);
    assert.equal(body.sourceRunId, runId);
    assert.equal(body.stateDigest, modelSource.stateDigest);
    assert.equal(body.targetComponentId, null);
    assert.equal(body.elementId, null);
    assert.equal(body.documentVisuals.length, 1);
    const [visual] = body.documentVisuals;
    assert.equal(visual.role, "edit");
    assert.ok(visual.pagePngBase64 && visual.annotatedPngBase64, "the real page and saved ink reach the Runtime");
    assert.deepEqual(body.gestures ?? [], []);
    assert.equal(body.documentAnnotations.length, 1);
    submittedRef = body.documentAnnotations[0];
    assert.deepEqual(submittedRef, { runId, assetSha256: assetSha, pageIndex: 1, revisionSha256: page1Snapshot.revisionSha256 });
    for (const field of ["runId", "assetSha256", "pageIndex", "revisionSha256"]) assert.equal(visual[field], submittedRef[field]);
    const historical = await snapshot(1, submittedRef.revisionSha256);
    assert.deepEqual(historical.annotations, page1Snapshot.annotations);
    const comments = await until(async () => (await context.request.get(`${apiUrl}/api/document-comments?runId=${runId}`)).json(),
      (result) => result.comments.some((item) => item.utterance === comment), "submitted comment retained");
    assert.deepEqual(comments.comments.find((item) => item.utterance === comment).documentAnnotations, [submittedRef]);
    await page.locator(".document-submitted summary").click();
    const article = page.locator(".document-submitted article").filter({ hasText: comment });
    await article.getByRole("button").click();
    await page.locator(".document-review-banner").waitFor();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    await until(count, (value) => value === page1Snapshot.annotations.length, "historical annotation snapshot loaded");
    assert.equal(await tool("画笔").isDisabled(), true);
    assert.equal(await page.locator("#document-comment").inputValue(), comment);
    assert.deepEqual((await paths()).map((path) => path.id), page1Snapshot.annotations.map((mark) => mark.id));
    await page.getByRole("button", { name: "返回当前批注", exact: true }).click();
    await ready(400, 300);
  });

  await step("saved pages and historical comments reopen after closing the browser page", async () => {
    await page.close(); page = await context.newPage(); watch(page);
    await openBoard(); await openBoardPage(`document-functional-${nonce}.pdf`);
    await page.getByRole("combobox", { name: "源文件", exact: true }).selectOption(assetSha);
    await ready(400, 300);
    assert.deepEqual(await paths(), page0Paths);
    await selectPage(1, 500, 600);
    assert.equal(await page.locator("#document-comment").inputValue(), comment);
    assert.deepEqual((await paths()).map((path) => path.id), page1Snapshot.annotations.map((mark) => mark.id));
    await page.locator(".document-submitted summary").click();
    await page.locator(".document-submitted article").filter({ hasText: comment }).getByRole("button").click();
    await page.locator(".document-review-banner").waitFor();
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    await until(count, (value) => value === page1Snapshot.annotations.length, "reopened historical annotation snapshot loaded");
    assert.equal(await tool("画笔").isDisabled(), true);
    await page.getByRole("button", { name: "返回当前批注", exact: true }).click();
    await ready(400, 300);
  });

  await step("PNG and EXIF-oriented JPEG render at the API visible dimensions", async () => {
    for (const fileName of ["reference.png", "reference-exif.jpg"]) {
      const upload = page.waitForResponse((response) => response.url().endsWith("/api/documents") && response.request().method() === "POST");
      await page.locator('.document-workspace input[type="file"]').setInputFiles(`${fixtureRoot}/${fileName}`);
      const response = await upload;
      assert.ok([200, 201].includes(response.status()));
      const document = await response.json(), [visible] = document.pages;
      await until(() => page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), (value) => value === document.assetSha256, "uploaded image selected");
      await ready(visible.width, visible.height);
      const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
      near(raster.width / raster.height, visible.width / visible.height, 0.005);
      if (fileName.endsWith(".jpg")) assert.equal(visible.width < visible.height, true, "EXIF 6 turns this landscape fixture upright");
    }
  });
  const annotationWrites = requests.filter(request => request.url.includes("document-annotations"));
  assert.ok(annotationWrites.length > 0);
  assert.ok(annotationWrites.every(request => request.body.runId === runId && request.body.assetSha256 === assetSha
    && [0, 1].includes(request.body.pageIndex)), "Every browser annotation write stays on the uploaded source and exact page");
  const originalQuery = new URLSearchParams({ runId, assetSha256: originalSource.assetSha256, pageIndex: "0" });
  assert.deepEqual(await apiCall("GET", `/api/document-annotations?${originalQuery}`), originalAnnotations,
    "The earlier source's saved ink and comment remain unchanged after every scenario");
  assert.deepEqual(await snapshot(0), page0Snapshot, "Other-page operations preserve page one's exact saved revision");
  assert.deepEqual(await snapshot(1), page1Snapshot, "Image uploads and history reads preserve page two's exact saved revision");
  assert.deepEqual(requests.filter(request => /\/api\/(proposals|candidates|jobs)(?:[/?]|$)/.test(request.url)), [],
    "An annotation intent needing clarification never generates or accepts a candidate");
  assert.deepEqual(errors, [], "no browser application exceptions");
  console.log(JSON.stringify({ passed: passed.length, assetSha256: assetSha, screenshotPath, submittedRef,
    annotationWrites: requests.filter((request) => request.url.includes("document-annotations") && request.body.assetSha256 === assetSha).length }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ failedStep: currentStep, passed, assetSha256: assetSha, browserErrors: errors,
    apiLog, selectedSource: await page?.getByRole("combobox", { name: "源文件", exact: true }).inputValue().catch(() => null),
    annotationWrites: requests.filter((request) => request.url.includes("document-annotations")).map((request) => ({
      assetSha256: request.body.assetSha256, pageIndex: request.body.pageIndex, count: request.body.annotations.length,
    })),
    visibleErrors: await page?.locator('.document-error, .document-render-error, [role="alert"]').allTextContents().catch(() => []) }, null, 2));
  throw error;
} finally {
  closing = true;
  for (const release of heldReads) release();
  await context?.close(); await browser?.close(); await vite?.close();
  if (http.listening) await new Promise(resolve => http.close(resolve));
  if (api && api.exitCode === null) {
    const exited = new Promise(resolve => api.once("exit", resolve)); api.kill(); await exited;
  }
  assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
  assert.ok(path.basename(root).startsWith("monkeyhub-document-canvas-"));
  await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
