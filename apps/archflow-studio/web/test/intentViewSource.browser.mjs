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

// The actual App and viewport load two small, generated 3dm files. Every API
// response and selection write lives in this process; no project/service is
// read. A and B deliberately share run/state identity but have different bytes.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const forbiddenPorts = new Set(["5187", "5188", "60617", "60616", "53621"]);
const runId = "same-run-two-assets";
const projectId = "intent-view-source-fixture";
const stateDigest = "1".repeat(64);
const published = { version: 0, stateSha256: "2".repeat(64) };
const referenceRun = { runId, baseVersion: published.version, baseSha256: published.stateSha256 };
const project = { projectId, projectDir: "in-memory fixture", published, referenceRun,
  intentProvider: "codex", intentModel: "fixture" };
const element = { componentId: "fixture-room", elementId: "fixture-floor", producer: "floor",
  numericFields: { height: 0.15 } };
const state = { projectId, published, referenceRun, referenceRunSource: "fixture", referenceReceipt: null,
  matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: "3".repeat(64), stateDigest,
  activePhase: "stage-2", counts: { entities: 1, components: 1, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
  componentTree: [{ componentId: element.componentId, parentComponentId: null, semanticKind: "room", intent: "fixture", maturity: "candidate", revision: 1 }],
  componentTreeError: null, elements: [element], parameters: [], dependencyEdges: [], honesty: [], catalog: {
    components: [{ componentId: element.componentId, parentId: null, children: [], elementIds: [element.elementId],
      descendantElementIds: [element.elementId], capabilityCount: 1, states: ["editable"], objectCount: 1, unboundObjectCount: 0, closure: [] }],
    elements: [{ ...element, capabilities: [{ capabilityId: "fixture-height", elementId: element.elementId, key: "height",
      value: 0.15, valueType: "number", unit: null, bounds: null, source: "authored", confidence: 1, status: "editable", validatorRefs: [] }],
      objectNames: [element.elementId] }],
    objects: [{ name: element.elementId, componentId: element.componentId, producerOp: "floor", elementId: element.elementId, status: "bound", detail: "fixture" }],
    coverage: { objects: 1, bound: 1, unbound: 0, ambiguous: 0, unknownComponent: 0 }, inspectionRun: runId, honesty: [],
  } };
const protocol = { protocol: "archflow/2", server: "in-memory fixture", serverVersion: "test", mode: "local",
  capabilities: ["working-copies", "model-annotations", "document-visual-input"] };
const rhino = await rhino3dm();
function modelBytes(width) {
  const document = new rhino.File3dm();
  const mesh = new rhino.Mesh();
  mesh.vertices().add(0, 0, 0); mesh.vertices().add(width, 0, 0);
  mesh.vertices().add(width, 1, 0); mesh.vertices().add(0, 1, 0);
  mesh.faces().addQuadFace(0, 1, 2, 3);
  const attributes = new rhino.ObjectAttributes(); attributes.name = element.elementId;
  document.objects().add(mesh, attributes);
  const bytes = Buffer.from(document.toByteArray());
  document.delete(); mesh.delete(); attributes.delete();
  return bytes;
}
const bytesA = modelBytes(1), bytesB = modelBytes(1.8), bytesC = modelBytes(2.6);
const sourceA = { runId, stateDigest, assetSha256: createHash("sha256").update(bytesA).digest("hex") };
const sourceB = { runId, stateDigest, assetSha256: createHash("sha256").update(bytesB).digest("hex") };
const sourceC = { runId: "cross-run-C", stateDigest: "c".repeat(64), assetSha256: createHash("sha256").update(bytesC).digest("hex") };
const stateC = { ...structuredClone(state), referenceRun: { ...referenceRun, runId: sourceC.runId }, stateDigest: sourceC.stateDigest,
  recordDigest: "b".repeat(64), catalog: { ...structuredClone(state.catalog), inspectionRun: sourceC.runId } };
assert.notEqual(sourceA.assetSha256, sourceB.assetSha256);
assert.notEqual(sourceC.runId, sourceB.runId);
const optionA = { id: "A", label: "Option A", modelSource: sourceA };
const optionB = { id: "B", label: "Option B", modelSource: sourceB };
let group = { projectId, groupId: "same-run-options", label: "Two model files", stageId: "stage-2", commonBase: sourceA,
  scope: [element.componentId], options: [optionA, optionB], selectedOptionId: "A", revisionSha256: "4".repeat(64) };
const artifact = (source, fileName, bytes) => ({ artifactId: source.assetSha256, runId: source.runId, modelSource: source,
  stageId: "stage-2", fileName, relativePath: null, sha256: source.assetSha256, sizeBytes: bytes.length,
  objectCount: 1, status: "registered", readbackVerified: null, available: true, unavailableReason: null,
  base: published, branchId: "fixture", branchEpoch: 0, programRef: null, programDigest: null,
  designStateDigest: source.stateDigest, receiptRef: null, format: "3dm", representation: "composed" });
const artifacts = { projectId, artifacts: [artifact(sourceA, "option-A.3dm", bytesA), artifact(sourceB, "option-B.3dm", bytesB), artifact(sourceC, "option-C.3dm", bytesC)] };
const models = new Map([[sourceA.assetSha256, bytesA], [sourceB.assetSha256, bytesB], [sourceC.assetSha256, bytesC]]);
const documentSha = "d".repeat(64), referenceSha = "e".repeat(64);
const pageInfo = { pageIndex: 0, width: 320, height: 240, rotation: 0 };
const sourceDocument = (sha, fileName, modelSource) => ({ projectId, runId, assetSha256: sha, fileName,
  mimeType: "image/png", sizeBytes: 1, pageCount: 1, pages: [pageInfo], modelSource, modelSourceBindingRef: "fixture-binding" });
const documents = [sourceDocument(documentSha, "edit-page.png", sourceB), sourceDocument(referenceSha, "reference-page.png", null)];
let documentPage = { projectId, runId, assetSha256: documentSha, pageIndex: 0, revisionSha256: "5".repeat(64),
  annotations: [{ id: "saved-line", kind: "line", points: [[0.15, 0.4], [0.75, 0.4]], color: "#2f80ed", lineWidth: 0.01 }],
  comment: "Revise the marked part" };
const referencePage = { projectId, runId, assetSha256: referenceSha, pageIndex: 0, revisionSha256: null, annotations: [], comment: "" };
let drawingBytes, referenceBytes, pixelAudit;
const documentToken = "fixture-document-continuation";
const comments = [];
const errors = [], requests = [], intents = [], selections = [], escapedApiRequests = [];
const passed = [];
let observationTransforms = 0, heldIntent = null, heldDocument = null, phase = "setup", vite, browser, page;
const http = createHttpServer();
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-intent-view-test-"));
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
async function until(read, accepts, message, timeout = 30_000) {
  const deadline = Date.now() + timeout; let value;
  do {
    assert.deepEqual(errors, [], `Unexpected browser/API error during ${phase}`);
    value = await read(); if (accepts(value)) return value;
    await sleep(60);
  } while (Date.now() < deadline);
  assert.fail(`${message}: ${JSON.stringify(value)}`);
}
async function step(name, action) { phase = name; await action(); passed.push(name); console.log(`PASS ${name}`); }
function versionButton(option) {
  return page.locator(`[data-working-copy="${group.groupId}"]`).getByRole("button", { name: new RegExp(`^View ${option.label}`), includeHidden: true });
}
async function openVersions() {
  const button = page.locator(".stage__versions-toggle");
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
}
async function openConversation() {
  const button = page.locator('button[aria-controls="conversation-panel"]');
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
}
async function ready(option) {
  await openVersions();
  await until(async () => ({ selected: await versionButton(option).getAttribute("aria-pressed"),
    annotations: await page.locator("[data-model-annotations-status]").getAttribute("data-model-annotations-status"),
    loading: await page.locator(".stage-model .source__status").count(),
    name: await page.locator(".source__name").textContent(),
  }), (value) => value.selected === "true" && value.annotations === "saved" && value.loading === 0 &&
    value.name?.includes(`option-${option.id}.3dm`), `${option.id}'s actual model did not finish loading`, 60_000);
}
async function view(option) {
  await openVersions();
  if (await versionButton(option).getAttribute("aria-pressed") !== "true") await versionButton(option).click();
  await ready(option);
}
async function viewState() {
  return { source: await page.locator(".stage-model .source").textContent(),
    state: await page.locator(".stage-model .source").getAttribute("data-state"),
    editing: await page.locator(".stage-model .editing-base").textContent(),
    match: await page.locator(".stage-model .editing-base").getAttribute("data-source-match"),
    selected: group.selectedOptionId,
    A: await versionButton(optionA).getAttribute("aria-pressed"), B: await versionButton(optionB).getAttribute("aria-pressed"),
    targets: await page.locator(".composer > .context .pill--accent").allTextContents() };
}
function pendingIntent(utterance, clarification) {
  return { requestId: `fixture-${intents.length}`, stateDigest, originalUtterance: utterance,
    actionKind: "change_existing_value", targetComponentId: element.componentId, elementId: element.elementId,
    requestedSemanticProperty: "height", knownSlots: { property: "height" }, missingSlots: clarification ? ["value"] : [],
    candidates: [], scopeOptions: [], rejectedCandidates: [], reasonCode: clarification ? "MISSING_VALUE" : "COMPILED",
    continuationToken: clarification ? "fixture-continuation" : null, turn: 1 };
}
function compiledResponse(utterance, documentCommentRef = null, modelSource = sourceB) {
  return { outcome: "COMPILED", agent: { provider: "deterministic", model: null, compiledUtterance: "set height to 0.2",
    why: "", latencyMs: 0, promptSha256: null, receiptId: null, status: null },
    proposal: { proposalId: `fixture-proposal-${intents.length}`, status: "proposed", modelSource, sourceRunId: modelSource.runId,
      baseStateDigest: stateDigest, recordDigest: state.recordDigest,
      target: { componentId: element.componentId, elementId: element.elementId, ref: `entity:${element.elementId}`, key: "height" },
      change: { kind: "set_scalar", old: element.numericFields.height, new: 0.2, unit: null }, protected: [], decisionOperator: null,
      impact: { direct: [], propagated: [], protected: [], conflicts: [], locks: [], unknownCoverage: { count: 0, componentIds: [] }, honesty: [] },
      utterance, persistence: "in-memory fixture", createdAt: "2026-09-09T00:00:00Z", scope: null },
    timings: { compileMs: 0, typeMs: 0 }, documentCommentRef, gestures: [], pendingIntent: pendingIntent(utterance, false) };
}
async function sendText(utterance, clarification = false, expectedToken = null) {
  await openConversation();
  const held = { utterance, clarification, expectedToken, requested: deferred(), release: deferred() };
  heldIntent = held;
  await page.locator(".composer input").fill(utterance);
  await page.locator(".composer button[type=submit]").click();
  await Promise.race([held.requested.promise, sleep(12_000).then(() => assert.fail("The intent was not intercepted"))]);
  return held;
}

try {
  vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [{ name: "observe-actual-viewport-methods", enforce: "pre",
      transform(source, id) {
        const modulePath = id.split("?")[0].replaceAll("\\", "/");
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/features/stage/documentVisualInput.ts`) {
          const marker = "  if (!Number.isFinite(maxEdge)";
          assert.equal(source.split(marker).length, 2, "Observe the real document renderer once");
          return { code: source.replace(marker, '(window as unknown as { __documentVisualRenders: number }).__documentVisualRenders++;\n' + marker), map: null };
        }
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) {
          const marker = "  const gestures = modelAnnotations.annotations;";
          assert.equal(source.split(marker).length, 2, "Observe the actual App annotation snapshot once");
          return { code: source.replace(marker, marker + '\n(window as unknown as { __modelInkSnapshot: unknown }).__modelInkSnapshot = structuredClone(gestures);'), map: null };
        }
        if (modulePath !== `${webRoot.replaceAll("\\", "/")}/src/viewer/ThreeDmViewport.tsx`) return;
        for (const [name, entry] of [["ghost", "(spec: GhostSpec | null) => {"], ["highlight", "(target: HighlightRequest): number => {"]]) {
          assert.equal(source.split(entry).length, 2);
          source = source.replace(entry, `${entry}\n(window as unknown as { __intentViewCalls: string[] }).__intentViewCalls.push(${JSON.stringify(name)});`);
        }
        observationTransforms++; return { code: source, map: null };
      },
    }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) {
      escapedApiRequests.push(`${request.method} ${request.url}`); response.writeHead(405); response.end("All API requests must be intercepted in memory"); return;
    }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const port = String(http.address().port); assert.ok(!forbiddenPorts.has(port));
  const uiOrigin = `http://127.0.0.1:${port}`;
  const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
    "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  const preferenceKey = "archflow-studio.user-preferences";
  await context.addInitScript(({ key, projectId, runId }) => {
    window.__intentViewCalls = []; window.__documentVisualRenders = 0;
    if (!localStorage.getItem(key)) localStorage.setItem(key, JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: false, editingBases: { [JSON.stringify(["", projectId])]: runId } }));
  }, { key: preferenceKey, projectId, runId });
  page = await context.newPage(); page.setDefaultTimeout(15_000);
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    requests.push({ method, path: url.pathname, query: url.search });
    try {
      assert.equal(url.origin, uiOrigin, "The test must never contact an external API");
      const json = async (value, status = 200) => route.fulfill({ status, json: value });
      if (method === "GET") {
        if (url.pathname === "/api/protocol") return await json(protocol);
        if (url.pathname === "/api/project") return await json(project);
        if (url.pathname === "/api/working-copies") return await json({ workingCopies: [group] });
        if (url.pathname === "/api/state") {
          const requestedRun = url.searchParams.get("run");
          assert.ok(!requestedRun || [runId, sourceC.runId].includes(requestedRun));
          return await json(requestedRun === sourceC.runId ? stateC : state);
        }
        if (url.pathname === "/api/artifacts") return await json(artifacts);
        const model = /^\/api\/artifacts\/([^/]+)\/bytes$/.exec(url.pathname);
        if (model) { assert.ok(models.has(model[1])); return await route.fulfill({ status: 200, contentType: "application/octet-stream", body: models.get(model[1]) }); }
        if (url.pathname === "/api/model-annotations") {
          const modelSource = { runId: url.searchParams.get("runId"), stateDigest: url.searchParams.get("stateDigest"), assetSha256: url.searchParams.get("assetSha256") };
          assert.ok([sourceA, sourceB, sourceC].some((source) => JSON.stringify(source) === JSON.stringify(modelSource)));
          return await json({ projectId, modelSource, revisionSha256: "6".repeat(64), annotations: [], comment: "" });
        }
        if (url.pathname === "/api/documents") return await json({ projectId, runId, documents });
        if (url.pathname === "/api/document-annotations") {
          const row = url.searchParams.get("assetSha256") === documentSha ? documentPage : referencePage;
          return await json(row);
        }
        if (/^\/api\/documents\/[^/]+\/bytes$/.test(url.pathname)) {
          const bytes = url.pathname.includes(referenceSha) ? referenceBytes : drawingBytes;
          assert.ok(bytes); return await route.fulfill({ status: 200, contentType: "image/png", body: bytes });
        }
        if (url.pathname === "/api/document-comments") return await json({ comments });
      }
      if (method === "PUT" && url.pathname === `/api/working-copies/${group.groupId}/selection`) {
        const body = request.postDataJSON();
        assert.deepEqual(body, { projectId, baseRevisionSha256: group.revisionSha256, optionId: selections.length === 0 ? "B" : "A" });
        selections.push(body); group = { ...group, selectedOptionId: body.optionId, revisionSha256: String(selections.length + 10).padStart(64, "0") };
        return await json(group);
      }
      if (method === "PUT" && url.pathname === "/api/document-annotations") {
        const body = request.postDataJSON();
        assert.equal(body.projectId, projectId); assert.equal(body.runId, runId); assert.equal(body.assetSha256, documentSha);
        assert.equal(body.baseRevisionSha256, documentPage.revisionSha256);
        documentPage = { ...body, revisionSha256: "7".repeat(64) }; delete documentPage.baseRevisionSha256;
        return await json(documentPage);
      }
      if (method === "POST" && url.pathname === "/api/intents") {
        const body = request.postDataJSON(); intents.push(body);
        assert.equal(body.projectId, projectId); assert.equal(body.sourceRunId, runId); assert.equal(body.stateDigest, stateDigest);
        const expectedSource = group.selectedOptionId === "A" ? sourceA : sourceB;
        assert.deepEqual(body.modelSource, expectedSource, "Every request must keep the explicitly continued source, including its asset SHA");
        assert.equal(body.targetComponentId, expectedSource === sourceA ? element.componentId : null);
        assert.equal(body.elementId, expectedSource === sourceA ? element.elementId : null);
        if (body.documentAnnotations?.length) {
          assert.equal(body.continuationToken, null);
          assert.deepEqual(body.gestures, []); assert.equal(Object.hasOwn(body, "camera"), false);
          assert.equal(body.documentAnnotations[0].revisionSha256, documentPage.revisionSha256);
          assert.equal(body.documentVisuals?.length, 2, "Actual App POST must contain edit and chosen reference page visuals");
          const [edit, reference] = body.documentVisuals;
          assert.deepEqual({ role: edit.role, runId: edit.runId, assetSha256: edit.assetSha256, pageIndex: edit.pageIndex, revisionSha256: edit.revisionSha256 },
            { role: "edit", runId, assetSha256: documentSha, pageIndex: 0, revisionSha256: documentPage.revisionSha256 });
          assert.equal(reference.role, "reference"); assert.equal(reference.assetSha256, referenceSha);
          assert.equal(reference.referenceNote, "Use this page for alignment");
          for (const visual of [edit, reference]) assert.equal(Buffer.from(visual.pagePngBase64, "base64").subarray(1, 4).toString(), "PNG");
          assert.ok(edit.annotatedPngBase64 && edit.annotatedPngBase64 !== edit.pagePngBase64);
          assert.equal(reference.annotatedPngBase64, null);
          const commentRef = `fixture-document-comment-${comments.length + 1}`;
          comments.push({ commentRef, projectId, sourceRunId: runId, stateDigest, utterance: body.utterance,
            documentAnnotations: body.documentAnnotations, submittedAt: "2026-09-09T00:00:00Z" });
          const delayed = heldDocument;
          if (delayed) { delayed.requested.resolve(); await delayed.release.promise; heldDocument = null; }
          return await json({ code: "BLOCKED_NEEDS_HUMAN", detail: "B's drawing needs one dimension.", outcome: "NEEDS_CLARIFICATION",
            question: delayed ? "Which height should B use while C is visible?" : "Which height should the blue drawing mark use?", acceptedForms: [], documentCommentRef: commentRef,
            pendingIntent: { ...pendingIntent(body.utterance, true), continuationToken: delayed?.token ?? documentToken } }, 409);
        }
        assert.equal(Object.hasOwn(body, "documentAnnotations"), false, "Ordinary chat must omit document fields");
        assert.equal(Object.hasOwn(body, "documentVisuals"), false);
        const held = heldIntent; assert.ok(held, "Text responses are explicitly delayed by the current scenario"); heldIntent = null;
        assert.equal(body.utterance, held.utterance); assert.equal(body.continuationToken, held.expectedToken);
        held.requested.resolve(); await held.release.promise;
        return held.clarification
          ? await json({ code: "BLOCKED_NEEDS_HUMAN", detail: "The height remains open.", outcome: "NEEDS_CLARIFICATION",
            question: "Which height should B use?", acceptedForms: [], pendingIntent: {
              ...pendingIntent(body.utterance, true), ...(held.nextToken ? { continuationToken: held.nextToken } : {}),
            } }, 409)
          : await json(compiledResponse(body.utterance, null, body.modelSource), 201);
      }
      assert.fail(`Unexpected API request: ${method} ${url.pathname}${url.search}`);
    } catch (error) { errors.push(error.stack ?? String(error)); await route.abort("blockedbyclient"); }
  });
  await page.goto(`${uiOrigin}/?lang=en`, { waitUntil: "domcontentloaded" });
  await ready(optionA);
  assert.ok(observationTransforms > 0);
  const imageSources = await page.evaluate(() => {
    const raster = (reference) => {
      const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 240;
      const context = canvas.getContext("2d");
      context.fillStyle = reference ? "#d9ebf7" : "#f5f2ea"; context.fillRect(0, 0, 320, 240);
      context.fillStyle = reference ? "#937047" : "#445566";
      if (reference) { context.fillRect(200, 40, 12, 150); context.fillRect(80, 170, 132, 12); }
      else { context.fillRect(40, 30, 220, 4); context.fillRect(40, 30, 4, 150); context.fillRect(40, 176, 220, 4); }
      return canvas.toDataURL("image/png").split(",")[1];
    };
    return { edit: raster(false), reference: raster(true) };
  });
  drawingBytes = Buffer.from(imageSources.edit, "base64");
  referenceBytes = Buffer.from(imageSources.reference, "base64");
  assert.notDeepEqual(drawingBytes, referenceBytes);


  await step("same-run/state View B stays separate until Continue selects exact B", async () => {
    await view(optionB);
    assert.equal(group.selectedOptionId, "A"); assert.deepEqual(selections, []);
    assert.equal(await page.locator(".editing-base").getAttribute("data-source-match"), "different");
    await page.locator(".editing-base").getByRole("button", { name: "Continue from this version", exact: true }).click();
    await until(() => page.locator(".editing-base").getAttribute("data-source-match"), (value) => value === "same", "Continue B did not bind B");
    await ready(optionB); assert.equal(group.selectedOptionId, "B"); assert.equal(selections.length, 1);
  });
  await step("viewing same-run A preserves editing B and posts B's full identity", async () => {
    await view(optionA); await openConversation();
    const before = await viewState(); assert.equal(before.match, "different"); assert.ok(before.editing.includes(optionB.label));
    const held = await sendText("Change B while inspecting A");
    await page.evaluate(() => { window.__intentViewCalls = []; }); held.release.resolve();
    await page.locator(".card--proposal").waitFor();
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The text response did not finish");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), [], "B's reply must not highlight or preview A");
    assert.deepEqual(await viewState(), before); assert.equal(selections.length, 1);
  });
  await step("reload restores selected B when A is listed first in the same run/state", async () => {
    const beforeReads = requests.length;
    await page.reload({ waitUntil: "domcontentloaded" }); await ready(optionB);
    const modelReads = requests.slice(beforeReads).filter((row) => /^\/api\/artifacts\/[^/]+\/bytes$/.test(row.path));
    assert.ok(modelReads.length > 0);
    assert.deepEqual([...new Set(modelReads.map((row) => row.path))], [`/api/artifacts/${sourceB.assetSha256}/bytes`]);
    assert.equal(await page.locator(".editing-base").getAttribute("data-source-match"), "same");
    assert.equal(selections.length, 1, "Restoring is a read, not a new selection");
  });
  for (const clarification of [false, true]) await step(`B's delayed ${clarification ? "clarification" : "proposal"} leaves same-run A unchanged`, async () => {
    await view(optionB); await openConversation();
    const held = await sendText(clarification ? "Clarify B floor height" : "Raise B floor height", clarification);
    await view(optionA);
    const before = await viewState(); await page.evaluate(() => { window.__intentViewCalls = []; });
    held.release.resolve();
    await page.locator(clarification ? ".card--question" : ".card--proposal").last().waitFor();
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The delayed response did not finish");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), []);
    assert.deepEqual(await viewState(), before);
  });
  await step("View A / Edit B posts exact drawing pixels, then continues without sending the pages again", async () => {
    await view(optionA); await openConversation();
    assert.equal(await page.locator(".editing-base").getAttribute("data-source-match"), "different");
    assert.equal(group.selectedOptionId, "B");
    await page.locator(".composer .context").getByRole("button").click();
    await page.locator(".tree__row--element").getByRole("button", { name: element.elementId, exact: true }).click();
    assert.equal(await page.locator(".composer .context .pill--accent").count(), 1, "Select an old target through A's actual catalog");
    assert.ok((await page.evaluate(() => window.__intentViewCalls)).includes("highlight"));
    const beforeDocument = await viewState();
    await page.locator(".stage-mode-switch").getByRole("button", { name: "Drawings & images", exact: true }).click();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.equal(await page.locator(".document-model-source").getAttribute("data-model-source-status"), "ready");
    await page.locator("#document-comment").fill("Apply the drawing annotation to B");
    await page.locator(".document-references summary").click();
    await page.locator(".document-reference").first().locator('input[type="checkbox"]').first().check();
    await page.locator(".document-reference input[type=text]").fill("Use this page for alignment");
    await page.evaluate(() => { window.__intentViewCalls = []; });
    await page.getByRole("button", { name: "Submit page note", exact: true }).click();
    await until(() => comments.length, (count) => count === 1, "The document POST did not reach the real client");
    await page.getByText("Which height should the blue drawing mark use?", { exact: true }).waitFor();
    await until(() => page.getByRole("button", { name: "Submit page note", exact: true }).isEnabled(), Boolean, "The saved-page exchange did not finish");
    const documentIntent = intents.at(-1);
    assert.equal(documentIntent.documentVisuals.length, 2);
    assert.deepEqual(documentIntent.modelSource, sourceB);
    assert.deepEqual(await viewState(), beforeDocument, "The drawing's B response must leave displayed A and its old target intact");
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), []);

    pixelAudit = await page.evaluate(async ({ edit, reference, imageSources }) => {
      const decode = async (base64) => {
        const image = new Image(); image.src = `data:image/png;base64,${base64}`; await image.decode();
        const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
        const context = canvas.getContext("2d"); context.drawImage(image, 0, 0);
        return { width: canvas.width, height: canvas.height, data: context.getImageData(0, 0, canvas.width, canvas.height).data };
      };
      const [original, annotated, other, editSource, referenceSource] = await Promise.all([
        decode(edit.pagePngBase64), decode(edit.annotatedPngBase64), decode(reference.pagePngBase64),
        decode(imageSources.edit), decode(imageSources.reference),
      ]);
      const pixel = (image, x, y) => [...image.data.slice((y * image.width + x) * 4, (y * image.width + x) * 4 + 4)];
      let originalMismatch = 0, referenceMismatch = 0, inkPixels = 0, inkOutside = 0, referenceDifference = 0;
      for (let index = 0; index < original.data.length; index += 4) {
        const differs = (left, right) => [0, 1, 2, 3].some((channel) => left.data[index + channel] !== right.data[index + channel]);
        if (differs(original, editSource)) originalMismatch++;
        if (differs(other, referenceSource)) referenceMismatch++;
        if (differs(original, other)) referenceDifference++;
        if (differs(original, annotated)) {
          inkPixels++;
          const x = (index / 4) % original.width, y = Math.floor(index / 4 / original.width);
          if (x < 45 || x > 243 || y < 93 || y > 99) inkOutside++;
        }
      }
      return { dimensions: [original, annotated, other].map(({ width, height }) => [width, height]),
        originalMismatch, referenceMismatch, inkPixels, inkOutside, referenceDifference,
        editBackground: pixel(original, 10, 10), originalUnderInk: pixel(original, 144, 96),
        savedBlueInk: pixel(annotated, 144, 96), outsideInk: pixel(annotated, 144, 104),
        originalStructure: pixel(original, 42, 40), retainedStructure: pixel(annotated, 42, 40),
        referenceBackground: pixel(other, 10, 10), referenceStructure: pixel(other, 205, 50),
        referenceAtEditStructure: pixel(other, 42, 40) };
    }, { edit: documentIntent.documentVisuals[0], reference: documentIntent.documentVisuals[1], imageSources });
    assert.deepEqual(pixelAudit.dimensions, [[320, 240], [320, 240], [320, 240]]);
    assert.equal(pixelAudit.originalMismatch, 0, "POST's original page must match the actual edit source pixels");
    assert.equal(pixelAudit.referenceMismatch, 0, "POST's reference must match the separately chosen reference pixels");
    assert.deepEqual(pixelAudit.editBackground, [245, 242, 234, 255]);
    assert.deepEqual(pixelAudit.originalUnderInk, pixelAudit.editBackground, "The original input must not already contain the blue saved stroke");
    assert.deepEqual(pixelAudit.savedBlueInk, [47, 128, 237, 255], "Saved blue ink must land at its actual page coordinates");
    assert.deepEqual(pixelAudit.outsideInk, pixelAudit.editBackground);
    assert.deepEqual(pixelAudit.originalStructure, [68, 85, 102, 255]);
    assert.deepEqual(pixelAudit.retainedStructure, pixelAudit.originalStructure);
    assert.deepEqual(pixelAudit.referenceBackground, [217, 235, 247, 255]);
    assert.deepEqual(pixelAudit.referenceStructure, [147, 112, 71, 255]);
    assert.deepEqual(pixelAudit.referenceAtEditStructure, pixelAudit.referenceBackground);
    assert.ok(pixelAudit.inkPixels > 300 && pixelAudit.inkPixels < 1200);
    assert.equal(pixelAudit.inkOutside, 0, "Saved ink may only change its declared line region");
    assert.ok(pixelAudit.referenceDifference > 70_000, "The reference must be a different page, not another encoding of the edit page");

    const documentReads = () => requests.filter((row) => row.method === "GET" && row.path.startsWith("/api/document")).length;
    const readsBeforeReply = documentReads();
    const rendersBeforeReply = await page.evaluate(() => window.__documentVisualRenders);
    assert.equal(rendersBeforeReply, 2, "Only the edit page and selected reference page are rendered for the initial request");
    const proposalsBeforeReply = await page.locator(".card--proposal").count();
    const reply = await sendText("Use a height of 0.2", false, documentToken);
    reply.release.resolve();
    await until(() => page.locator(".card--proposal").count(), (count) => count === proposalsBeforeReply + 1, "The same-token document reply did not finish");
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The Composer reply remained busy");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const replyIntent = intents.at(-1);
    assert.equal(replyIntent.continuationToken, documentToken);
    assert.deepEqual(replyIntent.modelSource, sourceB);
    assert.equal(Object.hasOwn(replyIntent, "documentAnnotations"), false);
    assert.equal(Object.hasOwn(replyIntent, "documentVisuals"), false);
    assert.equal(documentReads(), readsBeforeReply, "A same-token reply must not reread saved pages");
    assert.equal(await page.evaluate(() => window.__documentVisualRenders), rendersBeforeReply, "A same-token reply must not capture page images again");
    assert.deepEqual(await viewState(), beforeDocument);
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), [], "Neither B response may paint a ghost or highlight on viewed A");
  });
  await step("a delayed B document question can continue while viewing cross-run C, and changing the editing asset clears its token", async () => {
    const crossRunToken = "fixture-cross-run-document-token";
    const delayed = { requested: deferred(), release: deferred(), token: crossRunToken };
    heldDocument = delayed;
    await page.locator("#document-comment").fill("Check B's marked height while I inspect C");
    await page.getByRole("button", { name: "Submit page note", exact: true }).click();
    await Promise.race([delayed.requested.promise, sleep(12_000).then(() => assert.fail("The document request was not held"))]);
    await page.locator(".stage-mode-switch").getByRole("button", { name: "3D model", exact: true }).click();
    await openVersions();
    await page.locator(".vcard__export").filter({ hasText: "option-C.3dm" }).click();
    await until(async () => ({ name: await page.locator(".source__name").textContent(),
      annotations: await page.locator("[data-model-annotations-status]").getAttribute("data-model-annotations-status"),
      loading: await page.locator(".source__status").count() }),
    (value) => value.name === "option-C.3dm" && value.annotations === "saved" && value.loading === 0,
    "The cross-run C model was not accepted by the actual viewer");
    assert.equal(group.selectedOptionId, "B");
    assert.equal(selections.length, 1, "Viewing C must not choose a new editing source");
    const beforeQuestion = await viewState();
    assert.equal(beforeQuestion.match, "different");
    assert.ok(beforeQuestion.editing.includes(optionB.label));
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.evaluate(() => { window.__intentViewCalls = []; });
    delayed.release.resolve();
    await page.getByText("Which height should B use while C is visible?", { exact: true }).waitFor();
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The document question did not finish");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(await viewState(), beforeQuestion);
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), [], "B's late document question must not paint C");
    await page.locator(".composer input").fill("The drawing's height should be 0.2");
    assert.equal(await page.locator(".composer button[type=submit]").isEnabled(), true,
      "The document continuation must enable the actual Composer despite the cross-run viewport");
    const readCount = () => requests.filter((row) => row.method === "GET" && row.path.startsWith("/api/document")).length;
    const readsBefore = readCount(), rendersBefore = await page.evaluate(() => window.__documentVisualRenders);
    const questionCount = await page.locator(".card--question").count();
    const reply = await sendText("The drawing's height should be 0.2", true, crossRunToken);
    reply.nextToken = crossRunToken;
    reply.release.resolve();
    await until(() => page.locator(".card--question").count(), (count) => count === questionCount + 1, "The cross-run document continuation was not returned");
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The cross-run Composer reply remained busy");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.deepEqual(intents.at(-1).modelSource, sourceB);
    assert.equal(intents.at(-1).continuationToken, crossRunToken);
    assert.equal(Object.hasOwn(intents.at(-1), "documentAnnotations"), false);
    assert.equal(Object.hasOwn(intents.at(-1), "documentVisuals"), false);
    assert.equal(readCount(), readsBefore); assert.equal(await page.evaluate(() => window.__documentVisualRenders), rendersBefore);
    assert.deepEqual(await viewState(), beforeQuestion);
    assert.deepEqual(await page.evaluate(() => window.__intentViewCalls), [], "The same-token answer must not paint C either");

    // Keep the document question open, then explicitly change just the asset
    // SHA within the original run/state. The old B token must not migrate to A.
    await view(optionA);
    await page.locator(".editing-base").getByRole("button", { name: "Continue from this version", exact: true }).click();
    await until(() => page.locator(".editing-base").getAttribute("data-source-match"), (value) => value === "same", "Continue A did not finish");
    await ready(optionA);
    assert.equal(group.selectedOptionId, "A"); assert.equal(selections.length, 2);
    const proposalCount = await page.locator(".card--proposal").count();
    const fresh = await sendText("Start a fresh change in A", false, null);
    assert.deepEqual(intents.at(-1).modelSource, sourceA);
    assert.equal(intents.at(-1).continuationToken, null, "Changing only the editing SHA must discard the old B document token");
    fresh.release.resolve();
    await until(() => page.locator(".card--proposal").count(), (count) => count === proposalCount + 1, "The fresh A request did not finish");
    await until(() => page.locator(".composer input").isEnabled(), Boolean, "The fresh A reply remained busy");
  });
  await step("switching local files starts fresh Undo/Redo without restoring another file's camera or hits", async () => {
    const localInput = page.locator('input[type="file"][accept=".3dm"]');
    const undo = page.locator(".viewtools").getByRole("button", { name: "Undo mark", exact: true });
    const redo = page.locator(".viewtools").getByRole("button", { name: "Redo", exact: true });
    const modelRequests = () => requests.filter((row) => row.path === "/api/model-annotations").length;
    const requestsBefore = modelRequests();
    const currentInk = () => page.evaluate(() => window.__modelInkSnapshot);
    const loadLocal = async (name, buffer) => {
      await localInput.setInputFiles({ name, mimeType: "application/octet-stream", buffer });
      await until(async () => ({ name: await page.locator(".source__name").textContent(),
        tag: await page.locator(".stage-model .source").getAttribute("data-tag"),
        loading: await page.locator(".stage-model .source__status").count() }),
      (value) => value.name === name && value.tag === "LOCAL" && value.loading === 0, "The local file was not accepted");
    };
    const drawLine = async () => {
      const toggle = page.locator('button[aria-controls="annotation-tools"]');
      if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
      await page.locator("#annotation-tools").getByRole("button", { name: "╱ Line", exact: true }).click();
      const overlay = page.locator('canvas.annotate[data-armed="true"]');
      const box = await overlay.boundingBox(); assert.ok(box);
      await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.5);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5, { steps: 8 });
      await page.mouse.up();
      await until(currentInk, (ink) => ink.length === 1, "The real local-model stroke was not recorded");
      return (await currentInk())[0];
    };
    await loadLocal("local-first.3dm", bytesA);
    const first = await drawLine();
    assert.ok(first.camera.position.length === 3 && first.camera.target.length === 3);
    assert.ok(first.hits.length > 0, "The first file's actual model hits must be part of the regression");
    assert.equal(await undo.isEnabled(), true);
    await loadLocal("local-second.3dm", bytesB);
    assert.deepEqual(await currentInk(), []);
    assert.equal(await undo.isEnabled(), false, "Changing a file is not an undoable erase of the previous file's marks");
    assert.equal(await redo.isEnabled(), false);
    await page.keyboard.press("Control+z");
    assert.deepEqual(await currentInk(), []);
    const second = await drawLine();
    assert.notEqual(second.id, first.id);
    assert.notDeepEqual(second.camera, first.camera, "The different model sizes produce different camera snapshots");
    await undo.click(); assert.deepEqual(await currentInk(), []);
    await redo.click(); assert.deepEqual(await currentInk(), [second], "Redo restores only the current file's full stroke snapshot");
    assert.equal((await currentInk()).some((ink) => ink.id === first.id), false);
    await loadLocal("local-first.3dm", bytesA);
    assert.deepEqual(await currentInk(), []);
    assert.equal(await undo.isEnabled(), false); assert.equal(await redo.isEnabled(), false);
    assert.equal(modelRequests(), requestsBefore, "Local history changes never GET or PUT model annotations");
  });
  assert.deepEqual(errors, []); assert.deepEqual(escapedApiRequests, []);
  console.log(JSON.stringify({ passed: passed.length, projectId, sources: { A: sourceA, B: sourceB, C: sourceC },
    interceptedIntents: intents.length, mockSelections: selections.length, pixelAudit, escapedApiRequests: 0, jsErrors: 0 }, null, 2));
} catch (error) {
  console.error(`FAIL ${phase}: ${error.stack ?? error}`); if (errors.length) console.error(JSON.stringify(errors, null, 2));
  if (page && !page.isClosed()) console.error(await page.locator("body").innerText().catch(() => "Cannot inspect failed page"));
  process.exitCode = 1;
} finally {
  heldIntent?.release.resolve(); heldDocument?.release.resolve(); await browser?.close();
  if (http.listening) await new Promise((resolve, reject) => http.close((error) => error ? reject(error) : resolve()));
  await vite?.close();
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir())); assert.ok(path.basename(cacheDir).startsWith("monkeyarch-intent-view-test-"));
  await rm(cacheDir, { recursive: true, force: true });
}
