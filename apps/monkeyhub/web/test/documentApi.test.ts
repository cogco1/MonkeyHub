import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";
import type {
  DocumentAnnotationsRequestDto,
  SourceDocumentDto,
} from "../src/api/generated/index.ts";

const projectId = "project-a";
const runId = "candidate-a";
const assetSha256 = "a".repeat(64);

async function documentApi(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://studio.test/" },
    btoa: globalThis.btoa,
  } });
  t.after(() => {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  });
  const { createStudioClient, StudioApiError } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection("http://studio.test", "document-test-token"));
  return { studio, StudioApiError };
}

test("documents use the selected run and preserve original upload and download bytes", async (t) => {
  const { studio } = await documentApi(t);
  const bytes = Uint8Array.from({ length: 65_553 }, (_, index) => index % 256);
  const file = new File([bytes], "参考图.png", { type: "image/png" });
  const document: SourceDocumentDto = {
    projectId, runId, assetSha256, fileName: file.name, mimeType: "image/png",
    sizeBytes: bytes.length, pageCount: 1,
    pages: [{ pageIndex: 0, width: 320, height: 240, rotation: 0 }],
  };
  const list = { projectId, runId, documents: [document] };
  const comments = { comments: [] };
  const requests: { path: string; method: string; body: unknown }[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.equal(url.origin, "http://studio.test");
    assert.equal(request.headers.get("authorization"), "Bearer document-test-token");
    requests.push({
      path: url.pathname + url.search,
      method: request.method,
      body: request.method === "POST" ? await request.json() : null,
    });
    if (url.pathname.endsWith("/bytes")) {
      return new Response(bytes, { headers: { "Content-Type": "image/png" } });
    }
    return Response.json(request.method === "POST" ? document
      : url.pathname === "/api/documents" ? list : comments);
  });

  assert.deepEqual(await studio.documents(runId), list);
  assert.deepEqual(await studio.uploadDocument(projectId, runId, file), document);
  const downloaded = await studio.documentFile(runId, assetSha256, file.name);
  assert.ok(downloaded instanceof File);
  assert.equal(downloaded.name, file.name);
  assert.equal(downloaded.type, "image/png");
  assert.deepEqual(new Uint8Array(await downloaded.arrayBuffer()), bytes);
  assert.deepEqual(await studio.documentComments(runId), comments);
  assert.deepEqual(requests.map(({ method, path }) => `${method} ${path}`), [
    `GET /api/documents?runId=${runId}`,
    "POST /api/documents",
    `GET /api/documents/${assetSha256}/bytes?runId=${runId}`,
    `GET /api/document-comments?runId=${runId}`,
  ]);
  assert.deepEqual(requests[1].body, {
    projectId, runId, fileName: file.name, mimeType: file.type,
    contentBase64: Buffer.from(bytes).toString("base64"),
  });
});

test("page revisions and annotation ratios pass through unchanged, including an empty page", async (t) => {
  const { studio } = await documentApi(t);
  const revisionSha256 = "b".repeat(64);
  const body: DocumentAnnotationsRequestDto = {
    projectId, runId, assetSha256, pageIndex: 2, baseRevisionSha256: revisionSha256,
    annotations: [{
      id: "stroke-1", kind: "arrow", points: [[0.125, 0.87654321], [0.987654, 0.333333]],
      color: "#ff3300", lineWidth: 0.0035, label: "保持原比例",
    }],
    comment: "降低左柜，保留右柜。",
  };
  const saved = {
    projectId, runId, assetSha256, pageIndex: 2, revisionSha256,
    annotations: body.annotations, comment: body.comment,
  };
  const requests: { query: Record<string, string>; body: unknown }[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    assert.equal(url.pathname, "/api/document-annotations");
    requests.push({
      query: Object.fromEntries(url.searchParams),
      body: request.method === "PUT" ? await request.json() : null,
    });
    return Response.json(saved);
  });

  assert.deepEqual(await studio.documentAnnotations(runId, assetSha256, 2), saved);
  assert.deepEqual(await studio.documentAnnotations(runId, assetSha256, 2, revisionSha256), saved);
  assert.deepEqual(await studio.saveDocumentAnnotations(body), saved);
  const emptyPage = { ...body, pageIndex: 0, baseRevisionSha256: null, annotations: [] };
  await studio.saveDocumentAnnotations(emptyPage);
  assert.deepEqual(requests, [
    { query: { runId, assetSha256, pageIndex: "2" }, body: null },
    { query: { runId, assetSha256, pageIndex: "2", revisionSha256 }, body: null },
    { query: {}, body },
    { query: {}, body: emptyPage },
  ]);
});

