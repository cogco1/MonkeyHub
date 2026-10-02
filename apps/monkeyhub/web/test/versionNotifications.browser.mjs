import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createServer } from "vite";
import { createRetainedModelFixture } from "./retainedModelFixture.mjs";
import { workspaceFixture } from "./workspaceFixture.mjs";

/**
 * GH-355: the retained-model notification regressions, on the real ProjectWorkspace,
 * its real Hub event relay and an owned disposable Project Runtime. Run with
 * `node test/versionNotifications.browser.mjs` from apps/monkeyhub/web after sync.
 * PYTHON and PLAYWRIGHT_MODULE may select installed tools.
 * No existing project, provider or fixed application port is used.
 *
 * Old seven contracts -> their current consumers:
 * 1. One stream in normal mode -> the Hub relay shared by Modeling and the tree.
 * 2. New results notify without hijacking -> the tree's candidate-ready notice (#302).
 *    A raw registered export refreshes discovery; it is not an admitted candidate.
 * 3. Registration/option/candidate events -> retained choices and working draft refresh.
 * 4. Acknowledgement/replay -> inspecting the exact candidate marks it seen; replay
 *    never reopens it, changes Current or repeats the ready notice.
 * 5. Failed list reads -> the current model and already discovered choices survive.
 * 6. Both viewport sizes -> usable controls and the same camera and exact sources.
 * 7. New asset in the viewed run -> the explicitly viewed exact export stays put.
 *
 * Only list visibility, failure responses and the event transport are controlled.
 * The project, Stage, historical Exploration, draft, states, 3DM bytes, annotations,
 * previews and new same-run registration all use the actual runtime/P036 boundary.
 * The test host supplies the same relayHubStream used by ChatShell. It does not
 * replace App, ProjectWorkspace, the tree, viewport or their source-selection code.
 */
const fixture = await createRetainedModelFixture({ name: "version-notifications" });
const { webRoot, project, group, sources, apiOrigin } = fixture;
const { A, B, nativeA, alternateB } = sources;
const sourceKey = (source) => source && JSON.stringify([source.runId, source.stateDigest, source.assetSha256]);
const runtimeId = "retained-notifications-runtime";
const preferenceKey = "archflow-studio.user-preferences";
const readMethods = new Set(["GET", "HEAD", "OPTIONS"]);
const listPaths = ["/api/artifacts", "/api/working-copies", "/api/working-draft"];
const errors = [], requests = [], writes = [], blockedWrites = [], failedRefreshes = [], passed = [], screenshots = [];
const nextFailures = new Set();
const requestLogs = new WeakMap();
let revealB = false;
let phase = "setup", vite, browser, page;
const http = createHttpServer();
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyhub-version-notifications-vite-"));
const screenshotDir = await mkdtemp(path.join(tmpdir(), "monkeyhub-version-notifications-review-"));
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const state = () => page.evaluate(() => window.__versionWorkspace?.());
const versions = () => page.locator("button[aria-controls='stage-versions-panel']");
const more = () => page.locator("button[aria-controls='stage-more-menu']");
const readyNotice = () => page.locator(".stage-chip__ready").filter({ hasText: "1 option ready" });
const choice = () => page.locator(`[role="treeitem"][data-node=${JSON.stringify(`candidate:${B.runId}`)}]`);
const listCounts = () => Object.fromEntries(listPaths.map((name) => [name,
  requests.filter((request) => request.path === name && request.completed).length]));
