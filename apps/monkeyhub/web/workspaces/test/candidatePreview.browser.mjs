import { workspaceFixture } from "./workspaceFixture.mjs";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

// Actual App, job observation and 3DM parser; every API response is in memory.
// No user service, project, browser profile or accepted design is touched.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
// Run the independent model timing cases without requiring the drawing/history UI tour.
const modelTimingOnly = process.argv.includes("--model-timing");
const candidateDeliveryOnly = process.argv.includes("--candidate-delivery");
// Viewing, Undo, Continue, Sync and an unsaved base choice, walked as one flow.
const viewBaseOnly = process.argv.includes("--view-base");
// The one reason every direct edit gives while the picture is not the editing base.
const VIEW_ONLY = "Viewing only. Choose “Continue from here”, or return to the editing base before making changes.";
const cacheDir = await mkdtemp(path.join(tmpdir(), "monkeyarch-candidate-preview-test-"));
const rhino = await rhino3dm();
const published = { version: 0, stateSha256: "1".repeat(64) };
const relations = { held: 0, violated: 0, unchecked: 0, heldFlag: true, fullyChecked: true };
const allArtifacts = [], models = new Map(), jobs = new Map(), candidates = new Map();
const workingCopies = [];
const parameterStates = new Map();
let nextLockCandidate = null, lockProposalGate = null;
const candidateStartQueues = new Map();
const requests = [], errors = [], passed = [], validationGates = new Map(), modelGates = new Map(), stateGates = new Map();
let projectId = "candidate-preview-fixture", artifactFailures = 0, seq = 0;
let historyEnabled = false, acceptFailure = false, annotationFailure = false, lastDrawing = null, nextCombined = null;
let monitorFailure = false, historyGate = null;
let diagnosticsEnabled = false, nextIntent = null, intentGate = null, documentGate = null, timingGate = null;
let projectionOnly = false;
// The server's working draft: the saved editing base, one retained local draft and
// the finished candidates listed for recovery, each written with the revision it was
// read at. As in the Studio API (GH-234 Q2), a finished candidate is only listed:
// `current` moves solely through an explicit PUT /api/working-draft.
let workingDraftEnabled = viewBaseOnly, nextDelete = null, failNextSelect = false;
const workingDraft = { revisionSha256: null, current: null, localDraft: null, listed: new Map() };
const workingDraftDto = () => ({ projectId, revisionSha256: workingDraft.revisionSha256, current: workingDraft.current,
  recovery: [...workingDraft.listed.values()].reverse(), saved: [], managedRunIds: [...workingDraft.listed.keys()].sort(),
  localDraft: workingDraft.localDraft });
