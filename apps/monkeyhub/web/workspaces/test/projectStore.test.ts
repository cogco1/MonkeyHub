/**
 * The client project store's rules (#366): one request in flight, `wanted = max(wanted, hint)`,
 * a delta only onto the revision it starts from, no older answer applied, an epoch change resets,
 * pulls on every stream open and on focus, the stream before the first snapshot, a write done only
 * once the store holds its revision, and one notification per frame.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { ProjectStore, ProjectStores, type IndexAnswer, type IndexEntity } from "../src/api/projectStore.ts";

type Since = { epoch: string; revision: number } | null;

/** A reader whose answers the test gives one at a time, in the order asked. */
function scripted() {
  const asked: Since[] = [];
  const pending: ((answer: IndexAnswer | null) => void)[] = [];
  const read = (since: Since) => { asked.push(since); return new Promise<IndexAnswer | null>((resolve) => { pending.push(resolve); }); };
  const answer = async (value: IndexAnswer | null) => {
    const next = pending.shift();
    assert.ok(next, "a request is waiting for this answer");
    next(value);
    await settle();
  };
  return { read, asked, pending, answer };
}

const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
const run = (id: string, rev: number, body: Record<string, unknown> = {}): IndexEntity => ({ id, domain: id.split(":")[0], rev, body });
const snapshot = (epoch: string, revision: number, upserts: IndexEntity[] = []): IndexAnswer =>
  ({ epoch, revision, reset: true, from: null, to: revision, upserts, deletes: [] });
const delta = (epoch: string, from: number, to: number, upserts: IndexEntity[] = [], deletes: string[] = []): IndexAnswer =>
  ({ epoch, revision: to, reset: false, from, to, upserts, deletes });

/** A store whose frames the test runs by hand. */
function store(read: ReturnType<typeof scripted>["read"]) {
  const frames: (() => void)[] = [];
  const created = new ProjectStore(read, { frame: (callback) => { frames.push(callback); } });
  const flush = () => { for (const callback of frames.splice(0)) callback(); };
  return { store: created, frames, flush };
}

test("the first pull reads a snapshot and a later one the changes since its revision", async () => {
  const reader = scripted();
  const { store: s, flush } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 3, [run("run:a", 2)]));
  flush();
  assert.deepEqual(reader.asked, [null]);
  assert.equal(s.getSnapshot().revision, 3);
  assert.equal(s.getSnapshot().byId.get("run:a")?.rev, 2);
  s.pull();
  assert.deepEqual(reader.asked[1], { epoch: "e1", revision: 3 });
  await reader.answer(delta("e1", 3, 4, [run("run:b", 4)], ["run:a"]));
  flush();
  const state = s.getSnapshot();
  assert.equal(state.revision, 4);
  assert.deepEqual([...state.byId.keys()], ["run:b"]);
  assert.equal(state.domains.run, 4);
});

test("at most one request is in flight; pulls asked meanwhile become one more", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  s.pull(); s.pull(); s.pull();
  assert.equal(reader.asked.length, 1);
  await reader.answer(snapshot("e1", 1));
  assert.equal(reader.asked.length, 2, "one request for everything asked while the first was out");
  await reader.answer(delta("e1", 1, 1));
  assert.equal(reader.asked.length, 2);
});

test("a hint pulls only when the store is behind it, and wanted keeps the highest revision", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 5));
  s.hint({ epoch: "e1", revision: 5, domains: ["run"] });
  s.hint({ epoch: "e1", revision: 4 });
  assert.equal(reader.asked.length, 1, "a hint the store already holds reads nothing");
  s.hint({ epoch: "e1", revision: 7 });
  s.hint({ epoch: "e1", revision: 9 });
  s.hint({ epoch: "e1", revision: 8 });
  assert.equal(reader.asked.length, 2);
  // The answer reaches 7 only; wanted is 9, so it asks again at once.
  await reader.answer(delta("e1", 5, 7));
  assert.deepEqual(reader.asked[2], { epoch: "e1", revision: 7 });
  await reader.answer(delta("e1", 7, 9));
  assert.equal(reader.asked.length, 3);
  assert.equal(s.getSnapshot().revision, 0, "not yet published: no frame has run");
});

test("a hint without a revision, or of another epoch, pulls", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 5));
  s.hint(null);
  assert.equal(reader.asked.length, 2);
  await reader.answer(delta("e1", 5, 5));
  s.hint({ epoch: "e2", revision: 1 });
  assert.equal(reader.asked.length, 3);
});

test("a delta applies only onto the revision it starts from, and an answer that is not newer is dropped", async () => {
  const reader = scripted();
  const { store: s, flush } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 5, [run("run:a", 5, { v: 1 })]));
  s.pull();
  await reader.answer(delta("e1", 4, 6, [run("run:a", 6, { v: 2 })]));
  flush();
  assert.equal(s.getSnapshot().revision, 5, "a delta from another revision does not apply");
  assert.deepEqual(s.getSnapshot().byId.get("run:a")?.body, { v: 1 });
  s.pull();
  await reader.answer(snapshot("e1", 4, [run("run:old", 4)]));
  flush();
  assert.equal(s.getSnapshot().revision, 5, "an older snapshot of the same epoch is dropped");
  assert.ok(!s.getSnapshot().byId.has("run:old"));
  s.pull();
  await reader.answer(delta("e1", 5, 5));
  flush();
  assert.equal(s.getSnapshot().revision, 5);
});

