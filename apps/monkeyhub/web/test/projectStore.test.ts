/**
 * The client project store's rules (#366): one request in flight, `wanted = max(wanted, hint)`,
 * a delta only onto the revision it starts from, no older answer applied, an epoch change resets,
 * pulls on every stream open and on focus, the stream before the first snapshot, a write done only
 * once the store holds its revision, and one notification per frame.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { movedAt, ProjectStore, ProjectStores, relayHubStream, type IndexAnswer, type IndexEntity } from "../src/api/project-runtime/projectStore.ts";

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

test("a write stops waiting after its timeout when nothing answers, and is no longer kept", async () => {
  const reader = scripted();
  const { store: s } = store(reader.read);
  await s.wrote("e1", 1, 5);
  assert.equal(reader.asked.length, 1);
  assert.equal(s.waiting, 0, "a wait that timed out is removed");
  const held = s.wrote("e1", 2, 50);
  assert.equal(s.waiting, 1);
  await reader.answer(snapshot("e1", 2));
  await held;
  assert.equal(s.waiting, 0);
});

test("each entity's last move is kept, so a surface follows only what it shows", async () => {
  const reader = scripted();
  const { store: s, flush } = store(reader.read);
  const tree = (id: string) => id === "tree" || id === "working";
  assert.equal(movedAt(s.current(), tree), null, "nothing read yet");
  s.pull();
  await reader.answer(snapshot("e1", 5, [run("run:a", 2), run("tree", 3), run("working", 4), run("area:working", 5)]));
  assert.equal(movedAt(s.current(), tree), "e1:5", "a snapshot moves everything it holds");
  s.pull();
  await reader.answer(delta("e1", 5, 6, [run("area:working", 6), run("run:studio-working-draft", 6)]));
  assert.equal(movedAt(s.current(), tree), "e1:5", "an autosave moves nothing the tree shows");
  s.pull();
  await reader.answer(delta("e1", 6, 7, [run("area:working", 7), run("working", 7)]));
  assert.equal(movedAt(s.current(), tree), "e1:7");
  s.pull();
  await reader.answer(delta("e1", 7, 8, [], ["tree"]));
  flush();
  assert.equal(movedAt(s.getSnapshot(), tree), "e1:8", "a delete is a move");
  s.pull();
  await reader.answer(snapshot("e2", 1, [run("run:a", 1)]));
  assert.equal(movedAt(s.current(), tree), "e2:1", "a reset moves what the store held before too");
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
  const candidate = new ProjectStore(reader.read);
  const a = stores.acquire("runtime-a", candidate);
  assert.equal(a, candidate, "the first surface's store is held");
  assert.equal(stores.acquire("runtime-a", new ProjectStore(reader.read)), a, "one store per project, shared");
  assert.equal(stores.count("runtime-a"), 2);
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
  stores.acquire("runtime-b", new ProjectStore(other.read));
  assert.equal(other.asked.length, 1, "a store taken while the stream is open reads at once");
  stores.release("runtime-a");
  assert.equal(stores.get("runtime-a"), a);
  stores.release("runtime-a");
  stores.acquire("runtime-a", new ProjectStore(reader.read));
  await settle();
  assert.equal(stores.get("runtime-a"), a, "taken again before the task ended: kept");
  stores.release("runtime-a");
  await settle();
  assert.equal(stores.get("runtime-a"), undefined, "the last release drops it");
  // A store made for a render React threw away was never acquired: nothing holds it.
  new ProjectStore(reader.read);
  assert.equal(stores.get("runtime-c"), undefined);
});

test("a project's Studio events are kept, replayed to a later listener, and known by stream and seq", () => {
  const stores = new ProjectStores();
  const heard: string[] = [];
  const said = (event: Record<string, unknown>, replayed: boolean) => `${event.stream}:${event.seq}:${event.type}${replayed ? ":replayed" : ""}`;
  const kinds = ["candidate.queued", "candidate.running", "candidate.succeeded"];
  kinds.forEach((type, index) => stores.studioEvent("runtime-a", { seq: index + 1, type }, "old"));
  const stop = stores.onStudioEvent("runtime-a", (event, replayed) => heard.push(said(event, replayed)));
  assert.deepEqual(heard, kinds.map((type, index) => `old:${index + 1}:${type}:replayed`), "a later panel opens with them, as no news");
  // The Hub's stream reconnected and replayed them: nothing is delivered twice.
  kinds.forEach((type, index) => stores.studioEvent("runtime-a", { seq: index + 1, type }, "old"));
  assert.equal(heard.length, 3);
  // The worker restarted and numbers from 1 again: six events, not three.
  kinds.forEach((type, index) => stores.studioEvent("runtime-a", { seq: index + 1, type }, "new"));
  assert.deepEqual(heard.slice(3), kinds.map((type, index) => `new:${index + 1}:${type}`));
  // One the Hub replays that this page never had: delivered, and said to be a replay.
  stores.studioEvent("runtime-a", { seq: 9, type: "candidate.failed" }, "older", true);
  assert.equal(heard.pop(), "older:9:candidate.failed:replayed");
  stores.studioEvent("runtime-b", { seq: 1, type: "candidate.queued" }, "other");
  stop();
  stores.studioEvent("runtime-a", { seq: 4, type: "candidate.queued" }, "new");
  assert.equal(heard.length, 6, "a listener that stopped hears nothing more");
});

test("the Hub's stream has the stores read once the Hub has subscribed, not when the connection opens", () => {
  const stores = new ProjectStores();
  const reader = scripted();
  stores.acquire("runtime-a", new ProjectStore(reader.read));
  const listeners = new Map<string, Set<(event: Event) => void>>();
  const stream = {
    addEventListener(type: string, listener: (event: Event) => void) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(listener);
    },
    removeEventListener(type: string, listener: (event: Event) => void) { listeners.get(type)?.delete(listener); },
  };
  const emit = (type: string, data: unknown = {}) => {
    for (const listener of listeners.get(type) ?? []) listener(new MessageEvent(type, { data: JSON.stringify(data) }));
  };
  const stop = relayHubStream(stream as unknown as EventSource, stores);
  emit("open");
  assert.equal(reader.asked.length, 0, "the connection is open before the Hub has subscribed");
  emit("runtime", { kind: "runtime/snapshot" });
  assert.equal(reader.asked.length, 1, "the snapshot comes after the subscription: now read");
  emit("runtime", { kind: "project/opened" });
  assert.equal(reader.asked.length, 1, "later runtime frames are not a new subscription");
  const heard: unknown[] = [];
  stores.onStudioEvent("runtime-a", (event, replayed) => heard.push({ ...event, replayed }));
  emit("studio", { runtimeId: "runtime-a", stream: "s1", studio: { seq: 1, type: "candidate.queued" } });
  emit("studio", { runtimeId: "runtime-a", stream: "s0", replay: true, studio: { seq: 7, type: "candidate.succeeded" } });
  assert.deepEqual(heard, [{ seq: 1, type: "candidate.queued", stream: "s1", replayed: false },
    { seq: 7, type: "candidate.succeeded", stream: "s0", replayed: true }], "the Hub's replay is marked as one");
  emit("error");
  emit("open");
  emit("runtime", { kind: "runtime/snapshot" });
  assert.equal(reader.asked.length, 1, "one request in flight: the reconnect's read waits for it");
  stop();
  assert.ok([...listeners.values()].every((set) => set.size === 0), "stopping relays nothing more");
});
