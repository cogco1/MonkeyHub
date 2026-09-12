/**
 * The keys an architect already has in their fingers, against a real service.
 *
 * Nothing is stubbed here: a temporary P036 project is built by the Studio's
 * own fixture, the real API serves it with the real OCCT export, and every
 * assertion about what happened is read back out of the ``.3dm`` the run
 * saved — not out of a button's disabled attribute. The page is opened the way
 * the Hub embeds it, on a candidate that is *not* the project's default
 * reference run, because that is the case where a pick used to light the object
 * and form no selection at all.
 *
 * What it holds to, in one pass:
 *   - a rectangle drawn with a typed height comes out of the exporter at that
 *     height, on the Z-up axis the viewer draws it on;
 *   - a picked element is deleted and nothing beside it is;
 *   - Ctrl+Z returns to the run before the delete, whose model still has the
 *     object; Ctrl+Y goes forward again;
 *   - an edit made after an undo drops the redo branch and keeps every run;
 *   - Delete and Ctrl+Z inside a text field belong to the text;
 *   - Esc clears the selection and changes no model;
 *   - a held key is one deletion, not a stream of them.
 *
 * No user service, project, browser profile or accepted design is touched.
 */

import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer as createHttpServer, request as httpRequest } from "node:http";
import { mkdtemp, readdir, rm, stat } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import rhino3dm from "rhino3dm";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = path.resolve(webRoot, "../../..");
const apiRoot = path.resolve(webRoot, "../api");
const python = process.env.PYTHON ?? "python";
const rhino = await rhino3dm();
const root = await mkdtemp(path.join(tmpdir(), "monkeyarch-model-shortcuts-"));
const projectDir = path.join(root, "demo-project");
const errors = [];
let api, vite, browser, page, closing = false;
const http = createHttpServer();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fail(message) {
  errors.push(message);
  return false;
}

// ---- the project, built by the API's own fixture so nothing here invents one

const build = spawnSync(python, ["-c", `
import sys
sys.path.insert(0, r"${apiRoot.replaceAll("\\", "/")}")
from tests.support import make_project
make_project(r"${root.replaceAll("\\", "/")}")
print("built")
`], { cwd: apiRoot, encoding: "utf8" });
assert.equal(build.status, 0, `fixture project failed: ${build.stderr || build.stdout}`);

// ---- the real Studio API, on a port of its own, exporting real geometry

const apiPort = await new Promise((resolve) => {
  const probe = createHttpServer();
  probe.listen(0, "127.0.0.1", () => {
    const { port } = probe.address();
    probe.close(() => resolve(port));
  });
});
api = spawn(python, ["-m", "archflow_studio_api.main", "--port", String(apiPort), "--project-dir", projectDir], {
  cwd: apiRoot,
  env: { ...process.env, ARCHFLOW_STUDIO_CAD_EXPORT: "occt", PYTHONUTF8: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});
api.stderr.on("data", (chunk) => { const line = String(chunk); if (line.includes("Traceback")) errors.push(line); });
const apiOrigin = `http://127.0.0.1:${apiPort}`;

async function apiReady() {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    try {
      const answer = await fetch(`${apiOrigin}/api/health`);
      if (answer.ok) return true;
    } catch { /* not listening yet */ }
    await delay(100);
  }
  return false;
}
assert.ok(await apiReady(), "the Studio API never started");

async function call(method, route, body) {
  const answer = await fetch(apiOrigin + route, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await answer.text();
  const value = text === "" ? null : JSON.parse(text);
  if (!answer.ok) throw new Error(`${method} ${route} -> ${answer.status} ${text}`);
  return value;
}

async function finished(jobId) {
  for (let attempt = 0; attempt < 3000; attempt += 1) {
    const job = await call("GET", `/api/jobs/${jobId}`);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await delay(100);
  }
  throw new Error(`job ${jobId} never finished`);
}

/** Every named object of one run's exported model, with the box it occupies. */
async function exported(runId) {
  const runDir = path.join(projectDir, "runs", runId);
  const found = new Map();
  const walk = async (directory) => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.name.endsWith(".3dm")) {
        const document = rhino.File3dm.fromByteArray(readFileSync(full));
        const objects = document.objects();
        for (let index = 0; index < objects.count; index += 1) {
          const item = objects.get(index);
          const name = item.attributes().name;
          if (!name) continue;
          const box = item.geometry().getBoundingBox();
          found.set(name, {
            x: Number((box.max[0] - box.min[0]).toFixed(3)),
            y: Number((box.max[1] - box.min[1]).toFixed(3)),
            z: Number((box.max[2] - box.min[2]).toFixed(3)),
          });
        }
        document.delete();
      }
    }
  };
  await stat(runDir);
  await walk(runDir);
  return found;
}

