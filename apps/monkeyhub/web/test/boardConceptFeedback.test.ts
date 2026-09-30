import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { DocumentAnnotationsDto, SourceDocumentDto } from "../src/api/generated/index.ts";
import type { BoardFeedbackSelection } from "../src/workspaces/monkeyboard/boardFeedbackGeometry.ts";

const digest = (letter: string) => letter.repeat(64);
const model = { runId: "model-run", stateDigest: digest("s"), assetSha256: digest("m") };
const targetPage = { pageIndex: 0, width: 1000, height: 500, rotation: 0 };
const referencePage = { pageIndex: 0, width: 800, height: 800, rotation: 0 };
const targetDocument: SourceDocumentDto = {
  projectId: "project", runId: "target-run", assetSha256: digest("a"), fileName: "Plan.pdf",
  mimeType: "application/pdf", sizeBytes: 1000, pageCount: 1, pages: [targetPage], modelSource: model,
  modelSourceBindingRef: "binding-target", revisionRef: "project://project/runs/target-run/records/drawing.json",
  sourceStageRef: "stage-1",
};
const referenceDocument: SourceDocumentDto = {
  projectId: "project", runId: "reference-run", assetSha256: digest("b"), fileName: "Facade.png",
  mimeType: "image/png", sizeBytes: 800, pageCount: 1, pages: [referencePage], modelSource: null,
  modelSourceBindingRef: null, revisionRef: "project://project/runs/reference-run/records/reference.json",
  sourceStageRef: null,
};
const linkedReferenceDocument: SourceDocumentDto = {
  ...referenceDocument,
  modelSource: model,
  modelSourceBindingRef: "binding-reference",
  sourceStageRef: "stage-1",
};
const secondReferenceDocument: SourceDocumentDto = {
  ...referenceDocument, runId: "second-reference-run", assetSha256: digest("c"), fileName: "Courtyard.png",
  revisionRef: "project://project/runs/second-reference-run/records/reference.json",
};
const targetSource = { runId: targetDocument.runId, assetSha256: targetDocument.assetSha256,
  revisionRef: targetDocument.revisionRef!, pageIndex: 0 };
const referenceSource = { runId: referenceDocument.runId, assetSha256: referenceDocument.assetSha256,
  revisionRef: referenceDocument.revisionRef!, pageIndex: 0 };
const secondReferenceSource = { runId: secondReferenceDocument.runId, assetSha256: secondReferenceDocument.assetSha256,
  revisionRef: secondReferenceDocument.revisionRef!, pageIndex: 0 };

function element(type: ExcalidrawElement["type"], id: string, changes: Record<string, unknown> = {}): ExcalidrawElement {
  return { type, id, x: 0, y: 0, width: 100, height: 100, angle: 0, isDeleted: false,
    strokeColor: "#334455", backgroundColor: "transparent", strokeWidth: 2, strokeStyle: "solid",
    roughness: 0, opacity: 100, roundness: null, frameId: null, boundElements: null,
    ...(type === "image" ? { width: 1000, height: 500, fileId: `preview-${id}`, scale: [1, 1], crop: null,
      status: "saved", customData: { sourceDocument: id === "target" ? targetSource : referenceSource } } : {}),
    ...(["line", "arrow", "freedraw"].includes(type) ? { points: [[0, 0], [100, 100]],
      startArrowhead: null, endArrowhead: type === "arrow" ? "arrow" : null, elbowed: false } : {}),
    ...changes,
  } as unknown as ExcalidrawElement;
}

async function geometryModule(t: TestContext) {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  return await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardFeedbackGeometry.ts") as typeof import("../src/workspaces/monkeyboard/boardFeedbackGeometry.ts");
}

