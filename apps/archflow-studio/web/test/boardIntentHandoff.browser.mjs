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

// Exercise the real Connected -> App -> compileIntent path with a saved Board
// request. The tiny Board button stands in for its separately tested save/render
// work; every API read and write is intercepted in memory.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "board-intent-handoff-"));
const projectId = "board-intent-fixture", runId = "drawing-model", stateDigest = "1".repeat(64);
const published = { version: 0, stateSha256: "2".repeat(64) };
const referenceRun = { runId, baseVersion: 0, baseSha256: published.stateSha256 };
const rhino = await rhino3dm(), document = new rhino.File3dm(), mesh = new rhino.Mesh();
mesh.vertices().add(0, 0, 0); mesh.vertices().add(1, 0, 0); mesh.vertices().add(0, 1, 0);
mesh.faces().addTriFace(0, 1, 2);
const attributes = new rhino.ObjectAttributes(); attributes.name = "floor";
document.objects().add(mesh, attributes);
const bytes = Buffer.from(document.toByteArray()); document.delete(); mesh.delete(); attributes.delete();
const modelSource = { runId, stateDigest, assetSha256: createHash("sha256").update(bytes).digest("hex") };
const wrongSource = { runId: "default-head", stateDigest: "a".repeat(64), assetSha256: "b".repeat(64) };
const sourceStageRef = "historical-stage", documentSha = "d".repeat(64), revisionRef = "drawing-revision";
const pageSource = { runId: "drawing-storage", assetSha256: documentSha, pageIndex: 0, revisionRef };
const documentAnnotations = [{ runId: pageSource.runId, assetSha256: documentSha, pageIndex: 0,
  drawingRevisionRef: revisionRef, revisionSha256: "e".repeat(64) }];
const visual = { role: "edit", ...documentAnnotations[0], pagePngBase64: "fixture-png", annotatedPngBase64: "fixture-ink" };
const handoff = { projectId, utterance: "Raise the marked floor", modelSource, sourceStageRef,
  source: pageSource, documentAnnotations, documentVisuals: [visual] };
const project = { projectId, projectDir: "in-memory fixture", published, referenceRun,
  intentProvider: "deterministic", intentModel: null };
