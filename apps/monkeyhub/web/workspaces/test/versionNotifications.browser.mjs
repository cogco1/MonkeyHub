import { workspaceFixture } from "./workspaceFixture.mjs";
import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

// The actual App reads retained A/B model bytes from an explicitly named,
// disposable API. Only the version lists and EventSource are simulated here.
// Every API mutation is blocked in both the browser and the local proxy.
// Required: STUDIO_AB_URL and STUDIO_AB_PROJECT_ROOT.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
assert.ok(process.env.STUDIO_AB_URL, "Name the isolated API with STUDIO_AB_URL");
assert.ok(process.env.STUDIO_AB_PROJECT_ROOT, "Name the isolated project with STUDIO_AB_PROJECT_ROOT");
const apiOrigin = new URL(process.env.STUDIO_AB_URL);
const readMethods = new Set(["GET", "HEAD", "OPTIONS"]);
const forbiddenPorts = new Set(["5187", "5188", "60617", "60616"]);
assert.ok(["http:", "https:"].includes(apiOrigin.protocol));
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(apiOrigin.hostname));
assert.ok(apiOrigin.port && !forbiddenPorts.has(apiOrigin.port));
assert.equal(apiOrigin.username + apiOrigin.password + apiOrigin.search + apiOrigin.hash, "");
assert.equal(apiOrigin.pathname, "/");
assert.ok(path.isAbsolute(process.env.STUDIO_AB_PROJECT_ROOT));
const projectRoot = path.resolve(process.env.STUDIO_AB_PROJECT_ROOT);
async function getJson(endpoint, query = {}) {
  const url = new URL(endpoint, apiOrigin);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
  const response = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(20_000) });
  assert.equal(response.ok, true, `GET ${url.pathname}: HTTP ${response.status}`);
  return response.json();
}
const project = await getJson("/api/project");
assert.ok(path.isAbsolute(project.projectDir));
assert.equal(path.resolve(project.projectDir), projectRoot);
assert.equal(await realpath(project.projectDir), await realpath(projectRoot));
const [protocol, copyList, artifactList] = await Promise.all([
  getJson("/api/protocol"), getJson("/api/working-copies"), getJson("/api/artifacts"),
]);
for (const capability of ["working-copies", "model-annotations", "events"])
  assert.ok(protocol.capabilities.includes(capability), `The isolated API needs ${capability}`);
const originalGroup = copyList.workingCopies.find((row) => row.projectId === project.projectId && row.groupId === "m-upper-cabinets");
assert.ok(originalGroup, "The retained A/B group must exist in the isolated project");
const optionA = originalGroup.options.find((row) => row.id === "A");
const optionB = originalGroup.options.find((row) => row.id === "B");
assert.ok(optionA && optionB);
const sourceA = optionA.modelSource;
const sourceB = optionB.modelSource;
assert.notEqual(sourceA.runId, sourceB.runId);
assert.notEqual(sourceA.assetSha256, sourceB.assetSha256);
const sourceKey = (source) => source && JSON.stringify([source.runId, source.stateDigest, source.assetSha256]);
const artifactA = artifactList.artifacts.find((row) => sourceKey(row.modelSource) === sourceKey(sourceA));
const artifactB = artifactList.artifacts.find((row) => sourceKey(row.modelSource) === sourceKey(sourceB));
for (const artifact of [artifactA, artifactB])
  assert.ok(artifact?.available && artifact.format === "3dm" && artifact.representation === "composed");
const otherB = artifactList.artifacts.find((row) => row.available && row.format === "3dm" &&
  row.modelSource?.runId === sourceB.runId && row.modelSource.stateDigest === sourceB.stateDigest &&
  row.modelSource.assetSha256 !== sourceB.assetSha256);
assert.ok(otherB, "The retained B run must already expose another exact asset, so a run-only diff cannot pass");
const nativeA = artifactList.artifacts.find((row) => row.available && row.format === "3dm" &&
  row.representation !== "composed" && row.modelSource?.runId === sourceA.runId &&
  row.modelSource.stateDigest === sourceA.stateDigest && row.modelSource.assetSha256 !== sourceA.assetSha256);
