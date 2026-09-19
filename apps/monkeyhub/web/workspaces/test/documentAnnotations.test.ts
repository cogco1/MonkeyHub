import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import { createServer } from "vite";
import type { DocumentAnnotationsDto, DocumentAnnotationsRequestDto, DocumentGestureDto } from "../src/api/generated/index.ts";
import type { DocumentAnnotationsOptions } from "../src/workspaces/monkeydiagram/useDocumentAnnotations.ts";

const scope = { projectId: "project-a", runId: "run-a", assetSha256: "a".repeat(64), pageIndex: 0 };
const revision = (index: number) => String(index).repeat(64);
const ink = (id: string): DocumentGestureDto => ({
  id, kind: "line", points: [[0.123456, 0.25], [0.875, 0.765432]], color: "#ff3300", lineWidth: 0.004,
});
const reply = (page: DocumentAnnotationsOptions, annotations: readonly DocumentGestureDto[] = [], comment = "", sha = revision(0)): DocumentAnnotationsDto => ({
  projectId: page.projectId, runId: page.runId, assetSha256: page.assetSha256!, pageIndex: page.pageIndex,
  revisionSha256: sha, annotations: structuredClone([...annotations]), comment,
});
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

test("calibration and corrected contours share page revision, undo, retry and cold reopen", async t => {
  const h = await harness(t);
  const contour: DocumentGestureDto = { ...ink("footprint"), kind: "polyline", closed: true,
    points: [[0.1, 0.2], [0.7, 0.2], [0.5, 0.8]] };
  const calibration = { origin: [0.1, 0.8] as [number, number], axisPoint: [0.7, 0.8] as [number, number], distance: 6 };
  let stored = reply(scope, [contour]);
  let writes = 0, fail = true;
  t.mock.method(h.studio, "documentAnnotations", async () => structuredClone(stored));
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    if (fail) throw new Error("offline");
    assert.equal(body.baseRevisionSha256, stored.revisionSha256);
    stored = { ...reply(scope, body.annotations, body.comment, revision(++writes)), tracingCalibration: body.tracingCalibration };
    return structuredClone(stored);
  });
  const page = h.controller.page(scope);
  await page.load();
  page.setTracingCalibration(calibration);
  await assert.rejects(page.save());
  assert.deepEqual(page.getSnapshot().tracingCalibration, calibration);
  assert.deepEqual(page.getSnapshot().annotations, [contour]);
  fail = false;
  await page.save();
  const corrected = { ...contour, points: [[0.1, 0.2], [0.8, 0.2], [0.5, 0.8]] };
  page.changeAnnotations([corrected]);
  await page.save();
  page.undo(); await page.save();
  assert.deepEqual(stored.annotations, [contour]);
  assert.deepEqual(stored.tracingCalibration, calibration);
  page.undo(); await page.save();
  assert.equal(stored.tracingCalibration, null);
  page.redo(); await page.save();
  const ref = await page.save();
  const cold = h.createController().page(scope);
  await cold.load();
  assert.deepEqual(cold.getSnapshot().tracingCalibration, calibration);
  assert.deepEqual(cold.getSnapshot().annotations, [contour]);
  assert.deepEqual(await cold.save(), ref);
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createDocumentAnnotationsController } = await vite.ssrLoadModule("/src/workspaces/monkeydiagram/useDocumentAnnotations.ts");
  const { createStudioClient, StudioApiError, NETWORK_ERROR } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection("http://studio.test"));
  const createController = () => createDocumentAnnotationsController(studio);
  return { controller: createController(), createController, studio, StudioApiError, NETWORK_ERROR };
}

