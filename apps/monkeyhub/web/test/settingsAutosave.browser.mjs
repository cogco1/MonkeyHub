import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// GH-302: Hub settings save themselves. The real built Hub UI against a
// synthetic local Hub; no provider, project or runtime is involved.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const screenshots = process.env.SETTINGS_AUTOSAVE_SCREENSHOTS ?? await mkdtemp(path.join(tmpdir(), "monkeyhub-settings-autosave-"));
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    res.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: 1, kind: "snapshot", snapshot: runtimeSnapshot() })}\n\n`);
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

// The one saved preferences document, and the launch settings, as the Hub keeps them.
let userSettings = { language: "en", theme: "light", fontScale: 1, autoUpdate: true };
let launch = { projectDir: null, workspaceDir: null, referenceRun: null, cadExport: "occt", studioPort: 18789, monitorPort: 18788 };
let studioProcess = null, userGate = null;
// #334: what the Hub keeps about keys: whether one is saved, never shown back.
const keys = { gemini: null, "coding-plan": null };
const keyWrites = [], linkOpens = [], signIns = [];
const keyStatus = (id) => ({ id, configured: keys[id] !== null, source: keys[id] !== null ? "saved" : null, variable: null,
  saved: keys[id] !== null, storeAvailable: true });
const userWrites = [], launchWrites = [], userReads = [], errors = [], unexpected = [];
const runtimeSnapshot = () => ({ serverId: "fixture-hub", sequence: 1, workers: [], projects: [] });
const apps = () => ["monkeyarch", "monkeyboard"].map((appId) => ({ appId, title: appId, serviceId: "studio", available: true,
  state: studioProcess ? "running" : "stopped", processId: studioProcess, url: null, apiUrl: null }));
const updateStatus = () => ({ currentVersion: "fixture-desktop", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null,
  canApply: false, message: null, error: null, channel: "unsigned-prerelease", autoUpdate: userSettings.autoUpdate !== false, nextLaunch: false,
  check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } });

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(12000);
page.on("pageerror", (error) => errors.push(error.message));
await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
  const request = route.request(), url = new URL(request.url()), method = request.method();
  if (url.pathname === "/api/runtime/events") return route.continue();
  const json = (body, status = 200) => route.fulfill({ json: body, status });
  if (url.pathname === "/api/settings/user") {
    if (method === "GET") { userReads.push(Date.now()); return json(userSettings); }
    const body = request.postDataJSON(); userWrites.push(body);
    if (userGate) await userGate;
    userSettings = body; return json(userSettings);
  }
  if (url.pathname === "/api/credentials") return json(Object.keys(keys).map(keyStatus));
  const keyRoute = url.pathname.match(/^\/api\/credentials\/(gemini|coding-plan)$/);
  if (keyRoute && method === "PUT") { keys[keyRoute[1]] = request.postDataJSON().key.trim(); keyWrites.push([keyRoute[1], keys[keyRoute[1]]]); return json(keyStatus(keyRoute[1])); }
  if (keyRoute && method === "DELETE") { keys[keyRoute[1]] = null; return json(keyStatus(keyRoute[1])); }
  if (url.pathname === "/api/credentials/gemini/check") return json({ id: "gemini", result: keys.gemini ? "accepted" : "missing", detail: "Fixture" });
  const linkRoute = url.pathname.match(/^\/api\/links\/([a-z-]+)\/open$/);
  if (linkRoute) { linkOpens.push(linkRoute[1]); return json({ opened: true }, 202); }
  const signInRoute = url.pathname.match(/^\/api\/chat\/providers\/(codex|claude)\/login$/);
  if (signInRoute) { signIns.push(signInRoute[1]); return json({ started: true }, 202); }
  if (url.pathname === "/api/settings/apps") {
    if (method === "GET") return json(launch);
    const body = request.postDataJSON(); launchWrites.push(body);
    // As the Hub does: a running runtime keeps its launch configuration.
    if (studioProcess && body.studioPort !== launch.studioPort) return json({ code: "APPS_RUNNING", detail: "Stop the applications before changing their launch configuration." }, 409);
    launch = body; return json(launch);
  }
  if (url.pathname === "/api/updates/status") return json(updateStatus());
  if (url.pathname === "/api/updates/settings") { userSettings = { ...userSettings, autoUpdate: request.postDataJSON().autoUpdate }; return json(updateStatus()); }
  if (url.pathname === "/api/apps") return json(apps());
  if (url.pathname === "/api/runtime") return json(runtimeSnapshot());
  if (url.pathname === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture only", installed: true,
    signedIn: true, models: ["fixture-model-a"], modelCatalog: "ready", modelDetail: "Listed by the fixture CLI." }]);
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: [] });
  if (url.pathname === "/api/chat/projects" || url.pathname === "/api/chat/sessions") return json([]);
  unexpected.push(`${method} ${url.pathname}`); return json({ detail: "Not part of this fixture" }, 404);
});

// The dialog is found by its place, not its heading: the heading changes language here.
const dialog = page.locator("dialog.chat-dialog--settings");
const status = page.locator("#settings-save-state");
const until = async (read, label) => {
  const deadline = Date.now() + 8000;
  while (!await read()) { if (Date.now() > deadline) assert.fail(label); await page.waitForTimeout(25); }
};
const openSettings = async (name = "Hub settings") => { await page.getByRole("button", { name, exact: true }).click(); await dialog.waitFor(); };
// #328: Settings is paged; each page opens from the list beside it.
const openPage = (name) => dialog.getByRole("tab", { name, exact: true }).click();
const passed = [];
async function step(name, action) { await action(); passed.push(name); console.log(`PASS ${name}`); }
try {
  await page.goto(origin);
  await page.getByRole("button", { name: "Hub settings", exact: true }).waitFor();
  await openSettings();
  await status.filter({ hasText: /^Saved$/ }).waitFor();

  await step("settings have no Save buttons and a choice saves at once", async () => {
    assert.equal(await dialog.getByRole("button", { name: /^Save/ }).count(), 0, "no Save button is left in Hub settings");
    assert.equal(await page.locator("#save-appearance, #save-launch").count(), 0);
    let release; userGate = new Promise((resolve) => { release = resolve; });
    await page.locator("#theme").selectOption("dark");
    await until(() => userWrites.length === 1, "choosing a theme writes the preferences document");
    await status.filter({ hasText: /^Saving…$/ }).waitFor();
    await dialog.screenshot({ path: path.join(screenshots, "settings-saving-en.png") });
    userGate = null; release();
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(userWrites[0].theme, "dark");
    assert.equal(userWrites[0].autoUpdate, true, "a settings save keeps fields it does not edit");
    assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  });

  await step("Settings opens on Display, its pages follow the list, and a style card saves at once", async () => {
    assert.equal(await dialog.getByRole("tab", { name: "Display", exact: true }).getAttribute("aria-selected"), "true");
    assert.deepEqual(await dialog.getByRole("tab").allInnerTexts(), ["Display", "Conversations", "AI Render", "Workspace", "Software update"]);
    const before = userWrites.length;
    await dialog.getByRole("radio", { name: /^Title block/ }).check();
    await until(() => userWrites.length === before + 1, "choosing a style writes the preferences document");
    assert.equal(userWrites.at(-1).uiStyle, "titleblock");
    assert.equal(await page.locator("html").getAttribute("data-ui-style"), "titleblock");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    await dialog.screenshot({ path: path.join(screenshots, "settings-display-titleblock-en.png") });
    // Arrows, Home and End move along the list and open each page.
    await dialog.getByRole("tab", { name: "Display", exact: true }).focus();
    await page.keyboard.press("ArrowDown");
    assert.equal(await dialog.getByRole("tab", { name: "Conversations", exact: true }).getAttribute("aria-selected"), "true");
    await page.locator("#default-chat-provider").waitFor();
    await page.keyboard.press("End");
    await dialog.getByRole("heading", { name: "Software update", exact: true }).waitFor();
    await page.keyboard.press("Home");
    await page.locator("#theme").waitFor();
    await dialog.getByRole("radio", { name: /^Classic/ }).check();
    await until(() => userWrites.at(-1)?.uiStyle === "classic", "Classic is saved back");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
  });

  await step("a key goes in once, is never shown again, and the page says where the key in use comes from", async () => {
    await openPage("AI Render");
    const field = page.locator("#gemini-key");
    assert.equal(await field.getAttribute("type"), "password");
    await page.locator("#gemini-key-state").filter({ hasText: /^Not set\.$/ }).waitFor();
    await field.fill("AIzaSyFixture-settings-key-01");
    await field.press("Enter");
    await page.locator("#gemini-key-state").filter({ hasText: "Saved in this computer's Windows Credential Manager, for this account only." }).waitFor();
    assert.equal(await field.inputValue(), "", "the field empties once the key is saved");
    assert.deepEqual(keyWrites, [["gemini", "AIzaSyFixture-settings-key-01"]]);
    await dialog.getByRole("button", { name: "Check", exact: true }).click();
    await dialog.getByText("Google accepted this key.", { exact: true }).waitFor();
    await dialog.getByRole("button", { name: "Get a key ↗", exact: true }).click();
    await until(() => linkOpens.includes("gemini-keys"), "the key page opens through the Hub, never in this window");
    assert.equal(await dialog.getByRole("button", { name: /^Save/ }).count(), 0, "a key saves itself too");
    await dialog.screenshot({ path: path.join(screenshots, "settings-render-key-en.png") });
    await openPage("Conversations");
    await page.locator("#coding-plan-provider").selectOption("zhipu");
    await until(() => userWrites.at(-1)?.codingPlanBaseUrl === "https://open.bigmodel.cn/api/anthropic", "a preset fills and saves the endpoint");
    assert.equal(await page.locator("#coding-plan-url").inputValue(), "https://open.bigmodel.cn/api/anthropic");
    await page.locator("#coding-plan-url").fill("not an address");
    await dialog.getByRole("alert").filter({ hasText: "The Coding Plan endpoint is a full http(s) address" }).waitFor();
    await page.locator("#coding-plan-url").fill("https://open.bigmodel.cn/api/anthropic");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    await dialog.getByRole("button", { name: "Sign in again", exact: true }).click();
    await until(() => signIns.includes("codex"), "Codex sign-in opens from Settings");
    await dialog.getByText("The sign-in window is open. Come back here when it is done.", { exact: true }).waitFor();
    assert.equal(await dialog.getByRole("button", { name: "Sign in", exact: true }).isDisabled(), true, "a CLI that is not installed cannot be signed in");
    await dialog.screenshot({ path: path.join(screenshots, "settings-conversations-keys-en.png") });
    for (const text of [JSON.stringify(userWrites), JSON.stringify(Object.keys(keys).map(keyStatus))]) {
      assert.equal(text.includes("AIzaSyFixture-settings-key-01"), false, "no preference or status carries a key");
    }
  });

  await step("typed text is saved once, after its pause", async () => {
    const before = userWrites.length;
    await openPage("AI Render");
    await page.locator("#render-model").pressSequentially("gemini-3.1-flash-image", { delay: 20 });
    await status.filter({ hasText: /^Saving…$/ }).waitFor();
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(userWrites.length, before + 1, "one request for the whole word");
    assert.equal(userWrites.at(-1).renderModel, "gemini-3.1-flash-image");
  });

  await step("an invalid value is named inline and never saved", async () => {
    const before = userWrites.length;
    await page.locator("#render-timeout").fill("500");
    await status.filter({ hasText: /^Unsaved changes$/ }).waitFor();
    await dialog.getByRole("alert").filter({ hasText: "The render request timeout must be from 1 to 300 seconds." }).waitFor();
    assert.equal(await page.locator("#render-timeout").getAttribute("aria-invalid"), "true");
    assert.equal(userWrites.length, before, "an out-of-range timeout is not sent");
    await page.locator("#render-timeout").fill("75");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(userWrites.at(-1).renderTimeoutS, 75);
    assert.equal(await dialog.getByRole("alert").count(), 0);
  });

  await step("a folder and a port are validated before the launch settings are saved", async () => {
    await openPage("Workspace");
    await page.locator("#workspace-dir").fill("relative\\projects");
    await dialog.getByRole("alert").filter({ hasText: "The folder for new projects must be a full path" }).waitFor();
    assert.equal(launchWrites.length, 0, "a relative folder is not sent");
    await page.locator("#workspace-dir").fill("D:\\fixture\\new-projects");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(launchWrites.at(-1).workspaceDir, "D:\\fixture\\new-projects");
    await dialog.getByText("More launch options", { exact: true }).click();
    await page.locator("#studio-port").fill("80");
    await dialog.getByRole("alert").filter({ hasText: "The project runtime port must be a whole number from 1024 to 65535." }).waitFor();
    await page.locator("#studio-port").fill("18788");
    await dialog.getByRole("alert").filter({ hasText: "Hub, Studio and Monitor must use different ports." }).waitFor();
    assert.equal(launchWrites.length, 1, "neither port is sent");
    await page.locator("#studio-port").fill("18790");
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(launch.studioPort, 18790);
    await page.locator("#cad-export").selectOption("off");
    await until(() => launch.cadExport === "off", "a launch choice saves at once");
  });

  await step("a change refused while the runtime runs stays in its field and saves once it stops", async () => {
    studioProcess = 4321;
    await dialog.getByText("Project Runtime · Running").waitFor();
    await page.locator("#studio-port").fill("18791");
    await dialog.getByRole("alert").filter({ hasText: "Stop the applications before changing their launch configuration." }).waitFor();
    await status.filter({ hasText: /^Unsaved changes$/ }).waitFor();
    assert.equal(await page.locator("#studio-port").inputValue(), "18791", "the refused value is kept");
    assert.equal(launch.studioPort, 18790);
    studioProcess = null;
    await status.filter({ hasText: /^Saved$/ }).waitFor();
    assert.equal(launch.studioPort, 18791);
  });

  await step("closing Settings saves what is still waiting for its pause", async () => {
    const before = userWrites.length;
    await openPage("AI Render");
    await page.locator("#render-model").fill("gemini-3.1-flash-image-preview");
    const typedAt = Date.now();
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await until(() => userWrites.length === before + 1, "closing Settings flushes the pending edit");
    assert.ok(userReads.at(-1) - typedAt < 450, `the save starts on close, not after the pause (${userReads.at(-1) - typedAt} ms)`);
    assert.equal(userSettings.renderModel, "gemini-3.1-flash-image-preview");
  });

  await step("the software update switch keeps its own save beside autosave", async () => {
    await openSettings();
    await openPage("Software update");
    const autoSwitch = page.getByRole("switch");
    await autoSwitch.click();
    await page.getByText("Automatic updates: Off · Unsigned prerelease channel", { exact: true }).waitFor();
    await openPage("Display");
    await page.locator("#font-scale").selectOption("1.1");
    await until(() => userWrites.at(-1)?.fontScale === 1.1, "text size saved");
    assert.equal(userWrites.at(-1).autoUpdate, false, "autosave carries the switch's value, never an older one");
  });

  await step("the status line reads in Chinese", async () => {
    await page.locator("#language").selectOption("zh-CN");
    await status.filter({ hasText: /^已保存$/ }).waitFor();
    assert.equal(userSettings.language, "zh-CN");
    await dialog.evaluate((node) => { node.scrollTop = node.scrollHeight; });
    await dialog.screenshot({ path: path.join(screenshots, "settings-saved-zh.png") });
    let release; userGate = new Promise((resolve) => { release = resolve; });
    await page.locator("#theme").selectOption("light");
    await status.filter({ hasText: /^正在保存…$/ }).waitFor();
    await dialog.screenshot({ path: path.join(screenshots, "settings-saving-zh.png") });
    userGate = null; release();
    await status.filter({ hasText: /^已保存$/ }).waitFor();
    await openPage("AI 渲染");
    await page.locator("#render-timeout").fill("0");
    await status.filter({ hasText: /^有未保存修改$/ }).waitFor();
    await dialog.screenshot({ path: path.join(screenshots, "settings-invalid-zh.png") });
    await page.locator("#render-timeout").fill("");
    await status.filter({ hasText: /^已保存$/ }).waitFor();
  });

  // A reload shows what was saved, with nothing left to save.
  await page.reload();
  await openSettings("Hub 设置");
  await status.filter({ hasText: /^已保存$/ }).waitFor();
  assert.equal(await page.locator("#render-model").inputValue(), "gemini-3.1-flash-image-preview");
  assert.equal(await page.locator("#studio-port").inputValue(), "18791");
  assert.deepEqual(errors, []);
  assert.deepEqual(unexpected, []);
  console.log(JSON.stringify({ passed, userWrites: userWrites.length, launchWrites: launchWrites.length, screenshots }));
} catch (error) {
  console.error(JSON.stringify({ screenshots, errors, unexpected, passed }));
  await page.screenshot({ path: path.join(screenshots, "failure.png") }).catch(() => {});
  throw error;
} finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