assert.ok(nativeA?.modelSource, "A needs its real original export for the same-run complete-model regression");

let group = { ...originalGroup, selectedOptionId: optionA.id,
  options: originalGroup.options.filter((option) => sourceKey(option.modelSource) !== sourceKey(sourceB)) };
let artifacts = { ...artifactList,
  artifacts: artifactList.artifacts.filter((artifact) => sourceKey(artifact.modelSource) !== sourceKey(sourceB)) };
const copies = () => ({ ...copyList,
  workingCopies: copyList.workingCopies.map((copy) => copy.groupId === group.groupId ? group : copy) });
const errors = [];
const requests = [];
const mutations = [];
const blockedProxyWrites = [];
const failedRefreshes = [];
const nextFailures = new Set();
const passed = [];
const screenshots = [];
const preferenceKey = "archflow-studio.user-preferences";
const editingKey = JSON.stringify(["", project.projectId]);
let phase = "setup";
let vite;
let browser;
let page;
const http = createHttpServer();
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-version-notifications-test-"));
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function until(read, accepts, message, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  let value;
  do {
    assert.deepEqual(errors, [], `Unexpected browser/API error during ${phase}`);
    value = await read();
    if (accepts(value)) return value;
    await sleep(70);
  } while (Date.now() < deadline);
  assert.fail(`${message}: ${JSON.stringify(value)}`);
}
async function step(name, action) {
  phase = name;
  await action();
  passed.push(name);
  console.log(`PASS ${name}`);
}
async function painted() {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function assertAnnotationNotePlacement() {
  const placement = await page.locator(".annotate__note").evaluateAll((notes) => {
    const controls = [...document.querySelectorAll(".stage-mode-switch, #stage-versions-panel .stage__versions-session, .stage-model .viewtools, .stage__context")];
    const visible = notes.filter((note) => {
      const style = getComputedStyle(note);
      const rect = note.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0;
    });
    return { visibleNotes: visible.length, overlaps: visible.flatMap((note) => {
      const rect = note.getBoundingClientRect();
      return controls.filter((control) => {
        const other = control.getBoundingClientRect();
        return Math.min(rect.right, other.right) - Math.max(rect.left, other.left) > 1 &&
          Math.min(rect.bottom, other.bottom) - Math.max(rect.top, other.top) > 1;
      }).map((control) => ({ note: note.textContent, control: control.className }));
    }), clipped: visible.some((note) => {
      const rect = note.getBoundingClientRect();
      return rect.left < 0 || rect.top < 0 || rect.right > innerWidth + 1 || rect.bottom > innerHeight + 1;
    }) };
  });
  assert.ok(placement.visibleNotes > 0 && placement.overlaps.length === 0 && !placement.clipped,
    `The retained-mark view-change note must stay visible without overlapping model controls: ${JSON.stringify(placement)}`);
}
const versionsToggle = () => page.locator(".stage__versions-toggle");
const versionSession = () => page.locator("#stage-versions-panel .stage__versions-session");
const newVersionBadge = () => page.locator(".stage__versions-new");
const toolsToggle = () => page.locator("button[aria-controls='annotation-tools']");
const viewToolsToggle = () => page.locator("button[aria-controls='view-tools']");
const workspaceState = () => page.evaluate(() => window.__versionWorkspace);
async function sessionState() {
  assert.equal(await versionsToggle().getAttribute("aria-expanded"), "true");
  return {
    sourceMatch: await page.locator(".stage__foot .editing-base[data-source-match]").getAttribute("data-source-match"),
    modelAnnotations: await versionSession().locator("[data-model-annotations-status]").getAttribute("data-model-annotations-status"),
  };
}
async function setVersionsOpen(open) {
  const changed = (await versionsToggle().getAttribute("aria-expanded") === "true") !== open;
  const before = changed && open ? listCounts() : null;
  if (changed) await versionsToggle().click();
  await until(() => versionsToggle().getAttribute("aria-expanded"), (value) => value === String(open), "The versions panel did not toggle");
  if (before) await until(listCounts, (counts) => [...listPaths].every((pathname) => counts[pathname] > before[pathname]),
    "Opening versions must finish refreshing both lists");
  await painted();
}
function versionButton(option) {
  const label = option.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return page.locator(`[data-working-copy=${JSON.stringify(group.groupId)}]`)
    .getByRole("button", { name: new RegExp(`^View ${label}`) });
}
async function readyA() {
  await until(async () => ({
      selected: await versionButton(optionA).getAttribute("aria-pressed"),
      annotations: await versionSession().locator("[data-model-annotations-status]").getAttribute("data-model-annotations-status"),
      status: (await workspaceState())?.status,
    }), (value) => value.selected === "true" && value.annotations === "saved" && value.status === "ready",
    "The real A model and annotations must be ready", 60_000);
  return sessionState();
}
async function preservedState() {
  // Read the existing view state without opening or acknowledging Versions.
  assert.equal(await versionsToggle().getAttribute("aria-expanded"), "false");
  assert.equal(await versionSession().count(), 0);
  const unread = await newVersionBadge().count();
  const source = await workspaceState();
  assert.equal(await versionsToggle().getAttribute("aria-expanded"), "false");
  assert.equal(await versionSession().count(), 0);
  assert.equal(await newVersionBadge().count(), unread, "Reading model details must not acknowledge a version notice");
  const annotationRequest = requests.findLast((request) => request.path === "/api/model-annotations" && request.completed);
  assert.ok(annotationRequest, "The viewed model must have loaded its exact annotation scope");
  const query = new URLSearchParams(annotationRequest.query);
  return {
    ...source,
    modelSource: Object.fromEntries(["runId", "stateDigest", "assetSha256"].map((key) => [key, query.get(key)])),
    preferences: await page.evaluate((key) => localStorage.getItem(key), preferenceKey),
    modelCanvasCount: await page.locator(".stage-model canvas").count(),
    url: page.url(),
  };
}
async function assertOneStream() {
  const state = await page.evaluate(() => {
    const active = window.__versionEvents.connections.filter((source) => !source.closed);
    return { active: active.length, created: window.__versionEvents.connections.length,
      urls: active.map((source) => new URL(source.url, location.href).pathname) };
  });
  assert.equal(state.active, 1, "The App owns exactly one live SSE connection");
  assert.deepEqual(state.urls, ["/api/events"]);
  return state.created;
}
const listPaths = new Set(["/api/artifacts", "/api/working-copies"]);
const listCounts = () => Object.fromEntries([...listPaths].map((pathname) => [pathname,
  requests.filter((request) => request.path === pathname && request.completed).length]));
function event(seq, type = "model_asset.registered") {
  return { seq, at: "2026-09-08T00:00:00Z", type, runId: sourceB.runId };
}
async function emit(value, { reconnect = false } = {}) {
  await page.evaluate(({ value, reconnect }) => {
    const active = window.__versionEvents.connections.filter((source) => !source.closed);
    if (active.length !== 1) throw new Error(`Expected one live stream, got ${active.length}`);
    if (reconnect) { active[0].fail(); active[0].open(); }
    active[0].emit(value.type, value);
  }, { value, reconnect });
}
async function refreshFrom(value, options) {
  const before = listCounts();
  const start = requests.length;
  await emit(value, options);
  await until(listCounts, (counts) => [...listPaths].every((pathname) => counts[pathname] > before[pathname]),
    `${value.type} must refresh both version lists`);
  await painted();
  assert.deepEqual(requests.slice(start).map(({ method, path }) => ({ method, path })).sort((a, b) => a.path.localeCompare(b.path)),
    [...listPaths].sort().map((path) => ({ method: "GET", path })),
    "An external version event only refreshes artifacts and working copies");
}

try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir,
    publicDir: "../.generated/public", plugins: [workspaceFixture(), {
      name: "observe-workspace-source", enforce: "pre",
      transform(source, id) {
        if (id.split("?")[0].replaceAll("\\", "/") !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
        const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, marker + `
          (window as unknown as { __versionWorkspace: unknown }).__versionWorkspace = {
            loadedModelSource, editingModelSource, stateDigest, projectId: project?.projectId,
            status: viewerStatus, loadingSha: artifactLoadingSha,
          };`), map: null };
      },
    }, react()],
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null,
      proxy: { "/api": { target: apiOrigin.origin, changeOrigin: false, followRedirects: false } } },
  });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/") && !readMethods.has(request.method)) {
      blockedProxyWrites.push(`${request.method} ${request.url}`);
      response.writeHead(405);
      response.end("Version notification tests never forward mutations");
      return;
    }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const port = String(http.address().port);
  assert.ok(!forbiddenPorts.has(port));
  const uiOrigin = `http://127.0.0.1:${port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1200 }, locale: "en-US" });
  await context.addInitScript(({ preferenceKey, editingKey, runId }) => {
    localStorage.setItem(preferenceKey, JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: false, editingBases: { [editingKey]: runId } }));
    const state = window.__versionEvents = { connections: [] };
    class FakeEventSource {
      constructor(url) {
        this.url = url;
        this.listeners = new Map();
        this.closed = false;
        state.connections.push(this);
        queueMicrotask(() => this.open());
      }
      addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type).add(listener);
      }
      removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
      close() { this.closed = true; }
      open() { if (!this.closed) this.onopen?.(new Event("open")); }
      fail() { if (!this.closed) this.onerror?.(new Event("error")); }
      emit(type, value) {
        if (this.closed) return;
        const message = new MessageEvent(type, { data: JSON.stringify(value) });
        for (const listener of this.listeners.get(type) ?? []) listener(message);
        if (type === "message") this.onmessage?.(message);
      }
    }
    window.EventSource = FakeEventSource;
  }, { preferenceKey, editingKey, runId: sourceA.runId });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (!new URL(response.url()).pathname.startsWith("/api/") || response.ok()) return;
    if (response.headers()["x-version-fixture-failure"] !== "expected")
      errors.push(`${response.request().method()} ${new URL(response.url()).pathname}: HTTP ${response.status()}`);
  });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const log = { method: request.method(), path: url.pathname, query: url.search, completed: false };
    requests.push(log);
    try {
      assert.equal(url.origin, uiOrigin);
      if (!readMethods.has(request.method())) {
        mutations.push({ ...log, body: request.postData() });
        assert.fail(`Unexpected mutation ${request.method()} ${url.pathname}`);
      }
      assert.notEqual(url.pathname, "/api/events", "The fixture must never open an actual SSE request");
      if (nextFailures.delete(url.pathname)) {
        failedRefreshes.push(url.pathname);
        await route.fulfill({ status: 503, headers: { "x-version-fixture-failure": "expected" },
          json: { code: "FIXTURE_REFRESH_FAILURE", detail: "This list refresh is deliberately unavailable." } });
      } else if (url.pathname === "/api/artifacts") {
        await route.fulfill({ json: artifacts });
      } else if (url.pathname === "/api/working-copies") {
        await route.fulfill({ json: copies() });
      } else {
        await route.continue();
      }
      log.completed = true;
    } catch (error) {
      errors.push(`Blocked unexpected API request: ${String(error)}`);
      await route.abort("blockedbyclient");
    }
  });

  await page.goto(`${uiOrigin}/?lang=en`, { waitUntil: "domcontentloaded" });
  let baseline;
  let baselineSession;
  let connectionCount;
  let refreshStart;
  await step("normal mode keeps one SSE connection with the developer drawer and event stream hidden", async () => {
    await versionsToggle().waitFor();
    await setVersionsOpen(true);
    baselineSession = await readyA();
    assert.equal(await page.locator(".drawer").count(), 0);
    assert.equal(await page.locator(".events").count(), 0);
    assert.equal(await newVersionBadge().count(), 0, "Initial retained lists are not new versions");
    assert.equal(await versionButton(optionB).count(), 0);
    assert.ok(requests.some((request) => request.path === `/api/artifacts/${sourceA.assetSha256}/bytes`),
      "A must load its actual retained model bytes");
    await setVersionsOpen(false);
    await painted();
    baseline = await preservedState();
    assert.deepEqual(baseline.modelSource, sourceA);
    const preferences = JSON.parse(baseline.preferences);
    assert.equal(preferences.developerMode, false);
    assert.equal(preferences.eventStreamVisible, false);
    assert.equal(preferences.editingBases[editingKey], sourceA.runId);
    connectionCount = await assertOneStream();
    refreshStart = requests.length;
  });

  await step("registering a complete retained model only shows a new-version notice", async () => {
    artifacts = artifactList;
    await refreshFrom(event(1));
    await newVersionBadge().waitFor();
    assert.equal(await newVersionBadge().count(), 1);
    assert.equal(await versionsToggle().getAttribute("aria-expanded"), "false");
    assert.deepEqual(await preservedState(), baseline);
    assert.equal(await assertOneStream(), connectionCount);
  });

  await step("working-copy and candidate events refresh both lists without changing the current work", async () => {
    group = originalGroup;
    await refreshFrom(event(2, "working_copy.option_added"));
    assert.equal(await newVersionBadge().count(), 1);
    assert.deepEqual(await preservedState(), baseline);
    await refreshFrom(event(3, "candidate.succeeded"));
    assert.equal(await newVersionBadge().count(), 1);
    assert.deepEqual(await preservedState(), baseline);
    await setVersionsOpen(true);
    await versionButton(optionB).waitFor();
    assert.deepEqual(await readyA(), baselineSession);
    assert.equal(await versionButton(optionB).getAttribute("aria-pressed"), "false");
    assert.equal(await newVersionBadge().count(), 0, "Opening versions acknowledges the notice without selecting B");
    await setVersionsOpen(false);
    assert.deepEqual(await preservedState(), baseline);
  });

  await step("same-sequence replay, reconnect replay and unchanged lists do not repeat an acknowledged notice", async () => {
    const start = requests.length;
    await emit(event(3, "candidate.succeeded"));
    await painted();
    await sleep(150);
    assert.equal(requests.length, start, "A duplicate sequence must not trigger a second refresh");
    await refreshFrom(event(3, "candidate.succeeded"), { reconnect: true });
    assert.equal(await newVersionBadge().count(), 0);
    await refreshFrom(event(4, "model_asset.registered"));
    assert.equal(await newVersionBadge().count(), 0);
    assert.deepEqual(await preservedState(), baseline);
    assert.equal(await assertOneStream(), connectionCount);
  });

  await step("either list refresh may fail while the loaded model and existing version choices stay ready", async () => {
    for (const [index, pathname] of [...listPaths].entries()) {
      nextFailures.add(pathname);
      await refreshFrom(event(5 + index));
      assert.equal(await newVersionBadge().count(), 0);
      assert.deepEqual(await preservedState(), baseline);
      await setVersionsOpen(true);
      assert.deepEqual(await readyA(), baselineSession);
      assert.equal(await versionButton(optionB).count(), 1, "A failed refresh retains the existing B choice");
      await setVersionsOpen(false);
    }
    assert.deepEqual(failedRefreshes, [...listPaths]);
    await setVersionsOpen(true);
    assert.deepEqual(await readyA(), baselineSession);
    await setVersionsOpen(false);
    assert.equal(await newVersionBadge().count(), 0);
    assert.deepEqual(await preservedState(), baseline);
    assert.equal(await assertOneStream(), connectionCount);
    assert.deepEqual(mutations, []);
    assert.deepEqual(blockedProxyWrites, []);
    assert.deepEqual(requests.slice(refreshStart).filter((request) => !listPaths.has(request.path)), [],
      "Notifications must not reload project/state, fetch projection/model bytes, or Continue a version");
    assert.deepEqual(errors, []);
  });

  await step("default controls fit 1121 and 1440 pixel windows while the loaded model and editing choice survive resizing", async () => {
    const screenshotDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-version-notifications-review-"));
    assert.equal(path.dirname(path.resolve(screenshotDir)), path.resolve(tmpdir()));
    for (const viewport of [{ width: 1121, height: 874 }, { width: 1440, height: 900 }]) {
      await page.setViewportSize(viewport);
      await painted();
      assert.equal(await versionsToggle().getAttribute("aria-expanded"), "false");
      assert.equal(await toolsToggle().getAttribute("aria-expanded"), "false");
      assert.equal(await viewToolsToggle().getAttribute("aria-expanded"), "false");
      assert.equal(await page.locator("#conversation-panel").count(), 0);
      const width = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth }));
      assert.ok(width.document <= width.viewport + 1 && width.body <= width.viewport + 1,
        `Default controls must not overflow horizontally at ${viewport.width}: ${JSON.stringify(width)}`);
      await assertAnnotationNotePlacement();
      const screenshot = path.join(screenshotDir, `default-${viewport.width}x${viewport.height}.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      screenshots.push(screenshot);
      await painted();
      assert.deepEqual(await preservedState(), baseline);
      assert.equal(await assertOneStream(), connectionCount);
    }
    assert.deepEqual(requests.slice(refreshStart).filter((request) => !listPaths.has(request.path)), [],
      "Resizing and opening local panels must not reload or select a project version");
    assert.deepEqual(mutations, []);
    assert.deepEqual(errors, []);
  });

  await step("a same-run complete model does not replace the original export being viewed and edited", async () => {
    // The second App mount starts on A's actual original preview export. There
    // is no A working-copy option to lend it a source. Its real composed file
    // arrives later through the list, so no invented model bytes are needed.
    group = { ...originalGroup, options: originalGroup.options.filter((option) => option.modelSource.runId !== sourceA.runId) };
    artifacts = { ...artifactList, artifacts: artifactList.artifacts.filter((artifact) =>
      artifact.runId !== sourceA.runId || artifact.representation !== "composed") };
    const mountStart = requests.length;
    await page.reload({ waitUntil: "domcontentloaded" });
    await versionsToggle().waitFor();
    await until(async () => ({
        status: (await workspaceState())?.status,
        originalBytes: requests.slice(mountStart).some((request) => request.path === `/api/artifacts/${nativeA.sha256}/bytes`),
        originalAnnotations: requests.slice(mountStart).some((request) => request.path === "/api/model-annotations" && request.completed &&
          Object.entries(nativeA.modelSource).every(([key, value]) => new URLSearchParams(request.query).get(key) === value)),
      }), (value) => value.status === "ready" && value.originalBytes && value.originalAnnotations,
      "A's original export must load its real model bytes and exact annotation scope", 60_000);
    assert.equal(await newVersionBadge().count(), 0);
    const originalState = await preservedState();
    assert.deepEqual(originalState.modelSource, nativeA.modelSource);
    assert.equal(JSON.parse(originalState.preferences).editingBases[editingKey], sourceA.runId);
    const originalConnections = await assertOneStream();
    const start = requests.length;
    artifacts = artifactList;
    await refreshFrom({ ...event(1), runId: sourceA.runId });
    await newVersionBadge().waitFor();
    assert.deepEqual(await preservedState(), originalState);
    assert.equal(await assertOneStream(), originalConnections);
    await setVersionsOpen(true);
    await until(sessionState, (value) => value.modelAnnotations === "saved" && value.sourceMatch === "same",
      "A's original export must remain both the viewed and editing source, with its own saved annotation scope");
    assert.equal(await newVersionBadge().count(), 0);
    await setVersionsOpen(false);
    assert.deepEqual(await preservedState(), originalState);
    assert.deepEqual(requests.slice(start).filter((request) => !listPaths.has(request.path)), [],
      "The new same-run complete file must not reload model bytes, annotation scope, projection, or editing base");
    assert.deepEqual(mutations, []);
    assert.deepEqual(blockedProxyWrites, []);
    assert.deepEqual(errors, []);
  });

  console.log(JSON.stringify({ passed: passed.length, projectId: project.projectId, groupId: group.groupId,
    sources: { A: sourceA, B: sourceB, originalA: nativeA.modelSource }, listRefreshes: listCounts(), failedRefreshes,
    activeSseConnections: 1, forwardedMutations: blockedProxyWrites.length, mutationRequests: mutations.length,
    jsErrors: errors.length, screenshots }, null, 2));
} catch (error) {
  console.error(`FAIL ${phase}: ${error.stack ?? error}`);
  if (errors.length) console.error(JSON.stringify(errors, null, 2));
  if (screenshots.length) console.error(JSON.stringify({ screenshots }, null, 2));
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (http.listening) await new Promise((resolve, reject) => {
    http.close((error) => error ? reject(error) : resolve());
  });
  await vite?.close();
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("monkeyarch-version-notifications-test-"));
  await rm(cacheDir, { recursive: true, force: true });
}