test("a target mark inside one page frame keeps another model-linked page as explicit visual reference", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const reference = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "target-frame" });
  const connector = element("arrow", "relationship", { x: 240, y: 160, width: 1000, height: 200,
    points: [[0, 0], [1000, 200]], startBinding: { elementId: "zone" }, endBinding: { elementId: "reference" } });
  const elements = [target, reference, circle, connector];
  const result = geometry.createBoardFeedback(elements, Object.fromEntries(elements.map((item) => [item.id, true])),
    [targetDocument, linkedReferenceDocument]);

  assert.deepEqual(result.source, targetSource);
  assert.equal(result.references?.length, 1);
  assert.deepEqual(result.references?.[0].source, referenceSource);
  assert.equal(result.references?.[0].document.modelSource?.runId, model.runId,
    "a reference may carry its own model binding without becoming the edit target");
  assert.match(result.references?.[0].referenceNote ?? "", /edit target toward this reference/);
  assert.equal(result.annotations.length, 1, "the target circle is page ink but the relationship connector stays on Board");
  assert.equal(result.annotations[0].kind, "circle");
  assert.deepEqual(result.annotationGroups, ["board:target:relationship", "board:target:zone"]);

  assert.throws(() => geometry.createBoardFeedback([target, reference, circle], { target: true, reference: true, zone: true },
    [targetDocument, linkedReferenceDocument]), (cause: unknown) =>
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_REFERENCE_UNLINKED");
});

test("a connector drawn between two references stays Board evidence instead of becoming edit-page ink", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const first = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  const second = element("image", "second", { x: 2000, width: 600, height: 600, frameId: "second-frame",
    customData: { sourceDocument: secondReferenceSource } });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "target-frame" });
  const toFirst = element("arrow", "link-first", { x: 240, y: 160, points: [[0, 0], [1000, 200]],
    startBinding: { elementId: "zone" }, endBinding: { elementId: "reference" } });
  const toSecond = element("arrow", "link-second", { x: 240, y: 160, points: [[0, 0], [1800, 200]],
    startBinding: { elementId: "zone" }, endBinding: { elementId: "second" } });
  // Both endpoints sit far to the right of the edit page; as ordinary ink this
  // arrow could only be refused as outside the page.
  const between = element("arrow", "between", { x: 1800, y: 300, points: [[0, 0], [200, 0]],
    startBinding: { elementId: "reference" }, endBinding: { elementId: "second" } });
  const elements = [target, first, second, circle, toFirst, toSecond, between];
  const result = geometry.createBoardFeedback(elements, Object.fromEntries(elements.map((item) => [item.id, true])),
    [targetDocument, linkedReferenceDocument, secondReferenceDocument]);

  assert.deepEqual(result.source, targetSource);
  assert.equal(result.references?.length, 2);
  assert.equal(result.annotations.length, 1, "only the target circle reaches the edit page");
  assert.equal(result.annotations[0].kind, "circle");
  assert.deepEqual(new Set(result.annotationGroups),
    new Set(["board:target:link-first", "board:target:link-second", "board:target:between", "board:target:zone"]));
});

test("a reference's own marks are refused rather than converted against the edit page", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const reference = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "target-frame" });
  const connector = element("arrow", "relationship", { x: 240, y: 160, points: [[0, 0], [1000, 200]],
    startBinding: { elementId: "zone" }, endBinding: { elementId: "reference" } });
  // Selecting the reference by its own frame pulls the ink drawn on that page in.
  const referenceInk = element("freedraw", "reference-ink", { x: 1300, y: 100, frameId: "reference-frame" });
  const elements = [target, reference, circle, connector, referenceInk];
  assert.throws(() => geometry.createBoardFeedback(elements, Object.fromEntries(elements.map((item) => [item.id, true])),
    [targetDocument, referenceDocument]), (cause: unknown) =>
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_REFERENCE_MARKED");
});

test("a frame shared by the edit page and a reference keeps the edit page's own marks and ink", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "shared" });
  const reference = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "shared" });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "shared" });
  // Bound to the shared frame, this arrow reaches no reference and stays page ink.
  const pointer = element("arrow", "pointer", { x: 200, y: 200, points: [[0, 0], [100, 60]],
    startBinding: { elementId: "shared" }, endBinding: { elementId: "zone" } });
  const elements = [target, reference, circle, pointer];
  const ids = Object.fromEntries(elements.map((item) => [item.id, true]));
  assert.throws(() => geometry.createBoardFeedback(elements, ids, [targetDocument, referenceDocument]),
    (cause: unknown) => cause instanceof geometry.BoardFeedbackGeometryError
      && cause.code === "BOARD_FEEDBACK_REFERENCE_UNLINKED",
    "a shared frame never counts as the reference's own explicit connector");

  const linked = element("arrow", "relationship", { x: 240, y: 160, points: [[0, 0], [1000, 200]],
    startBinding: { elementId: "zone" }, endBinding: { elementId: "reference" } });
  const joined = [...elements, linked];
  const result = geometry.createBoardFeedback(joined, Object.fromEntries(joined.map((item) => [item.id, true])),
    [targetDocument, referenceDocument]);
  assert.deepEqual(result.source, targetSource);
  assert.equal(result.references?.length, 1);
  assert.deepEqual(result.annotations.map((mark) => mark.id).sort(),
    ["board:target:pointer:end", "board:target:pointer:shaft", "board:target:zone"],
    "the pointer drawn on the edit page is still saved as that page's ink");
});

