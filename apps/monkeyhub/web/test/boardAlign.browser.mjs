/** Board smart alignment (#615): pages snap while moving, and a new page joins the row of an existing one.
 *
 * Real Board, save queue and Excalidraw. Only the fixture entry, the project API (answered in memory) and a
 * read handle on Excalidraw's public API are injected; no component stub. Page previews are real PNGs.
 */
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { crc32, deflateSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "board-align-"));
const projectId = "board-align-fixture";

/** A solid-colour RGB PNG of the given size. */
function png(width, height, [r, g, b]) {
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const check = Buffer.alloc(4); check.writeUInt32BE(crc32(body) >>> 0);
    return Buffer.concat([length, body, check]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4);
  header[8] = 8; header[9] = 2; // 8-bit RGB
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x++) row.set([r, g, b], 1 + x * 3);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

const documentOf = (runId, letter, fileName, width, height) => ({ projectId, runId, assetSha256: letter.repeat(64),
  revisionRef: null, fileName, mimeType: "image/png", sizeBytes: 1, pageCount: 1,
  pages: [{ pageIndex: 0, width, height, rotation: 0 }], modelSource: null, modelSourceBindingRef: null, sourceStageRef: null });
const landscape = documentOf("documents-a", "a", "Plan.png", 600, 400);
const portrait = documentOf("documents-b", "b", "Section.png", 400, 600);
const rasters = new Map([[landscape.assetSha256, png(600, 400, [70, 110, 200])], [portrait.assetSha256, png(400, 600, [200, 120, 60])]]);
const sourceOf = (document) => ({ runId: document.runId, assetSha256: document.assetSha256, revisionRef: null, pageIndex: 0 });
const documentKey = (document) => JSON.stringify([document.runId, document.revisionRef ?? document.assetSha256]);
// One page already on the board, in its frame, as Board places one: the anchor of the row.
const anchor = { x: 100, y: 200, width: 300, height: 200 };
const board = { projectId, title: "Alignment board", revisionSha256: "0".repeat(64),
  seenDocuments: [documentKey(landscape), documentKey(portrait)],
  elements: [
    { id: "frame-a", type: "frame", x: 90, y: 190, width: 320, height: 220, angle: 0, name: "Plan.png · 1/1",
      strokeColor: "#bbb", backgroundColor: "transparent", fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid",
      roughness: 0, opacity: 100, groupIds: [], frameId: null, roundness: null, seed: 1, version: 1, versionNonce: 1,
      isDeleted: false, boundElements: null, updated: 1, link: null, locked: false },
    { id: "page-a", type: "image", ...anchor, angle: 0, fileId: "file-a", status: "saved", scale: [1, 1], crop: null,
      customData: { sourceDocument: sourceOf(landscape) }, strokeColor: "transparent", backgroundColor: "transparent",
      fillStyle: "solid", strokeWidth: 2, strokeStyle: "solid", roughness: 0, opacity: 100, groupIds: [], frameId: "frame-a",
      roundness: null, seed: 2, version: 1, versionNonce: 2, isDeleted: false, boundElements: null, updated: 1, link: null, locked: false },
  ] };

