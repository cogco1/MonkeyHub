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

// Actual App, job observation and 3DM parser; every API response is in memory.
// No user service, project, browser profile or accepted design is touched.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-candidate-preview-test-"));
const rhino = await rhino3dm();
const published = { version: 0, stateSha256: "1".repeat(64) };
const relations = { held: 0, violated: 0, unchecked: 0, heldFlag: true, fullyChecked: true };
const allArtifacts = [], models = new Map(), jobs = new Map(), candidates = new Map();
const workingCopies = [];
const requests = [], errors = [], passed = [], validationGates = new Map(), modelGates = new Map(), stateGates = new Map();
let projectId = "candidate-preview-fixture", artifactFailures = 0, seq = 0, nextProgram = null;
let historyEnabled = false, acceptFailure = false, annotationFailure = false, lastDrawing = null, nextCombined = null;
const branches = new Map(), stages = new Map(), candidateBases = new Map(), documents = [], annotations = new Map();
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDWsAAAAASUVORK5CYII=", "base64");
function historyDto(branchId = "main") {
  const chain = []; let ref = branches.get(branchId)?.headStageRef;
  while (ref) { const stage = stages.get(ref); chain.unshift(stage); ref = stage.parentStageRef; }
  return { projectId, branchId, branches: [...branches.values()], stages: chain };
}
function commitStage(modelSource, branchId, label, parentStageRef = null) {
  const stageRef = `archflow-project://${projectId}/design_stage/${branchId}/${label}`;
  const stage = { stageRef, parentStageRef, branchId, label, candidateId: modelSource.runId, modelSource, recordDigest: digest(modelSource.runId) };
  stages.set(stageRef, stage);
  branches.set(branchId, { branchId, parentBranch: null, forkStageRef: stageRef, ...branches.get(branchId), headStageRef: stageRef });
  return stage;
}
let vite, browser, page;
const http = createHttpServer();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
const digest = (text) => createHash("sha256").update(text).digest("hex");
const stateDigest = (run) => digest(`state:${run}`);
const artifactDto = ({ projectId: _owner, ...artifact }) => artifact;
function makeArtifact(runId, width = 1, owner = projectId) {
  const document = new rhino.File3dm(), mesh = new rhino.Mesh(), attributes = new rhino.ObjectAttributes();
  mesh.vertices().add(0, 0, 0); mesh.vertices().add(width, 0, 0);
  mesh.vertices().add(width, 1, 0); mesh.vertices().add(0, 1, 0); mesh.faces().addQuadFace(0, 1, 2, 3);
  attributes.name = "fixture-floor"; document.objects().add(mesh, attributes);
  const bytes = Buffer.from(document.toByteArray());
  document.delete(); mesh.delete(); attributes.delete();
  const sha256 = digest(bytes);
  models.set(sha256, bytes);
  return { artifactId: `${runId}:model`, projectId: owner, runId, stageId: "fixture-seat", seatId: "fixture-seat",
    fileName: `${runId}.3dm`, sha256, sizeBytes: bytes.length, available: true, unavailableReason: null,
    lengthUnit: "meters", programRef: null, programDigest: null, designStateDigest: stateDigest(runId), receiptRef: "fixture",
    format: "3dm", representation: "composed", modelSource: { runId, stateDigest: stateDigest(runId), assetSha256: sha256 } };
}
const home = makeArtifact("original"), other = makeArtifact("other-view", 2);
allArtifacts.push(home, other);
let currentHome = home;
const binding = () => ({ projectId, projectDir: "in-memory fixture", published,
  referenceRun: { runId: currentHome.runId, baseVersion: 0, baseSha256: "1".repeat(64) }, intentProvider: "codex", intentModel: "fixture" });
