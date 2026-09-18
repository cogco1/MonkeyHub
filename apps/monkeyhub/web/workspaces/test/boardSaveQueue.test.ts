import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

import type { BoardDto, BoardRequestDto } from "../src/api/generated/index.ts";
import type { BoardDraft } from "../src/workspaces/monkeyboard/boardScene.ts";
import type { BoardSaveState } from "../src/workspaces/monkeyboard/boardSaveQueue.ts";

const revision = (index: number) => String(index).repeat(64);
const draft = (text: string): BoardDraft => ({
  projectId: "project-a", title: "Meeting board",
  elements: [{ id: "note-1", type: "text", x: 80, y: 120, text }],
  seenDocuments: [JSON.stringify(["drawing-run", "project://project-a/runs/drawing-run/records/revision-1.json"])],
});
const initial = (): BoardDto => ({ ...draft("Original note"), revisionSha256: revision(0) });
const reply = (body: BoardRequestDto, index: number): BoardDto => ({
  projectId: body.projectId, title: body.title ?? "Meeting board",
  elements: structuredClone(body.elements), seenDocuments: [...body.seenDocuments], revisionSha256: revision(index),
});
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

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
  return await vite.ssrLoadModule("/src/workspaces/monkeyboard/boardSaveQueue.ts") as typeof import("../src/workspaces/monkeyboard/boardSaveQueue.ts");
}

test("in-flight edits save only the latest draft after the sent snapshot receives its revision", async (t) => {
  const { createBoardSaveQueue } = await harness(t);
  const requests: BoardRequestDto[] = [];
  const responses: ReturnType<typeof deferred<BoardDto>>[] = [];
  const queue = createBoardSaveQueue(initial(), (body) => {
    requests.push(structuredClone(body));
    const response = deferred<BoardDto>();
    responses.push(response);
    return response.promise;
  }, () => {}, 60_000);
  t.after(() => queue.dispose());

  queue.change(draft("First edit"));
  const saving = queue.flush();
  await tick();
  queue.change(draft("Intermediate edit"));
  const latest = draft("Latest edit");
  latest.title = "Facade review";
  queue.change(latest);
  latest.elements[0].text = "Outside mutation";
  assert.equal(queue.flush(), saving);
  assert.equal(requests.length, 1, "new input cannot start an overlapping write");
  assert.equal(requests[0].baseRevisionSha256, revision(0));
  assert.equal(requests[0].elements[0].text, "First edit");

  responses[0].resolve(reply(requests[0], 1));
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].baseRevisionSha256, revision(1));
  assert.equal(requests[1].elements[0].text, "Latest edit", "an earlier ACK never replaces newer local work");
  assert.equal(requests[1].title, "Facade review");
  assert.equal(queue.getState().dirty, true);
  assert.equal(queue.getState().saving, true);

  responses[1].resolve(reply(requests[1], 2));
  await saving;
  assert.deepEqual(queue.getState(), { dirty: false, saving: false, error: null, conflict: false, revisionSha256: revision(2) });
  await queue.flush();
  assert.equal(requests.length, 2);
});

for (const code of ["BOARD_STALE", "BOARD_CONFLICT", "BOARD_BINDING_MISMATCH"]) {
  test(`${code} pauses local edits and an explicit retry cannot overwrite another revision`, async (t) => {
    const { createBoardSaveQueue } = await harness(t);
    const conflict = Object.assign(new Error("Another board revision exists."), { code });
    const requests: BoardRequestDto[] = [];
    const queue = createBoardSaveQueue(initial(), async (body) => {
      requests.push(structuredClone(body));
      throw conflict;
    }, () => {}, 5);
    t.after(() => queue.dispose());

    queue.change(draft("Local note"));
    await assert.rejects(queue.flush(), (error) => error === conflict);
    queue.change(draft("Continued local note"));
    await delay(20);
    await assert.rejects(queue.retry(), (error) => error === conflict);
    await assert.rejects(queue.flush(), (error) => error === conflict);
    assert.deepEqual(queue.getState(), { dirty: true, saving: false, error: conflict, conflict: true, revisionSha256: revision(0) });
    queue.dispose();
    await tick();
    assert.equal(requests.length, 1, "neither editing, retrying nor leaving the board writes through a conflict");
  });
}

