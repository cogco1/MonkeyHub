import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";
import type { ModelAnnotationsDto, ModelAnnotationsRequestDto, ModelGestureDto, ModelSourceDto } from "../src/api/generated/index.ts";
import type { ModelAnnotationsOptions } from "../src/workspaces/monkeyarch/useModelAnnotations.ts";

const source: ModelSourceDto = { runId: "run-a", stateDigest: "d".repeat(64), assetSha256: "a".repeat(64) };
const scope: ModelAnnotationsOptions = { projectId: "project-a", modelSource: source };
const revision = (index: number) => index.toString(16).padStart(64, "0");
const ink = (id: string): ModelGestureDto => ({
  id, kind: "arrow", screen: [[10, 20], [80, 50]], screenSize: [800, 600],
  camera: { position: [12, -8, 6], target: [3, 4, 2], up: [0, 0, 1], fov: 38 },
  hits: [{ objectName: "object-a", userStrings: { Element: "element-a" }, world: [30.5, 40.25, 5] }],
  worldStart: [3, 4, 5], worldEnd: [30.5, 40.25, 5], worldDirection: [0.6, 0.8, 0], lengthModelUnits: 45,
  color: "#ff3300", lineWidth: 4, label: "Keep this view",
});
const reply = (modelSource: ModelSourceDto = source, annotations: readonly ModelGestureDto[] = [], comment = "stored model comment", sha: string | null = revision(0)): ModelAnnotationsDto => ({
  projectId: scope.projectId, modelSource: structuredClone(modelSource), revisionSha256: sha,
  annotations: structuredClone([...annotations]), comment,
});
const flush = () => new Promise<void>((resolve) => setImmediate(resolve));
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function harness(t: TestContext) {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { createModelAnnotationsController } = await vite.ssrLoadModule("/src/workspaces/monkeyarch/useModelAnnotations.ts");
  const { createStudioClient, StudioApiError, NETWORK_ERROR } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection("http://studio.test"));
  const createController = () => createModelAnnotationsController(studio);
  return { controller: createController(), createController, studio, StudioApiError, NETWORK_ERROR };
}

test("a response for another model source cannot replace ink or become its CAS base", async (t) => {
  const h = await harness(t);
  let wrongRead = true;
  t.mock.method(h.studio, "modelAnnotations", async () => wrongRead
    ? reply({ ...source, assetSha256: "f".repeat(64) }, [ink("foreign")]) : reply());
  t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => ({
    ...reply(source, body.annotations, body.comment, revision(1)), projectId: "other-project",
  }));
  const model = h.controller.model(scope);
  await model.load();
  assert.equal(model.getSnapshot().ready, false);
  assert.deepEqual(model.getSnapshot().annotations, []);
  assert.equal(model.getSnapshot().error.code, "TRANSPORT_ERROR");
  wrongRead = false;
  await model.reload();
  model.changeAnnotations([ink("local")]);
  await assert.rejects(model.save(), /another model source/);
  assert.deepEqual(model.getSnapshot().annotations, [ink("local")]);
  assert.equal(model.getSnapshot().dirty, true);
});

test("server defaults in the saved gesture do not leave an acknowledged local draft dirty", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "modelAnnotations", async () => reply());
  let writes = 0;
  t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => {
    writes++;
    return reply(source, body.annotations.map((gesture) => ({ ...gesture, hits: gesture.hits ?? [], label: gesture.label ?? null })), body.comment, revision(1));
  });
  const model = h.controller.model(scope);
  await model.load();
  const { hits: _hits, label: _label, ...line } = ink("server-defaults");
  model.changeAnnotations([line]);
  await model.save();
  await flush();
  assert.equal(model.getSnapshot().dirty, false);
  assert.equal(model.getSnapshot().saving, false);
  await model.save();
  assert.equal(writes, 1, "an unchanged acknowledged page needs no second PUT");
});

