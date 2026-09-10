import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

// Actual App and retained A/B model bytes, with every mutation intercepted.
// Program sheets, volume measurements and option baselines are read from the
// explicitly named disposable project. No candidate or selection is persisted.
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
for (const capability of ["working-copies", "model-annotations", "program"])
  assert.ok(protocol.capabilities.includes(capability), `The isolated API needs ${capability}`);
const originalGroup = copyList.workingCopies.find((row) => row.projectId === project.projectId && row.groupId === "m-upper-cabinets");
assert.ok(originalGroup);
const optionA = originalGroup.options.find((row) => row.id === "A");
const optionB = originalGroup.options.find((row) => row.id === "B");
assert.ok(optionA && optionB);
const sourceA = optionA.modelSource;
const sourceB = optionB.modelSource;
assert.notEqual(sourceA.runId, sourceB.runId);
assert.notEqual(sourceA.assetSha256, sourceB.assetSha256);
for (const source of [sourceA, sourceB]) {
  const artifact = artifactList.artifacts.find((row) => row.runId === source.runId && row.sha256 === source.assetSha256);
  assert.ok(artifact?.available && artifact.format === "3dm" && artifact.representation === "composed");
  assert.deepEqual(artifact.modelSource, source);
}
const otherB = artifactList.artifacts.find((row) => row.runId === sourceB.runId && row.available && row.format === "3dm" &&
  row.modelSource?.stateDigest === sourceB.stateDigest && row.sha256 !== sourceB.assetSha256);
assert.ok(otherB?.modelSource, "The real B run must expose a second exact asset for the same state");
const firstBOption = { id: "test-other-B-asset", label: "Other retained B asset", modelSource: otherB.modelSource };
// This read-only fixture makes a first-match-by-run implementation choose the
// other retained file. The user then explicitly views the complete, real B file.
let group = { ...originalGroup, options: [firstBOption, ...originalGroup.options] };
const data = new Map();
await Promise.all([sourceA, sourceB].map(async (source) => {
  const [state, program, options, volumes] = await Promise.all([
    getJson("/api/state", { run: source.runId }), getJson("/api/program", { run: source.runId }),
    getJson("/api/options", { run: source.runId }), getJson("/api/state/volumes", { run: source.runId }),
  ]);
  assert.equal(state.stateDigest, source.stateDigest);
  assert.equal(program.stateDigest, source.stateDigest);
  assert.equal(program.sheet.recordDigest, state.recordDigest);
  assert.equal(program.sheet.projectId, project.projectId);
  assert.equal(options.stateDigest, source.stateDigest);
  assert.ok(program.sheet.departments.some((department) => department.spaces.length > 0));
  assert.ok(volumes.volumes.length > 0);
  data.set(source.runId, { state, program, options, volumes });
}));
const baselineB = data.get(sourceB.runId).program;
assert.notEqual(baselineB.sheet.stateDigest, baselineB.stateDigest,
  "This regression needs the real Program sheet and outer binding digests to differ");
