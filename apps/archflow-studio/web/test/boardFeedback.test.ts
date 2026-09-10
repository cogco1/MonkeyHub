import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import type { DocumentAnnotationsDto, DocumentGestureDto, SourceDocumentDto } from "../src/api/generated/index.ts";
import type { BoardFeedbackSelection } from "../src/workspaces/monkeyboard/boardFeedbackGeometry.ts";

const model = { runId: "model-run", stateDigest: "s".repeat(64), assetSha256: "m".repeat(64) };
const page = { pageIndex: 1, width: 800, height: 1200, rotation: 90 };
const document: SourceDocumentDto = {
  projectId: "project-a", runId: "drawing-run", assetSha256: "d".repeat(64), fileName: "Elevation.pdf",
  mimeType: "application/pdf", sizeBytes: 12000, pageCount: 2, pages: [page], modelSource: model,
  modelSourceBindingRef: "binding-1", revisionRef: "project://project-a/runs/drawing-run/drawing-1",
  sourceStageRef: "stage-1",
};
const source = { runId: document.runId, assetSha256: document.assetSha256, revisionRef: document.revisionRef!, pageIndex: 1 };
const mark = (id: string): DocumentGestureDto => ({ id, kind: "line", points: [[0.1, 0.2], [0.4, 0.6]], color: "#ff0000", lineWidth: 0.002 });
const selection = (): BoardFeedbackSelection => ({
  image: {} as BoardFeedbackSelection["image"], document: structuredClone(document), page: { ...page }, source: { ...source },
  annotations: [mark("board:image:arrow:shaft")], annotationGroups: ["board:image:arrow"],
});

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false, logLevel: "silent",
    server: { middlewareMode: true, watch: null },
    plugins: [{ name: "fixed-document-renderer", enforce: "pre",
      resolveId(id, importer) {
        if (id === "../monkeydiagram/documentVisualInput" && importer?.endsWith("boardFeedback.ts")) return "\0fixed-document-renderer";
      },
      load(id) {
        if (id === "\0fixed-document-renderer") return `export async function renderDocumentVisual(file,page,annotations) {
          return {pagePngBase64:JSON.stringify({name:file.name,type:file.type,page}),annotatedPngBase64:JSON.stringify(annotations),width:800,height:1200};
        }`;
      },
    }],
  });
  t.after(() => vite.close());
  const module = await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardFeedback.ts") as typeof import("../src/workspaces/monkeyboard/boardFeedback.ts");
  const { studio } = await vite.ssrLoadModule("/src/api/client.ts") as typeof import("../src/api/client.ts");
  let listed = structuredClone(document);
  let current: DocumentAnnotationsDto = { projectId: "project-a", runId: source.runId, assetSha256: source.assetSha256,
    pageIndex: 1, drawingRevisionRef: source.revisionRef, revisionSha256: "before", comment: "Existing unfinished Diagram comment",
    annotations: [mark("diagram-note"), mark("board:image:arrow:start"), mark("board:image:arrow:shaft")],
  };
  const calls: { method: string; args: unknown[] }[] = [];
  studio.documents = async () => ({ projectId: "project-a", runId: null, documents: [listed] });
  studio.state = async (...args) => { calls.push({ method: "state", args }); return {
    projectId: "project-a", referenceRun: { runId: model.runId }, stateDigest: model.stateDigest, sourceStageRef: "stage-1",
  } as Awaited<ReturnType<typeof studio.state>>; };
  studio.documentFile = async (...args) => { calls.push({ method: "file", args }); return new File(["original"], document.fileName, { type: "application/octet-stream" }); };
  studio.documentAnnotations = async (...args) => { calls.push({ method: "read", args }); return structuredClone(current); };
  studio.saveDocumentAnnotations = async (body) => {
    calls.push({ method: "write", args: [structuredClone(body)] });
    current = { ...body, revisionSha256: "saved-exact" }; return structuredClone(current);
  };
  return { ...module, studio, calls, current: () => structuredClone(current), replaceDocument: (value: SourceDocumentDto) => { listed = value; } };
}

test("returning a board arrow replaces its previous group and preserves other page work", async (t) => {
  const h = await harness(t), before = h.current(), chosen = selection();
  const request = await h.prepareBoardDesignRequest(chosen, "project-a", "  set height to 2.4  ");
  assert.equal(h.current().comment, before.comment);
  assert.deepEqual(h.current().annotations, [before.annotations[0], ...chosen.annotations]);
  assert.deepEqual(h.calls.find((call) => call.method === "state")?.args, [model.runId, "stage-1"]);
  const saved = h.calls.find((call) => call.method === "write")?.args[0] as Record<string, unknown>;
  assert.equal(saved.baseRevisionSha256, "before"); assert.equal(saved.drawingRevisionRef, source.revisionRef);
  assert.equal(request.utterance, "set height to 2.4"); assert.deepEqual(request.modelSource, model);
  assert.equal(request.documentAnnotations[0].revisionSha256, "saved-exact");
  assert.equal(request.documentVisuals[0].revisionSha256, "saved-exact");
  assert.deepEqual(JSON.parse(request.documentVisuals[0].annotatedPngBase64!), h.current().annotations);
  assert.equal(JSON.parse(request.documentVisuals[0].pagePngBase64).type, "application/pdf");
  assert.deepEqual(before.annotations[1], mark("board:image:arrow:start"), "the prior record is not mutated");
});

test("a second send does not accumulate copies or overwrite an unrelated Diagram comment", async (t) => {
  const h = await harness(t), chosen = selection();
  await h.prepareBoardDesignRequest(chosen, "project-a", "first");
  await h.prepareBoardDesignRequest(chosen, "project-a", "second");
  assert.equal(h.current().annotations.length, 2);
  assert.equal(h.current().comment, "Existing unfinished Diagram comment");
  assert.equal((h.calls.filter((call) => call.method === "write")[1].args[0] as Record<string, unknown>).baseRevisionSha256, "saved-exact");
});

test("missing, rebound, mismatched or resized drawing sources fail before annotation writes", async (t) => {
  for (const changed of [
    { ...document, modelSource: null },
    { ...document, modelSource: { ...model, assetSha256: "other" } },
    { ...document, modelSourceBindingRef: "binding-2" },
    { ...document, revisionRef: "another-revision" },
    { ...document, pages: [{ ...page, rotation: 0 }] },
  ]) {
    const h = await harness(t); h.replaceDocument(changed);
    await assert.rejects(h.prepareBoardDesignRequest(selection(), "project-a", "change"));
    assert.ok(!h.calls.some((call) => call.method === "write"));
  }
});

test("a page save conflict keeps the current page and never produces an intent handoff", async (t) => {
  const h = await harness(t), original = h.current();
  const conflict = Object.assign(new Error("Another page save won"), { code: "ANNOTATION_STALE" });
  h.studio.saveDocumentAnnotations = async () => { throw conflict; };
  await assert.rejects(h.prepareBoardDesignRequest(selection(), "project-a", "change"), (cause) => cause === conflict);
  assert.deepEqual(h.current(), original);
});