test("3D strokes, erase and undo/redo save serial CAS snapshots while newer input remains live", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "modelAnnotations", async () => reply());
  const requests: ModelAnnotationsRequestDto[] = [];
  const pending: ReturnType<typeof deferred<ModelAnnotationsDto>>[] = [];
  t.mock.method(h.studio, "saveModelAnnotations", (body: ModelAnnotationsRequestDto) => {
    requests.push(structuredClone(body));
    const response = deferred<ModelAnnotationsDto>(); pending.push(response); return response.promise;
  });
  const model = h.controller.model(scope);
  await model.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  model.changeAnnotations([a]);
  const first = model.save();
  model.changeAnnotations([a, b]);
  model.changeAnnotations([]);
  model.undo();
  assert.deepEqual(model.getSnapshot().annotations, [a, b]);
  model.redo(); model.undo(); model.changeAnnotations([a, c]);
  assert.equal(model.getSnapshot().canRedo, false);
  const last = model.save();
  await flush();
  assert.equal(requests.length, 1, "only one PUT is in flight while local history changes");
  for (let index = 1; index <= 7; index++) {
    const body = requests[index - 1];
    assert.equal(body.baseRevisionSha256, revision(index - 1));
    assert.equal(body.projectId, scope.projectId);
    assert.deepEqual(body.modelSource, source);
    assert.equal(body.comment, "stored model comment", "ink changes preserve the server's existing comment");
    pending[index - 1].resolve(reply(source, body.annotations, body.comment, revision(index)));
    await flush();
    assert.deepEqual(model.getSnapshot().annotations, [a, c], "an earlier ACK never replaces the current draft");
  }
  assert.deepEqual((await first).annotations, [a]);
  assert.equal((await first).revisionSha256, revision(1));
  assert.deepEqual((await last).annotations, [a, c]);
  assert.equal((await last).revisionSha256, revision(7));
  assert.equal(model.getSnapshot().dirty, false);
  assert.equal(model.getSnapshot().saving, false);
});

test("late GET/PUT cannot cross run, state, asset or project histories", async (t) => {
  const h = await harness(t);
  const lateRead = deferred<ModelAnnotationsDto>();
  t.mock.method(h.studio, "modelAnnotations", async (modelSource: ModelSourceDto) => {
    if (JSON.stringify(modelSource) === JSON.stringify(source)) return lateRead.promise;
    return reply(modelSource, [ink(`${modelSource.runId}-${modelSource.stateDigest[0]}-${modelSource.assetSha256[0]}`)]);
  });
  const bodies: ModelAnnotationsRequestDto[] = [];
  const lateWrite = deferred<ModelAnnotationsDto>();
  t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => {
    bodies.push(structuredClone(body));
    return bodies.length === 1 ? lateWrite.promise : reply(body.modelSource, body.annotations, body.comment, revision(bodies.length));
  });
  const a = h.controller.model(scope);
  const loading = a.load();
  const others = [
    h.controller.model({ ...scope, modelSource: { ...source, runId: "run-b" } }),
    h.controller.model({ ...scope, modelSource: { ...source, stateDigest: "e".repeat(64) } }),
    h.controller.model({ ...scope, modelSource: { ...source, assetSha256: "b".repeat(64) } }),
  ];
  const otherProject = h.controller.model({ ...scope, projectId: "project-b" });
  assert.notEqual(otherProject, a);
  await Promise.all(others.map((model) => model.load()));
  const before = [...others, otherProject].map((model) => model.getSnapshot());
  lateRead.resolve(reply(source, [ink("original")])); await loading;
  assert.deepEqual([...others, otherProject].map((model) => model.getSnapshot()), before);
  a.changeAnnotations([ink("local")]); const saved = a.save(); await flush();
  lateWrite.resolve(reply(source, [ink("local")], "stored model comment", revision(1))); await saved;
  assert.deepEqual([...others, otherProject].map((model) => model.getSnapshot()), before);
  assert.equal(h.controller.model({ projectId: scope.projectId, modelSource: { ...source } }), a);
  a.undo(); await a.save();
  assert.deepEqual(a.getSnapshot().annotations, [ink("original")]);
  assert.equal(a.getSnapshot().canRedo, true);
  assert.ok(bodies.every((body) => body.projectId === scope.projectId && JSON.stringify(body.modelSource) === JSON.stringify(source)));
});

