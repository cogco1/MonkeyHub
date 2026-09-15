import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

// Real Board, Excalidraw and PDF.js. Only the app entry and the test's access to
// Excalidraw's public API are transformed; API state and document bytes stay in
// memory. No live Studio, project directory or external service is contacted.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "board-replacement-"));
const projectId = "board-replacement-fixture";
const documentKey = (source) => JSON.stringify([source.runId, source.revisionRef ?? source.assetSha256]);
const pageSource = (document, pageIndex) => ({ runId: document.runId, assetSha256: document.assetSha256,
  revisionRef: document.revisionRef ?? null, pageIndex });

// Tiny but genuine PDFs exercise PDF.js's zero/one-based page boundary. The
// first replacement is a smaller PNG so crop scaling is observable as well.
function pdfBytes(colors) {
  const objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""];
  const pages = [];
  for (const color of colors) {
    const page = objects.length + 1, content = page + 1;
    pages.push(`${page} 0 R`);
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 120] /Resources << >> /Contents ${content} 0 R >>`);
    const drawing = `${color.join(" ")} rg 0 0 200 120 re f\n`;
    objects.push(`<< /Length ${Buffer.byteLength(drawing)} >>\nstream\n${drawing}endstream`);
  }
  objects[1] = `<< /Type /Pages /Kids [${pages.join(" ")}] /Count ${pages.length} >>`;
  let text = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(text));
    text += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xref = Buffer.byteLength(text);
  text += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  text += offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  return Buffer.from(`${text}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}
const oldBytes = pdfBytes([[0.1, 0.2, 0.8], [0.8, 0.1, 0.1]]);
function sourceDocument(bytes, fileName, count, revisionRef, mimeType = "application/pdf") {
  return { projectId, runId: "source-documents", assetSha256: createHash("sha256").update(bytes).digest("hex"),
    fileName, mimeType, sizeBytes: bytes.length, pageCount: count,
    pages: Array.from({ length: count }, (_, pageIndex) => ({ pageIndex, width: 200, height: 120, rotation: 0 })),
    modelSource: null, modelSourceBindingRef: null, sourceStageRef: null, revisionRef };
}
const oldDocument = sourceDocument(oldBytes, "Original two pages.pdf", 2, "old-drawing-revision");
let replacementBytes, replacement;
const uploadBytes = pdfBytes([[0.3, 0.3, 0.3], [0.9, 0.7, 0.1]]);
let uploadedReplacement;
// The explicit editable copy of one registered single-page image.
let workCopy, workCopyRefusal;
const workCopies = [];
const oldSource = pageSource(oldDocument, 1);
const seeds = [
  { type: "image", id: "kept-image", fileId: "old-second-page", x: 80, y: 80, width: 500, height: 300,
    status: "saved", scale: [-1, 1], customData: { sourceDocument: oldSource, note: "keep this custom field" } },
  { type: "frame", id: "kept-frame", children: ["kept-image"], name: "Original two pages.pdf · 2/2" },
  { type: "image", id: "deleted-copy", fileId: "old-second-page", x: 780, y: 80, width: 250, height: 150,
    status: "saved", customData: { sourceDocument: oldSource } },
  { type: "frame", id: "deleted-frame", children: ["deleted-copy"], name: "Second copy" },
  { type: "image", id: "unmapped-image", fileId: "old-first-page", x: 80, y: 580, width: 400, height: 240,
    status: "saved", customData: { sourceDocument: pageSource(oldDocument, 0) } },
  { type: "frame", id: "unmapped-frame", children: ["unmapped-image"], name: "Unaffected first page" },
  { type: "ellipse", id: "existing-mark", x: 160, y: 145, width: 100, height: 65,
    strokeColor: "#e03131", strokeWidth: 3, roughness: 0, frameId: "kept-frame" },
];
let saved = { projectId, title: "Page replacement regression", elements: [],
  seenDocuments: [documentKey(oldDocument)], revisionSha256: "1".repeat(64) };
let seeded = false, documents = [oldDocument], documentReads = 0;
let competingVersion = false, conflicts = 0;
const writes = [], uploads = [], failures = [], escaped = [], fileReads = [];
let releasePreview;
const previewGate = new Promise((resolve) => { releasePreview = resolve; });
let previewRequested;
const previewStarted = new Promise((resolve) => { previewRequested = resolve; });
let browser, vite, page, origin;
const http = createHttpServer();