test("strokes, erase and undo/redo save serial snapshots without blocking input or changing a captured ref", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "documentAnnotations", async () => reply(scope));
  const requests: DocumentAnnotationsRequestDto[] = [];
  const responses: ReturnType<typeof deferred<DocumentAnnotationsDto>>[] = [];
  t.mock.method(h.studio, "saveDocumentAnnotations", (body: DocumentAnnotationsRequestDto) => {
    requests.push(structuredClone(body));
    const pending = deferred<DocumentAnnotationsDto>();
    responses.push(pending);
    return pending.promise;
  });
  const page = h.controller.page(scope);
  await page.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  page.changeAnnotations([a]);
  const firstRef = page.save();
  page.changeAnnotations([a, b]);
  page.changeAnnotations([]);
  page.undo();
  assert.deepEqual(page.getSnapshot().annotations, [a, b]);
  page.redo();
  page.undo();
  page.changeAnnotations([a, c]);
  assert.equal(page.getSnapshot().canRedo, false, "a new gesture discards the undone future");
  const finalRef = page.save();
  await flush();
  assert.equal(requests.length, 1, "only the first PUT is in flight while input continues");
  assert.deepEqual(page.getSnapshot().annotations, [a, c]);
  assert.equal(page.getSnapshot().dirty, true);
  const expected = [[a], [a, b], [], [a, b], [], [a, b], [a, c]];
  for (const [index, annotations] of expected.entries()) {
    assert.equal(requests.length, index + 1);
    assert.equal(requests[index].baseRevisionSha256, revision(index));
    assert.deepEqual(requests[index].annotations, annotations);
    responses[index].resolve(reply(scope, annotations, "", revision(index + 1)));
    await flush();
    if (index === 0) {
      assert.deepEqual(await firstRef, {
        runId: scope.runId, assetSha256: scope.assetSha256, pageIndex: 0, revisionSha256: revision(1),
      });
      assert.deepEqual(page.getSnapshot().annotations, [a, c], "an earlier ACK never replaces newer local ink");
    }
  }
  assert.equal((await finalRef).revisionSha256, revision(7));
  assert.equal(page.getSnapshot().dirty, false);
  assert.equal(page.getSnapshot().saving, false);
});

test("late loads and saves remain bound to their page, source and run, with each page history retained", async (t) => {
  const h = await harness(t);
  const lateRead = deferred<DocumentAnnotationsDto>();
  t.mock.method(h.studio, "documentAnnotations", async (runId: string, assetSha256: string, pageIndex: number) => {
    if (runId === scope.runId && assetSha256 === scope.assetSha256 && pageIndex === 0) return lateRead.promise;
    return reply({ ...scope, runId, assetSha256, pageIndex }, [ink(`${runId}-${assetSha256[0]}-${pageIndex}`)]);
  });
  const writes: DocumentAnnotationsRequestDto[] = [];
  const lateWrite = deferred<DocumentAnnotationsDto>();
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    writes.push(structuredClone(body));
    if (writes.length === 1) return lateWrite.promise;
    return reply(body, body.annotations, body.comment, revision(writes.length));
  });
  const pageA = h.controller.page(scope);
  const loadingA = pageA.load();
  const pageB = h.controller.page({ ...scope, pageIndex: 1 });
  const otherRun = h.controller.page({ ...scope, runId: "run-b" });
  const otherSource = h.controller.page({ ...scope, assetSha256: "b".repeat(64) });
  await Promise.all([pageB.load(), otherRun.load(), otherSource.load()]);
  const before = [pageB, otherRun, otherSource].map((page) => page.getSnapshot());
  lateRead.resolve(reply(scope, [ink("original")]));
  await loadingA;
  assert.deepEqual([pageB, otherRun, otherSource].map((page) => page.getSnapshot()), before);
  pageA.changeAnnotations([ink("local")]);
  const saved = pageA.save();
  await flush();
  lateWrite.resolve(reply(scope, [ink("local")], "", revision(1)));
  await saved;
  assert.deepEqual([pageB, otherRun, otherSource].map((page) => page.getSnapshot()), before);
  assert.equal(h.controller.page({ ...scope }), pageA);
  assert.equal(pageA.getSnapshot().canUndo, true);
  pageA.undo();
  await pageA.save();
  assert.deepEqual(pageA.getSnapshot().annotations, [ink("original")]);
  assert.equal(pageA.getSnapshot().canRedo, true);
  assert.ok(writes.every((body) => body.runId === scope.runId && body.assetSha256 === scope.assetSha256 && body.pageIndex === 0));
});

test("historical revisions load their exact ref and cannot change, undo or save", async (t) => {
  const h = await harness(t);
  const historicRevision = "f".repeat(64);
  const requested: (string | null | undefined)[] = [];
  t.mock.method(h.studio, "documentAnnotations", async (_run: string, _asset: string, _page: number, selected?: string | null) => {
    requested.push(selected);
    return reply(scope, [ink("historic")], "saved note", selected ?? revision(0));
  });
  const put = t.mock.method(h.studio, "saveDocumentAnnotations", async () => reply(scope));
  const historic = h.controller.page({ ...scope, revisionSha256: historicRevision });
  await historic.load();
  historic.changeAnnotations([ink("replacement")]);
  historic.setComment("replacement");
  historic.undo();
  historic.redo();
  assert.deepEqual(historic.getSnapshot().annotations, [ink("historic")]);
  assert.equal(historic.getSnapshot().comment, "saved note");
  assert.equal(historic.getSnapshot().readOnly, true);
  assert.equal(historic.getSnapshot().canUndo, false);
  await assert.rejects(historic.save(), { code: "UNSUPPORTED_REQUEST" });
  await historic.reload();
  assert.deepEqual(requested, [historicRevision, historicRevision]);
  const empty = h.controller.page({ ...scope, assetSha256: null });
  await empty.load();
  assert.equal(empty.getSnapshot().ready, false);
  await assert.rejects(empty.save(), { code: "UNSUPPORTED_REQUEST" });
  assert.equal(put.mock.callCount(), 0);
});

