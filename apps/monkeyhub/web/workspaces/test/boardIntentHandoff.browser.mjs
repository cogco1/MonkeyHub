import { workspaceFixture } from "./workspaceFixture.mjs";
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

// Exercise the real ProjectWorkspace -> App -> compileIntent path with a saved
// Board request. The tiny Board button stands in for its separately tested
// save/render work; every API read and write is intercepted in memory.
//
// Modeling is mounted behind the Board and opens on the project's head, Current,
// as it does in the Hub; the Board hands a note over in place, without a
// Documents view or a URL of its own. The note was made on an older Stage, so
// Modeling asks first (#302): View only is the focused default, and Continue
// from here is what makes the note's exact model and Stage the editing base and
// submits it. A refused note is kept unsent and is never replayed.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "board-intent-handoff-"));
const projectId = "board-intent-fixture", runId = "drawing-model", stateDigest = "1".repeat(64);
const published = { version: 0, stateSha256: "2".repeat(64) };
const referenceRun = { runId, baseVersion: 0, baseSha256: published.stateSha256 };
const rhino = await rhino3dm();
/** One named triangle as 3DM bytes; the two models differ so their reads can be told apart. */
function modelBytes(name, offset) {
  const document = new rhino.File3dm(), mesh = new rhino.Mesh(), attributes = new rhino.ObjectAttributes();
  mesh.vertices().add(offset, 0, 0); mesh.vertices().add(offset + 1, 0, 0); mesh.vertices().add(offset, 1, 0);
  mesh.faces().addTriFace(0, 1, 2);
  attributes.name = name;
  document.objects().add(mesh, attributes);
  const bytes = Buffer.from(document.toByteArray()); document.delete(); mesh.delete(); attributes.delete();
  return bytes;
}
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
// The drawing's exact model, on an older Stage, and Current's model at the branch head.
const bytes = modelBytes("floor", 0), headBytes = modelBytes("roof", 3);
const modelSource = { runId, stateDigest, assetSha256: sha256(bytes) };
const headSource = { runId: "default-head", stateDigest: "a".repeat(64), assetSha256: sha256(headBytes) };
const sourceStageRef = "historical-stage", headStageRef = "current-stage";
const documentSha = "d".repeat(64), revisionRef = "drawing-revision";
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
// Each run answers with its own record: the session refuses a projection of another run or Stage.
const headState = { ...state, referenceRun: { ...referenceRun, runId: headSource.runId }, sourceStageRef: headStageRef,
  stateDigest: headSource.stateDigest };
const stage = (stageRef, source) => ({ stageRef, modelSource: source, label: stageRef, branchId: "main",
  stageId: "stage-2", createdAt: "2026-09-09T00:00:00Z", parentStageRef: null });
const history = { projectId, branchId: "main", branches: [{ branchId: "main", label: "Main", headStageRef }],
  stages: [stage(sourceStageRef, modelSource), stage(headStageRef, headSource)], explorations: [] };
const artifactOf = (source, fileName, sizeBytes) => ({ artifactId: source.assetSha256, runId: source.runId, modelSource: source,
  stageId: "stage-2", fileName, relativePath: null, sha256: source.assetSha256, sizeBytes, objectCount: 1, status: "registered",
  readbackVerified: null, available: true, unavailableReason: null, base: published, branchId: "main", branchEpoch: 0,
  programRef: null, programDigest: null, designStateDigest: source.stateDigest, receiptRef: null, format: "3dm", representation: "composed" });