test("a transient failure retries the latest draft against the last acknowledged revision", async (t) => {
  const { createBoardSaveQueue } = await harness(t);
  const requests: BoardRequestDto[] = [];
  const failed = deferred<BoardDto>();
  const offline = new Error("Connection interrupted");
  const queue = createBoardSaveQueue(initial(), async (body) => {
    requests.push(structuredClone(body));
    if (requests.length === 2) return failed.promise;
    return reply(body, requests.length === 1 ? 1 : 2);
  }, () => {}, 60_000);
  t.after(() => queue.dispose());

  queue.change(draft("Acknowledged note"));
  await queue.flush();
  queue.change(draft("Failed snapshot"));
  const saving = queue.flush();
  const rejected = assert.rejects(saving, (error) => error === offline);
  await tick();
  queue.change(draft("Edit during failure"));
  failed.reject(offline);
  await rejected;
  queue.change(draft("Latest retained note"));
  await tick();
  assert.equal(requests.length, 2, "editing does not silently restart a failed write");
  assert.deepEqual(queue.getState(), { dirty: true, saving: false, error: offline, conflict: false, revisionSha256: revision(1) });

  await queue.retry();
  assert.equal(requests.length, 3);
  assert.deepEqual(requests.map((body) => body.baseRevisionSha256), [revision(0), revision(1), revision(1)]);
  assert.deepEqual(requests.map((body) => body.elements[0].text), ["Acknowledged note", "Failed snapshot", "Latest retained note"]);
  assert.deepEqual(queue.getState(), { dirty: false, saving: false, error: null, conflict: false, revisionSha256: revision(2) });
});

test("unchanged canvas captures from app-only updates never dirty the board or duplicate a save", async (t) => {
  const { createBoardSaveQueue } = await harness(t);
  const requests: BoardRequestDto[] = [];
  const notifications: BoardSaveState[] = [];
  const queue = createBoardSaveQueue(initial(), async (body) => {
    requests.push(structuredClone(body));
    return reply(body, requests.length);
  }, (state) => notifications.push(state), 5);
  t.after(() => queue.dispose());

  queue.change(draft("Original note"));
  queue.change(structuredClone(draft("Original note")));
  await queue.flush();
  assert.equal(requests.length, 0);
  assert.equal(notifications.length, 0);
  queue.change(draft("One saved edit"));
  queue.change(structuredClone(draft("One saved edit")));
  await delay(20);
  await queue.flush();
  queue.change(structuredClone(draft("One saved edit")));
  await delay(20);
  assert.equal(requests.length, 1);
  assert.equal(queue.getState().dirty, false);
});

test("dispose immediately flushes a pending final edit and suppresses all subsequent notifications", async (t) => {
  const { createBoardSaveQueue } = await harness(t);
  const requests: BoardRequestDto[] = [];
  const response = deferred<BoardDto>();
  const notifications: BoardSaveState[] = [];
  const queue = createBoardSaveQueue(initial(), (body) => {
    requests.push(structuredClone(body));
    return response.promise;
  }, (state) => notifications.push(state), 60_000);
  t.after(() => queue.dispose());

  const final = draft("Final note before leaving");
  final.elements[0].isDeleted = true;
  queue.change(final);
  const count = notifications.length;
  queue.dispose();
  queue.dispose();
  queue.change(draft("Ignored after leaving"));
  await tick();
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].elements, final.elements);
  assert.deepEqual(requests[0].seenDocuments, final.seenDocuments, "deleting an element preserves received document identities");
  const saving = queue.flush();
  response.resolve(reply(requests[0], 1));
  await saving;
  assert.equal(notifications.length, count);
  assert.equal(queue.getState().dirty, false);
});

test("dispose during a write still saves the final local draft after that write is acknowledged", async (t) => {
  const { createBoardSaveQueue } = await harness(t);
  const requests: BoardRequestDto[] = [];
  const responses: ReturnType<typeof deferred<BoardDto>>[] = [];
  const notifications: BoardSaveState[] = [];
  const queue = createBoardSaveQueue(initial(), (body) => {
    requests.push(structuredClone(body));
    const response = deferred<BoardDto>();
    responses.push(response);
    return response.promise;
  }, (state) => notifications.push(state), 60_000);
  t.after(() => queue.dispose());

  queue.change(draft("Sent note"));
  const saving = queue.flush();
  await tick();
  queue.change(draft("Final local note"));
  const count = notifications.length;
  queue.dispose();
  responses[0].resolve(reply(requests[0], 1));
  await tick();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].elements[0].text, "Final local note");
  assert.equal(requests[1].baseRevisionSha256, revision(1));
  responses[1].resolve(reply(requests[1], 2));
  await saving;
  assert.equal(notifications.length, count);
  assert.deepEqual(queue.getState(), { dirty: false, saving: false, error: null, conflict: false, revisionSha256: revision(2) });
});