test("network failure pauses that page and an explicit save retries its retained snapshots in order", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "documentAnnotations", async () => reply(scope));
  const requests: DocumentAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    requests.push(structuredClone(body));
    if (requests.length === 1) throw new h.StudioApiError({ status: 0, code: h.NETWORK_ERROR, detail: "offline" });
    return reply(scope, body.annotations, body.comment, revision(requests.length - 1));
  });
  const page = h.controller.page(scope);
  await page.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  page.changeAnnotations([a]);
  page.changeAnnotations([a, b]);
  await flush();
  assert.equal(requests.length, 1);
  assert.equal(page.getSnapshot().error.code, h.NETWORK_ERROR);
  assert.equal(page.getSnapshot().saving, false);
  page.changeAnnotations([a, b, c]);
  await flush();
  assert.equal(requests.length, 1, "new ink does not silently restart a failed queue");
  const ref = await page.save();
  assert.equal(ref.revisionSha256, revision(3));
  assert.deepEqual(requests.map((body) => body.baseRevisionSha256), [revision(0), revision(0), revision(1), revision(2)]);
  assert.deepEqual(requests.map((body) => body.annotations), [[a], [a], [a, b], [a, b, c]]);
  assert.deepEqual(page.getSnapshot().annotations, [a, b, c]);
  assert.equal(page.getSnapshot().error, null);
});

test("a conflict cannot retry over a newer revision; explicit reload preserves the local present in Undo", async (t) => {
  const h = await harness(t);
  const remote = deferred<DocumentAnnotationsDto>();
  let reads = 0;
  t.mock.method(h.studio, "documentAnnotations", async () => ++reads === 1 ? reply(scope) : remote.promise);
  const requests: DocumentAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    requests.push(structuredClone(body));
    if (requests.length === 1) {
      throw new h.StudioApiError({ status: 409, code: "DOCUMENT_REVISION_CONFLICT", detail: "The page has a newer revision." });
    }
    return reply(scope, body.annotations, body.comment, revision(9));
  });
  const page = h.controller.page(scope);
  await page.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  page.changeAnnotations([a]);
  page.changeAnnotations([a, b]);
  await assert.rejects(page.save(), { status: 409 });
  await assert.rejects(page.save(), { status: 409 });
  assert.equal(requests.length, 1);
  assert.deepEqual(page.getSnapshot().annotations, [a, b]);
  const loading = page.reload();
  await flush();
  page.changeAnnotations([a, b, c]);
  page.setComment("my local note");
  remote.resolve(reply(scope, [ink("remote")], "another person's note", revision(8)));
  await loading;
  assert.equal(requests.length, 1, "reading latest never automatically writes the old draft over it");
  assert.deepEqual(page.getSnapshot().annotations, [ink("remote")]);
  assert.equal(page.getSnapshot().dirty, false);
  assert.equal(page.getSnapshot().error, null);
  page.undo();
  assert.deepEqual(page.getSnapshot().annotations, [a, b, c]);
  assert.equal(page.getSnapshot().comment, "my local note");
  assert.equal((await page.save()).revisionSha256, revision(9));
  assert.equal(requests[1].baseRevisionSha256, revision(8));
  assert.deepEqual(requests[1].annotations, [a, b, c]);
  assert.equal(requests[1].comment, "my local note");
});

test("text is debounced and save flushes the current comment before returning its exact revision", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "documentAnnotations", async () => reply(scope));
  const requests: DocumentAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    requests.push(structuredClone(body));
    return reply(scope, body.annotations, body.comment, revision(requests.length));
  });
  const page = h.controller.page(scope);
  await page.load();
  page.setComment("first");
  page.setComment("finished typing");
  assert.equal(requests.length, 0);
  assert.equal(page.getSnapshot().dirty, true);
  await delay(550);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].comment, "finished typing");
  page.setComment("submit this exact note");
  assert.equal((await page.save()).revisionSha256, revision(2));
  assert.equal(requests[1].comment, "submit this exact note");
  await delay(550);
  assert.equal(requests.length, 2, "save cancelled the pending text timer");
});

