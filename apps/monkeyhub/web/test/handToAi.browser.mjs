/** S08: one 「交给 AI」 on the Board's selection.
 *
 * Real Board, save queue and Excalidraw inside the project's hand-to-AI context, as ProjectWorkspace gives it.
 * The context records what it is handed instead of posting to a conversation; the project API is answered in
 * memory. A circle and a sentence over a drawing are selected and handed over: the sentence is on the first
 * circle, the picture and the covered drawing go along, the marks leave the board, and the screen shows no
 * service words. The older design-feedback and image-discussion entries are not on the ordinary screen.
 */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { crc32, deflateSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { cmd } from "./commandLocator.mjs";
import { assertPlainScreen } from "./plainScreen.mjs";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "hand-to-ai-"));
const projectId = "hand-to-ai-fixture";

function png(width, height, [r, g, b]) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const check = Buffer.alloc(4); check.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, check]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 2;
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set([r, g, b], 1 + x * 3);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

const plan = { projectId, runId: "documents-a", assetSha256: "a".repeat(64), revisionRef: null, fileName: "一层平面.png",
  mimeType: "image/png", sizeBytes: 1, pageCount: 1, pages: [{ pageIndex: 0, width: 600, height: 400, rotation: 0 }],
  modelSource: null, modelSourceBindingRef: null, sourceStageRef: null };
const raster = png(600, 400, [70, 110, 200]);
const source = { runId: plan.runId, assetSha256: plan.assetSha256, revisionRef: null, pageIndex: 0 };
const base = { angle: 0, strokeColor: "transparent", backgroundColor: "transparent", fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid",
  roughness: 0, opacity: 100, groupIds: [], roundness: null, version: 1, versionNonce: 1, isDeleted: false, boundElements: null,
  updated: 1, link: null, locked: false };
const board = { projectId, title: "交给 AI", revisionSha256: "0".repeat(64), seenDocuments: [JSON.stringify([plan.runId, plan.assetSha256])],
  elements: [
    { ...base, id: "frame-a", type: "frame", x: 90, y: 190, width: 320, height: 220, name: "一层平面.png · 1/1", strokeColor: "#bbb", frameId: null, seed: 1 },
    { ...base, id: "page-a", type: "image", x: 100, y: 200, width: 300, height: 200, fileId: "file-a", status: "saved", scale: [1, 1], crop: null,
      customData: { sourceDocument: source }, frameId: "frame-a", seed: 2 },
  ] };
const sentence = "这面墙往外推一米";

