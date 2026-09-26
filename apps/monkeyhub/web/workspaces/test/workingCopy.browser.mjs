import assert from "node:assert/strict";
import { mkdir, readdir, realpath } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";

// This exercises the unmodified App against a disposable, explicitly named
// project copy. It intentionally leaves one new saved line on each model and
// selects B. No production/test entry point or mocked API is installed.
// Required: STUDIO_AB_URL (API origin), STUDIO_AB_PROJECT_ROOT (absolute path).
// Optional: STUDIO_AB_SCREENSHOT (absolute output path outside both roots).
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const oldEditingRun = "rooms-ML-window-dimensions-shell-v02";
const drawingSha = "527a4eac1748d1b8a491d877cf69e0464a82a8ab282a3029ca99698a66a645e2";
assert.ok(process.env.STUDIO_AB_URL, "STUDIO_AB_URL must name the isolated API origin");
assert.ok(process.env.STUDIO_AB_PROJECT_ROOT, "STUDIO_AB_PROJECT_ROOT must name the isolated project copy");
const apiOrigin = new URL(process.env.STUDIO_AB_URL);
const forbiddenPorts = new Set(["5187", "5188", "60617", "60616"]);
assert.ok(["http:", "https:"].includes(apiOrigin.protocol), "Use an HTTP(S) API origin");
assert.ok(["127.0.0.1", "localhost", "[::1]"].includes(apiOrigin.hostname), "This test only writes to an explicitly isolated local API");
assert.ok(apiOrigin.port && !forbiddenPorts.has(apiOrigin.port), "The frozen user/test ports must never be used");
assert.equal(apiOrigin.username + apiOrigin.password + apiOrigin.search + apiOrigin.hash, "", "Use an origin without credentials or query parameters");
assert.equal(apiOrigin.pathname, "/", "STUDIO_AB_URL is the API origin, without a path");
const requestedRoot = process.env.STUDIO_AB_PROJECT_ROOT;
assert.ok(path.isAbsolute(requestedRoot), "STUDIO_AB_PROJECT_ROOT must be absolute");
const projectRoot = path.resolve(requestedRoot);
const screenshot = process.env.STUDIO_AB_SCREENSHOT;
function inside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}
if (screenshot) {
  assert.ok(path.isAbsolute(screenshot), "STUDIO_AB_SCREENSHOT must be absolute");
  assert.ok(!inside(webRoot, path.resolve(screenshot)) && !inside(projectRoot, path.resolve(screenshot)),
    "Screenshots belong at the explicitly supplied external path, outside source and project storage");
}

async function getJson(endpoint, query = {}) {
  const url = new URL(endpoint, apiOrigin);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value));
  const response = await fetch(url, { redirect: "error", signal: AbortSignal.timeout(20_000) });
  assert.equal(response.ok, true, `GET ${url.pathname} returned ${response.status}: ${await response.clone().text()}`);
  return response.json();
}
function sourceKey(source) {
  return JSON.stringify([source.runId, source.stateDigest, source.assetSha256]);
}
function sameSource(actual, expected, message) {
  assert.deepEqual(actual, expected, message);
}
async function verifyProject() {
  const value = await getJson("/api/project");
  assert.ok(path.isAbsolute(value.projectDir), "/api/project must report an absolute projectDir");
  assert.equal(path.resolve(value.projectDir), projectRoot, "Refusing to launch or write: API projectDir is not the explicitly supplied isolated project");
  assert.equal(await realpath(value.projectDir), await realpath(projectRoot), "The API and expected project must resolve to the identical directory");
  return value;
}

// Complete all read-only binding checks before creating either Vite or Chrome.
const project = await verifyProject();
const [protocol, listing, artifacts, initialState] = await Promise.all([
  getJson("/api/protocol"), getJson("/api/working-copies"), getJson("/api/artifacts"), getJson("/api/state", { run: oldEditingRun }),
]);
assert.ok(protocol.capabilities.includes("working-copies") && protocol.capabilities.includes("model-annotations"),
  "The isolated API must advertise working copies and persistent model annotations");
