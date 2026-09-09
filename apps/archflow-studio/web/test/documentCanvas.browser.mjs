import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

// Functional regression against the isolated full App and its disposable project.
// Every ink edit targets a unique uploaded PDF; existing sources are never annotated.
const appUrl = process.env.DOCUMENT_APP_URL ?? "http://127.0.0.1:5187";
const apiUrl = process.env.DOCUMENT_API_URL ?? "http://127.0.0.1:60616";
const fixtureRoot = process.env.DOCUMENT_FIXTURES ?? "C:/Users/asus/AppData/Local/Temp/archflow-document-integration-20260908";
const screenshotPath = process.env.DOCUMENT_SCREENSHOT ?? "C:/Users/asus/.codex/workspaces/document-canvas-20260908/document-page-2.png";
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ?? "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
const nonce = randomUUID();
const runId = "run-001";
const boundProject = await (await fetch(`${appUrl}/api/project`)).json();
assert.equal(boundProject.projectId, process.env.DOCUMENT_PROJECT_ID ?? "demo-project",
  "The write regression requires its disposable project; this frontend is bound elsewhere.");
let assetSha;
let currentStep = "launch";
const passed = [];
const errors = [];
const requests = [];
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1800, height: 1200 }, deviceScaleFactor: 2 });
let page = await context.newPage();

function watch(target) {
  target.on("pageerror", (error) => errors.push(String(error)));
  target.on("request", (request) => {
    if (request.url().includes("/api/") && ["PUT", "POST"].includes(request.method())) {
      requests.push({ url: request.url(), method: request.method(), body: request.postDataJSON() });
    }
  });
}
watch(page);
async function step(name, action) {
  currentStep = name;
  await action();
  passed.push(name);
  console.log(`PASS ${name}`);
}
const near = (actual, expected, tolerance = 0.003) => assert.ok(Math.abs(actual - expected) <= tolerance,
  `expected ${actual} within ${tolerance} of ${expected}`);
