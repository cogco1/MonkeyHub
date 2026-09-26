/** Real Board gestures, document registration and CAS against an isolated Studio project.
 * No scene injection, canvas API probe, model/agent request, or user service is used.
 * The existing Board intent handoff test separately covers Connected -> App.
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

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.resolve(repoRoot, "apps/archflow-studio/api");
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1",
  PYTHONPATH: [repoRoot, apiRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
const root = await mkdtemp(path.join(tmpdir(), "monkeyboard-whiteboard-"));
const projectDir = path.join(root, "demo-project");
const errors = [], requests = [], submissions = [];
const http = createHttpServer();
let api, vite, browser, page, closing = false, apiLog = "";
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const active = (board, type) => board.elements.filter((element) => !element.isDeleted && (!type || element.type === type));
const html = `<!doctype html><html><head><style>html,body,#root{height:100%;margin:0}</style></head>
<body><div id="root"></div><script type="module">
import React from "react";
import { createRoot } from "react-dom/client";
import Board from "/src/workspaces/monkeyboard/Board.tsx";
import { UserPreferencesProvider } from "/test/TestProviders.tsx";
import "/@fs/${path.join(repoRoot, "apps/shared-web/src/base.css").replaceAll("\\", "/")}";
import "/src/styles.css";
createRoot(document.getElementById("root")).render(React.createElement(UserPreferencesProvider, null,
  React.createElement(Board, { onSubmit: (request) => window.receiveBoardRequest(request) })));
</script></body></html>`;

try {
  // Same P036 project and retained native model source as the existing API tests.
  const fixture = spawnSync(python, ["-c", `
import base64, hashlib, json, sys
from pathlib import Path
from unittest import mock
import archflow
from tests.support import make_project, retain_rhino_receipt, runner_state_digest, REFERENCE_RUN_ID
from tests.test_documents import two_page_pdf
assert Path(archflow.__file__).resolve() == Path(sys.argv[2], "archflow/__init__.py").resolve(), archflow.__file__
repository, _ = make_project(Path(sys.argv[1]))
digest = runner_state_digest(repository, REFERENCE_RUN_ID)
models = []
with mock.patch("tests.support.RHINO_DESIGN_STATE_DIGEST", digest):
    for suffix in ("a", "b"):
        model = Path(f"tests/fixtures/model-source-{suffix}.3dm").read_bytes()
        retain_rhino_receipt(repository, repository.load_run(REFERENCE_RUN_ID), stage_id=f"document-source-{suffix}", file_name=f"source-{suffix}.3dm", payload_bytes=model)
        models.append({"runId": REFERENCE_RUN_ID, "stateDigest": digest, "assetSha256": hashlib.sha256(model).hexdigest()})
print(json.dumps({"models": models, "pdf": base64.b64encode(two_page_pdf()).decode()}))
`, root, repoRoot], { cwd: apiRoot, encoding: "utf8", env: pythonEnv });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const { models: [modelSource, alternateModelSource], pdf } = JSON.parse(fixture.stdout.trim().split(/\r?\n/).at(-1));
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
  const board = () => call("GET", "/api/board");
  async function savedWhere(predicate, message) {
    for (let attempt = 0; attempt < 100; attempt++) {
      const result = await board();
      if (predicate(result)) return result;
      await delay(100);
    }
    assert.fail(message);
  }

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", publicDir: "../.generated/public",
    cacheDir: path.join(root, "vite-cache"), define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", async (request, response) => {
    if (request.url.startsWith("/board-whiteboard-test")) {
      response.setHeader("content-type", "text/html");
      response.end(await vite.transformIndexHtml(request.url, html)); return;
    }
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const entry = { method: request.method, path: request.url }; requests.push(entry);
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => { if (chunks.length) entry.body = JSON.parse(Buffer.concat(chunks).toString("utf8")); });
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, (answer) => {
      entry.status = answer.statusCode;
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    // A proxy failure after the answer began must not replace the real failure
    // with an unhandled header error during teardown.
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
  await context.exposeBinding("receiveBoardRequest", (_source, request) => { submissions.push(request); });
  page = await context.newPage(); page.setDefaultTimeout(20_000);
  page.on("pageerror", (error) => errors.push(error.message));
  const screenshot = async (name) => {
    if (!process.env.BOARD_SCREENSHOT) return;
    const output = process.env.BOARD_SCREENSHOT.replace(/\.png$/i, `-${name}.png`);
    await page.screenshot({ path: output }); console.log(`Board visual: ${output}`);
  };
  const open = async (language = "en", theme = "dark") => {
    await page.goto(`${origin}/board-whiteboard-test?lang=${language}&theme=${theme}`, { waitUntil: "domcontentloaded" });
    await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
    await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  };
  const more = () => page.locator(".monkeyboard-actions > summary");
  const setMore = async (expanded) => { if ((await page.locator(".monkeyboard-actions").getAttribute("open") !== null) !== expanded) await more().click(); };
  const fit = async () => { await setMore(true); await page.getByRole("button", { name: "Fit board", exact: true }).click(); await setMore(false); };
  const selectAll = async () => {
    await page.locator(".excalidraw").focus(); await page.keyboard.press("Escape");
    await page.keyboard.press("v"); await page.keyboard.press("Control+a");
  };
  const draw = async (key, from, to) => {
    await page.locator(".excalidraw").focus(); await page.keyboard.press("Escape"); await page.keyboard.press(key);
    await page.mouse.move(...from); await page.mouse.down(); await page.mouse.move(...to, { steps: 12 }); await page.mouse.up();
  };
  await open();
  await page.getByText("Drop a drawing or image here", { exact: true }).waitFor();
  const documentsToggle = page.getByRole("button", { name: "Project documents", exact: true });
  assert.equal(await documentsToggle.getAttribute("aria-expanded"), "false");
  assert.equal(await page.locator(".monkeyboard-sources").isVisible(), false);
  assert.equal(await page.locator(".monkeyboard-actions").getAttribute("open"), null);
  assert.equal(await page.getByRole("button", { name: "Crit mode", exact: true }).isVisible(), false);
  assert.equal(await page.locator(".monkeyboard-context").isVisible(), false);
  await more().focus(); await page.keyboard.press("Enter");
  assert.notEqual(await page.locator(".monkeyboard-actions").getAttribute("open"), null);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator(".monkeyboard-actions").getAttribute("open"), null);
  assert.equal(await more().evaluate((element) => document.activeElement === element), true);
  await documentsToggle.focus(); await page.keyboard.press("Enter");
  await page.locator(".monkeyboard-sources").waitFor();
  await page.keyboard.press("Enter");
  assert.equal(await documentsToggle.getAttribute("aria-expanded"), "false");
  await open("zh-CN");
  await page.getByText("拖入图纸或图片开始", { exact: true }).waitFor();
  await screenshot("empty-zh");
  await page.setViewportSize({ width: 620, height: 900 });
  const canvasWidth = (await page.locator(".monkeyboard-canvas").boundingBox()).width;
  const narrowDocuments = page.getByRole("button", { name: "项目资料", exact: true });
  await narrowDocuments.click();
  assert.equal(await narrowDocuments.getAttribute("aria-expanded"), "true");
  assert.equal((await page.locator(".monkeyboard-canvas").boundingBox()).width, canvasWidth,
    "Narrow documents overlay must not squeeze the canvas");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await screenshot("narrow-sources-zh");
  await page.getByRole("button", { name: "收起项目资料", exact: true }).click();
  assert.equal(await narrowDocuments.getAttribute("aria-expanded"), "false");
  await screenshot("narrow-zh");
  await page.setViewportSize({ width: 1440, height: 1000 });
  await open();

  // A note on an otherwise empty board needs no source, model or feedback step.
  await page.keyboard.press("t"); await page.mouse.click(700, 350);
  await page.keyboard.insertText("Meeting starts at 10"); await page.keyboard.press("Escape");
  await savedWhere((value) => active(value, "text").some((element) => element.text === "Meeting starts at 10"),
    "An ordinary note on the empty board must save without a source");
  await selectAll();
  assert.equal(await page.locator(".monkeyboard-context").isVisible(), false);
  assert.deepEqual((await call("GET", "/api/documents")).documents, []);
  assert.equal(submissions.length, 0);
  await page.keyboard.press("Delete");
  await savedWhere((value) => active(value).length === 0, "Deleting the unrelated note must return to an empty board");

  // Browser File/DataTransfer dispatch exercises the same native file-drop handler.
  const png = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 600; canvas.height = 400;
    const ctx = canvas.getContext("2d"); ctx.fillStyle = "#c7dded"; ctx.fillRect(0, 0, 600, 400);
    return canvas.toDataURL().split(",")[1];
  });
  const transfer = await page.evaluateHandle((content) => {
    const data = new DataTransfer();
    data.items.add(new File([Uint8Array.from(atob(content), (char) => char.charCodeAt(0))], "讨论图纸.png", { type: "image/png" }));
    return data;
  }, png);
  await page.locator(".monkeyboard-canvas").dispatchEvent("drop", { dataTransfer: transfer }); await transfer.dispose();
  const imported = await savedWhere((value) => active(value, "image").length === 1, "Dropped image was not registered and saved");
  const firstDocument = (await call("GET", "/api/documents")).documents[0];
  assert.equal(firstDocument.fileName, "讨论图纸.png"); assert.equal(firstDocument.modelSource, null);
  assert.deepEqual(active(imported, "image")[0].customData.sourceDocument,
    { runId: firstDocument.runId, assetSha256: firstDocument.assetSha256, revisionRef: firstDocument.revisionRef, pageIndex: 0 });
  await fit();
  await draw("a", [620, 410], [780, 490]);
  await savedWhere((value) => active(value, "arrow").length === 1, "Native arrow gesture did not persist");
  await selectAll();
  await page.locator(".monkeyboard-context").waitFor();
  await page.locator(".monkeyboard-context").getByRole("button", { name: "Link model in MonkeyDiagram", exact: true }).waitFor();
  assert.equal(await page.locator(".monkeyboard-context-identity strong").innerText(), firstDocument.fileName);
  assert.equal(await page.locator(".monkeyboard-context-binding").innerText(), "Drawing only");
  assert.equal(submissions.length, 0);
  // Associate the uploaded source through the existing API, then rediscover it.
  const bound = await call("POST", `/api/documents/${firstDocument.assetSha256}/model-source`, {
    projectId: project.projectId, runId: firstDocument.runId, modelSource });
  assert.deepEqual(bound.modelSource, modelSource);
  let workingCase = "current";
  await page.route("**/api/working-source?workspace=modeling", async (route) => {
    const source = workingCase === "unknown" ? null : workingCase === "stale" ? alternateModelSource : modelSource;
    await route.fulfill({ json: { projectId: workingCase === "other-project" ? "another-project" : project.projectId,
      workspace: "modeling", policy: "live", revisionSha256: "f".repeat(64), head: source ? {
        runId: source.runId, stateDigest: source.stateDigest, recordDigest: "e".repeat(64), accepted: true,
        origin: "branch-head", label: "Current stage", modelSource: source, lineage: [source.runId],
      } : null, compatible: source !== null, source } });
  });
  const datedDocuments = await call("GET", "/api/documents");
  datedDocuments.documents[0].generatedAt = "2026-09-26T10:30:00Z";
  datedDocuments.documents[0].projectId = "another-project";
  await page.route("**/api/documents", (route) => route.fulfill({ json: datedDocuments }), { times: 1 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await fit(); await selectAll();
  const feedback = () => page.locator(".monkeyboard-context").getByRole("button", { name: "Send design feedback", exact: true });
  await page.getByText("Current editing source unavailable", { exact: true }).waitFor();
  assert.match(await page.locator(".monkeyboard-context-identity").innerText(), /Generated Sep 26, 2026/);
  assert.equal(submissions.length, 0, "A page claiming another project remains view-only until the existing handoff validates it");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await fit(); await selectAll();
  await feedback().waitFor(); assert.equal(await feedback().isEnabled(), true);
  assert.equal(await page.locator(".monkeyboard-context").getByRole("button", { name: "Link model in MonkeyDiagram", exact: true }).count(), 0);
  const sourceStatus = page.locator(".monkeyboard-context-binding");
  await page.getByText("Matches current editing source", { exact: true }).waitFor();
  assert.equal(await sourceStatus.innerText(), "Matches current editing source");

  // The selected page compares all three model-source fields with the current
  // modeling source. A visibility refresh updates the read-only warning; it
  // never changes the Board selection or sends the existing feedback action.
  const refreshWorkingSource = async (expected) => {
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await page.getByText(expected, { exact: true }).waitFor();
    assert.equal(submissions.length, 0, "Freshness display must not send feedback");
  };
  workingCase = "stale"; await refreshWorkingSource("Older than current editing source");
  workingCase = "unknown"; await refreshWorkingSource("Current editing source unavailable");
  workingCase = "other-project"; await refreshWorkingSource("Current editing source unavailable");
  workingCase = "current"; await refreshWorkingSource("Matches current editing source");
  assert.match(await page.locator(".monkeyboard-context-identity").innerText(), /Current · Current stage/);
  for (const [name, width, height] of [["dock-wide", 1440, 1000], ["dock-narrow", 620, 900], ["dock-phone", 390, 844]]) {
    await page.setViewportSize({ width, height });
    const dock = page.locator(".monkeyboard-context");
    await dock.waitFor();
    const layout = await dock.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right, viewport: innerWidth, scroll: element.scrollWidth, client: element.clientWidth };
    });
    assert.ok(layout.left >= 0 && layout.right <= layout.viewport && layout.scroll <= layout.client + 1,
      `Selected drawing actions must fit the ${width}px viewport: ${JSON.stringify(layout)}`);
    assert.equal(await feedback().isEnabled(), true, "Resizing keeps the same actionable drawing selection");
    await screenshot(name);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await setMore(true);
  await page.locator(".monkeyboard-actions-panel").getByRole("button", { name: "Send design feedback", exact: true }).click();
  await page.getByRole("dialog", { name: "Discuss this drawing", exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "Discuss this drawing", exact: true }).waitFor({ state: "hidden" });
  if (!await more().evaluate((element) => document.activeElement === element)) {
    errors.push(`Secondary feedback cancellation did not restore its summary: ${await page.evaluate(() => document.activeElement.outerHTML.slice(0, 600))}`);
  }
  await draw("o", [815, 405], [875, 465]);
  await draw("p", [615, 510], [705, 525]);
  const marksScene = await savedWhere((value) => active(value, "ellipse").length === 1 && active(value, "freedraw").length === 1,
    "Native circle and pen gestures did not persist");
  // Space-drag changes the viewport, while all persisted document/ink geometry stays put.
  await page.keyboard.press("Escape"); await page.keyboard.down("Space");
  await page.mouse.move(1050, 730); await page.mouse.down(); await page.mouse.move(1110, 765, { steps: 6 });
  await page.mouse.up(); await page.keyboard.up("Space");
  assert.deepEqual((await board()).elements, marksScene.elements, "Pan must not modify the saved drawing or ink geometry");
  await fit();

  // The selected native text is already the instruction: send it without retyping.
  await page.locator(".excalidraw").focus(); await page.keyboard.press("Escape"); await page.keyboard.press("t");
  await page.mouse.click(650, 545); await page.keyboard.insertText("入口保持净宽"); await page.keyboard.press("Escape");
  const textScene = await savedWhere((value) => active(value, "text").some((element) => element.text === "入口保持净宽"), "Native Chinese text did not persist");
  await selectAll();
  assert.equal(await feedback().isEnabled(), true);
  await screenshot("text-selection");
  await feedback().click();
  const dialog = page.getByRole("dialog", { name: "Discuss this drawing", exact: true });
  const comment = () => dialog.getByLabel("What would you like to change?", { exact: true });
  assert.equal(await comment().inputValue(), "入口保持净宽");
  assert.equal(submissions.length, 0, "Selecting and opening existing text must not submit an intent");
  await screenshot("text-prefilled");
  await dialog.getByRole("button", { name: "Send to design", exact: true }).click();
  for (let attempt = 0; submissions.length < 1 && attempt < 100; attempt++) await delay(100);
  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].utterance, "入口保持净宽", "Selected Chinese text must be sent exactly once without retyping");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  // A real text selection/delete remains reversible through the native keys.
  await page.keyboard.press("Escape"); await page.mouse.click(665, 555); await page.keyboard.press("Delete");
  await savedWhere((value) => active(value, "text").length === 0, "Delete did not remove selected text");
  await page.keyboard.press("Control+z");
  await savedWhere((value) => active(value, "text").length === active(textScene, "text").length, "Undo did not restore editable text");
  await page.keyboard.press("Control+Shift+z");
  await savedWhere((value) => active(value, "text").length === 0, "Redo did not remove text");
  await page.keyboard.press("Control+z");
  await savedWhere((value) => active(value, "text").length === 1, "Undo must return the editable Board note");
  const addText = async (point, text) => {
    await page.locator(".excalidraw").focus(); await page.keyboard.press("Escape"); await page.keyboard.press("t");
    await page.mouse.click(...point); await page.keyboard.insertText(text); await page.keyboard.press("Escape");
    return savedWhere((value) => active(value, "text").some((element) => element.text === text), `Native note did not persist: ${text}`);
  };
  // Creation order differs from reading order. The far note is intentionally unselected.
  await addText([1150, 805], "另一张图的待办");
  await addText([650, 770], "保留现有雨棚");
  await page.keyboard.press("v"); await page.mouse.dblclick(700, 450);
  await page.keyboard.insertText("把入口向右移"); await page.keyboard.press("Escape");
  const labelled = await savedWhere((value) => active(value, "text").some((element) => element.text === "把入口向右移"),
    "Double-clicking the arrow did not create its native label");
  assert.equal(active(labelled, "text").find((element) => element.text === "把入口向右移").containerId, active(labelled, "arrow")[0].id,
    "The fixture must exercise native bound text, not a nearby loose label");
  await draw("v", [350, 235], [1100, 810]);
  await feedback().waitFor(); assert.equal(await feedback().isEnabled(), true, "Native marquee must select the explicit source with its marks");
  await screenshot("selection");
  await feedback().click();
  const selectedText = "把入口向右移\n\n入口保持净宽\n\n保留现有雨棚";
  assert.equal(await comment().inputValue(), selectedText, "Read top-to-bottom, include bound/outside selected text once, exclude the unselected note");
  const editedComment = `${selectedText}\n保留窗洞位置。`;
  await comment().press("End"); await comment().press("Control+End"); await comment().press("Enter");
  await page.keyboard.insertText("保留窗洞位置。");
  assert.equal(await comment().inputValue(), editedComment);
  assert.equal(submissions.length, 1, "Opening and extending feedback must not call the agent");
  await screenshot("text-with-conditions");
  await dialog.getByRole("button", { name: "Send to design", exact: true }).click();
  for (let attempt = 0; submissions.length < 2 && attempt < 100; attempt++) await delay(100);
  assert.equal(submissions.length, 2);
  const submitted = submissions[1];
  assert.deepEqual(submitted.modelSource, modelSource);
  assert.deepEqual(submitted.source, active(imported, "image")[0].customData.sourceDocument);
  assert.equal(submitted.utterance, editedComment, "The final edited message must not prepend or duplicate selected text");
  assert.equal(submitted.documentAnnotations.length, 1);
  assert.ok(submitted.documentVisuals[0].annotatedPngBase64);
  const annotationWrites = () => requests.filter((request) => request.method === "PUT" && request.path === "/api/document-annotations");
  assert.equal(annotationWrites().length, 2);
  assert.ok(annotationWrites().every((request) => request.body.annotations.every((mark) => mark.kind !== "text" && !mark.label)),
    "Board text travels as the editable message, never as geometry annotations");
  assert.equal(submitted.documentVisuals[0].annotatedPngBase64, submissions[0].documentVisuals[0].annotatedPngBase64,
    "Adding only Board text, including a native arrow label, must not redraw text into the geometry PNG");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();

  // Persisted source bindings are immutable. Exercise stale reads by changing only
  // the documents response after the modal opens, preserving all real project data.
  const documentsUrl = `${origin}/api/documents`;
  for (const change of ["model", "source"]) {
    await feedback().click();
    assert.equal(await comment().inputValue(), selectedText);
    await page.route(documentsUrl, async (route) => {
      const response = await route.fetch(); const value = await response.json();
      const documents = change === "source" ? [] : value.documents.map((document) =>
        document.assetSha256 === firstDocument.assetSha256 ? { ...document, modelSource: alternateModelSource } : document);
      await route.fulfill({ response, json: { ...value, documents } });
    });
    try {
      await dialog.getByRole("button", { name: "Send to design", exact: true }).click();
      await dialog.getByRole("alert").waitFor();
      assert.match(await dialog.getByRole("alert").innerText(), change === "model" ? /linked model changed|cannot be opened/i : /source changed/i);
      assert.equal(await comment().inputValue(), selectedText);
      assert.equal(submissions.length, 2); assert.equal(annotationWrites().length, 2);
      await screenshot(`${change}-changed`);
    } finally { await page.unroute(documentsUrl); }
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  }

  // Export is the registered clean original even after real Board/page ink was saved.
  await setMore(true);
  await page.getByRole("combobox", { name: "Export board pages", exact: true }).selectOption("png");
  await screenshot("more");
  const downloadReady = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export board pages", exact: true }).click();
  const download = await downloadReady;
  const exportedPath = path.join(root, "clean-original.png"); await download.saveAs(exportedPath);
  const inspect = spawnSync(python, ["-c", `
import sys
from PIL import Image
image = Image.open(sys.argv[1]).convert("RGB")
assert image.size == (600, 400), image.size
assert image.getextrema() == ((199, 199), (221, 221), (237, 237)), image.getextrema()
`, exportedPath], { encoding: "utf8" });
  assert.equal(inspect.status, 0, `Export must contain only the clean source pixels: ${inspect.stderr}`);
  await setMore(false);

  // The selection entry replaces a real registered source, then undo and
  // reopening retain that choice instead of rediscovering another revision.
  await selectAll();
  const beforeReplacement = await board();
  const originalImage = active(beforeReplacement, "image")[0];
  const replacementPng = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 300; canvas.height = 200;
    const context = canvas.getContext("2d"); context.fillStyle = "#bbddaa"; context.fillRect(0, 0, 300, 200);
    return canvas.toDataURL().split(",")[1];
  });
  // Hold a real background refresh. It must not swallow this enabled entry;
  // opening the picker writes nothing, and upload still joins the serial queue.
  let releaseRefresh;
  const refreshHeld = new Promise(resolve => { releaseRefresh = resolve; });
  let sawRefresh;
  const refreshSeen = new Promise(resolve => { sawRefresh = resolve; });
  let refreshContinued;
  const refreshDone = new Promise(resolve => { refreshContinued = resolve; });
  const holdDocuments = async route => { sawRefresh(); await refreshHeld; await route.continue(); refreshContinued(); };
  await page.route("**/api/documents", holdDocuments);
  await Promise.race([refreshSeen, delay(15000).then(() => assert.fail("Background document refresh did not start"))]);
  const replacementDialog = page.getByRole("dialog", { name: "Update this page", exact: true });
  try {
    await page.locator(".monkeyboard-context").getByRole("button", { name: "Update this page", exact: true }).click();
    await replacementDialog.waitFor();
  } finally {
    releaseRefresh(); await refreshDone; await page.unroute("**/api/documents", holdDocuments);
  }
  await replacementDialog.getByLabel("Updated PDF / image", { exact: true }).setInputFiles({
    name: "原位更新.png", mimeType: "image/png", buffer: Buffer.from(replacementPng, "base64") });
  await replacementDialog.getByRole("button", { name: "Update in place", exact: true }).click();
  await replacementDialog.waitFor({ state: "hidden" });
  const replaced = await board();
  const updatedImage = active(replaced, "image")[0];
  const replacementDocument = (await call("GET", "/api/documents")).documents.find((document) => document.fileName === "原位更新.png");
  assert.ok(replacementDocument);
  assert.deepEqual(updatedImage.customData.sourceDocument, { runId: replacementDocument.runId,
    assetSha256: replacementDocument.assetSha256, revisionRef: replacementDocument.revisionRef, pageIndex: 0 });
  const placement = ({ id, x, y, width, height, angle, scale, frameId, crop }) => ({ id, x, y, width, height, angle, scale, frameId, crop });
  assert.deepEqual(placement(updatedImage), placement(originalImage), "Real replacement must preserve placement and scale");
  assert.deepEqual(replaced.elements.filter((element) => element.id !== originalImage.id),
    beforeReplacement.elements.filter((element) => element.id !== originalImage.id), "Replacement must preserve frames, marks and unrelated notes");
  await page.locator(".excalidraw").focus(); await page.keyboard.press("Control+z");
  const undone = await savedWhere((value) => active(value, "image")[0].customData.sourceDocument.assetSha256 === firstDocument.assetSha256,
    "Undo must save the previous registered source");
  assert.deepEqual(placement(active(undone, "image")[0]), placement(originalImage));
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/api/documents"),
    page.evaluate(() => window.dispatchEvent(new Event("focus"))),
  ]);
  await page.waitForTimeout(900);
  assert.deepEqual((await board()).elements, undone.elements, "Reopening and discovery must preserve the undone replacement");

  const beforeReload = await board();
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  assert.deepEqual((await board()).elements, beforeReload.elements, "Reload must retain scene and source identities");
  // Receive a genuinely new registered file while the documents panel is hidden.
  const extra = await call("POST", "/api/documents", { projectId: project.projectId, runId: modelSource.runId,
    fileName: "自动到达.png", mimeType: "image/png", contentBase64: png, modelSource });
  await savedWhere((value) => active(value, "image").some((element) => element.customData.sourceDocument.runId === extra.runId
    && element.customData.sourceDocument.assetSha256 === extra.assetSha256
    && element.customData.sourceDocument.revisionRef === extra.revisionRef), "Hidden documents panel prevented automatic discovery");
  assert.equal(await documentsToggle.getAttribute("aria-expanded"), "false");
  await documentsToggle.click();
  await page.getByRole("heading", { name: "自动到达.png", exact: true }).waitFor();
  await documentsToggle.click();
  const pickerPng = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 120; canvas.height = 80;
    canvas.getContext("2d").fillRect(0, 0, 120, 80); return canvas.toDataURL().split(",")[1];
  });
  const chooserReady = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Upload PDF / image", exact: true }).click();
  await (await chooserReady).setFiles({ name: "文件选择上传.png", mimeType: "image/png", buffer: Buffer.from(pickerPng, "base64") });
  await savedWhere((value) => active(value, "image").length === 3, "Native file chooser upload did not reach the real saved board");
  assert.ok((await call("GET", "/api/documents")).documents.some((document) => document.fileName === "文件选择上传.png"));
  // A browser File payload exercises paste capture without accessing the system clipboard.
  await page.locator(".excalidraw").focus();
  const pastedPng = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 240; canvas.height = 160;
    const context = canvas.getContext("2d"); context.fillStyle = "#8ccb9b"; context.fillRect(0, 0, 240, 160);
    const base64 = canvas.toDataURL().split(",")[1];
    const data = new DataTransfer();
    data.items.add(new File([Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))], "剪贴板图片.png", { type: "image/png" }));
    document.querySelector(".monkeyboard-canvas").dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
    return base64;
  });
  const pastedScene = await savedWhere((value) => active(value, "image").length === 4, "File clipboard paste did not register and save");
  const pastedDocument = (await call("GET", "/api/documents")).documents.find((document) => document.fileName === "剪贴板图片.png");
  assert.ok(pastedDocument); assert.equal(pastedDocument.modelSource, null);
  assert.ok(active(pastedScene, "image").some((element) => {
    const source = element.customData.sourceDocument;
    return source.runId === pastedDocument.runId && source.assetSha256 === pastedDocument.assetSha256
      && source.revisionRef === pastedDocument.revisionRef && source.pageIndex === 0;
  }), "Pasted image must retain its registered run, source, revision and page identity");
  assert.ok(requests.some((request) => request.method === "POST" && request.path === "/api/documents"
    && request.body.fileName === pastedDocument.fileName && request.body.contentBase64 === pastedPng));

  // A real two-page PDF goes through the same drop path. Add its rotated,
  // cropped second page through the document rail and verify exact page ids.
  const pdfTransfer = await page.evaluateHandle((base64) => {
    const data = new DataTransfer();
    data.items.add(new File([Uint8Array.from(atob(base64), (char) => char.charCodeAt(0))], "两页图纸.pdf", { type: "application/pdf" }));
    return data;
  }, pdf);
  await page.locator(".monkeyboard-canvas").dispatchEvent("drop", { dataTransfer: pdfTransfer }); await pdfTransfer.dispose();
  await savedWhere((value) => active(value, "image").length === 5, "Dropped PDF did not register and place its first page");
  const pdfDocument = (await call("GET", "/api/documents")).documents.find((document) => document.fileName === "两页图纸.pdf");
  assert.ok(pdfDocument); assert.equal(pdfDocument.pageCount, 2); assert.equal(pdfDocument.modelSource, null);
  await documentsToggle.click();
  const pdfCard = page.locator(".monkeyboard-source").filter({ has: page.getByRole("heading", { name: "两页图纸.pdf", exact: true }) });
  await pdfCard.getByRole("combobox").selectOption("1");
  await pdfCard.getByRole("button", { name: "Add page", exact: true }).click();
  const pdfScene = await savedWhere((value) => active(value, "image").length === 6, "Adding the second PDF page did not persist");
  const pdfImages = active(pdfScene, "image").filter((element) => element.customData.sourceDocument.assetSha256 === pdfDocument.assetSha256);
  assert.deepEqual(pdfImages.map((element) => element.customData.sourceDocument), [0, 1].map((pageIndex) => ({
    runId: pdfDocument.runId, assetSha256: pdfDocument.assetSha256, revisionRef: pdfDocument.revisionRef, pageIndex })));
  for (const element of pdfImages) {
    const sourcePage = pdfDocument.pages[element.customData.sourceDocument.pageIndex];
    assert.ok(Math.abs(element.width / element.height - sourcePage.width / sourcePage.height) < 0.002,
      "PDF preview must apply its page crop and rotation exactly once");
  }
  await documentsToggle.click();
  await screenshot("pdf-pages");

  // A second writer advances the actual CAS revision. This page keeps its draft.
  const latest = await board();
  const winner = await call("PUT", "/api/board", { projectId: latest.projectId, title: "Other saved version",
    elements: latest.elements, seenDocuments: latest.seenDocuments, baseRevisionSha256: latest.revisionSha256 });
  await page.getByRole("textbox", { name: "Board title", exact: true }).fill("My unsent board title");
  await page.getByText(/Another saved version exists/).waitFor();
  assert.equal(await page.getByRole("textbox", { name: "Board title", exact: true }).inputValue(), "My unsent board title");
  assert.deepEqual(await board(), winner, "A CAS conflict must never overwrite the winning saved revision");
  assert.ok(requests.some((request) => request.path === "/api/board" && request.status === 409));
  // Hub embeds Board; the retired Studio "Open saved board" external link is
  // no longer a navigation contract. A second reader must still see the winner
  // without replacing the first reader's unsaved draft.
  const savedTab = await context.newPage();
  await savedTab.goto(page.url(), { waitUntil: "domcontentloaded" });
  await savedTab.getByRole("textbox", { name: "Board title", exact: true }).waitFor();
  await savedTab.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  assert.equal(await savedTab.getByRole("textbox", { name: "Board title", exact: true }).inputValue(), "Other saved version");
  assert.equal(await page.getByRole("textbox", { name: "Board title", exact: true }).inputValue(), "My unsent board title");
  assert.deepEqual(await board(), winner, "Opening the saved board must preserve both the winner and the original tab's draft");
  await savedTab.close();
  await open("zh-CN");
  await page.getByRole("button", { name: "项目资料", exact: true }).waitFor();
  await page.getByText("更多画板操作", { exact: true }).click();
  await page.getByRole("button", { name: "清除批注", exact: true }).waitFor();
  assert.equal((await board()).title, "Other saved version");
  await open("zh-CN", "light");
  await page.getByRole("button", { name: "项目资料", exact: true }).waitFor();
  await screenshot("light-zh");
  assert.deepEqual(requests.filter((request) => /\/api\/(intents|proposals|jobs|model-annotations|candidates)/.test(request.path)), [],
    "Ordinary board actions must never enter a model or agent path");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: "real isolated Board PNG/PDF drop, rotated PDF page intake, file-upload/file-clipboard-paste, native gestures and undo/redo, ordered selected/bound/outside text, editable feedback without duplication, source/model-change refusal, clean export, real source replacement and undone reopen, hidden-panel discovery, reload and CAS second-reader comparison", requests: requests.length, submissions: submissions.length }));
} catch (error) {
  if (page && !page.isClosed()) {
    if (process.env.BOARD_SCREENSHOT) await page.screenshot({ path: process.env.BOARD_SCREENSHOT });
    console.error(await page.locator("body").innerText().catch(() => ""));
  }
  console.error(JSON.stringify({ errors, requests: requests.map(({ method, path, status }) => ({ method, path, status })), apiLog }));
  throw error;
} finally {
  closing = true;
  await browser?.close(); await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  if (api && api.exitCode === null) {
    const exited = new Promise((resolve) => api.once("exit", resolve)); api.kill(); await exited;
  }
  assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
  assert.ok(path.basename(root).startsWith("monkeyboard-whiteboard-"));
  await rm(root, { recursive: true, force: true });
}