const element = { componentId: "room", elementId: "floor", producer: "floor", numericFields: { height: 0.15 } };
const state = { projectId, published, referenceRun, sourceStageRef, referenceRunSource: "fixture", referenceReceipt: null,
  matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: "3".repeat(64), stateDigest, activePhase: "stage-2",
  counts: { entities: 1, components: 1, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
  componentTree: [{ componentId: "room", parentComponentId: null, semanticKind: "room", intent: "fixture", maturity: "candidate", revision: 1 }],
  componentTreeError: null, elements: [element], parameters: [], dependencyEdges: [], honesty: [], catalog: null };
const stage = (stageRef, source) => ({ stageRef, modelSource: source, label: stageRef, branchId: "main",
  stageId: "stage-2", createdAt: "2026-09-09T00:00:00Z", parentStageRef: null });
const history = { projectId, branchId: "main", branches: [{ branchId: "main", label: "Main", headStageRef: "current-stage" }],
  stages: [stage(sourceStageRef, modelSource), stage("current-stage", wrongSource)], explorations: [] };
const artifact = { artifactId: modelSource.assetSha256, runId, modelSource, stageId: "stage-2", fileName: "drawing-model.3dm",
  relativePath: null, sha256: modelSource.assetSha256, sizeBytes: bytes.length, objectCount: 1, status: "registered",
  readbackVerified: null, available: true, unavailableReason: null, base: published, branchId: "main", branchEpoch: 0,
  programRef: null, programDigest: null, designStateDigest: stateDigest, receiptRef: null, format: "3dm", representation: "composed" };
const sourceDocument = { projectId, runId: pageSource.runId, assetSha256: documentSha, fileName: "marked-drawing.png",
  mimeType: "image/png", sizeBytes: 100, pageCount: 1, pages: [{ pageIndex: 0, width: 32, height: 24, rotation: 0 }],
  modelSource, modelSourceBindingRef: "fixture-binding", sourceStageRef, revisionRef };
const annotationPage = { projectId, ...documentAnnotations[0], annotations: [], comment: "Saved board note" };
const pending = (utterance, continuationToken) => ({ requestId: "board-pending", stateDigest, originalUtterance: utterance,
  actionKind: "change_existing_value", targetComponentId: "room", elementId: "floor", requestedSemanticProperty: "height",
  knownSlots: { property: "height" }, missingSlots: continuationToken ? ["value"] : [], candidates: [], scopeOptions: [],
  rejectedCandidates: [], reasonCode: continuationToken ? "MISSING_VALUE" : "COMPILED", continuationToken, turn: 1 });
const compiled = (utterance) => ({ outcome: "COMPILED", agent: null, pendingIntent: pending(utterance, null), gestures: [],
  documentCommentRef: "saved-board-comment", timings: { compileMs: 0, typeMs: 0 }, proposal: {
    proposalId: "board-proposal", status: "proposed", modelSource, sourceRunId: runId, sourceStageRef,
    baseStateDigest: stateDigest, recordDigest: state.recordDigest,
    target: { componentId: "room", elementId: "floor", ref: "entity:floor", key: "height" },
    change: { kind: "set_scalar", old: 0.15, new: 0.2, unit: null }, protected: [], decisionOperator: null,
    impact: { direct: [], propagated: [], protected: [], conflicts: [], locks: [], unknownCoverage: { count: 0, componentIds: [] }, honesty: [] },
    utterance, persistence: "in-memory fixture", createdAt: "2026-09-09T00:00:00Z", scope: null } });
let browser, vite, currentCase, page;
const http = createHttpServer(), failures = [], escaped = [], passed = [];
try {
  vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [{ name: "saved-board-request-fixture", enforce: "pre",
      transform(_source, id) {
        if (id.split("?")[0].replaceAll("\\", "/") !== `${webRoot.replaceAll("\\", "/")}/src/workspaces/monkeyboard/Board.tsx`) return;
        return { code: `export default function Board({onSubmit}) { return <button onClick={() => onSubmit(window.__boardRequest)}>Submit saved Board comments</button>; }`, map: null };
      },
    }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  for (const scenario of ["compiled", "clarification", "changed-stage", "unavailable-base", "changed-model", "wrong-project"]) {
    currentCase = scenario;
    history.branches[0].headStageRef = "current-stage";
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const request = structuredClone(handoff);
    if (scenario === "wrong-project") request.projectId = "other-project";
    await context.addInitScript((request) => { window.__boardRequest = request; }, request);
    page = await context.newPage(); page.setDefaultTimeout(15_000);
    const intents = [], stateReads = [], mutations = [], modelReads = [];
    let recovered = false;
    page.on("pageerror", (error) => failures.push(`${scenario}: ${error.message}`));
    const png = await page.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = 32; canvas.height = 24;
      canvas.getContext("2d").fillRect(0, 0, 32, 24); return canvas.toDataURL().split(",")[1];
    });
    await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
      const req = route.request(), url = new URL(req.url()), method = req.method();
      const json = (body, status = 200) => route.fulfill({ status, json: body });
      try {
        assert.equal(url.origin, origin);
        if (method === "GET") {
          if (url.pathname === "/api/protocol") return await json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local", capabilities: ["design-history", "document-visual-input"] });
          if (url.pathname === "/api/project") return await json(project);
          if (url.pathname === "/api/design-history") return await json(history);
          if (url.pathname === "/api/state") {
            stateReads.push(Object.fromEntries(url.searchParams));
            if (scenario === "unavailable-base" && !recovered) return await json({
              code: "EDITING_BASE_UNAVAILABLE", detail: "The drawing model is temporarily unavailable." }, 503);
            return await json({ ...state, ...(scenario === "changed-stage" && !recovered ? { sourceStageRef: "different-stage" } : {}) });
          }
          if (url.pathname === "/api/artifacts") return await json({ projectId, artifacts: [artifact] });
          if (url.pathname === `/api/artifacts/${modelSource.assetSha256}/bytes`) {
            modelReads.push(modelSource.assetSha256);
            return await route.fulfill({ status: 200, contentType: "application/octet-stream", body: bytes });
          }
          if (url.pathname === "/api/documents") return await json({ projectId, runId: pageSource.runId, documents: [sourceDocument] });
          if (url.pathname === "/api/drawings/styles") return await json({ styles: [] });
          if (url.pathname === "/api/document-annotations") return await json(annotationPage);
          if (url.pathname === "/api/document-comments") return await json({ comments: [] });
          if (url.pathname === `/api/documents/${documentSha}/bytes`) return await route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from(png, "base64") });
        }
        mutations.push(`${method} ${url.pathname}`);
        assert.equal(`${method} ${url.pathname}`, "POST /api/intents", "Handoff never generates, endorses or accepts a candidate automatically");
        const body = req.postDataJSON(); intents.push(body);
        assert.equal(body.projectId, projectId); assert.equal(body.stateDigest, stateDigest);
        assert.equal(body.sourceStageRef, sourceStageRef); assert.equal(body.sourceRunId, runId);
        assert.deepEqual(body.modelSource, modelSource);
        if (intents.length === 1) {
          assert.equal(body.utterance, handoff.utterance); assert.equal(body.continuationToken, null);
          assert.deepEqual(body.documentAnnotations, documentAnnotations); assert.deepEqual(body.documentVisuals, [visual]);
          assert.deepEqual(body.gestures, []); assert.equal(body.targetComponentId, null); assert.equal(body.elementId, null);
          if (scenario === "clarification") return await json({ code: "BLOCKED_NEEDS_HUMAN", detail: "One dimension is needed.",
            question: "What height should the marked floor use?", acceptedForms: [], outcome: "NEEDS_CLARIFICATION",
            pendingIntent: pending(body.utterance, "board-continuation") }, 409);
        } else {
          assert.equal(scenario, "clarification"); assert.equal(body.continuationToken, "board-continuation");
          assert.equal(Object.hasOwn(body, "documentAnnotations"), false);
          assert.equal(Object.hasOwn(body, "documentVisuals"), false);
        }
        return await json(compiled(body.utterance), 201);
      } catch (error) { failures.push(`${scenario}: ${error.stack ?? error}`); await route.abort("blockedbyclient"); }
    });
    if (scenario === "changed-model") history.stages[0] = stage(sourceStageRef, { ...modelSource, assetSha256: "f".repeat(64) });
    else history.stages[0] = stage(sourceStageRef, modelSource);
    await page.goto(`${origin}/?view=board&lang=en`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: "Submit saved Board comments" }).click();
    assert.equal(new URL(page.url()).searchParams.get("view"), "documents");
    assert.equal(new URL(page.url()).searchParams.get("documentRun"), pageSource.runId);
    assert.equal(new URL(page.url()).searchParams.get("documentSource"), documentSha);
    assert.equal(new URL(page.url()).searchParams.get("documentRevision"), revisionRef);
    assert.equal(await page.title(), "MonkeyArch");
    if (["changed-stage", "unavailable-base", "changed-model", "wrong-project"].includes(scenario)) {
      await page.locator(".card--refusal, .refusal__card").first().waitFor();
      assert.deepEqual(intents, []); assert.deepEqual(mutations, []);
      assert.ok((await page.locator("body").innerText()).includes(handoff.utterance) ||
        await page.locator(".composer input").inputValue().catch(() => "") === handoff.utterance,
      "A refused handoff must keep the unsent instruction visible or in the composer");
      if (["changed-stage", "unavailable-base"].includes(scenario)) {
        await page.locator(".document-header").waitFor();
        await page.getByText(scenario === "changed-stage"
          ? "This drawing no longer matches its saved model version. Your feedback was not sent; the original instruction is kept below."
          : "This drawing's saved model could not be opened. Your feedback was not sent; the original instruction is kept below.", { exact: true }).waitFor();
        assert.equal(await page.locator(".composer input").inputValue(), handoff.utterance);
        assert.equal(await page.locator(".composer input").isVisible(), true);
        // Correcting the source and explicitly reopening its model must not
        // resume the refused one-time submission behind the user's back.
        recovered = true;
        history.branches[0].headStageRef = sourceStageRef;
        await page.getByRole("button", { name: "MonkeyArch · 3D", exact: true }).click();
        await page.locator(".refusal__card").getByRole("button", { name: "Retry", exact: true }).click();
        await page.locator(".refusal__card").waitFor({ state: "hidden" });
        await page.getByRole("button", { name: "MonkeyDiagram · Drawings", exact: true }).click();
        await page.locator(".document-header").waitFor();
        await page.getByRole("button", { name: "Continue from this model", exact: true }).waitFor({ state: "hidden" });
        assert.equal(await page.locator(".composer input").inputValue(), handoff.utterance);
        assert.equal(await page.locator(".card--refusal").count(), 1, "Recovery must not duplicate the refusal");
        assert.deepEqual(intents, []); assert.deepEqual(mutations, []);
        await page.reload({ waitUntil: "domcontentloaded" });
        await page.locator(".document-header").waitFor();
        await page.locator(".card--refusal").waitFor();
        assert.equal(await page.locator(".composer input").inputValue(), handoff.utterance);
        assert.deepEqual(intents, []); assert.deepEqual(mutations, []);
      }
    } else {
      await page.locator(scenario === "clarification" ? ".card--question" : ".card--proposal").waitFor();
      assert.equal(intents.length, 1, "The saved request is compiled exactly once");
      assert.equal(await page.locator('button[aria-controls="conversation-panel"]').getAttribute("aria-expanded"), "true");
      assert.ok(stateReads.length > 0);
      assert.ok(stateReads.every((read) => read.run === runId), JSON.stringify(stateReads));
      assert.ok(stateReads.slice(0, 2).every((read) => read.sourceStageRef === sourceStageRef), JSON.stringify(stateReads));
      assert.deepEqual([...new Set(modelReads)], [modelSource.assetSha256], "Default HEAD must not load over the drawing's exact model");
      if (scenario === "clarification") {
        await page.locator(".composer input").fill("Use 0.2 metres");
        await page.locator(".composer button[type=submit]").click();
        await page.locator(".card--proposal").waitFor(); assert.equal(intents.length, 2);
      }
      const before = intents.length;
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.locator(".document-header, .refusal__card").first().waitFor();
      assert.equal(intents.length, before, "Refresh preserves the page URL without replaying its one-time intent");
    }
    assert.deepEqual(failures, []); passed.push(scenario); console.log(`PASS ${scenario}`);
    // Unmount the document while API interception is still active. Closing the
    // context directly can remove its page routes before a new ink read starts.
    await page.goto("about:blank");
    await context.close(); page = null;
  }
  assert.deepEqual(escaped, []); console.log(JSON.stringify({ passed }));
} catch (error) {
  console.error(JSON.stringify({ currentCase, failures, escaped, visible: await page?.locator("body").innerText().catch(() => "") }));
  throw error;
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
