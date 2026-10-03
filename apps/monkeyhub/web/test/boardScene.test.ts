import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

import type { SourceDocumentDto } from "../src/api/project-runtime/generated/index.ts";

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

test("a new page joins the row of the selected page, or of the page placed last, at its top and height (#615)", async (t) => {
  const { nextPagePlacement, pageSource } = await harness(t);
  const source = (pageIndex: number) => ({ sourceDocument: pageSource(document(), pageIndex) });
  const elements = [
    { id: "frame-a", type: "frame", x: 90, y: 190, width: 420, height: 320 },
    { id: "page-a", type: "image", frameId: "frame-a", x: 100, y: 200, width: 400, height: 300, customData: source(0) },
    { id: "frame-b", type: "frame", x: 590, y: -10, width: 220, height: 170 },
    { id: "page-b", type: "image", frameId: "frame-b", x: 600, y: 0, width: 200, height: 150, customData: source(1) },
    { id: "note", type: "rectangle", x: 700, y: 250, width: 100, height: 40 },
    { id: "raw", type: "image", x: 3000, y: 3000, width: 100, height: 100 },
    { id: "gone", type: "image", x: 5000, y: 5000, width: 999, height: 999, isDeleted: true, customData: source(0) },
  ];
  const before = structuredClone(elements);
  const afterLast = { x: 810 + 96 + 10, y: 0, width: 100, height: 150 };
  assert.deepEqual(nextPagePlacement(elements, {}, 2 / 3), afterLast, "the page placed last, at its top and height");
  assert.deepEqual(nextPagePlacement(elements, { note: true, raw: true }, 2 / 3), afterLast,
    "a selection without a registered page anchors nothing");
  assert.deepEqual(nextPagePlacement(elements, { "page-a": false }, 2 / 3), afterLast);
  const besideA = { x: 800 + 96 + 10, y: 200, width: 400, height: 300 };
  assert.deepEqual(nextPagePlacement(elements, { "page-a": true }, 4 / 3), besideA,
    "the selected page anchors, and the note already in its row is passed, never covered");
  assert.deepEqual(nextPagePlacement(elements, { "frame-a": true }, 4 / 3), besideA, "a selected frame anchors its page");
  assert.equal(nextPagePlacement(elements.filter((element) => !String(element.id).startsWith("page")), {}, 1), null,
    "with no page on the board the caller places it as before");
  for (const aspect of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) assert.equal(nextPagePlacement(elements, {}, aspect), null);
  assert.deepEqual(elements, before, "nothing already on the board moves");
});

test("a new page passes strokes drawn leftwards and rotated marks by where they actually are (#615)", async (t) => {
  const { nextPagePlacement, pageSource } = await harness(t);
  const page = { id: "page", type: "image", x: 0, y: 0, width: 400, height: 300,
    customData: { sourceDocument: pageSource(document(), 0) } };
  // The new page's frame would span x 496 to 616. Stored at its first point, x 680, this stroke is drawn
  // leftwards to x 580: measured from x and width it misses the frame, measured by its points it is in the way.
  const stroke = { id: "stroke", type: "freedraw", x: 680, y: 100, width: 100, height: 4, points: [[0, 0], [-50, 4], [-100, 0]] };
  assert.equal(nextPagePlacement([page, stroke], {}, 1 / 3)!.x, 680 + 96 + 10, "the stroke is passed by where it really is");
  // A bar lying below the row is not in the way; turned a quarter about its centre (600, 410) it spans y 260 to 560
  // and x 590 to 610, across the new page.
  const bar = { id: "bar", type: "rectangle", x: 450, y: 400, width: 300, height: 20, angle: 0 };
  assert.equal(nextPagePlacement([page, bar], {}, 1 / 3)!.x, 400 + 96 + 10, "unturned, the bar is below the row");
  assert.equal(nextPagePlacement([page, { ...bar, angle: Math.PI / 2 }], {}, 1 / 3)!.x, 610 + 96 + 10,
    "turned, the bar crosses the row and is passed");
});


test("Tracing Paper reviews are named as review items on Board", async (t) => {
  const { boardDocumentFrameName } = await harness(t);
  const review = document({ fileName: "Tracing Paper - abc.png", mimeType: "image/png", pageCount: 1,
    pages: [{ pageIndex: 0, width: 120, height: 80, rotation: 0 }],
    viewRecipe: { schema: "TracingPaperSnapshot@1", kind: "tracing-paper-review" } });
  assert.equal(boardDocumentFrameName(review, 0), "Tracing Paper review · Tracing Paper - abc.png · 1/1");
});