const artifacts = [artifactOf(modelSource, "drawing-model.3dm", bytes.length), artifactOf(headSource, "head-model.3dm", headBytes.length)];
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
// A note refused before it was sent says why in the Board's own words (ErrorPanel, MonkeyBoard).
const BOARD_REFUSAL = {
  "changed-stage": "This drawing no longer matches its saved model version. Your feedback was not sent; the original instruction is kept below.",
  "unavailable-base": "This drawing's saved model could not be opened. Your feedback was not sent; the original instruction is kept below.",
};
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(read, test, message, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (test(value)) return value;
    assert.ok(Date.now() < deadline, message);
    await delay(50);
  }
}
let browser, vite, currentCase, page;
const http = createHttpServer(), failures = [], escaped = [], passed = [], failed = [];
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir, publicDir: "../.generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") }, plugins: [workspaceFixture(), { name: "saved-board-request-fixture", enforce: "pre",
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
    history.branches[0].headStageRef = headStageRef;
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const request = structuredClone(handoff);
    if (scenario === "wrong-project") request.projectId = "other-project";
    await context.addInitScript((request) => { window.__boardRequest = request; }, request);
    page = await context.newPage(); page.setDefaultTimeout(15_000);
    const intents = [], stateReads = [], mutations = [], modelReads = [];
    const scenarioFailures = failures.length;
    let recovered = false, unsaid = null;
    page.on("pageerror", (error) => failures.push(`${scenario}: ${error.message}`));
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
            const read = Object.fromEntries(url.searchParams);
            stateReads.push(read);
            if (read.run === headSource.runId) return await json(headState);
            if (scenario === "unavailable-base" && !recovered) return await json({
              code: "EDITING_BASE_UNAVAILABLE", detail: "The drawing model is temporarily unavailable." }, 503);
            return await json({ ...state, ...(scenario === "changed-stage" && !recovered ? { sourceStageRef: "different-stage" } : {}) });
          }
          if (url.pathname === "/api/artifacts") return await json({ projectId, artifacts });
          const model = artifacts.find((row) => url.pathname === `/api/artifacts/${row.sha256}/bytes`);
          if (model) {
            modelReads.push(model.sha256);
            return await route.fulfill({ status: 200, contentType: "application/octet-stream", body: model.runId === runId ? bytes : headBytes });
          }
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
    const composer = () => page.locator(".composer input");
    const choice = () => page.getByRole("group", { name: "The Board note's model version", exact: true });
    const refusals = () => page.locator(".card--refusal");
    /** After Modeling has read again, give a replay the time it would take; nothing more may be sent. */
    async function nothingMoreSent(sent, why) {
      await delay(400);
      assert.equal(intents.length, sent, why);
    }
    try {
      // The first scenario also waits for Vite's dependency scan, which alone can take half a minute.
      await page.goto(`${origin}/?view=board&lang=en`, { waitUntil: "domcontentloaded", timeout: 180_000 });
      await page.getByRole("button", { name: "Submit saved Board comments" }).click({ timeout: 60_000 });
      // The Board hands the note over in place: Modeling shows, the Board hides, no page of its own.
      await page.locator('[data-project-surface="arch"]:not([hidden]) .stage').waitFor();
      assert.equal(await page.locator('[data-project-surface="board"]').isHidden(), true);
      assert.equal(new URL(page.url()).searchParams.get("view"), "board", "The handoff does not rewrite the host's navigation");
      await page.locator("#conversation-panel").waitFor();
      if (scenario === "wrong-project") {
        // A note from another project is refused on arrival, before any question.
        await refusals().first().waitFor();
        assert.match(await refusals().first().textContent(), /The board request belongs to another project/);
        assert.equal(await choice().count(), 0, "A note from another project is not offered as a base");
        assert.equal(await composer().inputValue(), handoff.utterance);
        assert.deepEqual(intents, []); assert.deepEqual(mutations, []);
      } else {
        // #302: a note on another model version asks first, naming it; only looking is the default.
        await choice().waitFor();
        assert.match(await choice().innerText(), scenario === "changed-model"
          ? /made on another model version: drawing-model\.3dm\./ : /made on another model version: historical-stage\./);
        assert.equal(await page.evaluate(() => document.activeElement?.textContent), "View only", "Only looking is the default choice");
        await delay(200);
        assert.deepEqual(intents, [], "Nothing is submitted before the architect chooses");
        assert.deepEqual(mutations, []);
        assert.ok(stateReads.every((read) => read.run === headSource.runId), JSON.stringify(stateReads));
        const readsBefore = stateReads.length, modelReadsBefore = modelReads.length;
        await choice().getByRole("button", { name: "Continue from here", exact: true }).click();
        if (scenario === "compiled" || scenario === "clarification") {
          await page.locator(scenario === "clarification" ? ".card--question" : ".card--proposal").waitFor();
          assert.equal(intents.length, 1, "The saved request is compiled exactly once");
          // Current's own refresh may still land after the click; from the note's first
          // read on, only the note's run is read, starting at its exact Stage.
          const continued = stateReads.slice(readsBefore), first = continued.findIndex((read) => read.run === runId);
          assert.ok(first >= 0 && continued.slice(first).every((read) => read.run === runId), JSON.stringify(continued));
          assert.equal(continued[first].sourceStageRef, sourceStageRef, JSON.stringify(continued));
          const loaded = modelReads.slice(modelReadsBefore), drawn = loaded.indexOf(modelSource.assetSha256);
          assert.ok(drawn >= 0 && loaded.slice(drawn).every((sha) => sha === modelSource.assetSha256),
            `Current's model must not load over the drawing's exact model: ${JSON.stringify(loaded)}`);
          assert.equal(await choice().count(), 0);
          if (scenario === "clarification") {
            await composer().fill("Use 0.2 metres");
            await page.locator(".composer button[type=submit]").click();
            await page.locator(".card--proposal").waitFor(); assert.equal(intents.length, 2);
          }
        } else {
          await page.locator(".card--refusal, .refusal__card").first().waitFor();
          await delay(200);
          assert.deepEqual(intents, []); assert.deepEqual(mutations, []);
          assert.ok((await page.locator("body").innerText()).includes(handoff.utterance) ||
            await composer().inputValue().catch(() => "") === handoff.utterance,
          "A refused handoff must keep the unsent instruction visible or in the composer");
          if (scenario in BOARD_REFUSAL) {
            // Checked last, so the recovery below still runs when the reason is missing.
            if (!await page.getByText(BOARD_REFUSAL[scenario], { exact: true }).waitFor({ timeout: 3_000 }).then(() => true, () => false)) {
              unsaid = `A note refused on Continue must say why in the Board's words: "${BOARD_REFUSAL[scenario]}". ` +
                `Shown instead: ${JSON.stringify(await page.locator(".error-panel__reason").allInnerTexts())}`;
            }
            // Correcting the source and explicitly reopening its model must not
            // resume the refused one-time submission behind the architect's back.
            recovered = true;
            history.branches[0].headStageRef = sourceStageRef;
            const readsAtRecovery = stateReads.length;
            if (await page.locator(".refusal__card").count()) {
              await page.locator(".refusal__card").getByRole("button", { name: "Retry", exact: true }).click();
              await page.locator(".refusal__card").waitFor({ state: "hidden" });
            } else {
              await page.getByRole("button", { name: "Return to default editing base", exact: true }).click();
            }
            await until(() => stateReads.slice(readsAtRecovery), (reads) => reads.some((read) => read.run === runId),
              "The explicit reopening did not read the note's model");
            await page.locator('[data-project-surface="arch"] .stage').waitFor();
            await nothingMoreSent(0, "Reopening the note's model must not resume its refused submission");
            assert.equal(await composer().inputValue(), handoff.utterance);
            assert.equal(await composer().isVisible(), true);
            assert.equal(await refusals().count(), 1, "Recovery must not duplicate the refusal");
            assert.equal(await choice().count(), 0, "A refused note is not offered again");
            assert.deepEqual(mutations, []);
          }
        }
      }
      // A reload drops the one-time note: Modeling reads behind the Board and nothing is sent again.
      // It reopens on the base the architect last chose, which may itself refuse (changed-model).
      const sent = intents.length, readsAtReload = stateReads.length;
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: "Submit saved Board comments" }).waitFor();
      await until(() => stateReads.length, (count) => count > readsAtReload, "Modeling did not read the project after the reload");
      await page.getByTestId("workspace-arch").click();
      await page.locator('[data-project-surface="arch"]:not([hidden]) :is(.stage, .refusal__card)').first().waitFor();
      await nothingMoreSent(sent, "Refresh must not replay the one-time intent");
      assert.equal(await choice().count(), 0, "Refresh must not ask about the one-time note again");
      assert.deepEqual(failures.slice(scenarioFailures), []);
      assert.equal(unsaid, null, unsaid);
      passed.push(scenario); console.log(`PASS ${scenario}`);
    } catch (error) {
      failed.push(scenario);
      failures.push(`${scenario}: ${error.stack ?? error}`);
      console.error(`FAIL ${scenario}: ${error.message}`);
      console.error(JSON.stringify({ visible: await page.locator("body").innerText().catch(() => "") }));
    }
    // Unmount the workspace while API interception is still active. Closing the
    // context directly can remove its page routes before a new read starts.
    await page.goto("about:blank");
    await context.close(); page = null;
  }
  assert.deepEqual(escaped, []); console.log(JSON.stringify({ passed, failed }));
  assert.deepEqual(failed, [], failures.join("\n"));
} catch (error) {
  console.error(JSON.stringify({ currentCase, failures, escaped }));
  throw error;
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