assert.equal(initialState.projectId, project.projectId);
assert.equal(artifacts.projectId, project.projectId);
assert.equal(initialState.referenceRun.runId, oldEditingRun, "The existing browser choice must resolve to the old editing run");
const group = listing.workingCopies.find((copy) => copy.projectId === project.projectId && copy.groupId === "m-upper-cabinets");
assert.ok(group, "The real m-upper-cabinets working copy must be present");
assert.equal(group.options.length, 2);
// This fixture already records B as the working-copy selection. Merely reading
// that selection must not replace the browser's existing old editing choice.
const optionB = group.options.find((option) => option.id === group.selectedOptionId);
assert.ok(optionB, "The supplied fixture must already select B in its working-copy group");
const optionA = group.options.find((option) => option.id !== optionB.id);
const sourceA = optionA.modelSource;
const sourceB = optionB.modelSource;
assert.notEqual(sourceA.runId, sourceB.runId, "A and B must belong to different retained model runs");
assert.notEqual(sourceA.assetSha256, sourceB.assetSha256, "A and B must have different exact model files");
for (const source of [sourceA, sourceB]) {
  const row = artifacts.artifacts.find((item) => item.runId === source.runId && item.sha256 === source.assetSha256);
  assert.ok(row?.available && row.format === "3dm", "Every option needs its available exact 3dm in the real artifact list");
  sameSource(row.modelSource, source, "Artifact and working copy must name the same full model source");
}
const [baselineA, baselineB] = await Promise.all([
  getJson("/api/model-annotations", sourceA), getJson("/api/model-annotations", sourceB),
]);
for (const [baseline, source] of [[baselineA, sourceA], [baselineB, sourceB]]) {
  assert.equal(baseline.projectId, project.projectId);
  sameSource(baseline.modelSource, source);
}