test("local and legacy models retain memory-only undo/redo without GET or PUT", async (t) => {
  const h = await harness(t);
  const get = t.mock.method(h.studio, "modelAnnotations", async () => reply());
  const put = t.mock.method(h.studio, "saveModelAnnotations", async () => reply());
  const localScope = { projectId: scope.projectId, modelSource: null };
  const local = h.controller.model(localScope);
  await local.load();
  assert.equal(local.getSnapshot().ready, true);
  assert.equal(local.getSnapshot().dirty, false);
  local.changeAnnotations([ink("local")]);
  assert.equal(local.getSnapshot().dirty, true);
  local.undo(); assert.deepEqual(local.getSnapshot().annotations, []); assert.equal(local.getSnapshot().dirty, false);
  local.redo(); assert.deepEqual(local.getSnapshot().annotations, [ink("local")]);
  await local.reload();
  assert.deepEqual(local.getSnapshot().annotations, [ink("local")], "reload cannot discard an unbound local draft");
  await assert.rejects(local.save(), { code: "UNSUPPORTED_REQUEST" });
  assert.equal(local.getSnapshot().saving, false);
  assert.equal(get.mock.callCount(), 0); assert.equal(put.mock.callCount(), 0);
  assert.equal(h.controller.model(localScope), local);
  assert.deepEqual(h.controller.model({ ...localScope, projectId: "other-project" }).getSnapshot().annotations, []);
  assert.deepEqual(h.createController().model(localScope).getSnapshot().annotations, [], "local marks are not presented as persisted across restart");
});

test("a new unbound file cannot undo into the previous file while bound and other-project drafts survive", async (t) => {
  const h = await harness(t);
  const get = t.mock.method(h.studio, "modelAnnotations", async () => reply(source, [ink("bound-original")]));
  const lateWrite = deferred<ModelAnnotationsDto>();
  const put = t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => lateWrite.promise);
  const bound = h.controller.model(scope); await bound.load();
  bound.changeAnnotations([ink("bound-wip")]);
  const saving = bound.save(); await flush();
  const boundBefore = bound.getSnapshot();
  const localScope = { projectId: scope.projectId, modelSource: null };
  const otherScope = { projectId: "other-project", modelSource: null };
  const other = h.controller.model(otherScope);
  other.changeAnnotations([ink("other-project-wip")]);
  const first = h.controller.model(localScope);
  first.changeAnnotations([ink("first-file")]);
  first.changeAnnotations([ink("first-file"), ink("first-file-later")]);
  first.undo();
  assert.equal(first.getSnapshot().canUndo, true); assert.equal(first.getSnapshot().canRedo, true);

  h.controller.resetUnbound(localScope.projectId);
  const second = h.controller.model(localScope);
  assert.deepEqual(second.getSnapshot().annotations, []);
  assert.equal(second.getSnapshot().canUndo, false); assert.equal(second.getSnapshot().canRedo, false);
  second.undo(); second.redo();
  assert.deepEqual(second.getSnapshot().annotations, [], "Neither direction may recover the previous file's camera or hits");
  const secondInk = { ...ink("second-file"), camera: { ...ink("second-file").camera, position: [90, 80, 70] },
    hits: [{ objectName: "second-file-object", userStrings: {}, world: [7, 8, 9] }] } as ModelGestureDto;
  second.changeAnnotations([secondInk]);
  second.undo(); assert.deepEqual(second.getSnapshot().annotations, []);
  second.redo(); assert.deepEqual(second.getSnapshot().annotations, [secondInk]);
  assert.equal(h.controller.model(scope), bound);
  assert.deepEqual(bound.getSnapshot(), boundBefore, "Replacing local history does not touch an in-flight bound save or its WIP");
  assert.equal(h.controller.model(otherScope), other);
  assert.deepEqual(other.getSnapshot().annotations, [ink("other-project-wip")]);
  assert.equal(get.mock.callCount(), 1); assert.equal(put.mock.callCount(), 1);
  lateWrite.resolve(reply(source, [ink("bound-wip")], "stored model comment", revision(1)));
  await saving; await flush();
  assert.deepEqual(bound.getSnapshot().annotations, [ink("bound-wip")]);
  assert.equal(bound.getSnapshot().canUndo, true);
  assert.equal(bound.getSnapshot().dirty, false);
});

