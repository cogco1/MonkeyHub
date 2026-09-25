import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createProjectWorkspaceFixture } from "../../test/projectWorkspaceFixture.mjs";

// GH-302: the project canvas keeps its chrome (zoom, undo) inside its own
// surface. The real built Hub with Board mounted in it; the Hub API and the
// project runtime are local synthetic fixtures.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../../dist/", import.meta.url)));
const screenshots = process.env.CANVAS_CHROME_SCREENSHOTS ?? await mkdtemp(path.join(tmpdir(), "monkeyhub-canvas-chrome-"));
// CANVAS_CHROME_LANG=zh-CN runs the same checks in the Chinese UI.
const language = process.env.CANVAS_CHROME_LANG === "zh-CN" ? "zh-CN" : "en";
const words = language === "en" ? { board: "Board", hide: "Hide tools", show: "Show tools", projects: "Hide projects" }
  : { board: "画板", hide: "收起工具", show: "展开工具", projects: "收起项目栏" };
const streams = new Set();
let sequence = 0;
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    streams.add(res); req.on("close", () => streams.delete(res));
    res.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence, kind: "snapshot", snapshot: runtimeSnapshot() })}\n\n`);
    return;
  }
  const filename = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  if (!filename.startsWith(root)) { res.writeHead(404); res.end(); return; }
  try {
    res.setHeader("Content-Type", filename.endsWith(".js") ? "text/javascript" : filename.endsWith(".css") ? "text/css"
      : filename.endsWith(".wasm") ? "application/wasm" : filename.endsWith(".woff2") ? "font/woff2" : "text/html");
    res.end(await readFile(filename));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;

const project = { projectId: "A", projectDir: "D:\\fixture\\A", name: "Project A", chatCount: 0, version: 1, stage: null };
const sessions = [], runtimes = new Map(), errors = [], unexpected = [];
const workspaceFixture = await createProjectWorkspaceFixture(runtimes, sessions);
const apps = ["monkeyarch", "monkeyboard", "monkeyrender"].map((appId) => ({ appId, title: appId, serviceId: "studio", available: true,
  state: "stopped", processId: null, url: null, apiUrl: null }));
const runtimeSnapshot = () => ({ serverId: "fixture-hub", sequence, workers: [], projects: [...runtimes.values()].map((runtime) => {
  const running = apps[0].processId !== null;
  return { ...runtime, sessions: [], workers: running ? [{ workerId: runtime.runtimeId, serviceId: "studio", projectId: runtime.projectId,
    projectDir: runtime.projectDir, instanceId: "instance-1", processId: apps[0].processId, desiredState: "running", healthy: true,
    state: "ready", url: apps[0].apiUrl, error: null }] : [] };
}) });
const emit = () => {
  sequence += 1;
  for (const stream of streams) stream.write(`event: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence, kind: "changed", snapshot: runtimeSnapshot() })}\n\n`);
};

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(20000);
page.on("pageerror", (error) => errors.push(error.message));
await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
  const request = route.request(), url = new URL(request.url()), method = request.method();
  if (url.pathname === "/api/runtime/events") return route.continue();
  if (/^\/api\/runtime\/projects\/[^/]+\/studio\//.test(url.pathname)) {
    try { if (await workspaceFixture.handle(route, url)) return; }
    catch (error) { errors.push(error.message); return route.fulfill({ status: 500, json: { code: "FIXTURE_UNEXPECTED", detail: error.message } }); }
  }
  const json = (body, status = 200) => route.fulfill({ json: body, status });
  const data = () => request.postDataJSON();
  if (url.pathname === "/api/settings/user") return json({ language, theme: "light", fontScale: 1 });
  if (url.pathname === "/api/settings/apps") return json({ projectDir: project.projectDir, workspaceDir: null, referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18788 });
  if (url.pathname === "/api/updates/status") return json({ currentVersion: "fixture", currentRevision: "d".repeat(40), mode: "unsupported", state: "idle",
    prepared: null, canApply: false, message: null, error: null, channel: "unsigned-prerelease", autoUpdate: true, nextLaunch: false,
    check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } });
  if (url.pathname === "/api/apps") return json(apps);
  if (url.pathname === "/api/runtime") return json(runtimeSnapshot());
  if (url.pathname === "/api/runtime/projects/open") {
    const body = data();
    assert.equal(body.projectDir, project.projectDir);
    if (!runtimes.has(body.projectDir)) runtimes.set(body.projectDir, { runtimeId: randomUUID(), ...body, state: "open", operations: [],
      retained: null, projection: "ready", clients: 1, error: null });
    await json(runtimeSnapshot().projects[0]); emit(); return;
  }
  if (url.pathname.startsWith("/api/apps/") && url.pathname.endsWith("/start")) {
    const runtimeId = runtimes.get(url.searchParams.get("projectDir")).runtimeId;
    for (const app of apps) Object.assign(app, { state: "running", processId: 2001,
      url: `${origin}/?view=${app.appId === "monkeyboard" ? "board" : app.appId === "monkeyrender" ? "render" : "arch"}&runtimeId=${runtimeId}`,
      apiUrl: `${origin}/api/runtime/projects/${runtimeId}/studio/` });
    await json(apps.find((app) => url.pathname.includes(app.appId)) ?? apps[0]); emit(); return;
  }
  if (url.pathname === "/api/project/modeling") return json({ projectId: project.projectId, initialized: true });
  if (url.pathname === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture only", installed: true,
    signedIn: true, models: [], modelCatalog: "ready", modelDetail: null }]);
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: [project.projectId] });
  if (url.pathname === "/api/chat/projects") return json([project]);
  if (url.pathname === "/api/chat/sessions") return json([]);
  unexpected.push(`${method} ${url.pathname}`); return json({ detail: "Not part of this fixture" }, 404);
});

// Excalidraw's zoom and undo bar: the desktop footer, or the bottom bar of its narrow layout.
const BAR = ".layer-ui__wrapper__footer-left, .App-bottom-bar";
/** Where Board's zoom and undo bar is, and whether any part of the canvas paints or takes a click over the composer. */
const readChrome = () => page.evaluate((selector) => {
  const box = (node) => { const value = node.getBoundingClientRect(); return { x: value.x, y: value.y, right: value.right, bottom: value.bottom }; };
  const host = document.querySelector(".chat-project-workspace .monkeyboard-canvas");
  const bar = host?.querySelector(selector);
  const composer = document.querySelector(".chat-composer");
  const painted = host ? [...host.querySelectorAll("*")].filter((node) => getComputedStyle(node).visibility === "visible"
    && node.getBoundingClientRect().width > 0 && node.getBoundingClientRect().height > 0) : [];
  const zoom = bar ? box(bar) : null, input = composer ? box(composer) : null;
  const overlap = zoom && input ? { x: Math.max(zoom.x, input.x), y: Math.max(zoom.y, input.y), right: Math.min(zoom.right, input.right), bottom: Math.min(zoom.bottom, input.bottom) } : null;
  // What a click in the middle of the shared area reaches.
  const hit = overlap && overlap.right > overlap.x && overlap.bottom > overlap.y
    ? document.elementFromPoint((overlap.x + overlap.right) / 2, (overlap.y + overlap.bottom) / 2) : null;
  return { host: host ? box(host) : null, zoom, composer: input, controls: bar?.querySelectorAll("button").length ?? 0,
    painted: painted.map((node) => ({ ...box(node), name: `${node.tagName.toLowerCase()}.${[...node.classList].join(".")}` })),
    hitInCanvas: Boolean(hit?.closest(".excalidraw")), hitInComposer: Boolean(hit?.closest(".chat-composer")) };
}, BAR);
const intersects = (a, b) => a.x < b.right && b.x < a.right && a.y < b.bottom && b.y < a.bottom;
const inside = (a, b) => a.x >= b.x - 1 && a.y >= b.y - 1 && a.right <= b.right + 1 && a.bottom <= b.bottom + 1;
const passed = [];
try {
  await page.goto(origin);
  await page.waitForFunction((board) => document.querySelector(`.chat-rail__tool[aria-label="${board}"]`)?.dataset.state === "running", words.board);
  // As in the review's screenshots: the project list folded, so the whole composer is in view.
  await page.getByRole("button", { name: words.projects, exact: true }).first().click();
  await page.getByRole("button", { name: words.board, exact: true }).click();
  await page.locator('.chat-project-workspace:not([hidden]) [data-project-surface="board"]:not([hidden]) .monkeyboard-canvas canvas').first().waitFor();
  await page.locator(`.chat-project-workspace .monkeyboard-canvas :is(${BAR}) button`).first().waitFor();
  for (const width of [1440, 900, 375]) {
    await page.setViewportSize({ width, height: 960 });
    if (await page.getByRole("button", { name: words.show, exact: true }).count()) await page.getByRole("button", { name: words.show, exact: true }).click();
    await page.waitForFunction(() => document.querySelector(".chat-project-workspace .monkeyboard-canvas")?.getBoundingClientRect().width > 0);
    const open = await readChrome();
    assert.ok(open.controls > 0, `${width}: Board shows its zoom and undo controls`);
    assert.ok(inside(open.zoom, open.host), `${width}: the zoom bar lies within the canvas surface: ${JSON.stringify(open)}`);
    if (width > 900) assert.ok(!intersects(open.zoom, open.composer), `${width}: beside the chat, the zoom bar stays clear of the composer`);
    await page.screenshot({ path: path.join(screenshots, `board-open-${width}-${language}.png`) });
    // The panel hidden: Board keeps its layout in its hidden page, and none of it shows or takes a click over the chat.
    await page.getByRole("button", { name: words.hide, exact: true }).click();
    await page.locator(".chat-browser[hidden]").waitFor({ state: "attached" });
    const hidden = await readChrome();
    assert.ok(hidden.composer && hidden.composer.right > hidden.composer.x, `${width}: the composer is on screen`);
    assert.deepEqual(hidden.painted.filter((box) => intersects(box, hidden.composer)), [],
      `${width}: no part of the hidden canvas paints over the composer: ${JSON.stringify(hidden.painted)}`);
    assert.equal(hidden.hitInCanvas, false, `${width}: a click on the composer never reaches the hidden canvas`);
    await page.screenshot({ path: path.join(screenshots, `board-hidden-${width}-${language}.png`) });
    await page.getByRole("button", { name: words.show, exact: true }).click();
    passed.push(width);
    console.log(`PASS ${width} px: zoom bar inside the canvas; hidden panel shows nothing over the composer`);
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log(JSON.stringify({ passed, screenshots }));
} catch (error) {
  console.error(JSON.stringify({ screenshots, errors, unexpected, passed, requests: workspaceFixture.requests.slice(-8).map((row) => row.name) }));
  await page.screenshot({ path: path.join(screenshots, "failure.png") }).catch(() => {});
  throw error;
} finally {
  for (const stream of streams) stream.end();
  await browser.close(); await new Promise((resolve) => server.close(resolve));
}