function listFinishedCandidate(candidateId) {
  if (!workingDraftEnabled) return;
  workingDraft.listed.set(candidateId, { runId: candidateId, sourceStageRef: candidateBases.get(candidateId) ?? null,
    branchId: null, label: null, updatedAt: new Date().toISOString() });
  workingDraft.revisionSha256 = digest(JSON.stringify([workingDraft.revisionSha256, "candidate", candidateId]));
}
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
/** A drawing already registered against an exact model, as the project retains it. */
function registerDrawing(modelSource, sourceStageRef = null) {
  lastDrawing = { projectId, runId: modelSource.runId, assetSha256: digest(png), fileName: "front-elevation.png", mimeType: "image/png", sizeBytes: png.length,
    pageCount: 1, pages: [{ pageIndex: 0, width: 200, height: 150, rotation: 0 }], modelSource,
    drawingId: "front", revisionRef: `archflow-project://${projectId}/drawing/front/${documents.length}`, sourceStageRef, viewRecipe: { view: "front" } };
  documents.push({ ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-older` }, lastDrawing);
  return lastDrawing;
}
let vite, browser, page;
const http = createHttpServer();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function deferred() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
const digest = (text) => createHash("sha256").update(text).digest("hex");
const stateDigest = (run) => digest(`state:${run}`);
const artifactDto = ({ projectId: _owner, ...artifact }) => ({
  ...artifact, sourceStageRef: candidateBases.get(artifact.runId) ?? null,
});
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
    referenceRun: { ...binding().referenceRun, runId }, referenceRunSource: projectionOnly ? "none" : "fixture",
    referenceReceipt: null, matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: digest(`record:${runId}`), stateDigest: stateDigest(runId),
    activePhase: "stage-2", counts: { entities: 1, components: 1, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
    componentTree: [{ componentId: element.componentId, parentComponentId: null, semanticKind: "room", intent: "fixture", maturity: "candidate", revision: 1 }],
    componentTreeError: null, elements: [element], parameters: parameterStates.get(runId) ?? [], dependencyEdges: [], honesty: [], catalog: {
      components: [{ componentId: element.componentId, parentId: null, children: [], elementIds: [element.elementId], descendantElementIds: [element.elementId],
        capabilityCount: 1, states: ["editable"], objectCount: 1, unboundObjectCount: 0, closure: [] }],
      elements: [{ ...element, objectNames: [element.elementId], capabilities: [{ capabilityId: "height", elementId: element.elementId, key: "height", value: 0.2,
        valueType: "number", unit: null, bounds: null, source: "authored", confidence: 1, status: "editable", validatorRefs: [] }] }],
      objects: [{ name: element.elementId, componentId: element.componentId, producerOp: "floor", elementId: element.elementId, status: "bound", detail: "fixture" }],
      coverage: { objects: 1, bound: 1, unbound: 0, ambiguous: 0, unknownComponent: 0 }, inspectionRun: runId, honesty: [],
    } };
}
function prepare(id) {
  if (diagnosticsEnabled && historyEnabled) candidateBases.set(id, historyDto().stages[0].stageRef);
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
  listFinishedCandidate(candidate.candidateId);
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
const loadTimings = (runId) => requests.filter((row) => row.name === "/api/events/model-load" && row.body.runId === runId);
const diagnosticEvents = (phase) => requests.filter((row) => row.name === "/api/events/timing" && (!phase || row.body.phase === phase)).map((row) => row.body);
const latestDiagnostic = (phase) => diagnosticEvents(phase).at(-1);
async function finishedDiagnostic(phase, operationId, status = "succeeded") {
  return until(() => diagnosticEvents(phase).findLast((row) => row.operationId === operationId && row.status !== "running"),
    (row) => row?.status === status, `${phase} did not finish ${status} in ${operationId}`);
}
async function rootForCandidate(candidate) {
  const request = requests.findLast((row) => row.name === `/api/proposals/${candidate.proposalId}/candidate`);
  assert.ok(request?.headers["x-monkey-operation"]);
  return until(() => diagnosticEvents("design_edit").find((row) => row.operationId === request.headers["x-monkey-operation"]), Boolean, "Missing candidate operation root");
}
async function reportedLoad(artifact, status) {
  await until(() => loadTimings(artifact.runId), (rows) => rows.length === 1, `${artifact.runId} did not report exactly one finished model load`);
  const { body } = loadTimings(artifact.runId)[0];
  assert.equal(body.status, status);
  assert.equal(body.projectId, artifact.projectId);
  assert.equal(body.sourceRef, artifact.receiptRef);
  assert.ok(Number.isSafeInteger(body.durationMs) && body.durationMs >= 0);
  assert.ok(Date.parse(body.endedAt) >= Date.parse(body.startedAt));
  return body;
}
/** Open the view tools if a step has since opened the panel that closes them. */
async function openViewTools() {
  const toggle = page.locator('button[aria-controls="view-tools"]');
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
}

/** Open one registered drawing the way the Board hands a page to this workspace. */
async function openDrawingFromBoard(drawing, pageIndex = 0) {
  await page.evaluate((source) => { window.__candidateDocumentSource = source; },
    { runId: drawing.runId, assetSha256: drawing.assetSha256, pageIndex, revisionRef: drawing.revisionRef });
  await page.getByTestId("workspace-board").click();
  await page.getByRole("button", { name: "Open registered drawing", exact: true }).click();
}

/** Open the versions panel if a step has since opened the tools that close it. */
async function openVersions() {
  const toggle = page.locator('button[aria-controls="stage-versions-panel"]');
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  const retained = page.locator('#stage-versions-panel details').filter({ has: page.getByText("候选方案与已有历史", { exact: true }) });
  if (await retained.count() && await retained.getAttribute("open") === null) await retained.locator("summary").click();
}

async function rendered(id, fileName = `${id}.3dm`) {
  await until(snapshot, (value) => value.loadedRunId === id && value.loadedFileName === fileName && value.status === "ready" && value.loadingSha === null,
    `${id} did not finish parsing and become the displayed model`, 45_000);
}
async function diagnosticRendered(candidate) {
  const base = (await snapshot()).editingRunId;
  await rendered(candidate.candidateId);
  // GH-234 Q1/Q2: a finished candidate is shown automatically but becomes the
  // editing base only through the architect's explicit Continue.
  await delay(300);
  const after = await snapshot();
  assert.equal(after.editingRunId, base, `${candidate.candidateId} became the editing base without Continue`);
  assert.equal(after.changingBase, false);
}
/** The architect's explicit "Continue from here" on the model on screen. */
async function continueFromViewed(runId) {
  await page.locator(".stage__foot .editing-base").getByRole("button", { name: "Continue from here", exact: true }).click();
  await until(snapshot, (value) => value.editingRunId === runId && !value.changingBase, `Continue did not make ${runId} the editing base`);
}
/**
 * #302: a Stage row in Versions only shows that Stage, read-only; the editing-base
 * row's Continue from here is what makes it the base. `label` is the row's button name.
 */
async function stageAsBase(stage, label, runId) {
  const before = await snapshot(), writes = requests.filter((row) => row.method !== "GET" && !row.name.startsWith("/api/events/")).length;
  await openVersions();
  await page.locator(`[data-design-stage="${stage}"]`).getByRole("button", { name: label, exact: true }).click();
  await rendered(runId);
  const viewed = await snapshot();
  assert.equal(requests.filter((row) => row.method !== "GET" && !row.name.startsWith("/api/events/")).length, writes,
    `Viewing ${stage} wrote to the project`);
  assert.equal(viewed.editingRunId, before.editingRunId, `Viewing ${stage} moved the editing base`);
  assert.equal(viewed.sourceStageRef, before.sourceStageRef, `Viewing ${stage} moved the editing Stage`);
  if (viewed.editingModelSource?.runId === runId && viewed.editingModelSource.assetSha256 === viewed.loadedModelSource?.assetSha256) return;
  await continueFromViewed(runId);
}
async function view(artifact) {
  await page.evaluate((value) => window.__candidatePreview.view(value), artifactDto(artifact));
  await rendered(artifact.runId, artifact.fileName);
}
async function launch(candidate) {
  await page.evaluate((id) => window.__candidatePreview.run(id), candidate.proposalId);
  await until(snapshot, (value) => value.runs[candidate.candidateId]?.job.status === "ready", "The shell did not begin observing the job");
}
async function step(name, action) { await action(); passed.push(name); console.log(`PASS ${name}`); }

try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir, publicDir: "../.generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [workspaceFixture(), {
      name: "observe-actual-candidate-shell", enforce: "pre",
      transform(source, id) {
        const modulePath = id.split("?")[0].replaceAll("\\", "/");
        if ((viewBaseOnly || process.env.BOARD_NOTE_SCREENSHOTS) && modulePath === `${webRoot.replaceAll("\\", "/")}/test/workspace-fixture.tsx`) {
          // The footer layout is checked against the real Hub theme.
          return { code: `import "/@fs/${path.resolve(webRoot, "../../../shared-web/src/base.css").replaceAll("\\", "/")}";\n${source}`, map: null };
        }
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/workspaces/monkeyboard/Board.tsx`) {
          return { code: `export default function Board({onOpenDocument, onSubmit}) {
            return <><button onClick={() => onOpenDocument({source: window.__candidateDocumentSource,
              view:{scrollX:0,scrollY:0,zoom:1,selectedElementIds:{},selectedGroupIds:{}}})}>Open registered drawing</button>
              <button onClick={() => onSubmit(window.__boardNote)}>Send board note</button></>;
          }`, map: null };
        }
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/workspaces/monkeydiagram/DocumentCanvas.tsx`) {
          const marker = "      const canvas = canvasRef.current;";
          assert.equal(source.split(marker).length, 2);
          return { code: source.replace(marker, `
            const gate = (window as unknown as { __documentRenderGate?: { waiting: boolean; promise: Promise<void> } }).__documentRenderGate;
            if (gate) { gate.waiting = true; await gate.promise; }
            if (stopped) return;
` + marker), map: null };
        }
        if (modulePath === `${webRoot.replaceAll("\\", "/")}/src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx`) {
          const marker = "      let models = parsed";
          assert.equal(source.split(marker).length, 2);
          return { code: source.replace(marker, `
            const held = (window as unknown as { __previewParseGates: Record<string, { waiting: boolean; promise: Promise<void> }> }).__previewParseGates[files[0].name];
            if (held) { held.waiting = true; await held.promise; }
` + marker), map: null };
        }
        if (modulePath !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
        if (candidateDeliveryOnly || viewBaseOnly) {
          // The Hub chat's "Open this candidate on the right" names one run to show.
          source = source.replace("export default function App(", "function CandidateApp(") + `
            export default function DeliveryFixture(props: Parameters<typeof CandidateApp>[0]) {
              const [request, setRequest] = useState({ runId: props.initialRunId, refreshKey: 0 });
              (window as unknown as { __openCandidate: unknown }).__openCandidate = (runId: string) =>
                setRequest(current => ({ runId, refreshKey: current.refreshKey + 1 }));
              return <CandidateApp {...props} initialRunId={request.runId} refreshKey={request.refreshKey} />;
            }
          `;
        }
        const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
        assert.equal(source.split(marker).length, 2);
        return { code: source.replace(marker, marker + `
          (window as unknown as { __candidatePreview: unknown }).__candidatePreview = {
            camera: () => viewportRef.current?.camera(), run: runCandidate, changeBase: changeEditingBase, reload, propose,
            view: (artifact: ProjectArtifactDto) => { manualLoadRef.current = true; return loadArtifactIntoViewer(artifact, artifact.fileName); },
            edit: commitLocalCommand, pick: resolvePick,
            snapshot: { loadedRunId: loadedArtifact?.runId, loadedFileName: loadedArtifact?.fileName, status: viewerStatus, loadingSha: artifactLoadingSha,
              baseNotice, persistenceFailed, picked: picked?.elementId ?? null,
              projectId: project?.projectId, editingRunId: projection?.referenceRun.runId, changingBase, runs: candidateRuns.runs,
              sourceStageRef: projection?.sourceStageRef, history: designHistory, historyError, documentView,
              loadedModelSource, editingModelSource, baseError: baseError?.code ?? null,
              draftReady: draftProjection !== null, localCommands: draftSnapshot?.commands ?? [],
              artifactShas: artifacts.status === "ready" ? artifacts.value.artifacts.map((row) => row.sha256) : [],
              artifacts: artifacts.status === "ready" ? artifacts.value.artifacts.map((row) => row.runId) : [],
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
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : { channel: "chrome" }) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await context.addInitScript(({ projectId, runId }) => {
    if (!localStorage.getItem("archflow-studio.user-preferences")) {
      localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1,
        eventStreamVisible: false, developerMode: false, editingBases: { [JSON.stringify(["", projectId])]: runId } }));
    }
    const sources = new Set();
    class FakeEventSource {
      constructor() { this.listeners = new Map(); sources.add(this); queueMicrotask(() => this.onopen?.()); }
      addEventListener(type, listener) { this.listeners.set(type, listener); }
      removeEventListener(type) { this.listeners.delete(type); }
      close() { sources.delete(this); }
    }
    window.EventSource = FakeEventSource;
    // A promise nobody handles is a failure of the action that made it.
    window.__unhandled = [];
    window.addEventListener("unhandledrejection", (event) => window.__unhandled.push(String(event.reason?.message ?? event.reason)));
    window.__previewParseGates = {};
    window.__previewEvents = { emit(value) { for (const source of sources) source.listeners.get(value.type)?.({ data: JSON.stringify(value) }); } };
  }, { projectId, runId: home.runId });
  page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error" && message.text().includes("Encountered two children with the same key")) errors.push(message.text()); });
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), name = url.pathname;
    requests.push({ method, name, headers: request.headers(), query: Object.fromEntries(url.searchParams), body: method === "POST" || method === "PUT" ? request.postDataJSON() : null });
    try {
      assert.equal(url.origin, origin);
      const json = (body, status = 200) => route.fulfill({ status, json: body });
      if (method === "GET") {
        if (name === "/api/protocol") return await json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local",
          capabilities: ["working-copies", "model-annotations", "events", "operation-timing", ...(diagnosticsEnabled ? ["operation-diagnostics"] : []), ...(historyEnabled ? ["design-history", "drawing-elevations", "document-visual-input"] : []),
            ...(workingDraftEnabled ? ["working-draft"] : [])] });
        if (name === "/api/working-draft") return await json(workingDraftDto());
        if (name === "/api/project") return await json(binding());
        if (name === "/api/drawings/styles") return await json({ styles: [] });
        if (name === "/api/state") {
          const runId = projectionOnly ? "studio-projection" : url.searchParams.get("run") ?? currentHome.runId;
          const gate = stateGates.get(runId); if (gate) { gate.requested = true; await gate.promise; }
          const value = projection(runId, url.searchParams.get("sourceStageRef"));
          if (projectionOnly) value.sourceStageRef = null;
          return await json(value);
        }
        if (name === "/api/state/frame") {
          // While a drawing is mounted the record's levels are read for the current
          // local draft source, which is not necessarily the open document's own model
          // source: exactly one such run is named. This record holds one floor
          // element and declares no level or axis rows, so the frame is empty.
          assert.deepEqual([...url.searchParams.keys()].filter((key) => key !== "run"), [],
            `The frame read carries only its source run: ${url.search}`);
          assert.ok(url.searchParams.getAll("run").length <= 1, `The frame read names one source run: ${url.search}`);
          const requested = url.searchParams.get("run");
          const ownedHere = (artifact) => artifact.projectId === projectId && artifact.runId === requested;
          if (projectionOnly) {
            assert.ok(requested === null || requested === "studio-projection",
              `A project with no retained run must not name one in its frame read: ${requested}`);
          } else {
            assert.ok(requested !== null, "The frame read must name the current local draft source run");
            assert.ok(allArtifacts.some(ownedHere) ||
              [...candidates.values()].some((candidate) => candidate.artifacts.some(ownedHere)),
              `The frame read must name a source this project retains: ${requested}`);
          }
          return await json({ levels: [], axes: [], honesty: [] });
        }
        if (name === "/api/design-history") {
          const branchId = url.searchParams.get("branchId") ?? "main", gate = historyGate;
          if (gate?.branchId === branchId) {
            gate.requested = true; await gate.promise;
            if (gate.failed) return await json({ code: "HISTORY_UNAVAILABLE", detail: "Other branch history unavailable." }, 503);
          }
          return await json(historyDto(branchId));
        }
        if (name === "/api/documents") {
  const runId = url.searchParams.get("runId");
  return await json({ projectId, runId, documents: runId === null ? documents : documents.filter((doc) => doc.runId === runId) });
}
        if (name === "/api/document-comments") return await json({ projectId, runId: url.searchParams.get("runId"), comments: [] });
        if (name === "/api/document-annotations") {
          const runId = url.searchParams.get("runId"), assetSha256 = url.searchParams.get("assetSha256"), pageIndex = Number(url.searchParams.get("pageIndex"));
          const drawingRevisionRef = url.searchParams.get("drawingRevisionRef");
          return await json(annotations.get(`${runId}:${assetSha256}:${pageIndex}:${drawingRevisionRef ?? ""}`) ?? {
            projectId, runId, assetSha256, pageIndex, drawingRevisionRef, revisionSha256: null, annotations: [], comment: "" });
        }
        if (/^\/api\/documents\/[^/]+\/bytes$/.test(name)) {
          if (documentGate) { documentGate.requested = true; await documentGate.promise; }
          return await route.fulfill({ status: 200, body: png, contentType: "image/png" });
        }
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
      }
      if (method === "PUT" && name === "/api/document-annotations") {
        if (annotationFailure) return await json({ code: "SAVE_UNAVAILABLE", detail: "Save failed; keep this page open." }, 503);
        const body = request.postDataJSON();
        const result = { ...body, revisionSha256: digest(JSON.stringify(body)) }; annotations.set(`${body.runId}:${body.assetSha256}:${body.pageIndex}:${body.drawingRevisionRef ?? ""}`, result);
        return await json(result);
      }
      if (method === "PUT" && (name === "/api/working-draft" || name === "/api/working-draft/local")) {
        const body = request.postDataJSON();
        assert.ok(workingDraftEnabled); assert.equal(body.projectId, projectId);
        if (body.baseRevisionSha256 !== workingDraft.revisionSha256) {
          return await json({ code: "WORKING_DRAFT_CONFLICT", detail: "The working draft changed; read it again." }, 409);
        }
        if (name === "/api/working-draft" && failNextSelect) {
          failNextSelect = false;
          return await json({ code: "WORKING_DRAFT_UNAVAILABLE", detail: "The working position could not be saved." }, 503);
        }
        if (name === "/api/working-draft") {
          workingDraft.current = body.runId === null ? null
            : { runId: body.runId, sourceStageRef: null, branchId: body.branchId ?? null, updatedAt: new Date().toISOString() };
        } else {
          workingDraft.localDraft = body.draft === null ? null : { ...body.draft, updatedAt: new Date().toISOString() };
        }
        workingDraft.revisionSha256 = digest(JSON.stringify([workingDraft.revisionSha256, name, body]));
        return await json(workingDraftDto());
      }
      if (method === "POST" && name === "/api/proposals/delete") {
        // Sync's one delete command; its candidate is the one the step prepared.
        const body = request.postDataJSON(), candidate = nextDelete;
        assert.ok(candidate, "Sync was not expected here"); nextDelete = null;
        assert.equal(body.projectId, projectId);
        return await json({ proposalId: candidate.proposalId, status: "proposed", modelSource: null, sourceRunId: body.sourceRunId,
          sourceStageRef: body.sourceStageRef ?? null, baseStateDigest: body.stateDigest, recordDigest: digest(`record:${body.sourceRunId}`),
          target: { componentId: "fixture-room", elementId: body.elementId, ref: `entity:${body.elementId}`, key: null },
          change: { kind: "edit_components", summary: "delete", kept: [], edits: { entities: [], parameters: [], relations: [], removeEntityIds: [body.elementId], removeParameterKeys: [], removeRelationIds: [] }, changes: [] },
          protected: [], decisionOperator: null,
          impact: { direct: [], propagated: [], protected: [], conflicts: [], locks: [], honesty: [], unknownCoverage: { count: 0, componentIds: [], parameterIds: [] } },
          utterance: "delete", persistence: "fixture", createdAt: new Date().toISOString() }, 201);
      }
      if (method === "POST" && name === "/api/pick/resolve") {
        const body = request.postDataJSON();
        return await json({ componentId: "fixture-room", elementId: body.objectName, status: "resolved", sourceState: "current", detail: "fixture pick" });
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
      if (method === "POST" && (name === "/api/events/model-load" || name === "/api/events/timing")) {
        if (name === "/api/events/timing" && request.postDataJSON().status === "running" && timingGate) await timingGate.promise;
        return await json(monitorFailure ? { code: "MONITOR_UNAVAILABLE", detail: "Diagnostic write unavailable." } : { recorded: true }, monitorFailure ? 503 : 200);
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
      if (method === "POST" && name === "/api/intents") {
        if (intentGate) await intentGate.promise;
        if (!nextIntent) return await json({ code: "UNSUPPORTED_REQUEST", detail: "Fixture records the source context." }, 422);
        const candidate = nextIntent; nextIntent = null; const body = request.postDataJSON();
        return await json({ outcome: "COMPILED", agent: { provider: "codex", model: "fixture", compiledUtterance: body.utterance,
          why: "", latencyMs: 1, promptSha256: null }, gestures: [], proposal: {
          proposalId: candidate.proposalId, status: "proposed", modelSource: body.modelSource, sourceRunId: body.sourceRunId, sourceStageRef: body.sourceStageRef,
          baseStateDigest: body.stateDigest, recordDigest: digest("fixture-record"),
          target: { componentId: "fixture-room", elementId: "fixture-floor", ref: "entity:fixture-room", key: "height" },
          change: { kind: "set_scalar", old: 0.2, new: 0.3, unit: null }, protected: [], decisionOperator: null,
          impact: { direct: [], propagated: [], protected: [], conflicts: [], locks: [], honesty: [], unknownCoverage: { count: 0, componentIds: [], parameterIds: [] } },
          utterance: body.utterance, persistence: "fixture", createdAt: new Date().toISOString(),
        }, pendingIntent: null, timings: { totalMs: 1, agentMs: 1, proposalMs: 0 } }, 201);
      }
      if (method === "POST" && name === "/api/proposals/parameter-locks") {
        const body = request.postDataJSON(), candidate = nextLockCandidate;
        assert.ok(candidate); nextLockCandidate = null;
        if (lockProposalGate) { lockProposalGate.requested = true; await lockProposalGate.promise; }
        assert.equal(body.projectId, projectId);
        assert.equal(body.stateDigest, stateDigest(body.sourceRunId));
        const parameters = (parameterStates.get(body.sourceRunId) ?? []).map((parameter) => ({ ...parameter,
          lockAuthority: body.parameterKeys.includes(parameter.key) ? (body.action === "lock" ? "fixture-user" : null) : parameter.lockAuthority }));
        parameterStates.set(candidate.candidateId, parameters);
        candidateBases.set(candidate.candidateId, body.sourceStageRef ?? null);
        return await json({ proposalId: candidate.proposalId, status: "proposed", modelSource: null,
          sourceRunId: body.sourceRunId, sourceStageRef: body.sourceStageRef, baseStateDigest: body.stateDigest,
          recordDigest: digest(`record:${body.sourceRunId}`),
          target: { componentId: "fixture-room", elementId: "fixture-floor", ref: "parameter:height", key: "height" },
          change: { kind: "edit_components", summary: `${body.action} parameter`, kept: [], edits: { entities: [], parameters: [], relations: [], removeEntityIds: [], removeParameterKeys: [], removeRelationIds: [] }, changes: [] },
          protected: [], decisionOperator: null,
          impact: { direct: [], propagated: [], protected: [], conflicts: [], locks: [], honesty: [], unknownCoverage: { count: 0, componentIds: [], parameterIds: [] } },
          utterance: `${body.action} parameter`, persistence: "fixture", createdAt: new Date().toISOString() }, 201);
      }
      if (method === "POST" && name === "/api/model-assets") {
        // A dropped or chosen local file is retained as an external model of its
        // own upload run, as the runtime registers it: no design state, no source.
        const body = request.postDataJSON(); assert.equal(body.projectId, projectId);
        const bytes = Buffer.from(body.contentBase64, "base64"), sha256 = digest(bytes);
        models.set(sha256, bytes);
        const imported = { artifactId: sha256, projectId, runId: `model-upload-${sha256}`, stageId: null, seatId: null,
          fileName: body.fileName, sha256, sizeBytes: bytes.length, available: true, unavailableReason: null,
          lengthUnit: "meters", programRef: null, programDigest: null, designStateDigest: null, receiptRef: "fixture-upload",
          format: "3dm", representation: "external", modelSource: null };
        if (!allArtifacts.some((row) => row.runId === imported.runId)) allArtifacts.push(imported);
        return await json(artifactDto(imported), 201);
      }
      if (method === "POST" && name === "/api/candidates/combine") {
        assert.ok(nextCombined); const candidate = nextCombined; nextCombined = null;
        return await json({ candidateId: candidate.candidateId, jobId: candidate.jobId, status: "running" }, 202);
      }
      if (method === "POST" && /^\/api\/proposals\/.+\/candidate$/.test(name)) {
        const id = name.split("/")[3];
        const candidate = candidateStartQueues.get(id)?.shift() ?? [...candidates.values()].find((row) => row.proposalId === id);
        assert.ok(candidate);
        return await json({ candidateId: candidate.candidateId, jobId: candidate.jobId, status: "running" }, 202);
      }
      assert.fail(`Unexpected request: ${method} ${name}`);
    } catch (error) {
      errors.push(error.stack ?? String(error)); await route.fulfill({ status: 500, body: String(error) }).catch(() => {});
    }
  });
  // Two finished candidates beside the editing base; neither is continued yet.
  const viewBaseCandidates = viewBaseOnly ? ["view-base-c1", "view-base-c2"].map((id) => {
    const candidate = prepare(id);
    jobs.get(candidate.jobId).status = "succeeded"; allArtifacts.push(...candidate.artifacts);
    return candidate;
  }) : [];
  await page.goto(`${origin}/?lang=en`, { waitUntil: "domcontentloaded" });
  await rendered(home.runId);

  if (viewBaseOnly) {
    const [c1, c2] = viewBaseCandidates;
    const REFUSED_UNSYNCED = "The editing base was not changed: local model edits are not recorded yet and are kept in the working draft. Record them and continue, or undo them on the model you edited.";
    const NOT_SAVED = "This editing choice could not be saved in this browser; it applies to this tab only.";
    const footer = () => page.locator(".stage__foot .editing-base");
    const notices = () => footer().locator(".editing-base__notice").allInnerTexts();
    const continueButton = () => footer().getByRole("button", { name: "Continue from here", exact: true });
    const undoButton = () => page.getByRole("button", { name: "Undo model", exact: true });
    const rectangle = () => page.locator('button[data-tool-icon="rectangle"]');
    const baseWrites = () => requests.filter((row) => row.method === "PUT" && row.name === "/api/working-draft");
    const storedBases = () => page.evaluate(() => JSON.parse(localStorage.getItem("archflow-studio.user-preferences")).editingBases);
    const settled = (runId) => until(snapshot, (value) => value.editingRunId === runId && !value.changingBase,
      `The editing base did not settle on ${runId}`);
    const shown = (text, message) => until(notices, (texts) => texts.includes(text), message);
    const screenshots = process.env.VIEW_BASE_SCREENSHOTS ? path.resolve(process.env.VIEW_BASE_SCREENSHOTS) : null;
    if (screenshots) await mkdir(screenshots, { recursive: true });

    await step("opening and previewing candidates, then Undo, leave the editing base, its saved choice and the picture alone", async () => {
      await settled(home.runId);
      const writes = baseWrites().length, bases = await storedBases();
      await page.evaluate((runId) => window.__openCandidate(runId), c1.candidateId); // the Hub chat's "Open this candidate on the right"
      await rendered(c1.candidateId);
      await view(c2.artifacts[0]); // a candidate card's preview
      assert.equal(await undoButton().isDisabled(), true, "Looking is not a model step, so nothing can be undone");
      await page.keyboard.press("Control+z");
      await delay(300);
      const after = await snapshot();
      assert.equal(after.editingRunId, home.runId, "Undo after looking keeps the editing base");
      assert.equal(after.loadedRunId, c2.candidateId, "Undo after looking keeps the picture");
      assert.match(await footer().innerText(), /Next edit starts from\s+original\.3dm/);
      assert.equal(baseWrites().length, writes, "No working-draft selection is written while only looking");
      assert.deepEqual(await storedBases(), bases, "No base preference is written while only looking");
    });

    await step("a viewed candidate refuses every direct edit with one reason beside Continue, while looking stays free", async () => {
      await rectangle().click();
      await shown(VIEW_ONLY, "The drawing tool's refusal is not beside Continue");
      assert.equal(await rectangle().getAttribute("aria-pressed"), "false", "A drawing tool must not arm on a viewed candidate");
      await page.keyboard.press("l"); await page.keyboard.press("c");
      for (const icon of ["line", "circle"]) assert.equal(await page.locator(`button[data-tool-icon="${icon}"]`).getAttribute("aria-pressed"), "false");
      await page.locator('button[data-model-tool="pushPull"]').click();
      await page.keyboard.press("m");
      for (const tool of ["pushPull", "move"]) assert.equal(await page.locator(`button[data-model-tool="${tool}"]`).getAttribute("aria-pressed"), "false");
      await page.evaluate(() => window.__candidatePreview.pick({ objectName: "fixture-floor", object: { parent: null }, userStrings: {} }));
      await until(snapshot, (value) => value.picked === "fixture-floor", "Picking on a viewed model must stay available");
      await page.keyboard.press("Delete");
      assert.deepEqual((await snapshot()).localCommands, [], "A refused Delete starts no draft");
      assert.equal((await snapshot()).picked, "fixture-floor", "A refused Delete removes nothing");
      await page.keyboard.press("t");
      await page.locator('.stage-sketch[data-phase="from"]').waitFor(); // measuring still arms
      await page.keyboard.press("Escape");
      assert.deepEqual(await notices(), [VIEW_ONLY], "One reason, shown once");
      assert.equal(await continueButton().isVisible(), true, "The next step is one click away");
      assert.equal((await snapshot()).editingRunId, home.runId);
      if (screenshots) await page.screenshot({ path: path.join(screenshots, "view-only-refusal.png") });
    });

    await step("Continue makes the viewed candidate the editing base, and its tools then work", async () => {
      const writes = baseWrites().length;
      await continueButton().click();
      await settled(c2.candidateId);
      assert.equal(await footer().getAttribute("data-source-match"), "same");
      assert.deepEqual(await notices(), [], "A successful Continue retires the refusal");
      assert.equal(baseWrites().length, writes + 1);
      assert.equal(baseWrites().at(-1).body.runId, c2.candidateId);
      await rectangle().click();
      assert.equal(await rectangle().getAttribute("aria-pressed"), "true");
      await page.keyboard.press("Escape");
      assert.equal(await rectangle().getAttribute("aria-pressed"), "false");
    });

    await step("Undo is refused beside Continue while another version is on screen and steps through bases on the base", async () => {
      await view(c1.artifacts[0]);
      const writes = baseWrites().length;
      assert.equal(await undoButton().isEnabled(), true, "Continuing was a model step");
      await page.keyboard.press("Control+z");
      await shown(VIEW_ONLY, "Undo's refusal is not beside Continue");
      await undoButton().click();
      await delay(200);
      const refused = await snapshot();
      assert.equal(refused.editingRunId, c2.candidateId, "A refused Undo keeps the editing base");
      assert.equal(refused.loadedRunId, c1.candidateId, "A refused Undo keeps the picture");
      assert.equal(baseWrites().length, writes, "A refused Undo writes nothing");
      await view(c2.artifacts[0]);
      await page.keyboard.press("Control+z");
      await settled(home.runId); await rendered(home.runId);
      assert.equal(baseWrites().at(-1).body.runId, home.runId, "Undo on the base returns to the previous base and saves it");
      await page.keyboard.press("Control+Shift+Z");
      await settled(c2.candidateId); await rendered(c2.candidateId);
      assert.equal(baseWrites().at(-1).body.runId, c2.candidateId);
    });

    await step("with unsynced local edits, Continue is refused in the editing-base row with its recovery step and no page error", async () => {
      await page.evaluate(() => window.__candidatePreview.edit({ kind: "delete", elementId: "fixture-floor" }));
      await until(() => workingDraft.localDraft?.commands?.length ?? 0, (count) => count === 1, "The local edit was not kept in the working draft");
      await view(c1.artifacts[0]);
      const writes = baseWrites().length;
      await continueButton().click();
      await shown(REFUSED_UNSYNCED, "The unsynced refusal and its recovery step are not beside Continue");
      assert.equal((await snapshot()).editingRunId, c2.candidateId);
      assert.equal(baseWrites().length, writes, "A refused Continue writes nothing");
      assert.deepEqual(await page.evaluate(() => window.__unhandled), [], "A refused Continue leaves no unhandled rejection");
      assert.deepEqual(errors, []);
      for (const width of [1440, 800, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const layout = await page.locator(".stage__context").evaluate((node) => {
          if (!getComputedStyle(node).getPropertyValue("--ink").trim()) throw new Error("Load the real Hub theme before checking layout");
          const box = node.getBoundingClientRect(), notice = node.querySelector(".editing-base__notice");
          const line = notice.getBoundingClientRect(), lineHeight = parseFloat(getComputedStyle(notice).lineHeight);
          const tools = document.querySelector(".stage-model .viewtools").getBoundingClientRect();
          return { left: box.left, right: box.right, width: box.width, top: box.top, bottom: box.bottom,
            lineLeft: line.left, lineRight: line.right, lineRows: Math.round(line.height / lineHeight),
            toolsBottom: tools.bottom, overflow: document.documentElement.scrollWidth > innerWidth,
            overlap: Math.min(box.right, tools.right) > Math.max(box.left, tools.left) && Math.min(box.bottom, tools.bottom) > Math.max(box.top, tools.top) };
        });
        assert.ok(layout.width <= 761 && layout.left >= 0 && layout.right <= width + 1 && layout.bottom <= 900 && !layout.overflow,
          `The footer box must stay within 760px and the page at ${width}px: ${JSON.stringify(layout)}`);
        assert.ok(!layout.overlap && layout.toolsBottom <= layout.top, `The notice must not push the footer over the toolbar at ${width}px: ${JSON.stringify(layout)}`);
        assert.ok(layout.lineLeft >= layout.left && layout.lineRight <= layout.right + 1, `The notice must wrap inside its box at ${width}px`);
        if (width === 390) assert.ok(layout.lineRows > 1, `The notice must wrap at phone width: ${JSON.stringify(layout)}`);
        if (screenshots) await page.screenshot({ path: path.join(screenshots, `unsynced-refusal-${width}.png`) });
      }
      await page.setViewportSize({ width: 1440, height: 900 });
    });

    // #302: the refusal above offers one click that records the edits and asks for the same Continue again.
    await step("Record edits and continue records the draft from its own base, then retries the refused Continue", async () => {
      const sync = nextDelete = prepare("view-base-sync");
      const record = footer().getByRole("button", { name: "Record edits and continue", exact: true });
      await record.waitFor();
      if (screenshots) {
        await page.screenshot({ path: path.join(screenshots, "record-and-continue.png") });
        // The same refusal and offer in Chinese, asked for again so the notice is in Chinese too.
        await page.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
        await footer().getByRole("button", { name: "从这里继续", exact: true }).click();
        await footer().getByRole("button", { name: "记录修改并继续", exact: true }).waitFor();
        await page.screenshot({ path: path.join(screenshots, "record-and-continue-zh.png") });
        await page.evaluate(() => window.__workspaceFixture.setLanguage("en"));
        await continueButton().click();
        await shown(REFUSED_UNSYNCED, "The English refusal did not come back");
      }
      await record.click();
      const submitted = await until(() => requests.findLast((row) => row.name === "/api/proposals/delete"), Boolean, "Recording did not submit");
      assert.equal(submitted.body.sourceRunId, c2.candidateId, "Recording starts from the base the edit was made on");
      await until(snapshot, (value) => value.runs[sync.candidateId]?.job.status === "ready", "Recording did not start its candidate");
      assert.equal((await snapshot()).editingRunId, c2.candidateId, "Continue waits until the edits are recorded");
      await complete(sync);
      await settled(c1.candidateId);
      await until(() => workingDraft.localDraft, (draft) => draft === null, "The recorded draft was not released");
      assert.deepEqual(baseWrites().slice(-2).map((row) => row.body.runId), [sync.candidateId, c1.candidateId],
        "The recorded batch became the saved base first, then the retried Continue moved it to the viewed candidate");
      assert.deepEqual(await notices(), []);
      assert.equal(await record.count(), 0, "The offer leaves with the refusal it resolved");
      assert.equal(await footer().getAttribute("data-source-match"), "same");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(c1.candidateId); await rendered(c1.candidateId);
    });

    // GH-234 Q2: only the architect's own Sync results advance the saved working
    // position; a generated candidate never does. This fixture has no design history.
    await step("a finished proposal candidate is only listed: no working-draft select, and reopening keeps the architect's base", async () => {
      const writes = baseWrites().length, base = workingDraft.current;
      const generated = prepare("view-base-generated");
      await launch(generated); // an Arch proposal's Run: the normal candidate lifecycle
      await complete(generated);
      await rendered(generated.candidateId); // shown once finished, never continued
      assert.match(await footer().innerText(), /Next edit starts from\s+view-base-c1\.3dm/, "The footer still names the architect's base");
      assert.equal((await snapshot()).editingRunId, c1.candidateId, "Showing a finished candidate keeps the editing base");
      assert.ok(workingDraft.listed.has(generated.candidateId), "The finished candidate is listed for recovery");
      assert.deepEqual(workingDraft.current, base, "Finishing a candidate never moves the saved working position");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(c1.candidateId); await rendered(c1.candidateId);
      assert.equal(await footer().getAttribute("data-source-match"), "same", "Reopening shows the architect's base as the model being edited");
      await view(generated.artifacts[0]);
      assert.match(await footer().innerText(), /Next edit starts from\s+view-base-c1\.3dm/, "After reopening the footer still names the architect's base");
      assert.equal(baseWrites().length, writes, "Neither the candidate nor reopening posted a working-draft select");
    });

    await step("a quietly adopted Sync posts exactly one working-draft select for its run, and reopening starts from it", async () => {
      const writes = baseWrites().length;
      await view(c1.artifacts[0]);
      await until(snapshot, (value) => value.draftReady && value.editingRunId === c1.candidateId, "The architect's base is not editable");
      await page.evaluate(() => window.__candidatePreview.edit({ kind: "delete", elementId: "fixture-floor" }));
      await until(() => workingDraft.localDraft?.commands?.length ?? 0, (count) => count === 1, "The local edit was not kept in the working draft");
      const sync = nextDelete = prepare("view-base-adopted-sync");
      await page.getByRole("button", { name: "Record", exact: true }).click();
      await until(() => requests.findLast((row) => row.name === "/api/proposals/delete"), (row) => row?.body.sourceRunId === c1.candidateId,
        "Sync did not submit from the architect's base");
      await until(snapshot, (value) => value.runs[sync.candidateId]?.job.status === "ready", "Sync did not start its candidate");
      await complete(sync);
      await settled(sync.candidateId); await rendered(sync.candidateId);
      await until(() => workingDraft.localDraft, (draft) => draft === null, "The synced draft was not released");
      assert.deepEqual(baseWrites().slice(writes).map((row) => row.body.runId), [sync.candidateId],
        "Adopting the Sync result posts exactly one working-draft select, for that run");
      assert.equal(workingDraft.current?.runId, sync.candidateId);
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(sync.candidateId); await rendered(sync.candidateId);
      assert.equal(await footer().getAttribute("data-source-match"), "same", "Reopening shows the Sync result as the model being edited");
      await view(c1.artifacts[0]);
      assert.match(await footer().innerText(), /Next edit starts from\s+view-base-adopted-sync\.3dm/, "After reopening the footer names the Sync result as the base");
      assert.equal(baseWrites().length, writes + 1, "Reopening posts no further working-draft select");
    });

    const nextEdit = async (file) => assert.ok((await footer().innerText()).replace(/\s+/g, " ").includes(`Next edit starts from ${file}`),
      `The footer does not name ${file} as the base: ${await footer().innerText()}`);
    const localClears = () => requests.filter((row) => row.method === "PUT" && row.name === "/api/working-draft/local" && row.body.draft === null);
    const editBase = async (base) => {
      await until(snapshot, (value) => value.draftReady && value.editingRunId === base && value.loadedRunId === base && !value.changingBase,
        `${base} is not the editable model on screen`);
      await page.evaluate(() => window.__candidatePreview.edit({ kind: "delete", elementId: "fixture-floor" }));
      await until(() => workingDraft.localDraft?.commands?.length ?? 0, (count) => count === 1, "The local edit was not kept in the working draft");
    };
    const syncWhileLooking = async (id, base) => {
      const sync = nextDelete = prepare(id);
      await page.getByRole("button", { name: "Record", exact: true }).click();
      await until(() => requests.findLast((row) => row.name === "/api/proposals/delete"), (row) => row?.body.sourceRunId === base,
        "Sync did not submit from the architect's base");
      await until(snapshot, (value) => value.runs[sync.candidateId]?.job.status === "ready", "Sync did not start its candidate");
      await view(c2.artifacts[0]); // looking at another model while the batch runs
      return sync;
    };

    // The Studio API advertises design history. With it too, a finished proposal
    // candidate is only shown until the architect's explicit Continue.
    await step("with design history, a finished proposal candidate is shown but becomes the saved base only through Continue", async () => {
      const base = workingDraft.current.runId;
      historyEnabled = true;
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(base); await rendered(base);
      const writes = baseWrites().length;
      const generated = prepare("view-base-history-generated");
      await launch(generated); await complete(generated); await diagnosticRendered(generated);
      await nextEdit(`${base}.3dm`);
      assert.equal(await continueButton().isVisible(), true);
      assert.equal(baseWrites().length, writes, "Showing a generated candidate posts no working-draft select");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(base); await rendered(base);
      assert.equal(baseWrites().length, writes, "Reopening keeps the architect's base and writes nothing");
      await view(generated.artifacts[0]);
      await continueFromViewed(generated.candidateId);
      assert.deepEqual(baseWrites().slice(writes).map((row) => row.body.runId), [generated.candidateId], "Continue saves exactly that choice");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(generated.candidateId); await rendered(generated.candidateId);
    });

    await step("a Sync that finishes while another model is on screen still becomes the saved base without changing what is shown", async () => {
      const base = workingDraft.current.runId, writes = baseWrites().length;
      await editBase(base);
      const sync = await syncWhileLooking("view-base-unadopted-sync", base);
      const clears = localClears().length;
      await complete(sync);
      await until(() => workingDraft.current?.runId, (runId) => runId === sync.candidateId, "The architect's own Sync did not become the saved base");
      await until(() => workingDraft.localDraft, (draft) => draft === null, "The recovery was not released once the saved base held the batch");
      const after = await snapshot();
      assert.equal(after.loadedRunId, c2.candidateId, "What is on screen does not change");
      assert.equal(after.editingRunId, base, "The tab is not re-projected underneath the architect");
      const selects = baseWrites().slice(writes);
      assert.deepEqual(selects.map((row) => row.body.runId), [sync.candidateId], "Exactly one working-draft select, for the Sync result");
      assert.ok(requests.indexOf(selects[0]) < requests.indexOf(localClears()[clears]),
        "The recovery is cleared only after the saved base holds the batch");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(sync.candidateId); await rendered(sync.candidateId);
      await view(c1.artifacts[0]);
      await nextEdit(`${sync.candidateId}.3dm`);
      assert.equal(baseWrites().length, writes + 1);
      await view(sync.artifacts[0]);
    });

    await step("a Sync never overrides a base chosen in another window meanwhile; its result stays listed", async () => {
      const base = workingDraft.current.runId, writes = baseWrites().length;
      await editBase(base);
      const sync = await syncWhileLooking("view-base-overridden-sync", base);
      // The architect continues from c1 in another window while this batch runs.
      workingDraft.current = { runId: c1.candidateId, sourceStageRef: null, branchId: null, updatedAt: new Date().toISOString() };
      workingDraft.revisionSha256 = digest(JSON.stringify([workingDraft.revisionSha256, "another window", c1.candidateId]));
      await complete(sync);
      await until(() => workingDraft.localDraft, (draft) => draft === null, "The synced recovery was not released");
      assert.deepEqual(baseWrites().slice(writes), [], "The other window's explicit choice stands");
      assert.equal(workingDraft.current.runId, c1.candidateId);
      assert.ok(workingDraft.listed.has(sync.candidateId), "The Sync result stays a listed candidate");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(c1.candidateId); await rendered(c1.candidateId);
    });

    await step("a Sync result the saved base cannot take keeps its recovery, so reopening still shows the edits", async () => {
      const base = c1.candidateId, writes = baseWrites().length;
      await editBase(base);
      const sync = await syncWhileLooking("view-base-unsaved-sync", base);
      failNextSelect = true;
      await complete(sync);
      await until(() => workingDraft.localDraft, (draft) => draft?.attempt?.pending === null && draft.commands.length === 1,
        "The recovery was not kept with its synced commands");
      assert.equal(failNextSelect, false, "The saved base was asked to take the batch");
      assert.deepEqual(baseWrites().slice(writes).map((row) => row.body.runId), [sync.candidateId]);
      assert.equal(workingDraft.current.runId, base, "The refused selection left the saved base alone");
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(base); await rendered(base);
      await until(snapshot, (value) => value.localCommands.length === 1, "Reopening did not restore the synced edits on their exact source");
      historyEnabled = false;
    });

    await step("a base choice this browser cannot save is reported in the editing-base row", async () => {
      workingDraftEnabled = false;
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(home.runId); await rendered(home.runId); // this browser's saved choice
      await page.evaluate(() => {
        const save = Storage.prototype.setItem;
        window.__restoreStorage = () => { Storage.prototype.setItem = save; };
        Storage.prototype.setItem = function () { throw new DOMException("The quota has been exceeded.", "QuotaExceededError"); };
      });
      await view(c2.artifacts[0]);
      await continueButton().click();
      await settled(c2.candidateId);
      await shown(NOT_SAVED, "The unsaved choice is not reported in the editing-base row");
      assert.equal((await snapshot()).persistenceFailed, true);
      if (screenshots) await page.screenshot({ path: path.join(screenshots, "not-saved.png") });
      await page.evaluate(() => window.__restoreStorage());
      await page.reload({ waitUntil: "domcontentloaded" });
      await settled(home.runId); // as the row said: the choice applied to that tab only
      assert.deepEqual(await notices(), []);
      assert.deepEqual(await page.evaluate(() => window.__unhandled), []);
    });
  } else if (candidateDeliveryOnly) {
    const bytesReads = artifact => requests.filter(row => row.name === `/api/artifacts/${artifact.sha256}/bytes`).length;
    const delivery = (id) => {
      const native = { ...makeArtifact(id, 3), fileName: `${id}-native.3dm`, representation: "preview", modelSource: null };
      const composed = { ...makeArtifact(id, 7), fileName: `${id}-composed.3dm` };
      allArtifacts.push(native);
      return { native, composed };
    };
    const open = async ({ native }) => {
      await page.evaluate(runId => window.__openCandidate(runId), native.runId);
      await rendered(native.runId, native.fileName);
      await until(snapshot, value => value.draftReady, "The candidate's own state was not read");
    };
    const register = async artifact => {
      allArtifacts.push(artifact);
      await emit("model_asset.registered");
      await until(snapshot, value => value.artifactShas.includes(artifact.sha256), "The registered asset was not discovered");
    };
    const edit = async () => {
      await page.evaluate(() => window.__candidatePreview.edit({ kind: "delete", elementId: "fixture-floor" }));
      await until(snapshot, value => value.localCommands.length === 1, "The local command was not retained");
    };
    // Opening a chat result only shows it; editing it follows the explicit Continue.
    const notice = () => page.locator(".stage__foot .editing-base .editing-base__notice");
    const continueFromView = async (runId) => {
      await page.locator(".stage__foot .editing-base").getByRole("button", { name: "Continue from here", exact: true }).click();
      await until(snapshot, value => value.editingRunId === runId && !value.changingBase, "Continue did not make the viewed candidate the editing base");
    };

    await step("an opened candidate refuses direct edits beside Continue until it is continued; Sync then starts from it", async () => {
      const value = delivery("delivery-continue"); await open(value);
      const before = await snapshot();
      assert.notEqual(before.editingRunId, value.native.runId, "Opening a chat result only shows it");
      const rectangle = page.locator('button[data-tool-icon="rectangle"]');
      await rectangle.click();
      await until(() => notice().allInnerTexts(), texts => texts.includes(VIEW_ONLY), "The refusal was not shown beside Continue");
      assert.equal(await rectangle.getAttribute("aria-pressed"), "false", "A drawing tool must not arm on a viewed candidate");
      await page.keyboard.press("p");
      assert.equal(await page.locator('button[data-model-tool="pushPull"]').getAttribute("aria-pressed"), "false");
      const refused = await page.evaluate(() => {
        try { window.__candidatePreview.edit({ kind: "delete", elementId: "fixture-floor" }); return null; }
        catch (error) { return error.message; }
      });
      assert.equal(refused, VIEW_ONLY, "A commit that reaches the draft layer is refused with the same reason");
      assert.deepEqual((await snapshot()).localCommands, []);
      assert.equal((await snapshot()).editingRunId, before.editingRunId, "Refusing an edit never moves the editing base");
      await continueFromView(value.native.runId);
      assert.equal(await notice().count(), 0, "A successful Continue retires the refusal");
      await rectangle.click();
      assert.equal(await rectangle.getAttribute("aria-pressed"), "true", "The continued candidate's own tools work");
      await page.keyboard.press("Escape");
      await edit();
      const sync = nextDelete = prepare("delivery-continue-sync");
      await page.getByRole("button", { name: "Record", exact: true }).click();
      const submitted = await until(() => requests.findLast(row => row.name === "/api/proposals/delete"), Boolean, "Sync did not submit its edit");
      assert.equal(submitted.body.sourceRunId, value.native.runId, "Sync is based on the continued run");
      assert.equal(submitted.body.stateDigest, stateDigest(value.native.runId));
      await until(snapshot, v => v.runs[sync.candidateId]?.job.status === "ready", "Sync did not start its candidate");
      await complete(sync);
      await until(snapshot, v => v.editingRunId === sync.candidateId && !v.changingBase, "The synced result did not become the editing base");
      await rendered(sync.candidateId);
      assert.deepEqual(await page.evaluate(() => window.__unhandled), []);
    });

    await step("a late composed asset replaces the automatic native preview once, preserving its camera", async () => {
      const value = delivery("delivery-upgrade"); await open(value);
      const camera = await page.evaluate(() => window.__candidatePreview.camera());
      assert.ok(camera);
      await register(value.composed);
      await rendered(value.composed.runId, value.composed.fileName);
      assert.deepEqual((await snapshot()).loadedModelSource, value.composed.modelSource);
      assert.deepEqual(await page.evaluate(() => window.__candidatePreview.camera()), camera);
      const reads = requests.filter(row => row.name === "/api/artifacts").length;
      await emit("model_asset.registered");
      await page.evaluate(runId => window.__openCandidate(runId), value.native.runId);
      await until(() => requests.filter(row => row.name === "/api/artifacts").length, count => count > reads, "Repeated delivery did not refresh the list");
      await delay(300);
      assert.equal(bytesReads(value.native), 1);
      assert.equal(bytesReads(value.composed), 1);
      assert.deepEqual(await page.evaluate(() => window.__candidatePreview.camera()), camera);
    });

    await step("a manual historical choice, including returning to the native preview, wins over late delivery", async () => {
      const value = delivery("delivery-manual"); await open(value);
      await view(other); await view(value.native);
      await register(value.composed); await delay(300);
      assert.equal((await snapshot()).loadedFileName, value.native.fileName);
      assert.equal(bytesReads(value.composed), 0);
    });

    await step("repeated presentation refreshes do not interrupt an in-flight composed delivery", async () => {
      const value = delivery("delivery-refresh-in-flight"); await open(value);
      const gate = deferred(); modelGates.set(value.composed.sha256, gate);
      await register(value.composed);
      await until(() => bytesReads(value.composed), count => count === 1, "The complete model download did not start");
      const reads = requests.filter(row => row.name === "/api/artifacts").length;
      await page.evaluate(runId => window.__openCandidate(runId), value.native.runId);
      await emit("model_asset.registered");
      await until(() => requests.filter(row => row.name === "/api/artifacts").length, count => count > reads, "The repeated delivery was not observed");
      gate.resolve();
      await rendered(value.composed.runId, value.composed.fileName);
      assert.equal(bytesReads(value.native), 1);
      assert.equal(bytesReads(value.composed), 1);
    });

    await step("same-run composed assets with a different state cannot replace the native preview", async () => {
      const value = delivery("delivery-wrong-state"); await open(value);
      const mismatch = { ...value.composed, modelSource: { ...value.composed.modelSource, stateDigest: stateDigest("different-state") } };
      await register(mismatch); await delay(300);
      assert.equal((await snapshot()).loadedFileName, value.native.fileName);
      assert.equal(bytesReads(mismatch), 0);
    });

    await step("unsubmitted local model edits on a continued candidate survive late composed delivery", async () => {
      const value = delivery("delivery-edited"); await open(value); await continueFromView(value.native.runId); await edit();
      await register(value.composed); await delay(300);
      assert.equal((await snapshot()).loadedFileName, value.native.fileName);
      assert.deepEqual((await snapshot()).localCommands, [{ kind: "delete", elementId: "fixture-floor" }]);
      assert.equal(bytesReads(value.composed), 0);
    });

    await step("editing during the composed download cancels installation without losing the draft", async () => {
      const value = delivery("delivery-edit-in-flight"); await open(value); await continueFromView(value.native.runId);
      const gate = deferred(); modelGates.set(value.composed.sha256, gate);
      await register(value.composed);
      await until(() => bytesReads(value.composed), count => count === 1, "The complete model download did not start");
      await edit(); gate.resolve();
      await until(() => loadTimings(value.native.runId), rows => rows.some(row => row.body.status === "cancelled"), "The stale delivery was not cancelled");
      assert.equal((await snapshot()).loadedFileName, value.native.fileName);
      assert.deepEqual((await snapshot()).localCommands, [{ kind: "delete", elementId: "fixture-floor" }]);
    });

    await step("manual model selection during composed parsing cancels the background installation", async () => {
      const value = delivery("delivery-manual-in-flight"); await open(value);
      await page.evaluate(fileName => {
        let resolve; const promise = new Promise(done => { resolve = done; });
        window.__previewParseGates[fileName] = { waiting: false, promise, resolve };
      }, value.composed.fileName);
      await register(value.composed);
      await until(() => page.evaluate(fileName => window.__previewParseGates[fileName].waiting, value.composed.fileName), Boolean, "The complete model parser did not start");
      await view(other);
      await page.evaluate(fileName => window.__previewParseGates[fileName].resolve(), value.composed.fileName);
      await until(() => loadTimings(value.native.runId), rows => rows.some(row => row.body.status === "cancelled"), "The stale parser did not cancel");
      assert.equal((await snapshot()).loadedFileName, other.fileName);
    });

    await step("delivery discovered while the first native model parses is installed afterwards", async () => {
      const value = delivery("delivery-during-native");
      await page.evaluate(fileName => {
        let resolve; const promise = new Promise(done => { resolve = done; });
        window.__previewParseGates[fileName] = { waiting: false, promise, resolve };
      }, value.native.fileName);
      await page.evaluate(runId => window.__openCandidate(runId), value.native.runId);
      await until(() => page.evaluate(fileName => window.__previewParseGates[fileName].waiting, value.native.fileName), Boolean, "Native parsing did not start");
      await register(value.composed);
      await page.evaluate(fileName => window.__previewParseGates[fileName].resolve(), value.native.fileName);
      await rendered(value.composed.runId, value.composed.fileName);
      assert.equal(bytesReads(value.native), 1);
      assert.equal(bytesReads(value.composed), 1);
    });
  } else {
  if (!modelTimingOnly) {
  await step("the loaded model retains its exact runtime source", async () => {
    assert.equal((await snapshot()).loadedFileName, home.fileName);
    assert.deepEqual((await snapshot()).loadedModelSource, home.modelSource);
    assert.equal((await snapshot()).projectId, projectId);
  });

  await step("a failed background artifact refresh retries without losing the displayed model", async () => {
    const external = makeArtifact("external-result", 4); allArtifacts.push(external);
    const before = requests.filter((row) => row.name === "/api/artifacts").length;
    artifactFailures = 1;
    await emit("model_asset.registered");
    await until(snapshot, (value) => value.artifacts.includes(external.runId), "The first failed refresh was never retried");
    assert.ok(requests.filter((row) => row.name === "/api/artifacts").length >= before + 2);
    await rendered(home.runId);
  });

  await step("candidate completion and preview continue without a mounted chat while validation is pending", async () => {
    const candidate = prepare("closed-chat");
    const validation = deferred(), bytes = deferred();
    validationGates.set(candidate.candidateId, validation); modelGates.set(candidate.artifacts[0].sha256, bytes);
    await launch(candidate); await complete(candidate);
    await until(snapshot, (value) => value.loadingSha === candidate.artifacts[0].sha256, "Completed geometry did not start downloading without validation");
    const waiting = await snapshot();
    assert.equal(waiting.loadedRunId, home.runId);
    assert.equal(waiting.runs[candidate.candidateId].validation.status, "loading");
    assert.equal(waiting.entries.some((line) => line.includes("model is on screen") && line.includes(candidate.candidateId)), false);
    assert.equal(loadTimings(candidate.candidateId).length, 0, "A download still in flight must not report a completed load");
    bytes.resolve(); await rendered(candidate.candidateId);
    await reportedLoad(candidate.artifacts[0], "succeeded");
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

  await step("failed monitoring cannot repeat a model load or block the next view", async () => {
    monitorFailure = true;
    const candidate = prepare("monitor-unavailable");
    try {
      await launch(candidate); await complete(candidate); await rendered(candidate.candidateId);
      await reportedLoad(candidate.artifacts[0], "succeeded");
      await emit("candidate.succeeded", candidate.candidateId); await emit("model_asset.registered");
      await delay(250); await rendered(candidate.candidateId);
      assert.equal(requests.filter((row) => row.name === `/api/artifacts/${candidate.artifacts[0].sha256}/bytes`).length, 1,
        "A failed diagnostic POST must not retry the artifact download");
      assert.equal(loadTimings(candidate.candidateId).length, 1, "A failed diagnostic POST must not restart the load");
      const otherLoads = loadTimings(other.runId).length;
      await view(other);
      await until(() => loadTimings(other.runId).length, (count) => count === otherLoads + 1, "The next view did not finish while monitoring was unavailable");
      assert.equal(loadTimings(other.runId).at(-1).body.status, "succeeded");
    } finally { monitorFailure = false; }
    await view(home);
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
    await reportedLoad(candidate.artifacts[0], "failed");
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
    await reportedLoad(candidate.artifacts[0], "cancelled");
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
    assert.equal(loadTimings(candidate.candidateId).length, 0, "A parse still in flight must not report a completed load");
    const stateGate = deferred(); stateGates.set(other.runId, stateGate);
    await page.evaluate((source) => { void window.__candidatePreview.changeBase(source.runId, source); }, other.modelSource);
    await until(() => stateGate.requested, Boolean, "The next editing context was not requested");
    await page.evaluate((fileName) => window.__previewParseGates[fileName].release(), candidate.artifacts[0].fileName);
    await until(snapshot, (value) => value.loadingSha === null, "The stale parse did not finish");
    assert.equal((await snapshot()).status, "ready", "Cancelling a parsed replacement must release its loading status while preserving the old model");
    assert.equal((await snapshot()).loadedRunId, home.runId, "The old parse must not attach even before the new base model starts loading");
    await reportedLoad(candidate.artifacts[0], "cancelled");
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

  await step("normal modeling does not call retired Program/Massing panel endpoints", async () => {
    assert.deepEqual(requests.filter(({ name }) => /^\/api\/(program|options|semantics)(?:\/|$)/.test(name)), []);
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

  await step("switching projects releases a pending parse and blocks the previous project's late candidate", async () => {
    await page.evaluate((source) => window.__candidatePreview.changeBase(source.runId, source), home.modelSource); await rendered(home.runId);
    const candidate = prepare("late-project"); await launch(candidate);
    const parsing = prepare("late-project-parse");
    await page.evaluate((fileName) => {
      let release; const promise = new Promise((resolve) => { release = resolve; });
      window.__previewParseGates[fileName] = { promise, release, waiting: false };
    }, parsing.artifacts[0].fileName);
    await launch(parsing); await complete(parsing);
    await page.waitForFunction((fileName) => window.__previewParseGates[fileName].waiting, parsing.artifacts[0].fileName);
    assert.equal((await snapshot()).loadingSha, parsing.artifacts[0].sha256, "The old project's artifact must be pending in the actual parser");
    projectId = "different-project"; currentHome = makeArtifact("other-project-home", 12); allArtifacts.push(currentHome);
    // A task belongs to the project it was opened for. When the server turns
    // out to be bound to another one, refreshing this task refuses rather than
    // quietly adopting it: the session fails, which clears what the viewer was
    // holding, and no model of either project is put in its place.
    await page.evaluate(() => window.__candidatePreview.reload());
    await until(snapshot, (value) => value.baseError === "EDITING_PROJECT_CHANGED",
                "The task must refuse a server that now binds another project");
    assert.notEqual((await snapshot()).loadedRunId, currentHome.runId,
                    "The other project's model must not be adopted by this task");
    // The parse that was still running belonged to the project this task left
    // behind: releasing it reports a cancelled load and shows nothing.
    await page.evaluate((fileName) => window.__previewParseGates[fileName].release(), parsing.artifacts[0].fileName);
    await reportedLoad(parsing.artifacts[0], "cancelled");
    await complete(candidate);
    await until(snapshot, (value) => value.runs[candidate.candidateId].candidate.status === "ready", "The previous project job did not finish");
    await delay(150);
    // Neither project's newer work reaches this view: the refused task shows
    // the other project nothing, and its own late candidate nothing either.
    assert.ok(![currentHome.runId, candidate.candidateId].includes((await snapshot()).loadedRunId),
              "no model of either project may be adopted after the refusal");
    // Opening the project the server actually binds is a fresh page, which is
    // what the refusal above tells the architect to do. The reload that follows
    // this step is that page, and it renders the new project's own model.
  });

  assert.deepEqual(errors, []);
  assert.equal(requests.some((row) => /\/(decision|accept|issue)(\/|$)/.test(row.name)), false, "Preview must never accept or issue a design");
  historyEnabled = true;
  // Opening the project the server binds is a fresh page, which is what the
  // refusal above tells the architect to do. Nothing of the project this task
  // left behind may come with it.
  await page.reload({ waitUntil: "domcontentloaded" });
  await rendered(currentHome.runId);
  assert.equal((await snapshot()).projectId, projectId, "The reopened page binds the project the server serves");
  assert.deepEqual((await snapshot()).loadedModelSource, currentHome.modelSource,
                   "and shows that project's own model");
  assert.deepEqual((await snapshot()).artifacts, [currentHome.runId],
                   "and lists only that project's exports");
  await openVersions();
  let s0, s1, historyA, historyB, historyC;

  await step("S0 requires one explicit confirmation and cold reopen restores the committed head", async () => {
    assert.equal(stages.size, 0);
    assert.equal(await page.getByText("已有模型与历史运行 · 尚未归入 Stage", { exact: true }).count(), 1);
    await page.getByRole("button", { name: "Accept as S0", exact: true }).click();
    await until(snapshot, (value) => value.history?.stages.length === 1, "S0 was not confirmed");
    s0 = [...stages.values()][0];
    assert.deepEqual(s0.modelSource, currentHome.modelSource);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(currentHome.runId);
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef);
    await openVersions();
  });

  await step("a finished preview becomes the editable candidate context only after Continue, retaining its actual source Stage", async () => {
    historyA = prepare("history-a"); candidateBases.set(historyA.candidateId, s0.stageRef);
    await launch(historyA); await complete(historyA); await diagnosticRendered(historyA);
    await continueFromViewed(historyA.candidateId);
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef);
    await stageAsBase("S0", "S0 · 当前提交", currentHome.runId);
    const continueReadStart = requests.length;
    await openVersions();
    await page.locator('[data-preview-candidate="history-a"]').getByRole("button", { name: "Continue from here", exact: true }).click();
    await rendered(historyA.candidateId);
    await until(snapshot, (value) => value.editingRunId === historyA.candidateId && !value.changingBase,
      "Continuing the retained candidate did not finish switching its editing context");
    assert.deepEqual(requests.slice(continueReadStart).filter((row) => row.method === "GET" && row.name === "/api/state" &&
      row.query.run === currentHome.runId).map((row) => row.query.run), [],
      "Switching from S0 to the retained candidate must not read the departed model's state for its catalog");
    await page.evaluate(() => window.__candidatePreview.propose("record candidate context"));
    const intent = requests.findLast((row) => row.name === "/api/intents");
    assert.equal(intent.body.sourceRunId, historyA.candidateId);
    assert.equal(intent.body.sourceStageRef, s0.stageRef);
    assert.deepEqual(intent.body.modelSource, historyA.artifacts[0].modelSource);
    assert.equal(stages.size, 1); assert.equal(branches.size, 1);
  });

  await step("failed acceptance preserves the preview; successful acceptance appends S1 on main", async () => {
    const accept = page.getByRole("button", { name: "Accept as next Stage", exact: true });
    await accept.waitFor(); acceptFailure = true; await accept.click();
    await until(snapshot, (value) => value.historyError?.includes("Candidate kept"), "Acceptance failure was hidden");
    await rendered(historyA.candidateId); assert.equal(stages.size, 1);
    acceptFailure = false; await accept.click();
    await until(snapshot, (value) => value.history?.stages.length === 2, "Accepted candidate did not become S1");
    s1 = historyDto().stages.at(-1);
    assert.equal(s1.parentStageRef, s0.stageRef); assert.equal(branches.size, 1);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(historyA.candidateId);
    assert.equal((await snapshot()).sourceStageRef, s1.stageRef);
    await openVersions();
  });

  // #302: a Board note made on another model version asks before the editing base moves.
  const sendBoardNote = async (utterance) => {
    await page.evaluate((value) => { window.__boardNote = value; }, { projectId, utterance, modelSource: s0.modelSource,
      sourceStageRef: s0.stageRef, source: { runId: s0.modelSource.runId, assetSha256: "a".repeat(64), revisionRef: null, pageIndex: 0 },
      documentAnnotations: [], documentVisuals: [] });
    await page.getByTestId("workspace-board").click();
    await page.getByRole("button", { name: "Send board note", exact: true }).click();
    const choice = page.getByRole("group", { name: "The Board note's model version", exact: true });
    await choice.waitFor();
    return choice;
  };
  const intentCount = () => requests.filter((row) => row.name === "/api/intents").length;

  await step("a Board note on another model version asks first; View only is the default and moves nothing", async () => {
    const before = await snapshot(), intents = intentCount();
    const choice = await sendBoardNote("Open the west porch");
    assert.match(await choice.innerText(), /made on another model version: S0\./);
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), "View only", "Only looking is the default choice");
    if (process.env.BOARD_NOTE_SCREENSHOTS) {
      await mkdir(process.env.BOARD_NOTE_SCREENSHOTS, { recursive: true });
      const versionsPanel = page.locator("#stage-versions-panel");
      if (await versionsPanel.count()) await versionsPanel.getByRole("button", { name: "Close", exact: true }).click();
      await page.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
      await page.getByRole("group", { name: "画板意见针对的模型版本", exact: true }).getByRole("button", { name: "只查看", exact: true }).waitFor();
      await page.screenshot({ path: path.join(process.env.BOARD_NOTE_SCREENSHOTS, "board-note-asks-zh.png") });
      await page.evaluate(() => window.__workspaceFixture.setLanguage("en"));
      await choice.getByRole("button", { name: "View only", exact: true }).waitFor();
    }
    await delay(200);
    assert.equal((await snapshot()).editingRunId, before.editingRunId, "Asking moves nothing");
    assert.equal(intentCount(), intents, "Nothing is submitted before the architect chooses");
    await choice.getByRole("button", { name: "View only", exact: true }).click();
    await rendered(currentHome.runId);
    await choice.waitFor({ state: "detached" });
    const after = await snapshot();
    assert.equal(after.editingRunId, before.editingRunId, "View only keeps the editing base");
    assert.equal(after.sourceStageRef, before.sourceStageRef);
    assert.equal(intentCount(), intents, "View only submits nothing");
    assert.ok(after.entries.some((entry) => /Current did not move and the note was not submitted/.test(entry)));
  });

  await step("Continue from here on a Board note makes its model the base, submits it, and a Stage restores the base", async () => {
    const choice = await sendBoardNote("Close the west porch");
    await choice.getByRole("button", { name: "Continue from here", exact: true }).click();
    await until(snapshot, (value) => value.editingRunId === currentHome.runId && value.sourceStageRef === s0.stageRef && !value.changingBase,
      "Continue from here did not make the note's model the editing base");
    const intent = await until(() => requests.findLast((row) => row.name === "/api/intents" && row.body.utterance === "Close the west porch"),
      Boolean, "The Board note was not submitted after Continue");
    assert.equal(intent.body.sourceStageRef, s0.stageRef);
    assert.deepEqual(intent.body.modelSource, s0.modelSource);
    await stageAsBase("S1", "S1 · 当前提交", historyA.candidateId);
  });

  await step("viewing a historical candidate labels its own source Stage without moving the editing base", async () => {
    const historical = prepare("history-source-label");
    candidateBases.set(historical.candidateId, s0.stageRef);
    jobs.get(historical.jobId).status = "succeeded"; allArtifacts.push(...historical.artifacts);
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(historyA.candidateId);
    await openVersions();
    await page.locator('[data-preview-candidate="history-source-label"]').waitFor();
    await view(historical.artifacts[0]);
    await until(() => page.locator(".stage__versions-current").innerText(),
      value => value.includes("S0") && value.includes("Uncommitted candidate"), "The viewed candidate must show its own source Stage");
    assert.equal((await snapshot()).sourceStageRef, s1.stageRef);
    assert.equal((await snapshot()).editingRunId, historyA.candidateId);
    await view(historyA.artifacts[0]);
  });

  await step("a historical Stage opens read-only, Continue makes it the real editing source, and branch creation is explicit", async () => {
    await stageAsBase("S0", "S0", currentHome.runId);
    await page.evaluate(() => window.__candidatePreview.propose("record historical context"));
    const intent = requests.findLast((row) => row.name === "/api/intents");
    assert.equal(intent.body.sourceRunId, currentHome.runId); assert.equal(intent.body.sourceStageRef, s0.stageRef);
    assert.equal(branches.size, 1);
    await openVersions();
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "从这里新建分支" }).click();
    await page.getByPlaceholder("alternate-layout").fill("alternate");
    await page.getByRole("button", { name: "创建分支", exact: true }).click();
    await until(snapshot, (value) => value.history?.branchId === "alternate", "Named branch was not selected");
    assert.equal(branches.get("main").headStageRef, s1.stageRef);
    assert.equal(branches.get("alternate").headStageRef, s0.stageRef);
  });

  await step("cold candidate recovery and same-Stage combination reuse the normal preview lifecycle", async () => {
    historyB = prepare("history-b"); historyC = prepare("history-c");
    const otherStage = prepare("history-other-stage");
    for (const [candidate, stage] of [[historyB, s0], [historyC, s0], [otherStage, s1]]) {
      candidateBases.set(candidate.candidateId, stage.stageRef);
      jobs.get(candidate.jobId).status = "succeeded"; allArtifacts.push(...candidate.artifacts);
    }
    const readStart = requests.length;
    const unselectedStateReads = () => requests.slice(readStart).filter((row) => row.method === "GET" && row.name === "/api/state" &&
      [historyB, historyC, otherStage].some((candidate) => candidate.candidateId === row.query.run)).map((row) => row.query.run);
    historyGate = { ...deferred(), branchId: "main", failed: true };
    // An explicitly selected historical Stage remains the editing source
    // after reopening; retained candidates still use exact source classification.
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(currentHome.runId);
    await until(() => historyGate.requested, Boolean, "The other branch's history was not requested");
    assert.deepEqual(unselectedStateReads(), [], "Cold reopen must not project every retained candidate before one is selected");
    await openVersions();
    assert.equal(await page.locator('[data-preview-candidate="history-a"]').count(), 0,
      "A model already accepted on main must not appear as an unaccepted candidate while another branch is still loading");
    assert.equal(await page.locator('[data-preview-candidate="history-b"]').count(), 0,
      "Cold candidate classification must wait for the accepted sources from every branch");
    historyGate.resolve();
    await until(snapshot, (value) => value.historyError?.includes("Other branch history unavailable."), "The branch history failure was hidden");
    assert.equal(await page.locator('[data-preview-candidate="history-a"]').count(), 0,
      "A failed other-branch read must not turn the accepted main model into a candidate");
    assert.deepEqual(unselectedStateReads(), [], "A failed branch history read must not fall back to projecting unselected candidates");
    historyGate = null;
    await page.reload({ waitUntil: "domcontentloaded" }); await rendered(currentHome.runId);
    await openVersions();
    await page.locator('[data-preview-candidate="history-b"]').waitFor();
    await page.locator('[data-preview-candidate="history-c"]').waitFor();
    await page.locator('[data-preview-candidate="history-other-stage"]').waitFor();
    assert.deepEqual(unselectedStateReads(), [], "Opening the candidate list must use retained artifact metadata without reading candidate states");
    await openVersions();
    await page.getByRole("combobox", { name: "Branch", exact: true }).selectOption("alternate"); await rendered(currentHome.runId);
    await page.locator('[data-preview-candidate="history-b"]').waitFor();
    assert.equal(await page.locator('[data-preview-candidate="history-a"]').count(), 0, "A model accepted on main must not become an unaccepted candidate on another branch");
    // Cold candidates come from retained project artifacts. Reopening a workspace
    // does not manufacture local conversation rows for the Hub's chat history.
    assert.deepEqual((await snapshot()).candidateEntries, []);
    const checkB = page.locator('[data-preview-candidate="history-b"] input[type="checkbox"]');
    const checkC = page.locator('[data-preview-candidate="history-c"] input[type="checkbox"]');
    const checkOtherStage = page.locator('[data-preview-candidate="history-other-stage"] input[type="checkbox"]');
    const combine = page.getByRole("button", { name: "合并选中候选并预览", exact: true });
    await checkOtherStage.check();
    assert.equal(await checkB.isDisabled(), true, "Candidates from S0 cannot join a selected candidate from S1");
    assert.equal(await checkC.isDisabled(), true);
    assert.equal(await combine.isDisabled(), true);
    await checkOtherStage.uncheck();
    await checkB.check();
    assert.equal(await checkOtherStage.isDisabled(), true, "The same source-Stage restriction must hold when selection starts from S0");
    assert.equal(await checkC.isEnabled(), true);
    await checkC.check();
    assert.equal(await combine.isEnabled(), true, "Two cold candidates from the same Stage must remain combinable");
    assert.deepEqual(unselectedStateReads(), [], "Changing branches and selecting candidates for combination must not load their complete state");
    nextCombined = prepare("history-combined"); candidateBases.set(nextCombined.candidateId, s0.stageRef); const combined = nextCombined;
    await combine.click();
    await until(snapshot, (value) => value.runs[combined.candidateId]?.job.status === "ready", "Combined job was not observed");
    const request = requests.findLast((row) => row.name === "/api/candidates/combine");
    assert.deepEqual(request.body.candidateIds.sort(), [historyB.candidateId, historyC.candidateId]);
    await complete(combined); await diagnosticRendered(combined);
    await continueFromViewed(combined.candidateId);
    assert.equal((await snapshot()).sourceStageRef, s0.stageRef); assert.equal(branches.size, 2);
    assert.deepEqual(unselectedStateReads(), [], "Previewing the combined result must not load the unviewed source candidates");
  });

  await step("Versions yields to viewport tools without changing the camera or model", async () => {
    const state = async () => ({ camera: await page.evaluate(() => window.__candidatePreview.camera()),
      source: (await snapshot()).loadedModelSource, editing: (await snapshot()).editingModelSource,
      stage: (await snapshot()).sourceStageRef });
    const assertReachable = async (button, label) => {
      assert.equal(await button.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return rect.width > 0 && rect.height > 0 && element.contains(hit);
      }), true, `${label} is occluded by another overlay`);
    };
    for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 800, height: 720 }]) {
      await page.setViewportSize(size);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const before = await state();
      const requestStart = requests.length;
      for (const [selector, panel, label] of [
        ['button[aria-controls="view-tools"]', '#view-tools', 'View tools'],
        ['button[aria-controls="annotation-tools"]', '#annotation-tools', 'Tracing Paper'],
        ['button[data-tool-icon="select"]', null, 'Select'],
      ]) {
        await openVersions();
        await page.locator('#stage-versions-panel').waitFor();
        const button = page.locator(selector);
        await assertReachable(button, label);
        const separation = await page.evaluate(() => {
          const history = document.querySelector('#stage-versions-panel').getBoundingClientRect();
          const tools = document.querySelector('.viewtools-wrap').getBoundingClientRect();
          return { historyBottom: history.bottom, toolsTop: tools.top, historyTop: history.top };
        });
        assert.ok(separation.historyBottom <= separation.toolsTop - 4,
          `${label} and version history physically overlap at ${size.width}px`);
        assert.ok(separation.historyTop >= 0, 'Version history must stay inside the visible page');
        await button.click();
        assert.equal(await page.locator('#stage-versions-panel').count(), 0);
        if (panel) await page.locator(panel).waitFor();
        assert.deepEqual(await state(), before, `${label} changed camera or source at ${size.width}px`);
      }
      await openVersions();
      const close = page.locator('#stage-versions-panel').getByRole('button', { name: 'Close', exact: true });
      await assertReachable(close, 'Close versions');
      await close.click();
      assert.equal(await page.locator('#stage-versions-panel').count(), 0);
      assert.deepEqual(await state(), before);
      assert.deepEqual(requests.slice(requestStart).filter(row => row.method !== 'GET'), [],
        'Switching overlay panels must not submit project changes');
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  await step("the view tools keep the standard cameras and no longer offer the retired elevation entry", async () => {
    await openVersions();
    await page.getByRole("combobox", { name: "Branch", exact: true }).selectOption("main"); await rendered(historyA.candidateId);
    await openViewTools();
    const viewTools = page.locator("#view-tools");
    assert.equal(await page.getByRole("button", { name: "Generate elevation", exact: true }).count(), 0);
    assert.equal(await page.getByRole("combobox", { name: "Elevation direction", exact: true }).count(), 0);
    assert.equal(await page.locator(".stage-drawing-error").count(), 0);
    for (const view of ["Top", "Front", "Right", "Isometric", "Perspective"]) {
      assert.equal(await viewTools.getByRole("button", { name: view, exact: true }).count(), 1,
                   `${view} must remain a standard camera view`);
    }
    await viewTools.getByRole("button", { name: "Fit selected", exact: true }).waitFor();
    const requestStart = requests.length;
    await viewTools.getByRole("button", { name: "Isometric", exact: true }).click();
    assert.deepEqual(requests.slice(requestStart).filter((row) => row.method !== "GET" && !row.name.startsWith("/api/events/")).map((row) => row.name), [],
                     "A standard camera view must not submit project changes");
    await rendered(historyA.candidateId);
  });

  await step("a registered drawing opens its exact immutable revision with no chat expansion", async () => {
    // Inside a task the conversation is part of the workspace, so what this
    // holds to is that opening a drawing leaves it exactly as it was.
    const drawing = registerDrawing(historyA.artifacts[0].modelSource, s1.stageRef);
    const conversationBefore = await page.locator("#conversation-panel").count();
    await openDrawingFromBoard(drawing);
    await page.locator('.document-workspace:not([aria-hidden="true"]) .document-viewport[data-ready="true"]').waitFor();
    const value = await snapshot();
    assert.equal(value.documentView.open, true); assert.equal(value.documentView.revisionRef, drawing.revisionRef);
    assert.equal(await page.locator("#conversation-panel").count(), conversationBefore,
                 "Opening a drawing must not expand or collapse the conversation");
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, drawing.revisionRef);
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), drawing.revisionRef);
  });

  await step("failed drawing autosave blocks Continue from a viewed Stage and a retry preserves the comment", async () => {
    annotationFailure = true;
    await page.locator("#document-comment").fill("Keep the terrace line.");
    await until(async () => page.locator(".document-error").count(), (value) => value > 0, "Autosave failure did not remain visible");
    // Viewing S0 saves and moves nothing (#302); Continue from here is the switch the failed save refuses.
    await openVersions();
    await page.locator('[data-design-stage="S0"]').getByRole("button", { name: "S0", exact: true }).click();
    await rendered(currentHome.runId);
    assert.equal((await snapshot()).editingRunId, historyA.candidateId);
    // Dismiss the overlay before reaching the drawing's autosave error; a longer retained history may cover it.
    await page.locator("#stage-versions-panel").getByRole("button", { name: "Close", exact: true }).click();
    await page.locator(".stage__foot .editing-base").getByRole("button", { name: "Continue from here", exact: true }).click();
    await delay(150);
    assert.equal((await snapshot()).editingRunId, historyA.candidateId, "A drawing that could not be saved keeps the editing base");
    assert.equal((await snapshot()).documentView.revisionRef, lastDrawing.revisionRef);
    assert.equal(await page.locator("#document-comment").inputValue(), "Keep the terrace line.");
    annotationFailure = false;
    await page.locator(".document-error button").first().click();
    await until(async () => page.locator(".document-error").count(), (value) => value === 0, "Retry did not save the retained draft");
    await continueFromViewed(currentHome.runId);
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
    await stageAsBase("S1", "S1 · 当前提交", historyA.candidateId);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), JSON.stringify([latest.runId, latest.assetSha256, latest.revisionRef]));
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, latest.revisionRef);
  });

  await step("an only drawing from a different model stays unselected, and undated matching revisions are not guessed", async () => {
    await stageAsBase("S0", "S0", currentHome.runId);
    documents.splice(0, documents.length, { ...lastDrawing, modelSource: { ...lastDrawing.modelSource, assetSha256: "e".repeat(64) }, generatedAt: "2026-09-09T11:00:00.000Z" });
    const before = requests.filter((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).length;
    await stageAsBase("S1", "S1 · 当前提交", historyA.candidateId);
    await page.getByRole("combobox", { name: "Source document", exact: true }).waitFor();
    assert.equal(await page.getByRole("combobox", { name: "Source document", exact: true }).inputValue(), "");
    assert.equal(await page.locator(".document-viewport").count(), 0);
    assert.equal(requests.filter((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).length, before);
    await stageAsBase("S0", "S0", currentHome.runId);
    documents.splice(0, documents.length, { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-undated-a`, generatedAt: null },
      { ...lastDrawing, revisionRef: `${lastDrawing.revisionRef}-undated-b`, generatedAt: null });
    await stageAsBase("S1", "S1 · 当前提交", historyA.candidateId);
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
    await openVersions();
    await view(alternate);
    await openVersions();
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
    await page.evaluate((source) => { window.__candidateDocumentSource = source; }, {
      runId: older.runId, assetSha256: older.assetSha256, pageIndex: 0, revisionRef: older.revisionRef,
    });
    await page.getByTestId("workspace-board").click();
    await page.getByRole("button", { name: "Open registered drawing", exact: true }).click();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    const picker = page.getByRole("combobox", { name: "Source document", exact: true });
    assert.equal(await picker.inputValue(), older.revisionRef, "The Board's exact drawing must not be replaced by newer bytes from another model");
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

  }
  diagnosticsEnabled = true;
  await page.reload({ waitUntil: "domcontentloaded" });
  await until(snapshot, (value) => value.status === "ready" && !value.changingBase, "Diagnostics fixture did not reload");
  await page.evaluate((run) => window.__candidatePreview.changeBase(run), currentHome.runId);
  await view(currentHome);

  await step("one design operation separates manual dwell and waits through download and actual parse", async () => {
    const candidate = prepare("diagnostic-edit"); nextIntent = candidate; intentGate = deferred();
    const bytes = deferred(); modelGates.set(candidate.artifacts[0].sha256, bytes);
    await page.evaluate((name) => { let resolve; const promise = new Promise((done) => { resolve = done; });
      window.__previewParseGates[name] = { waiting: false, promise, resolve }; }, candidate.artifacts[0].fileName);
    await page.evaluate(() => { void window.__candidatePreview.propose("Raise the selected floor"); });
    const root = await until(() => latestDiagnostic("design_edit"), Boolean, "Missing design root");
    assert.equal(root.status, "running");
    const intentRequest = requests.findLast((row) => row.name === "/api/intents");
    assert.equal(intentRequest.headers["x-monkey-operation"], root.operationId);
    intentGate.resolve(); intentGate = null;
    const intent = await finishedDiagnostic("intent_wait", root.operationId);
    assert.equal(requests.filter((row) => row.name === `/api/proposals/${candidate.proposalId}/candidate`).length, 0);
    await delay(120);
    await launch(candidate); await complete(candidate);
    await until(() => diagnosticEvents("model_download").find((row) => row.runId === candidate.candidateId), Boolean, "Missing model download");
    assert.equal(diagnosticEvents("design_edit").filter((row) => row.operationId === root.operationId).at(-1).status, "running");
    bytes.resolve();
    await until(() => page.evaluate((name) => window.__previewParseGates[name].waiting, candidate.artifacts[0].fileName), Boolean, "Parser did not reach real gate");
    assert.equal(diagnosticEvents("design_edit").filter((row) => row.operationId === root.operationId).at(-1).status, "running");
    await page.evaluate((name) => window.__previewParseGates[name].resolve(), candidate.artifacts[0].fileName);
    await diagnosticRendered(candidate);
    const ended = await finishedDiagnostic("design_edit", root.operationId);
    const wait = await finishedDiagnostic("candidate_wait", root.operationId);
    const load = await finishedDiagnostic("model_load", root.operationId);
    const download = await finishedDiagnostic("model_download", root.operationId);
    const parse = await finishedDiagnostic("model_parse", root.operationId);
    const install = await finishedDiagnostic("model_install", root.operationId);
    const projection = await finishedDiagnostic("model_projection", root.operationId);
    assert.equal(ended.eventId, root.eventId);
    assert.equal(ended.details.active_wait_ms, intent.durationMs + wait.durationMs);
    assert.ok(ended.details.between_actions_ms >= 100);
    assert.ok(Math.abs(ended.durationMs - ended.details.active_wait_ms - ended.details.between_actions_ms) < 50);
    assert.equal(wait.parentEventId, root.eventId); assert.equal(load.parentEventId, wait.eventId);
    assert.equal(download.parentEventId, load.eventId); assert.equal(parse.parentEventId, load.eventId);
    assert.equal(install.parentEventId, load.eventId); assert.equal(projection.parentEventId, load.eventId);
    assert.ok(Date.parse(parse.endedAt) <= Date.parse(install.startedAt));
    assert.ok(Date.parse(install.endedAt) <= Date.parse(projection.startedAt));
    assert.equal(projection.runId, candidate.candidateId);
    assert.equal(download.details.input_bytes, models.get(candidate.artifacts[0].sha256).length);
    const fetch = requests.findLast((row) => row.name === `/api/artifacts/${candidate.artifacts[0].sha256}/bytes`);
    assert.equal(fetch.headers["x-monkey-operation"], root.operationId);
    assert.equal(fetch.headers["x-monkey-parent"], download.eventId);
    const polls = diagnosticEvents("api_wait").filter((row) => row.operationId === root.operationId && row.status === "succeeded");
    assert.ok(polls.some((row) => row.details.request_kind === "candidate_poll"));
    assert.ok(polls.some((row) => row.details.request_kind === "candidate_read"));
    assert.equal(loadTimings(candidate.candidateId).length, 0, "New diagnostics must not duplicate the legacy load event");
  });

  await step("superseded candidate roots end cancelled and the replacement owns its own trace", async () => {
    const old = prepare("diagnostic-old"), current = prepare("diagnostic-new");
    await launch(old); const oldRoot = await rootForCandidate(old);
    await launch(current); const newRoot = await rootForCandidate(current);
    assert.notEqual(oldRoot.operationId, newRoot.operationId);
    await finishedDiagnostic("design_edit", oldRoot.operationId, "cancelled");
    await complete(old); await complete(current); await diagnosticRendered(current);
    await finishedDiagnostic("design_edit", newRoot.operationId);
    for (const candidate of [old, current]) {
      const request = requests.findLast((row) => row.name === `/api/proposals/${candidate.proposalId}/candidate`);
      assert.equal(request.headers["x-monkey-operation"], candidate === old ? oldRoot.operationId : newRoot.operationId);
    }
  });

  await step("job failure ends the root and reporting failure never repeats user work", async () => {
    const broken = prepare("diagnostic-job-failed"); await launch(broken); const failedRoot = await rootForCandidate(broken);
    jobs.get(broken.jobId).status = "failed"; await emit("candidate.failed", broken.candidateId);
    await finishedDiagnostic("design_edit", failedRoot.operationId, "failed");
    monitorFailure = true;
    const candidate = prepare("diagnostic-monitor-failed"); await launch(candidate); const root = await rootForCandidate(candidate);
    await complete(candidate); await diagnosticRendered(candidate); await finishedDiagnostic("design_edit", root.operationId);
    assert.equal(requests.filter((row) => row.name === `/api/proposals/${candidate.proposalId}/candidate`).length, 1);
    assert.equal(requests.filter((row) => row.name === `/api/artifacts/${candidate.artifacts[0].sha256}/bytes`).length, 1);
    monitorFailure = false;
  });

  await step("delayed initial telemetry cannot overwrite a finished event or hold the viewer", async () => {
    timingGate = deferred(); const candidate = prepare("diagnostic-report-order");
    await launch(candidate); const root = await rootForCandidate(candidate);
    await complete(candidate); await diagnosticRendered(candidate);
    assert.equal(diagnosticEvents("design_edit").filter((row) => row.operationId === root.operationId).length, 1);
    timingGate.resolve(); timingGate = null;
    await finishedDiagnostic("design_edit", root.operationId);
    assert.deepEqual(diagnosticEvents("design_edit").filter((row) => row.operationId === root.operationId).map((row) => row.status), ["running", "succeeded"]);
  });

  await step("applying the same proposal twice gives the second candidate an independent action", async () => {
    await view(currentHome); // the composer asks from the editing base, not the candidate on screen
    const first = prepare("diagnostic-apply-first"), second = prepare("diagnostic-apply-second");
    second.proposalId = first.proposalId; jobs.get(second.jobId).proposalId = first.proposalId;
    candidateStartQueues.set(first.proposalId, [first, second]); nextIntent = first;
    await page.evaluate(() => window.__candidatePreview.propose("Adjust the floor again"));
    await launch(first); const firstRoot = await rootForCandidate(first);
    await launch(second); const secondRoot = await rootForCandidate(second);
    assert.notEqual(firstRoot.operationId, secondRoot.operationId);
    await finishedDiagnostic("design_edit", firstRoot.operationId, "cancelled");
    await finishedDiagnostic("candidate_wait", firstRoot.operationId, "cancelled");
    await complete(first); await complete(second); await diagnosticRendered(second);
    await finishedDiagnostic("design_edit", secondRoot.operationId);
    const waits = diagnosticEvents("candidate_wait").filter((row) => row.status === "running" && [firstRoot.operationId, secondRoot.operationId].includes(row.operationId));
    assert.equal(waits.length, 2); assert.notEqual(waits[0].eventId, waits[1].eventId);
  });

  await step("dropping a local model cancels a candidate still parsing", async () => {
    const candidate = prepare("diagnostic-local-drop");
    await page.evaluate((name) => { let resolve; const promise = new Promise((done) => { resolve = done; });
      window.__previewParseGates[name] = { waiting: false, promise, resolve }; }, candidate.artifacts[0].fileName);
    await launch(candidate); const root = await rootForCandidate(candidate); await complete(candidate);
    await until(() => page.evaluate((name) => window.__previewParseGates[name].waiting, candidate.artifacts[0].fileName), Boolean, "Candidate did not pause in the actual parser");
    await page.evaluate((bytes) => {
      const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(bytes)], "dropped-local.3dm"));
      document.querySelector(".viewport-host").dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
    }, [...models.get(currentHome.sha256)]);
    const imported = `model-upload-${currentHome.sha256}`;
    await until(snapshot, (value) => value.status === "ready" && value.loadedRunId === imported, "Local drop did not become the displayed model");
    await finishedDiagnostic("design_edit", root.operationId, "cancelled");
    await page.evaluate((name) => window.__previewParseGates[name].resolve(), candidate.artifacts[0].fileName);
    await finishedDiagnostic("model_load", root.operationId, "cancelled");
    assert.equal((await snapshot()).loadedRunId, imported, "The retained local file stays on screen, not the cancelled candidate");
    assert.equal(diagnosticEvents("design_edit").filter((row) => row.operationId === root.operationId && row.status === "succeeded").length, 0);
    await view(currentHome);
  });

  if (!modelTimingOnly) {
  await step("an opened drawing becomes ready only after its own bytes are downloaded and painted", async () => {
    documentGate = deferred();
    await page.evaluate(() => { let resolve; const promise = new Promise((done) => { resolve = done; });
      window.__documentRenderGate = { waiting: false, promise, resolve }; });
    const drawing = registerDrawing(currentHome.modelSource);
    await openDrawingFromBoard(drawing);
    await until(() => documentGate.requested, Boolean, "Drawing bytes did not start");
    assert.equal(await page.locator('.document-viewport[data-ready="true"]').count(), 0,
                 "A drawing cannot be ready while its bytes are still being downloaded");
    documentGate.resolve(); documentGate = null;
    await until(() => page.evaluate(() => window.__documentRenderGate.waiting), Boolean, "Drawing did not reach actual render gate");
    assert.equal(await page.locator('.document-viewport[data-ready="true"]').count(), 0,
                 "A drawing cannot be ready while it is still being painted");
    await page.evaluate(() => { window.__documentRenderGate.resolve(); delete window.__documentRenderGate; });
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.equal((await snapshot()).documentView.revisionRef, drawing.revisionRef);
    assert.equal(requests.findLast((row) => /^\/api\/documents\/.+\/bytes$/.test(row.name)).query.revisionRef, drawing.revisionRef);
    await page.getByTestId("workspace-arch").click();
    await until(snapshot, (value) => !value.documentView.open, "Returning from an opened drawing must restore the model tools");
  });

  await step("an initial project with no retained run reports project-only timing", async () => {
    projectionOnly = true; historyEnabled = false; projectId = "initial-projection-project";
    await page.reload({ waitUntil: "domcontentloaded" });
    await until(snapshot, (value) => value.projectId === projectId && value.editingRunId === "studio-projection" && !value.changingBase,
      "Initial project projection did not become available");
    await page.evaluate(() => window.__candidatePreview.propose("Create the first candidate"));
    const request = requests.findLast((row) => row.name === "/api/intents");
    const root = await finishedDiagnostic("design_edit", request.headers["x-monkey-operation"], "failed");
    const intent = await finishedDiagnostic("intent_wait", root.operationId, "failed");
    assert.equal(root.runId, null); assert.equal(intent.runId, null);
    assert.equal(root.sourceRef, null);
    assert.equal(diagnosticEvents().some((row) => row.runId === "studio-projection"), false);
  });
  }

  if (!modelTimingOnly) {
    await step("parameter locks retain the selected values on an exact candidate continued explicitly, reopen, and unlock without accepting a Stage", async () => {
      projectionOnly = false; historyEnabled = true; diagnosticsEnabled = false; projectId = "parameter-lock-fixture";
      branches.clear(); stages.clear(); candidateBases.clear();
      currentHome = makeArtifact("parameter-lock-base"); allArtifacts.push(currentHome);
      const stage = commitStage(currentHome.modelSource, "main", "S0");
      parameterStates.set(currentHome.runId, [
        { key: "height", value: 3.6, unit: "m", expr: null, inputs: [], epistemicStatus: "authored", lockAuthority: null },
        { key: "width", value: 12, unit: "m", expr: null, inputs: [], epistemicStatus: "authored", lockAuthority: null },
      ]);
      await page.reload({ waitUntil: "domcontentloaded" }); await rendered(currentHome.runId);
      await openViewTools(); await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      let panel = page.getByRole("region", { name: "Parameter locks", exact: true });
      assert.equal(await panel.getByRole("button", { name: "Lock selected", exact: true }).isDisabled(), true);
      await panel.getByRole("checkbox", { name: /height/ }).check();
      const candidate = nextLockCandidate = prepare("parameter-locked");
      await panel.getByRole("button", { name: "Lock selected", exact: true }).click();
      await until(snapshot, value => value.runs[candidate.candidateId]?.job.status === "ready", "Lock action did not start the retained candidate");
      const request = requests.findLast(row => row.name === "/api/proposals/parameter-locks");
      assert.deepEqual(request.body.parameterKeys, ["height"]);
      assert.equal(request.body.sourceRunId, currentHome.runId); assert.equal(request.body.sourceStageRef, stage.stageRef);
      await complete(candidate); await diagnosticRendered(candidate);
      await continueFromViewed(candidate.candidateId);
      assert.equal(stages.size, 1);
      await page.reload({ waitUntil: "domcontentloaded" }); await rendered(candidate.candidateId);
      await openViewTools(); await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      panel = page.getByRole("region", { name: "Parameter locks", exact: true });
      assert.match(await panel.locator("label").filter({ hasText: "height" }).innerText(), /height\s+3\.6 m\s+Locked/);
      assert.match(await panel.locator("label").filter({ hasText: "width" }).innerText(), /Editable/);
      await panel.getByRole("checkbox", { name: /height/ }).check();
      const unlocked = nextLockCandidate = prepare("parameter-unlocked");
      await panel.getByRole("button", { name: "Unlock selected", exact: true }).click();
      await until(snapshot, value => value.runs[unlocked.candidateId]?.job.status === "ready", "Unlock action did not start the retained candidate");
      await complete(unlocked); await diagnosticRendered(unlocked);
      await continueFromViewed(unlocked.candidateId);
      assert.equal(parameterStates.get(unlocked.candidateId)[0].lockAuthority, null);
      assert.equal(stages.size, 1);
    });

    await step("a delayed lock proposal cannot start a candidate after changing the editing base", async () => {
      await openViewTools(); await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      const panel = page.getByRole("region", { name: "Parameter locks", exact: true });
      await panel.getByRole("checkbox", { name: /height/ }).check();
      lockProposalGate = deferred(); const candidate = nextLockCandidate = prepare("parameter-stale");
      await panel.getByRole("button", { name: "Lock selected", exact: true }).click();
      await until(() => lockProposalGate.requested, Boolean, "Lock proposal was not submitted");
      await page.evaluate(run => window.__candidatePreview.changeBase(run), currentHome.runId);
      await until(snapshot, value => value.editingRunId === currentHome.runId && !value.changingBase, "Base did not change during the held lock proposal");
      lockProposalGate.resolve(); lockProposalGate = null;
      await until(() => panel.getByText("Saving…", { exact: true }).count(), value => value === 0, "Held lock proposal did not settle");
      assert.equal(requests.filter(row => row.name === `/api/proposals/${candidate.proposalId}/candidate`).length, 0);
      assert.equal(stages.size, 1);
    });

    await step("unresolved, browsed and local models cannot change the editing base's parameter locks", async () => {
      await view(currentHome);
      await openViewTools(); await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      const panel = page.getByRole("region", { name: "Parameter locks", exact: true });
      await page.keyboard.press("Escape");
      assert.equal(await panel.count(), 0);
      await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      await panel.getByRole("checkbox", { name: /height/ }).check();
      const requestCount = requests.filter(row => row.name === "/api/proposals/parameter-locks").length;
      const alternative = makeArtifact("parameter-lock-other"); allArtifacts.push(alternative);
      const held = deferred(); stateGates.set(alternative.runId, held);
      await view(alternative);
      assert.equal(await panel.getByRole("button", { name: "Lock selected", exact: true }).isDisabled(), true);
      held.resolve(); stateGates.delete(alternative.runId);
      await view(currentHome);
      await until(() => panel.getByRole("button", { name: "Lock selected", exact: true }).isEnabled(), Boolean, "Returning to the edit base did not re-enable locking");
      await page.evaluate(bytes => {
        const transfer = new DataTransfer(); transfer.items.add(new File([new Uint8Array(bytes)], "local-parameters.3dm"));
        document.querySelector(".viewport-host").dispatchEvent(new DragEvent("drop", { bubbles: true, dataTransfer: transfer }));
      }, [...models.get(currentHome.sha256)]);
      await until(snapshot, value => value.status === "ready" && value.loadedRunId === `model-upload-${currentHome.sha256}`,
        "Local model was not displayed");
      assert.equal(await panel.getByRole("button", { name: "Lock selected", exact: true }).isDisabled(), true);
      assert.equal(requests.filter(row => row.name === "/api/proposals/parameter-locks").length, requestCount);
      await view(currentHome);
    });

    await step("a delayed lock proposal stays cancelled after browsing away and back to the same base", async () => {
      await openViewTools(); await page.getByRole("button", { name: "Parameter locks", exact: true }).click();
      const panel = page.getByRole("region", { name: "Parameter locks", exact: true });
      await panel.getByRole("checkbox", { name: /height/ }).check();
      lockProposalGate = deferred(); const candidate = nextLockCandidate = prepare("parameter-returned-view");
      await panel.getByRole("button", { name: "Lock selected", exact: true }).click();
      await until(() => lockProposalGate.requested, Boolean, "Lock proposal was not submitted");
      await view(allArtifacts.find(row => row.runId === "parameter-lock-other"));
      await view(currentHome);
      lockProposalGate.resolve(); lockProposalGate = null;
      await until(() => panel.getByText("Saving…", { exact: true }).count(), value => value === 0, "Held lock proposal did not settle");
      assert.equal(requests.filter(row => row.name === `/api/proposals/${candidate.proposalId}/candidate`).length, 0);
      assert.equal(stages.size, 1);
    });
  }
  }
  assert.deepEqual(errors, []);
  console.log(`Passed ${passed.length} candidate preview scenarios; actual 3DM files parsed in an isolated headless browser.`);
} catch (error) {
  if (process.env.CANDIDATE_PREVIEW_EVIDENCE && page) {
    const destination = path.resolve(process.env.CANDIDATE_PREVIEW_EVIDENCE);
    await mkdir(destination, { recursive: true });
    await page.screenshot({ path: path.join(destination, 'candidate-preview.png') }).catch(() => {});
    await writeFile(path.join(destination, 'candidate-preview.html'), await page.content()).catch(() => {});
  }
  throw error;
} finally {
  historyGate?.resolve();
  lockProposalGate?.resolve();
  intentGate?.resolve(); documentGate?.resolve(); timingGate?.resolve();
  for (const gate of [...validationGates.values(), ...modelGates.values(), ...stateGates.values()]) gate.resolve();
  await browser?.close();
  if (http.listening) await new Promise((resolve, reject) => http.close((error) => error ? reject(error) : resolve()));
  await vite?.close();
  assert.equal(path.dirname(path.resolve(cacheDir)), path.resolve(tmpdir()));
  assert.ok(path.basename(cacheDir).startsWith("monkeyarch-candidate-preview-test-"));
  await rm(cacheDir, { recursive: true, force: true });
}
