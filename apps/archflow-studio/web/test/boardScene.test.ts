import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

import type { SourceDocumentDto } from "../src/api/generated/index.ts";

function document(overrides: Partial<SourceDocumentDto> = {}): SourceDocumentDto {
  return {
    projectId: "project-a", runId: "drawing-run", assetSha256: "a".repeat(64),
    fileName: "Elevation.pdf", mimeType: "application/pdf", sizeBytes: 12000, pageCount: 2,
    pages: [
      { pageIndex: 0, width: 1200, height: 800, rotation: 0 },
      { pageIndex: 1, width: 800, height: 1200, rotation: 90 },
    ],
    drawingId: "east-elevation", revisionRef: "project://project-a/runs/drawing-run/records/drawing-revision-1.json",
    ...overrides,
  };
}

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  return await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardScene.ts") as typeof import("../src/workspaces/monkeyboard/boardScene.ts");
}

test("drawing revisions remain distinct when their file bytes are identical", async (t) => {
  const { documentKey, pageKey, pageSource, findSource } = await harness(t);
  const first = document();
  const second = document({ revisionRef: "project://project-a/runs/drawing-run/records/drawing-revision-2.json" });
  const anotherRun = document({ runId: "another-run" });
  assert.equal(first.assetSha256, second.assetSha256);
  assert.notEqual(documentKey(first), documentKey(second));
  assert.notEqual(documentKey(first), documentKey(anotherRun));
  assert.notEqual(pageKey(pageSource(first, 0)), pageKey(pageSource(second, 0)));
  assert.notEqual(pageKey(pageSource(first, 0)), pageKey(pageSource(first, 1)));
  assert.equal(findSource([second, anotherRun, first], pageSource(first, 1)), first);
  assert.equal(findSource([first], pageSource(second, 1)), undefined, "matching pixels cannot substitute another drawing revision");
});

test("explicit page replacements resolve reordered deliveries without guessing names or page numbers", async (t) => {
  const { pageKey, pageSource, pageReplacements } = await harness(t);
  const original = document({ drawingId: null, revisionRef: null });
  const middle = document({ runId: "middle", assetSha256: "b".repeat(64), drawingId: null, revisionRef: null,
    pageCount: 1, pages: [{ pageIndex: 0, width: 800, height: 1200, rotation: 90 }],
    replacesPages: [{ ...pageSource(original, 1), newPageIndex: 0 }] });
  const latest = document({ runId: "latest", assetSha256: "c".repeat(64), drawingId: null, revisionRef: null,
    replacesPages: [{ ...pageSource(middle, 0), newPageIndex: 1 }] });
  for (const documents of [[latest, original, middle], [original, middle, latest]]) {
    const mapping = pageReplacements(documents);
    assert.deepEqual(mapping.get(pageKey(pageSource(original, 1))), pageSource(latest, 1));
    assert.deepEqual(mapping.get(pageKey(pageSource(middle, 0))), pageSource(latest, 1));
    assert.equal(mapping.has(pageKey(pageSource(original, 0))), false, "another page in the same file is not replaced");
  }
  assert.equal(pageReplacements([original, document({ runId: "same-name", assetSha256: "d".repeat(64) })]).size, 0);
  assert.throws(() => pageReplacements([original, middle, { ...latest, replacesPages: [{ ...pageSource(original, 1), newPageIndex: 0 }] }]), /competing/);
  assert.throws(() => pageReplacements([{ ...original, replacesPages: [{ ...pageSource(middle, 0), newPageIndex: 1 }] }, middle]), /earlier page/);
});

test("retained received identities keep a removed drawing absent while allowing its next revision", async (t) => {
  const { documentKey, pageSource, imageSource } = await harness(t);
  const removed = document();
  const nextRevision = document({ revisionRef: "project://project-a/runs/drawing-run/records/drawing-revision-2.json" });
  const saved = JSON.parse(JSON.stringify({
    elements: [{ id: "removed-page", type: "image", isDeleted: true, customData: { sourceDocument: pageSource(removed, 1) } }],
    seenDocuments: [documentKey(removed)],
  }));
  const seen = new Set(saved.seenDocuments);
  const newlyReceived = [removed, nextRevision].filter((item) => !seen.has(documentKey(item)));
  assert.deepEqual(newlyReceived, [nextRevision]);
  assert.deepEqual(imageSource(saved.elements[0]), pageSource(removed, 1), "deleted elements keep their exact source for undo");

  const uploaded = document({ drawingId: null, revisionRef: null });
  assert.equal(documentKey(uploaded), documentKey({ ...uploaded, revisionRef: undefined }));
  assert.notEqual(documentKey(uploaded), documentKey({ ...uploaded, assetSha256: "b".repeat(64) }));
});

