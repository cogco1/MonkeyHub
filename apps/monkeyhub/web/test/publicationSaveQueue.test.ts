import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { createPublicationSaveQueue } from "../src/workspaces/publish/publicationSaveQueue.ts";
import type { PublicationDto, PublicationRequestDto } from "../src/api/generated/index.ts";

const initial = (): PublicationDto => ({ projectId: "review", revisionSha256: "a".repeat(64), title: "Original",
  spec: { width: 960, height: 540, template: "hero" }, pages: [], sources: [] });
const reply = (body: PublicationRequestDto, revision = "b"): PublicationDto => ({ ...initial(), ...body, revisionSha256: revision.repeat(64) });
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test("autosave keeps newer edits, serializes writes and flushes the final snapshot", async () => {
  const requests: PublicationRequestDto[] = [], responses: ((value: PublicationDto) => void)[] = [];
  const states: [PublicationDto, boolean, boolean, unknown][] = [];
  const queue = createPublicationSaveQueue(initial(), (body) => {
    requests.push(body); return new Promise((resolve) => responses.push(resolve));
  }, (...state) => states.push(state), 0);
  queue.change({ ...initial(), title: "First" });
  await delay(10);
  assert.equal(requests.length, 1);
  const latest = { ...initial(), title: "Newest" };
  queue.change(latest); latest.title = "Outside mutation";
  const flushing = queue.flush();
  assert.equal(queue.flush(), flushing);
  responses[0](reply(requests[0])); await tick();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].title, "Newest");
  assert.equal(requests[1].baseRevisionSha256, "b".repeat(64));
  assert.equal(states.at(-1)![0].title, "Newest");
  assert.equal(queue.accept(initial()), false, "a delayed GET cannot overwrite pending content");
  responses[1](reply(requests[1], "c"));
  assert.equal((await flushing).title, "Newest");
  assert.equal(queue.pending(), false);
  assert.deepEqual(states.at(-1)!.slice(1), [false, false, null]);
  queue.dispose();
});

test("failure retains edits and retries exactly once without silently rebasing a conflict", async () => {
  let calls = 0;
  const queue = createPublicationSaveQueue(initial(), async (body) => {
    calls++; if (calls === 1) throw new Error("PUBLICATION_STALE"); return reply(body);
  }, () => {}, 10000);
  queue.change({ ...initial(), title: "My text" });
  await assert.rejects(queue.flush(), /PUBLICATION_STALE/);
  queue.change({ ...initial(), title: "My repaired text" });
  assert.equal(queue.accept({ ...initial(), title: "Remote" }), false);
  await assert.rejects(queue.flush(), /PUBLICATION_STALE/);
  assert.equal(calls, 1);
  assert.equal((await queue.retry()).title, "My repaired text");
  assert.equal(calls, 2); queue.dispose();
});

test("leaving before debounce flushes to the original project without later UI callbacks", async () => {
  const writes: PublicationRequestDto[] = []; let notifications = 0;
  const queue = createPublicationSaveQueue(initial(), async (body) => { writes.push(body); return reply(body); }, () => notifications++, 10000);
  queue.change({ ...initial(), title: "Last edit before switching" });
  const before = notifications; queue.dispose(); await tick();
  assert.equal(writes.length, 1); assert.equal(writes[0].projectId, "review");
  assert.equal(writes[0].title, "Last edit before switching"); assert.equal(notifications, before);
});

test("a mismatched project response cannot clear unsaved content", async () => {
  const queue = createPublicationSaveQueue(initial(), async (body) => ({ ...reply(body), projectId: "other" }), () => {}, 10000);
  queue.change({ ...initial(), title: "Keep me" });
  await assert.rejects(queue.flush(), /project and revision/);
  assert.equal(queue.pending(), true); assert.equal(queue.current().title, "Keep me"); queue.dispose();
});

test("a failed draft can reconnect after its project surface closes and retry against its original base", async () => {
  const queue = createPublicationSaveQueue(initial(), async () => { throw new Error("offline"); }, () => {}, 10000);
  queue.change({ ...initial(), title: "Recover after project close" });
  await assert.rejects(queue.flush(), /offline/); queue.dispose();
  let restored: PublicationDto | undefined;
  queue.resume(async (body) => { assert.equal(body.baseRevisionSha256, initial().revisionSha256); return reply(body); }, (draft) => { restored = draft; });
  assert.equal(restored!.title, "Recover after project close");
  assert.equal(queue.accept(initial()), false);
  await queue.retry(); assert.equal(queue.pending(), false); queue.dispose();
});

test("Board import remains pending across unmount and hands its new revision to the remounted editor", async () => {
  let finish!: (saved: PublicationDto) => void;
  const queue = createPublicationSaveQueue(initial(), async (body) => reply(body, "c"), () => {}, 10000);
  const imported = queue.append(() => new Promise((resolve) => { finish = resolve; }));
  await tick(); assert.equal(queue.importing(), true); assert.equal(queue.pending(), true);
  queue.dispose();
  let current: PublicationDto | undefined;
  queue.resume(async (body) => { assert.equal(body.baseRevisionSha256, "b".repeat(64)); return reply(body, "c"); }, (draft) => { current = draft; });
  assert.equal(queue.accept(initial()), false);
  const saved = { ...initial(), revisionSha256: "b".repeat(64), pages: [{ id: "board-page", elements: [] }] };
  finish(saved); await imported;
  assert.deepEqual(current!.pages, saved.pages); assert.equal(queue.importing(), false);
  queue.change({ ...current!, title: "Edit after imported pages" });
  await queue.flush(); assert.equal(queue.current().revisionSha256, "c".repeat(64)); queue.dispose();
});

test("retry after remount replays the failed Board import instead of acknowledging an empty save", async () => {
  let calls = 0;
  const queue = createPublicationSaveQueue(initial(), async (body) => reply(body), () => {}, 10000);
  await assert.rejects(queue.append(async (base) => {
    calls++; if (calls === 1) throw new Error("Import offline");
    return { ...base, revisionSha256: "b".repeat(64), pages: [{ id: "imported", elements: [] }] };
  }), /Import offline/);
  assert.equal(queue.pending(), true); queue.dispose();
  queue.resume(async (body) => reply(body), () => {});
  const saved = await queue.retry();
  assert.equal(calls, 2); assert.equal(saved.pages[0].id, "imported");
  assert.equal(queue.pending(), false); queue.dispose();
});