test("marks on more than one model-linked page name the real mistake instead of ambiguity", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const reference = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "target-frame" });
  const referenceCircle = element("ellipse", "reference-zone", { x: 1300, y: 100, frameId: "reference-frame" });
  const elements = [target, reference, circle, referenceCircle];
  assert.throws(() => geometry.createBoardFeedback(elements, Object.fromEntries(elements.map((item) => [item.id, true])),
    [targetDocument, linkedReferenceDocument]), (cause: unknown) =>
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_REFERENCE_MARKED");
});

test("a connector chained onto another connector never stands in for the edit drawing", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const first = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  const second = element("image", "second", { x: 2000, width: 600, height: 600, frameId: "second-frame",
    customData: { sourceDocument: secondReferenceSource } });
  const circle = element("ellipse", "zone", { x: 100, y: 100, width: 160, height: 120, frameId: "target-frame" });
  const toFirst = element("arrow", "link-first", { x: 240, y: 160, points: [[0, 0], [1000, 200]],
    startBinding: { elementId: "zone" }, endBinding: { elementId: "reference" } });
  const chained = element("arrow", "chained", { x: 900, y: 300, points: [[0, 0], [1100, 100]],
    startBinding: { elementId: "link-first" }, endBinding: { elementId: "second" } });
  const elements = [target, first, second, circle, toFirst, chained];
  assert.throws(() => geometry.createBoardFeedback(elements, Object.fromEntries(elements.map((item) => [item.id, true])),
    [targetDocument, linkedReferenceDocument, secondReferenceDocument]), (cause: unknown) =>
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_REFERENCE_UNLINKED");
});

test("two model-linked pages without an explicit framed target mark remain ambiguous", async (t) => {
  const geometry = await geometryModule(t);
  const target = element("image", "target", { width: 1000, height: 500, frameId: "target-frame" });
  const reference = element("image", "reference", { x: 1200, width: 600, height: 600, frameId: "reference-frame" });
  assert.throws(() => geometry.createBoardFeedback([target, reference], { target: true, reference: true },
    [targetDocument, linkedReferenceDocument]), (cause: unknown) =>
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_SOURCE_AMBIGUOUS");
});

async function handoffHarness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false, logLevel: "silent",
    server: { middlewareMode: true, watch: null },
    plugins: [{ name: "fixed-document-renderer", enforce: "pre",
      resolveId(id, importer) {
        if (id === "../monkeydiagram/documentVisualInput" && importer?.endsWith("boardFeedback.ts")) return "\0fixed-document-renderer";
      },
      load(id) {
        if (id === "\0fixed-document-renderer") return `export async function renderDocumentVisual(file,page,annotations) {
          return {pagePngBase64:JSON.stringify({name:file.name,type:file.type,page}),annotatedPngBase64:annotations.length?JSON.stringify(annotations):null,width:page.width,height:page.height};
        }`;
      },
    }],
  });
  t.after(() => vite.close());
  const module = await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardFeedback.ts") as typeof import("../src/workspaces/monkeyboard/boardFeedback.ts");
  const { createStudioClient } = await vite.ssrLoadModule("/src/api/client.ts") as typeof import("../src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection(""));
  const calls: { method: string; args: unknown[] }[] = [];
  const snapshots = new Map<string, DocumentAnnotationsDto>([
    [targetDocument.runId, { projectId: "project", runId: targetDocument.runId, assetSha256: targetDocument.assetSha256,
      pageIndex: 0, drawingRevisionRef: targetDocument.revisionRef, revisionSha256: "target-before", comment: "",
      annotations: [] }],
    [linkedReferenceDocument.runId, { projectId: "project", runId: linkedReferenceDocument.runId, assetSha256: linkedReferenceDocument.assetSha256,
      pageIndex: 0, drawingRevisionRef: linkedReferenceDocument.revisionRef, revisionSha256: "reference-rev", comment: "",
      annotations: [] }],
  ]);
  studio.documents = async () => ({ projectId: "project", runId: null,
    documents: [structuredClone(targetDocument), structuredClone(linkedReferenceDocument)] });
  studio.state = async (...args) => { calls.push({ method: "state", args }); return {
    projectId: "project", referenceRun: { runId: model.runId }, stateDigest: model.stateDigest, sourceStageRef: "stage-1",
  } as Awaited<ReturnType<typeof studio.state>>; };
  studio.documentFile = async (...args) => { calls.push({ method: "file", args });
    const runId = String(args[0]); const doc = runId === targetDocument.runId ? targetDocument : linkedReferenceDocument;
    return new File([runId], doc.fileName, { type: "application/octet-stream" }); };
  studio.documentAnnotations = async (...args) => { calls.push({ method: "read", args });
    return structuredClone(snapshots.get(String(args[0]))!); };
  studio.saveDocumentAnnotations = async (body) => { calls.push({ method: "write", args: [structuredClone(body)] });
    const saved = { ...body, revisionSha256: "target-saved" } as DocumentAnnotationsDto;
    snapshots.set(targetDocument.runId, saved); return structuredClone(saved); };
  return { ...module, studio, calls };
}

