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
const targetSource = { runId: targetDocument.runId, assetSha256: targetDocument.assetSha256,
  revisionRef: targetDocument.revisionRef!, pageIndex: 0 };
const referenceSource = { runId: referenceDocument.runId, assetSha256: referenceDocument.assetSha256,
  revisionRef: referenceDocument.revisionRef!, pageIndex: 0 };

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
      cause instanceof geometry.BoardFeedbackGeometryError && cause.code === "BOARD_FEEDBACK_UNSUPPORTED");
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
  const { studio } = await vite.ssrLoadModule("/src/api/client.ts") as typeof import("../src/api/client.ts");
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
  return { ...module, calls };
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
  const request = await h.prepareBoardDesignRequest(selection, "project", selection.selectedText);

  assert.equal(request.documentAnnotations.length, 1, "reference pages do not become model-bound edit sources");
  assert.equal(request.documentAnnotations[0].runId, targetDocument.runId);
  assert.deepEqual(request.documentVisuals.map((visual) => visual.role), ["edit", "reference"]);
  assert.equal(request.documentVisuals[1].runId, linkedReferenceDocument.runId);
  assert.equal(request.documentVisuals[1].referenceNote, referenceNote);
  assert.equal(request.documentVisuals[1].revisionSha256, "reference-rev");
  assert.equal(h.calls.filter((call) => call.method === "write").length, 1, "only target annotations are written");
  assert.equal(h.calls.filter((call) => call.method === "state").length, 1, "the target's declared model remains the sole editing base");
});
