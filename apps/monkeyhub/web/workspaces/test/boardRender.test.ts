import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer, type ViteDevServer } from "vite";
import type { SourceDocumentDto, SourceDocumentListDto } from "../src/api/generated/index.ts";

// Pure Board-side checks for handing a render discussion to the Hub (#253). The
// studio client is a local stand-in that answers one document listing; nothing
// is saved, sent or rendered.
type Module = typeof import("../src/workspaces/monkeyboard/boardRender.ts");
let render: Module;
let vite: ViteDevServer;
before(async () => {
  vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  render = await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardRender.ts") as Module;
});
after(async () => { await vite?.close(); });

function document(overrides: Partial<SourceDocumentDto> = {}): SourceDocumentDto {
  return { projectId: "project-a", runId: "studio-documents", assetSha256: "a".repeat(64), fileName: "Courtyard-A.png",
    mimeType: "image/png", sizeBytes: 1200, pageCount: 1, pages: [{ pageIndex: 0, width: 1600, height: 1094, rotation: 0 }],
    revisionRef: null, modelSource: null, ...overrides };
}
const courtyard = document();
const result = document({ assetSha256: "b".repeat(64), fileName: "AI-Result.jpg", mimeType: "image/jpeg" });
const material = document({ assetSha256: "c".repeat(64), fileName: "Material.png" });
const plan = document({ assetSha256: "d".repeat(64), fileName: "Plan.pdf", mimeType: "application/pdf", pageCount: 2,
  pages: [{ pageIndex: 0, width: 800, height: 600, rotation: 0 }, { pageIndex: 1, width: 800, height: 600, rotation: 0 }] });
// Same file name and bytes as the courtyard, registered again in another run: a different page.
const twin = document({ runId: "render-" + "e".repeat(32) });
const page = (value: SourceDocumentDto, pageIndex = 0) => ({ runId: value.runId, assetSha256: value.assetSha256,
  revisionRef: value.revisionRef ?? null, pageIndex });

function image(id: string, value: SourceDocumentDto, changes: Record<string, unknown> = {}): Record<string, unknown> {
  return { type: "image", id, x: 0, y: 0, width: 400, height: 300, isDeleted: false, frameId: null,
    customData: { sourceDocument: page(value) }, ...changes };
}

function listing(documents: SourceDocumentDto[], projectId = "project-a") {
  const reads: number[] = [];
  const studio = { documents: async (): Promise<SourceDocumentListDto> => { reads.push(1);
    return { projectId, runId: null, documents: structuredClone(documents) }; } };
  return { studio, reads };
}

test("a selection names registered image pages by their exact source, never by position or file name", () => {
  const documents = [courtyard, result, material, plan, twin];
  const elements = [
    image("far", courtyard, { x: 9000, y: -4000 }),
    image("near", twin, { x: 10, y: 10 }),
    image("drawing", plan),
    { type: "frame", id: "frame-1", isDeleted: false },
    image("framed", result, { frameId: "frame-1" }),
    image("unregistered", document({ assetSha256: "f".repeat(64) })),
    { type: "ellipse", id: "mark", isDeleted: false },
  ];
  const chosen = render.selectedRenderPages(elements, { far: true, near: true, drawing: true, "frame-1": true, unregistered: true, mark: true }, documents);
  assert.deepEqual(chosen, [page(courtyard), page(twin), page(result)],
    "a PDF page, an unregistered image and a mark are not render images; two same-named files stay two pages");
  // Moving an image does not change what it names.
  const moved = elements.map((element) => element.id === "far" ? { ...element, x: 0, y: 0 } : element);
  assert.deepEqual(render.selectedRenderPages(moved, { far: true }, documents), [page(courtyard)]);
  assert.deepEqual(render.selectedRenderPages(elements, { mark: true }, documents), [], "a mark alone names no image");
  // The same page placed twice is one page.
  assert.deepEqual(render.selectedRenderPages([...elements, image("copy", courtyard)], { far: true, copy: true }, documents), [page(courtyard)]);
});

test("a plain PNG or JPEG with no model is a render image; a PDF page is not", () => {
  assert.equal(courtyard.modelSource, null);
  assert.equal(render.isRenderImage(courtyard), true);
  assert.equal(render.isRenderImage(result), true);
  assert.equal(render.isRenderImage(plan), false);
});

test("roles need one source and at most three distinct references other than it", () => {
  assert.throws(() => render.checkRenderRoles(null, []), (error: { code?: string }) => error.code === "SOURCE_REQUIRED");
  assert.throws(() => render.checkRenderRoles(page(courtyard), [page(courtyard)]), (error: { code?: string }) => error.code === "DUPLICATE");
  assert.throws(() => render.checkRenderRoles(page(courtyard), [page(result), page(result)]), (error: { code?: string }) => error.code === "DUPLICATE");
  const four = [page(result), page(material), page(twin), page(document({ assetSha256: "9".repeat(64) }))];
  assert.throws(() => render.checkRenderRoles(page(courtyard), four), (error: { code?: string }) => error.code === "TOO_MANY_REFERENCES");
  assert.doesNotThrow(() => render.checkRenderRoles(page(courtyard), four.slice(0, 3)));
  assert.equal(render.RENDER_REFERENCE_LIMIT, 3);
});