test("persisted image metadata resolves only its exact run, file, drawing revision and page", async (t) => {
  const { imageSource, pageSource, findSource } = await harness(t);
  const original = document();
  const source = pageSource(original, 1);
  const element = JSON.parse(JSON.stringify({ type: "image", customData: { sourceDocument: source } }));
  assert.deepEqual(imageSource(element), source);
  assert.equal(findSource([original], source), original);
  for (const changed of [
    { ...source, runId: "another-run" },
    { ...source, assetSha256: "b".repeat(64) },
    { ...source, revisionRef: null },
    { ...source, pageIndex: 2 },
  ]) assert.equal(findSource([original], changed), undefined);
  for (const malformed of [
    { type: "text", customData: { sourceDocument: source } },
    { type: "image" },
    { type: "image", customData: { sourceDocument: { ...source, pageIndex: -1 } } },
    { type: "image", customData: { sourceDocument: { ...source, pageIndex: 0.5 } } },
    { type: "image", customData: { sourceDocument: { ...source, revisionRef: undefined } } },
  ]) assert.equal(imageSource(malformed), null);
});

test("source actions resolve one explicit image or native frame independently of model and mark geometry", async (t) => {
  const { selectedPageSource, pageSource, findSource } = await harness(t);
  const original = document({ modelSource: null });
  const source = pageSource(original, 1);
  const elements = [
    { id: "page", type: "image", frameId: "frame", crop: { x: 10, y: 20 }, customData: { sourceDocument: source } },
    { id: "frame", type: "frame", customData: { sourceDocument: pageSource(original, 0) } },
    { id: "mark", type: "diamond", frameId: "frame", x: -1000, backgroundColor: "red", startBinding: { elementId: "page" } },
    { id: "label", type: "text", containerId: "page" },
    { id: "deleted", type: "image", frameId: "frame", isDeleted: true },
  ];
  const before = structuredClone(elements);
  for (const selection of [{ page: true }, { frame: true }, { frame: true, page: true }, { page: true, mark: true, label: true }]) {
    assert.deepEqual(selectedPageSource(elements, selection), source);
    assert.equal(findSource([original], selectedPageSource(elements, selection)!), original);
  }
  for (const selection of [{}, { page: false }, { mark: true }, { label: true }, { deleted: true }, { page: true, missing: true }]) {
    assert.equal(selectedPageSource(elements, selection), null, "only explicit live image/frame membership may choose a source");
  }
  assert.deepEqual(elements, before);
});

test("source actions refuse multiple images, unregistered sources and frame metadata without a page", async (t) => {
  const { selectedPageSource, pageSource, findSource } = await harness(t);
  const original = document();
  const source = pageSource(original, 1);
  const page = { id: "page", type: "image", frameId: "frame", customData: { sourceDocument: source } };
  const frame = { id: "frame", type: "frame", customData: { sourceDocument: source } };
  for (const second of [page, { ...page, customData: { sourceDocument: pageSource(original, 0) } }, { type: "image" }]) {
    const elements = [page, frame, { ...second, id: "second", frameId: "frame" }];
    assert.equal(selectedPageSource(elements, { page: true, second: true }), null);
    assert.equal(selectedPageSource(elements, { frame: true }), null, "even identical page copies require one selected image");
  }
  assert.equal(selectedPageSource([frame], { frame: true }), null);
  assert.equal(selectedPageSource([{ id: "raw", type: "image" }], { raw: true }), null);
  const stale = { ...page, customData: { sourceDocument: { ...source, revisionRef: "missing" } } };
  assert.equal(findSource([original], selectedPageSource([stale], { page: true })!), undefined);
});

test("opening a source preserves appearance parameters and selects its exact revision and page", async (t) => {
  const { documentUrl, pageSource } = await harness(t);
  const source = pageSource(document({ runId: "drawing run + 1" }), 1);
  const current = "http://127.0.0.1:5173/studio?view=board&documentRun=old&documentSource=old&documentRevision=old&documentPage=0&lang=zh-CN&theme=dark&fontScale=1.25#board";
  const opened = new URL(documentUrl(current, source));
  assert.equal(opened.origin, "http://127.0.0.1:5173");
  assert.equal(opened.pathname, "/studio");
  assert.equal(opened.hash, "#board");
  assert.deepEqual(Object.fromEntries(opened.searchParams), {
    view: "documents", documentRun: source.runId, documentSource: source.assetSha256,
    documentRevision: source.revisionRef, documentPage: "1", lang: "zh-CN", theme: "dark", fontScale: "1.25",
  });
  const uploadUrl = new URL(documentUrl(opened.href, { ...source, revisionRef: null, pageIndex: 0 }));
  assert.equal(uploadUrl.searchParams.has("documentRevision"), false, "an uploaded original clears a prior drawing revision");
  assert.equal(uploadUrl.searchParams.get("documentPage"), "0");
  assert.equal(uploadUrl.searchParams.get("theme"), "dark");
  assert.equal(uploadUrl.searchParams.get("lang"), "zh-CN");
  assert.equal(uploadUrl.searchParams.get("fontScale"), "1.25");
});

test("a new drawing is placed beyond existing visible work without repositioning it", async (t) => {
  const { nextDocumentPosition } = await harness(t);
  const elements = [
    { id: "image", x: 20, y: 50, width: 800, height: 500 },
    { id: "note", x: 950, y: -100, width: -200, height: 60 },
    { id: "removed", x: 5000, y: -5000, width: 800, height: 500, isDeleted: true },
  ];
  const before = structuredClone(elements);
  assert.deepEqual(nextDocumentPosition(elements), { x: 1246, y: -100 });
  assert.deepEqual(elements, before);
  assert.deepEqual(nextDocumentPosition([]), { x: 80, y: 80 });
});
