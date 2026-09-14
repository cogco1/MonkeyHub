/**
 * Asking for an editable 3dm from the shipped controls, in a browser.
 *
 * The page mounts the real ``Stage`` the way the Hub embeds it and the real
 * ``VersionsStrip``, both driven by the real export hook and the real client;
 * only the HTTP answers are in memory. Nothing here re-implements a control.
 *
 * What it holds to:
 *   - the control the embedded stage renders is reachable and bound to the
 *     model on screen: the request names that run and that delivery's exact
 *     STEP, never the editing base, the newest run, or one seat of a picture
 *     showing several;
 *   - one Rhino: a second click while an export runs is not sent, and the
 *     control stays unavailable when the architect looks at another model;
 *   - a refusal shows and can be tried again; a success offers the artifact
 *     that request answered with, through the ordinary bytes route;
 *   - the versions panel offers the editable copy only when it succeeded, and
 *     never as a model to view.
 *
 * No user service, project, browser profile or accepted design is touched.
 */

import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const sha = (text) => text.padEnd(64, "0").slice(0, 64);
const base = {
  artifactId: "x", runId: "run-a", stageId: "seat-portico", fileName: "seat.3dm", relativePath: null,
  sha256: sha("a"), sizeBytes: 10, objectCount: 2, status: "succeeded", readbackVerified: true,
  available: true, unavailableReason: null, base: { version: 0, stateSha256: sha("base") },
  branchId: "runner-v1", branchEpoch: 1, programRef: "project://demo/program.json",
  programDigest: sha("p"), designStateDigest: sha("d"), lengthUnit: "meter", upAxis: "Z-up",
  receiptRef: "project://demo/runs/run-a/records/seat-occt-execution-1.json",
  format: "3dm", representation: "preview", sourceStepSha256: null,
};
const preview = { ...base, artifactId: sha("a"), sha256: sha("a"), fileName: "seat.preview.3dm" };
const step = { ...base, artifactId: sha("b"), sha256: sha("b"), fileName: "seat.step", format: "step", representation: "exact" };
const secondSeat = {
  ...base, artifactId: sha("7"), sha256: sha("7"), fileName: "seat-two.preview.3dm", stageId: "seat-porch",
  receiptRef: "project://demo/runs/run-a/records/seat-occt-execution-3.json",
};
const otherPreview = {
  ...base, artifactId: sha("c"), sha256: sha("c"), runId: "run-b", fileName: "other.preview.3dm",
  receiptRef: "project://demo/runs/run-b/records/seat-occt-execution-2.json",
};
const otherStep = {
  ...base, artifactId: sha("e"), sha256: sha("e"), runId: "run-b", fileName: "other.step",
  format: "step", representation: "exact",
  receiptRef: "project://demo/runs/run-b/records/seat-occt-execution-2.json",
};
const work = {
  ...base, artifactId: sha("f"), sha256: sha("f"), fileName: "seat.work.3dm",
  format: "3dm", representation: "exact", sourceStepSha256: step.sha256,
  receiptRef: "project://demo/runs/run-a/records/seat-rhino-execution-1.json",
};
const failedWork = {
  ...work, artifactId: sha("9"), sha256: sha("9"), fileName: "seat-failed.work.3dm",
  status: "failed", readbackVerified: false,
};