test("reference choices are registered image pages not yet chosen and not replaced", () => {
  const updated = document({ assetSha256: "7".repeat(64), fileName: "Material v2.png",
    replacesPages: [{ ...page(material), newPageIndex: 0 }] });
  const choices = render.renderReferenceChoices([courtyard, result, material, plan, updated], [page(courtyard)]);
  assert.deepEqual(choices, [page(result), page(updated)], "the chosen source, a PDF page and a replaced page are not offered");
});

test("confirmation re-reads the registered documents and hands over exactly the chosen pages", async () => {
  const { studio, reads } = listing([courtyard, result, material]);
  const request = await render.prepareBoardRenderChatRequest(studio, "project-a", "  参考右边这张的材料感觉，屋顶和视角别动。 ",
    page(courtyard), [page(result)]);
  assert.equal(reads.length, 1, "the documents are read again at confirmation");
  assert.deepEqual(request, { projectId: "project-a", content: "参考右边这张的材料感觉，屋顶和视角别动。",
    source: page(courtyard), references: [page(result)] });
  const bare = await render.prepareBoardRenderChatRequest(studio, "project-a", "Keep the roof.", page(courtyard), []);
  assert.deepEqual(bare.references, [], "references are optional");
});

test("a changed project, a stale or missing revision and an unsupported page are refused", async () => {
  const reject = async (documents: SourceDocumentDto[], source: ReturnType<typeof page>, references: ReturnType<typeof page>[], code: string, projectId = "project-a") => {
    const { studio } = listing(documents, projectId);
    await assert.rejects(render.prepareBoardRenderChatRequest(studio, "project-a", "Warmer concrete.", source, references),
      (error: { code?: string }) => error.code === code, code);
  };
  await reject([courtyard, result], page(courtyard), [page(result)], "PROJECT_CHANGED", "project-b");
  await reject([result], page(courtyard), [], "SOURCE_CHANGED");
  await reject([courtyard, result], { ...page(courtyard), revisionRef: "project://project-a/runs/r/records/other.json" }, [], "SOURCE_CHANGED");
  await reject([courtyard], page(courtyard), [page(result)], "REFERENCE_CHANGED");
  const replacement = document({ assetSha256: "8".repeat(64), fileName: "Courtyard-A v2.png", replacesPages: [{ ...page(courtyard), newPageIndex: 0 }] });
  await reject([courtyard, replacement, result], page(courtyard), [page(result)], "SOURCE_CHANGED");
  await reject([courtyard, plan], page(courtyard), [page(plan)], "UNSUPPORTED");
  await reject([courtyard, { ...result, projectId: "project-b" }], page(courtyard), [page(result)], "REFERENCE_CHANGED");
  const { studio } = listing([courtyard]);
  await assert.rejects(render.prepareBoardRenderChatRequest(studio, "project-a", "   ", page(courtyard), []),
    (error: { code?: string }) => error.code === "EMPTY");
});

test("handing over asks the project only for its documents, then gives the host exactly the checked request", async () => {
  const calls: string[] = [];
  const studio = new Proxy({}, { get: (_target, name) => {
    calls.push(String(name));
    if (name === "documents") return async () => ({ projectId: "project-a", runId: null, documents: [courtyard, result] });
    return async () => { throw new Error(`unexpected ${String(name)}`); };
  } }) as Parameters<Module["handOverBoardRender"]>[0];
  const taken: unknown[] = [];
  const request = await render.handOverBoardRender(studio, "project-a", " Warmer concrete. ", page(courtyard), [page(result)],
    (value) => { taken.push(structuredClone(value)); });
  assert.deepEqual(calls, ["documents"], "no board save, annotation write or other project request");
  assert.deepEqual(taken, [{ projectId: "project-a", content: "Warmer concrete.", source: page(courtyard), references: [page(result)] }],
    "the host takes the request once, as checked");
  assert.deepEqual(request, taken[0]);
});

test("a host that cannot take the request refuses it back to the Board, and an unchecked request never reaches it", async () => {
  const { studio } = listing([courtyard, result]);
  const offered: unknown[] = [];
  await assert.rejects(render.handOverBoardRender(studio, "project-a", "Warmer concrete.", page(courtyard), [page(result)], (value) => {
    offered.push(value);
    throw new render.BoardRenderError("CONVERSATION_UNAVAILABLE", "This project's conversation is not the one open.");
  }), (error: { code?: string }) => error.code === "CONVERSATION_UNAVAILABLE",
  "the refusal reaches the dialog, which stays open with it");
  assert.equal(offered.length, 1);
  await assert.rejects(render.handOverBoardRender(studio, "project-a", "Warmer concrete.", page(material), [],
    () => assert.fail("a page that failed its check is not offered")), (error: { code?: string }) => error.code === "SOURCE_CHANGED");
  await assert.rejects(render.handOverBoardRender(studio, "project-a", "  ", page(courtyard), [],
    () => assert.fail("empty words are not offered")), (error: { code?: string }) => error.code === "EMPTY");
});
