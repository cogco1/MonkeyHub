import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rename, rm, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

// Real Hub, Studio/P036, Board and Excalidraw, all in disposable directories.
// Only the component entry and access to Excalidraw's public API are transformed.
// API traffic is never mocked and no existing app or browser is contacted.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const root = path.resolve(webRoot, "../../../..");
const cacheDir = await mkdtemp(path.join(tmpdir(), "bound-work-copy-board-"));
const fixture = spawn(process.env.PYTHON ?? "python", [path.join(root, "apps/monkeyhub/api/tests/work_copy_browser_fixture.py")],
  { cwd: root, windowsHide: true, stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONUTF8: "1" } });
const lines = createInterface({ input: fixture.stdout });
const replies = [], waiting = [];
let fixtureError = "", exitCode;
const fixtureExited = new Promise((resolve) => fixture.once("exit", resolve));
fixture.stderr.on("data", (data) => { fixtureError += data; });
lines.on("line", (line) => {
  try { const payload = JSON.parse(line); waiting.length ? waiting.shift().resolve(payload) : replies.push(payload); }
  catch { fixtureError += `${line}\n`; }
});
fixture.on("exit", (code) => {
  exitCode = code;
  for (const pending of waiting.splice(0)) pending.reject(new Error(`Fixture exited (${code}): ${fixtureError}`));
});
const nextReply = (timeout = 90_000) => replies.length ? Promise.resolve(replies.shift()) : new Promise((resolve, reject) => {
  if (exitCode !== undefined) return reject(new Error(`Fixture exited (${exitCode}): ${fixtureError}`));
  const pending = { resolve: (value) => { clearTimeout(timer); resolve(value); }, reject: (error) => { clearTimeout(timer); reject(error); } };
  const timer = setTimeout(() => { waiting.splice(waiting.indexOf(pending), 1); reject(new Error(`Fixture timed out: ${fixtureError}`)); }, timeout);
  waiting.push(pending);
});
const snapshot = async () => { fixture.stdin.write(`${JSON.stringify("snapshot")}\n`); return await nextReply(); };
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const failures = [], escaped = [], writes = [];
let browser, page, vite, http;
const geometry = ({ x, y, width, height, angle, scale, frameId }) => ({ x, y, width, height, angle, scale, frameId });
const scene = async () => page.evaluate(() => ({ elements: window.__boardApi.getSceneElementsIncludingDeleted(),
  selected: window.__boardApi.getAppState().selectedElementIds }));