function projection(runId = currentHome.runId, stageRef = null) {
  const element = { componentId: "fixture-room", elementId: "fixture-floor", producer: "floor", numericFields: { height: 0.2 } };
  return { projectId, published, sourceStageRef: stageRef ?? candidateBases.get(runId) ?? [...stages.values()].find((stage) => stage.modelSource.runId === runId)?.stageRef ?? null,
    referenceRun: { ...binding().referenceRun, runId }, referenceRunSource: "fixture",
    referenceReceipt: null, matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: digest(`record:${runId}`), stateDigest: stateDigest(runId),
    activePhase: "stage-2", counts: { entities: 1, components: 1, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
    componentTree: [{ componentId: element.componentId, parentComponentId: null, semanticKind: "room", intent: "fixture", maturity: "candidate", revision: 1 }],
    componentTreeError: null, elements: [element], parameters: [], dependencyEdges: [], honesty: [], catalog: {
      components: [{ componentId: element.componentId, parentId: null, children: [], elementIds: [element.elementId], descendantElementIds: [element.elementId],
        capabilityCount: 1, states: ["editable"], objectCount: 1, unboundObjectCount: 0, closure: [] }],
      elements: [{ ...element, objectNames: [element.elementId], capabilities: [{ capabilityId: "height", elementId: element.elementId, key: "height", value: 0.2,
        valueType: "number", unit: null, bounds: null, source: "authored", confidence: 1, status: "editable", validatorRefs: [] }] }],
      objects: [{ name: element.elementId, componentId: element.componentId, producerOp: "floor", elementId: element.elementId, status: "bound", detail: "fixture" }],
      coverage: { objects: 1, bound: 1, unbound: 0, ambiguous: 0, unknownComponent: 0 }, inspectionRun: runId, honesty: [],
    } };
}
const totals = { targetAreaM2: 0, mappedAreaM2: 0, spaces: 0, mappedSpaces: 0, adjacencyCount: 0 };
function program(runId) {
  return { sourceRunId: runId, source: "derived", stateDigest: stateDigest(runId),
    sheet: { schema: "ProgramSheet@1", projectId, recordDigest: digest(`record:${runId}`), stateDigest: stateDigest(runId),
      departments: [], adjacencies: [], totals, honesty: [] } };
}
function prepare(id) {
  const artifact = makeArtifact(id, candidates.size + 3);
  const job = { jobId: `job-${id}`, candidateId: id, proposalId: `proposal-${id}`, status: "running", createdAt: new Date().toISOString(),
    startedAt: new Date().toISOString(), finishedAt: null, error: null, wallTimeS: null, lane: "parallel", waitingFor: null, waitingReason: null, persistence: "fixture" };
  const candidate = { candidateId: id, proposalId: job.proposalId, jobId: job.jobId, status: "succeeded", base: published,
    stateDigest: stateDigest(id), recordDigest: digest(`record:${id}`), changedVsProjection: true, receiptRef: "fixture", seatExecutionComplete: true,
    seatResults: [], relationChecks: relations, artifacts: [artifact], skippedRuns: [], wallTimeS: 0.1,
    timings: { runS: 0.1, seats: [], exports: [] }, harness: "Unaccepted candidate", honesty: [] };
  jobs.set(job.jobId, job); candidates.set(id, candidate);
  return candidate;
}
async function emit(type, candidateId = null) {
  const event = { seq: ++seq, at: new Date().toISOString(), type, candidateId, runId: candidateId,
    jobId: candidateId ? candidates.get(candidateId).jobId : null };
  await page.evaluate((value) => window.__previewEvents.emit(value), event);
}
async function complete(candidate) {
  jobs.get(candidate.jobId).status = "succeeded";
  allArtifacts.push(...candidate.artifacts);
  await emit("candidate.succeeded", candidate.candidateId);
}
async function until(read, accepts, message, timeout = 20_000) {
  const end = Date.now() + timeout;
  let value;
  do {
    assert.deepEqual(errors, [], message);
    value = await read();
    if (accepts(value)) return value;
    await delay(50);
  } while (Date.now() < end);
  assert.fail(`${message}: ${JSON.stringify(value)}`);
}
const snapshot = () => page.evaluate(() => window.__candidatePreview?.snapshot ?? {});
async function rendered(id, fileName = `${id}.3dm`) {
  await until(snapshot, (value) => value.loadedRunId === id && value.loadedFileName === fileName && value.status === "ready" && value.loadingSha === null,
    `${id} did not finish parsing and become the displayed model`, 45_000);
  assert.equal(await page.locator(".source__name").textContent(), fileName);
}
async function view(artifact) {
  await page.evaluate((value) => window.__candidatePreview.view(value), artifactDto(artifact));
  await rendered(artifact.runId, artifact.fileName);
}
async function launch(candidate) {
  await page.evaluate((id) => window.__candidatePreview.run(id), candidate.proposalId);
  await until(snapshot, (value) => value.runs[candidate.candidateId]?.job.status === "ready", "The shell did not begin observing the job");
}
async function conversation(open) {
  const button = page.locator('button[aria-controls="conversation-panel"]');
  if ((await button.getAttribute("aria-expanded") === "true") !== open) await button.click();
  assert.equal(await button.getAttribute("aria-expanded"), String(open));
}
async function step(name, action) { await action(); passed.push(name); console.log(`PASS ${name}`); }

try {
  vite = await createServer({ root: webRoot, configFile: false, logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [{
      name: "observe-actual-candidate-shell", enforce: "pre",
      transform(source, id) {
        const modulePath = id.split("?")[0].replaceAll("\\", "/");
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx`) {
          const marker = "      let models = parsed";
          assert.equal(source.split(marker).length, 2);
          return { code: source.replace(marker, `
            const held = (window as unknown as { __previewParseGates: Record<string, { waiting: boolean; promise: Promise<void> }> }).__previewParseGates[files[0].name];
            if (held) { held.waiting = true; await held.promise; }
` + marker), map: null };
        }
        if (modulePath !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
        const marker = '  const booting = session.status === "idle" || session.status === "loading";';
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, marker + `
          (window as unknown as { __candidatePreview: unknown }).__candidatePreview = {
            run: runCandidate, program: applyProgram, readProgram: loadProgram, changeBase: changeEditingBase, reload, propose,
            view: (artifact: ProjectArtifactDto) => { manualLoadRef.current = true; return loadArtifactIntoViewer(artifact, artifact.fileName); },
            snapshot: { loadedRunId: loadedArtifact?.runId, loadedFileName: loadedArtifact?.fileName, status: viewerStatus, loadingSha: artifactLoadingSha,
              projectId: project?.projectId, editingRunId: projection?.referenceRun.runId, changingBase, runs: candidateRuns.runs,
              sourceStageRef: projection?.sourceStageRef, history: designHistory, historyError, drawingError, documentView,
              loadedModelSource, editingModelSource, baseError: baseError?.code ?? null,
              artifacts: artifacts.status === "ready" ? artifacts.value.artifacts.map((row) => row.runId) : [],
              program: program.status === "ready" ? program.value.sheet : null,
              entries: transcript.entries.map((entry) => entry.kind === "system" ? entry.text : entry.kind),
              candidateEntries: candidateEntries.map((entry) => ({ candidateId: entry.candidateId, proposalId: entry.proposalId })) }
          };`), map: null };
      },
    }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { errors.push(`API escaped fixture: ${request.method} ${request.url}`); response.writeHead(405); response.end(); }
    else vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
    "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await context.addInitScript(({ projectId, runId }) => {
    localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
      eventStreamVisible: false, developerMode: false, editingBases: { [JSON.stringify(["", projectId])]: runId } }));
    const sources = new Set();
    class FakeEventSource {
      constructor() { this.listeners = new Map(); sources.add(this); queueMicrotask(() => this.onopen?.()); }
      addEventListener(type, listener) { this.listeners.set(type, listener); }
      removeEventListener(type) { this.listeners.delete(type); }
      close() { sources.delete(this); }
    }
    window.EventSource = FakeEventSource;
    window.__previewParseGates = {};
    window.__previewEvents = { emit(value) { for (const source of sources) source.listeners.get(value.type)?.({ data: JSON.stringify(value) }); } };
  }, { projectId, runId: home.runId });
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && message.text().includes("Encountered two children with the same key")) errors.push(message.text()); });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), name = url.pathname;
    requests.push({ method, name, query: Object.fromEntries(url.searchParams), body: method === "POST" || method === "PUT" ? request.postDataJSON() : null });
    try {
      assert.equal(url.origin, origin);
      const json = (body, status = 200) => route.fulfill({ status, json: body });
      if (method === "GET") {
        if (name === "/api/protocol") return await json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local",
          capabilities: ["working-copies", "model-annotations", "events", "program", ...(historyEnabled ? ["design-history", "drawing-elevations", "document-visual-input"] : [])] });
        if (name === "/api/project") return await json(binding());
        if (name === "/api/state") {
          const runId = url.searchParams.get("run") ?? currentHome.runId;
          const gate = stateGates.get(runId); if (gate) { gate.requested = true; await gate.promise; }
          return await json(projection(runId, url.searchParams.get("sourceStageRef")));
        }
        if (name === "/api/design-history") return await json(historyDto(url.searchParams.get("branchId") ?? "main"));
        if (name === "/api/documents") return await json({ projectId, runId: url.searchParams.get("runId"), documents: documents.filter((doc) => doc.runId === url.searchParams.get("runId")) });
        if (name === "/api/document-comments") return await json({ projectId, runId: url.searchParams.get("runId"), comments: [] });
        if (name === "/api/document-annotations") {
          const runId = url.searchParams.get("runId"), assetSha256 = url.searchParams.get("assetSha256"), pageIndex = Number(url.searchParams.get("pageIndex"));
          const drawingRevisionRef = url.searchParams.get("drawingRevisionRef");
          return await json(annotations.get(`${runId}:${assetSha256}:${pageIndex}:${drawingRevisionRef ?? ""}`) ?? {
            projectId, runId, assetSha256, pageIndex, drawingRevisionRef, revisionSha256: null, annotations: [], comment: "" });
        }
        if (/^\/api\/documents\/[^/]+\/bytes$/.test(name)) return await route.fulfill({ status: 200, body: png, contentType: "image/png" });
        if (name === "/api/working-copies") return await json({ workingCopies });
        if (name === "/api/artifacts") {
          if (artifactFailures > 0) { artifactFailures--; return await json({ code: "TEMPORARY_LIST_FAILURE", detail: "Retry this model list." }, 503); }
          return await json({ projectId, artifacts: allArtifacts.filter((row) => row.projectId === projectId).map(artifactDto), skippedRuns: [] });
        }
        const bytes = /^\/api\/artifacts\/([^/]+)\/bytes$/.exec(name);
        if (bytes) {
          const gate = modelGates.get(bytes[1]);
          if (gate) await gate.promise;
          assert.ok(models.has(bytes[1]));
          return await route.fulfill({ status: 200, body: models.get(bytes[1]), contentType: "application/octet-stream" });
        }
        const job = /^\/api\/jobs\/(.+)$/.exec(name);
        if (job) { assert.ok(jobs.has(job[1])); return await json(jobs.get(job[1])); }
        const candidate = /^\/api\/candidates\/([^/]+)$/.exec(name);
        if (candidate) {
          assert.ok(candidates.has(candidate[1])); const value = candidates.get(candidate[1]);
          return await json({ ...value, artifacts: value.artifacts.map(artifactDto) });
        }
        const validation = /^\/api\/candidates\/([^/]+)\/validation$/.exec(name);
        if (validation) {
          const gate = validationGates.get(validation[1]); if (gate) await gate.promise;
          return await json({ code: "VALIDATION_UNAVAILABLE", detail: "The independent review is unavailable." }, 503);
        }
        if (name === "/api/model-annotations") return await json({ projectId, modelSource: {
          runId: url.searchParams.get("runId"), stateDigest: url.searchParams.get("stateDigest"), assetSha256: url.searchParams.get("assetSha256"),
        }, revisionSha256: null, annotations: [], comment: "" });
        if (name === "/api/program") return await json(program(url.searchParams.get("run") ?? currentHome.runId));
      }
      if (method === "PUT" && name === "/api/document-annotations") {
        if (annotationFailure) return await json({ code: "SAVE_UNAVAILABLE", detail: "Save failed; keep this page open." }, 503);
        const body = request.postDataJSON();
        const result = { ...body, revisionSha256: digest(JSON.stringify(body)) }; annotations.set(`${body.runId}:${body.assetSha256}:${body.pageIndex}:${body.drawingRevisionRef ?? ""}`, result);
        return await json(result);
      }
      if (method === "PUT" && /^\/api\/working-copies\/[^/]+\/selection$/.test(name)) {
        const copy = workingCopies.find((item) => item.groupId === name.split("/")[3]); const body = request.postDataJSON();
        assert.ok(copy); assert.equal(body.baseRevisionSha256, copy.revisionSha256);
        copy.selectedOptionId = body.optionId; copy.revisionSha256 = digest(`${copy.revisionSha256}:${body.optionId}`);
        return await json(copy);
      }
      if (method === "POST" && name === "/api/design-stages/initialize") {
        const body = request.postDataJSON(); return await json(commitStage(body.modelSource, body.branchId, body.label), 201);
      }
      if (method === "POST" && /^\/api\/candidates\/[^/]+\/accept$/.test(name)) {
        if (acceptFailure) return await json({ code: "DESIGN_HEAD_MOVED", detail: "The branch head changed. Candidate kept." }, 409);
        const body = request.postDataJSON(), id = name.split("/")[3];
        assert.equal(body.expectedHeadStageRef, branches.get(body.branchId).headStageRef);
        return await json(commitStage(candidates.get(id).artifacts[0].modelSource, body.branchId, `S${historyDto(body.branchId).stages.length}`, body.expectedHeadStageRef), 201);
      }
      if (method === "POST" && name === "/api/design-branches") {
        const body = request.postDataJSON(); const branch = { branchId: body.branchId, parentBranch: body.parentBranch, forkStageRef: body.stageRef, headStageRef: body.stageRef };
        branches.set(body.branchId, branch); return await json(branch, 201);
      }
      if (method === "POST" && name === "/api/intents") return await json({ code: "UNSUPPORTED_REQUEST", detail: "Fixture records the source context." }, 422);
      if (method === "POST" && name === "/api/drawings/elevations") {
        const body = request.postDataJSON(); const source = body.sourceStageRef ? stages.get(body.sourceStageRef).modelSource : body.modelSource;
        lastDrawing = { projectId, runId: source.runId, assetSha256: digest(png), fileName: "front-elevation.png", mimeType: "image/png", sizeBytes: png.length,
          pageCount: 1, pages: [{ pageIndex: 0, width: 200, height: 150, rotation: 0 }], modelSource: source,
          drawingId: "front", revisionRef: `archflow-project://${projectId}/drawing/front/${documents.length}`, sourceStageRef: body.sourceStageRef ?? null, viewRecipe: { view: body.view } };
        documents.push({ ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-older` }, lastDrawing);
        return await json(lastDrawing, 201);
      }
      if (method === "POST" && name === "/api/candidates/combine") {
        assert.ok(nextCombined); const candidate = nextCombined; nextCombined = null;
        return await json({ candidateId: candidate.candidateId, jobId: candidate.jobId, status: "running" }, 202);
      }
      if (method === "POST" && /^\/api\/proposals\/.+\/candidate$/.test(name)) {
        const id = name.split("/")[3];
        const candidate = [...candidates.values()].find((row) => row.proposalId === id);
        assert.ok(candidate);
        return await json({ candidateId: candidate.candidateId, jobId: candidate.jobId, status: "running" }, 202);
      }
      if (method === "POST" && name === "/api/program") {
        assert.ok(nextProgram); const candidate = nextProgram; nextProgram = null;
        const body = request.postDataJSON();
        assert.equal(body.sourceRunId, home.runId); assert.deepEqual(body.modelSource, home.modelSource);
        return await json({ candidateId: candidate.candidateId, jobId: candidate.jobId, status: "running", totals, savedInput: false, honesty: [] }, 202);
      }
      assert.fail(`Unexpected request: ${method} ${name}`);
    } catch (error) {
      errors.push(error.stack ?? String(error)); await route.fulfill({ status: 500, body: String(error) }).catch(() => {});
    }
  });
  await page.goto(`${origin}/?lang=en`, { waitUntil: "domcontentloaded" });
  await rendered(home.runId);

  await step("a failed background artifact refresh retries without losing the displayed model", async () => {
    const external = makeArtifact("external-result", 4); allArtifacts.push(external);
    const before = requests.filter((row) => row.name === "/api/artifacts").length;
    artifactFailures = 1;
    await emit("model_asset.registered");
    await until(snapshot, (value) => value.artifacts.includes(external.runId), "The first failed refresh was never retried");
    assert.ok(requests.filter((row) => row.name === "/api/artifacts").length >= before + 2);
    await rendered(home.runId);
  });

  await step("closed conversation does not stop completion or preview while validation is pending", async () => {
    const candidate = prepare("closed-chat");
    const validation = deferred(), bytes = deferred();
    validationGates.set(candidate.candidateId, validation); modelGates.set(candidate.artifacts[0].sha256, bytes);
    await conversation(true); await launch(candidate); await conversation(false); await complete(candidate);
    await until(snapshot, (value) => value.loadingSha === candidate.artifacts[0].sha256, "Completed geometry did not start downloading without validation");
    const waiting = await snapshot();
    assert.equal(waiting.loadedRunId, home.runId);
    assert.equal(waiting.runs[candidate.candidateId].validation.status, "loading");
    assert.equal(waiting.entries.some((line) => line.includes("model is on screen") && line.includes(candidate.candidateId)), false);
    bytes.resolve(); await rendered(candidate.candidateId);
    assert.equal((await snapshot()).runs[candidate.candidateId].validation.status, "loading");
    assert.equal(await page.locator("#conversation-panel").count(), 0);
    assert.equal((await snapshot()).entries.filter((line) => line.includes("model is on screen") && line.includes(candidate.candidateId)).length, 1);
    validation.resolve();
    await until(snapshot, (value) => value.runs[candidate.candidateId].validation.status === "failed", "The validation failure disappeared");
    await rendered(candidate.candidateId);
  });

  await step("an immediate validation failure does not block a candidate preview", async () => {
    await view(home);
    const candidate = prepare("validation-failed"); await launch(candidate); await complete(candidate); await rendered(candidate.candidateId);
    assert.equal((await snapshot()).runs[candidate.candidateId].validation.status, "failed");
  });

  await step("a parse failure retains the previous model and never announces the candidate as displayed", async () => {
    await view(home);
    const candidate = prepare("invalid-model");
    const bytes = Buffer.from("This is not a valid Rhino file."), sha256 = digest(bytes);
    candidate.artifacts[0] = { ...candidate.artifacts[0], sha256, sizeBytes: bytes.length,
      modelSource: { ...candidate.artifacts[0].modelSource, assetSha256: sha256 } };
    models.set(sha256, bytes);
    await launch(candidate); await complete(candidate);
    await until(snapshot, (value) => value.status === "error" && value.loadingSha === null, "Invalid model bytes were not reported");
    const failed = await snapshot();
    assert.equal(failed.loadedRunId, home.runId);
    assert.equal(failed.entries.some((line) => line.includes("model is on screen") && line.includes(candidate.candidateId)), false);
  });

  await step("a delayed model download cannot replace a newer explicit view", async () => {
    await view(home);
    const candidate = prepare("late-download"), bytes = deferred();
    modelGates.set(candidate.artifacts[0].sha256, bytes);
    await launch(candidate); await complete(candidate);
    await until(snapshot, (value) => value.loadingSha === candidate.artifacts[0].sha256, "The candidate download did not begin");
    await view(other); bytes.resolve();
    await delay(150); await rendered(other.runId);
    assert.equal((await snapshot()).entries.some((line) => line.includes("model is on screen") && line.includes(candidate.candidateId)), false);
  });

  await step("a parsed candidate cannot attach after an editing-context switch begins", async () => {
    await view(home);
    const candidate = prepare("late-parse");
    await page.evaluate((fileName) => {
      let release; const promise = new Promise((resolve) => { release = resolve; });
      window.__previewParseGates[fileName] = { promise, release, waiting: false };
    }, candidate.artifacts[0].fileName);
    await launch(candidate); await complete(candidate);
    await page.waitForFunction((fileName) => window.__previewParseGates[fileName].waiting, candidate.artifacts[0].fileName);
    const stateGate = deferred(); stateGates.set(other.runId, stateGate);
    await page.evaluate((source) => { void window.__candidatePreview.changeBase(source.runId, source); }, other.modelSource);
    await until(() => stateGate.requested, Boolean, "The next editing context was not requested");
    await page.evaluate((fileName) => window.__previewParseGates[fileName].release(), candidate.artifacts[0].fileName);
    await until(snapshot, (value) => value.loadingSha === null, "The stale parse did not finish");
    assert.equal((await snapshot()).status, "ready", "Cancelling a parsed replacement must release its loading status while preserving the old model");
    assert.equal((await snapshot()).loadedRunId, home.runId, "The old parse must not attach even before the new base model starts loading");
    stateGates.delete(other.runId); stateGate.resolve(); await rendered(other.runId);
    await page.evaluate((source) => window.__candidatePreview.changeBase(source.runId, source), home.modelSource); await rendered(home.runId);
  });

  await step("a late completion cannot replace an explicitly selected model", async () => {
    await view(home);
    const candidate = prepare("late-selection"); await launch(candidate); await view(other); await complete(candidate);
    await until(snapshot, (value) => value.runs[candidate.candidateId].candidate.status === "ready", "The late candidate was not retained in the shell");
    await delay(150); await rendered(other.runId);
    assert.equal(requests.some((row) => row.name === `/api/artifacts/${candidate.artifacts[0].sha256}/bytes`), false);
  });

  await step("completion order cannot make an older candidate replace the latest requested candidate", async () => {
    await view(home);
    const older = prepare("older-request"), newer = prepare("newer-request");
    await launch(older); await launch(newer); await complete(newer); await rendered(newer.candidateId); await complete(older);
    await until(snapshot, (value) => value.runs[older.candidateId].candidate.status === "ready", "The older completion was not read");
    await delay(150); await rendered(newer.candidateId);
    assert.equal(requests.some((row) => row.name === `/api/artifacts/${older.artifacts[0].sha256}/bytes`), false);
  });

  await step("Program candidates are observed and shown with the conversation closed", async () => {
    await view(home); await conversation(false);
    await page.evaluate(() => window.__candidatePreview.readProgram());
    await until(snapshot, (value) => value.program !== null, "The actual Program read did not finish");
    const candidate = prepare("program-result"); candidate.proposalId = null; nextProgram = candidate;
    await page.evaluate(() => window.__candidatePreview.program(window.__candidatePreview.snapshot.program, false));
    await until(snapshot, (value) => value.runs[candidate.candidateId]?.job.status === "ready", "Program returned a job that was not watched");
    await complete(candidate); await rendered(candidate.candidateId);
    assert.equal((await snapshot()).candidateEntries.find((row) => row.candidateId === candidate.candidateId).proposalId, null);
  });

  await step("changing editing Stage blocks a candidate from the previous context", async () => {
    await view(home);
    const candidate = prepare("late-stage"); await launch(candidate);
    await page.evaluate((source) => window.__candidatePreview.changeBase(source.runId, source), other.modelSource);
    await rendered(other.runId); await complete(candidate);
    await until(snapshot, (value) => value.runs[candidate.candidateId].candidate.status === "ready", "The old Stage result did not finish");
    await delay(150); await rendered(other.runId);
    assert.equal((await snapshot()).editingRunId, other.runId);
  });

  await step("switching projects blocks the previous project's late candidate", async () => {
    await page.evaluate((source) => window.__candidatePreview.changeBase(source.runId, source), home.modelSource); await rendered(home.runId);
    const candidate = prepare("late-project"); await launch(candidate);
    projectId = "different-project"; currentHome = makeArtifact("other-project-home", 12); allArtifacts.push(currentHome);
    await page.evaluate(() => window.__candidatePreview.reload()); await rendered(currentHome.runId); await complete(candidate);
    await until(snapshot, (value) => value.runs[candidate.candidateId].candidate.status === "ready", "The previous project job did not finish");
    await delay(150); await rendered(currentHome.runId);
    assert.equal((await snapshot()).projectId, projectId);
  });

  assert.deepEqual(errors, []);
  assert.equal(requests.some((row) => /\/(decision|accept|issue)(\/|$)/.test(row.name)), false, "Preview must never accept or issue a design");
  historyEnabled = true;
  await page.reload({ waitUntil: "domcontentloaded" });
  await rendered(currentHome.runId);
  await page.locator('button[aria-controls="stage-versions-panel"]').click();
  let s0, s1, historyA, historyB, historyC;

  await step("S0 requires one explicit confirmation and cold reopen restores the committed head", async () => {
    assert.equal(stages.size, 0);
    assert.equal(await page.getByText("已有模型与历史运行 · 尚未归入 Stage", { exact: true }).count(), 1);
    await page.getByRole("button", { name: "确认当前模型为 S0", exact: true }).click();
    await until(snapshot, (value) => value.history?.stages.length === 1, "S0 was not confirmed");
    s0 = [...stages.values()][0];
    assert.deepEqual(s0.modelSource, currentHome.modelSource);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(currentHome.runId);
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef);
    await page.locator('button[aria-controls="stage-versions-panel"]').click();
  });

  await step("a preview becomes the editable candidate context while retaining its actual source Stage", async () => {
    historyA = prepare("history-a"); candidateBases.set(historyA.candidateId, s0.stageRef);
    await launch(historyA); await complete(historyA); await rendered(historyA.candidateId);
    await until(snapshot, (value) => value.editingRunId === historyA.candidateId, "Preview did not become the candidate editing context");
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef);
    await page.evaluate(() => window.__candidatePreview.propose("record candidate context"));
    const intent = requests.findLast((row) => row.name === "/api/intents");
    assert.equal(intent.body.sourceRunId, historyA.candidateId);
    assert.equal(intent.body.sourceStageRef, s0.stageRef);
    assert.deepEqual(intent.body.modelSource, historyA.artifacts[0].modelSource);
    assert.equal(stages.size, 1); assert.equal(branches.size, 1);
  });

  await step("failed acceptance preserves the preview; successful acceptance appends S1 on main", async () => {
    const accept = page.getByRole("button", { name: "接受为下一 Stage", exact: true });
    await accept.waitFor(); acceptFailure = true; await accept.click();
    await until(snapshot, (value) => value.historyError?.includes("Candidate kept"), "Acceptance failure was hidden");
    await rendered(historyA.candidateId); assert.equal(stages.size, 1);
    acceptFailure = false; await accept.click();
    await until(snapshot, (value) => value.history?.stages.length === 2, "Accepted candidate did not become S1");
    s1 = historyDto().stages.at(-1);
    assert.equal(s1.parentStageRef, s0.stageRef); assert.equal(branches.size, 1);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(historyA.candidateId);
    assert.equal((await snapshot()).sourceStageRef, s1.stageRef);
    await page.locator('button[aria-controls="stage-versions-panel"]').click();
  });

  await step("historical Stage selection changes the real editing source and branch creation is explicit", async () => {
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click();
    await rendered(currentHome.runId);
    await page.evaluate(() => window.__candidatePreview.propose("record historical context"));
    const intent = requests.findLast((row) => row.name === "/api/intents");
    assert.equal(intent.body.sourceRunId, currentHome.runId); assert.equal(intent.body.sourceStageRef, s0.stageRef);
    assert.equal(branches.size, 1);
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "从这里新建分支" }).click();
    await page.getByPlaceholder("alternate-layout").fill("alternate");
    await page.getByRole("button", { name: "创建分支", exact: true }).click();
    await until(snapshot, (value) => value.history?.branchId === "alternate", "Named branch was not selected");
    assert.equal(branches.get("main").headStageRef, s1.stageRef);
    assert.equal(branches.get("alternate").headStageRef, s0.stageRef);
  });

  await step("cold candidate recovery and same-Stage combination reuse the normal preview lifecycle", async () => {
    historyB = prepare("history-b"); historyC = prepare("history-c");
    for (const candidate of [historyB, historyC]) { candidateBases.set(candidate.candidateId, s0.stageRef); jobs.get(candidate.jobId).status = "succeeded"; allArtifacts.push(...candidate.artifacts); }
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(historyA.candidateId);
    await page.locator('button[aria-controls="stage-versions-panel"]').click();
    await page.getByRole("combobox", { name: "Branch", exact: true }).selectOption("alternate"); await rendered(currentHome.runId);
    await page.locator('[data-preview-candidate="history-b"]').waitFor();
    assert.equal(await page.locator('[data-preview-candidate="history-a"]').count(), 0, "A model accepted on main must not become an unaccepted candidate on another branch");
    assert.equal((await snapshot()).candidateEntries.length, 0);
    await page.locator('[data-preview-candidate="history-b"] input[type="checkbox"]').check();
    await page.locator('[data-preview-candidate="history-c"] input[type="checkbox"]').check();
    nextCombined = prepare("history-combined"); candidateBases.set(nextCombined.candidateId, s0.stageRef); const combined = nextCombined;
    await page.getByRole("button", { name: "合并选中候选并预览", exact: true }).click();
    await until(snapshot, (value) => value.runs[combined.candidateId]?.job.status === "ready", "Combined job was not observed");
    const request = requests.findLast((row) => row.name === "/api/candidates/combine");
    assert.deepEqual(request.body.candidateIds.sort(), [historyB.candidateId, historyC.candidateId]);
    await complete(combined); await rendered(combined.candidateId);
    await until(snapshot, (value) => value.editingRunId === combined.candidateId, "Combined preview was not editable");
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef); assert.equal(branches.size, 2);
  });

  await step("generated elevation opens the returned immutable revision with no chat expansion", async () => {
    await page.getByRole("combobox", { name: "Branch", exact: true }).selectOption("main"); await rendered(historyA.candidateId);
    await page.getByRole("button", { name: "生成立面", exact: true }).click();
    await page.locator('.document-workspace:not([aria-hidden="true"]) .document-viewport[data-ready="true"]').waitFor();
    const value = await snapshot();
    assert.equal(value.documentView.open, true); assert.equal(value.documentView.revisionRef, lastDrawing.revisionRef);
    assert.equal(await page.locator("#conversation-panel").count(), 0);
    assert.equal(requests.findLast((row) => row.name === "/api/drawings/elevations").body.sourceStageRef, s1.stageRef);
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, lastDrawing.revisionRef);
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), lastDrawing.revisionRef);
  });

  await step("failed drawing autosave blocks a Stage switch and a retry preserves the comment", async () => {
    annotationFailure = true;
    await page.locator("#document-comment").fill("Keep the terrace line.");
    await until(async () => page.locator(".document-error").count(), (value) => value > 0, "Autosave failure did not remain visible");
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click();
    await delay(150); await rendered(historyA.candidateId);
    assert.equal((await snapshot()).documentView.revisionRef, lastDrawing.revisionRef);
    assert.equal(await page.locator("#document-comment").inputValue(), "Keep the terrace line.");
    annotationFailure = false;
    await page.locator(".document-error button").first().click();
    await until(async () => page.locator(".document-error").count(), (value) => value === 0, "Retry did not save the retained draft");
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click();
    await rendered(currentHome.runId);
    assert.equal((await snapshot()).documentView.runId, currentHome.runId);
    assert.ok([...annotations.values()].some((saved) => saved.comment === "Keep the terrace line."));
  });

  await step("opening a Stage defaults to its newest matching generated drawing, independent of list order", async () => {
    const old = { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-dated-old`, generatedAt: "2026-09-09T08:00:00.000Z" };
    const latest = { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-dated-new`, sourceStageRef: "archflow-project://same-model/another-branch/stage",
      generatedAt: "2026-09-09T09:00:00.000Z" };
    const mismatched = { ...lastDrawing, modelSource: { ...lastDrawing.modelSource, assetSha256: "f".repeat(64) },
      revisionRef: `${lastDrawing.revisionRef}-different-model`, generatedAt: "2026-09-09T10:00:00.000Z" };
    // The first row is older; the globally newest row belongs to another exact model.
    documents.splice(0, documents.length, old, latest, mismatched);
    await page.locator('[data-design-stage="S1"]').getByRole("button", { name: "S1 · 当前提交", exact: true }).click();
    await rendered(historyA.candidateId);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), latest.revisionRef);
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, latest.revisionRef);
  });

  await step("an only drawing from a different model stays unselected, and undated matching revisions are not guessed", async () => {
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click(); await rendered(currentHome.runId);
    documents.splice(0, documents.length, { ...lastDrawing, modelSource: { ...lastDrawing.modelSource, assetSha256: "e".repeat(64) }, generatedAt: "2026-09-09T11:00:00.000Z" });
    const before = requests.filter((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).length;
    await page.locator('[data-design-stage="S1"]').getByRole("button", { name: "S1 · 当前提交", exact: true }).click(); await rendered(historyA.candidateId);
    await page.getByRole("combobox", { name: "Source document", exact: true }).waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), "");
    assert.equal(await page.locator(".document-viewport").count(), 0);
    assert.equal(requests.filter((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).length, before);
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click(); await rendered(currentHome.runId);
    documents.splice(0, documents.length, { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-undated-a`, generatedAt: null },
      { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-undated-b`, generatedAt: null });
    await page.locator('[data-design-stage="S1"]').getByRole("button", { name: "S1 · 当前提交", exact: true }).click(); await rendered(historyA.candidateId);
    await page.getByRole("combobox", { name: "Source document", exact: true }).waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), "");
    assert.equal(await page.locator(".document-viewport").count(), 0);
  });

  await step("default and explicit Stage selection use the pinned SHA despite another working option in the same run", async () => {
    const pinned = historyA.artifacts[0];
    const alternate = { ...makeArtifact(pinned.runId, 27), artifactId: "alternate-same-run-B", fileName: "same-run-selected-B.3dm" };
    const ungrouped = { ...makeArtifact(pinned.runId, 28), artifactId: "alternate-same-run-C", fileName: "same-run-unselected-C.3dm" };
    allArtifacts.push(alternate, ungrouped);
    workingCopies.push({ projectId, groupId: "same-run-options", label: "Same-run models", stageId: "fixture-seat", commonBase: pinned.modelSource,
      scope: ["fixture-room"], options: [{ id: "A", label: "Pinned A", modelSource: pinned.modelSource },
        { id: "B", label: "Selected B", modelSource: alternate.modelSource }], selectedOptionId: "B", revisionSha256: digest("working-copy-v1") });
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(pinned.runId);
    assert.deepEqual((await snapshot()).loadedModelSource, pinned.modelSource);
    assert.deepEqual((await snapshot()).editingModelSource, pinned.modelSource);
    assert.equal(workingCopies[0].selectedOptionId, "B", "Cold Stage restore must not change the saved working option");
    await page.locator('button[aria-controls="stage-versions-panel"]').click();
    await view(alternate);
    const stageButton = page.locator('[data-design-stage="S1"]').getByRole("button", { name: "S1 · 当前提交", exact: true });
    assert.equal(await stageButton.getAttribute("aria-pressed"), "false", "Sharing the Stage run is insufficient to claim its pinned model is shown");
    await page.evaluate(() => window.__candidatePreview.changeBase(null)); await rendered(pinned.runId);
    assert.deepEqual((await snapshot()).loadedModelSource, pinned.modelSource);
    assert.deepEqual((await snapshot()).editingModelSource, pinned.modelSource);
    await view(alternate); await stageButton.click(); await rendered(pinned.runId);
    assert.deepEqual((await snapshot()).editingModelSource, pinned.modelSource);
    await until(async () => page.locator('[data-preview-candidate="history-a"]').count(), (value) => value === 0,
      "Other files from an accepted candidate run must not become new candidates");
    const legacy = page.locator("details").filter({ has: page.getByText("已有模型与历史运行 · 尚未归入 Stage", { exact: true }) });
    await legacy.locator("summary").first().click();
    assert.ok(await legacy.getByText(ungrouped.fileName, { exact: true }).count() > 0,
      "An ungrouped file sharing an accepted Stage's run must remain available as a legacy model");
  });

  await step("a retained Stage remains usable after canonical HEAD advances while an unbound old run remains blocked", async () => {
    published.version = 1; published.stateSha256 = "9".repeat(64);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(historyA.candidateId);
    assert.equal((await snapshot()).sourceStageRef, s1.stageRef);
    assert.deepEqual((await snapshot()).editingModelSource, historyA.artifacts[0].modelSource);
    await page.evaluate(() => window.__candidatePreview.propose("edit the retained Stage on its own exact base"));
    assert.equal(requests.findLast((row) => row.name === "/api/intents").body.sourceStageRef, s1.stageRef);
    await page.evaluate(() => window.__candidatePreview.reload("legacy-unbound-old-run"));
    assert.equal((await snapshot()).baseError, "EDITING_BASE_UNAVAILABLE");
    assert.equal((await snapshot()).sourceStageRef, s1.stageRef);
    await rendered(historyA.candidateId);
  });

  await step("identical PNG revisions keep separate drafts and submit the explicitly selected drawing source", async () => {
    const source = historyA.artifacts[0].modelSource;
    const older = { ...lastDrawing, modelSource: source, revisionRef: `${lastDrawing.revisionRef}-same-png-old`, generatedAt: "2026-09-09T08:00:00.000Z" };
    const newer = { ...lastDrawing, modelSource: workingCopies[0].options[1].modelSource, sourceStageRef: null,
      revisionRef: `${lastDrawing.revisionRef}-same-png-new`, generatedAt: "2026-09-09T09:00:00.000Z" };
    documents.splice(0, documents.length, newer, older);
    await page.locator(".stage-mode-switch").getByRole("button", { name: "MonkeyDiagram · Drawings", exact: true }).click();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    const picker = page.getByRole("combobox", { name: "Source document", exact: true });
    assert.equal(await picker.inputValue(), older.revisionRef, "A newer drawing of another exact model must not become this Stage's default");
    await page.locator("#document-comment").fill("Keep the older drawing note.");
    await until(() => [...annotations.values()].some((item) => item.drawingRevisionRef === older.revisionRef && item.comment === "Keep the older drawing note."), Boolean, "The old drawing draft was not saved to its own revision");
    await picker.selectOption(newer.revisionRef);
    await until(async () => page.locator("#document-comment").isEnabled(), Boolean, "The newer revision draft did not load");
    assert.equal(await page.locator("#document-comment").inputValue(), "", "Same PNG bytes must not share another revision's draft");
    await page.locator("#document-comment").fill("Separate newer drawing note.");
    await until(() => [...annotations.values()].some((item) => item.drawingRevisionRef === newer.revisionRef && item.comment === "Separate newer drawing note."), Boolean, "The new drawing draft was not saved separately");
    await picker.selectOption(older.revisionRef);
    await until(async () => page.locator("#document-comment").inputValue(), (value) => value === "Keep the older drawing note.", "Returning to the old revision lost its own note");
    await page.getByRole("button", { name: "Submit page note", exact: true }).click();
    await until(() => requests.findLast((row) => row.name === "/api/intents" && row.body?.utterance === "Keep the older drawing note."), Boolean, "The old drawing never reached the real App intent boundary");
    const sent = requests.findLast((row) => row.name === "/api/intents").body;
    assert.deepEqual(sent.modelSource, source);
    assert.equal(sent.sourceStageRef, s1.stageRef);
    assert.equal(sent.documentAnnotations[0].drawingRevisionRef, older.revisionRef);
    assert.equal(sent.documentVisuals[0].drawingRevisionRef, older.revisionRef);
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, older.revisionRef);
  });

  assert.deepEqual(errors, []);
  console.log(`Passed ${passed.length} candidate preview scenarios; actual 3DM files parsed in an isolated headless browser.`);
} finally {
  for (const gate of [...validationGates.values(), ...modelGates.values(), ...stateGates.values()]) gate.resolve();
  await browser?.close();
  if (http.listening) await new Promise((resolve, reject) => http.close((error) => error ? reject(error) : resolve()));
  await vite?.close();
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("monkeyarch-candidate-preview-test-"));
  await rm(cacheDir, { recursive: true, force: true });
}
