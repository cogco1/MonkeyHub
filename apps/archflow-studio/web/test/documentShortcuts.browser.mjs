import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const playwrightModule = process.env.PLAYWRIGHT_MODULE?.trim();
if (!playwrightModule) {
  throw new Error("Set PLAYWRIGHT_MODULE to the Playwright module file path.");
}

// The full App, an explicitly named disposable project, and a unique uploaded
// source. No housing project, existing source, model, or intent is writable.
for (const key of ["DOCUMENT_SHORTCUTS_APP_URL", "DOCUMENT_PROJECT_ID", "DOCUMENT_RUN_ID", "DOCUMENT_FIXTURE_PDF"]) {
  assert.ok(process.env[key], `${key} is required; this test has no live-project defaults`);
}
const appUrl = new URL(process.env.DOCUMENT_SHORTCUTS_APP_URL);
assert.ok(["127.0.0.1", "localhost"].includes(appUrl.hostname), "use an isolated local App");
assert.ok(!["5174", "5187", "8000"].includes(appUrl.port), "protected application ports cannot run this regression");
const projectId = process.env.DOCUMENT_PROJECT_ID;
const runId = process.env.DOCUMENT_RUN_ID;
assert.ok(!/housing|arch401/i.test(`${projectId} ${runId}`), "housing projects and runs are outside this regression");
const response = await fetch(new URL("/api/project", appUrl));
assert.equal(response.status, 200, "isolated App API is available");
assert.equal((await response.json()).projectId, projectId, "App binding must match the explicitly authorized disposable project");
const nonce = randomUUID();
const fileName = `document-shortcuts-${nonce}.pdf`;
const fileBytes = Buffer.concat([await readFile(process.env.DOCUMENT_FIXTURE_PDF), Buffer.from(`\n% document-shortcuts-${nonce}\n`)]);
const { chromium } = await import(pathToFileURL(playwrightModule).href);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1700, height: 1100 }, deviceScaleFactor: 1 });
const page = await context.newPage();
const errors = [];
const denied = [];
const writes = [];
const writesByRequest = new Map();
const passed = [];
let assetSha = null;
let currentCase = "open isolated source";
let expectHeldIntent = false;
let heldIntentRequests = 0;
let acceptHeldIntent;
let releaseHeldIntent;
const heldIntentSeen = new Promise((resolve) => { acceptHeldIntent = resolve; });
const heldIntentRelease = new Promise((resolve) => { releaseHeldIntent = resolve; });
page.on("pageerror", (error) => errors.push(String(error)));
page.on("response", (response) => {
  const write = writesByRequest.get(response.request());
  if (write) { write.status = response.status(); write.elapsedMs = Date.now() - write.startedAt; }
});
await context.route(/\/api\//, async (route) => {
  const request = route.request();
  if (["GET", "HEAD", "OPTIONS"].includes(request.method())) { await route.continue(); return; }
  const pathname = new URL(request.url()).pathname;
  const body = request.postDataJSON();
  if (expectHeldIntent && request.method() === "POST" && pathname === "/api/intents" && body.projectId === projectId &&
    body.documentAnnotations?.length === 1 && body.documentAnnotations[0].runId === runId &&
    body.documentAnnotations[0].assetSha256 === assetSha && body.documentAnnotations[0].pageIndex === 0) {
    // Hold the actual frontend request entirely inside the browser. It never
    // reaches the backend or creates a submitted opinion/candidate.
    heldIntentRequests++; acceptHeldIntent(body);
    await heldIntentRelease;
    await route.abort("blockedbyclient"); return;
  }
  const upload = request.method() === "POST" && pathname === "/api/documents" &&
    body.projectId === projectId && body.runId === runId && body.fileName === fileName;
  const annotation = request.method() === "PUT" && pathname === "/api/document-annotations" &&
    assetSha !== null && body.projectId === projectId && body.runId === runId && body.assetSha256 === assetSha && body.pageIndex === 0;
  if (!upload && !annotation) {
    denied.push({ currentCase, method: request.method(), pathname, projectId: body?.projectId, runId: body?.runId });
    await route.abort("blockedbyclient"); return;
  }
  const write = { method: request.method(), pathname, startedAt: Date.now(), status: null, elapsedMs: null };
  writes.push(write); writesByRequest.set(request, write);
  await route.continue();
});
const marks = () => page.locator(".document-workspace [data-saved-ink] [data-stroke-id]").evaluateAll((paths) => paths.map((path) => path.dataset.strokeId));
const tool = (name) => page.locator(".document-tools").getByRole("button", { name, exact: true });
const viewport = page.locator(".document-viewport");
async function until(read, expected, label) {
  const deadline = Date.now() + 12_000;
  let actual;
  do {
    actual = await read();
    if (JSON.stringify(actual) === JSON.stringify(expected)) return;
    await new Promise((resolve) => setTimeout(resolve, 40));
  } while (Date.now() < deadline);
  assert.deepEqual(actual, expected, label);
}
async function step(name, work) {
  currentCase = name;
  await work();
  assert.deepEqual(denied, [], "writes stay on the unique disposable source");
  passed.push(name); console.log(`PASS ${name}`);
}
async function pressUndo(focus, chord, expected) {
  await focus.focus();
  await page.evaluate(() => { window.__documentShortcutKeys = []; });
  await page.keyboard.press(chord);
  await until(marks, expected, `${chord} follows the same document history`);
  const key = await page.evaluate(() => window.__documentShortcutKeys.at(-1));
  assert.ok(key?.prevented, `${chord} must be consumed by the document shortcut handler`);
}
async function nativeTextUndo(locator, read) {
  await locator.focus();
  await page.keyboard.type(`native-${nonce.slice(0, 8)}`);
  const typed = await read();
  const inkBefore = await marks();
  await page.evaluate(() => { window.__documentShortcutKeys = []; });
  await page.keyboard.press("Control+z");
  assert.notEqual(await read(), typed, "the browser's own text undo remains available");
  assert.equal(await page.evaluate(() => window.__documentShortcutKeys.at(-1)?.prevented), false, "document shortcuts must not prevent native text undo");
  await page.keyboard.press("Control+Shift+z");
  assert.equal(await read(), typed, "the browser's own text redo restores the text");
  assert.equal(await page.evaluate(() => window.__documentShortcutKeys.at(-1)?.prevented), false);
  assert.deepEqual(await marks(), inkBefore, "text undo/redo never changes document ink");
}

