import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

// Real Connected/App components, disposable browser, generated meshes and local-only API fixtures.
const webRoot = fileURLToPath(new URL("..", import.meta.url));
const cacheDir = await mkdtemp(path.join(tmpdir(), "studio-task-sidebar-"));
const http = createHttpServer(), errors = [], intents = [], requests = [];
const projectId = "task-isolation-fixture", published = { version: 0, stateSha256: "a".repeat(64) };
const rhino = await rhino3dm();
const sources = {}, meshes = {}, artifacts = [];
for (const [id, width] of [["run-a", 1], ["run-b", 2]]) {
  const doc = new rhino.File3dm(), mesh = new rhino.Mesh();
  mesh.vertices().add(0, 0, 0); mesh.vertices().add(width, 0, 0); mesh.vertices().add(width, 1, 0); mesh.vertices().add(0, 1, 0);
  mesh.faces().addQuadFace(0, 1, 2, 3); doc.objects().add(mesh, new rhino.ObjectAttributes());
  const bytes = Buffer.from(doc.toByteArray()); doc.delete(); mesh.delete();
  const sha = createHash("sha256").update(bytes).digest("hex");
  const source = sources[id] = { runId: id, stateDigest: (id === "run-a" ? "1" : "2").repeat(64), assetSha256: sha };
  meshes[sha] = bytes;
  artifacts.push({ artifactId: sha, runId: id, modelSource: source, stageId: "test", fileName: `${id}.3dm`, relativePath: null,
    sha256: sha, sizeBytes: bytes.length, objectCount: 1, status: "registered", available: true, base: published,
    format: "3dm", representation: "composed", readbackVerified: null });
}
const reference = (id) => ({ runId: id, baseVersion: 0, baseSha256: published.stateSha256 });
const project = { projectId, projectDir: "in-memory", published, referenceRun: reference("run-a"), intentProvider: "codex", intentModel: "fixture" };
const state = (id) => ({ projectId, published, referenceRun: reference(id), referenceRunSource: "fixture", referenceReceipt: null,
  matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: "b".repeat(64), stateDigest: sources[id].stateDigest,
  activePhase: "test", counts: { entities: 0, components: 0, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
  componentTree: [], componentTreeError: null, elements: [], parameters: [], dependencyEdges: [], honesty: [], catalog: null });
let browser, vite, page, releaseA;
const gateA = new Promise((resolve) => { releaseA = resolve; });
try {
  vite = await createServer({ root: webRoot, configFile: false, cacheDir, publicDir: ".generated/public", logLevel: "silent", plugins: [react()],
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url.startsWith("/api/")) { errors.push(`escaped API: ${request.url}`); response.writeHead(500); response.end(); }
    else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await context.addInitScript(({ origin, projectId, sources }) => {
    const key = `archflow-studio.tasks.v1:${origin}`;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version: 1, activeTaskId: "task-a", tasks:
      ["a", "b"].map((id) => ({ id: `task-${id}`, projectId, title: `Task ${id.toUpperCase()}`, renamed: true,
        createdAt: 1, updatedAt: 1, archived: false, entries: [], pending: null,
        view: { draft: "", selection: null, base: { ...sources[`run-${id}`], sourceStageRef: null } } })) }));
    localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: false }));
  }, { origin, projectId, sources });
  page = await context.newPage(); page.setDefaultTimeout(20_000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const req = route.request(), url = new URL(req.url()); requests.push([req.method(), url.pathname + url.search]);
    const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (url.pathname === "/api/protocol") return json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local", capabilities: [] });
    if (url.pathname === "/api/project") return json(project);
    if (url.pathname === "/api/state") return json(state(url.searchParams.get("run") ?? "run-a"));
    if (url.pathname === "/api/artifacts") return json({ projectId, artifacts });
    const match = /^\/api\/artifacts\/([^/]+)\/bytes$/.exec(url.pathname);
    if (match) return route.fulfill({ contentType: "application/octet-stream", body: meshes[match[1]] });
    if (url.pathname === "/api/intents") {
      const body = req.postDataJSON(); intents.push(body);
      if (body.utterance === "Request A") await gateA;
      return json({ code: "UNSUPPORTED", detail: `Fixture answer for ${body.utterance}`, outcome: "UNSUPPORTED", pendingIntent: null }, 409);
    }
    errors.push(`unexpected API: ${req.method()} ${url.pathname}`); return json({ code: "TEST_UNEXPECTED", detail: url.pathname }, 404);
  });
  await page.goto(origin);
  const active = () => page.locator('[data-studio-task]:not([hidden])');
  const choose = (name) => page.locator(".task-sidebar").getByRole("button", { name: new RegExp(`^${name}`) }).click();
  const send = async (text) => {
    await active().locator(".composer input").fill(text);
    await active().getByRole("button", { name: "Propose", exact: true }).click();
  };
  await active().locator(".composer input").waitFor();
  await send("Request A");
  await page.waitForFunction(() => document.querySelector('.task-row[data-active="true"] .task-row__status')?.textContent === "Working");
  await choose("Task B"); await active().locator(".composer input").fill("B unsent draft");
  releaseA();
  await page.waitForFunction(() => document.querySelector('[data-studio-task="task-a"] .card--refusal') !== null);
  assert.equal(await active().getByText("Request A", { exact: true }).count(), 0);
  assert.equal(await active().locator(".composer input").inputValue(), "B unsent draft");
  await send("Request B"); await active().locator(".card--refusal").waitFor();
  const histories = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)).tasks,
    `archflow-studio.tasks.v1:${origin}`);
  for (const id of ["a", "b"]) {
    const own = histories.find((task) => task.id === `task-${id}`);
    assert.equal(own.entries.filter((entry) => entry.kind === "refusal")[0].error.detail, `Fixture answer for Request ${id.toUpperCase()}`);
  }
  assert.equal(intents.length, 2);
  for (const [index, runId] of ["run-a", "run-b"].entries()) {
    assert.equal(intents[index].sourceRunId, runId); assert.equal(intents[index].stateDigest, sources[runId].stateDigest);
    assert.equal("messages" in intents[index], false); assert.equal("history" in intents[index], false);
  }
  console.log("PASS delayed replies, drafts, request bases and history isolation");
  const rowB = page.locator(".task-row").filter({ has: page.getByRole("button", { name: /^Task B/ }) });
  await rowB.locator("summary").click(); await rowB.getByRole("button", { name: "Rename", exact: true }).click();
  await page.getByRole("textbox", { name: "Task name" }).fill("Bedroom study"); await page.getByRole("button", { name: "Save", exact: true }).click();
  const renamed = page.locator(".task-row").filter({ has: page.getByRole("button", { name: /^Bedroom study/ }) });
  await renamed.locator("summary").click(); await renamed.getByRole("button", { name: "Archive", exact: true }).click();
  assert.equal(await active().getAttribute("data-studio-task"), "task-a");
  await page.locator(".task-archive > summary").click();
  const archived = page.locator(".task-archive .task-row"); await archived.locator("summary").click();
  await archived.getByRole("button", { name: "Restore", exact: true }).click();
  await active().locator(".composer input").fill("Keep this draft");
  await page.reload(); await active().locator(".composer input").waitFor();
  assert.equal(await active().getAttribute("data-studio-task"), "task-b");
  assert.equal(await active().locator(".composer input").inputValue(), "Keep this draft");
  await active().getByText("Request B", { exact: true }).waitFor();
  assert.equal(intents.length, 2, "Reload never sends a model request");
  await choose("Task A"); await active().getByText("Request A", { exact: true }).waitFor();
  await page.getByRole("button", { name: "+ New task", exact: true }).click();
  await active().locator(".composer input").waitFor(); assert.equal(await active().locator(".card--terminal").count(), 0);
  assert.equal(intents.length, 2, "Creating a task never sends a model request");
  console.log("PASS rename, archive/restore, reload, fresh task without automatic inference");
  await active().locator(".composer input").fill("Unsent readiness check");
  await page.waitForFunction(() => document.querySelector('[data-studio-task]:not([hidden]) .composer button[type="submit"]')?.disabled === false);
  await active().locator(".composer input").fill("");
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    if (width === 390) await page.getByRole("button", { name: "Projects & tasks", exact: true }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
  }
  if (process.env.TASK_SIDEBAR_SCREENSHOT) {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole("button", { name: "☰", exact: true }).click();
    await page.screenshot({ path: process.env.TASK_SIDEBAR_SCREENSHOT });
    await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
    await page.waitForTimeout(250); // Allow the existing theme transition to settle before visual QA.
    await page.screenshot({ path: process.env.TASK_SIDEBAR_SCREENSHOT.replace(/\.png$/, "-dark.png") });
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ modelCalls: 0, fixtureIntents: intents.length, jsErrors: errors.length, apiRequests: requests.length }));
} catch (error) {
  console.error(error); console.error(errors);
  if (page && !page.isClosed()) console.error(await page.locator("body").innerText());
  process.exitCode = 1;
} finally {
  releaseA(); await browser?.close(); await vite?.close();
  if (http.listening) await new Promise((resolve) => http.close(resolve));
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("studio-task-sidebar-")); await rm(cacheDir, { recursive: true, force: true });
}
