/** Real isolated Runtime + existing Board document editor. Manual synthetic baseline;
 * no image/model/provider response is fabricated. STUDY_EVIDENCE_DIR retains screenshots.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../../..");
const apiRoot = path.resolve(webRoot, "../../../archflow-studio/api");
const python = process.env.PYTHON ?? "python";
const pythonEnv = { ...process.env, PYTHONUTF8: "1",
  PYTHONPATH: [repoRoot, apiRoot, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
const root = await mkdtemp(path.join(tmpdir(), "monkeyboard-document-study-"));
const evidenceDir = process.env.STUDY_EVIDENCE_DIR
  ? path.resolve(process.env.STUDY_EVIDENCE_DIR)
  : await mkdtemp(path.join(tmpdir(), "monkeyboard-study-evidence-"));
await mkdir(evidenceDir, { recursive: true });
const projectDir = path.join(root, "demo-project");
const http = createHttpServer(), requests = [], errors = [];
let api, vite, browser, page, apiLog = "", closing = false, refuseSave = false;
let releaseUpload = () => {}, releaseStudyRead = () => {};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const activeImages = board => board.elements.filter(row => !row.isDeleted && row.type === "image");
async function until(read, predicate, message, attempts = 200) {
  let last;
  for (let attempt = 0; attempt < attempts; attempt++) {
    last = await read(); if (predicate(last)) return last;
    await delay(100);
  }
  assert.fail(`${message}: ${JSON.stringify(last)}`);
}

try {
  const fixture = spawnSync(python, ["-c", `
import base64, json, sys
from pathlib import Path
import archflow
from tests.support import make_empty_project
from tests.study_fixture import fixture_png, fixture_evidence, fixture_research
assert Path(archflow.__file__).resolve() == Path(sys.argv[2], "archflow/__init__.py").resolve()
make_empty_project(Path(sys.argv[1]))
print(json.dumps({"cases": [{"name": case, "png": base64.b64encode(fixture_png(case)).decode(),
    "evidence": fixture_evidence(case)} for case in ("base", "contracted", "blocked")],
    "research": fixture_research()}))
`, root, repoRoot], { cwd: apiRoot, env: pythonEnv, encoding: "utf8" });
  assert.equal(fixture.status, 0, fixture.stderr || fixture.stdout);
  const { cases, research } = JSON.parse(fixture.stdout.trim());
  const headBefore = await readFile(path.join(projectDir, "HEAD"), "utf8");
  const apiPort = await new Promise(resolve => {
    const probe = createHttpServer(); probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address(); probe.close(() => resolve(port));
    });
  });
  api = spawn(python, ["-m", "archflow_studio_api.main", "--port", String(apiPort), "--project-dir", projectDir], {
    cwd: apiRoot, env: { ...pythonEnv, ARCHFLOW_STUDIO_CAD_EXPORT: "off", ARCHFLOW_STUDIO_INTENT_PROVIDER: "deterministic" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  api.on("error", error => errors.push(error.message));
  api.stdout.on("data", chunk => { apiLog = (apiLog + chunk).slice(-10000); });
  api.stderr.on("data", chunk => { apiLog = (apiLog + chunk).slice(-10000); });
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  await until(async () => {
    assert.equal(api.exitCode, null, apiLog);
    return fetch(`${apiOrigin}/api/health`).then(response => response.ok).catch(() => false);
  }, Boolean, "Study Runtime startup failed");
  async function call(method, route, body) {
    const response = await fetch(apiOrigin + route, { method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    const result = await response.json();
    assert.ok(response.ok, `${method} ${route}: ${response.status} ${JSON.stringify(result)}`);
    return result;
  }
  const project = await call("GET", "/api/project");
  assert.equal(path.resolve(project.projectDir), path.resolve(projectDir));
  async function register(item) {
    const document = await call("POST", "/api/documents", { projectId: project.projectId,
      fileName: `synthetic-study-${item.name}.png`, mimeType: "image/png", contentBase64: item.png });
    const source = { runId: document.runId, assetSha256: document.assetSha256,
      revisionRef: document.revisionRef ?? null, pageIndex: 0 };
    const body = { projectId: project.projectId, studyId: `browser-study-${item.name}`,
      source, evidence: item.evidence, ...(item.name === "base" ? { research } : {}), expectedPreviousRef: null };
    return { document, source, body, study: await call("POST", "/api/studies", body) };
  }
  const main = await register(cases[0]);
  const currentStudy = () => call("GET", `/api/studies/${main.study.studyId}`);
  const annotationRoute = "/api/document-annotations?" + new URLSearchParams({ runId: main.source.runId,
    assetSha256: main.source.assetSha256, pageIndex: "0",
    ...(main.source.revisionRef ? { drawingRevisionRef: main.source.revisionRef } : {}) });
  const annotations = () => call("GET", annotationRoute);
  const annotationsBefore = await annotations();
  const board = () => call("GET", "/api/board");

  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error",
    publicDir: "../.generated/public", cacheDir: path.join(root, "vite-cache"),
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [react(), workspaceFixture(), {
      name: "document-study-host-appearance",
      transform(code, id) {
        if (id.replaceAll("\\", "/").endsWith("/test/workspace-fixture.tsx"))
          return `import "../../../../shared-web/src/base.css";\n${code}`;
      },
    }], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (!request.url.startsWith("/api/")) { vite.middlewares(request, response); return; }
    const entry = { method: request.method, path: request.url.split("?")[0], query: request.url };
    requests.push(entry);
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => { if (chunks.length) try { entry.body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { /* source bytes */ } });
    if (entry.method === "POST" && entry.path === "/api/studies" && refuseSave) {
      request.resume(); entry.status = 503;
      entry.result = { code: "TRANSPORT_ERROR", detail: "Injected Study save failure." };
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify(entry.result)); return;
    }
    const proxied = httpRequest({ hostname: "127.0.0.1", port: apiPort, path: request.url,
      method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, answer => {
      entry.status = answer.statusCode;
      if (answer.headers["content-type"]?.includes("application/json")) {
        const output = []; answer.on("data", chunk => output.push(chunk));
        answer.on("end", () => { try { entry.result = JSON.parse(Buffer.concat(output).toString("utf8")); } catch { /* streaming */ } });
      }
      response.writeHead(answer.statusCode, answer.headers); answer.pipe(response);
    });
    proxied.on("error", error => { if (!closing) errors.push(error.message); if (!response.headersSent) response.writeHead(502); response.end(); });
    request.pipe(proxied);
  });
  await new Promise(resolve => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1800, height: 1150 }, locale: "en-US" });
  await context.addInitScript(() => { window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`; });
  await context.route(url => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, route => {
    errors.push(`External request: ${route.request().url()}`); return route.abort("blockedbyclient");
  });
  page = await context.newPage(); page.setDefaultTimeout(30_000);
  page.on("pageerror", error => errors.push(error.message));
  const panel = () => page.getByRole("complementary", { name: "Document study", exact: true });
  const toolbar = () => page.getByRole("toolbar", { name: "Page annotation tools", exact: true });
  const step = name => panel().getByRole("navigation", { name: "Study steps" }).getByRole("button", { name: new RegExp(name) }).click();
  const saveButton = () => panel().getByRole("button", { name: "Save study & run selected changes", exact: true });
  const posts = () => requests.filter(row => row.method === "POST" && row.path === "/api/studies");
  const point = async ([u, v]) => {
    const box = await page.locator(".document-page").boundingBox();
    assert.ok(box, "The registered document page is visible");
    return [box.x + u * box.width, box.y + v * box.height];
  };
  async function openStudy() {
    await page.locator(".document-viewport[data-ready=true]").waitFor();
    assert.equal(await page.getByLabel("Page", { exact: true }).inputValue(), "0");
    if (!await panel().isVisible()) await page.getByRole("button", { name: "Study this page", exact: true }).click();
    await panel().waitFor();
    await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  }
  async function save() {
    const count = posts().length;
    await saveButton().click();
    const entry = await until(() => posts().at(count), row => row?.result,
      "The document Study did not finish its real HTTP save");
    assert.equal(entry.status, 201, JSON.stringify(entry.result));
    await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
    assert.deepEqual((await currentStudy()).research, entry.result.research, "Readback is the saved research revision");
    return entry;
  }

  await page.goto(`${origin}/?view=board&embedded=tool&lang=en`, { waitUntil: "domcontentloaded" });
  await page.locator(".monkeyboard-initializing").waitFor({ state: "hidden" });
  await page.locator(".monkeyboard-canvas canvas").first().waitFor();
  const placed = await until(board, value => activeImages(value).length === 1, "The source image is not on Board");
  const boardBox = await page.locator(".monkeyboard-canvas").boundingBox();
  let centre;
  for (const across of [0.5, 0.42, 0.58, 0.34, 0.66]) {
    centre = [boardBox.x + boardBox.width * across, boardBox.y + boardBox.height / 2];
    await page.mouse.click(...centre);
    if (/synthetic-study-base\.png/.test(await page.locator(".monkeyboard-footer").innerText())) break;
  }
  await page.locator(".monkeyboard-context").waitFor();
  const zoom = await page.locator(".excalidraw .reset-zoom-button").innerText();
  await page.evaluate(() => { window.__studyBoardCanvas = document.querySelector(".monkeyboard-canvas canvas"); });
  await page.mouse.dblclick(...centre); await openStudy();
  console.log("study: registered source opened from the same Board document editor");

  // A fifth trace is genuinely drawn, classified, confirmed and corrected in the page UI.
  await step("Evidence");
  await toolbar().getByRole("button", { name: "Outline", exact: true }).click();
  for (const vertex of [[0.2, 0.2], [0.3, 0.2], [0.3, 0.3], [0.2, 0.3]]) await page.mouse.click(...await point(vertex));
  await toolbar().getByRole("button", { name: "Close outline", exact: true }).click();
  await until(() => panel().getByLabel("Geometric classification", { exact: true }).count(), value => value === 5,
    "The new closed contour did not enter Study evidence");
  await panel().getByLabel("Geometric classification", { exact: true }).nth(4).selectOption("floor_plate");
  await panel().getByLabel("Evidence decision", { exact: true }).nth(4).selectOption("confirmed");
  const initialIds = new Set(cases[0].evidence.map(row => row.evidenceId));
  const traceId = await page.locator(".document-page__ink [data-stroke-id]").evaluateAll((nodes, ids) =>
    nodes.map(node => node.getAttribute("data-stroke-id")).find(id => !ids.includes(id)), [...initialIds]);
  assert.ok(traceId, "A new trace retains its own identity");
  const tracePath = () => page.locator(`.document-page__ink [data-stroke-id="${traceId}"]`).getAttribute("d");
  const beforeDrag = await tracePath();
  await toolbar().getByRole("button", { name: "Edit line", exact: true }).click();
  await page.mouse.move(...await point([0.3, 0.2])); await page.mouse.down();
  await page.mouse.move(...await point([0.32, 0.18]), { steps: 12 }); await page.mouse.up();
  let correctedPath = await until(tracePath, value => value !== beforeDrag, "The vertex correction did not change the Study contour");
  assert.equal(await panel().getByLabel("Evidence decision", { exact: true }).nth(4).inputValue(), "proposed",
    "Correcting geometry requires confirmation again");
  await toolbar().getByRole("button", { name: "Undo", exact: true }).click();
  assert.equal(await tracePath(), beforeDrag, "Study undo restores the original contour");
  await toolbar().getByRole("button", { name: "Redo", exact: true }).click();
  assert.equal(await tracePath(), correctedPath, "Study redo restores the correction");
  await panel().getByLabel("Evidence decision", { exact: true }).nth(4).selectOption("confirmed");

  // Edit all reasoning categories through ordinary form controls, not React state injection.
  const question = "Does geometric continuity survive the declared edit, and what use evidence is still missing?";
  const claim = "A connected clear interval can support a route only after ground level and endpoint access are verified.";
  const gap = "No section or access record establishes a passable ground surface; the drawing has no scale.";
  await step("Explanations");
  await panel().getByLabel("Research question", { exact: true }).fill(question);
  await panel().getByLabel("Challengeable claim", { exact: true }).nth(0).fill(claim);
  await panel().getByLabel("Challengeable claim", { exact: true }).nth(1).fill("The same interval may instead separate planting or drainage from occupied volumes; function remains unknown.");
  await panel().getByLabel("Gap 1", { exact: true }).fill(gap);
  await step("Counterfactuals");
  const prediction = "Moving the right mass 0.05 left should remove 0.04 of the traced void area while preserving one connected remainder.";
  await panel().getByLabel("Horizontal shift / page width", { exact: true }).nth(0).fill("-0.05");
  await panel().getByLabel("Prediction before execution", { exact: true }).nth(0).fill(prediction);
  await panel().getByLabel("Run this change on save", { exact: true }).nth(0).check();
  await step("Pattern & prior");
  await panel().getByLabel("Pattern name", { exact: true }).fill("Conditional clear interval — manually revised");
  await panel().getByLabel("Relationship / mechanism", { exact: true }).fill("Test connected unobstructed space separately from contact with two flanking masses.");
  await panel().getByLabel("Prior to test in design", { exact: true }).fill("Transfer a required connection only when its surface and endpoints are independently supported; preserve no silhouette by default.");
  await panel().getByRole("button", { name: "Change applicability conditions", exact: true }).click();
  await panel().getByLabel("Changed conditions", { exact: true }).fill("The interval is a non-passable planting strip.\nThe brief does not require through-access.");
  await panel().getByLabel("Decision under changed conditions", { exact: true }).selectOption("revise");
  await panel().getByLabel("Reason tied to evidence / experiment", { exact: true }).fill("The source geometry remains connected but the conditions for a circulation recommendation are absent.");
  const revisedPrior = "Retain separation geometry as evidence and withdraw the circulation recommendation until the brief or access evidence changes.";
  await panel().getByLabel("Revised prior", { exact: true }).fill(revisedPrior);
  assert.equal(await panel().getByLabel("Human preference status", { exact: true }).inputValue(), "unresolved");
  const saved = await save();
  const retained = saved.result;
  assert.equal(retained.research.question, question);
  assert.equal(retained.research.hypotheses[0].statement, claim);
  assert.equal(retained.research.gaps[0].description, gap);
  assert.equal(retained.research.counterfactuals[0].prediction, prediction);
  assert.equal(retained.research.counterfactuals.length, 4);
  assert.ok(retained.research.counterfactuals.every(row => row.actual?.status === "computed"));
  assert.equal(retained.research.designPrior.changedContext.revisedStatement, revisedPrior);
  assert.equal(retained.research.designPrior.preferenceStatus, "unresolved");
  assert.equal(retained.evidence.find(row => row.evidenceId === traceId).kind, "floor_plate");
  assert.equal(retained.evidence.find(row => row.evidenceId === traceId).status, "confirmed");
  const correctedVertex = retained.evidence.find(row => row.evidenceId === traceId).geometry.points[1];
  assert.ok(Math.abs(correctedVertex[0] - 0.32) < 0.002 && Math.abs(correctedVertex[1] - 0.18) < 0.002,
    "The saved polygon retains the manually moved vertex after server coordinate normalization");
  correctedPath = await tracePath();
  assert.deepEqual(await annotations(), annotationsBefore);
  await page.screenshot({ path: path.join(evidenceDir, "study-saved-prior.png") });
  await panel().getByRole("button", { name: "Reopen saved", exact: true }).click();
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  await step("Explanations");
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), question);
  await step("Counterfactuals");
  assert.equal(await panel().getByText("Saved actual result", { exact: true }).count(), 4);
  await page.screenshot({ path: path.join(evidenceDir, "study-counterfactuals.png") });

  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  await page.locator(".monkeyboard-context").waitFor();
  assert.equal(await page.evaluate(() => window.__studyBoardCanvas === document.querySelector(".monkeyboard-canvas canvas")), true);
  assert.equal(await page.locator(".excalidraw .reset-zoom-button").innerText(), zoom);
  assert.match(await page.locator(".monkeyboard-footer").innerText(), /synthetic-study-base\.png/);
  assert.deepEqual(activeImages(await board()).map(row => row.customData.sourceDocument),
    activeImages(placed).map(row => row.customData.sourceDocument));
  await page.getByRole("button", { name: "Edit this page", exact: true }).click(); await openStudy();
  assert.equal(await tracePath(), correctedPath, "Returning through Board retains the corrected Study geometry");
  console.log("study: UI correction, all reasoning fields, save/reopen and original Board context verified");

  // Two further exact sources are independently registered; their seeded studies only feed Compare.
  const alternatives = [];
  for (const item of cases.slice(1)) alternatives.push(await register(item));
  await panel().getByRole("button", { name: "Reopen saved", exact: true }).click();
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  await step("Compare");
  let compared = retained;
  for (const alternative of alternatives) {
    await panel().getByLabel("Comparison study", { exact: true }).selectOption(`${alternative.study.studyId}:${alternative.study.ledgerRef}`);
    const before = compared;
    const count = posts().length;
    await panel().getByRole("button", { name: "Compare saved revisions", exact: true }).click();
    const comparison = await until(() => posts().at(count),
      row => row?.result, "The comparison was not archived with the research");
    assert.equal(comparison.status, 201, JSON.stringify(comparison.result));
    assert.deepEqual(comparison.body.research.comparisons.at(-1).studies, [
      { studyId: before.studyId, ledgerRef: before.ledgerRef },
      { studyId: alternative.study.studyId, ledgerRef: alternative.study.ledgerRef },
    ]);
    compared = comparison.result;
    await panel().getByText("Comparison result", { exact: true }).waitFor();
    await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  }
  assert.equal(compared.research.comparisonResults.length, 2);
  await panel().getByRole("button", { name: "Reopen saved", exact: true }).click();
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  await panel().getByRole("heading", { name: "Archived comparison 1", exact: true }).waitFor();
  await panel().getByRole("heading", { name: "Archived comparison 2", exact: true }).waitFor();
  assert.deepEqual((await currentStudy()).research.comparisonResults, compared.research.comparisonResults,
    "Reopening must replay both exact comparison results");
  await page.screenshot({ path: path.join(evidenceDir, "study-compare.png") });

  await step("Explanations");
  const draft503 = `${question} [unsaved transport retry]`;
  await panel().getByLabel("Research question", { exact: true }).fill(draft503);
  refuseSave = true;
  await saveButton().click();
  await panel().getByRole("alert").filter({ hasText: "Injected Study save failure" }).waitFor();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), draft503);
  assert.equal((await currentStudy()).ledgerRef, compared.ledgerRef);
  assert.equal(await tracePath(), correctedPath);
  await panel().getByRole("button", { name: "Retry reading saved study", exact: true }).click();
  await panel().getByRole("group", { name: "Discard draft confirmation", exact: true }).waitFor();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), draft503,
    "Reading after a failed save must ask before replacing the draft");
  await panel().getByRole("button", { name: "Keep editing", exact: true }).click();
  refuseSave = false;
  const retried = await save();

  // The 409 comes from a real competing saved revision, not a fabricated HTTP response.
  const concurrent = await call("POST", "/api/studies", { ...retried.body,
    expectedPreviousRef: retried.result.ledgerRef,
    research: { ...retried.body.research, question: "Concurrent editor revision kept by the runtime." } });
  const draft409 = `${question} [local correction after another editor saved]`;
  await panel().getByLabel("Research question", { exact: true }).fill(draft409);
  const beforeStale = posts().length;
  await saveButton().click();
  const stale = await until(() => posts().at(beforeStale), row => row?.result, "No stale-write response arrived");
  assert.equal(stale.status, 409, JSON.stringify(stale.result));
  assert.equal(stale.result.code, "STUDY_REVISION_STALE");
  await panel().getByRole("alert").waitFor();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), draft409);
  assert.equal(await tracePath(), correctedPath);
  assert.equal((await currentStudy()).ledgerRef, concurrent.ledgerRef, "A refused stale save preserves the competing revision");
  assert.deepEqual(await annotations(), annotationsBefore, "Study evidence never writes ordinary document annotation ink");
  assert.equal(requests.filter(row => row.method === "PUT" && row.path === "/api/document-annotations").length, 0);
  assert.equal(await readFile(path.join(projectDir, "HEAD"), "utf8"), headBefore, "Study cannot issue design HEAD");
  assert.equal(requests.filter(row => row.method === "POST" && (row.path === "/api/intents" || row.path.endsWith("/propose"))).length, 0,
    "The manual browser test cannot claim a model/provider call");
  assert.equal(context.pages().length, 1);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(evidenceDir, "study-stale-draft-retained.png") });
  await panel().getByRole("button", { name: "Discard unsaved draft and reopen…", exact: true }).click();
  await panel().getByRole("button", { name: "Keep editing", exact: true }).click();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), draft409,
    "Cancelling explicit discard keeps the refused draft");
  await panel().getByRole("button", { name: "Discard unsaved draft and reopen…", exact: true }).click();
  await panel().getByRole("button", { name: "Discard draft and reopen", exact: true }).click();
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), concurrent.research.question,
    "Only explicit discard replaces a stale draft with the latest retained revision");

  // Hold the actual document upload at the HTTP boundary. Study edits made before
  // opening it must save, and the upload must prevent further editing or navigation.
  const sourceSelect = () => page.getByRole("combobox", { name: "Source document", exact: true });
  const mainSelection = await sourceSelect().inputValue();
  const uploadQuestion = `${concurrent.research.question} [saved before upload]`;
  await panel().getByLabel("Research question", { exact: true }).fill(uploadQuestion);
  const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
  let uploadHeld = false;
  const holdUpload = async route => {
    if (route.request().method() === "POST") { uploadHeld = true; await uploadGate; }
    await route.continue();
  };
  await page.route("**/api/documents", holdUpload);
  await page.locator('.document-header input[type="file"]').setInputFiles({ name: alternatives[0].document.fileName,
    mimeType: "image/png", buffer: Buffer.from(cases[1].png, "base64") });
  await until(() => uploadHeld, Boolean, "The real document upload did not reach the delayed boundary");
  assert.equal((await currentStudy()).research.question, uploadQuestion, "Opening a source first saves the current Study draft");
  assert.equal(await panel().getByLabel("Research question", { exact: true }).isDisabled(), true,
    "Study inputs must be locked while the source upload is pending");
  assert.equal(await toolbar().getByRole("button", { name: "Outline", exact: true }).isDisabled(), true);
  assert.equal(await toolbar().getByRole("button", { name: "Undo", exact: true }).isDisabled(), true);
  assert.equal(await sourceSelect().isDisabled(), true, "A pending upload must prevent source switches");
  assert.equal(await page.getByRole("combobox", { name: "Page", exact: true }).isDisabled(), true,
    "A pending upload must prevent page switches");
  assert.equal(await page.getByRole("button", { name: "Return to annotations", exact: true }).isDisabled(), true);
  assert.equal(await sourceSelect().inputValue(), mainSelection);
  assert.equal(await tracePath(), correctedPath, "The existing source remains visible while its replacement uploads");
  await page.getByRole("button", { name: "MonkeyBoard · Board", exact: true }).click();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  assert.equal(await panel().isVisible(), true, "Leaving during the upload cannot hide an unfinished Study operation");
  assert.equal(await page.locator(".monkeyboard-context").isVisible(), false);
  releaseUpload();
  await until(() => sourceSelect().inputValue(), value => value.includes(alternatives[0].source.assetSha256),
    "The completed real upload did not select its registered source");
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  await page.unroute("**/api/documents", holdUpload);
  assert.equal(await page.locator(`.document-page__ink [data-stroke-id="${traceId}"]`).count(), 0);
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), "",
    "The newly selected registered source cannot inherit the old source's question");
  assert.equal((await currentStudy()).research.question, uploadQuestion);

  // Give this second registered source distinct research, then hold and refuse
  // the read after switching back. No old-source geometry or question may show.
  const otherQuestion = "This question belongs only to the contracted source.";
  await panel().getByLabel("Research question", { exact: true }).fill(otherQuestion);
  const otherSaveCount = posts().length;
  await saveButton().click();
  const otherSaved = await until(() => posts().at(otherSaveCount), row => row?.result, "The second source research did not save");
  assert.equal(otherSaved.status, 201, JSON.stringify(otherSaved.result));
  assert.equal(otherSaved.result.source.assetSha256, alternatives[0].source.assetSha256);
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  const readGate = new Promise(resolve => { releaseStudyRead = resolve; });
  let readHeld = false;
  const holdStudyRead = async route => {
    if (route.request().method() !== "GET") { await route.continue(); return; }
    readHeld = true; await readGate;
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ code: "TRANSPORT_ERROR", detail: "Injected Study read failure after source switch." }) });
  };
  await page.route("**/api/studies", holdStudyRead);
  await sourceSelect().selectOption(mainSelection);
  await until(() => readHeld, Boolean, "Switching the document did not request its Study");
  await panel().getByText("Loading saved study…", { exact: true }).waitFor();
  assert.equal(await page.locator(".document-page__ink [data-stroke-id]").count(), 0,
    "A new source must not display the previous source's evidence while its Study read is pending");
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), "");
  assert.equal(await panel().getByLabel("Challengeable claim", { exact: true }).count(), 0);
  assert.equal(await panel().getByLabel("Research question", { exact: true }).isDisabled(), true);
  releaseStudyRead();
  await panel().getByRole("alert").filter({ hasText: "Injected Study read failure after source switch" }).waitFor();
  assert.equal(await page.locator(".document-page__ink [data-stroke-id]").count(), 0,
    "A refused source read must not reveal stale geometry from a different page");
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), "");
  await page.unroute("**/api/studies", holdStudyRead);
  await panel().getByRole("button", { name: "Retry reading saved study", exact: true }).click();
  await panel().getByText("Saved · can reopen", { exact: true }).waitFor();
  assert.equal(await panel().getByLabel("Research question", { exact: true }).inputValue(), uploadQuestion,
    "Retrying must restore only the selected source's saved research");
  assert.equal(await tracePath(), correctedPath);
  assert.equal((await call("GET", `/api/studies/${alternatives[0].study.studyId}`)).research.question, otherQuestion);
  assert.deepEqual(await annotations(), annotationsBefore);
  assert.equal(await readFile(path.join(projectDir, "HEAD"), "utf8"), headBefore);
  assert.deepEqual(errors, []);
  await page.screenshot({ path: path.join(evidenceDir, "study-source-recovery.png") });
  console.log("study: pending upload locks edits/navigation; delayed and failed new-source reads hide old evidence and recover exactly");
  const summary = { passed: "Board exact source; same-page Study trace draw/classify/confirm/drag/undo/redo; edited research, 4 actual counterfactuals, changed-context prior; save/reopen; 2 exact comparison sources; original Board context; 503 retry and real 409 retain draft; pending upload locks edits/navigation; delayed/503 new-source reads hide old research and retry exact source; annotations and HEAD unchanged",
    source: main.source.assetSha256, savedLedgerRef: retained.ledgerRef,
    comparisonStudyIds: alternatives.map(row => row.study.studyId), evidenceDir };
  await writeFile(path.join(evidenceDir, "result.json"), JSON.stringify(summary, null, 2) + "\n");
  console.log(JSON.stringify(summary));
} catch (error) {
  console.error(`FAILED: ${error?.stack ?? error}`);
  if (page && !page.isClosed()) {
    await page.screenshot({ path: path.join(evidenceDir, "study-failure.png") });
    console.error(await page.locator("body").innerText().catch(() => ""));
  }
  console.error(JSON.stringify({ evidenceDir, errors, requests: requests.map(({ method, path: route, status, result }) =>
    ({ method, route, status, ...(status >= 400 ? { result } : {}) })), apiLog }));
  throw error;
} finally {
  closing = true;
  releaseUpload(); releaseStudyRead();
  await browser?.close(); await vite?.close();
  if (http.listening) await new Promise(resolve => http.close(resolve));
  if (api && api.exitCode === null) { const exited = new Promise(resolve => api.once("exit", resolve)); api.kill(); await exited; }
  assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
  assert.ok(path.basename(root).startsWith("monkeyboard-document-study-"));
  await rm(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
}