test("text creation, editing, moving and deletion each occupy one mixed-page history step", async (t) => {
  const h = await harness(t);
  const legacy = ink("existing-line");
  const created: DocumentGestureDto = {
    id: "note-1", kind: "text", points: [[0.125, 0.25]],
    label: "第一行\nSecond line", fontSize: 0.0275, color: "#184b82", lineWidth: 0.004,
  };
  const edited: DocumentGestureDto = { ...created, label: "修改第一行\nSecond line\n第三行", fontSize: 0.043 };
  const moved: DocumentGestureDto = { ...edited, points: [[0.6875, 0.375]] };
  const states = [[legacy], [legacy, created], [legacy, edited], [legacy, moved], [legacy]];
  let stored = reply(scope, [legacy], "page comment");
  const requests: DocumentAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "documentAnnotations", async () => structuredClone(stored));
  t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    assert.equal(body.baseRevisionSha256, stored.revisionSha256, "each snapshot uses the previous ACK's CAS revision");
    assert.deepEqual(body.annotations[0], legacy);
    assert.equal(Object.hasOwn(body.annotations[0], "fontSize"), false);
    assert.equal(body.comment, "page comment", "canvas text does not replace the page comment");
    requests.push(structuredClone(body));
    stored = reply(scope, body.annotations, body.comment, requests.length.toString(16).padStart(64, "0"));
    return structuredClone(stored);
  });
  const page = h.controller.page(scope);
  await page.load();
  for (const annotations of states.slice(1)) {
    page.changeAnnotations(annotations);
    const ref = await page.save();
    assert.deepEqual(page.getSnapshot().annotations, annotations);
    assert.equal(ref.revisionSha256, stored.revisionSha256);
  }
  assert.equal(requests.length, 4, "one complete snapshot per committed text operation");
  for (let index = states.length - 2; index >= 0; index -= 1) {
    page.undo();
    assert.deepEqual(page.getSnapshot().annotations, states[index]);
    await page.save();
  }
  assert.equal(page.getSnapshot().canUndo, false);
  for (const annotations of states.slice(1)) {
    page.redo();
    assert.deepEqual(page.getSnapshot().annotations, annotations);
    await page.save();
  }
  assert.equal(page.getSnapshot().canRedo, false);
  assert.deepEqual(requests.map((body) => body.annotations), [
    ...states.slice(1), ...states.slice(0, -1).reverse(), ...states.slice(1),
  ]);
});

test("a rebuilt controller reads multiline text, its font ratio and anchor without changing legacy ink", async (t) => {
  const h = await harness(t);
  const legacy = ink("legacy-line");
  const text: DocumentGestureDto = {
    id: "saved-note", kind: "text", points: [[0.123456789, 0.7654321]],
    label: "保留换行\nKeep this second line\n第三行。", fontSize: 0.0417, color: "#224466", lineWidth: 0.004,
  };
  let persisted = JSON.stringify(reply(scope, [legacy], "independent page comment"));
  const reads: unknown[][] = [];
  t.mock.method(h.studio, "documentAnnotations", async (...args: unknown[]) => {
    reads.push(args);
    return JSON.parse(persisted);
  });
  const put = t.mock.method(h.studio, "saveDocumentAnnotations", async (body: DocumentAnnotationsRequestDto) => {
    assert.equal(body.baseRevisionSha256, revision(0));
    assert.deepEqual(body.annotations, [legacy, text]);
    persisted = JSON.stringify(reply(scope, body.annotations, body.comment, revision(1)));
    return JSON.parse(persisted);
  });
  const original = h.controller.page(scope);
  await original.load();
  original.changeAnnotations([legacy, text]);
  const ref = await original.save();
  assert.deepEqual(ref, {
    runId: scope.runId, assetSha256: scope.assetSha256, pageIndex: 0, revisionSha256: revision(1),
  });
  const reopened = h.createController().page(scope);
  assert.notEqual(reopened, original);
  await reopened.load();
  assert.deepEqual(reads, [
    [scope.runId, scope.assetSha256, scope.pageIndex, undefined, undefined],
    [scope.runId, scope.assetSha256, scope.pageIndex, undefined, undefined],
  ]);
  assert.deepEqual(reopened.getSnapshot().annotations, [legacy, text]);
  assert.equal(Object.hasOwn(reopened.getSnapshot().annotations[0], "fontSize"), false);
  assert.equal(reopened.getSnapshot().comment, "independent page comment");
  assert.equal(reopened.getSnapshot().dirty, false);
  assert.equal(reopened.getSnapshot().canUndo, false, "reading a saved page does not invent local edit history");
  assert.deepEqual(await reopened.save(), ref, "the exact saved ref is reusable without another PUT");
  assert.equal(put.mock.callCount(), 1);
});