async function runIds() {
  return (await readdir(path.join(projectDir, "runs"), { withFileTypes: true }))
    .filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort();
}

// ---- one candidate ahead of the page, so the embed opens on a run the
//      project's default projection is not on.

const home = await call("GET", "/api/state");
const seedProposal = await call("POST", "/api/proposals/sketch", {
  stateDigest: home.stateDigest, componentId: "portico", elementId: "seed-block",
  profile: [[10, 0], [12, 0], [12, 2], [10, 2]], height: 1.5, baseLevel: "level-ground",
});
const seedRun = (await finished((await call("POST", `/api/proposals/${seedProposal.proposalId}/candidate`)).jobId)).candidateId;
assert.ok((await exported(seedRun)).has("obj-seed-block"), "the seed candidate exported nothing");

// ---- the app, served by vite, talking to that API through this origin

vite = await createServer({
  root: webRoot,
  configFile: false,
  logLevel: "error",
  publicDir: ".generated/public",
  define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
  cacheDir: path.join(root, "vite-cache"),
  plugins: [{
    name: "model-shortcuts-probe",
    enforce: "pre",
    transform(source, id) {
      const modulePath = id.split("?")[0].replaceAll("\\", "/");
      if (modulePath !== `${webRoot.replaceAll("\\", "/")}/src/app/App.tsx`) return;
      const marker = '  const booting = !canOpenDocuments && (session.status === "idle" || session.status === "loading");';
      assert.equal(source.split(marker).length, 2);
      return {
        code: source.replace(marker, marker + `
          (window as unknown as { __shortcuts: unknown }).__shortcuts = {
            loadedRunId: loadedArtifact?.runId ?? null,
            editingRunId: projection?.referenceRun.runId ?? null,
            status: viewerStatus,
            loading: modelLoading,
            picked: picked?.elementId ?? null,
            pickedStatus: picked?.status ?? null,
            selection: selection?.elementId ?? null,
            deletable: deletableElementId,
            canDelete: canDeleteModel,
            canUndo: canUndoModel,
            canRedo: canRedoModel,
            busy: modelNavigationBusy,
            history: modelHistory.runs,
            historyIndex: modelHistory.index,
            ink: gestures.length,
            viewedSource,
            viewArtifact: (artifact: ProjectArtifactDto) => {
              manualLoadRef.current = true;
              return loadArtifactIntoViewer(artifact, artifact.fileName);
            },
            documentOpen: documentView.open,
            // The embedded tool page carries no workspace switcher of its own:
            // the host rail does. This sets the same state that rail sets.
            openDocuments: (open: boolean) =>
              setDocumentView((current) => ({ ...current, mounted: true, open })),
            entries: transcript.entries.map((entry) => entry.kind === "system" ? entry.text
              : entry.kind === "refusal" ? \`refusal:\${entry.error.code}:\${entry.error.detail}\` : entry.kind),
          };`),
        map: null,
      };
    },
  }, react()],
  server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
});
const sent = [];
http.on("request", (request, response) => {
  if (!request.url?.startsWith("/api/")) { vite.middlewares(request, response); return; }
  if (request.method === "POST" || request.method === "PUT") {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      sent.push({ method: request.method, path: request.url,
        body: body.startsWith("{") ? JSON.parse(body) : body });
    });
  }
  const proxied = httpRequest({ host: "127.0.0.1", port: apiPort, path: request.url,
    method: request.method, headers: { ...request.headers, host: `127.0.0.1:${apiPort}` } }, (answer) => {
    response.writeHead(answer.statusCode ?? 502, answer.headers);
    answer.pipe(response);
  });
  proxied.on("error", (cause) => {
    if (!closing) errors.push(`proxy: ${cause.message}`);
    response.writeHead(502); response.end();
  });
  request.pipe(proxied);
});
await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${http.address().port}`;

const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ??
  "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
await context.addInitScript(() => {
  localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({
    version: 1, language: "en", theme: "light", fontScale: 1,
    eventStreamVisible: false, developerMode: true, editingBases: {},
  }));
  // The live event stream is a long-lived response; this test proxies plain
  // requests and drives the app by its own reads instead.
  class FakeEventSource {
    constructor() { queueMicrotask(() => this.onopen?.()); }
    addEventListener() {}
    removeEventListener() {}
    close() {}
  }
  window.EventSource = FakeEventSource;
});
page = await context.newPage();
page.on("pageerror", (error) => errors.push(error.message));
page.on("requestfailed", (request) => {
  if (!closing) errors.push(`request failed: ${request.url()}`);
});
page.on("response", (answer) => {
  // A 404 the page asks for is a missing asset, and a missing asset is a
  // difference between this run and a real one. The dev server has no
  // favicon, which is the one this test allows.
  if (answer.status() === 404 && !answer.url().endsWith("/favicon.ico") && !closing) {
    errors.push(`404: ${answer.url()}`);
  }
});

const snapshot = () => page.evaluate(() => window.__shortcuts);
const modelHistoryOf = (state) => state?.history ?? [];

async function settled(predicate, what, timeout = 120000) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await snapshot();
    if (last && predicate(last)) return last;
    await delay(120);
  }
  throw new Error(`${what} — last saw ${JSON.stringify(last)}`);
}

await page.goto(`${origin}/?embedded=tool&candidate=${seedRun}`);
const opened = await settled(
  (state) => state.status === "ready" && state.loadedRunId === seedRun && state.editingRunId === seedRun,
  "the embedded page never showed the candidate it was opened on",
);
assert.equal(opened.editingRunId, seedRun,
  "the embed edits the candidate it was opened on, not the project's default run");

const canvas = await page.locator("canvas").first().boundingBox();
assert.ok(canvas, "no viewport canvas");
const centre = { x: Math.round(canvas.x + canvas.width / 2), y: Math.round(canvas.y + canvas.height / 2) };


// Where the objects are on screen is the camera's business, so the click that
// finds one is looked for: a coarse sweep of the canvas, nearest the middle
// first, stopping at the first ray that the server resolves to this element.
const sweep = [];
for (let x = canvas.x + 50; x < canvas.x + canvas.width - 50; x += 70) {
  for (let y = canvas.y + 50; y < canvas.y + canvas.height - 50; y += 65) {
    sweep.push({ x: Math.round(x), y: Math.round(y) });
  }
}
sweep.sort((left, right) =>
  Math.hypot(left.x - centre.x, left.y - centre.y) - Math.hypot(right.x - centre.x, right.y - centre.y));

// The screen points that have actually hit something, so a later case can
// point at the same object again instead of searching the canvas for it.
const hits = [];
function remember(point, elementId) {
  if (!hits.some((row) => row.x === point.x && row.y === point.y)) hits.push({ ...point, elementId });
}

/** One click at a point known to hit, and the answer it settles on. */
async function pickAt(point, wait = 6000) {
  await page.mouse.click(point.x, point.y);
  const deadline = Date.now() + wait;
  while (Date.now() < deadline) {
    const state = await snapshot();
    if (state?.picked) return state;
    await delay(60);
  }
  throw new Error(`the click at ${point.x},${point.y} resolved to nothing`);
}

async function pickAnything() {
  for (const known of hits) {
    try {
      const state = await pickAt(known, 3000);
      return state;
    } catch { /* the picture has changed; fall back to looking */ }
  }
  for (const point of sweep) {
    await page.mouse.click(point.x, point.y);
    const deadline = Date.now() + 900;
    while (Date.now() < deadline) {
      const state = await snapshot();
      if (state?.picked) { remember(point, state.picked); return state; }
      await delay(60);
    }
  }
  throw new Error("no click on this picture resolved to anything");
}

async function pickOn(elementId, options = {}) {
  const seen = new Set();
  for (const point of sweep) {
    await page.mouse.click(point.x, point.y);
    const deadline = Date.now() + 1200;
    while (Date.now() < deadline) {
      const state = await snapshot();
      if (state?.picked) {
        seen.add(`${state.picked}:${state.pickedStatus}`);
        remember(point, state.picked);
        if (state.picked === elementId &&
            (options.expect === false || state.selection === elementId)) return state;
        break;
      }
      await delay(60);
    }
  }
  throw new Error(`no click resolved to ${elementId}; the clicks that hit anything found ${[...seen].join(", ") || "nothing"}`);
}

// ---- 1. the embed opens on a candidate, and a click there is a selection
console.log("1 · the embed opens on a candidate");
//
// This is the case the Delete key was useless in: the Hub opens the tool page
// on one candidate, the object lights up under the ray, and nothing became a
// selection. It is also where a delete the record cannot allow has to say why.

const embedPick = await pickOn("portico-base");
assert.equal(embedPick.selection, "portico-base",
  "a resolved pick on the embedded candidate has to be a selection, not only a highlight");
assert.equal(embedPick.deletable, "portico-base");
assert.equal(embedPick.canDelete, true);
const runsBeforeRefusal = await runIds();
await page.keyboard.press("Delete");
const refused = await settled(
  (state) => state.entries.some((line) => line.startsWith("refusal:ELEMENT_HAS_DEPENDENTS")),
  "deleting something that is stood on was not refused in the page",
);
const refusal = refused.entries.find((line) => line.startsWith("refusal:ELEMENT_HAS_DEPENDENTS"));
assert.ok(refusal.includes("portico-cornice"),
  `the refusal has to name what stands on it: ${refusal}`);
assert.deepEqual(await runIds(), runsBeforeRefusal,
  "a delete the record refuses must leave no run behind");
assert.equal(refused.editingRunId, seedRun, "and it must not move the editing base");
await page.keyboard.press("Escape");
await settled((state) => state.selection === null, "Esc after the refusal");

// ---- 2. a rectangle drawn with a typed height comes out at that height
console.log("2 · a rectangle drawn with a typed height");
/**
 * One rectangle, drawn the way a person draws it: arm the tool, put the first
 * corner on the work plane, type the side, type the height. The typed numbers
 * are what the exported solid has to measure.
 */
async function drawRectangle(side, height, from, whileArmed) {
  const before = await snapshot();
  await page.getByRole("button", { name: "Rectangle" }).click();
  await page.mouse.move(from.x, from.y);
  await page.mouse.click(from.x, from.y);
  const entry = page.locator(".sketch-entry input");
  await entry.waitFor({ state: "visible", timeout: 15000 });
  await entry.fill(String(side));
  await entry.press("Enter");
  await entry.fill(String(height));
  await entry.press("Enter");
  const done = await settled(
    (state) => state.editingRunId !== null && state.editingRunId !== before.editingRunId &&
      !state.loading && state.status === "ready",
    "the drawn rectangle never became the run being edited",
  );
  if (whileArmed) await whileArmed({ before: before.editingRunId, after: done.editingRunId });
  // The tool stays armed after an action, which is what lets a second
  // rectangle follow the first. Put it away, so the next click is a pick.
  await page.getByRole("button", { name: "Rectangle" }).click();
  await page.waitForSelector(".sketch-entry", { state: "detached", timeout: 10000 });
  const added = new Set((await call("GET", `/api/state?run=${before.editingRunId}`)).elements.map((row) => row.elementId));
  const state = await call("GET", `/api/state?run=${done.editingRunId}`);
  const element = state.elements.map((row) => row.elementId).find((id) => !added.has(id));
  assert.ok(element, "the drawing added no element to the record");
  const model = await exported(done.editingRunId);
  const box = model.get(`obj-${element}`);
  assert.ok(box, `the drawn element is not in the export: ${[...model.keys()].join(", ")}`);
  assert.equal(box.z, height,
    `the typed height must be the exported height, on the Z-up axis; got ${JSON.stringify(box)}`);
  assert.equal(box.x, side, `the typed side must be the exported side; got ${JSON.stringify(box)}`);
  assert.equal(box.y, side, `the typed side must be the exported side; got ${JSON.stringify(box)}`);
  return { runId: done.editingRunId, element, model };
}

const firstDrawing = await drawRectangle(3, 2.4, { x: centre.x - 120, y: centre.y + 60 }, async (runs) => {
  // An armed tool with nothing in progress owns no keys: the rectangle is
  // committed, so Ctrl+Z here is the model's and takes back the volume just
  // made. Keeping the tool armed is how a second rectangle follows the first,
  // and it must not cost the undo of the first.
  await page.keyboard.press("Control+z");
  await settled((state) => state.editingRunId === runs.before && !state.busy,
    "Ctrl+Z with the tool still armed did not take back the volume just drawn");
  await page.keyboard.press("Control+y");
  await settled((state) => state.editingRunId === runs.after && !state.busy,
    "Ctrl+Y did not put the drawn volume back");
});
const drawnRun = firstDrawing.runId;
const drawnElement = firstDrawing.element;
const afterDraw = firstDrawing.model;

// ---- 3. pick that object with a real click, then delete only it
console.log("3 · pick, then delete only that");

const picked = await pickOn(drawnElement);
assert.equal(picked.deletable, drawnElement, "Delete would remove what was picked");
assert.equal(picked.canDelete, true, "a resolved pick on the edited run is deletable");

const runsBeforeDelete = await runIds();
await page.keyboard.press("Delete");
const deletedState = await settled(
  (state) => state.editingRunId !== null && state.editingRunId !== drawnRun && !state.loading && state.status === "ready",
  "Delete never produced a candidate that became the edited run",
);
const deletedRun = deletedState.editingRunId;
const afterDelete = await exported(deletedRun);
assert.ok(!afterDelete.has(`obj-${drawnElement}`), "the picked object survived the delete");
assert.deepEqual(
  [...afterDraw.keys()].filter((name) => !afterDelete.has(name)),
  [`obj-${drawnElement}`],
  "the delete took exactly one object",
);
for (const kept of ["obj-portico-base", "obj-portico-cornice", "obj-seed-block"]) {
  assert.ok(afterDelete.has(kept), `${kept} was taken by a delete nobody asked for`);
}

// ---- 4. Ctrl+Z goes back to the model before the delete; Ctrl+Y forward
console.log("4 · Ctrl+Z and Ctrl+Y");

await page.keyboard.press("Control+z");
const undone = await settled(
  (state) => state.editingRunId === drawnRun && state.loadedRunId === drawnRun && !state.loading,
  "Ctrl+Z did not return to the run before the delete",
);
assert.ok((await exported(drawnRun)).has(`obj-${drawnElement}`),
  "the run undo returned to must still have the object in its saved model");
assert.equal(undone.picked, null, "what was picked belonged to the picture that went away");
assert.equal(undone.deletable, null, "and nothing is deletable until something on this picture is picked");
assert.equal(undone.canDelete, false, "Delete acts on a pick, never on a carried-over selection");
await settled((state) => state.canRedo, "after an undo there is something to redo");

await page.keyboard.press("Control+y");
const redone = await settled(
  (state) => state.editingRunId === deletedRun && state.loadedRunId === deletedRun && !state.loading,
  "Ctrl+Y did not go forward to the delete",
);
assert.ok(!(await exported(redone.editingRunId)).has(`obj-${drawnElement}`),
  "redo must land on the run whose model has the object removed");

// Ctrl+Shift+Z is the other spelling of redo, so undo first and come forward
// on it rather than on Ctrl+Y.
await page.keyboard.press("Control+z");
await settled((state) => state.editingRunId === drawnRun && !state.busy && state.canRedo,
  "Ctrl+Z before checking redo's other spelling");
await page.keyboard.press("Control+Shift+z");
await settled((state) => state.editingRunId === deletedRun && !state.busy,
  "Ctrl+Shift+Z is not redo's other spelling");

// ---- 5. an edit after an undo drops the redo branch and keeps every run
console.log("5 · an edit after an undo");

await page.keyboard.press("Control+z");
await settled((state) => state.editingRunId === drawnRun && !state.busy && state.canRedo,
  "back on the drawn run with a redo available");
// The drawn object is here again, because this is the run before the delete.
// Removing it from *here* is a second, independent edit made after an undo.
const branchPick = await pickOn(drawnElement);
assert.equal(branchPick.deletable, drawnElement);
await page.keyboard.press("Delete");
const branched = await settled(
  (state) => state.editingRunId !== null && ![drawnRun, deletedRun].includes(state.editingRunId) &&
    !state.loading && state.status === "ready",
  "the edit made after an undo never produced its own run",
);
assert.equal(branched.canRedo, false, "an edit after an undo leaves nothing to redo forward into");
assert.ok(!branched.history.includes(deletedRun), "the abandoned branch is not navigable any more");
const runsNow = await runIds();
for (const runId of [...runsBeforeDelete, deletedRun]) {
  assert.ok(runsNow.includes(runId), `${runId} was removed from the project; undo must delete nothing`);
}
assert.ok((await exported(deletedRun)).size > 0, "the abandoned run's model is still readable");
const branchedModel = await exported(branched.editingRunId);
assert.ok(!branchedModel.has(`obj-${drawnElement}`), "the second delete did happen");
assert.ok(branchedModel.has("obj-seed-block"),
  "and it branched from the run the undo returned to, which still had everything else");

// ---- 6. keys inside text belong to the text
console.log("6 · keys inside text");

const runsBeforeTyping = await runIds();
// The drawing entry is a real text field on this stage, so it is the one to
// hold to: while a number is being typed into it, Delete, Backspace and
// Ctrl+Z edit that text and reach no model.
await page.getByRole("button", { name: "Rectangle" }).click();
await page.mouse.click(centre.x - 150, centre.y + 90);
const typed = page.locator(".sketch-entry input");
await typed.waitFor({ state: "visible", timeout: 15000 });
await typed.fill("12");
await typed.press("Delete");
await typed.press("Backspace");
await typed.press("Control+z");
await delay(800);
assert.deepEqual(await runIds(), runsBeforeTyping, "a keystroke in a text field changed the model");
const whileTyping = await snapshot();
assert.equal(whileTyping.editingRunId, branched.editingRunId, "typing moved the editing base");
// Esc belongs to the drawing while a drawing is running.
await page.keyboard.press("Escape");
await page.getByRole("button", { name: "Rectangle" }).click();
await page.waitForSelector(".sketch-entry", { state: "detached", timeout: 10000 });
assert.deepEqual(await runIds(), runsBeforeTyping, "cancelling a drawing changed the model");

// ---- 7. Esc clears the selection and changes nothing
console.log("7 · Esc");
//
// The object drawn at the start has been deleted on this branch, so this draws
// another one — which is also a drawing made after an undo, continuing the
// same history rather than starting a second one.

const secondDrawing = await drawRectangle(3, 1.8, { x: centre.x - 200, y: centre.y + 110 });
assert.ok(!modelHistoryOf(await snapshot()).includes(deletedRun),
  "the abandoned branch stays out of the way after a second drawing");
const beforeEscape = await runIds();
const picked3 = await pickOn(secondDrawing.element);
assert.equal(picked3.selection, secondDrawing.element);
await page.keyboard.press("Escape");
const cleared = await settled((state) => state.selection === null && state.picked === null,
  "Esc did not clear the selection and the pick");
assert.equal(cleared.canDelete, false, "with nothing picked there is nothing Delete may remove");
assert.deepEqual(await runIds(), beforeEscape, "Esc changed the model");

// ---- 8. a held key is one deletion
console.log("8 · a held key");

const pickedAgain = await pickOn(secondDrawing.element);
assert.equal(pickedAgain.deletable, secondDrawing.element);
const beforeRepeat = await runIds();
await page.keyboard.down("Delete");
await delay(600);
await page.keyboard.up("Delete");
const afterRepeat = await settled((state) => !state.loading && state.status === "ready" &&
  state.editingRunId !== pickedAgain.editingRunId, "the held Delete never produced its one run");
const runsAfterRepeat = await runIds();
assert.equal(runsAfterRepeat.length, beforeRepeat.length + 1,
  `a held Delete must submit once; runs went from ${beforeRepeat.length} to ${runsAfterRepeat.length}`);
assert.ok(!(await exported(afterRepeat.editingRunId)).has(`obj-${secondDrawing.element}`),
  "the one deletion happened");

// ---- 9. one Ctrl+Z reaches one owner: the ink's or the model's, never both

console.log("9 · one Ctrl+Z, one owner");
const inkRun = (await snapshot()).editingRunId;
// Pick the model object *before* the ink is armed: once the annotation layer
// is over the canvas it takes the pointer, and the picked element this case
// needs is the one that was resolved while the model was the mode in hand.
const pickedUnderInk = await pickAnything();
assert.ok(pickedUnderInk.picked, "something on the model is picked before the ink is armed");
assert.equal(pickedUnderInk.canDelete, true, "and it is deletable while the model is the mode in hand");
const annotate = page.locator('button[aria-controls="annotation-tools"]');
if ((await annotate.getAttribute("aria-expanded")) !== "true") await annotate.click();
await page.locator("#annotation-tools").getByRole("button", { name: "\u2571 Line", exact: true }).click();
const inkLayer = page.locator('canvas.annotate[data-armed="true"]');
const inkBox = await inkLayer.boundingBox();
assert.ok(inkBox, "the annotation layer is not armed");
await page.mouse.move(inkBox.x + inkBox.width * 0.4, inkBox.y + inkBox.height * 0.55);
await page.mouse.down();
await page.mouse.move(inkBox.x + inkBox.width * 0.6, inkBox.y + inkBox.height * 0.55, { steps: 8 });
await page.mouse.up();
const inked = await settled((state) => state.ink > 0, "the stroke was not recorded");
const runsBeforeInkUndo = await runIds();

await page.keyboard.press("Control+z");
const afterInkUndo = await settled((state) => state.ink === inked.ink - 1,
  "Ctrl+Z did not reach the ink that was being drawn");
assert.equal(afterInkUndo.editingRunId, inkRun,
  "the same Ctrl+Z must not also step the model: one press, one owner");
assert.deepEqual(await runIds(), runsBeforeInkUndo, "an ink undo touched the project");

// With the tool still armed and nothing left to take back, the ink is still
// the owner: the model is not stepped behind its back.
await page.keyboard.press("Control+z");
await delay(500);
const stillInk = await snapshot();
assert.equal(stillInk.editingRunId, inkRun, "an armed annotation tool must keep Ctrl+Z");
assert.equal(stillInk.ink, 0);

// With the ink in hand, Delete is not the model's either — the element picked
// a moment ago is still picked, and still must not be removed by this key.
assert.equal((await snapshot()).picked, pickedUnderInk.picked, "the pick survived arming the ink");
const runsUnderInk = await runIds();
const callsUnderInk = sent.length;
await page.keyboard.press("Delete");
await delay(700);
assert.equal(sent.slice(callsUnderInk).filter((row) => row.path === "/api/proposals/delete").length, 0,
  "Delete with an annotation tool armed sent a model delete");
assert.deepEqual(await runIds(), runsUnderInk, "and it changed the model");

// Put the ink away, and the model has the key again.
await page.locator("#annotation-tools").getByRole("button", { name: "\u2571 Line", exact: true }).click();
await page.keyboard.press("Control+z");
const backToModel = await settled((state) => state.editingRunId !== inkRun && !state.busy,
  "with no annotation owner, Ctrl+Z is the model's again");
await page.keyboard.press("Control+y");
await settled((state) => state.editingRunId === inkRun && !state.busy, "and redo returns");
assert.notEqual(backToModel.editingRunId, inkRun);

// A mark left on the picture from an earlier round does not own Ctrl+Z for
// good: with the tool away, a rectangle drawn now is what the next Ctrl+Z
// takes back, even though the ink still has something it could undo.
await page.locator(".viewtools").getByRole("button", { name: "\u2571 Line", exact: true }).click()
  .catch(async () => {
    if ((await annotate.getAttribute("aria-expanded")) !== "true") await annotate.click();
    await page.locator("#annotation-tools").getByRole("button", { name: "\u2571 Line", exact: true }).click();
  });
const secondInkBox = await page.locator('canvas.annotate[data-armed="true"]').boundingBox();
assert.ok(secondInkBox, "the annotation layer is armed again");
await page.mouse.move(secondInkBox.x + secondInkBox.width * 0.35, secondInkBox.y + secondInkBox.height * 0.65);
await page.mouse.down();
await page.mouse.move(secondInkBox.x + secondInkBox.width * 0.55, secondInkBox.y + secondInkBox.height * 0.65, { steps: 8 });
await page.mouse.up();
const withOldInk = await settled((state) => state.ink > 0, "the older mark was not recorded");
await page.locator("#annotation-tools").getByRole("button", { name: "\u2571 Line", exact: true }).click();
await page.waitForSelector('canvas.annotate[data-armed="true"]', { state: "detached", timeout: 10000 });

const beforeMixedDrawing = (await snapshot()).editingRunId;
const mixedDrawing = await drawRectangle(2, 1.2, { x: centre.x + 140, y: centre.y + 120 }, async (runs) => {
  // Armed tool, nothing in progress, and ink that could be undone: the model
  // owns this Ctrl+Z, and the mark drawn earlier stays where it is.
  await page.keyboard.press("Control+z");
  const stepped = await settled((state) => state.editingRunId === runs.before && !state.busy,
    "an older annotation swallowed the Ctrl+Z that belonged to the volume just drawn");
  assert.equal(stepped.ink, withOldInk.ink, "and the mark from the earlier round is untouched");
  await page.keyboard.press("Control+y");
  await settled((state) => state.editingRunId === runs.after && !state.busy, "redo puts the volume back");
});
assert.notEqual(mixedDrawing.runId, beforeMixedDrawing);

// ---- 10. the drawings workspace owns its own keys, and calls no model action
console.log("10 · the drawings workspace");

const beforeDocuments = await runIds();
// Something on the model is picked, which is the state this check needs: a
// model selection the drawings workspace must not act on. The switcher itself
// belongs to the host rail in an embedded page, so the workspace is opened the
// way that rail opens it.
await pickAnything();
const documentRun = (await snapshot()).editingRunId;
await page.evaluate(() => window.__shortcuts.openDocuments(true));
await settled((state) => state.documentOpen === true, "the drawings workspace never opened");
await delay(400);
const callsBeforeDocumentKeys = sent.length;
await page.keyboard.press("Control+z");
await page.keyboard.press("Control+y");
await page.keyboard.press("Delete");
await delay(700);
assert.deepEqual(await runIds(), beforeDocuments, "a key pressed in the drawings workspace changed the model");
assert.equal((await snapshot()).editingRunId, documentRun, "and it did not step the model's history");
assert.equal(sent.length, callsBeforeDocumentKeys,
  `the drawings workspace made ${sent.length - callsBeforeDocumentKeys} model calls: ${
    JSON.stringify(sent.slice(callsBeforeDocumentKeys))}`);
await page.evaluate(() => window.__shortcuts.openDocuments(false));
await settled((state) => state.documentOpen === false && state.status === "ready",
  "back on the model workspace");

// ---- 11. a run being looked at, which is not the run being edited
console.log("11 · a run being looked at");

const editingRun = (await snapshot()).editingRunId;
const otherRun = drawnRun === editingRun ? seedRun : drawnRun;
const artifacts = (await call("GET", "/api/artifacts")).artifacts
  .filter((row) => row.runId === otherRun && row.available && row.fileName.endsWith(".3dm"));
assert.ok(artifacts.length > 0, `no export of ${otherRun} to look at`);
await page.evaluate((artifact) => window.__shortcuts.viewArtifact(artifact), artifacts[0]);
const viewing = await settled(
  (state) => state.loadedRunId === otherRun && state.editingRunId === editingRun && !state.loading,
  "the viewer never showed a run other than the one being edited",
);
assert.notEqual(viewing.loadedRunId, viewing.editingRunId, "this is the case that was broken");
assert.equal(viewing.viewedSource.runId, otherRun);

// Pointing at it works, without being asked to continue from it first.
const lookedAt = await pickOn("seed-block", { expect: false });
assert.equal(lookedAt.picked, "seed-block", "a pick on the picture on screen has to resolve");
assert.equal(lookedAt.deletable, "seed-block");
assert.equal(lookedAt.canDelete, true, "looking at a run is enough to point at what is in it");

// And a change made by pointing at it continues from *it*.
const callsBeforeViewedDelete = sent.length;
await page.keyboard.press("Delete");
const fromViewed = await settled(
  (state) => state.editingRunId !== editingRun && state.editingRunId !== otherRun && !state.busy,
  "the delete made on the viewed run never became its own run",
);
const deleteCall = sent.slice(callsBeforeViewedDelete).find((row) => row.path === "/api/proposals/delete");
assert.ok(deleteCall, "no delete was sent");
assert.equal(deleteCall.body.sourceRunId, otherRun,
  "the delete continues from the run that was on screen, not from the editing base");
assert.equal(deleteCall.body.stateDigest, viewing.viewedSource.stateDigest,
  "and against that run's own state");
assert.equal(deleteCall.body.elementId, "seed-block");
assert.ok(!(await exported(fromViewed.editingRunId)).has("obj-seed-block"), "the object is gone from the new run");
assert.ok((await exported(otherRun)).has("obj-seed-block"), "and still there in the run it was made from");

// Undo returns to the candidate that was being looked at, not to the old base.
await page.keyboard.press("Control+z");
const undoneToViewed = await settled(
  (state) => state.editingRunId === otherRun && state.loadedRunId === otherRun && !state.busy,
  "undo did not return to the run the delete was made from",
);
assert.equal(undoneToViewed.picked, null, "and the pick belonged to the picture that went away");
await page.keyboard.press("Control+y");
await settled((state) => state.editingRunId === fromViewed.editingRunId && !state.busy,
  "redo goes forward to the delete again");

// ---- 12. between clicking B and B's answer, Delete removes nothing

console.log("12 · a click on B leaves nothing of A to delete");
const beforeDelayed = await runIds();
// Two points that hit two different objects *on the picture as it stands*.
// What each remembered point used to hit is no guide: runs have come and gone
// since, so the pair is settled by pointing at them now.
assert.ok(hits.length >= 2, `two known hits are needed; known: ${
  JSON.stringify(hits.map((row) => row.elementId))}`);
const first = await pickAt(hits[0]);
let pointA = hits[0];
let pointB = null;
for (const known of hits.slice(1)) {
  const state = await pickAt(known, 3000).catch(() => null);
  if (state && state.picked !== first.picked) { pointB = known; break; }
}
assert.ok(pointB, `no second known point hits another object: ${
  JSON.stringify(hits.map((row) => row.elementId))}`);
// A is picked again, so there is a real resolved answer in hand when B is
// clicked — which is the whole point of this case.
const resolvedA = await pickAt(pointA);
assert.ok(resolvedA.picked, "A has to be resolved before B is clicked");
const callsBeforeDelayed = sent.length;
await page.route((url) => url.pathname === "/api/pick/resolve", async (route) => {
  await delay(2500);
  await route.continue();
});
await page.mouse.click(pointB.x, pointB.y);
await delay(200);
const waiting = await snapshot();
assert.equal(waiting.picked, null, "the old pick must be over the moment a new click is made");
assert.equal(waiting.canDelete, false, "and nothing is deletable while the answer is on its way");
await page.keyboard.press("Delete");
await delay(600);
assert.equal(sent.slice(callsBeforeDelayed).filter((row) => row.path === "/api/proposals/delete").length, 0,
  "a Delete pressed while a pick was unanswered sent a delete anyway");
assert.deepEqual(await runIds(), beforeDelayed, "and it must have changed nothing");
await page.unroute((url) => url.pathname === "/api/pick/resolve");
// When B's answer does arrive, it is B that is picked — not the A it replaced.
const resolvedB = await settled((state) => state.picked !== null, "B's delayed answer never landed");
assert.notEqual(resolvedB.picked, resolvedA.picked,
  "the delayed click was meant to land on a different object than the one before it");

// ---- done

closing = true;
await browser.close().catch(() => {});
await vite.close().catch(() => {});
await new Promise((resolve) => http.close(resolve));
api.kill();
await delay(300);
await rm(root, { recursive: true, force: true }).catch(() => {});
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("model shortcuts: drawing height, delete, undo, redo, branch, text keys, Esc and key repeat all verified against real geometry");
