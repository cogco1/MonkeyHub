/**
 * Modeling's opening (#364), in the real ProjectWorkspace and viewer against the Hub's project
 * fixture: every runtime answer waits a fixed latency, as a Hub-forwarded request does, so the
 * time from mounting to a model on screen counts the rounds of requests made one after another.
 * It then opens another run's model and the first one again: the second opening of the same
 * model downloads no bytes, and rhino3dm's wasm is fetched and instantiated once for the app.
 * MODELING_OPEN_WEB_ROOT=<workspaces dir> measures another checkout with this same walk;
 * MODELING_OPEN_LATENCY_MS sets the latency (default 200). Timings are synthetic.
 * #366 leaves the opening as it was (5 rounds, the model at about 1.5 s at 200 ms a request): the
 * opening never waits for the project store, and without the Hub's stream open the store reads nothing.
 */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { createProjectWorkspaceFixture } from "../../test/projectWorkspaceFixture.mjs";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(process.env.MODELING_OPEN_WEB_ROOT ?? fileURLToPath(new URL("..", import.meta.url)));
const latency = Number(process.env.MODELING_OPEN_LATENCY_MS ?? 200);
const cacheDir = await mkdtemp(path.join(tmpdir(), "modeling-open-"));
const runtime = { runtimeId: randomUUID(), projectId: "M", projectDir: "D:\\fixture\\M" };
const fixture = await createProjectWorkspaceFixture(new Map([[runtime.projectDir, runtime]]), []);
fixture.designTrees.set("M", { stages: ["m-s0", "m-s1"], edits: [] });
fixture.workingDrafts.set("M", { revisionSha256: null, current: null, localDraft: null, writes: [], hold: null, failure: null });
const http = createHttpServer(), errors = [], unexpected = [], log = [];
let vite, browser;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir,
    publicDir: "../.generated/public",
    plugins: [workspaceFixture(), {
      name: "modeling-open-probe", enforce: "pre",
      transform(source, id) {
        if (!id.replaceAll("\\", "/").endsWith("/viewer/ThreeDmViewport.tsx")) return;
        const marker = "  const pickAt = useCallback(";
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, "  (window as any).readRuntime = () => runtimeRef.current;\n" + marker), map: null };
      },
    }, react()],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { errors.push(`API escaped fixture: ${request.method} ${request.url}`); response.writeHead(405); response.end(); }
    else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : { channel: "chrome" }), args: ["--enable-unsafe-swiftshader", "--no-sandbox"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 }, locale: "en-US" });
  await context.addInitScript(() => {
    // Every worker the page starts, by script: rhino3dm's is a blob the loader builds once.
    const Native = window.Worker;
    window.__workers = [];
    window.Worker = class extends Native { constructor(url, options) { window.__workers.push(String(url)); super(url, options); } };
    class QuietEventSource { constructor() { this.readyState = 1; } addEventListener() {} removeEventListener() {} close() {} }
    window.EventSource = QuietEventSource;
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  page.on("pageerror", (error) => errors.push(error.message));
  const wasm = [];
  page.on("request", (request) => { if (new URL(request.url()).pathname.endsWith("/rhino3dm.wasm")) wasm.push(request.url()); });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const url = new URL(route.request().url());
    const at = Date.now();
    await sleep(latency);
    log.push({ name: url.pathname, query: url.search, at, done: null });
    const row = log.at(-1);
    const bound = new URL(`/api/runtime/projects/${runtime.runtimeId}/studio${url.pathname}${url.search}`, origin);
    try {
      // A write reaches the runtime with the Idempotency-Key the Hub's relay adds to it.
      const relayed = Object.create(route);
      relayed.request = () => { const request = route.request(), key = randomUUID();
        return Object.assign(Object.create(request), { headers: () => ({ "idempotency-key": key, ...request.headers() }) }); };
      if (await fixture.handle(relayed, bound)) { row.done = Date.now(); return; }
    } catch (error) { unexpected.push(`${route.request().method()} ${url.pathname}: ${error.message.split("\n")[0]}`); }
    row.done = Date.now();
    await route.fulfill({ status: 404, json: { code: "FIXTURE_UNSERVED", detail: `${url.pathname} is not part of this fixture.` } });
  });

  const modelShown = (runId) => page.waitForFunction((name) => [...(window.readRuntime?.()?.model?.children ?? [])]
    .some((child) => child.name === name || child.userData?.attributes?.name === name), `floor-M-${runId}`, { polling: "raf" });
  const started = Date.now();
  await page.goto(`${origin}/?lang=en`);
  const firstApi = () => log[0]?.at ?? started;
  await modelShown("m-s1");
  const shown = Date.now();
  const opening = log.filter((row) => row.at <= shown);
  // Rounds made one after another: a request asked for before the one before it answered shares its round.
  let rounds = 0, roundEnds = -Infinity;
  for (const row of [...opening].sort((a, b) => a.at - b.at)) {
    if (row.at >= roundEnds) { rounds += 1; roundEnds = row.done ?? Infinity; } else roundEnds = Math.max(roundEnds, row.done ?? Infinity);
  }
  const bytesOf = () => fixture.requests.filter((row) => row.name.endsWith("/bytes")).map((row) => row.runId);
  assert.deepEqual(bytesOf(), ["m-s1"], "the opening downloads the model it shows");

  // Another run's model, then the first one again.
  await page.evaluate(() => window.__workspaceFixture.setCandidateRunId("m-s0"));
  await modelShown("m-s0");
  const workersAfterTwo = await page.evaluate(() => window.__workers.length);
  await page.evaluate(() => window.__workspaceFixture.setCandidateRunId("m-s1"));
  await modelShown("m-s1");
  const reopened = bytesOf();
  const workers = await page.evaluate(() => window.__workers);
  const result = { latencyMs: latency, clickToModelMs: shown - firstApi(), sinceNavigationMs: shown - started, rounds,
    openingRequests: opening.map((row) => row.name + row.query), bytes: reopened, wasmFetches: wasm.length, workers, workersAfterTwo };
  console.log(JSON.stringify(result));
  assert.deepEqual(errors, []);
  assert.deepEqual(log.filter((row) => row.name === "/api/index"), [], "no Hub stream is open: the project store reads nothing yet");
  assert.deepEqual(reopened, ["m-s1", "m-s0"], "opening the same model a second time downloads no bytes");
  assert.equal(wasm.length, 1, "rhino3dm.wasm is fetched once for the app");
  assert.equal(workers.filter((url) => url.startsWith("blob:")).length, 1, "one rhino3dm worker parses every model");
  assert.equal(workers.length, workersAfterTwo, "reopening a model starts no worker");
  console.log(`Modeling opening: ${rounds} rounds, ${result.clickToModelMs} ms to the model at ${latency} ms a request (synthetic); reopened without bytes or wasm PASS`);

  // #450: a verified run on an older published version opens read-only and says why; its
  // tools refuse edits, inspection still works, and only the explicit return moves the base.
  // A broken run is still refused with the card and its Retry.
  const draft = fixture.workingDrafts.get("M"), project = fixture.projects.get("M");
  const oldPublished = project.published;
  project.published = { version: 1, stateSha256: "1".repeat(64) };
  fixture.runStates.set("M", new Map([["m-old", { baseVersion: 0, baseSha256: oldPublished.stateSha256 }],
    ["m-broken", { matchesReferenceReceipt: false }]]));
  const position = (runId) => ({ runId, sourceStageRef: null, branchId: null, updatedAt: "2026-09-25T00:00:00Z", label: null });
  draft.current = position("m-old"); draft.revisionSha256 = "fixture-stale-position";
  fixture.headsFollowDraft.add("M");
  const writes = () => fixture.requests.filter((row) => row.method !== "GET" && row.method !== "HEAD");
  const writesBefore = writes().length;
  const STALE = "The project has published v1; this design is based on v0 and cannot be edited further directly. " +
    "Choose “Return to default editing base” in the menu to continue from v1.";
  await page.goto(`${origin}/?lang=en`);
  await modelShown("m-old");
  await page.locator(".editing-base__notice", { hasText: STALE }).waitFor();
  assert.equal(await page.locator(".editing-base__notice", { hasText: STALE }).count(), 1, "the reason is said once");
  assert.equal(await page.locator(".refusal__card").count(), 0, "a stale base is not refused");
  assert.equal(await page.getByRole("button", { name: "Retry", exact: true }).count(), 0, "nothing about a stale base is transient");
  const rectangle = page.getByRole("button", { name: "Rectangle", exact: true });
  await rectangle.click();
  assert.equal(await rectangle.getAttribute("aria-pressed"), "false", "no drawing tool arms on a read-only view");
  assert.equal(await page.getByRole("button", { name: "Select", exact: true }).getAttribute("aria-pressed"), "true");
  const cameraAt = () => page.evaluate(() => window.readRuntime?.()?.camera?.position?.toArray().map((value) => value.toFixed(3)).join());
  const orbit = await cameraAt();
  const canvas = await page.locator(".viewport-canvas").first().boundingBox();
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
  await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width / 2 + 120, canvas.y + canvas.height / 2 + 40, { steps: 8 });
  await page.mouse.up();
  await page.waitForFunction((before) => window.readRuntime?.()?.camera?.position?.toArray().map((value) => value.toFixed(3)).join() !== before,
    orbit, { polling: "raf" }); // orbiting stays available
  const staleContext = await page.evaluate(() => window.__workspaceDesignContext);
  assert.equal(staleContext.unavailableReason, "readOnly");
  assert.equal(staleContext.designContext, null, "chat carries no editable design context");
  assert.deepEqual(staleContext.staleBase, { publishedVersion: 1, baseVersion: 0 });
  assert.deepEqual(writes().slice(writesBefore), [], "opening and inspecting the view writes nothing");
  assert.deepEqual(draft.current, position("m-old"), "the saved position is kept");
  const readsBefore = fixture.requests.length;
  await page.getByRole("button", { name: "Return to default editing base", exact: true }).click();
  await page.locator(".editing-base__notice", { hasText: STALE }).waitFor({ state: "detached" });
  assert.equal(draft.current?.runId, "m-s1", "only the explicit return moves the saved position, to the head Stage");
  const stateReads = fixture.requests.slice(readsBefore).filter((row) => row.name === "/api/state");
  assert.deepEqual(stateReads[0]?.query, { run: "m-s1", sourceStageRef: "project://M/runs/m-s1/review/design-stage.json" },
    "the return opens the default editing base, the head Stage");
  await page.waitForFunction(() => window.__workspaceDesignContext?.designContext !== null);
  assert.equal((await page.evaluate(() => window.__workspaceDesignContext)).unavailableReason, null, "editing is available again");
  draft.current = position("m-broken");
  await page.goto(`${origin}/?lang=en`);
  await page.locator(".refusal__card").waitFor();
  assert.equal(await page.locator(".refusal__title").innerText(), "Could not reopen the selected editing version");
  assert.equal(await page.locator(".refusal__card").getByRole("button", { name: "Retry", exact: true }).count(), 1);
  assert.deepEqual(errors, []);
  console.log("Stale published base: read-only view, reason once, continue from the default base; broken run still refused PASS");
  if (unexpected.length) console.log(JSON.stringify({ unserved: [...new Set(unexpected)] }));
} finally {
  await browser?.close(); await vite?.close(); await new Promise((resolve) => http.close(resolve)); await rm(cacheDir, { recursive: true, force: true });
}