async function until(read, accepts, label) {
  const end = Date.now() + 15_000;
  let last;
  do {
    last = await read();
    if (accepts(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 60));
  } while (Date.now() < end);
  assert.fail(`${label}: ${JSON.stringify(last)}`);
}
const tool = (name) => page.locator(".document-tools").getByRole("button", { name, exact: true });
const count = () => page.locator("[data-saved-ink] [data-stroke-id]").count();
const paths = () => page.locator("[data-saved-ink] [data-stroke-id]").evaluateAll((nodes) => nodes.map((node) => ({
  id: node.dataset.strokeId, d: node.getAttribute("d"), width: node.getAttribute("stroke-width"),
})));
async function ready(width, height) {
  if (width && height) await page.waitForFunction(([w, h]) => document.querySelector(".document-page__ink")?.getAttribute("viewBox") === `0 0 ${w} ${h}`, [width, height]);
  await page.locator('.document-viewport[data-ready="true"]').waitFor();
  await until(() => tool("画笔").isEnabled(), Boolean, "page annotation state loaded");
}
async function rect() {
  const box = await page.locator(".document-page").boundingBox();
  assert.ok(box, "visible document page has bounds");
  return box;
}
async function coords([x, y]) {
  const box = await rect();
  return [box.x + x * box.width, box.y + y * box.height];
}
async function draw(points, { button = "left", expectedCount = null } = {}) {
  assert.equal(await page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), assetSha,
    "the uploaded source must remain selected before drawing");
  const before = await count();
  const [x, y] = await coords(points[0]);
  await page.mouse.move(x, y); await page.mouse.down({ button });
  for (const point of points.slice(1)) {
    const [mx, my] = await coords(point); await page.mouse.move(mx, my);
  }
  await page.mouse.up({ button });
  if (expectedCount !== null) await until(count, (value) => value === expectedCount, "stroke count");
  else if (button === "left") await until(count, (value) => value === before + 1, "new stroke committed");
}
async function snapshot(pageIndex, revisionSha256) {
  const query = new URLSearchParams({ runId, assetSha256: assetSha, pageIndex: String(pageIndex) });
  if (revisionSha256) query.set("revisionSha256", revisionSha256);
  const response = await context.request.get(`${apiUrl}/api/document-annotations?${query}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function saved(pageIndex, expectedCount) {
  await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
  return until(() => snapshot(pageIndex), (value) => value.revisionSha256 && value.annotations.length === expectedCount,
    `page ${pageIndex + 1} persisted ${expectedCount} strokes`);
}
async function selectPage(index, width, height) {
  await page.getByRole("combobox", { name: "页码", exact: true }).selectOption(String(index));
  await ready(width, height);
}

try {
  let page0Snapshot;
  let page0Paths;
  let page1Snapshot;
  let originalSource;
  let originalAnnotations;
  await step("full App opens unique two-page PDF from the actual file input", async () => {
    // Keep the actual initial list response, but deliver it after upload. This
    // reproduces a slow directory read without mocking its content or the API.
    let listCaptured, releaseList, listDelivered;
    const captured = new Promise((resolve) => { listCaptured = resolve; });
    const released = new Promise((resolve) => { releaseList = resolve; });
    const delivered = new Promise((resolve) => { listDelivered = resolve; });
    let held = false;
    await page.route(/\/api\/documents(?:\?|$)/, async (route) => {
      if (route.request().method() !== "GET" || held) { await route.continue(); return; }
      held = true;
      const response = await route.fetch();
      listCaptured(await response.json());
      await released;
      await route.fulfill({ response });
      listDelivered();
    });
    await page.goto(appUrl);
    await page.locator(".stage-mode-switch button").nth(1).click();
    await page.locator('.document-workspace input[type="file"]').waitFor({ state: "attached" });
    const originalList = await captured;
    assert.ok(originalList.documents.length > 0, "the isolated fixture must include an earlier source for the list/upload regression");
    originalSource = originalList.documents[0];
    if (originalSource) {
      const query = new URLSearchParams({ runId, assetSha256: originalSource.assetSha256, pageIndex: "0" });
      originalAnnotations = await (await context.request.get(`${apiUrl}/api/document-annotations?${query}`)).json();
    }
    const source = await readFile(`${fixtureRoot}/two-page-crop-rotation.pdf`);
    const upload = page.waitForResponse((response) => response.url().endsWith("/api/documents") && response.request().method() === "POST");
    await page.locator('.document-workspace input[type="file"]').setInputFiles({
      name: `document-functional-${nonce}.pdf`, mimeType: "application/pdf",
      buffer: Buffer.concat([source, Buffer.from(`\n% document-functional-${nonce}\n`)]),
    });
    const response = await upload;
    assert.equal(response.status(), 201);
    const document = await response.json();
    assetSha = document.assetSha256;
    assert.equal(document.runId, runId);
    assert.deepEqual(document.pages.map(({ width, height, rotation }) => [width, height, rotation]), [[400, 300, 0], [500, 600, 90]]);
    await until(() => page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), (value) => value === assetSha, "uploaded source selected");
    releaseList(); await delivered;
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(await page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), assetSha,
      "late initial list cannot revert the just-uploaded source");
    const sourceSelect = page.getByRole("combobox", { name: "源文件", exact: true });
    const originalShas = originalList.documents.map((item) => item.assetSha256);
    const available = await until(() => sourceSelect.locator("option").evaluateAll((options) => options.map((option) => ({
      value: option.value, disabled: option.disabled,
    }))), (options) => originalShas.every((sha) => options.some((option) => option.value === sha)),
    "all earlier source options are restored after the upload");
    assert.ok(available.some((option) => option.value === assetSha), "the uploaded source remains in the refreshed list");
    assert.ok(available.filter((option) => originalShas.includes(option.value)).every((option) => !option.disabled),
      "earlier source options remain selectable");
    assert.equal(await sourceSelect.inputValue(), assetSha, "refresh preserves the uploaded selection");
    await sourceSelect.selectOption(originalSource.assetSha256);
    await ready(originalSource.pages[0].width, originalSource.pages[0].height);
    assert.equal(await count(), originalAnnotations.annotations.length, "an earlier source still opens its retained ink");
    await sourceSelect.selectOption(assetSha);
    await ready(400, 300);
    assert.equal(await count(), 0);
    assert.equal(requests.filter((request) => request.url.includes("document-annotations")).length, 0,
      "switching to the earlier source and back does not save or alter either page");
    const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
    near(raster.width / raster.height, 4 / 3, 0.005);
  });

  await step("single points, short strokes and curves persist page coordinates and SVG geometry", async () => {
    await tool("画笔").click();
    await draw([[0.15, 0.18]]);
    const shortStart = await coords([0.3, 0.18]);
    await page.mouse.move(...shortStart); await page.mouse.down();
    await page.mouse.move(shortStart[0] + 2, shortStart[1] + 1); await page.mouse.up();
    await until(count, (value) => value === 2, "short stroke retained");
    const curve = Array.from({ length: 25 }, (_, index) => [0.15 + index * 0.02, 0.42 + Math.sin(index / 3) * 0.08]);
    await draw(curve);
    page0Snapshot = await saved(0, 3);
    const writes = requests.filter((request) => request.url.includes("document-annotations"));
    assert.equal(writes.length, 3);
    assert.ok(writes.every((request) => request.body.assetSha256 === assetSha), "every stroke saves against the uploaded source");
    if (originalSource) {
      const query = new URLSearchParams({ runId, assetSha256: originalSource.assetSha256, pageIndex: "0" });
      const unchanged = await (await context.request.get(`${apiUrl}/api/document-annotations?${query}`)).json();
      assert.deepEqual(unchanged, originalAnnotations, "the earlier source remains unchanged");
    }
    assert.equal(page0Snapshot.annotations[0].points.length, 1);
    assert.equal(page0Snapshot.annotations[1].points.length, 2);
    assert.ok(page0Snapshot.annotations[2].points.length >= 20);
    near(page0Snapshot.annotations[0].points[0][0], 0.15);
    near(page0Snapshot.annotations[0].points[0][1], 0.18);
    assert.ok(page0Snapshot.annotations.every((mark) => mark.kind === "freehand" && mark.lineWidth === 0.004));
    page0Paths = await paths();
    for (let index = 0; index < page0Paths.length; index += 1) {
      const points = page0Snapshot.annotations[index].points;
      const expected = `M ${points[0][0] * 400} ${points[0][1] * 300}`;
      assert.ok(page0Paths[index].d.startsWith(expected));
      assert.equal(Number(page0Paths[index].width), 1.2);
    }
  });

  await step("pointer-centred Ctrl-wheel zoom and viewport resize preserve page ink", async () => {
    const before = await rect();
    const anchor = [before.x + before.width * 0.52, before.y + before.height * 0.55];
    await page.mouse.move(...anchor); await page.keyboard.down("Control");
    await page.mouse.wheel(0, -160); await page.keyboard.up("Control");
    const after = await until(rect, (box) => box.width > before.width * 1.1, "zoom applied");
    near((anchor[0] - after.x) / after.width, 0.52, 0.002);
    near((anchor[1] - after.y) / after.height, 0.55, 0.002);
    assert.deepEqual(await paths(), page0Paths);
    await page.setViewportSize({ width: 1650, height: 1080 });
    assert.deepEqual(await paths(), page0Paths);
    await tool("适合窗口").click();
    await ready(400, 300);
  });

  await step("Space pan remains active through keyup, with subsequent ink at the displayed page point", async () => {
    const before = await rect();
    const start = await coords([0.5, 0.5]);
    await page.locator(".document-viewport").focus();
    await page.keyboard.down("Space"); await page.mouse.move(...start); await page.mouse.down();
    await page.mouse.move(start[0] + 35, start[1] + 22);
    await page.keyboard.up("Space");
    assert.equal(await page.locator(".document-viewport").getAttribute("data-tool"), "pan");
    await page.mouse.up();
    const after = await rect();
    near(after.x - before.x, 35, 0.1); near(after.y - before.y, 22, 0.1);
    assert.equal(await page.locator(".document-viewport").getAttribute("data-tool"), "freehand");
    await draw([[0.72, 0.7], [0.8, 0.7]]);
    const state = await saved(0, 4);
    near(state.annotations[3].points[0][0], 0.72); near(state.annotations[3].points[0][1], 0.7);
  });

  await step("same-frame middle pan move/up publishes the final view before the next stroke", async () => {
    await page.evaluate(() => document.querySelector(".document-viewport").addEventListener("pointerdown", (event) => { window.__documentTestPointerId = event.pointerId; }, { once: true }));
    const before = await rect(), start = await coords([0.5, 0.5]);
    await page.mouse.move(...start); await page.mouse.down({ button: "middle" });
    await page.evaluate(([x, y]) => {
      const node = document.querySelector(".document-viewport"), pointerId = window.__documentTestPointerId;
      node.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, pointerId, pointerType: "mouse", clientX: x, clientY: y, buttons: 4, button: 1 }));
      node.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerId, pointerType: "mouse", clientX: x, clientY: y, buttons: 0, button: 1 }));
    }, [start[0] - 24, start[1] + 16]);
    await page.mouse.up({ button: "middle" });
    const after = await rect();
    near(after.x - before.x, -24, 0.1); near(after.y - before.y, 16, 0.1);
    await draw([[0.72, 0.82], [0.8, 0.82]]);
    const state = await saved(0, 5);
    near(state.annotations[4].points[0][0], 0.72); near(state.annotations[4].points[0][1], 0.82);
    page0Paths = await paths(); page0Snapshot = state;
  });

  await step("CropBox and 90-degree page rotation use a separate 500-by-600 ink surface", async () => {
    await selectPage(1, 500, 600);
    assert.equal(await count(), 0);
    await tool("直线").click(); await draw([[0.2, 0.3], [0.8, 0.3]]);
    await tool("箭头").click(); await draw([[0.2, 0.5], [0.7, 0.65]]);
    await tool("圈选").click(); await draw([[0.25, 0.72], [0.55, 0.9]]);
    page1Snapshot = await saved(1, 3);
    assert.deepEqual(page1Snapshot.annotations.map((mark) => mark.kind), ["line", "arrow", "circle"]);
    const first = page1Snapshot.annotations[0];
    near(first.points[0][0], 0.2); near(first.points[0][1], 0.3);
    const displayed = await paths();
    assert.equal(displayed[0].d, `M ${first.points[0][0] * 500} ${first.points[0][1] * 600} L ${first.points[1][0] * 500} ${first.points[1][1] * 600}`);
    assert.equal(Number(displayed[0].width), 2);
    const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
    near(raster.width / raster.height, 5 / 6, 0.005);
    await selectPage(0, 400, 300); assert.deepEqual(await paths(), page0Paths);
    await selectPage(1, 500, 600); assert.equal(await count(), 3);
  });

  await step("eraser fast sweep deletes one whole stroke and one Undo/Redo restores/deletes it", async () => {
    await tool("橡皮擦").click();
    assert.equal(await tool("橡皮擦").getAttribute("aria-pressed"), "true");
    assert.equal(await tool("画笔").getAttribute("aria-pressed"), "false");
    await draw([[0.5, 0.22], [0.5, 0.38]], { expectedCount: 2 });
    const remaining = await saved(1, 2);
    assert.deepEqual(remaining.annotations.map((mark) => mark.id), page1Snapshot.annotations.slice(1).map((mark) => mark.id));
    await tool("撤销").click(); await until(count, (value) => value === 3, "single undo restores erased stroke");
    assert.deepEqual((await saved(1, 3)).annotations, page1Snapshot.annotations);
    await tool("重做").click(); await until(count, (value) => value === 2, "single redo repeats erase");
    await saved(1, 2);
    await tool("撤销").click(); await until(count, (value) => value === 3, "restored page for subsequent checks");
    await saved(1, 3);
  });

  await step("pointercancel discards the live stroke and capture commits an outside-page release", async () => {
    await tool("画笔").click();
    await page.evaluate(() => document.querySelector(".document-viewport").addEventListener("pointerdown", (event) => { window.__documentTestPointerId = event.pointerId; }, { once: true }));
    const start = await coords([0.12, 0.12]);
    await page.mouse.move(...start); await page.mouse.down(); await page.mouse.move(start[0] + 12, start[1] + 8);
    await page.evaluate(() => document.querySelector(".document-viewport").dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerId: window.__documentTestPointerId })));
    await page.mouse.up();
    assert.equal(await count(), 3);
    assert.equal(await page.locator("[data-live-ink]").getAttribute("d"), "");
    const second = await coords([0.85, 0.15]);
    await page.mouse.move(...second); await page.mouse.down();
    assert.equal(await page.locator(".document-viewport").evaluate((node) => node.hasPointerCapture(1)), true);
    await page.mouse.move(1640, 1060); await page.mouse.up();
    await until(count, (value) => value === 4, "captured outside release committed");
    const state = await saved(1, 4);
    assert.ok(state.annotations[3].points.flat().every((value) => value >= 0 && value <= 1));
    assert.deepEqual(state.annotations[3].points.at(-1), [1, 1]);
  });

  await step("without pointer capture, window move/up completes the stroke exactly once", async () => {
    await page.locator(".document-viewport").evaluate((node) => { node.__originalCapture = node.setPointerCapture; node.setPointerCapture = undefined; });
    try {
      const start = await coords([0.15, 0.2]);
      await page.mouse.move(...start); await page.mouse.down();
      await page.mouse.move(1640, 1060); await page.mouse.up();
      await until(count, (value) => value === 5, "window fallback release committed");
      page1Snapshot = await saved(1, 5);
      assert.deepEqual(page1Snapshot.annotations[4].points.at(-1), [1, 1]);
      assert.equal(await page.locator(".document-viewport").getAttribute("data-active"), "false");
    } finally {
      await page.locator(".document-viewport").evaluate((node) => { node.setPointerCapture = node.__originalCapture; delete node.__originalCapture; });
    }
  });

  const comment = `检查旋转裁切页上的箭头与入口标记 ${nonce}`;
  let submittedRef;
  await step("page comment and intent submit the exact persisted document revision without 3D gestures", async () => {
    await page.locator("#document-comment").fill(comment);
    await page.getByRole("button", { name: "保存本页", exact: true }).click();
    page1Snapshot = await until(() => snapshot(1), (value) => value.comment === comment, "comment persisted");
    await page.screenshot({ path: screenshotPath, fullPage: true });
    const intentResponse = page.waitForResponse((response) => response.url().endsWith("/api/intents") && response.request().method() === "POST");
    await page.getByRole("button", { name: "提交本页意见", exact: true }).click();
    const response = await intentResponse;
    assert.ok([201, 422].includes(response.status()), `intent status ${response.status()}`);
    const body = response.request().postDataJSON();
    assert.equal(body.utterance, comment);
    assert.deepEqual(body.gestures ?? [], []);
    assert.equal(body.documentAnnotations.length, 1);
    submittedRef = body.documentAnnotations[0];
    assert.deepEqual(submittedRef, { runId, assetSha256: assetSha, pageIndex: 1, revisionSha256: page1Snapshot.revisionSha256 });
    const historical = await snapshot(1, submittedRef.revisionSha256);
    assert.deepEqual(historical.annotations, page1Snapshot.annotations);
    const comments = await until(async () => (await context.request.get(`${apiUrl}/api/document-comments?runId=${runId}`)).json(),
      (result) => result.comments.some((item) => item.utterance === comment), "submitted comment retained");
    assert.deepEqual(comments.comments.find((item) => item.utterance === comment).documentAnnotations, [submittedRef]);
    await page.locator(".document-submitted summary").click();
    const article = page.locator(".document-submitted article").filter({ hasText: comment });
    await article.getByRole("button").click();
    await page.locator(".document-review-banner").waitFor();
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    await until(count, (value) => value === page1Snapshot.annotations.length, "historical annotation snapshot loaded");
    assert.equal(await tool("画笔").isDisabled(), true);
    assert.equal(await page.locator("#document-comment").inputValue(), comment);
    assert.deepEqual((await paths()).map((path) => path.id), page1Snapshot.annotations.map((mark) => mark.id));
    await page.getByRole("button", { name: "返回当前批注", exact: true }).click();
    await ready(400, 300);
  });

  await step("saved pages and historical comments reopen after closing the browser page", async () => {
    await page.close(); page = await context.newPage(); watch(page);
    await page.goto(appUrl); await page.locator(".stage-mode-switch button").nth(1).click();
    await page.getByRole("combobox", { name: "源文件", exact: true }).selectOption(assetSha);
    await ready(400, 300);
    assert.deepEqual(await paths(), page0Paths);
    await selectPage(1, 500, 600);
    assert.equal(await page.locator("#document-comment").inputValue(), comment);
    assert.deepEqual((await paths()).map((path) => path.id), page1Snapshot.annotations.map((mark) => mark.id));
    await page.locator(".document-submitted summary").click();
    await page.locator(".document-submitted article").filter({ hasText: comment }).getByRole("button").click();
    await page.locator(".document-review-banner").waitFor();
    await page.locator(".document-save-state").filter({ hasText: "已保存" }).waitFor();
    await until(count, (value) => value === page1Snapshot.annotations.length, "reopened historical annotation snapshot loaded");
    assert.equal(await tool("画笔").isDisabled(), true);
    await page.getByRole("button", { name: "返回当前批注", exact: true }).click();
    await ready(400, 300);
  });

  await step("PNG and EXIF-oriented JPEG render at the API visible dimensions", async () => {
    for (const fileName of ["reference.png", "reference-exif.jpg"]) {
      const upload = page.waitForResponse((response) => response.url().endsWith("/api/documents") && response.request().method() === "POST");
      await page.locator('.document-workspace input[type="file"]').setInputFiles(`${fixtureRoot}/${fileName}`);
      const response = await upload;
      assert.ok([200, 201].includes(response.status()));
      const document = await response.json(), [visible] = document.pages;
      await until(() => page.getByRole("combobox", { name: "源文件", exact: true }).inputValue(), (value) => value === document.assetSha256, "uploaded image selected");
      await ready(visible.width, visible.height);
      const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
      near(raster.width / raster.height, visible.width / visible.height, 0.005);
      if (fileName.endsWith(".jpg")) assert.equal(visible.width < visible.height, true, "EXIF 6 turns this landscape fixture upright");
    }
  });
  assert.deepEqual(errors, [], "no browser application exceptions");
  console.log(JSON.stringify({ passed: passed.length, assetSha256: assetSha, screenshotPath, submittedRef,
    annotationWrites: requests.filter((request) => request.url.includes("document-annotations") && request.body.assetSha256 === assetSha).length }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ failedStep: currentStep, passed, assetSha256: assetSha, browserErrors: errors,
    selectedSource: await page.getByRole("combobox", { name: "源文件", exact: true }).inputValue().catch(() => null),
    annotationWrites: requests.filter((request) => request.url.includes("document-annotations")).map((request) => ({
      assetSha256: request.body.assetSha256, pageIndex: request.body.pageIndex, count: request.body.annotations.length,
    })),
    visibleErrors: await page.locator('.document-error, .document-render-error, [role="alert"]').allTextContents().catch(() => []) }, null, 2));
  throw error;
} finally {
  await context.close(); await browser.close();
}