// Include every actual storage run, including an R unrelated to model A or B.
async function documentSnapshot() {
  const entries = await readdir(path.join(projectRoot, "runs"), { withFileTypes: true });
  const runs = entries.filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
  const result = [];
  for (const runId of runs) {
    const sources = await getJson("/api/documents", { runId });
    const pages = [];
    for (const document of sources.documents) {
      for (const page of document.pages) {
        pages.push(await getJson("/api/document-annotations", {
          runId, assetSha256: document.assetSha256, pageIndex: page.pageIndex,
        }));
      }
    }
    result.push({ runId, sources, pages });
  }
  return result;
}
const documentsBefore = await documentSnapshot();
const drawingRun = documentsBefore.find((run) => run.runId === oldEditingRun);
const drawingSource = drawingRun?.sources.documents.find((document) => document.assetSha256 === drawingSha);
const drawingBaseline = drawingRun?.pages.find((page) => page.assetSha256 === drawingSha && page.pageIndex === 0);
assert.ok(drawingSource && drawingBaseline?.revisionSha256, "The old run must retain the specified L6 drawing and its saved first page");
assert.equal(drawingSource.modelSource ?? null, null, "L6 must start without a linked model");

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
let vite;
let browser;
let page;
let phase = "setup";
let allowedMutation = null;
const requests = [];
const modelReads = [];
const documentReads = [];
const modelWrites = [];
const selectionWrites = [];
const pendingResponses = new Set();
const errors = [];
const consoleErrors = [];
const passed = [];
const preferenceStorageKey = "archflow-studio.user-preferences";
const editPreferenceKey = JSON.stringify(["", project.projectId]);
const existingEditingBases = { [JSON.stringify(["unrelated-server", "unrelated-project"])]: "kept-choice", [editPreferenceKey]: oldEditingRun };
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function until(read, accepts, message, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  let last;
  do {
    assert.deepEqual(errors, [], `Browser/network error during ${phase}`);
    last = await read();
    if (accepts(last)) return last;
    await sleep(80);
  } while (Date.now() < deadline);
  assert.fail(`${message}: ${JSON.stringify(last)}`);
}
async function step(name, action) {
  phase = name;
  await action();
  passed.push(name);
  console.log(`PASS ${name}`);
}
function versionButton(option) {
  const escapedLabel = option.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return page.locator(`[data-working-copy=${JSON.stringify(group.groupId)}]`)
    .getByRole("button", { name: new RegExp(`^View ${escapedLabel}`) });
}
const inkCanvas = () => page.locator('.stage-model canvas.annotate[aria-hidden="true"]');
const activeCanvas = () => page.locator(".stage-model canvas.annotate[data-armed]");
const undo = () => page.locator(".stage-model .viewtools").getByRole("button", { name: "Undo mark", exact: true });
async function editingBases() {
  return page.evaluate((key) => JSON.parse(localStorage.getItem(key)).editingBases, preferenceStorageKey);
}
async function openVersions() {
  const button = page.locator(".stage__versions-toggle");
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
}
const workspaceState = () => page.evaluate(() => window.__workingCopyWorkspace);
const editingBase = () => page.locator(".editing-base");
const annotationStatus = () => page.locator("#stage-versions-panel [data-model-annotations-status]");
async function editingRun() {
  await openVersions();
  if (await editingBase().getAttribute("data-source-match") === "same") {
    for (const option of group.options) {
      if (await versionButton(option).getAttribute("aria-pressed") === "true") return option.modelSource.runId;
    }
    // The original ungrouped model remains identified by its Reference card.
    return page.locator("#stage-versions-panel .vcard[title]").filter({
      has: page.locator(".vcard__head > .label").filter({ hasText: /^Reference$/ }),
    }).getAttribute("title");
  }
  const label = (await editingBase().locator(".editing-base__name").textContent()).trim();
  const option = group.options.find((row) => label === `${group.label} · ${row.label}`);
  if (option) return option.modelSource.runId;
  const runs = [...new Set(artifacts.artifacts.filter((row) => row.fileName === label).map((row) => row.runId))];
  assert.equal(runs.length, 1, `The editing notice must identify one retained model: ${label}`);
  return runs[0];
}
async function ready(option) {
  await openVersions();
  await until(async () => ({ pressed: await versionButton(option).getAttribute("aria-pressed"),
    status: await annotationStatus().getAttribute("data-model-annotations-status") }),
  (value) => value.pressed === "true" && value.status === "saved", `Exact ${option.label} model and its annotations did not become ready`, 60_000);
  await page.locator(".stage-model .viewtools").getByTitle("Draw a straight annotation line", { exact: true }).waitFor();
}
async function drawingReady(readStart = 0) {
  await page.locator('.document-viewport[data-ready="true"]').waitFor({ timeout: 60_000 });
  assert.equal(await page.locator(".document-header > select").inputValue(), JSON.stringify([oldEditingRun, drawingSha, null]));
  assert.equal(await page.locator(".document-pages select").inputValue(), "0");
  assert.equal(await page.locator(".document-model-source").getAttribute("data-model-source-status"), "unknown");
  // GH-302: the page saves itself; there is no Save page button.
  await until(() => page.locator("#document-comment").isEditable(), Boolean, "The existing L6 page must remain editable");
  const read = await until(() => documentReads.slice(readStart).find((entry) =>
    entry.response.runId === oldEditingRun && entry.response.assetSha256 === drawingSha && entry.response.pageIndex === 0),
  Boolean, "The browser must read L6 from its original storage run");
  assert.deepEqual(read.response, drawingBaseline, "L6's actual saved revision and annotations must be unchanged");
}
function byteReads(start) {
  return requests.slice(start).filter((request) => request.method === "GET" && /^\/api\/artifacts\/[^/]+\/bytes$/.test(request.pathname));
}
function assertOnlyExactBytes(start, source) {
  const reads = byteReads(start);
  assert.ok(reads.length > 0, "The real exact file must be fetched");
  assert.deepEqual([...new Set(reads.map((request) => request.pathname))], [`/api/artifacts/${source.assetSha256}/bytes`],
    "A model option must not load another full model or any partial preview beside it");
}
async function inkImage() {
  // Let the public canvas finish painting and the camera-moved opacity settle.
  await sleep(380);
  return inkCanvas().evaluate((canvas) => canvas.toDataURL());
}
async function openOption(option) {
  await openVersions();
  const start = requests.length;
  await versionButton(option).click();
  await ready(option);
  assertOnlyExactBytes(start, option.modelSource);
}
async function drawLine(option, baseline, yRatio) {
  const before = modelWrites.length;
  allowedMutation = { kind: "annotations", source: option.modelSource };
  await page.locator(".stage-model .viewtools").getByTitle("Draw a straight annotation line", { exact: true }).click();
  await until(() => activeCanvas().getAttribute("data-armed"), (value) => value === "true", "Line tool did not arm");
  const rectangle = await activeCanvas().boundingBox();
  assert.ok(rectangle && rectangle.width > 200 && rectangle.height > 200, "The live 3D annotation canvas must be visible");
  const start = { x: rectangle.x + rectangle.width * 0.34, y: rectangle.y + rectangle.height * yRatio };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + rectangle.width * 0.17, start.y + 13, { steps: 6 });
  await page.mouse.up();
  await until(() => modelWrites.length, (count) => count === before + 1, `One ${option.label} line must produce exactly one successful PUT`);
  await ready(option);
  assert.equal(allowedMutation, null, "The intended annotation PUT must consume its only write allowance");
  const write = modelWrites[before];
  sameSource(write.body.modelSource, option.modelSource);
  assert.equal(write.body.projectId, project.projectId);
  assert.equal(write.body.baseRevisionSha256, baseline.revisionSha256);
  assert.deepEqual(write.body.annotations.slice(0, -1), baseline.annotations, "Another source's old ink must not enter this PUT");
  assert.equal(write.body.annotations.length, baseline.annotations.length + 1);
  const line = write.body.annotations.at(-1);
  assert.equal(line.kind, "line");
  assert.ok(line.id && !baseline.annotations.some((item) => item.id === line.id));
  assert.deepEqual(write.response.annotations, write.body.annotations.map((gesture) => ({
    label: null, lengthModelUnits: null, worldDirection: null, worldEnd: null, worldStart: null, ...gesture,
  })), "The saved geometry must match the sent geometry with the API's optional null defaults");
  assert.equal(await undo().isEnabled(), true, "The new model-specific stroke must be undoable");
  return { ...write, line, image: await inkImage() };
}
async function takeScreenshot() {
  if (!screenshot || !page || page.isClosed()) return;
  await mkdir(path.dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: true });
}