test("document authorization and revision conflicts remain StudioApiError failures", async (t) => {
  const { studio, StudioApiError } = await documentApi(t);
  const body: DocumentAnnotationsRequestDto = {
    projectId, runId, assetSha256, pageIndex: 0,
    baseRevisionSha256: "b".repeat(64), annotations: [],
  };
  let status = 401;
  let errorBody = { code: "UNAUTHENTICATED", detail: "A bearer token is required." };
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return Response.json(errorBody, { status });
  });
  const isExpectedError = (error: unknown) => {
    assert.ok(error instanceof StudioApiError);
    assert.equal(error.status, status);
    assert.equal(error.code, errorBody.code);
    assert.equal(error.detail, errorBody.detail);
    return true;
  };
  await assert.rejects(studio.documents(runId), isExpectedError);
  await assert.rejects(studio.documentFile(runId, assetSha256, "drawing.pdf"), isExpectedError);
  status = 409;
  errorBody = { code: "DOCUMENT_REVISION_CONFLICT", detail: "Read the latest revision before saving." };
  await assert.rejects(studio.saveDocumentAnnotations(body), isExpectedError);
  assert.equal(calls, 3, "a refusal is not retried or converted to an empty result");
});


test("external model import sends original bytes without inventing a semantic run/state", async (t) => {
  const { studio } = await documentApi(t);
  const bytes = Uint8Array.from({ length: 65_553 }, (_, index) => index % 256);
  const files = [new File([bytes], "建筑.3dm"), new File([bytes], "建筑.skp")];
  const stateDigest = "c".repeat(64);
  const artifact = { runId, sha256: assetSha256, modelSource: { runId, stateDigest, assetSha256 } };
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    assert.equal(request.url, "http://studio.test/api/model-assets");
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("authorization"), "Bearer document-test-token");
    const body = await request.json();
    assert.equal(body.projectId, projectId);
    assert.ok(files.some(file => file.name === body.fileName));
    assert.equal(body.contentBase64, Buffer.from(bytes).toString("base64"));
    return Response.json(artifact, { status: 201 });
  });
  for (const file of files) assert.deepEqual(await studio.uploadModel(projectId, file), artifact);
});

test("invalid or cancelled local model imports never send a registration request", async (t) => {
  const { studio } = await documentApi(t);
  const fetch = t.mock.method(globalThis, "fetch", async () => { throw new Error("unexpected upload"); });
  await assert.rejects(studio.uploadModel(projectId, new File(["x"], "model.obj")), /3dm or SketchUp \.skp/);
  await assert.rejects(studio.uploadModel(projectId, new File([], "empty.3dm")), /non-empty/);
  const tooLarge = new File(["x"], "large.3dm");
  Object.defineProperty(tooLarge, "size", { value: 128 * 1024 * 1024 + 1 });
  await assert.rejects(studio.uploadModel(projectId, tooLarge), /128 MiB/);
  const controller = new AbortController();
  const file = new File(["source"], "model.3dm");
  t.mock.method(file, "arrayBuffer", async () => { controller.abort(); return new ArrayBuffer(6); });
  await assert.rejects(studio.uploadModel(projectId, file, controller.signal), { name: "AbortError" });
  assert.equal(fetch.mock.callCount(), 0);
});