const html = `<!doctype html><html><head><style>
#root, .stage { height: 100vh; }
#root .viewport-state { display: none; }
</style></head><body><div id="root"></div><script type="module">
import React, { createRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Stage } from "/src/features/stage/Stage.tsx";
import { VersionsStrip } from "/src/features/stage/VersionsStrip.tsx";
import { UserPreferencesProvider } from "/src/features/settings/preferences.tsx";
import { studio } from "/src/api/client.ts";
import { useWorkModelExport } from "/src/features/artifacts/useWorkModelExport.ts";
import "/src/styles.css";

const state = window.__workModel = { reloads: 0, opened: [] };
const noop = () => {};
function Harness() {
  const [rows, setRows] = useState(window.__rows);
  const [viewed, setViewed] = useState(window.__viewed);
  const controls = useWorkModelExport({
    rows, viewed, exportWorkModel: studio.exportWorkModel,
    onExported: () => { state.reloads += 1; setRows(window.__rows); },
  });
  state.controls = {
    source: controls.source?.sha256 ?? null, refusal: controls.refusal,
    busy: controls.busy, error: controls.error, exported: controls.exported?.sha256 ?? null,
  };
  state.show = (next) => setViewed(next);
  state.reread = () => setRows([...window.__rows]);
  const groups = [{
    runId: "run-a", label: "Candidate", title: "raise the portico", detail: null,
    exports: rows.filter((row) => row.runId === "run-a")
      .map((artifact) => ({ artifact, seat: artifact.stageId, sourceLabel: "fixture" })),
  }];
  return React.createElement(UserPreferencesProvider, null,
    React.createElement(Stage, {
      viewportRef: createRef(), embedded: true, status: "ready", message: "", picked: null,
      versions: [], workingCopies: [], loadedShas: viewed.shas, loadingSha: null, designHistory: null,
      documentView: { open: false, mounted: false }, displayMode: "model", tool: null,
      gestures: [], home: null, hasModel: true, editingBaseRunId: null, loadedRunId: viewed.runId,
      modelAnnotations: null, annotationsReady: false, documentModelSources: [],
      editingModelSource: null, viewedModelSource: null, artifactError: null, baseError: null,
      blend: null, captureState: "idle", onTool: noop, onInspection: noop, onStatus: noop,
      onRequestFile: noop, onOpenFile: noop, onSource: noop, onPick: noop,
      workModel: controls,
    }),
    React.createElement("div", { id: "panel" },
      React.createElement(VersionsStrip, {
        groups, loadingSha: null, loadedShas: viewed.shas, loadedRunId: viewed.runId,
        onOpen: (artifact) => state.opened.push(artifact.fileName), onOpenRun: noop, onCompare: noop,
      })),
  );
}
createRoot(document.getElementById("root")).render(React.createElement(Harness));
</script></body></html>`;