const departmentB = baselineB.sheet.departments.find((row) => row.spaces.length > 0);
const spaceB = departmentB.spaces[0];
const programA = structuredClone(data.get(sourceA.runId).program);
programA.sheet.departments[0].spaces[0].name = "A current program space";
const programByRun = new Map([[sourceA.runId, programA], [sourceB.runId, baselineB]]);
function massingOption(id, label, modelSource) {
  return {
    optionId: id, runId: `fixture-${id}`, label, transform: "add_floor", parameters: {},
    sourceRunId: modelSource.runId, stateDigest: modelSource.stateDigest, modelSource,
    metrics: data.get(modelSource.runId).options.baseline, envelopeFindings: [],
    recordRef: `fixture-only/${id}`, persistence: "browser fixture only", honesty: [],
  };
}
const wrongAssetOption = massingOption("wrong-B-asset", "B option from the other asset", otherB.modelSource);
const rightAssetOption = massingOption("exact-B-asset", "B option from the loaded complete asset", sourceB);
const activeAOption = massingOption("current-A-option", "A current option", sourceA);
const optionsByRun = new Map([
  [sourceA.runId, { ...data.get(sourceA.runId).options, options: [activeAOption] }],
  [sourceB.runId, { ...data.get(sourceB.runId).options, options: [wrongAssetOption] }],
]);
const errors = [];
const requests = [];
const writes = [];
const blockedProxyWrites = [];
const gates = new Set();
const heldReads = new Map();
let expectedWrite = null;
let phase = "setup";
let vite;
let browser;
let page;
const http = createHttpServer();
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-program-options-test-"));
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  const gate = { promise, resolve };
  gates.add(gate);
  return gate;
}
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
  console.log(`PASS ${name}`);
}
function versionButton(option) {
  const label = option.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return page.locator(`[data-working-copy=${JSON.stringify(group.groupId)}]`)
    .getByRole("button", { name: new RegExp(`^View ${label}`) });
}
async function openVersions() {
  const button = page.locator(".stage__versions-toggle");
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
}
async function withModelDetails(read) {
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "Settings", exact: true });
  await settings.getByRole("tab", { name: "Model", exact: true }).click();
  const source = settings.locator(".source");
  await source.waitFor({ state: "visible" });
  try { return await read(source); }
  finally {
    await settings.getByRole("button", { name: "Close", exact: true }).click();
    await settings.waitFor({ state: "detached" });
  }
}
const editingBase = () => page.locator("#stage-versions-panel .stage__versions-session .editing-base");
const annotationStatus = () => page.locator("#stage-versions-panel [data-model-annotations-status]");
async function editingRun() {
  await openVersions();
  if (await editingBase().getAttribute("data-source-match") === "same") {
    for (const option of group.options) {
      if (await versionButton(option).getAttribute("aria-pressed") === "true") return option.modelSource.runId;
    }
    assert.fail("A matching editing source must identify the viewed A/B option");
  }
  // The compact editing notice names the real option rather than exposing a
  // run id in its title. Resolve that visible label against this A/B fixture.
  const label = (await editingBase().locator(".editing-base__name").textContent()).trim();
  const option = group.options.find((row) => label === `${group.label} · ${row.label}`);
  assert.ok(option, `The editing notice must name a retained option: ${label}`);
  return option.modelSource.runId;
}
async function ready(option) {
  await openVersions();
  await withModelDetails((source) => until(async () => ({ selected: await versionButton(option).getAttribute("aria-pressed"),
    annotations: await annotationStatus().getAttribute("data-model-annotations-status"),
    loading: await source.locator(".source__status").count(),
    facts: await source.locator(".source__facts").count(),
  }), (value) => value.selected === "true" && value.annotations === "saved" && value.loading === 0 && value.facts === 1,
  `${option.label}'s real model did not load`, 60_000));
}
const programPanel = () => page.locator(".program[role=dialog]");
const optionsPanel = () => page.locator(".options[role=dialog]");
const applyButton = () => programPanel().getByRole("button", { name: "Apply as candidate", exact: true });
const addFloorButton = () => optionsPanel().getByRole("button", { name: "Add floor", exact: true });
function selectButton(option) {
  return optionsPanel().locator(".options__card").filter({ hasText: option.label })
    .getByRole("button", { name: "Select", exact: true });
}
async function openProgram() {
  if (await programPanel().count() === 0)
    await page.locator(".stage-model .viewtools").getByRole("button", { name: "Program", exact: true }).click();
  await programPanel().waitFor();
}
async function openOptions() {
  if (await optionsPanel().count() === 0) {
    const button = page.locator(".stage-model .viewtools").getByRole("button", { name: "Massing", exact: true });
    // A long, non-modal Program sheet can cover the toolbar. Its existing
    // Massing button remains keyboard reachable when both panels are needed.
    if (await programPanel().count()) await button.press("Enter");
    else await button.click();
  }
  await optionsPanel().waitFor();
}
async function closeProgram() {
  if (await programPanel().count()) await programPanel().getByRole("button", { name: "Close", exact: true }).click();
}
async function closeOptions() {
  if (await optionsPanel().count()) await optionsPanel().getByRole("button", { name: "Close", exact: true }).click();
}
async function viewState() {
  await openVersions();
  const details = await withModelDetails(async (source) => ({ source: await source.textContent(),
    state: await source.getAttribute("data-state") }));
  return {
    ...details,
    editingRun: await editingRun(),
    editing: await editingBase().textContent(),
    sourceMatch: await editingBase().getAttribute("data-source-match"),
    A: await versionButton(optionA).getAttribute("aria-pressed"), B: await versionButton(optionB).getAttribute("aria-pressed"),
    context: await page.locator(".composer > .context").textContent(),
  };
}
function expectedProgramBody(value) {
  const sheet = structuredClone(baselineB.sheet);
  sheet.departments.find((row) => row.departmentId === departmentB.departmentId)
    .spaces.find((row) => row.spaceId === spaceB.spaceId).targetAreaM2 = value;
  return { stateDigest: sourceB.stateDigest, sourceRunId: sourceB.runId, modelSource: sourceB, sheet, saveInput: false };
}
function acceptProgram(body) {
  return { status: 202, json: { candidateId: "fixture-program-B", jobId: "fixture-program-job",
    status: "queued", savedInput: false, totals: body.sheet.totals, honesty: [] } };
}
function holdRead(endpoint, runId, response) {
  const held = { requested: false, release: deferred(), response };
  heldReads.set(`${endpoint}?run=${runId}`, held);
  return held;
}

