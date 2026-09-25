import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// GH-300: the Hub's attention layer. The real built Hub UI against a synthetic
// local Hub; no provider, project or runtime is involved. Time is Playwright's
// clock, so each reading of the chat list is one jump of the poll interval.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const screenshots = process.env.ATTENTION_SCREENSHOTS ?? await mkdtemp(path.join(tmpdir(), "monkeyhub-attention-"));
await mkdir(screenshots, { recursive: true });
const POLL_MS = 4000, FADE_MS = 8000;

const projectDir = "D:\\fixture\\住宅";
const runtimeId = "runtime-house";
let moment = Date.parse("2026-09-25T09:00:00Z");
const stamp = () => new Date(moment += 1000).toISOString().replace("Z", "+00:00");
const chat = (id, title, status, extra = {}) => ({ id, projectId: "house", projectDir, title, provider: "codex", model: null, status,
  archived: false, createdAt: "2026-09-25T08:00:00+00:00", updatedAt: stamp(), error: null, sourceSessionId: null, attention: null, ...extra });
const sessions = [
  chat("chat-facade", "立面研究", "idle"),
  chat("chat-site", "场地分析", "running"),
  // Waiting and failed before the page opened: the baseline, never news.
  chat("chat-structure", "结构选型", "running", { attention: "permission" }),
  chat("chat-materials", "材料清单", "failed", { error: { code: "CHAT_PROCESS_FAILED", detail: "The CLI exited with code 1." } }),
  chat("chat-sun", "日照模拟", "running"),
  chat("chat-roof", "屋面排水", "running"),
];
const byId = (id) => sessions.find((row) => row.id === id);
const change = (id, patch) => Object.assign(byId(id), patch, { updatedAt: stamp() });
const messagesFor = (id) => id === "chat-facade" ? [
  { id: "m1", role: "user", content: "把南立面的窗墙比控制在 0.4 以内。", createdAt: "2026-09-25T08:01:00+00:00", status: "complete", attachments: [], documents: [] },
  { id: "m2", role: "assistant", content: "已把南立面窗墙比调到 0.38，东西两侧保持不变。", createdAt: "2026-09-25T08:01:30+00:00", status: "complete", attachments: [], documents: [] },
] : [];