test("another epoch replaces everything, even at a lower revision", async () => {
  const reader = scripted();
  const { store: s, flush } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 40, [run("run:a", 40), run("tree", 3)]));
  s.pull();
  await reader.answer(snapshot("e2", 1, [run("run:b", 1)]));
  flush();
  const state = s.getSnapshot();
  assert.equal(state.epoch, "e2");
  assert.equal(state.revision, 1);
  assert.deepEqual([...state.byId.keys()], ["run:b"]);
  assert.equal(state.domains.tree, 1, "every domain moves on a reset");
});

test("a write is done only once the store holds its revision", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 5));
  let done = false;
  const written = s.wrote("e1", 6).then(() => { done = true; });
  assert.equal(reader.asked.length, 2);
  await settle();
  assert.equal(done, false);
  let caught = false;
  const caughtUp = s.caughtUp().then(() => { caught = true; });
  await reader.answer(delta("e1", 5, 6, [run("run:a", 6)]));
  await written; await caughtUp;
  assert.ok(done && caught);
  // Asked for while the first was out, which may have been read before the write: once more.
  assert.equal(reader.asked.length, 3);
  await reader.answer(delta("e1", 6, 6));
  await s.wrote("e1", 6);
  assert.equal(reader.asked.length, 3, "a revision already held waits for nothing");
});

test("a write of another epoch is held once the store resets to the index as it is now", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  s.pull();
  await reader.answer(snapshot("e1", 5));
  const written = s.wrote("e2", 2);
  await reader.answer(snapshot("e3", 1));
  await written;
});

test("a write stops waiting after its timeout when nothing answers", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  await s.wrote("e1", 1, 5);
  assert.equal(reader.asked.length, 1);
});

test("listeners are told at most once per frame, and only when the state moved", async () => {
  const reader = scripted();
  const { store: s, frames, flush } = store(reader.read);
  let told = 0;
  s.subscribe(() => { told += 1; });
  s.pull();
  await reader.answer(snapshot("e1", 1));
  s.pull();
  await reader.answer(delta("e1", 1, 2, [run("run:a", 2)]));
  s.pull();
  await reader.answer(delta("e1", 2, 3, [run("run:a", 3)]));
  assert.equal(frames.length, 1, "three changes ask for one frame");
  flush();
  assert.equal(told, 1);
  assert.equal(s.getSnapshot().revision, 3);
  s.pull();
  await reader.answer(delta("e1", 3, 3));
  flush();
  assert.equal(told, 1, "nothing moved, nobody is told");
});

test("an index that cannot answer leaves the store as it was, and the next pull tries again", async () => {
  const reader = scripted();
  const { store: s, flush } = store(reader.read);
  s.pull();
  await reader.answer(null);
  flush();
  assert.equal(s.getSnapshot().status, "unavailable");
  s.pull();
  await reader.answer(snapshot("e1", 2));
  flush();
  assert.equal(s.getSnapshot().status, "ready");
});

test("derived reads are kept per name under their key", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  let reads = 0;
  const compute = async () => { reads += 1; return reads; };
  assert.equal(await s.derive("tree", "e1:1", compute), 1);
  assert.equal(await s.derive("tree", "e1:1", compute), 1, "the same key reads nothing");
  assert.equal(await s.derive("tree", "e1:2", compute), 2);
  await assert.rejects(s.derive("tree", "e1:3", async () => { throw new Error("refused"); }));
  assert.equal(await s.derive("tree", "e1:3", compute), 3, "a failed read is not kept");
});

test("stores are shared per project, released by count, and read first once the stream opens", async () => {
  const stores = new ProjectStores();
  const reader = scripted();
  const a = stores.acquire("runtime-a", reader.read);
  assert.equal(stores.acquire("runtime-a", reader.read), a, "one store per project, shared");
  assert.equal(reader.asked.length, 0, "no snapshot before the stream is open");
  stores.opened();
  assert.equal(reader.asked.length, 1);
  await reader.answer(snapshot("e1", 1));
  stores.closed();
  stores.pullAll();
  assert.equal(reader.asked.length, 1, "focus while the stream is down waits for it");
  stores.opened();
  assert.equal(reader.asked.length, 2, "every reconnect reads what the stream may have carried meanwhile");
  await reader.answer(delta("e1", 1, 1));
  stores.pullAll();
  assert.equal(reader.asked.length, 3, "focus reads");
  await reader.answer(delta("e1", 1, 1));
  stores.hint("runtime-a", { epoch: "e1", revision: 2 });
  assert.equal(reader.asked.length, 4, "hints reach the store of their project");
  await reader.answer(delta("e1", 1, 2));
  const other = scripted();
  stores.acquire("runtime-b", other.read);
  assert.equal(other.asked.length, 1, "a store taken while the stream is open reads at once");
  stores.release("runtime-a");
  assert.equal(stores.get("runtime-a"), a);
  stores.release("runtime-a");
  stores.acquire("runtime-a", reader.read);
  await settle();
  assert.equal(stores.get("runtime-a"), a, "taken again before the task ended: kept");
  stores.release("runtime-a");
  await settle();
  assert.equal(stores.get("runtime-a"), undefined, "the last release drops it");
  const heard: unknown[] = [];
  const stop = stores.onStudioEvent("runtime-b", (event) => heard.push(event));
  stores.studioEvent("runtime-b", { type: "candidate.queued" });
  stores.studioEvent("runtime-a", { type: "candidate.queued" });
  stop();
  stores.studioEvent("runtime-b", { type: "candidate.running" });
  assert.deepEqual(heard, [{ type: "candidate.queued" }]);
});