try {
  await verifyProject();
  vite = await createServer({ root: webRoot, configFile: fileURLToPath(new URL("./vite.config.ts", import.meta.url)), logLevel: "error",
    plugins: [{ name: "working-copy-workspace-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/src/workspaces/monkeyboard/Board.tsx`) return { code: `
        export default function Board({onOpenDocument}) {
          return <button onClick={() => onOpenDocument({source: ${JSON.stringify({runId: oldEditingRun, assetSha256: drawingSha, pageIndex: 0})},
            view:{scrollX:0,scrollY:0,zoom:1,selectedElementIds:{},selectedGroupIds:{}}})}>Open saved L6 page</button>;
        }`, map: null };
      if (filename !== `${root}/src/app/App.tsx`) return;
      const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
      assert.equal(source.split(marker).length, 2);
      return { code: source.replace(marker, marker + `
        (window as unknown as { __workingCopyWorkspace: unknown }).__workingCopyWorkspace = {
          loadedModelSource, editingModelSource, status: viewerStatus, loadingSha: artifactLoadingSha,
        };`), map: null };
    } }],
    server: { host: "127.0.0.1", port: 0, strictPort: true, hmr: false,
      proxy: { "/api": { target: apiOrigin.origin, changeOrigin: false, followRedirects: false } } },
  });
  await vite.listen();
  const uiPort = String(vite.httpServer.address().port);
  assert.ok(!forbiddenPorts.has(uiPort), "Ephemeral UI port must not be a frozen port");
  const uiOrigin = `http://127.0.0.1:${uiPort}`;
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1540, height: 1060 }, deviceScaleFactor: 1, locale: "en-US" });
  await context.addInitScript(({ key, bases }) => {
    if (localStorage.getItem(key) === null) localStorage.setItem(key, JSON.stringify({
      version: 1, language: "en", theme: "light", fontScale: 1, eventStreamVisible: false, developerMode: true, editingBases: bases,
    }));
  }, { key: preferenceStorageKey, bases: existingEditingBases });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(`pageerror: ${String(error)}`));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    try {
      assert.equal(url.origin, uiOrigin, "The browser may only call its isolated API proxy");
      if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
        const body = request.postDataJSON();
        assert.equal(request.method(), "PUT", "This scenario has no authorized POST/DELETE actions");
        assert.ok(allowedMutation, `Unexpected ${request.method()} ${url.pathname}`);
        assert.equal(body.projectId, project.projectId);
        if (allowedMutation.kind === "annotations") {
          assert.equal(url.pathname, "/api/model-annotations");
          sameSource(body.modelSource, allowedMutation.source);
        } else {
          assert.equal(url.pathname, `/api/working-copies/${encodeURIComponent(group.groupId)}/selection`);
          assert.equal(body.optionId, optionB.id);
          assert.equal(body.baseRevisionSha256, group.revisionSha256);
        }
        await verifyProject();
        allowedMutation = null;
      }
      await route.continue();
    } catch (error) {
      errors.push(`Blocked unexpected API request: ${String(error)}`);
      await route.abort("blockedbyclient");
    }
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return;
    requests.push({ method: request.method(), pathname: url.pathname, search: url.search,
      body: request.postData() ? request.postDataJSON() : null });
  });
  page.on("response", (response) => {
    const request = response.request();
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/") || url.pathname === "/api/events") return;
    const task = (async () => {
      if (!response.ok()) { errors.push(`${request.method()} ${url.pathname}: HTTP ${response.status()}`); return; }
      if (url.pathname === "/api/model-annotations") {
        const value = await response.json();
        if (request.method() === "PUT") modelWrites.push({ body: request.postDataJSON(), response: value });
        else modelReads.push({ source: Object.fromEntries(url.searchParams), response: value });
      } else if (request.method() === "GET" && url.pathname === "/api/document-annotations") {
        documentReads.push({ response: await response.json() });
      } else if (request.method() === "PUT" && url.pathname.endsWith("/selection")) {
        selectionWrites.push({ body: request.postDataJSON(), response: await response.json() });
      }
    })().catch((error) => errors.push(`Response inspection: ${String(error)}`));
    pendingResponses.add(task);
    void task.finally(() => pendingResponses.delete(task));
  });

  const fixtureUrl = new URL(uiOrigin);
  fixtureUrl.search = new URLSearchParams({ lang: "en", view: "board" }).toString();
  await page.goto(fixtureUrl.href, { waitUntil: "domcontentloaded" });
  let savedA;
  let savedB;
  await step("the saved editing preference and exact Board page survive reading a group that already selects B", async () => {
    await page.getByRole("button", { name: "Open saved L6 page", exact: true }).click();
    await drawingReady();
    assert.equal(await editingRun(), oldEditingRun);
    assert.deepEqual(await editingBases(), existingEditingBases);
    assert.equal(modelWrites.length + selectionWrites.length, 0);
    await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor();
    assert.deepEqual(await getJson("/api/document-annotations", { runId: oldEditingRun, assetSha256: drawingSha, pageIndex: 0 }), drawingBaseline,
      "Saving an unchanged L6 page must retain its existing revision");
    assert.equal(requests.filter((request) => !["GET", "HEAD", "OPTIONS"].includes(request.method)).length, 0);
    await page.getByTestId("workspace-arch").click();
    await until(workspaceState, value => value?.status === "ready" && value?.loadedModelSource?.runId === oldEditingRun,
      "The old model must finish loading before viewing B", 60_000);
  });
  await step("view B fetches only B's exact model without selecting it or changing the existing editing choice", async () => {
    await openOption(optionB);
    assert.equal(await editingRun(), oldEditingRun);
    assert.deepEqual(await editingBases(), existingEditingBases);
    assert.equal(selectionWrites.length, 0);
    assert.equal(modelWrites.length, 0);
    assert.equal(await undo().isDisabled(), true, "A's history must not be inherited by B");
    const read = await until(() => modelReads.find((entry) => sourceKey(entry.response.modelSource) === sourceKey(sourceB)),
      Boolean, "Viewing B must read its own server annotations");
    assert.deepEqual(read.response, baselineB);
  });
  await step("one real line on A and one on B save their exact sources with an identical camera", async () => {
    await openOption(optionA);
    assert.equal(await editingRun(), oldEditingRun);
    assert.equal(await undo().isDisabled(), true);
    const readA = await until(() => modelReads.find((entry) => sourceKey(entry.response.modelSource) === sourceKey(sourceA)),
      Boolean, "Viewing A must read its own server annotations");
    assert.deepEqual(readA.response, baselineA);
    savedA = await drawLine(optionA, baselineA, 0.50);
    await openOption(optionB);
    assert.equal(await undo().isDisabled(), true, "Switching from edited A must not give untouched B an Undo entry");
    savedB = await drawLine(optionB, baselineB, 0.68);
    assert.deepEqual(savedA.line.camera, savedB.line.camera, "Viewing B must preserve A's exact camera");
    assert.deepEqual(savedA.line.screenSize, savedB.line.screenSize);
    assert.ok(!savedA.body.annotations.some((item) => item.id === savedB.line.id));
    assert.ok(!savedB.body.annotations.some((item) => item.id === savedA.line.id));
    assert.equal(modelWrites.length, 2);
    assert.equal(selectionWrites.length, 0);
    assert.deepEqual(await editingBases(), existingEditingBases);
  });
  await step("switching A/B restores each visible ink canvas and each independent Undo history", async () => {
    await openOption(optionA);
    assert.equal(await undo().isEnabled(), true);
    assert.equal(await inkImage(), savedA.image, "A's visible saved ink must return exactly");
    await openOption(optionB);
    assert.equal(await undo().isEnabled(), true);
    assert.equal(await inkImage(), savedB.image, "B's visible saved ink must return without A's line");
    assert.equal(modelWrites.length, 2, "Viewing an already saved model must not rewrite its ink");
    const [storedA, storedB] = await Promise.all([
      getJson("/api/model-annotations", sourceA), getJson("/api/model-annotations", sourceB),
    ]);
    assert.deepEqual(storedA, savedA.response);
    assert.deepEqual(storedB, savedB.response);
  });
  await step("only explicit Continue selects B and persists the editing choice", async () => {
    assert.equal(selectionWrites.length, 0);
    allowedMutation = { kind: "selection" };
    await openVersions();
    await editingBase()
      .getByRole("button", { name: "Continue from here", exact: true }).click();
    await until(() => selectionWrites.length, (count) => count === 1, "Continue must write exactly one working-copy selection");
    await until(editingRun, (runId) => runId === sourceB.runId, "The editing base must become B");
    await ready(optionB);
    assert.deepEqual(await editingBases(), { ...existingEditingBases, [editPreferenceKey]: sourceB.runId });
    assert.equal(selectionWrites[0].response.selectedOptionId, optionB.id);
    assert.equal(modelWrites.length, 2);
    assert.equal(await inkImage(), savedB.image, "Continuing must not clear B's saved ink or move its camera");
  });
  await step("reloading the same browser restores B's full file and server ink while drawing storage stays unchanged", async () => {
    const start = requests.length;
    const readsBefore = modelReads.length;
    const documentReadsBefore = documentReads.length;
    assert.equal(page.url(), fixtureUrl.href, "Workspace navigation must not rewrite the test host URL");
    await page.reload({ waitUntil: "domcontentloaded" });
    assert.equal(page.url(), fixtureUrl.href);
    await page.getByRole("button", { name: "Open saved L6 page", exact: true }).click();
    await drawingReady(documentReadsBefore);
    assert.equal(await editingRun(), sourceB.runId);
    assert.deepEqual(await editingBases(), { ...existingEditingBases, [editPreferenceKey]: sourceB.runId });
    await page.getByTestId("workspace-arch").click();
    await ready(optionB);
    assertOnlyExactBytes(start, sourceB);
    assert.equal(await editingRun(), sourceB.runId);
    assert.deepEqual(await editingBases(), { ...existingEditingBases, [editPreferenceKey]: sourceB.runId });
    assert.ok(requests.slice(start).some((request) => request.pathname === "/api/state" &&
      new URLSearchParams(request.search).get("run") === sourceB.runId), "Reload must request the persisted B editing run");
    const read = await until(() => modelReads.slice(readsBefore).find((entry) => sourceKey(entry.response.modelSource) === sourceKey(sourceB)),
      Boolean, "Reload must obtain B's ink from the real API");
    assert.deepEqual(read.response, savedB.response);
    assert.equal(await undo().isDisabled(), true, "A new browser page starts a fresh Undo history over the retained server ink");
    await until(() => inkCanvas().evaluate((canvas, point) => {
      const context = canvas.getContext("2d");
      const [x, y] = point;
      return [...context.getImageData(Math.max(0, x - 3), Math.max(0, y - 3), 7, 7).data].some((value, index) => index % 4 === 3 && value > 0);
    }, savedB.line.screen[0].map((value, index) => Math.round((value + savedB.line.screen[1][index]) / 2))),
    Boolean, "B's restored saved stroke must be painted on the public annotation canvas");
    assert.deepEqual(await documentSnapshot(), documentsBefore, "Model view, ink, Continue, and reload must leave all drawing sources and page revisions unchanged");
    assert.equal(modelWrites.length, 2);
    assert.equal(selectionWrites.length, 1);
    await Promise.all([...pendingResponses]);
    assert.deepEqual(errors, [], "The full App must have zero JavaScript or failed API errors");
    await takeScreenshot();
  });
  console.log(JSON.stringify({ passed: passed.length, projectId: project.projectId, groupId: group.groupId,
    oldEditingRun, drawing: { runId: oldEditingRun, assetSha256: drawingSha, revisionSha256: drawingBaseline.revisionSha256, modelSource: null },
    sources: { A: sourceA, B: sourceB }, savedModelRevisions: { A: savedA.response.revisionSha256, B: savedB.response.revisionSha256 },
    annotationPuts: modelWrites.length, selectionPuts: selectionWrites.length, documentRunsChecked: documentsBefore.length,
    jsErrors: errors.length, consoleErrors, screenshot: screenshot ?? null }, null, 2));
} catch (error) {
  await takeScreenshot().catch(() => undefined);
  console.error(`FAIL ${phase}: ${error.stack ?? error}`);
  if (errors.length) console.error(JSON.stringify(errors, null, 2));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await vite?.close();
}
