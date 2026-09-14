import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

// Read-only regression against an explicitly supplied real project URL.
// No fixture upload, ink input, save, intent, or retained-state mutation occurs.
assert.ok(process.env.DOCUMENT_LINK_URL, "DOCUMENT_LINK_URL is required");
const link = new URL(process.env.DOCUMENT_LINK_URL);
assert.equal(link.searchParams.get("view"), "documents");
const runId = link.searchParams.get("documentRun");
const sourceSha = link.searchParams.get("documentSource");
const revisionRef = link.searchParams.get("documentRevision");
const pageText = link.searchParams.get("documentPage");
const pageIndex = Number(pageText);
assert.ok(runId && sourceSha && pageText !== null, "the link must name its run, source and zero-based page");
assert.match(sourceSha, /^[a-f0-9]{64}$/);
assert.ok(Number.isSafeInteger(pageIndex) && pageIndex >= 0, "documentPage must be a non-negative integer");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const passed = [];
const attemptedWrites = [];
const pageErrors = [];
let activeCase = "source metadata";
let currentPage;
let metadata;

async function freshPage() {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
  context.on("page", (page) => page.on("pageerror", (error) => pageErrors.push({ test: activeCase, message: String(error) })));
  // A regression must fail before an unexpected application request can alter
  // the real project. Normal navigation here needs only GET requests.
  await context.route(/\/api\//, async (route) => {
    const request = route.request();
    if (["GET", "HEAD", "OPTIONS"].includes(request.method())) { await route.continue(); return; }
    attemptedWrites.push({ test: activeCase, method: request.method(), url: request.url() });
    await route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  currentPage = page;
  return { context, page };
}

async function runCase(name, action) {
  activeCase = name;
  const { context, page } = await freshPage();
  try {
    await action(page, context);
    assert.deepEqual(attemptedWrites, [], "read-only document navigation must not attempt writes");
    passed.push(name);
    console.log(`PASS ${name}`);
  } catch (error) {
    console.error(JSON.stringify({ failedCase: name, url: page.url(),
      visibleErrors: await page.locator('.document-empty [role="alert"], .document-error, .document-render-error').allTextContents().catch(() => []),
      source: await page.locator(".document-header > select").inputValue().catch(() => null),
      pageIndex: await page.locator(".document-pages select").inputValue().catch(() => null),
      attemptedWrites, pageErrors }, null, 2));
    throw error;
  } finally {
    await context.close();
    currentPage = null;
  }
}

async function selected(page, sha, index, revision = revisionRef) {
  await page.waitForFunction(({ sha, index, revision }) =>
    document.querySelector(".document-header > select")?.value === (revision ?? sha) &&
    document.querySelector(".document-pages select")?.value === String(index), { sha, index, revision });
}

async function documentView(page) {
  await page.locator(".stage-mode-switch button").nth(1).waitFor();
  assert.equal(await page.locator(".stage-mode-switch button").nth(1).getAttribute("aria-pressed"), "true");
  assert.equal(await page.locator(".stage-model").getAttribute("aria-hidden"), "true");
}

async function unavailable(page, url) {
  const bytes = [];
  page.on("request", (request) => {
    if (/\/api\/documents\/[^/]+\/bytes/.test(request.url())) bytes.push(request.url());
  });
  await page.goto(url.href);
  await documentView(page);
  const alert = page.locator('.document-empty [role="alert"]');
  await alert.waitFor();
  assert.match(await alert.textContent(), /不可用|unavailable/i);
  assert.equal(await page.locator(".document-page, .document-viewport").count(), 0, "an invalid link must not display another page");
  return bytes;
}

try {
  await runCase("clicking the Board source link opens its exact drawing in a new page", async (page, context) => {
    const board = new URL(link);
    board.searchParams.set("view", "board");
    await page.goto(board.href);
    const sources = page.locator(".monkeyboard-source-link");
    await sources.first().waitFor();
    const hrefs = await sources.evaluateAll((items) => items.map((item) => item.href));
    const index = hrefs.findIndex((href) => {
      const query = new URL(href).searchParams;
      return query.get("documentRun") === runId && query.get("documentSource") === sourceSha &&
        query.get("documentPage") === pageText && query.get("documentRevision") === revisionRef;
    });
    assert.ok(index >= 0, "the Board lists the requested drawing page");
    const opened = context.waitForEvent("page");
    await sources.nth(index).click();
    const drawing = await opened;
    await drawing.waitForLoadState("domcontentloaded");
    await documentView(drawing);
    await selected(drawing, sourceSha, pageIndex);
    await drawing.locator('.document-viewport[data-ready="true"]').waitFor();
  });

  await runCase("valid deep link opens the exact source/page with a ready raster and enabled pen", async (page, context) => {
    const query = new URLSearchParams({ runId });
    const response = await context.request.get(`${link.origin}/api/documents?${query}`);
    assert.equal(response.status(), 200, "same-origin document API is available");
    const listing = await response.json();
    assert.equal(listing.runId, runId);
    const source = listing.documents.find((item) => item.assetSha256 === sourceSha &&
      (revisionRef === null || item.revisionRef === revisionRef));
    assert.ok(source, "the requested source belongs to the requested run");
    const pageInfo = source.pages.find((item) => item.pageIndex === pageIndex);
    assert.ok(pageInfo, "the requested visible page exists");
    metadata = { runId, sourceSha, pageIndex, width: pageInfo.width, height: pageInfo.height, pageCount: source.pageCount };
    const documentReads = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/document-annotations" || /\/api\/documents\/[^/]+\/bytes/.test(url.pathname)) documentReads.push(url);
    });
    await page.goto(link.href);
    await documentView(page);
    await selected(page, sourceSha, pageIndex);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    await page.waitForFunction(() => document.querySelector(".document-tools button")?.disabled === false);
    assert.equal(await page.locator(".document-page__ink").getAttribute("viewBox"), `0 0 ${pageInfo.width} ${pageInfo.height}`);
    const raster = await page.locator(".document-page__raster").evaluate((canvas) => ({ width: canvas.width, height: canvas.height }));
    assert.ok(raster.width > 0 && raster.height > 0);
    assert.ok(Math.abs(raster.width / raster.height - pageInfo.width / pageInfo.height) < 0.005);
    assert.ok(documentReads.some((url) => url.pathname === "/api/document-annotations"), "the selected page's annotations were read");
    for (const url of documentReads) {
      assert.equal(url.searchParams.get("runId"), runId, "document bytes and ink use the URL run");
      if (url.pathname === "/api/document-annotations") {
        assert.equal(url.searchParams.get("drawingRevisionRef"), revisionRef, "ink uses the URL drawing revision");
        assert.equal(url.searchParams.get("assetSha256"), sourceSha);
        assert.equal(url.searchParams.get("pageIndex"), String(pageIndex));
      } else {
        assert.equal(url.searchParams.get("revisionRef"), revisionRef, "document bytes use the URL drawing revision");
        assert.ok(url.pathname.includes(`/${sourceSha}/bytes`));
      }
    }
  });

  await runCase("a failed 3D editing-base restore does not prevent opening the linked document", async (page) => {
    await page.route(/\/api\/state(?:\?|$)/, (route) => route.fulfill({ status: 404, contentType: "application/json",
      body: JSON.stringify({ code: "STATE_RECORD_NOT_FOUND", detail: "This test's 3D editing base is unavailable." }) }));
    await page.goto(link.href);
    await documentView(page);
    await selected(page, sourceSha, pageIndex);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    await page.waitForFunction(() => document.querySelector(".document-tools button")?.disabled === false);
    await page.locator(".stage-mode-switch button").nth(0).click();
    await page.locator(".refusal__title").waitFor();
    await page.getByRole("button", { name: /MonkeyDiagram/ }).click();
    await selected(page, sourceSha, pageIndex);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
  });

  await runCase("the linked page is usable while 3D state is still pending", async (page) => {
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    await page.route(/\/api\/state(?:\?|$)/, async (route) => { await pending; await route.continue(); });
    try {
      await page.goto(link.href);
      await selected(page, sourceSha, pageIndex);
      await page.locator('.document-viewport[data-ready="true"]').waitFor();
      await page.waitForFunction(() => document.querySelector(".document-tools button")?.disabled === false);
      assert.equal(await page.locator('.boot[data-mode="boot"]').count(), 0, "3D loading must not cover the document");
    } finally { release(); }
  });

  await runCase("switching from the linked page to 3D and back preserves its source and page", async (page) => {
    await page.goto(link.href);
    await selected(page, sourceSha, pageIndex);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
    await page.locator(".stage-mode-switch button").nth(0).click();
    assert.equal(await page.locator(".stage-mode-switch button").nth(0).getAttribute("aria-pressed"), "true");
    await page.locator(".stage-mode-switch button").nth(1).click();
    await documentView(page);
    await selected(page, sourceSha, pageIndex);
    await page.locator('.document-viewport[data-ready="true"]').waitFor();
  });

  await runCase("an unknown SHA shows an explicit unavailable state without selecting another source", async (page) => {
    const invalid = new URL(link);
    const missingSha = sourceSha === "0".repeat(64) ? "1".repeat(64) : "0".repeat(64);
    invalid.searchParams.set("documentSource", missingSha);
    const bytes = await unavailable(page, invalid);
    assert.equal(await page.locator(".document-header > select").inputValue(), missingSha);
    assert.deepEqual(bytes, [], "an unknown source must not fall back to another source's bytes");
  });

  await runCase("an out-of-range page shows unavailable without displaying a replacement page", async (page) => {
    const invalid = new URL(link);
    const missingPage = metadata.pageCount + 7;
    invalid.searchParams.set("documentPage", String(missingPage));
    await unavailable(page, invalid);
    await selected(page, sourceSha, missingPage);
  });

  await runCase("the normal URL still starts in 3D without mounting the document workspace", async (page) => {
    const normal = new URL(link); normal.search = ""; normal.hash = "";
    await page.goto(normal.href);
    await page.locator(".stage-mode-switch button").nth(0).waitFor();
    assert.equal(await page.locator(".stage-mode-switch button").nth(0).getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator(".stage-mode-switch button").nth(1).getAttribute("aria-pressed"), "false");
    assert.equal(await page.locator(".document-workspace").count(), 0);
  });

  assert.deepEqual(pageErrors, [], "no browser application exceptions");
  console.log(JSON.stringify({ passed: passed.length, ...metadata, annotationPuts: 0, intentPosts: 0,
    blockedMutations: attemptedWrites.length }, null, 2));
} finally {
  if (currentPage) await currentPage.context().close();
  await browser.close();
}
