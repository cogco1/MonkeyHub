/**
 * Modeling's opening (#364), in the real ProjectWorkspace and viewer against the Hub's project
 * fixture: every runtime answer waits a fixed latency, as a Hub-forwarded request does, so the
 * time from mounting to a model on screen counts the rounds of requests made one after another.
 * It then opens another run's model and the first one again: the second opening of the same
 * model downloads no bytes, and rhino3dm's wasm is fetched and instantiated once for the app.
 * MODELING_OPEN_WEB_ROOT=<workspaces dir> measures another checkout with this same walk;
 * MODELING_OPEN_LATENCY_MS sets the latency (default 200). Timings are synthetic.
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
      if (await fixture.handle(route, bound)) { row.done = Date.now(); return; }
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
  assert.deepEqual(reopened, ["m-s1", "m-s0"], "opening the same model a second time downloads no bytes");
  assert.equal(wasm.length, 1, "rhino3dm.wasm is fetched once for the app");
  assert.equal(workers.filter((url) => url.startsWith("blob:")).length, 1, "one rhino3dm worker parses every model");
  assert.equal(workers.length, workersAfterTwo, "reopening a model starts no worker");
  console.log(`Modeling opening: ${rounds} rounds, ${result.clickToModelMs} ms to the model at ${latency} ms a request (synthetic); reopened without bytes or wasm PASS`);
  if (unexpected.length) console.log(JSON.stringify({ unserved: [...new Set(unexpected)] }));
} finally {
  await browser?.close(); await vite?.close(); await new Promise((resolve) => http.close(resolve)); await rm(cacheDir, { recursive: true, force: true });
}