try {
  const setup = await nextReply();
  assert.equal(setup.ready, true);
  console.log(JSON.stringify({ started: "isolated real Hub and Studio", runtimeId: setup.runtimeId }));
  const beforeRuntime = await snapshot();
  const request = async (route, options) => {
    const response = await fetch(`${setup.origin}${setup.basePath}${route}`, options);
    assert.ok(response.ok, `${response.status} ${route}: ${await response.clone().text()}`);
    return response;
  };
  http = createHttpServer();
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir,
    publicDir: "../.generated/public", define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "bound-work-copy-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), base = webRoot.replaceAll("\\", "/");
      if (filename === `${base}/test/workspace-fixture.tsx`) return { code: `
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, newElementWith, CaptureUpdateAction } from "@excalidraw/excalidraw";
        import Board from "/src/workspaces/monkeyboard/Board";
        import { UserPreferencesProvider } from "/test/TestProviders.tsx";
        import "/src/styles.css";
        window.__boardHelpers = { convertToExcalidrawElements, newElementWith, CaptureUpdateAction };
        const forbidden = () => { throw new Error("Unexpected design operation"); };
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider baseUrl={${JSON.stringify(setup.basePath)}}>
          <Board onSubmit={forbidden} onSketch={forbidden} expectedProjectId={${JSON.stringify(setup.projectId)}} />
        </UserPreferencesProvider>);
      `, map: null };
      if (filename === `${base}/src/workspaces/monkeyboard/Board.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; }}";
        assert.equal(source.split(callback).length, 2);
        return { code: source.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; window.__boardApi = api; }}"), map: null };
      }
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null,
      proxy: { "/api/runtime": { target: setup.origin, changeOrigin: true, headers: { origin: setup.origin } } } } });
  http.on("request", vite.middlewares);
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    escaped.push(route.request().url()); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => failures.push(error.stack ?? error.message));
  page.on("request", (request) => { if (request.method() === "PUT" && new URL(request.url()).pathname.endsWith("/api/board")) writes.push(request.postDataJSON()); });
  await page.goto(`${origin}/?view=board&lang=zh-CN`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.__boardApi?.getSceneElements().some((element) => element.type === "image")
    && document.querySelector(".monkeyboard-save-state")?.textContent === "已保存");
  const imageId = await page.evaluate(() => {
    const api = window.__boardApi, { convertToExcalidrawElements, newElementWith, CaptureUpdateAction } = window.__boardHelpers;
    const image = api.getSceneElements().find((element) => element.type === "image");
    const marks = convertToExcalidrawElements([{ type: "ellipse", id: "kept-annotation", x: image.x + 40, y: image.y + 30,
      width: 100, height: 60, strokeColor: "#e03131", strokeWidth: 3, roughness: 0, frameId: image.frameId }], { regenerateIds: false });
    api.updateScene({ elements: [...api.getSceneElementsIncludingDeleted().map((element) => element.id === image.id
      ? newElementWith(element, { x: image.x + 20, y: image.y + 15, width: 500, height: 250, scale: [-1, 1] }) : element), ...marks],
      appState: { selectedElementIds: { [image.id]: true } }, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    return image.id;
  });
  await page.waitForFunction(() => document.querySelector(".monkeyboard-save-state")?.textContent === "已保存");
  const before = await scene();
  assert.equal(before.selected[imageId], true);
  await page.evaluate(() => {
    window.__updateNotices = [];
    let previous = "";
    new MutationObserver(() => {
      const current = document.querySelector(".monkeyboard-update")?.textContent ?? "";
      if (current && current !== previous) window.__updateNotices.push(current);
      previous = current;
    }).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  // These saves originate outside the browser/Hub: a normal local producer
  // writes a temporary file and atomically replaces the explicitly bound copy.
  const editStarted = performance.now();
  for (const [index, bytes] of setup.editsBase64.entries()) {
    const temporary = `${setup.workPath}.save-${index}`;
    await writeFile(temporary, Buffer.from(bytes, "base64"));
    await rename(temporary, setup.workPath);
    await delay(40);
  }
  const finalBytes = Buffer.from(setup.editsBase64.at(-1), "base64");
  const finalSha = createHash("sha256").update(finalBytes).digest("hex");
  await page.waitForFunction(({ imageId, sha }) => window.__boardApi.getSceneElements().find((element) => element.id === imageId)
    ?.customData?.sourceDocument?.assetSha256 === sha, { imageId, sha: finalSha });
  const refreshMs = Math.round(performance.now() - editStarted);
  await page.waitForFunction(() => document.querySelector(".monkeyboard-save-state")?.textContent === "已保存");
  const notice = page.locator(".monkeyboard-update");
  await notice.waitFor();
  assert.equal(await notice.count(), 1);
  assert.match(await notice.innerText(), /live-plan\.png.*已更新/);
  assert.equal((await page.evaluate(() => window.__updateNotices)).length, 1);
  const after = await scene();
  const row = (value, id) => value.elements.find((element) => element.id === id);
  assert.deepEqual(after.elements.map(({ id }) => id), before.elements.map(({ id }) => id));
  assert.deepEqual(geometry(row(after, imageId)), geometry(row(before, imageId)));
  assert.deepEqual(row(after, "kept-annotation"), row(before, "kept-annotation"));
  assert.deepEqual(after.selected, before.selected);
  assert.notEqual(row(after, imageId).fileId, row(before, imageId).fileId);
  const documents = (await (await request("/api/documents")).json()).documents;
  assert.equal(documents.length, 2, "Five saves coalesce to one legal document replacement");
  const replacement = documents.find((document) => document.assetSha256 === finalSha);
  assert.ok(replacement);
  assert.equal(replacement.sourceStageRef, null);
  assert.equal(replacement.modelSource, null);
  assert.deepEqual(replacement.replacesPages, [{ runId: setup.original.runId, assetSha256: setup.original.assetSha256,
    revisionRef: setup.original.revisionRef, pageIndex: 0, newPageIndex: 0 }]);
  const oldQuery = new URLSearchParams({ runId: setup.original.runId });
  if (setup.original.revisionRef) oldQuery.set("revisionRef", setup.original.revisionRef);
  assert.deepEqual(Buffer.from(await (await request(`/api/documents/${setup.original.assetSha256}/bytes?${oldQuery}`)).arrayBuffer()),
    Buffer.from(setup.originalBase64, "base64"), "The immutable old document remains readable");
  const afterRuntime = await snapshot();
  assert.equal(afterRuntime.head, beforeRuntime.head, "A document edit does not accept or publish a Stage");
  assert.equal(afterRuntime.artifactEvents.length - beforeRuntime.artifactEvents.length, 1);
  const retainedBeforeView = await (await request("/api/board")).json();
  const writesBeforeView = writes.length;
  await notice.getByRole("button", { name: "查看", exact: true }).click();
  await page.waitForTimeout(1000);
  assert.deepEqual((await scene()).elements, after.elements);
  assert.equal(writes.length, writesBeforeView, "View changes the camera, never the saved board");
  assert.deepEqual(await (await request("/api/board")).json(), retainedBeforeView);
  await notice.getByRole("button", { name: "关闭提示", exact: true }).click();
  // An unbound sibling and a timestamp-only edit are not new project revisions.
  await writeFile(path.join(path.dirname(setup.workPath), "unbound.png"), Buffer.from(setup.editsBase64[0], "base64"));
  await utimes(setup.workPath, new Date(), new Date());
  await page.waitForTimeout(6000); // Includes a complete production Board refresh interval.
  assert.equal((await (await request("/api/documents")).json()).documents.length, 2);
  assert.equal((await snapshot()).artifactEvents.length, afterRuntime.artifactEvents.length);
  assert.equal(await notice.count(), 0);
  assert.equal((await page.evaluate(() => window.__updateNotices)).length, 1);
  assert.deepEqual((await scene()).elements, after.elements);
  assert.deepEqual(failures, []); assert.deepEqual(escaped, []);
  console.log(JSON.stringify({ passed: "external atomic PNG saves → real P036 revision → original Board image and one View notice; history, HEAD, layout, marks, selection, unbound file and touch preserved",
    atomicSaves: setup.editsBase64.length, registeredReplacements: 1, artifactEvents: 1, notices: 1, refreshMs, writes: writes.length }));
} catch (error) {
  console.error(JSON.stringify({ failures, escaped, fixtureError, visible: await page?.locator("body").innerText().catch(() => "") }));
  throw error;
} finally {
  await browser?.close(); await vite?.close();
  if (http?.listening) await new Promise((resolve) => http.close(resolve));
  if (exitCode === undefined) {
    fixture.stdin.end(`${JSON.stringify("stop")}\n`);
    let stopTimer;
    try {
      await Promise.race([fixtureExited, new Promise((_, reject) => {
        stopTimer = setTimeout(() => {
          fixture.kill();
          reject(new Error(`Owned browser fixture failed to stop within 30 seconds: ${fixtureError}`));
        }, 30_000);
      })]);
    } finally { clearTimeout(stopTimer); }
  }
  assert.equal(exitCode, 0, fixtureError);
  await rm(cacheDir, { recursive: true, force: true });
}
