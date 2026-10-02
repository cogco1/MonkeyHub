import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

// Real Board, save queue, preferences and Excalidraw. Only the fixture entry and
// a read/write handle to Excalidraw's public API are injected; no component stub.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "board-locale-"));
const projectId = "board-locale-fixture";
const source = { projectId, runId: "documents", assetSha256: "a".repeat(64),
  revisionRef: "original-page", fileName: "Review {name}.png", mimeType: "image/png",
  sizeBytes: 1, pageCount: 1, pages: [{ pageIndex: 0, width: 200, height: 100, rotation: 0 }],
  modelSource: null, modelSourceBindingRef: null, sourceStageRef: null };
const board = { projectId, title: "Retained review board", elements: [],
  seenDocuments: [JSON.stringify([source.runId, source.revisionRef])], revisionSha256: "b".repeat(64) };
const expected = {
  en: { loading: "Opening board…", failed: "The board could not be opened.", retry: "Retry", title: "Board title",
    export: "Export board pages", empty: "Place at least one registered drawing page on the board before exporting.",
    replacement: "Replace this page wherever it is placed on the board. Keep its position, scale and marks. The new page must have the same aspect ratio; the original remains in project documents.",
    action: "Update this page", file: "Updated PDF / image", page: "Page number in the new file", cancel: "Cancel", submit: "Update in place" },
  "zh-CN": { loading: "正在打开画板…", failed: "画板暂时无法打开。", retry: "重试", title: "画板标题",
    export: "整理导出画板图纸", empty: "请先在画板中摆放至少一页已登记图纸。",
    replacement: "更新画板中此页的所有副本，保留位置、缩放与批注。新页须保持相同宽高比；旧原图仍保存在项目资料中。",
    action: "更新此页原图", file: "更新后的 PDF / 图片", page: "新文件中的页码", cancel: "取消", submit: "原位更新" },
};
const failures = [], escaped = [], observations = [];
function equal(actual, desired, label) {
  observations.push({ label, actual, expected: desired });
  try { assert.deepEqual(actual, desired, label); }
  catch (error) { failures.push(error.message); }
}
let vite, browser;
const http = createHttpServer();
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "real-board-locale-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/test/workspace-fixture.tsx`) return { code: `
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, CaptureUpdateAction } from "@excalidraw/excalidraw";
        import Board from "/src/workspaces/monkeyboard/Board";
        import { UserPreferencesProvider, usePreferences } from "/test/TestProviders.tsx";
        import "/src/app/styles.css";
        window.__boardHelpers = { convertToExcalidrawElements, CaptureUpdateAction };
        function Fixture() {
          const { setLanguage } = usePreferences();
          window.__setLanguage = setLanguage;
          return <Board expectedProjectId=${JSON.stringify(projectId)}
            onSubmit={() => { throw new Error("Unexpected design handoff"); }}
            onSketch={() => { throw new Error("Unexpected sketch handoff"); }} />;
        }
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider><Fixture /></UserPreferencesProvider>);
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
  for (const initial of ["zh-CN", "en"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.addInitScript((language) => {
      localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language }));
      window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`;
    }, initial);
    await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
      escaped.push(route.request().url()); return route.abort("blockedbyclient");
    });
    const page = await context.newPage(); page.setDefaultTimeout(15_000);
    page.on("pageerror", (error) => failures.push(`${initial}: ${error.message}`));
    let releaseLoad, boardReads = 0;
    const firstLoad = new Promise((resolve) => { releaseLoad = resolve; });
    const writes = [];
    await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
      const request = route.request(), url = new URL(request.url()), method = request.method();
      assert.equal(url.origin, origin);
      if (method === "GET" && url.pathname === "/api/board") {
        if (++boardReads === 1) {
          await firstLoad;
          return route.fulfill({ status: 503, json: { code: "FIXTURE_LOAD_FAILURE", detail: "Fixture load failure" } });
        }
        return route.fulfill({ json: board });
      }
      if (method === "GET" && url.pathname === "/api/documents") return route.fulfill({ json: { projectId, documents: [source] } });
      if (method === "PUT" && url.pathname === "/api/board") {
        writes.push(request.postDataJSON());
        return route.fulfill({ status: 503, json: { code: "FIXTURE_SAVE_FAILURE", detail: "Keep this draft unsaved" } });
      }
      escaped.push(`${method} ${url.pathname}`);
      return route.fulfill({ status: 405, json: { detail: "Unexpected fixture request" } });
    });
    try {
      await page.goto(origin);
      await page.locator(".monkeyboard-loading p").first().waitFor();
      equal(await page.locator(".monkeyboard-loading p").first().textContent(), expected[initial].loading, `${initial}: loading`);
      releaseLoad();
      await page.locator(".monkeyboard-loading button").waitFor();
      equal(await page.locator(".monkeyboard-loading p").first().textContent(), expected[initial].failed, `${initial}: load failure`);
      equal(await page.locator(".monkeyboard-loading button").textContent(), expected[initial].retry, `${initial}: retry`);
      await page.locator(".monkeyboard-loading button").click();
      await page.waitForFunction(() => window.__boardApi && !document.querySelector(".monkeyboard-initializing"));
      await page.locator(".monkeyboard-title").fill("Unfinished review {count}");
      await page.evaluate(() => {
        const { convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers;
        window.__boardApi.updateScene({ elements: convertToExcalidrawElements([
          { type: "rectangle", id: "unsaved-mark", x: 120, y: 160, width: 80, height: 60, strokeColor: "#e03131", roughness: 0 },
        ], { regenerateIds: false }), captureUpdate: CaptureUpdateAction.IMMEDIATELY });
      });
      await page.locator(".monkeyboard-alert[role=alert]").filter({ hasText: "Keep this draft unsaved" }).waitFor();
      await page.evaluate(() => {
        window.__retainedBoardApi = window.__boardApi;
        window.__retainedCanvas = document.querySelector(".monkeyboard-canvas canvas");
        window.__retainedScene = JSON.stringify(window.__boardApi.getSceneElementsIncludingDeleted());
      });
      const readsBeforeSwitch = boardReads, writesBeforeSwitch = writes.length;
      for (const language of [initial, initial === "en" ? "zh-CN" : "en", initial]) {
        await page.evaluate((language) => window.__setLanguage(language), language);
        await page.waitForFunction((language) => document.documentElement.lang === language, language);
        const text = expected[language];
        equal(await page.locator(".monkeyboard-title").getAttribute("aria-label"), text.title, `${initial} → ${language}: title label`);
        await page.locator(".monkeyboard-actions > summary").click();
        equal(await page.locator(".monkeyboard-export > button").textContent(), text.export, `${initial} → ${language}: export`);
        equal(await page.locator(".monkeyboard-export select").getAttribute("aria-label"), text.export, `${initial} → ${language}: export format label`);
        await page.locator(".monkeyboard-export > button").click();
        equal(await page.locator(".monkeyboard-alert").filter({ hasText: text.empty }).count(), 1, `${initial} → ${language}: empty export guidance`);
        await page.locator(".monkeyboard-actions > summary").click();
        const unchanged = await page.evaluate(() => ({
          api: window.__boardApi === window.__retainedBoardApi,
          canvas: document.querySelector(".monkeyboard-canvas canvas") === window.__retainedCanvas,
          scene: JSON.stringify(window.__boardApi.getSceneElementsIncludingDeleted()) === window.__retainedScene,
          title: document.querySelector(".monkeyboard-title").value,
        }));
        assert.deepEqual(unchanged, { api: true, canvas: true, scene: true, title: "Unfinished review {count}" });
      }
      assert.equal(boardReads, readsBeforeSwitch, "Language switching never rereads or remounts this Board");
      assert.equal(writes.length, writesBeforeSwitch, "Language switching never saves the unsaved Board");
      assert.equal(board.title, "Retained review board", "The saved fixture stays unchanged");
      assert.ok(writes.some((write) => write.title === "Unfinished review {count}" && write.elements.some((element) => element.id === "unsaved-mark")), "Real save queue saw the title and mark");

      await page.locator("button[aria-controls=monkeyboard-project-documents]").click();
      await page.locator(".monkeyboard-source-update").click();
      const dialog = page.locator("dialog[open]");
      await dialog.waitFor();
      await dialog.locator("input[type=number]").fill("3");
      await dialog.locator("input[type=file]").setInputFiles({ name: "replacement.png", mimeType: "image/png", buffer: Buffer.from("fixture, never uploaded") });
      for (const language of [initial, initial === "en" ? "zh-CN" : "en", initial]) {
        await page.evaluate((language) => window.__setLanguage(language), language);
        await page.waitForFunction((language) => document.documentElement.lang === language, language);
        const text = expected[language];
        equal(await dialog.locator("h2").textContent(), text.action, `${initial} → ${language}: replacement action`);
        equal(await dialog.locator("p").nth(1).textContent(), text.replacement, `${initial} → ${language}: replacement guidance`);
        equal(await dialog.locator("label").allTextContents(), [text.file, text.page], `${initial} → ${language}: replacement labels`);
        equal(await dialog.locator("button").allTextContents(), [text.cancel, text.submit], `${initial} → ${language}: replacement controls`);
        assert.equal(await dialog.locator("input[type=number]").inputValue(), "3");
        assert.equal(await dialog.locator("input[type=file]").evaluate((element) => element.files[0].name), "replacement.png");
      }
      await dialog.locator("button[type=button]").click();
      assert.equal(await page.locator("dialog[open]").count(), 0);
      assert.equal(await page.evaluate(() => window.__boardApi === window.__retainedBoardApi), true);
      console.log(`PASS real Board lifecycle and unsaved content: ${initial} ↔ ${initial === "en" ? "zh-CN" : "en"}`);
    } finally { releaseLoad(); await context.close(); }
  }
  assert.deepEqual(escaped, [], "No unexpected API or external service requests");
  console.log(JSON.stringify({ observations }, null, 2));
  assert.deepEqual(failures, [], "Real Board locale observations");
  console.log("PASS Board terminology in both locales, load recovery, export, replacement, and retained unsaved Board");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