let vite, browser;
const http = createHttpServer();
const escaped = [], errors = [], writes = [];
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "real-board-align-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/test/workspace-fixture.tsx`) return { code: `
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, CaptureUpdateAction } from "@excalidraw/excalidraw";
        import Board from "/src/workspaces/monkeyboard/Board";
        import { UserPreferencesProvider } from "/test/TestProviders.tsx";
        import "/src/app/styles.css";
        window.__boardHelpers = { convertToExcalidrawElements, CaptureUpdateAction };
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider>
          <Board expectedProjectId=${JSON.stringify(projectId)}
            onSubmit={() => { throw new Error("Unexpected design handoff"); }}
            onSketch={() => { throw new Error("Unexpected sketch handoff"); }} />
        </UserPreferencesProvider>);
      `, map: null };
      if (filename === `${root}/src/workspaces/monkeyboard/Board.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; }}";
        assert.equal(source.split(callback).length, 2, "Expose the existing Excalidraw callback exactly once");
        return { code: source.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; window.__boardApi = api; }}"), map: null };
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
    localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "en" }));
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
    if (method === "GET" && url.pathname === "/api/documents") return route.fulfill({ json: { projectId, documents: [landscape, portrait] } });
    if (method === "GET" && url.pathname === "/api/projections/pages") {
      const bytes = rasters.get(url.searchParams.get("assetSha256"));
      return bytes ? route.fulfill({ status: 200, contentType: "image/png", body: bytes })
        : route.fulfill({ status: 404, json: { code: "FIXTURE_NO_PAGE", detail: "No such page" } });
    }
    // No saved marks on these pages: Board places the page itself.
    if (method === "GET" && url.pathname === "/api/document-annotations") return route.fulfill({ status: 404, json: { code: "NOT_FOUND", detail: "No marks" } });
    if (method === "PUT" && url.pathname === "/api/board") {
      const body = request.postDataJSON(); writes.push(body);
      return route.fulfill({ json: { projectId, title: body.title, elements: body.elements, seenDocuments: body.seenDocuments,
        revisionSha256: String(++revision).padStart(64, "0") } });
    }
    escaped.push(`${method} ${url.pathname}`);
    return route.fulfill({ status: 405, json: { detail: "Unexpected fixture request" } });
  });

  await page.goto(origin);
  await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));
  assert.equal(await page.evaluate(() => window.__boardApi.getAppState().objectsSnapModeEnabled), true, "the Board snaps to objects by default");
  console.log("PASS the Board opens with object snapping on");

  // A new page joins the anchor's row: its top and height, its own aspect, past the anchor's frame by the usual gap.
  await page.locator("button[aria-controls=monkeyboard-project-documents]").click();
  await page.locator(".monkeyboard-page-row").filter({ has: page.locator("select[aria-label^='Section.png']") }).getByRole("button", { name: "Add page" }).click();
  await page.waitForFunction(() => window.__boardApi.getSceneElements().filter((element) => element.type === "image").length === 2);
  const added = await page.evaluate(() => window.__boardApi.getSceneElements().find((element) => element.type === "image" && element.id !== "page-a"));
  assert.equal(added.y, anchor.y, "the new page takes the anchor's top edge");
  assert.equal(added.height, anchor.height, "and its height");
  assert.ok(Math.abs(added.width - anchor.height * 400 / 600) < 0.01, `keeping its own aspect (${added.width})`);
  assert.equal(added.x, 90 + 320 + 96 + 10, "beside the anchor's frame by the usual gap");
  const addedFrame = await page.evaluate((id) => window.__boardApi.getSceneElements().find((element) => element.id === id), added.frameId);
  assert.equal(addedFrame.y, 190, "its frame lines up with the anchor's frame");
  console.log("PASS a new page joins the row of the page already on the board");
  const shots = process.env.BOARD_ALIGN_SCREENSHOTS;
  if (shots) {
    await page.evaluate(() => window.__boardApi.scrollToContent(undefined, { fitToContent: true, animate: false }));
    await page.screenshot({ path: path.join(shots, "board-row.png") });
  }

  // Snapping while moving (filled marks, so a press inside one picks it up): a mark dragged to within a few pixels of another's top edge lands on it; Ctrl places freely.
  await page.evaluate(() => {
    const { convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers, api = window.__boardApi;
    api.updateScene({ elements: [...api.getSceneElementsIncludingDeleted(), ...convertToExcalidrawElements([
      { type: "rectangle", id: "still", x: 2000, y: 1000, width: 100, height: 80, roughness: 0, backgroundColor: "#ced4da", fillStyle: "solid" },
      { type: "rectangle", id: "moving", x: 2300, y: 1100, width: 100, height: 80, roughness: 0, backgroundColor: "#ced4da", fillStyle: "solid" },
    ], { regenerateIds: false })], appState: { scrollX: -1900, scrollY: -900, zoom: { value: 1 }, selectedElementIds: {} },
    captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  });
  const drag = async (dy, ctrl) => {
    const view = await page.evaluate(() => { const state = window.__boardApi.getAppState();
      return { scrollX: state.scrollX, scrollY: state.scrollY, zoom: state.zoom.value, left: state.offsetLeft, top: state.offsetTop }; });
    const screen = (x, y) => [(x + view.scrollX) * view.zoom + view.left, (y + view.scrollY) * view.zoom + view.top];
    const moving = await page.evaluate(() => window.__boardApi.getSceneElements().find((element) => element.id === "moving"));
    const [sx, sy] = screen(moving.x + moving.width / 2, moving.y + moving.height / 2);
    await page.mouse.move(sx, sy); await page.mouse.down();
    await page.mouse.move(sx, sy + dy / 8);
    // Ctrl held once the drag is under way, as a person holds it to place freely.
    if (ctrl) await page.keyboard.down("Control");
    for (let step = 2; step <= 8; step++) await page.mouse.move(sx, sy + dy * step / 8);
    if (shots && !ctrl) await page.screenshot({ path: path.join(shots, "board-snap-guides.png") });
    await page.mouse.up();
    if (ctrl) await page.keyboard.up("Control");
    return await page.evaluate(() => window.__boardApi.getSceneElements().find((element) => element.id === "moving"));
  };
  const snapped = await drag(-96, false);
  assert.equal(snapped.y, 1000, "dragged to 4 px below the other mark's top, it snaps onto it");
  assert.equal(snapped.x, 2300, "and does not move sideways");
  await page.keyboard.press("Control+Z");
  await page.waitForFunction(() => window.__boardApi.getSceneElements().find((element) => element.id === "moving").y === 1100);
  const free = await drag(-96, true);
  assert.equal(free.y, 1004, "holding Ctrl places it exactly where it was dropped");
  console.log("PASS moving snaps to another element's edge, and Ctrl places freely");

  const savedPage = () => writes.some((write) => write.elements.some((element) => element.type === "image" && element.id !== "page-a"));
  for (let attempt = 0; attempt < 100 && !savedPage(); attempt++) await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(savedPage(), "the placed page was saved through the real save queue");
  assert.deepEqual(escaped, [], "No unexpected API or external service requests");
  assert.deepEqual(errors, [], "No page errors");
  console.log("PASS Board smart alignment");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
