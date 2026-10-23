import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createProjectWorkspaceFixture } from "./projectWorkspaceFixture.mjs";

// The attention shell (?shell=attention) over the real built Hub: the projects column with what
// waits on the architect, the Attention, Board and Model tabs over the same open surfaces, the
// conversation drawer above the composer, the side panel's pages and the phone drawers.
// ATTENTION_SHELL_SHOTS=<dir> saves desktop and phone, light and dark. With ATTENTION_SHELL_STYLE=board the
// shots are drawn in that interface style too, on a build that has it.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const shots = process.env.ATTENTION_SHELL_SHOTS ? path.resolve(process.env.ATTENTION_SHELL_SHOTS) : null;
const now = new Date().toISOString();
const projects = [
  { projectId: "A", projectDir: "D:\\fixture\\A", name: "Thi Hemp 墙身节点", chatCount: 2, version: 3, stage: "S2" },
  { projectId: "B", projectDir: "D:\\fixture\\B", name: "Housing 13 户", chatCount: 1, version: 0, stage: null },
];
const message = (id, role, content) => ({ id, role, content, status: "complete", createdAt: now });
const sessions = [
  { id: "a1", projectId: "A", projectDir: "D:\\fixture\\A", title: "楼板再薄一点", provider: "codex", status: "idle", createdAt: now, updatedAt: now,
    messages: [message("a1-u", "user", "楼板再薄一点"), message("a1-a", "assistant", "我把 V16 的楼板改成只留一层胶合板。")] },
  { id: "a2", projectId: "A", projectDir: "D:\\fixture\\A", title: "斜撑留不留", provider: "codex", status: "idle", createdAt: now, updatedAt: "2026-10-01T00:00:00Z",
    messages: [message("a2-u", "user", "斜撑留不留"), { id: "a2-p", role: "tool", content: "", status: "pending", createdAt: now,
      permission: { id: "perm-1", title: "允许运行 model-split v2 吗？", options: [{ optionId: "once", name: "允许一次" }] } }] },
  { id: "b1", projectId: "B", projectDir: "D:\\fixture\\B", title: "东南角入口", provider: "codex", status: "idle", createdAt: now, updatedAt: now, messages: [] },
];
const summary = (session) => ({ ...session, messages: undefined, attention: session.messages.some((item) => item.permission) ? "permission" : null });
const runtimes = new Map();
const workspace = await createProjectWorkspaceFixture(runtimes, sessions);
const apps = new Map();
let origin = "";
const appsFor = (projectDir) => {
  if (!apps.has(projectDir)) apps.set(projectDir, ["monkeyarch", "monkeyboard", "monkeyrender", "monkeyfab", "monkeymonitor"].map((appId) => ({
    appId, title: appId, serviceId: appId === "monkeyfab" ? "hub" : appId === "monkeymonitor" ? "monitor" : "studio", state: "stopped",
    available: true, processId: null, url: null, apiUrl: null, error: null })));
  return apps.get(projectDir);
};
const start = (projectDir) => {
  const runtime = runtimes.get(projectDir);
  for (const app of appsFor(projectDir).filter((row) => row.serviceId === "studio")) {
    Object.assign(app, { state: "running", processId: 2000, url: `${origin}/?view=${app.appId === "monkeyboard" ? "board" : "arch"}&runtimeId=${runtime.runtimeId}`,
      apiUrl: `${origin}/api/runtime/projects/${runtime.runtimeId}/studio/` });
  }
};
const snapshot = () => ({ serverId: "fixture-hub", sequence: 1, workers: [], projects: [...runtimes.values()].map((runtime) => {
  const app = appsFor(runtime.projectDir).find((row) => row.appId === "monkeyarch");
  return { ...runtime, workers: app.processId ? [{ workerId: runtime.runtimeId, serviceId: "studio", projectId: runtime.projectId, projectDir: runtime.projectDir,
    instanceId: "instance-2000", processId: 2000, desiredState: "running", healthy: true, state: "ready", url: app.apiUrl, error: null }] : [],
    sessions: sessions.filter((row) => row.projectId === runtime.projectId).map(summary) };
}) });
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    response.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: 1, kind: "snapshot", snapshot: snapshot() })}\n\n`);
    return;
  }
  const file = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  if (!file.startsWith(root)) { response.writeHead(404); response.end(); return; }
  try {
    response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".wasm") ? "application/wasm"
      : file.endsWith(".woff2") ? "font/woff2" : "text/html");
    response.end(await readFile(file));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
origin = `http://127.0.0.1:${server.address().port}`;
const unexpected = [];
// The theme each pass loads with, as the saved preferences say it.
let preferredTheme = "light";
const route = async (handler) => {
  const request = handler.request(), url = new URL(request.url()), method = request.method();
  if (url.pathname === "/api/runtime/events") return handler.continue();
  if (await workspace.handle(handler, url)) return;
  const json = (body, status = 200) => handler.fulfill({ json: body, status });
  const data = () => request.postDataJSON();
  if (url.pathname === "/api/settings/user") return json({ language: "zh-CN", theme: preferredTheme, fontScale: 1, sidebarPinned: true });
  if (url.pathname === "/api/settings/apps") return json({ projectDir: "D:\\fixture\\A", referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18790 });
  if (url.pathname === "/api/credentials") return json([]);
  if (url.pathname === "/api/updates/status") return json({ currentVersion: "fixture", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null,
    canApply: false, message: null, error: null, channel: "unsigned-prerelease", autoUpdate: false, nextLaunch: false,
    check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } });
  if (url.pathname === "/api/apps") return json(url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : appsFor("hub"));
  const app = url.pathname.match(/^\/api\/apps\/([^/]+)\/start$/);
  if (app && method === "POST") { start(url.searchParams.get("projectDir")); return json(appsFor(url.searchParams.get("projectDir")).find((row) => row.appId === app[1])); }
  if (url.pathname === "/api/runtime") return json(snapshot());
  if (url.pathname === "/api/runtime/projects/open" && method === "POST") {
    const body = data();
    if (!runtimes.has(body.projectDir)) runtimes.set(body.projectDir, { runtimeId: randomUUID(), ...body, state: "open", operations: [], retained: null,
      projection: "ready", clients: 1, error: null });
    return json(snapshot().projects.find((row) => row.projectDir === body.projectDir));
  }
  if (url.pathname === "/api/project/modeling" && method === "POST") {
    const projectDir = url.searchParams.get("projectDir");
    start(projectDir);
    return json({ projectId: projects.find((row) => row.projectDir === projectDir).projectId, initialized: true });
  }
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: projects.map((row) => row.projectId) });
  if (url.pathname === "/api/chat/projects") return json(projects);
  if (url.pathname === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture", installed: true, signedIn: true,
    models: ["fixture-model"], modelCatalog: "ready", modelDetail: "Fixture" }]);
  if (url.pathname === "/api/chat/sessions" && method === "GET") return json(url.searchParams.get("archived") === "true" ? [] : sessions.map(summary));
  const chat = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)$/);
  if (chat && method === "GET") { const session = sessions.find((row) => row.id === chat[1]); return session ? json({ ...session, attention: summary(session).attention }) : json({ code: "CHAT_NOT_FOUND", detail: "No such chat" }, 404); }
  unexpected.push(`${method} ${url.pathname}`);
  return json({ code: "FIXTURE_NOT_SERVED", detail: `${method} ${url.pathname}` }, 404);
};

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
if (shots) await mkdir(shots, { recursive: true });
const errors = [];
const shoot = async (page, name) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) }); };
const style = (page, value, family) => page.evaluate(([value, family]) => {
  document.documentElement.dataset.uiStyle = value; document.documentElement.dataset.uiFamily = family; }, [value, family]);