async function until(read, accept, message, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  let value;
  do {
    assert.deepEqual(errors, [], `Unexpected browser/API error during ${phase}`);
    value = await read();
    if (accept(value)) return value;
    await sleep(70);
  } while (Date.now() < deadline);
  assert.fail(`${message}: ${JSON.stringify(value)}`);
}
async function painted() {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function settledReads(start = 0) {
  // route.continue only admits a request. requestfinished below means its body
  // reached the browser; subsequent frames let its React consumers commit and
  // start any dependent reads before no-reload/no-write observations are made.
  for (const deadline = Date.now() + 30_000; ; ) {
    await until(() => requests.slice(start).filter((request) => !request.completed && !request.failed).length,
      (pending) => pending === 0, "Discovery reads must finish before observing their result");
    const count = requests.length;
    await painted();
    if (requests.length === count && requests.slice(start).every((request) => request.completed || request.failed)) return;
    assert.ok(Date.now() < deadline, "Discovery reads must settle");
  }
}
async function step(name, action) {
  phase = name;
  await action();
  passed.push(name);
  console.log(`PASS ${name}`);
}
async function settledSource(source) {
  await until(state, (value) => value?.status === "ready" && value.loadingSha === null && value.annotations.ready &&
    sourceKey(value.loadedModelSource) === sourceKey(source), "The exact retained model and its annotations must be ready", 60_000);
  // Automatic preview capture is an expected, exact-source runtime write, not a
  // legacy editing-base migration. Finish it before testing no-reload events.
  await until(() => fixture.getJson(`/api/model-assets/${source.assetSha256}/preview`, {
    runId: source.runId, stateDigest: source.stateDigest,
  }), (value) => sourceKey(value?.modelSource) === sourceKey(source) && value?.viewRecipe?.kind === "viewport-preview",
  "The real runtime must retain this exact model's viewport preview", 60_000);
  await settledReads();
}
async function preservedState() {
  const value = await state();
  assert.ok(value?.camera, "A real rendered viewport supplies its camera");
  return {
    loadedModelSource: value.loadedModelSource, editingModelSource: value.editingModelSource,
    stateDigest: value.stateDigest, projectId: value.projectId, status: value.status,
    loadingSha: value.loadingSha, camera: value.camera, annotations: value.annotations,
    workingDraft: await fixture.getJson("/api/working-draft"),
  };
}
async function oneStream() {
  const streams = await page.evaluate(() => ({ created: window.__versionEvents.connections.length,
    active: window.__versionEvents.connections.filter((source) => !source.closed).map((source) => new URL(source.url, location.href).pathname) }));
  assert.deepEqual(streams.active, ["/api/runtime/events"], "Modeling and the tree share one Hub stream");
  return streams.created;
}
function event(seq, type = "model_asset.registered", runId = B.runId) {
  return { seq, at: "2026-09-08T00:00:00Z", type, runId };
}
async function emit(value, { reconnect = false, replay = false } = {}) {
  await page.evaluate(({ value, runtimeId, reconnect, replay }) => {
    const active = window.__versionEvents.connections.filter((source) => !source.closed);
    if (active.length !== 1) throw new Error(`Expected one live stream, got ${active.length}`);
    if (reconnect) { active[0].fail(); active[0].open(); active[0].emit("runtime", {}); }
    active[0].emit("studio", { runtimeId, stream: "retained-runtime-1", replay, studio: value });
  }, { value, runtimeId, reconnect, replay });
}
async function refreshFrom(value) {
  const before = listCounts(), start = requests.length, writeCount = writes.length;
  await emit(value);
  await until(listCounts, (counts) => listPaths.every((name) => counts[name] > before[name]),
    `${value.type} must refresh artifacts, retained options and runtime working draft`);
  await settledReads(start);
  if (revealB) await until(state, (value) => value?.artifactSources.some((source) => sourceKey(source) === sourceKey(B)) &&
    value?.optionSources.some((source) => sourceKey(source) === sourceKey(B)), "Both discovery lists must commit the revealed exact choice");
  assert.equal(writes.length, writeCount, "A version event must not write a working position or capture another model");
  assert.deepEqual(requests.slice(start).filter(({ path: name }) => name === "/api/state" || name === "/api/project" ||
    /^\/api\/artifacts\/[^/]+\/bytes$/.test(name) || name === "/api/model-annotations"), [],
  "Discovery must not reload the viewed model, projection, annotation scope or project session");
}
async function showVersions(open) {
  if ((await versions().getAttribute("aria-expanded") === "true") !== open) await versions().click();
  await until(() => versions().getAttribute("aria-expanded"), (value) => value === String(open), "Versions must toggle");
  await painted();
}
async function backToModel() {
  await page.locator(".stage-chip[aria-pressed='true']").click();
  await versions().waitFor();
  await painted();
}
async function assertChoices() {
  await page.locator(".stage-chip").click();
  await page.locator(".design-tree").waitFor();
  await page.getByRole("button", { name: "List", exact: true }).click();
  await choice().waitFor();
  assert.equal(await choice().getAttribute("data-kind"), "candidate");
  await backToModel();
}

try {
  assert.notEqual(A.runId, B.runId);
  assert.notEqual(B.assetSha256, alternateB.assetSha256, "The same B run already has a different exact asset");
  assert.equal((await fixture.getJson("/api/working-draft")).current.runId, A.runId);
  assert.ok(group.baseStageRef, "The ready-candidate notice needs a retained common Stage");
  vite = await createServer({ root: webRoot, configFile: path.join(webRoot, "vite.config.ts"), resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public", plugins: [workspaceFixture(), {
      name: "retained-notifications-host", enforce: "pre",
      transform(source, id) {
        const file = id.split("?")[0].replaceAll("\\", "/");
        if (file === `${webRoot.replaceAll("\\", "/")}/test/workspace-fixture.tsx`) {
          const marker = "<UserPreferencesProvider>";
          assert.equal(source.split(marker).length, 2);
          const sharedCss = path.resolve(webRoot, "../../../packages/web-shared/src/base.css").replaceAll("\\", "/");
          return { code: `import "/@fs/${sharedCss}";\nimport { relayHubStream, projectStores } from "/src/api/project-runtime/projectStore.ts";
const stream = new EventSource("/api/runtime/events");
const stopRelay = relayHubStream(stream);
window.addEventListener("pagehide", () => { stopRelay(); stream.close(); projectStores.detached(); }, { once: true });
` + source.replace(marker, `<UserPreferencesProvider runtimeId=${JSON.stringify(runtimeId)}>`), map: null };
        }
        if (file !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
        const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, marker + `
          (window as unknown as { __versionWorkspace: unknown }).__versionWorkspace = () => ({
            loadedModelSource, editingModelSource, stateDigest, projectId: project?.projectId,
            status: viewerStatus, loadingSha: artifactLoadingSha, camera: viewportRef.current?.camera(),
            annotations: { ready: modelAnnotations.ready, dirty: modelAnnotations.dirty, annotations: modelAnnotations.annotations,
              comment: modelAnnotations.comment }, choiceSources: modelSources.map(row => row.modelSource),
            artifactSources: artifacts.status === "ready" ? artifacts.value.artifacts.map(row => row.modelSource).filter(Boolean) : [],
            optionSources: workingCopies.flatMap(copy => copy.options.map(option => option.modelSource)),
          });`), map: null };
      },
    }], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null,
      proxy: { "/api": { target: apiOrigin, changeOrigin: false, followRedirects: false } } } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/") && !readMethods.has(request.method)) {
      // Only preview capture and timing may reach the disposable runtime. The
      // browser guard below also validates their exact project/model binding.
      const pathname = new URL(request.url, "http://fixture.test").pathname;
      if (request.method !== "POST" || !["/api/captures", "/api/events/model-load", "/api/events/timing"].includes(pathname)) {
        blockedWrites.push(`${request.method} ${pathname}`); response.writeHead(405); response.end(); return;
      }
    }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1200 }, locale: "en-US" });
  await context.addInitScript(({ preferenceKey }) => {
    localStorage.setItem(preferenceKey, JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: false }));
    const state = window.__versionEvents = { connections: [] };
    class ControlledEventSource {
      constructor(url) {
        this.url = url; this.listeners = new Map(); this.closed = false; state.connections.push(this);
        queueMicrotask(() => { this.open(); this.emit("runtime", {}); });
      }
      addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, new Set()); this.listeners.get(type).add(listener); }
      removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
      close() { this.closed = true; }
      dispatch(type, message) { if (!this.closed) for (const listener of this.listeners.get(type) ?? []) listener(message); }
      open() { const message = new Event("open"); this.dispatch("open", message); this.onopen?.(message); }
      fail() { const message = new Event("error"); this.dispatch("error", message); this.onerror?.(message); }
      emit(type, value) { this.dispatch(type, new MessageEvent(type, { data: JSON.stringify(value) })); }
    }
    window.EventSource = ControlledEventSource;
  }, { preferenceKey });
  await context.route("**/*", async (route) => {
    const target = new URL(route.request().url());
    if (target.origin !== origin) {
      errors.push(`Blocked external request: ${target.origin}${target.pathname}`);
      await route.abort("blockedbyclient");
    } else await route.continue();
  });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("requestfinished", (request) => { const log = requestLogs.get(request); if (log) log.completed = true; });
  page.on("requestfailed", (request) => { const log = requestLogs.get(request); if (log) log.failed = true; });
  page.on("response", (response) => {
    const name = new URL(response.url()).pathname;
    if (name.startsWith("/api/") && !response.ok() && response.status() !== 304 &&
      response.headers()["x-version-fixture-failure"] !== "expected") errors.push(`${name}: HTTP ${response.status()}`);
  });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url());
    const log = { method: request.method(), path: url.pathname, query: url.search, completed: false };
    requests.push(log); requestLogs.set(request, log);
    try {
      assert.equal(url.origin, origin);
      assert.ok(!["/api/events", "/api/runtime/events"].includes(url.pathname), "Only the controlled transport opens SSE");
      if (!readMethods.has(request.method())) {
        const body = request.postDataJSON();
        assert.equal(request.method(), "POST");
        if (url.pathname === "/api/captures") {
          assert.deepEqual(Object.keys(body).sort(), ["modelSource", "pngBase64", "runId"]);
          assert.ok([A, nativeA].some((source) => sourceKey(source) === sourceKey(body.modelSource)));
          assert.deepEqual((await state()).loadedModelSource, body.modelSource, "A preview may only capture the exact model still on screen");
          assert.equal(body.runId, body.modelSource.runId);
          assert.equal(typeof body.pngBase64, "string");
          assert.deepEqual(Buffer.from(body.pngBase64, "base64").subarray(0, 8), Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
        } else {
          assert.ok(["/api/events/model-load", "/api/events/timing"].includes(url.pathname), `Unexpected mutation ${request.method()} ${url.pathname}`);
          assert.equal(body.projectId, project.projectId);
          assert.equal(body.runId, A.runId);
          const artifact = fixture.artifacts.artifacts.find((row) => row.runId === body.runId && row.receiptRef === body.sourceRef &&
            [A, nativeA].some((source) => sourceKey(source) === sourceKey(row.modelSource)));
          assert.ok(artifact, "Timing is bound to an exact fixture model's receipt");
          if (url.pathname === "/api/events/timing") {
            assert.ok(["model_load", "model_download", "model_parse", "model_install", "model_projection"].includes(body.phase));
            assert.ok([A.assetSha256, nativeA.assetSha256].includes(body.details?.asset_sha256));
            assert.equal(body.details.asset_sha256, artifact.sha256);
          }
        }
        writes.push({ method: request.method(), path: url.pathname, modelSource: body.modelSource ?? null });
        await route.continue();
      } else if (nextFailures.delete(url.pathname)) {
        failedRefreshes.push(url.pathname);
        await route.fulfill({ status: 503, headers: { "x-version-fixture-failure": "expected" },
          json: { code: "FIXTURE_REFRESH_FAILURE", detail: "This discovery refresh is deliberately unavailable." } });
      } else if (["/api/artifacts", "/api/working-copies", "/api/design-history", "/api/worktrees", "/api/working-source"].includes(url.pathname)) {
        // These are actual runtime answers. List visibility represents arrival
        // after an earlier read. Drop ETags because that visibility is controlled
        // here; forwarding the runtime's unchanged tag could hide its revelation.
        const value = await fixture.getJson(url.pathname, Object.fromEntries(url.searchParams));
        if (!revealB && url.pathname === "/api/artifacts")
          value.artifacts = value.artifacts.filter((row) => sourceKey(row.modelSource) !== sourceKey(B));
        if (!revealB && url.pathname === "/api/working-copies")
          value.workingCopies = value.workingCopies.map((copy) => copy.groupId === group.groupId
            ? { ...copy, selectedOptionId: "A", options: copy.options.filter((option) => sourceKey(option.modelSource) !== sourceKey(B)) } : copy);
        if (!revealB && url.pathname === "/api/design-history")
          value.candidates = value.candidates.filter((candidate) => candidate.candidateId !== B.runId);
        await route.fulfill({ json: value, headers: { "cache-control": "no-store" } });
      } else await route.continue();
    } catch (error) {
      errors.push(`Blocked unexpected API request: ${String(error)}`);
      await route.abort("blockedbyclient");
    }
  });

  await page.goto(`${origin}/?lang=en`, { waitUntil: "domcontentloaded" });
  let baseline, connectionCount;
  await step("normal mode shares one event stream and restores the runtime's exact working model", async () => {
    await versions().waitFor();
    await settledSource(A);
    assert.equal(await page.locator(".drawer").count(), 0);
    assert.equal(await page.locator(".events").count(), 0);
    assert.equal(await readyNotice().count(), 0);
    assert.equal(await versions().getAttribute("aria-expanded"), "false");
    const preferences = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), preferenceKey);
    assert.equal(preferences.developerMode, false);
    assert.equal(preferences.eventStreamVisible, false);
    assert.equal(Object.hasOwn(preferences, "editingBases"), false, "No browser editing-base migration supplies authority");
    baseline = await preservedState();
    assert.deepEqual(baseline.loadedModelSource, A);
    assert.deepEqual(baseline.editingModelSource, A);
    assert.equal(baseline.workingDraft.current.runId, A.runId);
    assert.ok(requests.some(({ path: name }) => name === `/api/artifacts/${A.assetSha256}/bytes`));
    assert.ok((await state()).choiceSources.some((source) => sourceKey(source) === sourceKey(alternateB)));
    connectionCount = await oneStream();
  });

  await step("a newly available candidate announces itself without replacing the current view or work", async () => {
    revealB = true;
    await refreshFrom(event(1, "candidate.succeeded"));
    await readyNotice().waitFor();
    assert.deepEqual(await preservedState(), baseline);
    assert.equal(await versions().getAttribute("aria-expanded"), "false");
    assert.equal(await page.locator("[data-project-surface='tree']:not([hidden])").count(), 0);
    assert.equal(await oneStream(), connectionCount);
  });

  await step("registration, retained-option and candidate events refresh exact choices without selecting them", async () => {
    for (const [index, type] of ["model_asset.registered", "working_copy.option_added", "candidate.succeeded"].entries()) {
      await refreshFrom(event(index + 2, type));
      assert.ok((await state()).choiceSources.some((source) => sourceKey(source) === sourceKey(B)));
      assert.deepEqual(await preservedState(), baseline);
    }
    await readyNotice().click();
    await page.locator(`.design-tree-inspector[data-node=${JSON.stringify(`candidate:${B.runId}`)}]`).waitFor();
    await page.getByRole("button", { name: "List", exact: true }).click();
    await choice().waitFor();
    assert.equal(await choice().getAttribute("aria-selected"), "true");
    assert.equal(await readyNotice().count(), 0, "Inspecting the ready candidate acknowledges it without viewing or continuing it");
    await backToModel();
    assert.deepEqual(await preservedState(), baseline);
  });

  await step("duplicate and reconnect replay never repeat the acknowledged candidate notice", async () => {
    await settledReads();
    const before = listCounts();
    await emit(event(4, "candidate.succeeded"));
    await painted(); await sleep(150);
    assert.deepEqual(listCounts(), before, "The already-seen stream sequence must not refresh lists again");
    await emit(event(4, "candidate.succeeded"), { reconnect: true, replay: true });
    await settledReads();
    assert.equal(await readyNotice().count(), 0);
    await refreshFrom(event(5, "candidate.succeeded"));
    assert.equal(await readyNotice().count(), 0, "An unchanged result remains acknowledged even under a new event sequence");
    assert.deepEqual(await preservedState(), baseline);
    assert.equal(await oneStream(), connectionCount);
  });

  await step("failed artifact or retained-option refresh keeps the model and existing choices usable", async () => {
    for (const [index, pathname] of ["/api/artifacts", "/api/working-copies"].entries()) {
      nextFailures.add(pathname);
      await refreshFrom(event(6 + index));
      assert.deepEqual(await preservedState(), baseline);
      assert.ok((await state()).choiceSources.some((source) => sourceKey(source) === sourceKey(B)));
      await assertChoices();
      assert.equal(await readyNotice().count(), 0);
    }
    assert.deepEqual(failedRefreshes, ["/api/artifacts", "/api/working-copies"]);
  });

  await step("1121 and 1440 pixel windows keep controls reachable, camera steady and exact sources unchanged", async () => {
    // A real view control first proves the camera is live. No fixed coordinates,
    // tree labels, folds, sibling order or total notice count enters the assertion.
    await more().click();
    await page.locator("#stage-more-menu").getByRole("button", { name: "Front", exact: true }).click();
    await more().click();
    await painted();
    const front = await preservedState();
    assert.notDeepEqual(front.camera, baseline.camera, "The view control must move the real camera");
    for (const viewport of [{ width: 1121, height: 874 }, { width: 1440, height: 900 }]) {
      await page.setViewportSize(viewport); await painted();
      const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
      assert.ok(layout.document <= layout.viewport + 1 && layout.body <= layout.viewport + 1, `No horizontal overflow: ${JSON.stringify(layout)}`);
      for (const control of [versions(), more(), page.locator("button[aria-controls='model-tools-more']")]) {
        await control.click({ trial: true });
        const box = await control.boundingBox();
        assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= viewport.width + 1 && box.y + box.height <= viewport.height + 1);
      }
      await more().click();
      await page.locator("#stage-more-menu").getByRole("button", { name: "Front", exact: true }).click({ trial: true });
      await more().click();
      assert.deepEqual(await preservedState(), front);
      const screenshot = path.join(screenshotDir, `model-${viewport.width}x${viewport.height}.png`);
      await page.screenshot({ path: screenshot }); screenshots.push(screenshot);
    }
    baseline = front;
    assert.equal(await oneStream(), connectionCount);
  });

  await step("registering another exact asset in the same run never replaces the export explicitly being viewed", async () => {
    await showVersions(true);
    const native = fixture.artifacts.artifacts.find((row) => sourceKey(row.modelSource) === sourceKey(nativeA));
    assert.ok(native?.fileName);
    // A native export remains reachable in the actual Versions inspector. Open
    // its enclosing details without depending on their labels or fold counts.
    const exportButton = page.locator("#stage-versions-panel button.vcard__export").filter({ has: page.locator(`.vcard__filename`, { hasText: native.fileName }) });
    const folds = exportButton.locator("xpath=ancestor::details");
    for (let index = 0; index < await folds.count(); index++) {
      const fold = folds.nth(index);
      if (await fold.getAttribute("open") === null) await fold.locator(":scope > summary").click();
    }
    await exportButton.click();
    await settledSource(nativeA);
    await showVersions(false);
    const original = await preservedState();
    assert.deepEqual(original.loadedModelSource, nativeA);
    assert.deepEqual(original.editingModelSource, A);
    assert.deepEqual(original.workingDraft, baseline.workingDraft);
    const bytes = await fetch(new URL(`/api/artifacts/${B.assetSha256}/bytes`, apiOrigin)).then(async (response) => {
      assert.ok(response.ok); return Buffer.from(await response.arrayBuffer());
    });
    const added = await fixture.request("POST", "/api/model-assets", { projectId: project.projectId, runId: A.runId,
      stateDigest: A.stateDigest, fileName: "another-same-run.3dm", contentBase64: bytes.toString("base64") });
    assert.equal(added.modelSource.runId, nativeA.runId);
    assert.notEqual(added.modelSource.assetSha256, nativeA.assetSha256);
    await refreshFrom(event(8, "model_asset.registered", A.runId));
    assert.ok((await state()).choiceSources.some((source) => sourceKey(source) === sourceKey(added.modelSource)));
    assert.deepEqual(await preservedState(), original);
    assert.equal(await readyNotice().count(), 0, "A registered export alone does not readmit an acknowledged candidate");
    assert.equal(await oneStream(), connectionCount);
  });

  assert.deepEqual(blockedWrites, []);
  assert.deepEqual(errors, []);
  assert.ok(writes.every(({ path: name }) => ["/api/captures", "/api/events/model-load", "/api/events/timing"].includes(name)));
  console.log(JSON.stringify({ passed: passed.length, projectId: project.projectId, sources,
    listRefreshes: listCounts(), failedRefreshes, activeSseConnections: 1, previewAndTimingWrites: writes,
    workingPositionWrites: 0, jsErrors: errors.length, screenshots }, null, 2));
} catch (error) {
  console.error(`FAIL ${phase}: ${error.stack ?? error}`);
  console.error(JSON.stringify({ errors, blockedWrites, failedRefreshes, passed, screenshots, requests: requests.slice(-30) }, null, 2));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
  await fixture.close();
}