test("network failure retains queued drafts and only explicit save retries them in order", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "modelAnnotations", async () => reply());
  const bodies: ModelAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => {
    bodies.push(structuredClone(body));
    if (bodies.length === 1) throw new h.StudioApiError({ status: 0, code: h.NETWORK_ERROR, detail: "offline" });
    return reply(source, body.annotations, body.comment, revision(bodies.length - 1));
  });
  const model = h.controller.model(scope); await model.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  model.changeAnnotations([a]); model.changeAnnotations([a, b]); await flush();
  assert.equal(model.getSnapshot().error.code, h.NETWORK_ERROR);
  assert.equal(model.getSnapshot().saving, false);
  model.changeAnnotations([a, b, c]); await flush();
  assert.equal(bodies.length, 1, "editing a failed draft does not silently retry its queue");
  assert.equal((await model.save()).revisionSha256, revision(3));
  assert.deepEqual(bodies.map((body) => body.baseRevisionSha256), [revision(0), revision(0), revision(1), revision(2)]);
  assert.deepEqual(bodies.map((body) => body.annotations), [[a], [a], [a, b], [a, b, c]]);
  assert.equal(model.getSnapshot().error, null);
});

test("409 cannot retry over remote ink; explicit reload keeps the local draft available in Undo", async (t) => {
  const h = await harness(t);
  const remote = deferred<ModelAnnotationsDto>();
  let reads = 0;
  t.mock.method(h.studio, "modelAnnotations", async () => ++reads === 1 ? reply() : remote.promise);
  const bodies: ModelAnnotationsRequestDto[] = [];
  t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => {
    bodies.push(structuredClone(body));
    if (bodies.length === 1) throw new h.StudioApiError({ status: 409, code: "ANNOTATION_STALE", detail: "The model annotation revision changed." });
    return reply(source, body.annotations, body.comment, revision(9));
  });
  const model = h.controller.model(scope); await model.load();
  const a = ink("a"), b = ink("b"), c = ink("c");
  model.changeAnnotations([a]); model.changeAnnotations([a, b]);
  await assert.rejects(model.save(), { status: 409, code: "ANNOTATION_STALE" });
  await assert.rejects(model.save(), { status: 409 });
  assert.equal(bodies.length, 1); assert.equal(reads, 1, "a conflict never triggers an automatic reload");
  const reloading = model.reload(); await flush();
  model.changeAnnotations([a, b, c]);
  await assert.rejects(model.save(), { code: "UNSUPPORTED_REQUEST" });
  remote.resolve(reply(source, [ink("remote")], "remote comment", revision(8))); await reloading;
  assert.equal(bodies.length, 1, "reload does not write the old draft over the new CAS base");
  assert.deepEqual(model.getSnapshot().annotations, [ink("remote")]);
  assert.equal(model.getSnapshot().dirty, false);
  model.undo();
  assert.deepEqual(model.getSnapshot().annotations, [a, b, c]);
  assert.equal(model.getSnapshot().comment, "stored model comment");
  assert.equal((await model.save()).revisionSha256, revision(9));
  assert.equal(bodies[1].baseRevisionSha256, revision(8));
  assert.deepEqual(bodies[1].annotations, [a, b, c]);
});