const look = process.env.ATTENTION_SHELL_STYLE ?? null;
try {
  // Desktop.
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage(); page.setDefaultTimeout(15_000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), route);
  await page.goto(`${origin}/?shell=attention`);
  const shell = page.locator(".attention-shell");
  await shell.waitFor();
  if (look) await style(page, look, look);
  assert.equal(await page.locator(".chat-shell").count(), 0, "the workbench layout is not drawn");
  // The projects column: Attention with what waits on the architect, each project and its latest conversations.
  const attentionRow = page.locator(".attention-shell__row--attention");
  await attentionRow.locator(".attention-shell__count").filter({ hasText: "1" }).waitFor();
  await page.locator(".attention-shell__projects .attention-shell__row").filter({ hasText: "Thi Hemp" }).locator(".attention-shell__count").filter({ hasText: "1" }).waitFor();
  assert.deepEqual(await page.locator(".attention-shell__recent").first().locator(".chat-thread__title").allTextContents(), ["楼板再薄一点", "斜撑留不留"],
    "a project's conversations, newest first");
  assert.equal(await page.locator(".menu-tab[aria-pressed=true]").textContent(), "关注");
  await page.getByText("现在没有要你处理的").or(page.getByText("1 项需要关注")).first().waitFor();
  assert.ok(await page.locator(".attention-shell__drawer").isHidden(), "the conversation waits in its drawer");
  assert.ok(await page.locator(".attention-shell .chat-composer").isVisible(), "the composer is always there");
  await shoot(page, "attention-desktop-light");

  // A conversation opens its drawer above the composer.
  await page.locator(".attention-shell__recent .chat-thread").filter({ hasText: "楼板再薄一点" }).click();
  await page.locator(".attention-shell__drawer").getByText("我把 V16 的楼板改成只留一层胶合板。").waitFor();

  // Board and Model are the same open surfaces as the workbench's; Alt+1 returns to Attention.
  await page.locator(".attention-shell__tabs .menu-tab").filter({ hasText: "Board" }).click();
  await page.locator(".attention-shell__surfaces .chat-project-workspace:not([hidden])").waitFor();
  await page.locator(".attention-shell__surfaces .monkeyboard-canvas canvas").last().waitFor();
  await page.waitForFunction(() => !document.querySelector(".attention-shell__surfaces .boot"));
  assert.equal(await page.locator(".menu-tab[aria-pressed=true]").textContent(), "Board");
  assert.ok(await page.locator(".attention-shell__queue").isHidden());
  await shoot(page, "attention-board-desktop-light");
  await page.locator("body").click({ position: { x: 700, y: 870 } }).catch(() => undefined);
  await page.locator(".attention-shell__top").click({ position: { x: 1200, y: 20 } });
  await page.keyboard.press("Alt+1");
  await page.locator(".attention-shell__queue:not([hidden])").waitFor();
  await page.keyboard.press("Alt+3");
  await page.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "模型");

  // The side panel: background, and the advanced tools that run the command tree.
  await page.locator(".attention-shell__rail-item").filter({ hasText: "高级工具" }).click();
  await page.locator(".advanced-tools__group h3").first().waitFor();
  assert.ok((await page.locator(".advanced-tools__group h3").allTextContents()).includes("外壳：文件、视图、对话"));
  await shoot(page, "attention-tools-desktop-light");
  await page.locator(".advanced-tools__command").filter({ hasText: /^设置/ }).first().click();
  await page.locator("dialog.chat-dialog--settings[open]").waitFor();
  await page.keyboard.press("Escape");
  await page.locator(".attention-shell__rail-item").filter({ hasText: "后台" }).click();
  await page.locator(".attention-shell__panel .chat-tasks").waitFor();
  // Ctrl+K from the top row's search.
  await page.locator(".attention-shell__search").click();
  await page.locator("dialog.command-palette[open]").waitFor();
  await page.keyboard.press("Escape");

  await context.close();
  preferredTheme = "dark";
  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const darkPage = await dark.newPage(); darkPage.setDefaultTimeout(15_000);
  darkPage.on("pageerror", (error) => errors.push(error.message));
  await darkPage.route((url) => url.pathname.startsWith("/api/"), route);
  await darkPage.goto(`${origin}/?shell=attention`);
  await darkPage.waitForFunction(() => document.documentElement.dataset.theme === "dark" && document.querySelector(".attention-shell"));
  if (look) await style(darkPage, look, look);
  await darkPage.locator(".attention-shell__row--attention .attention-shell__count").waitFor();
  await shoot(darkPage, "attention-desktop-dark");
  await dark.close();

  // Phone: the projects column and the side panel open over the center.
  for (const colour of ["light", "dark"]) {
    preferredTheme = colour;
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    const small = await phone.newPage(); small.setDefaultTimeout(15_000);
    small.on("pageerror", (error) => errors.push(error.message));
    await small.route((url) => url.pathname.startsWith("/api/"), route);
    await small.goto(`${origin}/?shell=attention`);
    await small.waitForFunction((colour) => document.documentElement.dataset.theme === colour && document.querySelector(".attention-shell"), colour);
    if (look) await style(small, look, look);
    assert.ok(await small.locator(".attention-shell__left").isHidden(), `${colour}: the projects column folds away`);
    assert.ok(await small.locator(".attention-shell .chat-composer").isVisible(), `${colour}: the composer stays`);
    const width = await small.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(width <= 390, `${colour}: no sideways scroll (${width}px)`);
    await shoot(small, `attention-phone-${colour}`);
    await small.locator(".attention-shell__left-toggle").click();
    await small.locator(".attention-shell__left").waitFor();
    await shoot(small, `attention-phone-projects-${colour}`);
    await small.locator(".attention-shell__scrim--left").click({ position: { x: 370, y: 400 } });
    await small.locator(".attention-shell__left").waitFor({ state: "hidden" });
    await small.getByRole("button", { name: "展开后台" }).click();
    await small.locator(".attention-shell__panel").waitFor();
    await shoot(small, `attention-phone-side-${colour}`);
    await phone.close();
  }
  assert.deepEqual(errors, [], "page errors");
  console.log(JSON.stringify({ unexpected: [...new Set(unexpected)] }));
  console.log("PASS attention shell: projects column, tabs over the open surfaces, conversation drawer, side panel, phone drawers");
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