const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    res.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: 1, kind: "snapshot", snapshot: runtimeSnapshot() })}\n\n`);
    return;
  }
  // A Hub page framed inside another page, as the Fabrication tool is inside the Hub.
  if (pathname === "/framed") {
    res.setHeader("Content-Type", "text/html");
    res.end(`<!doctype html><title>frame</title><iframe src="/?view=fab" style="width:800px;height:600px"></iframe>`);
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

const userSettings = { language: "zh-CN", theme: "light", fontScale: 1, autoUpdate: true };
const launch = { projectDir, workspaceDir: null, referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18788 };
const runtime = () => ({ runtimeId, projectId: "house", projectDir, state: "open", operations: [], retained: null,
  // Not "ready": the hidden Modeling mount stays out of this test.
  projection: "unknown", clients: 1, error: null,
  workers: [{ workerId: runtimeId, serviceId: "studio", projectId: "house", projectDir, instanceId: "instance-1", processId: 4321,
    desiredState: "running", healthy: true, state: "ready", url: `${origin}/api/runtime/projects/${runtimeId}/studio/`, error: null }],
  sessions: sessions.map((row) => ({ ...row })) });
const runtimeSnapshot = () => ({ serverId: "fixture-hub", sequence: 1, workers: [], projects: [runtime()] });
const apps = () => ["monkeyarch", "monkeyboard", "monkeyrender"].map((appId) => ({ appId, title: appId, serviceId: "studio", available: true,
  state: "running", processId: 4321, url: `${origin}/?view=arch&runtimeId=${runtimeId}`, apiUrl: `${origin}/api/runtime/projects/${runtimeId}/studio/` }));
const updateStatus = () => ({ currentVersion: "fixture-desktop", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null,
  canApply: false, message: null, error: null, channel: "unsigned-prerelease", autoUpdate: true, nextLaunch: false,
  check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } });

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, locale: "zh-CN" });
const page = await context.newPage();
page.setDefaultTimeout(12000);
const errors = [], unexpected = [];
let sessionReads = 0, documentLoads = 0;
page.on("pageerror", (error) => errors.push(error.message));
page.on("request", (request) => { if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documentLoads++; });
await context.route((url) => url.pathname.startsWith("/api/"), async (route) => {
  const request = route.request(), url = new URL(request.url()), method = request.method();
  if (url.pathname === "/api/runtime/events") return route.continue();
  const json = (body, status = 200) => route.fulfill({ json: body, status });
  if (url.pathname === "/api/chat/sessions" && method === "GET") {
    if (url.searchParams.get("archived") === "true") return json([]);
    sessionReads++;
    return json(sessions.map((row) => ({ ...row })));
  }
  const detail = /^\/api\/chat\/sessions\/([^/]+)$/.exec(url.pathname);
  if (detail && method === "GET") {
    const row = byId(decodeURIComponent(detail[1]));
    return row ? json({ ...row, messages: messagesFor(row.id) }) : json({ code: "CHAT_NOT_FOUND", detail: "This chat does not exist." }, 404);
  }
  if (url.pathname === "/api/chat/projects") return json([{ projectId: "house", projectDir, name: "house", chatCount: sessions.length, version: 3, stage: "S2" }]);
  if (url.pathname === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture only", installed: true,
    signedIn: true, models: ["fixture-model-a"], modelCatalog: "ready", modelDetail: "Listed by the fixture CLI." }]);
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: ["住宅"] });
  if (url.pathname === "/api/settings/user") return json(userSettings);
  if (url.pathname === "/api/settings/apps") return json(launch);
  if (url.pathname === "/api/apps") return json(apps());
  if (url.pathname === "/api/runtime") return json(runtimeSnapshot());
  if (url.pathname === "/api/runtime/projects/open") return json(runtime());
  if (url.pathname === `/api/runtime/projects/${runtimeId}/studio/api/project`) return json({ projectId: "house" });
  if (url.pathname === "/api/updates/status") return json(updateStatus());
  // Fabrication's own reads are not this test's subject.
  if (url.pathname.startsWith("/api/fab/")) return json({ code: "FAB_UNAVAILABLE", detail: "Not part of this fixture." }, 503);
  unexpected.push(`${method} ${url.pathname}`);
  return json({ detail: "Not part of this fixture" }, 404);
});

// The OS side: a Notification the page cannot tell from the browser's. It is
// granted asynchronously, as a person answering a prompt would.
await context.addInitScript(() => {
  const record = { notes: [], asks: 0 };
  class FixtureNotification {
    static permission = "default";
    static requestPermission() {
      record.asks += 1;
      return Promise.resolve().then(() => { FixtureNotification.permission = "granted"; return "granted"; });
    }
    constructor(title, options = {}) {
      Object.assign(this, { title, body: options.body, tag: options.tag, closed: false, onclick: null, onclose: null });
      record.notes.push(this);
    }
    close() { if (!this.closed) { this.closed = true; this.onclose?.(); } }
  }
  Object.defineProperty(window, "Notification", { configurable: true, writable: true, value: FixtureNotification });
  Object.defineProperty(window, "__attention", { value: record });
});
await context.clock.install({ time: new Date("2026-09-25T09:00:00Z") });

const toasts = page.locator(".attention-toast");
const toast = (text) => toasts.filter({ hasText: text });
const until = async (read, label, timeout = 8000) => {
  const deadline = Date.now() + timeout;
  while (!await read()) { if (Date.now() > deadline) assert.fail(label); await page.waitForTimeout(25); }
};
/** One reading by the attention layer: a jump of the poll interval, then its answer on screen. */
const poll = async (times = 1) => {
  for (let index = 0; index < times; index++) {
    const before = sessionReads;
    await page.clock.fastForward(POLL_MS);
    await until(() => sessionReads > before, "the attention layer read the chat list");
    await page.waitForTimeout(150);
  }
};
const shown = async () => (await toasts.allInnerTexts()).map((text) => text.replace(/\s+/g, " ").trim());
const setVisibility = (state) => page.evaluate((value) => {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => value });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => value === "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
}, state);
const notes = () => page.evaluate(() => window.__attention.notes.map(({ title, body, tag, closed }) => ({ title, body, tag, closed })));
const box = (locator) => locator.evaluate((element) => { const { left, top, right, bottom } = element.getBoundingClientRect(); return { left, top, right, bottom }; });

const passed = [];
async function step(name, action) { await action(); passed.push(name); console.log(`PASS ${name}`); }
try {
  await page.goto(`${origin}/?chatId=chat-facade`);
  await page.locator(".chat-shell").waitFor();
  await until(() => sessionReads >= 1, "the chat list was read");

  await step("nothing is announced on first load", async () => {
    await poll(2);
    assert.equal(await toasts.count(), 0, "a waiting permission and an old failure at load are the baseline");
    assert.equal(await page.locator("section.attention-toasts[aria-live=polite]").count(), 1, "one polite live region");
    assert.equal(await page.title(), "MonkeyHub");
  });

  await step("one notice per transition, and never twice", async () => {
    change("chat-site", { status: "idle" });
    await poll();
    await toast("「场地分析」已完成").waitFor();
    assert.deepEqual(await shown(), ["「场地分析」已完成 · 查看"]);
    await poll();
    assert.equal(await toasts.count(), 1, "the same moment is announced once");
  });

  await step("a notice being read does not fade", async () => {
    // One notice, so nothing moves under the resting pointer.
    await toast("「场地分析」已完成").hover();
    await page.clock.fastForward(FADE_MS + 1000);
    await page.clock.fastForward(500);
    await page.waitForTimeout(150);
    assert.equal(await toast("「场地分析」已完成").getAttribute("data-leaving"), null, "held past its eight seconds while pointed at");
    await page.mouse.move(0, 0);
    await page.clock.fastForward(FADE_MS + 1000);
    await page.clock.fastForward(500);
    await toast("「场地分析」已完成").waitFor({ state: "detached" });
  });

  await step("a waiting permission stays; news fades", async () => {
    change("chat-site", { status: "running" });
    await poll();
    assert.equal(await toasts.count(), 0, "a turn starting is not news");
    change("chat-site", { status: "idle" });
    change("chat-sun", { attention: "permission" });
    change("chat-roof", { status: "failed", error: { code: "CHAT_PROCESS_FAILED", detail: "The CLI exited with code 2." } });
    await poll();
    await toast("「日照模拟」需要你的授权").waitFor();
    await toast("「屋面排水」出错了").waitFor();
    assert.deepEqual(await shown(), ["「场地分析」已完成 · 查看", "「日照模拟」需要你的授权 · 查看", "「屋面排水」出错了 · 查看"]);
    await page.screenshot({ path: path.join(screenshots, "attention-toasts-zh.png") });
    await page.locator(".attention-toasts").screenshot({ path: path.join(screenshots, "attention-stack-zh.png") });
    const stack = await box(page.locator(".attention-toasts")), rail = await box(page.locator(".chat-rail"));
    const composer = await box(page.locator(".chat-composer-wrap"));
    assert.ok(stack.right <= rail.left, "the stack stays clear of the rail");
    assert.ok(stack.bottom <= composer.top, "and of the composer it would otherwise cover");
    await page.clock.fastForward(FADE_MS + 1000);
    await page.clock.fastForward(500);
    await toast("「场地分析」已完成").waitFor({ state: "detached" });
    await toast("「屋面排水」出错了").waitFor({ state: "detached" });
    await page.clock.fastForward(60_000);
    await page.clock.fastForward(60_000);
    await page.waitForTimeout(150);
    assert.deepEqual(await shown(), ["「日照模拟」需要你的授权 · 查看"], "a waiting permission stays until it is opened or dismissed");
  });

  await step("on a narrow window the stack rises above the composer", async () => {
    await page.setViewportSize({ width: 420, height: 860 });
    // The project list overlays the conversation at this width; it is closed while writing.
    await page.getByRole("button", { name: "收起项目栏" }).first().click();
    await page.waitForTimeout(300);
    const stack = await box(page.locator(".attention-toasts")), composer = await box(page.locator(".chat-composer-wrap"));
    const rail = await box(page.locator(".chat-rail"));
    assert.ok(stack.bottom <= composer.top, `the stack (${stack.bottom}) ends above the composer (${composer.top})`);
    assert.ok(stack.right <= rail.left, "and left of the rail");
    await page.screenshot({ path: path.join(screenshots, "attention-narrow-zh.png") });
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.getByRole("button", { name: "展开项目栏" }).first().click();
  });

  await step("查看 asks the page to open the chat, without a reload", async () => {
    await page.evaluate(() => {
      window.__opened = [];
      window.__openListener = (event) => { window.__opened.push(event.detail); event.preventDefault(); };
      window.addEventListener("monkeyhub:open-chat", window.__openListener);
      window.__sameDocument = true;
    });
    const loads = documentLoads;
    await toast("「日照模拟」需要你的授权").getByRole("button", { name: "查看" }).click();
    assert.deepEqual(await page.evaluate(() => window.__opened), [{ chatId: "chat-sun", projectDir }]);
    await toast("「日照模拟」需要你的授权").waitFor({ state: "detached" });
    await page.clock.fastForward(1000);
    await page.waitForTimeout(150);
    assert.equal(documentLoads, loads);
    assert.equal(await page.evaluate(() => window.__sameDocument), true);
  });

  await step("with nobody answering, the Hub loads that chat by its address", async () => {
    // Fabrication shows no conversation, so nothing on that page answers 查看.
    await page.evaluate(() => window.removeEventListener("monkeyhub:open-chat", window.__openListener));
    await page.goto(`${origin}/?view=fab`);
    await page.locator("main.fab-page").waitFor();
    await poll(2);
    change("chat-materials", { status: "running", error: null });
    await poll();
    assert.equal(await toasts.count(), 0, "a turn starting is not news");
    change("chat-materials", { status: "failed", error: { code: "CHAT_TIMEOUT", detail: "The CLI did not finish within this turn's time limit." } });
    await poll();
    await toast("「材料清单」出错了").waitFor();
    const loads = documentLoads;
    const arrived = page.waitForURL((url) => url.searchParams.get("chatId") === "chat-materials" && !url.searchParams.has("view"));
    await toast("「材料清单」出错了").getByRole("button", { name: "查看" }).click();
    await page.clock.fastForward(400);
    await arrived;
    await page.locator(".chat-shell").waitFor();
    assert.equal(documentLoads, loads + 1);
    await until(() => page.evaluate(() => document.readyState === "complete"), "the Hub reloaded");
    await poll(2);
    assert.equal(await toasts.count(), 0, "a reload starts from a new baseline; nothing is replayed");
  });

  await step("the chat on screen raises nothing", async () => {
    change("chat-materials", { status: "running", error: null });
    change("chat-site", { status: "running" });
    await poll();
    change("chat-materials", { status: "idle" });
    change("chat-site", { status: "idle" });
    await poll();
    await toast("「场地分析」已完成").waitFor();
    assert.deepEqual(await shown(), ["「场地分析」已完成 · 查看"], "only the chat that is not on screen");
  });

  await step("a hidden window gets one OS notification per moment and a title count", async () => {
    await page.clock.fastForward(FADE_MS + 1000);
    await page.clock.fastForward(500);
    await toast("「场地分析」已完成").waitFor({ state: "detached" });
    await setVisibility("hidden");
    change("chat-structure", { status: "idle", attention: null });
    await poll();
    await until(async () => (await notes()).length === 1, "the first moment while hidden reaches the OS");
    assert.equal(await page.evaluate(() => window.__attention.asks), 1, "permission is asked for once");
    assert.equal(await page.title(), "(1) MonkeyHub");
    // The chat that was on screen is not on screen while the window is hidden.
    change("chat-materials", { status: "running" });
    await poll();
    change("chat-materials", { status: "failed", error: { code: "CHAT_PROCESS_FAILED", detail: "The CLI exited with code 3." } });
    await poll();
    await until(async () => (await notes()).length === 2, "the second moment reaches the OS");
    assert.equal(await page.title(), "(2) MonkeyHub");
    const [first, second] = await notes();
    assert.deepEqual([first.title, second.title], ["「结构选型」已完成", "「材料清单」出错了"]);
    assert.equal(first.body, "点击查看");
    assert.notEqual(first.tag, second.tag);
    await poll(2);
    assert.equal((await notes()).length, 2, "nothing repeats");
    assert.equal(await page.evaluate(() => window.__attention.asks), 1, "and nothing is asked again");
    assert.equal(await page.title(), "(2) MonkeyHub");
    await setVisibility("visible");
    await until(async () => await page.title() === "MonkeyHub", "the count resets when the window is seen");
    assert.ok((await notes()).every((note) => note.closed), "the OS copies are closed once the window is seen");
    await page.waitForTimeout(150);
    assert.deepEqual(await shown(), ["「结构选型」已完成 · 查看"], "the chat on screen speaks for itself");
  });

  await step("the Hub shell answers 查看 by opening the chat in place", async () => {
    const loads = documentLoads;
    await toast("「结构选型」已完成").getByRole("button", { name: "查看" }).click();
    await until(() => page.evaluate(() => new URL(location.href).searchParams.get("chatId") === "chat-structure"),
      "the shell selected the chat");
    await page.clock.fastForward(1000);
    await page.waitForTimeout(150);
    assert.equal(documentLoads, loads, "the shell answered the event, so the page did not reload");
  });

  await step("a Hub page framed inside another leaves notices to that page", async () => {
    const framed = await context.newPage();
    await framed.goto(`${origin}/framed`);
    const inner = framed.frameLocator("iframe");
    await inner.locator("main.fab-page").waitFor();
    assert.equal(await inner.locator(".attention-toasts").count(), 0);
    // The same view as a window of its own carries them.
    await framed.goto(`${origin}/?view=fab`);
    await framed.locator("main.fab-page").waitFor();
    assert.equal(await framed.locator(".attention-toasts").count(), 1);
    await framed.close();
  });

  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log(JSON.stringify({ passed, sessionReads, screenshots }));
} catch (error) {
  console.error(JSON.stringify({ screenshots, errors, unexpected, passed }));
  await page.screenshot({ path: path.join(screenshots, "failure.png") }).catch(() => {});
  throw error;
} finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