try {
  vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", cacheDir,
    publicDir: ".generated/public", plugins: [react()],
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null,
      proxy: { "/api": { target: apiOrigin.origin, changeOrigin: false, followRedirects: false } } },
  });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/") && !readMethods.has(request.method)) {
      blockedProxyWrites.push(`${request.method} ${request.url}`);
      response.writeHead(405);
      response.end("Program/Options tests never forward mutations");
      return;
    }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const port = String(http.address().port);
  assert.ok(!forbiddenPorts.has(port));
  const uiOrigin = `http://127.0.0.1:${port}`;
  const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
    "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1200 }, locale: "en-US" });
  const preferenceKey = "archflow-studio.user-preferences";
  const editingKey = JSON.stringify(["", project.projectId]);
  await context.addInitScript(({ key, editingKey, runId }) => {
    localStorage.setItem(key, JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: true, editingBases: { [editingKey]: runId } }));
  }, { key: preferenceKey, editingKey, runId: sourceB.runId });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    try {
      assert.equal(url.origin, uiOrigin);
      requests.push({ method: request.method(), path: url.pathname, query: url.search });
      if (!readMethods.has(request.method())) {
        const expected = expectedWrite;
        assert.ok(expected, `Unexpected ${request.method()} ${url.pathname}`);
        expectedWrite = null;
        assert.equal(request.method(), expected.method ?? "POST");
        assert.equal(url.pathname, expected.path);
        const body = request.postData() ? request.postDataJSON() : null;
        if (expected.body !== undefined) assert.deepEqual(body, expected.body);
        writes.push({ method: request.method(), path: url.pathname, body });
        const response = expected.respond(body);
        if (expected.release) await expected.release.promise;
        await route.fulfill(response);
        return;
      }
      const runId = url.searchParams.get("run");
      const held = heldReads.get(`${url.pathname}?run=${runId}`);
      if (held) {
        heldReads.delete(`${url.pathname}?run=${runId}`);
        held.requested = true;
        await held.release.promise;
        await route.fulfill(held.response);
      } else if (url.pathname === "/api/working-copies") {
        await route.fulfill({ json: { ...copyList, workingCopies: copyList.workingCopies.map((copy) => copy.groupId === group.groupId ? group : copy) } });
      } else if (url.pathname === "/api/program") {
        assert.ok(programByRun.has(runId), "Program GET must request the explicit editing run");
        await route.fulfill({ json: programByRun.get(runId) });
      } else if (url.pathname === "/api/options") {
        assert.ok(optionsByRun.has(runId), "Options GET must request the explicit editing run");
        await route.fulfill({ json: optionsByRun.get(runId) });
      } else if (["/api/jobs/fixture-option-job", "/api/jobs/fixture-program-job"].includes(url.pathname)) {
        const isProgram = url.pathname.endsWith("fixture-program-job");
        await route.fulfill({ json: { jobId: isProgram ? "fixture-program-job" : "fixture-option-job",
          candidateId: isProgram ? "fixture-program-B" : "fixture-option-candidate",
          proposalId: isProgram ? "fixture-program" : rightAssetOption.optionId, status: "failed", createdAt: "2026-09-08T00:00:00Z",
          startedAt: null, finishedAt: "2026-09-08T00:00:00Z", error: "Execution is intercepted by this browser test.",
          wallTimeS: 0, lane: "parallel", waitingFor: null, waitingReason: null, persistence: "browser fixture only" } });
      } else {
        await route.continue();
      }
    } catch (error) {
      errors.push(`Blocked unexpected API request: ${String(error)}`);
      await route.abort("blockedbyclient");
    }
  });
  await page.goto(`${uiOrigin}/?lang=en`, { waitUntil: "domcontentloaded" });
  await ready(firstBOption);
  await versionButton(optionB).click();
  await ready(optionB);
  assert.equal(group.options[0].modelSource.runId, sourceB.runId);
  assert.equal(group.options[0].modelSource.stateDigest, sourceB.stateDigest);
  assert.notEqual(group.options[0].modelSource.assetSha256, sourceB.assetSha256);

  await step("Program edits preserve distinct inner/outer digests and the loaded complete B asset", async () => {
    await openProgram();
    await until(() => applyButton().isEnabled(), Boolean, "The real B sheet must be applicable even when its inner digest differs");
    const target = programPanel().getByLabel(`Target area of ${spaceB.name}, in square metres`, { exact: true });
    assert.equal(await target.inputValue(), String(spaceB.targetAreaM2));
    const value = spaceB.targetAreaM2 + 1.25;
    await target.fill(String(value));
    const body = expectedProgramBody(value);
    expectedWrite = { path: "/api/program", body, respond: acceptProgram };
    await applyButton().click();
    await until(() => writes.length, (count) => count === 1, "Program must dispatch exactly one intercepted Apply");
    await until(() => applyButton().isEnabled(), Boolean, "Program Apply must finish");
    assert.equal(writes[0].body.sheet.stateDigest, baselineB.sheet.stateDigest);
    assert.equal(writes[0].body.stateDigest, baselineB.stateDigest);
    assert.equal(writes[0].body.sheet.recordDigest, data.get(sourceB.runId).state.recordDigest);
    assert.deepEqual(writes[0].body.modelSource, sourceB);
    await closeProgram();
  });

  await step("Options create with B and select only a row bound to the same exact asset", async () => {
    await openOptions();
    await until(() => addFloorButton().isEnabled(), Boolean, "B's massing actions must be ready");
    assert.equal(await selectButton(wrongAssetOption).isDisabled(), true, "Same run/state with another SHA must not be selectable");
    assert.equal(writes.length, 1);
    expectedWrite = { path: "/api/options", body: { transform: "add_floor", stateDigest: sourceB.stateDigest,
      sourceRunId: sourceB.runId, modelSource: sourceB }, respond: () => {
      optionsByRun.set(sourceB.runId, { ...data.get(sourceB.runId).options, options: [wrongAssetOption, rightAssetOption] });
      return { status: 201, json: rightAssetOption };
    } };
    await addFloorButton().click();
    await until(() => selectButton(rightAssetOption).isEnabled(), Boolean, "The new exact-B option must become selectable");
    assert.equal(await selectButton(wrongAssetOption).isDisabled(), true);
    expectedWrite = { path: `/api/options/${rightAssetOption.optionId}/select`, body: null,
      respond: () => ({ status: 202, json: { candidateId: "fixture-option-candidate", jobId: "fixture-option-job", status: "queued" } }) };
    await selectButton(rightAssetOption).click();
    await until(() => writes.length, (count) => count === 3, "Only the matching option may dispatch selection");
    await until(() => selectButton(rightAssetOption).isEnabled(), Boolean, "Option selection must finish");
    assert.equal(writes.filter((row) => row.path.includes(wrongAssetOption.optionId)).length, 0);
    await closeOptions();
  });

  await step("View A leaves edit B read-only and a late Program STALE_BASE cannot reload the current view", async () => {
    await openProgram();
    await until(() => applyButton().isEnabled(), Boolean, "B's re-opened program must be ready");
    const target = programPanel().getByLabel(`Target area of ${spaceB.name}, in square metres`, { exact: true });
    const value = spaceB.targetAreaM2 + 2.5;
    await target.fill(String(value));
    const release = deferred();
    expectedWrite = { path: "/api/program", body: expectedProgramBody(value), release,
      respond: () => ({ status: 409, json: { code: "STALE_BASE", detail: "The old B request lost its original binding." } }) };
    await applyButton().click();
    await until(() => writes.length, (count) => count === 4, "The second Program request must be held before switching views");
    await versionButton(optionA).click();
    await ready(optionA);
    const before = await viewState();
    assert.equal(before.editingRun, sourceB.runId);
    const requestStart = requests.length;
    release.resolve();
    await page.locator(".card").filter({ hasText: "The old B request lost its original binding." }).waitFor();
    await until(() => applyButton().count(), (count) => count === 1, "The delayed Program request must finish");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await viewState(), before);
    assert.deepEqual(requests.slice(requestStart).filter((row) => row.path === "/api/project" || row.path === "/api/state"), [],
      "A late error from B must not refresh the current session");
    assert.equal(await applyButton().isDisabled(), true);
    await closeProgram();
    await openOptions();
    await selectButton(rightAssetOption).waitFor();
    assert.equal(await addFloorButton().isDisabled(), true);
    assert.equal(await selectButton(rightAssetOption).isDisabled(), true);
    assert.equal(await selectButton(wrongAssetOption).isDisabled(), true);
    assert.equal(writes.length, 4, "Viewing A must never submit B's sheet/options or borrow A's asset");
    assert.equal(await page.evaluate(({ key, editingKey }) => JSON.parse(localStorage.getItem(key)).editingBases[editingKey],
      { key: preferenceKey, editingKey }), sourceB.runId);
    await closeOptions();
  });

  await step("Continue A and new A reads survive both old B panel responses arriving last", async () => {
    const lateProgram = structuredClone(baselineB);
    lateProgram.sheet.departments[0].spaces[0].name = "B late program space";
    const lateOption = massingOption("late-B-option", "B late option", sourceB);
    const heldProgram = holdRead("/api/program", sourceB.runId, { json: lateProgram });
    const heldOptions = holdRead("/api/options", sourceB.runId,
      { json: { ...data.get(sourceB.runId).options, options: [lateOption] } });
    await openProgram();
    await until(() => heldProgram.requested, Boolean, "The old B program GET must be held");
    await openOptions();
    await until(() => heldOptions.requested, Boolean, "The old B options GET must be held");
    await openVersions();
    const continueButton = editingBase()
      .getByRole("button", { name: "Continue from this version", exact: true });
    assert.equal(await continueButton.isEnabled(), true, "Panel reads must not block explicit Continue");
    expectedWrite = { method: "PUT", path: `/api/working-copies/${group.groupId}/selection`,
      body: { projectId: project.projectId, baseRevisionSha256: group.revisionSha256, optionId: optionA.id },
      respond: () => {
        group = { ...group, selectedOptionId: optionA.id, revisionSha256: "e".repeat(64) };
        return { json: group };
      } };
    // Keyboard activation uses the existing public button while the two
    // non-modal reading panels remain open over the model.
    await continueButton.press("Enter");
    await until(async () => (await viewState()).editingRun, (runId) => runId === sourceA.runId, "Explicit Continue must select A");
    await ready(optionA);
    await until(async () => [await programPanel().count(), await optionsPanel().count()],
      (counts) => counts.every((count) => count === 0), "Continue must finish closing the previous editing panels");
    await openProgram();
    await until(() => applyButton().isEnabled(), Boolean, "A's new program must be applicable");
    const namesBefore = await programPanel().locator("input.program__text").evaluateAll((inputs) => inputs.map((input) => input.value));
    assert.ok(namesBefore.includes("A current program space"));
    await openOptions();
    await until(() => selectButton(activeAOption).isEnabled(), Boolean, "A's new option must be selectable");
    const lateReplies = Promise.all(["/api/program", "/api/options"].map((endpoint) => page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === "GET" && url.pathname === endpoint && url.searchParams.get("run") === sourceB.runId;
    })));
    heldProgram.release.resolve();
    heldOptions.release.resolve();
    await Promise.all((await lateReplies).map((response) => response.json()));
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await programPanel().locator("input.program__text").evaluateAll((inputs) => inputs.map((input) => input.value)), namesBefore);
    assert.equal(await applyButton().isEnabled(), true);
    assert.equal(await selectButton(activeAOption).isEnabled(), true);
    assert.equal(await optionsPanel().getByText(lateOption.label, { exact: true }).count(), 0);
    assert.equal(await programPanel().locator(".error").count(), 0);
    assert.equal(writes.length, 5, "Continue is the only additional intercepted mutation");
    assert.equal(await page.evaluate(({ key, editingKey }) => JSON.parse(localStorage.getItem(key)).editingBases[editingKey],
      { key: preferenceKey, editingKey }), sourceA.runId);
  });
  assert.equal(expectedWrite, null);
  assert.deepEqual(blockedProxyWrites, []);
  assert.deepEqual(errors, []);
  const counts = {
    program: writes.filter((row) => row.path === "/api/program").length,
    makeOption: writes.filter((row) => row.path === "/api/options").length,
    selectOption: writes.filter((row) => row.path === `/api/options/${rightAssetOption.optionId}/select`).length,
    continue: writes.filter((row) => row.method === "PUT").length,
  };
  assert.deepEqual(counts, { program: 2, makeOption: 1, selectOption: 1, continue: 1 });
  console.log(JSON.stringify({ passed: 4, projectId: project.projectId, sources: { A: sourceA, B: sourceB, otherB: otherB.modelSource },
    programDigests: { outer: baselineB.stateDigest, inner: baselineB.sheet.stateDigest, record: baselineB.sheet.recordDigest },
    interceptedMutations: writes.length, counts, realApiMutations: 0, jsErrors: errors.length }, null, 2));
} catch (error) {
  console.error(`FAIL ${phase}: ${error.stack ?? error}`);
  if (errors.length) console.error(JSON.stringify(errors, null, 2));
  if (page && !page.isClosed()) console.error(await page.locator("body").innerText().catch(() => "Cannot inspect failed page"));
  process.exitCode = 1;
} finally {
  for (const gate of gates) gate.resolve();
  await browser?.close();
  if (http.listening) await new Promise((resolve, reject) => http.close((error) => error ? reject(error) : resolve()));
  await vite?.close();
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("monkeyarch-program-options-test-"));
  await rm(cacheDir, { recursive: true, force: true });
}