let vite, browser;
const http = createHttpServer();
const escaped = [], errors = [];
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "hand-to-ai-fixture", enforce: "pre", transform(code, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/test/workspace-fixture.tsx`) return { code: `
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, CaptureUpdateAction } from "@excalidraw/excalidraw";
        import Board from "/src/workspaces/monkeyboard/Board";
        import { HandToAiContext } from "/src/features/handToAi/handToAi";
        import { UserPreferencesProvider } from "/test/TestProviders.tsx";
        import "/src/app/styles.css";
        window.__boardHelpers = { convertToExcalidrawElements, CaptureUpdateAction };
        window.__handed = [];
        const handOver = async (request) => {
          const files = await Promise.all(request.files.map(async (file) => ({ name: file.name, type: file.type, size: file.size,
            text: file.type === "application/json" ? await file.text() : null })));
          window.__handed.push({ ...request, files });
          return { chatId: "chat-1" };
        };
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider>
          <HandToAiContext.Provider value={handOver}>
            <Board expectedProjectId=${JSON.stringify(projectId)} onRenderChatRequest={() => { throw new Error("Unexpected image discussion"); }}
              onSubmit={() => { throw new Error("Unexpected design handoff"); }}
              onSketch={() => { throw new Error("Unexpected sketch handoff"); }} />
          </HandToAiContext.Provider>
        </UserPreferencesProvider>);
      `, map: null };
      if (filename === `${root}/src/workspaces/monkeyboard/Board.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; }}";
        assert.equal(code.split(callback).length, 2, "Expose the existing Excalidraw callback exactly once");
        return { code: code.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; window.__boardApi = api; }}"), map: null };
      }
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "zh-CN" }));
    window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`;
  });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    escaped.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const page = await context.newPage(); page.setDefaultTimeout(15_000);
  page.on("pageerror", (error) => errors.push(error.message));
  let revision = 0;
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (method === "GET" && url.pathname === "/api/board") return route.fulfill({ json: board });
    if (method === "GET" && url.pathname === "/api/documents") return route.fulfill({ json: { projectId, documents: [plan] } });
    if (method === "GET" && url.pathname === "/api/projections/pages") return route.fulfill({ status: 200, contentType: "image/png", body: raster });
    if (method === "GET" && url.pathname === "/api/document-annotations") return route.fulfill({ status: 404, json: { code: "NOT_FOUND", detail: "No marks" } });
    if (method === "GET" && url.pathname === "/api/representation-status") return route.fulfill({ status: 404, json: { code: "NOT_FOUND", detail: "No status" } });
    if (method === "PUT" && url.pathname === "/api/board") {
      const body = request.postDataJSON();
      return route.fulfill({ json: { projectId, title: body.title, elements: body.elements, seenDocuments: body.seenDocuments,
        revisionSha256: String(++revision).padStart(64, "0") } });
    }
    escaped.push(`${method} ${url.pathname}`);
    return route.fulfill({ status: 405, json: { detail: "Unexpected fixture request" } });
  });

  await page.goto(origin);
  await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));

  // A circle over the drawing and a sentence beside it, selected together with the drawing they are about.
  await page.evaluate((sentence) => {
    const { convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers, api = window.__boardApi;
    api.updateScene({ elements: [...api.getSceneElementsIncludingDeleted(), ...convertToExcalidrawElements([
      { type: "ellipse", id: "circle-1", x: 180, y: 250, width: 90, height: 70, strokeColor: "#e03131" },
      { type: "text", id: "note-1", x: 280, y: 240, text: sentence, strokeColor: "#e03131" },
    ], { regenerateIds: false })], captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    api.updateScene({ appState: { selectedElementIds: { "circle-1": true, "note-1": true, "page-a": true } }, captureUpdate: CaptureUpdateAction.NEVER });
  }, sentence);
  const overlay = page.locator(".monkeyboard-context");
  const button = cmd(overlay, "markup.hand_to_ai.hand_over");
  await button.waitFor();
  assert.equal(await button.innerText(), "交给 AI");
  assert.equal(await overlay.locator(".hand-to-ai__words").getAttribute("placeholder"), sentence, "the sentence already on the board is what goes, unless another is typed");
  for (const old of ["markup.hand_to_ai.open", "markup.hand_to_ai.discuss", "view.open_document.link_model"]) {
    assert.equal(await cmd(page, old).count(), 0, `${old} is not on the ordinary screen`);
  }
  await page.locator(".monkeyboard-actions summary").click();
  assert.equal(await cmd(page.locator(".monkeyboard-actions-panel"), "markup.hand_to_ai.open").count(), 0, "nor in More");
  await page.locator(".monkeyboard-actions summary").click();
  await assertPlainScreen(page, "selection with 交给 AI");
  console.log("PASS the selection offers one 交给 AI and none of the older entries");

  await button.click();
  await page.waitForFunction(() => window.__handed.length === 1);
  const [handed] = await page.evaluate(() => window.__handed);
  assert.equal(handed.projectId, projectId);
  assert.equal(handed.surface, "board");
  assert.equal(handed.words, sentence);
  assert.equal(handed.marks, 1, "one circle; the sentence is not counted as a mark");
  assert.deepEqual(handed.files.map((file) => [file.name, file.type]),
    [["hand-to-ai-board-picture.png", "image/png"], ["hand-to-ai-board-marks.json", "application/json"]]);
  assert.ok(handed.files[0].size > 1000, "the picture has content");
  assert.ok(handed.thumbnail?.startsWith("data:image/jpeg"), "a small picture for History");
  const marks = JSON.parse(handed.files[1].text);
  assert.equal(marks.kind, "monkeyhub.hand_to_ai");
  assert.equal(marks.words, sentence);
  assert.equal(marks.marks[0].id, "circle-1");
  assert.equal(marks.marks[0].note, sentence, "the sentence hangs on the first circle");
  assert.equal(marks.marks.filter((mark) => mark.note).length, 1, "and only there");
  const drawing = marks.objects.find((object) => object.id === "page-a");
  assert.equal(drawing?.page?.fileName, "一层平面.png", "the drawing under the circle goes with it");
  console.log("PASS the sentence, the circle, the covered drawing and a picture are handed over");

  await page.waitForFunction(() => !window.__boardApi.getSceneElements().some((element) => element.id === "circle-1" || element.id === "note-1"));
  assert.ok(await page.evaluate(() => window.__boardApi.getSceneElements().some((element) => element.id === "page-a")), "the drawing stays");
  await page.getByText("已交给 AI").waitFor();
  await assertPlainScreen(page, "after 交给 AI");
  await page.locator(".excalidraw").first().focus();
  await page.keyboard.press("Control+Z");
  await page.waitForFunction(() => window.__boardApi.getSceneElements().some((element) => element.id === "circle-1"));
  console.log("PASS the marks leave the board once handed over, and Ctrl+Z brings them back");

  assert.deepEqual(escaped, [], "No unexpected API or external service requests");
  assert.deepEqual(errors, [], "No page errors");
  console.log("PASS 交给 AI on the Board");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
}