test("reload waits for the in-flight write and rejects queued save promises before adopting remote state", async (t) => {
  const h = await harness(t);
  const inFlight = deferred<ModelAnnotationsDto>();
  const order: string[] = [];
  let reads = 0;
  t.mock.method(h.studio, "modelAnnotations", async () => {
    order.push("get"); return ++reads === 1 ? reply() : reply(source, [ink("remote")], "remote comment", revision(8));
  });
  t.mock.method(h.studio, "saveModelAnnotations", async () => { order.push("put"); return inFlight.promise; });
  const model = h.controller.model(scope); await model.load();
  model.changeAnnotations([ink("a")]); const first = model.save(); await flush();
  model.changeAnnotations([ink("a"), ink("b")]);
  const rejected = assert.rejects(model.save(), { code: "UNSUPPORTED_REQUEST" });
  const loading = model.reload(); await flush();
  assert.deepEqual(order, ["get", "put"]);
  inFlight.resolve(reply(source, [ink("a")], "stored model comment", revision(1)));
  await first; await loading; await rejected;
  assert.deepEqual(order, ["get", "put", "get"], "the second queued draft was not written during reload");
  assert.deepEqual(model.getSnapshot().annotations, [ink("remote")]);
  assert.equal(model.getSnapshot().canUndo, true);
});

test("reopening reads full 3D geometry and source identity; caller mutations cannot alter saved history", async (t) => {
  const h = await harness(t);
  let persisted = JSON.stringify(reply());
  const reads: ModelSourceDto[] = [];
  t.mock.method(h.studio, "modelAnnotations", async (modelSource: ModelSourceDto) => {
    reads.push(structuredClone(modelSource)); return JSON.parse(persisted);
  });
  const a = ink("a"), expected = structuredClone(a);
  const put = t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => {
    assert.deepEqual(body.annotations, [expected]);
    assert.deepEqual(body.modelSource, source);
    persisted = JSON.stringify(reply(source, body.annotations, body.comment, revision(1))); return JSON.parse(persisted);
  });
  const mutable = structuredClone(scope);
  const model = h.controller.model(mutable);
  mutable.modelSource!.runId = "a-later-selection";
  await model.load();
  model.changeAnnotations([a]);
  a.camera.position[0] = 999; a.screen[0][0] = 999; a.hits![0].world[0] = 999; a.hits![0].userStrings!.Element = "changed";
  const saved = await model.save();
  saved.modelSource.runId = "mutated-return"; saved.annotations[0].camera.position[0] = 888;
  assert.deepEqual(model.getSnapshot().annotations, [expected]);
  assert.deepEqual((await model.save()).modelSource, source, "returning a snapshot does not lend out the internal CAS state");
  const reopened = h.createController().model(scope); await reopened.load();
  assert.deepEqual(reads, [source, source]);
  assert.deepEqual(reopened.getSnapshot().annotations, [expected]);
  assert.equal(reopened.getSnapshot().comment, "stored model comment");
  assert.equal(reopened.getSnapshot().dirty, false); assert.equal(reopened.getSnapshot().canUndo, false);
  assert.equal((await reopened.save()).revisionSha256, revision(1));
  assert.equal(put.mock.callCount(), 1);
});

test("a missing PUT revision pauses the queue and preserves its unsaved draft", async (t) => {
  const h = await harness(t);
  t.mock.method(h.studio, "modelAnnotations", async () => reply(source, [], "", null));
  const put = t.mock.method(h.studio, "saveModelAnnotations", async (body: ModelAnnotationsRequestDto) => reply(source, body.annotations, body.comment, null));
  const model = h.controller.model(scope); await model.load();
  model.changeAnnotations([ink("a")]);
  await assert.rejects(model.save(), { code: "TRANSPORT_ERROR" });
  assert.equal(put.mock.callCount(), 1);
  assert.deepEqual(model.getSnapshot().annotations, [ink("a")]);
  assert.equal(model.getSnapshot().dirty, true);
  await flush(); assert.equal(model.getSnapshot().saving, false);
});