async function readScene() {
  return await page.evaluate(() => ({
    elements: window.__boardApi.getSceneElementsIncludingDeleted(),
    zoom: window.__boardApi.getAppState().zoom.value,
    scrollX: window.__boardApi.getAppState().scrollX,
    scrollY: window.__boardApi.getAppState().scrollY,
  }));
}
const byId = (scene, id) => scene.elements.find((element) => element.id === id);
const geometry = ({ x, y, width, height, angle, scale, frameId }) => ({ x, y, width, height, angle, scale, frameId });
const activeIds = (scene, type) => scene.elements.filter((element) => !element.isDeleted && element.type === type).map(({ id }) => id).sort();
// JSON omits undefined fields; Board uses Excalidraw's restored empty bindings.
const persisted = (elements) => JSON.parse(JSON.stringify(elements.map((element) => ({ ...element, boundElements: element.boundElements ?? [] }))));

try {
  vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", cacheDir,
    publicDir: ".generated/public", define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [{ name: "board-replacement-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/src/main.tsx`) return { code: `
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, newElementWith, CaptureUpdateAction, FONT_FAMILY } from "@excalidraw/excalidraw";
        import Board from "./workspaces/monkeyboard/Board";
        import { UserPreferencesProvider } from "./features/settings/preferences";
        import "./styles.css";
        window.__boardHelpers = { convertToExcalidrawElements, newElementWith, CaptureUpdateAction, FONT_FAMILY };
        window.__boardInitialElements = convertToExcalidrawElements(${JSON.stringify(seeds)}, { regenerateIds: false });
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider><Board onSubmit={() => { throw new Error("Unexpected design handoff"); }} /></UserPreferencesProvider>);
      `, map: null };
      if (filename === `${root}/src/workspaces/monkeyboard/Board.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; }}";
        assert.equal(source.split(callback).length, 2, "The test must expose the existing Excalidraw callback exactly once");
        return { code: source.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; window.__boardApi = api; }}"), map: null };
      }
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    escaped.push(route.request().url()); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  replacementBytes = Buffer.from(await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 200; canvas.height = 120;
    const context = canvas.getContext("2d"); context.fillStyle = "#1acc33"; context.fillRect(0, 0, 200, 120);
    return canvas.toDataURL("image/png").split(",")[1];
  }), "base64");
  replacement = { ...sourceDocument(replacementBytes, "Updated single page.png", 1, "new-drawing-revision", "image/png"),
    replacesPages: [{ ...oldSource, newPageIndex: 0 }] };
  uploadedReplacement = { ...sourceDocument(uploadBytes, "UI updated.pdf", 2, "ui-drawing-revision"),
    replacesPages: [{ ...pageSource(replacement, 0), newPageIndex: 1 }] };
  workCopy = { projectId, runId: replacement.runId, assetSha256: replacement.assetSha256,
    revisionRef: replacement.revisionRef, pageIndex: 0, fileName: replacement.fileName, mimeType: "image/png",
    relativePath: `runs/${replacement.runId}/workspaces/studio-documents/work/${replacement.assetSha256}/${replacement.fileName}`,
    headRunId: replacement.runId, headAssetSha256: replacement.assetSha256,
    headRevisionRef: replacement.revisionRef, headPageIndex: 0, refusal: null };
  // The old two-page PDF had one of its pages replaced on its own, so the
  // document owner has no single file that can stand for it.
  workCopyRefusal = "Pages of this document are answered for by different documents now, "
    + "so no single file can stand for it.";
  page.on("pageerror", (error) => failures.push(error.stack ?? error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url());
    try {
      assert.equal(url.origin, origin);
      if (request.method() === "GET" && url.pathname === "/api/board") {
        if (!seeded) { saved.elements = await page.evaluate(() => window.__boardInitialElements); seeded = true; }
        return await route.fulfill({ json: saved });
      }
      if (request.method() === "GET" && url.pathname === "/api/documents") {
        documentReads += 1;
        return await route.fulfill({ json: { projectId, runId: oldDocument.runId, documents } });
      }
      // A placed page is drawn as its own editor saved it; no page here is marked.
      if (request.method() === "GET" && url.pathname === "/api/document-annotations") {
        return await route.fulfill({ json: { projectId, runId: url.searchParams.get("runId"),
          assetSha256: url.searchParams.get("assetSha256"), pageIndex: Number(url.searchParams.get("pageIndex")),
          drawingRevisionRef: url.searchParams.get("drawingRevisionRef"), revisionSha256: null,
          annotations: [], comment: "" } });
      }
      if (request.method() === "GET" && url.pathname.startsWith("/api/documents/")) {
        fileReads.push({ path: url.pathname, query: Object.fromEntries(url.searchParams) });
        assert.equal(url.searchParams.get("runId"), oldDocument.runId);
        if (url.pathname === `/api/documents/${oldDocument.assetSha256}/bytes`) {
          assert.equal(url.searchParams.get("revisionRef"), oldDocument.revisionRef);
          return await route.fulfill({ contentType: "application/pdf", body: oldBytes });
        }
        if (url.pathname === `/api/documents/${uploadedReplacement.assetSha256}/bytes`) {
          assert.equal(url.searchParams.get("revisionRef"), uploadedReplacement.revisionRef);
          return await route.fulfill({ contentType: "application/pdf", body: uploadBytes });
        }
        assert.equal(url.pathname, `/api/documents/${replacement.assetSha256}/bytes`);
        assert.equal(url.searchParams.get("revisionRef"), replacement.revisionRef);
        previewRequested(); await previewGate;
        return await route.fulfill({ contentType: "image/png", body: replacementBytes });
      }
      if (request.method() === "POST" && url.pathname.endsWith("/work-copy")) {
        workCopies.push({ path: url.pathname, body: structuredClone(request.postDataJSON()) });
        if (url.pathname.includes(oldDocument.assetSha256)) {
          return await route.fulfill({ status: 422,
            json: { code: "DOCUMENT_NOT_EDITABLE", detail: workCopyRefusal } });
        }
        return await route.fulfill({ status: 201, json: workCopy });
      }
      if (request.method() === "POST" && url.pathname === "/api/documents") {
        const body = request.postDataJSON();
        assert.equal(body.projectId, projectId);
        assert.equal(body.fileName, uploadedReplacement.fileName);
        assert.equal(body.mimeType, "application/pdf");
        assert.equal(body.contentBase64, uploadBytes.toString("base64"));
        assert.deepEqual(body.replacesPages, uploadedReplacement.replacesPages,
          "The selected source page and the new file's page number must form one exact mapping");
        uploads.push(structuredClone(body)); documents = [...documents, uploadedReplacement];
        return await route.fulfill({ status: 201, json: uploadedReplacement });
      }
      assert.equal(`${request.method()} ${url.pathname}`, "PUT /api/board", "Only the scene is persisted in this test");
      const body = request.postDataJSON();
      assert.equal(body.projectId, projectId);
      if (competingVersion && body.baseRevisionSha256 !== saved.revisionSha256) {
        conflicts++;
        return await route.fulfill({ status: 409, json: { code: "BOARD_STALE", detail: "The board changed since it was opened." } });
      }
      assert.equal(body.baseRevisionSha256, saved.revisionSha256, "Every save must use the last acknowledged revision");
      writes.push(structuredClone(body));
      const { baseRevisionSha256: _base, ...scene } = body;
      saved = { ...scene, revisionSha256: createHash("sha256").update(JSON.stringify(body)).digest("hex") };
      return await route.fulfill({ json: saved });
    } catch (error) { failures.push(error.stack ?? String(error)); await route.abort("blockedbyclient"); }
  });
  await page.goto(`${origin}/?view=board&lang=en`, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Project documents", exact: true }).waitFor();
  await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));
  const original = await readScene();
  assert.equal(byId(original, "kept-image").frameId, "kept-frame");
  assert.equal(Object.hasOwn(byId(original, "kept-frame"), "children"), false,
    "The real Excalidraw frame stores membership on each image's frameId");
  assert.deepEqual(activeIds(original, "image"), ["deleted-copy", "kept-image", "unmapped-image"]);
  const select = async (ids) => page.evaluate(async (ids) => {
    const api = window.__boardApi;
    api.updateScene({ appState: { selectedElementIds: Object.fromEntries(ids.map((id) => [id, true])), selectedGroupIds: {} },
      captureUpdate: window.__boardHelpers.CaptureUpdateAction.NEVER });
    await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
  }, ids);
  const updateAction = (name = "Update this page") => page.locator(".monkeyboard-context").getByRole("button", { name, exact: true });
  const expectUpdate = async (ids, visible) => {
    await select(ids);
    await updateAction().waitFor({ state: visible ? "visible" : "hidden" });
    assert.equal(await updateAction().count(), Number(visible), `Update action selection: ${ids.join(", ") || "empty"}`);
    if (visible) assert.equal(await updateAction().isEnabled(), true);
  };
  assert.equal(await page.getByRole("button", { name: "Project documents", exact: true }).getAttribute("aria-expanded"), "false");
  for (const ids of [["kept-image"], ["kept-frame"], ["kept-image", "kept-frame"], ["kept-image", "existing-mark"]]) {
    await expectUpdate(ids, true);
  }
  for (const ids of [[], ["existing-mark"], ["kept-image", "unmapped-image"], ["kept-image", "deleted-copy"], ["kept-frame", "deleted-frame"]]) {
    await expectUpdate(ids, false);
  }
  // A native frame with two images stays ambiguous even when both name one page.
  await page.evaluate(() => {
    const api = window.__boardApi, { newElementWith, CaptureUpdateAction } = window.__boardHelpers;
    api.updateScene({ elements: api.getSceneElementsIncludingDeleted().map((element) => element.id === "deleted-copy"
      ? newElementWith(element, { frameId: "kept-frame" }) : element), captureUpdate: CaptureUpdateAction.NEVER });
  });
  await expectUpdate(["kept-frame"], false);
  // A displayed image's file bytes do not substitute for its registered source.
  await page.evaluate(() => {
    const api = window.__boardApi, { newElementWith, CaptureUpdateAction } = window.__boardHelpers;
    api.updateScene({ elements: api.getSceneElementsIncludingDeleted().map((element) => element.id === "kept-image"
      ? newElementWith(element, { customData: null }) : element), captureUpdate: CaptureUpdateAction.NEVER });
  });
  await expectUpdate(["kept-image"], false);
  await page.evaluate((elements) => window.__boardApi.updateScene({ elements,
    appState: { selectedElementIds: {}, selectedGroupIds: {} }, captureUpdate: window.__boardHelpers.CaptureUpdateAction.NEVER }), original.elements);

  documents = [oldDocument, replacement];
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("The replacement preview was never requested")), 30_000);
    previewStarted.then(() => { clearTimeout(timeout); resolve(); });
  });
  assert.deepEqual(byId(await readScene(), "kept-image").customData.sourceDocument, oldSource,
    "A delayed preview must leave the old image visible until its replacement is ready");

  // Public Excalidraw operations, applied while replacement bytes are pending.
  // Change the frame and its image together, retain one previous mark, add a
  // second mark, and delete a duplicate image/frame before rendering completes.
  await page.evaluate(async () => {
    const api = window.__boardApi;
    const { newElementWith, convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers;
    const bitmap = new Image(); bitmap.src = api.getFiles()["old-second-page"].dataURL; await bitmap.decode();
    const crop = { x: bitmap.width * 0.1, y: bitmap.height * 0.2, width: bitmap.width * 0.6,
      height: bitmap.height * 0.5, naturalWidth: bitmap.width, naturalHeight: bitmap.height };
    const elements = api.getSceneElementsIncludingDeleted().map((element) => {
      if (element.id === "kept-image") return newElementWith(element, { x: 247, y: 191, width: 650, height: 325, angle: 0.08, scale: [-1, 1], crop });
      if (element.id === "kept-frame") return newElementWith(element, { x: 225, y: 168, width: 710, height: 445 });
      if (element.id === "deleted-copy") return newElementWith(element, { isDeleted: true, crop });
      if (element.id === "deleted-frame") return newElementWith(element, { isDeleted: true });
      return element;
    });
    const marks = convertToExcalidrawElements([{ type: "line", id: "mark-during-preview", x: 310, y: 265, width: 150, height: 85,
      points: [[0, 0], [150, 85]], strokeColor: "#1971c2", strokeWidth: 4, roughness: 0, frameId: "kept-frame" }], { regenerateIds: false });
    api.updateScene({ elements: [...elements, ...marks], appState: { zoom: { value: 0.73 }, scrollX: 31, scrollY: -47 },
      captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  });
  await page.waitForFunction(() => window.__boardApi.getSceneElements().some(({ id }) => id === "mark-during-preview"));
  const edited = await readScene();
  assert.equal(byId(edited, "deleted-copy").isDeleted, true);
  releasePreview();
  await page.waitForFunction((sha) => window.__boardApi.getSceneElements().some((element) =>
    element.id === "kept-image" && element.customData?.sourceDocument?.assetSha256 === sha), replacement.assetSha256);
  const updated = await readScene();
  assert.deepEqual(byId(updated, "kept-image").customData.sourceDocument, pageSource(replacement, 0), "Old page 1 must become new page 0");
  assert.deepEqual(geometry(byId(updated, "kept-image")), geometry(byId(edited, "kept-image")), "Replacement preserves the latest image position, size, flip, angle and frame");
  const oldCrop = byId(edited, "kept-image").crop, newCrop = byId(updated, "kept-image").crop;
  assert.ok(oldCrop.naturalWidth > newCrop.naturalWidth, "The test must actually replace a larger rendered PDF with a smaller image");
  assert.deepEqual([newCrop.naturalWidth, newCrop.naturalHeight], [200, 120]);
  for (const [field, dimension] of [["x", "naturalWidth"], ["y", "naturalHeight"], ["width", "naturalWidth"], ["height", "naturalHeight"]]) {
    assert.ok(Math.abs(oldCrop[field] / oldCrop[dimension] - newCrop[field] / newCrop[dimension]) < 1e-12,
      `The visible crop's ${field} fraction must survive the changed preview resolution`);
  }
  assert.deepEqual(byId(updated, "kept-frame"), byId(edited, "kept-frame"), "Replacement never reconstructs its frame");
  assert.deepEqual(byId(updated, "existing-mark"), byId(edited, "existing-mark"));
  assert.deepEqual(byId(updated, "mark-during-preview"), byId(edited, "mark-during-preview"));
  assert.deepEqual(byId(updated, "deleted-copy"), byId(edited, "deleted-copy"), "A removed duplicate must not return");
  assert.deepEqual(byId(updated, "unmapped-image"), byId(edited, "unmapped-image"), "An unmapped page of the same PDF must not change");
  assert.deepEqual(activeIds(updated, "frame"), activeIds(edited, "frame"));
  assert.deepEqual(activeIds(updated, "image"), ["kept-image", "unmapped-image"]);
  assert.equal(updated.elements.length, edited.elements.length, "No replacement elements or frames are appended");
  assert.equal(byId(updated, "kept-image").customData.note, "keep this custom field");
  assert.deepEqual([updated.zoom, updated.scrollX, updated.scrollY], [edited.zoom, edited.scrollX, edited.scrollY], "Receiving a replacement must not refit the user's viewport");
  const previewPixel = await page.evaluate(async () => {
    const api = window.__boardApi, image = api.getSceneElements().find(({ id }) => id === "kept-image");
    const bitmap = new Image(); bitmap.src = api.getFiles()[image.fileId].dataURL; await bitmap.decode();
    const canvas = document.createElement("canvas"); canvas.width = 1; canvas.height = 1;
    const context = canvas.getContext("2d"); context.drawImage(bitmap, 0, 0, 1, 1);
    return [...context.getImageData(0, 0, 1, 1).data];
  });
  assert.ok(previewPixel[1] > 150 && previewPixel[0] < 80, `The displayed replacement must be the green new page: ${previewPixel}`);
  await page.waitForFunction(() => document.querySelector(".monkeyboard-save-state")?.textContent === "Saved");

  // A replacement the poll brought in reports itself once, politely, and offers
  // to show the page it swapped — without touching what gets saved.
  const quiet = page.locator(".monkeyboard-update");
  await quiet.waitFor();
  assert.equal(await quiet.count(), 1, "One arriving replacement produces exactly one quiet notice");
  assert.equal(await quiet.getAttribute("role"), "status", "A received update is reported politely, not as an alert");
  assert.equal(await quiet.locator("span").first().innerText(), `«${replacement.fileName}» updated`);
  assert.equal(await page.locator(".monkeyboard-alert[role=alert]").filter({ hasText: `«${replacement.fileName}» updated` }).count(), 0,
    "A received update must not use the error surface");
  const boardBeforeView = JSON.stringify(saved), writesBeforeView = writes.length;
  const beforeView = await readScene();
  await quiet.getByRole("button", { name: "View", exact: true }).click();
  await page.waitForFunction(() => window.__boardApi.getAppState().selectedElementIds["kept-image"] === true);
  assert.deepEqual(await page.evaluate(() => Object.entries(window.__boardApi.getAppState().selectedElementIds)
    .filter(([, active]) => active).map(([id]) => id).sort()), ["kept-image"], "View selects the updated page alone");
  const framed = await page.evaluate(() => {
    const api = window.__boardApi, state = api.getAppState();
    const image = api.getSceneElements().find(({ id }) => id === "kept-image");
    const left = (image.x + state.scrollX) * state.zoom.value, top = (image.y + state.scrollY) * state.zoom.value;
    return { left, top, right: left + image.width * state.zoom.value, bottom: top + image.height * state.zoom.value,
      width: state.width, height: state.height };
  });
  assert.ok(framed.left >= -2 && framed.top >= -2 && framed.right <= framed.width + 2 && framed.bottom <= framed.height + 2,
    `View must bring the updated page into view: ${JSON.stringify(framed)}`);
  // Past the 700 ms save debounce: focusing must never author a revision.
  await page.waitForTimeout(1_500);
  assert.deepEqual(persisted((await readScene()).elements), persisted(beforeView.elements), "Focusing an update changes no element");
  assert.equal(writes.length, writesBeforeView, "Focusing an update must not write the board");
  assert.equal(JSON.stringify(saved), boardBeforeView, "The saved board must be byte-identical across a View click");
  await quiet.getByRole("button", { name: "Dismiss", exact: true }).click();
  await quiet.waitFor({ state: "hidden" });

  assert.deepEqual(saved.elements, persisted(updated.elements), "The latest replacement and concurrent edits must reach the saved scene");
  assert.ok(saved.seenDocuments.includes(documentKey(replacement)), "The replacement must be marked received before saving");
  const writesBeforeReload = writes.length;
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));
  const readsBeforeRefresh = documentReads;
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/documents"),
    page.evaluate(() => window.dispatchEvent(new Event("focus"))),
  ]);
  assert.ok(documentReads > readsBeforeRefresh);
  // Cover both the existing 700 ms save debounce and the first automatic 5 s
  // discovery poll after reload; a spurious import would persist in this window.
  await page.waitForTimeout(5_200);
  const reopened = await readScene();
  assert.deepEqual(activeIds(reopened, "frame"), activeIds(updated, "frame"));
  assert.deepEqual(activeIds(reopened, "image"), activeIds(updated, "image"));
  for (const id of ["kept-image", "kept-frame", "existing-mark", "mark-during-preview", "unmapped-image"]) {
    assert.deepEqual(persisted([byId(reopened, id)]), persisted([byId(updated, id)]), `Reload preserves ${id}`);
  }
  assert.ok(!reopened.elements.some((element) => element.id === "deleted-copy" && !element.isDeleted));
  assert.equal(writes.length, writesBeforeReload, "Reload and rediscovery must not create another saved revision");

  // The source-card entry stays available, while the new selection entry also
  // works for this cropped, unbound image whose marks cannot enter feedback.
  await page.getByRole("button", { name: "Project documents", exact: true }).click();
  const originalCard = page.locator(".monkeyboard-source").filter({ has: page.getByRole("heading", { name: oldDocument.fileName, exact: true }) });
  // An editable copy is offered for every registered document. Which of them
  // can actually have one is the document owner's answer, and its refusal is
  // shown as it is rather than guessed at here.
  const workCopyName = "Get editable copy";
  const originalWorkCopy = originalCard.getByRole("button", { name: workCopyName, exact: true });
  assert.equal(await originalWorkCopy.isEnabled(), true,
    "Every registered document offers to be asked for an editable copy");
  await originalWorkCopy.click();
  const refusalNotice = page.locator(".monkeyboard-alert[role=alert]").filter({ hasText: "no single file" });
  await refusalNotice.waitFor();
  assert.match(await refusalNotice.innerText(), /different documents/,
    "The document owner's refusal reaches the operator unchanged");
  await refusalNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
  const replacementCard = page.locator(".monkeyboard-source").filter({ has: page.getByRole("heading", { name: replacement.fileName, exact: true }) });
  const workCopyButton = replacementCard.getByRole("button", { name: workCopyName, exact: true });
  assert.equal(await workCopyButton.isEnabled(), true, "A registered single-page PNG can be given an editable copy");
  await workCopyButton.click();
  const workCopyNotice = page.locator(".monkeyboard-alert[role=alert]").filter({ hasText: workCopy.relativePath });
  await workCopyNotice.waitFor();
  assert.equal(workCopies.length, 2, "Each click asks for exactly one work copy");
  assert.deepEqual(workCopies[1], { path: `/api/documents/${replacement.assetSha256}/work-copy`,
    body: { projectId, runId: replacement.runId, revisionRef: replacement.revisionRef } },
    "The work copy names the exact registration, and sends no path");
  assert.match(await workCopyNotice.innerText(), new RegExp(workCopy.relativePath.replaceAll(".", "\\.")),
    "The project-relative path of the editable copy is shown to the operator");
  await workCopyNotice.getByRole("button", { name: "Dismiss", exact: true }).click();
  await workCopyNotice.waitFor({ state: "hidden" });
  await originalCard.getByRole("combobox").selectOption("0");
  await originalCard.getByRole("button", { name: "Update this page", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Update this page", exact: true });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Project documents", exact: true }).click();
  await expectUpdate(["deleted-copy"], false);
  await expectUpdate(["kept-image"], true);
  await expectUpdate(["kept-frame"], true);
  assert.ok(byId(reopened, "kept-image").crop);
  assert.equal(replacement.modelSource, null);
  if (process.env.BOARD_REPLACEMENT_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_REPLACEMENT_SCREENSHOT });
  await updateAction().click();
  assert.match(await dialog.locator(".monkeyboard-feedback-source").innerText(), /Updated single page\.png.*1\/1/);
  await dialog.getByLabel("Updated PDF / image", { exact: true }).setInputFiles({ name: uploadedReplacement.fileName, mimeType: "application/pdf", buffer: uploadBytes });
  await dialog.getByLabel("Page number in the new file", { exact: true }).fill("2");
  await dialog.getByRole("button", { name: "Update in place", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  const afterUpload = await readScene();
  assert.equal(uploads.length, 1, "One user submission uploads one replacement");
  assert.equal(await quiet.count(), 0, "A replacement this tab uploaded is never announced back to its author");
  assert.equal(workCopies.length, 1, "Nothing but the explicit button asks for a work copy");
  assert.deepEqual(byId(afterUpload, "kept-image").customData.sourceDocument, pageSource(uploadedReplacement, 1));
  assert.deepEqual(geometry(byId(afterUpload, "kept-image")), geometry(byId(reopened, "kept-image")));
  assert.deepEqual(byId(afterUpload, "unmapped-image"), byId(reopened, "unmapped-image"));
  const uploadCrop = byId(afterUpload, "kept-image").crop;
  assert.ok(uploadCrop.naturalWidth > newCrop.naturalWidth, "The uploaded PDF must exercise a different preview resolution");
  for (const [field, dimension] of [["x", "naturalWidth"], ["y", "naturalHeight"], ["width", "naturalWidth"], ["height", "naturalHeight"]]) {
    assert.ok(Math.abs(uploadCrop[field] / uploadCrop[dimension] - newCrop[field] / newCrop[dimension]) < 1e-12,
      `Selection update preserves the crop's ${field} fraction`);
  }
  for (const id of ["kept-frame", "existing-mark", "mark-during-preview"]) assert.deepEqual(byId(afterUpload, id), byId(reopened, id));
  assert.deepEqual(activeIds(afterUpload, "frame"), activeIds(reopened, "frame"));
  assert.deepEqual(activeIds(afterUpload, "image"), activeIds(reopened, "image"));
  assert.deepEqual(saved.elements, persisted(afterUpload.elements), "The dialog completes only after its updated scene is saved");

  // Clear marks through both real buttons, with a single keyboard undo/redo.
  // Include a real Crit pen stroke and arrow/text bindings to a retained page.
  await page.locator(".monkeyboard-actions summary").click();
  await page.getByRole("button", { name: "Crit mode", exact: true }).click();
  await page.mouse.move(800, 450); await page.mouse.down();
  await page.mouse.move(840, 470, { steps: 6 }); await page.mouse.move(890, 450, { steps: 6 }); await page.mouse.up();
  await page.waitForFunction(() => window.__boardApi.getSceneElements().some((element) => element.type === "freedraw"));
  await page.evaluate(() => {
    const api = window.__boardApi;
    const { newElementWith, convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers;
    const marks = convertToExcalidrawElements([
      { type: "arrow", id: "bound-arrow", x: 950, y: 200, width: 150, height: 80, points: [[0, 0], [150, 80]] },
      { type: "text", id: "arrow-label", x: 960, y: 210, text: "Move this edge", fontFamily: window.__boardHelpers.FONT_FAMILY.Helvetica },
      { type: "text", id: "loose-note", x: 1000, y: 350, text: "Review note", fontFamily: window.__boardHelpers.FONT_FAMILY.Helvetica },
      { type: "rectangle", id: "box-mark", x: 1000, y: 450, width: 120, height: 60 },
      { type: "diamond", id: "diamond-mark", x: 1000, y: 550, width: 80, height: 80 },
    ], { regenerateIds: false }).map((element) => element.id === "bound-arrow"
      ? newElementWith(element, { startBinding: { elementId: "kept-image", focus: 0, gap: 1, fixedPoint: null }, boundElements: [{ id: "arrow-label", type: "text" }] })
      : element.id === "arrow-label" ? newElementWith(element, { containerId: "bound-arrow" }) : element);
    const elements = api.getSceneElementsIncludingDeleted().map((element) => element.id === "kept-image"
      ? newElementWith(element, { boundElements: [{ id: "bound-arrow", type: "arrow" }] }) : element);
    api.updateScene({ elements: [...elements, ...marks], captureUpdate: CaptureUpdateAction.IMMEDIATELY });
  });
  await page.getByRole("button", { name: "Exit", exact: true }).click();
  for (const ids of [["bound-arrow"], ["arrow-label"], ["loose-note"], ["bound-arrow", "arrow-label"]]) await expectUpdate(ids, false);
  await expectUpdate(["kept-image", "bound-arrow", "arrow-label"], true);
  await select([]);
  const beforeClear = await readScene();
  const markIds = beforeClear.elements.filter((element) => !element.isDeleted && !["image", "frame"].includes(element.type)).map(({ id }) => id).sort();
  assert.ok(markIds.length >= 8, "The fixture covers freehand, lines, shapes, bound text and loose notes");
  const preservedIds = beforeClear.elements.filter((element) => !element.isDeleted && ["image", "frame"].includes(element.type)).map(({ id }) => id).sort();
  const clear = page.getByRole("button", { name: "Clear annotations", exact: true });
  const waitCleared = () => page.waitForFunction((ids) => ids.every((id) => !window.__boardApi.getSceneElements().some((element) => element.id === id)), markIds);
  const waitRestored = () => page.waitForFunction((ids) => ids.every((id) => window.__boardApi.getSceneElements().some((element) => element.id === id)), markIds);
  await page.locator(".monkeyboard-actions summary").click();
  await clear.click(); await waitCleared();
  assert.equal(await page.getByRole("button", { name: "Clear annotations", exact: true, includeHidden: true }).isEnabled(), false);
  const cleared = await readScene();
  for (const id of preservedIds) {
    assert.deepEqual(geometry(byId(cleared, id)), geometry(byId(beforeClear, id)), `Clear preserves page/frame geometry: ${id}`);
    assert.deepEqual(byId(cleared, id).customData, byId(beforeClear, id).customData);
    assert.equal(byId(cleared, id).fileId, byId(beforeClear, id).fileId);
    assert.equal(byId(cleared, id).name, byId(beforeClear, id).name);
  }
  assert.deepEqual(byId(cleared, "kept-image").boundElements, [], "Retained pages must not point at deleted arrows");
  // No extra canvas click: the button must restore keyboard focus itself.
  await page.keyboard.press("Control+z"); await waitRestored();
  assert.deepEqual(byId(await readScene(), "kept-image").boundElements, byId(beforeClear, "kept-image").boundElements);
  await page.keyboard.press("Control+Shift+z"); await waitCleared();
  await page.keyboard.press("Control+z"); await waitRestored();
  await page.locator(".monkeyboard-actions summary").click();
  await page.getByRole("button", { name: "Crit mode", exact: true }).click();
  await clear.click(); await waitCleared();
  await page.keyboard.press("Control+z"); await waitRestored();
  await clear.click(); await waitCleared();
  await page.waitForFunction(() => document.querySelector(".monkeyboard-save-state")?.textContent === "Saved");
  assert.ok(markIds.every((id) => saved.elements.find((element) => element.id === id)?.isDeleted));
  const seenBeforeReopen = [...saved.seenDocuments];
  await page.goto(`${origin}/?view=board&lang=zh-CN`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));
  await waitCleared();
  assert.equal(await page.getByRole("button", { name: "清除批注", exact: true, includeHidden: true }).isEnabled(), false);
  assert.deepEqual(activeIds(await readScene(), "image"), activeIds(beforeClear, "image"));
  assert.deepEqual(activeIds(await readScene(), "frame"), activeIds(beforeClear, "frame"));
  assert.deepEqual(saved.seenDocuments, seenBeforeReopen);
  await select(["kept-frame"]);
  const chineseUpdate = updateAction("更新此页原图");
  await chineseUpdate.waitFor(); assert.equal(await chineseUpdate.isEnabled(), true);
  await chineseUpdate.click();
  const chineseDialog = page.getByRole("dialog", { name: "更新此页原图", exact: true });
  assert.match(await chineseDialog.locator(".monkeyboard-feedback-source").innerText(), /UI updated\.pdf.*2\/2/);
  await page.keyboard.press("Escape");
  await chineseDialog.waitFor({ state: "hidden" });
  assert.equal(await chineseUpdate.evaluate((element) => document.activeElement === element), true,
    `Cancel replacement should return to its keyboard trigger, got ${await page.evaluate(() => document.activeElement.tagName)}`);
  // Keep the server's competing revision and the page's unsent canvas separate.
  competingVersion = true;
  saved = { ...structuredClone(saved), title: "Another saved board", revisionSha256: "f".repeat(64) };
  const winner = structuredClone(saved), localScene = await readScene();
  await page.getByRole("textbox", { name: "画布标题", exact: true }).fill("我的未保存图墙");
  await page.getByText(/已有另一份保存版本/).waitFor();
  assert.equal(conflicts, 1);
  assert.deepEqual(saved, winner, "A rejected stale save must not overwrite the winning board");
  assert.deepEqual((await readScene()).elements, localScene.elements, "Conflict must preserve the current scene");
  assert.equal(await page.getByRole("textbox", { name: "画布标题", exact: true }).inputValue(), "我的未保存图墙");
  assert.equal(await chineseUpdate.isDisabled(), true, "Conflicted local state cannot upload another replacement");
  assert.equal(uploads.length, 1);
  assert.deepEqual(failures, []); assert.deepEqual(escaped, []);
  console.log(JSON.stringify({ passed: "Excalidraw explicit image/frame replacement, exact source mapping and crop/marks preservation, explicit editable work copy, quiet received-update notice whose View never writes the board, clear annotations and undo/redo in normal/Crit modes, save/reopen and CAS", writes: writes.length, uploads: uploads.length, workCopies: workCopies.length, conflicts, documentReads, fileReads: fileReads.length }));
} catch (error) {
  console.error(JSON.stringify({ failures, escaped, writes: writes.length, documentReads,
    visible: await page?.locator("body").innerText().catch(() => "") }));
  throw error;
} finally {
  releasePreview();
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