try {
  appUrl.searchParams.set("lang", "en");
  appUrl.searchParams.set("view", "documents");
  appUrl.searchParams.set("documentRun", runId);
  appUrl.searchParams.set("documentPage", "0");
  await page.goto(appUrl.href);
  await page.locator('.document-workspace input[type="file"]').waitFor({ state: "attached" });
  const uploaded = page.waitForResponse((result) => new URL(result.url()).pathname === "/api/documents" && result.request().method() === "POST");
  await page.locator('.document-workspace input[type="file"]').setInputFiles({ name: fileName, mimeType: "application/pdf", buffer: fileBytes });
  const upload = await uploaded;
  assert.equal(upload.status(), 201);
  const source = await upload.json();
  assert.equal(source.projectId, projectId); assert.equal(source.runId, runId);
  assetSha = source.assetSha256;
  await page.waitForFunction((sha) => document.querySelector(".document-header > select")?.value === sha, assetSha);
  await page.locator('.document-viewport[data-ready="true"]').waitFor();
  await tool("Pen").click();
  await until(marks, [], "unique source starts with no annotations");
  // Retain the native event and read its final state after keyboard.press.
  // A queued microtask can run between native listeners, before React handles
  // bubbling, so it is not a reliable place to read preventDefault here.
  await page.evaluate(() => {
    window.__documentShortcutKeys = [];
    window.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && /^[zy]$/i.test(event.key)) window.__documentShortcutKeys.push({
        key: event.key, get prevented() { return event.defaultPrevented; }, composing: event.isComposing, keyCode: event.keyCode,
      });
    }, true);
  });
  await step("three actual strokes establish one document history", async () => {
    const bounds = await page.locator(".document-page").boundingBox();
    assert.ok(bounds);
    for (let index = 0; index < 3; index++) {
      const y = bounds.y + bounds.height * (0.25 + index * 0.18);
      await page.mouse.move(bounds.x + bounds.width * 0.22, y); await page.mouse.down();
      await page.mouse.move(bounds.x + bounds.width * 0.55, y + bounds.height * 0.04); await page.mouse.up();
      await page.waitForFunction((length) => document.querySelectorAll(".document-workspace [data-saved-ink] [data-stroke-id]").length === length, index + 1);
    }
  });
  const initial = await marks();
  for (const [name, focus] of [
    ["viewport", viewport],
    ["toolbar Pen button", tool("Pen")],
    ["document header Open button", page.locator(".document-header > button").first()],
  ]) {
    await step(`Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y work from ${name}`, async () => {
      await pressUndo(focus, "Control+z", initial.slice(0, 2));
      await pressUndo(focus, "Control+Shift+z", initial);
      await pressUndo(focus, "Control+z", initial.slice(0, 2));
      await pressUndo(focus, "Control+y", initial);
    });
  }
  await step("toolbar buttons and shortcuts share the same history", async () => {
    await tool("Undo").click(); await until(marks, initial.slice(0, 2), "button undo");
    await pressUndo(page.locator(".document-header > button").first(), "Control+y", initial);
    await pressUndo(tool("Pen"), "Control+z", initial.slice(0, 2));
    await tool("Redo").click(); await until(marks, initial, "button redo restores identical IDs");
  });
  await step("native input and contenteditable retain text undo/redo", async () => {
    // The current workspace has a textarea; temporary native controls exercise
    // input/contenteditable bubbling inside the same real workspace boundary.
    await page.locator(".document-notes").evaluate((notes) => {
      const input = document.createElement("input"); input.id = "shortcut-native-input"; input.type = "text";
      const editable = document.createElement("div"); editable.id = "shortcut-native-editable"; editable.contentEditable = "true";
      editable.setAttribute("role", "textbox"); editable.style.minHeight = "24px";
      notes.append(input, editable);
    });
    const input = page.locator("#shortcut-native-input");
    await nativeTextUndo(input, () => input.inputValue());
    const editable = page.locator("#shortcut-native-editable");
    await nativeTextUndo(editable, () => editable.innerText());
  });
  await step("the actual comment textarea retains browser text undo/redo", async () => {
    const textarea = page.locator("#document-comment");
    await nativeTextUndo(textarea, () => textarea.inputValue());
  });
  await step("native isComposing and legacy keyCode 229 cannot consume annotation history", async () => {
    for (const focus of [viewport, tool("Pen"), page.locator(".document-header > button").first()]) {
      for (const flags of [{ isComposing: true }, { keyCode: 229, which: 229 }]) {
        await focus.focus();
        const before = await marks();
        const result = await focus.evaluate((node, flags) => {
          const event = new KeyboardEvent("keydown", { key: "z", code: "KeyZ", ctrlKey: true, bubbles: true, cancelable: true, ...flags });
          node.dispatchEvent(event);
          return { prevented: event.defaultPrevented, isComposing: event.isComposing, keyCode: event.keyCode };
        }, flags);
        assert.equal(result.prevented, false, "composition events are left to the browser/IME");
        if (flags.isComposing) assert.equal(result.isComposing, true); else assert.equal(result.keyCode, 229);
        assert.deepEqual(await marks(), before);
      }
    }
  });
  await step("a mounted but hidden document does not handle shortcuts", async () => {
    await page.locator(".stage-mode-switch").getByRole("button", { name: "3D model", exact: true }).click();
    const before = await marks();
    assert.equal(await page.locator(".document-workspace").isVisible(), false);
    await page.keyboard.press("Control+z");
    assert.deepEqual(await marks(), before, "the active 3D focus cannot undo hidden document ink");
    for (const hidden of [viewport, page.locator(".document-tools").getByRole("button", { name: "Pen", exact: true, includeHidden: true }), page.locator(".document-header > button").first()]) {
      const prevented = await hidden.evaluate((node) => {
        const event = new KeyboardEvent("keydown", { key: "z", code: "KeyZ", ctrlKey: true, bubbles: true, cancelable: true });
        node.dispatchEvent(event); return event.defaultPrevented;
      });
      assert.equal(prevented, false, "even a dispatched event in an inert document stays unhandled");
      assert.deepEqual(await marks(), before);
    }
    await page.locator(".stage-mode-switch").getByRole("button", { name: "Drawings & images", exact: true }).click();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    assert.deepEqual(await marks(), initial, "the visible document keeps the original stroke IDs");
  });
  currentCase = "settle the keyboard history's serial annotation saves";
  await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor({ timeout: 90_000 });
  await page.locator(".document-notes").getByRole("button", { name: "Save page", exact: true }).click();
  await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor();
  const query = new URLSearchParams({ runId, assetSha256: assetSha, pageIndex: "0" });
  const saved = await context.request.get(new URL(`/api/document-annotations?${query}`, appUrl).href);
  assert.equal(saved.status(), 200);
  assert.deepEqual((await saved.json()).annotations.map((mark) => mark.id), initial, "the final actual API snapshot retains the same three strokes");
  await step("submission pending locks document history from viewport, toolbar and header", async () => {
    await page.locator("#document-comment").fill(`Shortcut pending-state check ${nonce}`);
    expectHeldIntent = true;
    await page.locator(".document-notes").getByRole("button", { name: "Submit page note", exact: true }).click();
    await page.locator(".document-notes").getByRole("button", { name: "Submitting…", exact: true }).waitFor();
    let intentTimer;
    const captured = await Promise.race([heldIntentSeen, new Promise((_, reject) => { intentTimer = setTimeout(() => reject(new Error("frontend did not reach the held intent request")), 12_000); })])
      .finally(() => clearTimeout(intentTimer));
    assert.ok(captured.documentAnnotations[0].revisionSha256, "the source page was saved before the held submit");
    await page.locator(".document-save-state").filter({ hasText: /^Saved$/ }).waitFor();
    const before = await marks();
    const putsBefore = writes.filter((write) => write.method === "PUT").length;
    // Pan remains enabled while editing tools are disabled during submission.
    for (const focus of [viewport, tool("Pan"), page.locator(".document-header > button").first()]) {
      for (const chord of ["Control+z", "Control+Shift+z", "Control+y"]) {
        await focus.focus();
        await page.keyboard.press(chord);
        assert.deepEqual(await marks(), before, `${chord} cannot alter pending submission ink`);
      }
    }
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(writes.filter((write) => write.method === "PUT").length, putsBefore, "waiting-state shortcuts cannot enqueue annotation writes or undo the submitted text");
    assert.deepEqual(await marks(), initial);
    assert.equal(heldIntentRequests, 1);
  });
  assert.deepEqual(errors, [], "no browser exceptions");
  assert.deepEqual(denied, [], "no out-of-scope write attempted");
  console.log(JSON.stringify({ passed: passed.length, projectId, runId, assetSha, annotationWrites: writes.filter((write) => write.method === "PUT").length, heldIntentRequests, forwardedIntents: 0 }));
} catch (error) {
  console.error(JSON.stringify({ currentCase, url: page.url(), source: assetSha, errors, denied,
    saveState: await page.locator(".document-save-state").textContent().catch(() => null),
    comment: await page.locator("#document-comment").inputValue().catch(() => null), writes,
    documentErrors: await page.locator(".document-error,.document-render-error").allTextContents().catch(() => []) }));
  throw error;
} finally { releaseHeldIntent(); await context.unrouteAll({ behavior: "wait" }); await context.close(); await browser.close(); }