test("concept references travel as reference visuals while only the exact target page is saved and model-bound", async (t) => {
  const h = await handoffHarness(t);
  const referenceNote = "Explicit Board connector: use the facade as visual precedent only.";
  const selection: BoardFeedbackSelection = {
    image: {} as BoardFeedbackSelection["image"], document: structuredClone(targetDocument), page: { ...targetPage }, source: { ...targetSource },
    annotations: [], annotationGroups: [], selectedText: "让这个区域的立面参考右边这张图",
    references: [{ image: {} as NonNullable<BoardFeedbackSelection["references"]>[number]["image"],
      document: structuredClone(linkedReferenceDocument), page: { ...referencePage }, source: { ...referenceSource }, referenceNote }],
  };
  const request = await h.prepareBoardDesignRequest(h.studio, selection, "project", selection.selectedText);

  assert.equal(request.documentAnnotations.length, 1, "reference pages do not become model-bound edit sources");
  assert.equal(request.documentAnnotations[0].runId, targetDocument.runId);
  assert.deepEqual(request.documentVisuals.map((visual) => visual.role), ["edit", "reference"]);
  assert.equal(request.documentVisuals[1].runId, linkedReferenceDocument.runId);
  assert.equal(request.documentVisuals[1].referenceNote, referenceNote);
  assert.equal(request.documentVisuals[1].revisionSha256, "reference-rev");
  assert.equal(h.calls.filter((call) => call.method === "write").length, 1, "only target annotations are written");
  assert.equal(h.calls.filter((call) => call.method === "state").length, 1, "the target's declared model remains the sole editing base");
});

test("a reference page that changed since selection names the reference and writes no annotation", async (t) => {
  const h = await handoffHarness(t);
  const selection: BoardFeedbackSelection = {
    image: {} as BoardFeedbackSelection["image"], document: structuredClone(targetDocument), page: { ...targetPage }, source: { ...targetSource },
    annotations: [], annotationGroups: [], selectedText: "",
    references: [{ image: {} as NonNullable<BoardFeedbackSelection["references"]>[number]["image"],
      document: structuredClone(linkedReferenceDocument), page: { ...referencePage, width: 900 },
      source: { ...referenceSource }, referenceNote: "note" }],
  };
  await assert.rejects(() => h.prepareBoardDesignRequest(h.studio, selection, "project", "把这段立面往后退"),
    (cause: unknown) => cause instanceof h.BoardFeedbackError && cause.code === "REFERENCE_CHANGED");
  assert.equal(h.calls.filter((call) => call.method === "write").length, 0, "a stale reference never writes the edit page");
});