test("the shipped export control and versions panel answer for the model on screen", async (t) => {
  const webRoot = fileURLToPath(new URL("../", import.meta.url));
  const cacheDir = await mkdtemp(join(tmpdir(), "monkeyarch-work-model-test-"));
  const http = createHttpServer();
  const vite = await createServer({
    root: webRoot, configFile: false, cacheDir, publicDir: ".generated/public", logLevel: "silent",
    plugins: [react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null },
  });
  let browser;
  t.after(async () => {
    await browser?.close();
    if (http.listening) await new Promise((done, fail) => { http.close((error) => error ? fail(error) : done()); });
    await vite.close();
    assert.equal(dirname(resolve(cacheDir)), resolve(tmpdir()));
    assert.ok(basename(cacheDir).startsWith("monkeyarch-work-model-test-"));
    await rm(cacheDir, { recursive: true, force: true });
  });
  http.on("request", (request, response) => {
    if (request.url === "/work-model-test") {
      void vite.transformIndexHtml(request.url, html).then((page) => {
        response.setHeader("Content-Type", "text/html");
        response.end(page);
      });
    } else vite.middlewares(request, response, () => { response.statusCode = 404; response.end(); });
  });
  await new Promise((done) => { http.listen(0, "127.0.0.1", done); });
  const address = http.address();
  assert.ok(address !== null && typeof address !== "string");
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  const page = await (await browser.newContext()).newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));

  const posts = [];
  let answer = { status: 201, body: work };
  let held = null;
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname.endsWith("/rhino-export")) {
      posts.push({ sha256: url.pathname.split("/")[3], body: JSON.parse(request.postData() ?? "{}") });
      if (held) await held;
      await route.fulfill({ status: answer.status, contentType: "application/json", body: JSON.stringify(answer.body) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.addInitScript(([rows, viewed]) => { window.__rows = rows; window.__viewed = viewed; },
    [[preview, step, secondSeat, otherPreview, otherStep, failedWork], { runId: "run-a", shas: [preview.sha256] }]);
  await page.goto(`http://127.0.0.1:${address.port}/work-model-test`);
  await page.locator("#panel").waitFor();

  const controls = () => page.evaluate(() => window.__workModel.controls);
  const button = page.locator("[data-work-model-export]");
  const openViewTools = async () => {
    const toggle = page.locator('button[aria-controls="view-tools"]');
    if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  };

  await t.test("the embedded stage offers it in its own view tools, for the model being viewed", async () => {
    assert.deepEqual(errors, []);
    // Embedded: the desktop page's versions toggle is not rendered here, so
    // this control is not hiding behind it.
    assert.equal(await page.locator("button.stage__versions-toggle").count(), 0);
    await openViewTools();
    await button.waitFor({ state: "visible", timeout: 5_000 });
    assert.equal(await button.isEnabled(), true);
    assert.equal((await controls()).source, step.sha256);
  });

  await t.test("a view of several seats, or of nothing, disables it and says why", async () => {
    await page.evaluate((viewed) => window.__workModel.show(viewed), { runId: "run-a", shas: [] });
    await page.waitForFunction(() => window.__workModel.controls.refusal === "nothing-loaded");
    await openViewTools();
    assert.equal(await button.isDisabled(), true);

    await page.evaluate((viewed) => window.__workModel.show(viewed),
      { runId: "run-a", shas: [preview.sha256, secondSeat.sha256] });
    await page.waitForFunction(() => window.__workModel.controls.refusal === "several-seats");
    await openViewTools();
    assert.equal(await button.isDisabled(), true);
    assert.match(await button.getAttribute("title"), /several seats|多个席位/);

    await page.evaluate((viewed) => window.__workModel.show(viewed), { runId: "run-a", shas: [preview.sha256] });
    await page.waitForFunction((expected) => window.__workModel.controls.source === expected, step.sha256);
  });

  await t.test("a refusal shows the server's words on the stage and can be tried again", async () => {
    answer = { status: 409, body: { code: "RHINO_HOST_UNAVAILABLE", detail: "no Rhino here" } };
    await openViewTools();
    await button.click();
    await page.locator("[data-work-model-error]").waitFor({ timeout: 10_000 });
    assert.match(await page.locator("[data-work-model-error]").innerText(), /RHINO_HOST_UNAVAILABLE|no Rhino here/);
    assert.deepEqual(posts.at(-1), { sha256: step.sha256, body: { runId: "run-a" } });
    assert.equal(await page.locator(".viewtools [data-work-model-save]").count(), 0);
    assert.equal(await button.isEnabled(), true);
  });

  await t.test("one Rhino: a second click is not sent and another model shows it is busy", async () => {
    answer = { status: 201, body: work };
    const gate = (() => { let release; const promise = new Promise((done) => { release = done; }); return { promise, release }; })();
    held = gate.promise;
    const before = posts.length;
    await openViewTools();
    await button.click();
    await page.waitForFunction(() => window.__workModel.controls.busy === true);
    await button.click({ force: true });
    await page.evaluate((viewed) => window.__workModel.show(viewed), { runId: "run-b", shas: [otherPreview.sha256] });
    const elsewhere = await page.waitForFunction((expected) => {
      const current = window.__workModel.controls;
      return current.source === expected && current.busy ? current : false;
    }, otherStep.sha256).then((handle) => handle.jsonValue());
    assert.equal(elsewhere.refusal, "busy-elsewhere", JSON.stringify(elsewhere));
    await openViewTools();
    assert.equal(await button.isDisabled(), true);
    gate.release();
    held = null;
    await page.waitForFunction(() => window.__workModel.controls.busy === false);
    assert.equal(posts.length, before + 1, "exactly one request left the page");
  });

  await t.test("the stage offers this export's own file through the ordinary bytes route", async () => {
    await page.evaluate((viewed) => window.__workModel.show(viewed), { runId: "run-a", shas: [preview.sha256] });
    await page.waitForFunction((expected) => window.__workModel.controls.exported === expected, work.sha256);
    await openViewTools();
    const save = page.locator(".viewtools [data-work-model-save]");
    await save.waitFor({ timeout: 5_000 });
    assert.match(await save.getAttribute("href"), new RegExp(`/api/artifacts/${work.sha256}/bytes$`));
    assert.equal(await save.getAttribute("download"), work.fileName);
    assert.equal(await page.evaluate(() => window.__workModel.reloads), 1);
  });

  await t.test("the versions panel saves only a succeeded copy and never offers one to view", async () => {
    // The run now holds both: the copy this export made, and the file a
    // failed readback left behind.
    await page.evaluate(([rows]) => { window.__rows = rows; window.__workModel.reread(); },
      [[preview, step, secondSeat, otherPreview, otherStep, work, failedWork]]);
    const panel = page.locator("#panel");
    await panel.locator("[data-work-model-save]").first().waitFor({ timeout: 5_000 });

    const saves = await panel.locator("[data-work-model-save]").evaluateAll(
      (nodes) => nodes.map((node) => node.getAttribute("href")));
    assert.deepEqual(saves, [`/api/artifacts/${work.sha256}/bytes`], "only the succeeded copy is offered");

    // No editable copy is offered as something to open: the buttons that view
    // a run's models name the previews only.
    const viewButtons = await panel.locator("button.vcard__export").evaluateAll(
      (nodes) => nodes.map((node) => node.textContent));
    assert.equal(viewButtons.some((label) => label.includes("work.3dm")), false, viewButtons.join(" | "));
    assert.deepEqual(errors, []);
  });
});
