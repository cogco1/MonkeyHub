import assert from "node:assert/strict";
import test from "node:test";

import type { ChatSummary } from "../src/api/generated";
import {
  addNotice, attentionCopy, clearance, GAP, MAX_NOTICES, noticeText, noticeTitle, remember, settle, snapshotOf, titled, transitions,
  type AttentionEvent,
} from "../src/notifications/attention.ts";

const chat = (id: string, overrides: Partial<ChatSummary> = {}): ChatSummary => ({
  id, projectId: "A", projectDir: "D:\\work\\A", title: `Chat ${id}`, provider: "codex", status: "idle",
  createdAt: "2026-09-25T10:00:00+00:00", updatedAt: "2026-09-25T10:00:00+00:00", attention: null, ...overrides,
});
/** Readings in order, each against the one before; what each raised. */
const read = (readings: ChatSummary[][], open: string | null = null) => {
  let previous: ReturnType<typeof snapshotOf> | null = null;
  return readings.map((sessions) => { const raised = transitions(previous, sessions, open); previous = snapshotOf(sessions); return raised; });
};
const kinds = (events: AttentionEvent[]) => events.map((event) => `${event.chatId}:${event.kind}`);

test("the first reading is a baseline and raises nothing", () => {
  const [first] = read([[chat("a", { status: "running", attention: "permission" }), chat("b", { status: "failed", error: { code: "CHAT_PROCESS_FAILED", detail: "x" } })]]);
  assert.deepEqual(first, []);
});

test("each transition raises one notice, keyed by chat, kind and updatedAt", () => {
  const [, asked, answered, done] = read([
    [chat("a", { status: "running", updatedAt: "t1" })],
    [chat("a", { status: "running", attention: "permission", updatedAt: "t2" })],
    [chat("a", { status: "running", updatedAt: "t3" })],
    [chat("a", { status: "idle", updatedAt: "t4" })],
  ]);
  assert.deepEqual(asked, [{ key: "a:permission:t2", kind: "permission", chatId: "a", projectDir: "D:\\work\\A", title: "Chat a" }]);
  assert.deepEqual(answered, [], "an answered permission is not news");
  assert.deepEqual(kinds(done), ["a:finished"]);
  assert.equal(done[0].key, "a:finished:t4");
});

test("a permission that keeps waiting is announced once", () => {
  const waiting = chat("a", { status: "running", attention: "permission", updatedAt: "t2" });
  const [, asked, still] = read([[chat("a", { status: "running" })], [waiting], [{ ...waiting, updatedAt: "t3" }]]);
  assert.deepEqual(kinds(asked), ["a:permission"]);
  assert.deepEqual(still, []);
});

test("a failure is news; a stop the architect asked for is not", () => {
  const failure = { code: "CHAT_PROCESS_FAILED", detail: "The CLI exited with code 1." };
  const [, failed, same] = read([
    [chat("a", { status: "running" }), chat("b", { status: "running" }), chat("c", { status: "running" }), chat("d", { status: "running" })],
    [chat("a", { status: "failed", error: failure, updatedAt: "t2" }), chat("b", { status: "interrupted", error: { code: "CHAT_STOPPED", detail: "The response was stopped." } }),
      chat("c", { status: "interrupted", error: { code: "CHAT_INTERRUPTED", detail: "Hub closed before this turn finished." }, updatedAt: "t2" }),
      chat("d", { status: "failed", updatedAt: "t2" })],
    [chat("a", { status: "failed", error: failure, updatedAt: "t3" }), chat("b", { status: "interrupted" }), chat("c", { status: "interrupted" }), chat("d", { status: "failed" })],
  ]);
  assert.deepEqual(kinds(failed), ["a:failed", "c:failed", "d:failed"], "a failed turn, a turn the Hub ended, an external failure without an error");
  assert.deepEqual(same, [], "an error that stays is not new");
});

test("a turn that failed between two readings is still announced", () => {
  const [, failed] = read([[chat("a", { status: "idle" })], [chat("a", { status: "failed", error: { code: "CHAT_TIMEOUT", detail: "late" }, updatedAt: "t2" })]]);
  assert.deepEqual(kinds(failed), ["a:failed"]);
});

test("the chat on screen raises nothing, and its news is not held back for later", () => {
  const readings = [[chat("a", { status: "running" }), chat("b", { status: "running" })], [chat("a", { status: "idle", updatedAt: "t2" }), chat("b", { status: "idle", updatedAt: "t2" })]];
  const [, done] = read(readings, "a");
  assert.deepEqual(kinds(done), ["b:finished"]);
  // Leaving the chat later does not replay what happened while it was open.
  const again = transitions(snapshotOf(readings[1]), readings[1], null);
  assert.deepEqual(again, []);
});

test("a chat seen for the first time raises nothing, unless a permission already waits", () => {
  const [, appeared] = read([[chat("a")], [chat("a"), chat("b", { status: "failed", error: { code: "X", detail: "restored from the archive" } }),
    chat("c", { status: "running" }), chat("d", { status: "running", attention: "permission", updatedAt: "t2" })]]);
  assert.deepEqual(kinds(appeared), ["d:permission"]);
});

test("the same moment is never announced twice, even when an older reading comes back", () => {
  const seen = new Set<string>();
  const running = [chat("a", { status: "running", updatedAt: "t1" })], done = [chat("a", { status: "idle", updatedAt: "t2" })];
  const announced = read([running, done, running, done]).flat().filter((event) => remember(seen, event.key));
  assert.deepEqual(announced.map((event) => event.key), ["a:finished:t2"]);
  const small = new Set<string>();
  for (const key of ["1", "2", "3"]) remember(small, key, 2);
  assert.deepEqual([...small], ["2", "3"], "only the last keys are kept");
});

const notice = (chatId: string, kind: AttentionEvent["kind"]): AttentionEvent => ({ key: `${chatId}:${kind}:t`, kind, chatId, projectDir: "D:\\work\\A", title: chatId });

test("the stack keeps one notice per chat and at most three, permissions last", () => {
  let stack: AttentionEvent[] = [];
  stack = addNotice(stack, notice("a", "permission"));
  stack = addNotice(stack, notice("a", "finished"));
  assert.deepEqual(kinds(stack), ["a:finished"], "a chat's newer news replaces what it said before");
  for (const id of ["b", "c", "d"]) stack = addNotice(stack, notice(id, "permission"));
  assert.equal(stack.length, MAX_NOTICES);
  assert.deepEqual(kinds(stack), ["b:permission", "c:permission", "d:permission"], "an informing notice leaves first");
  assert.deepEqual(kinds(addNotice(stack, notice("e", "finished"))), kinds(stack), "news does not push out a waiting permission");
  assert.deepEqual(kinds(addNotice(stack, notice("e", "permission"))), ["c:permission", "d:permission", "e:permission"]);
});

test("an answered permission and an archived chat take their notices with them", () => {
  const stack = [notice("a", "permission"), notice("b", "finished"), notice("c", "failed")];
  const same = [chat("a", { attention: "permission" }), chat("b"), chat("c")];
  assert.equal(settle(stack, same), stack, "nothing changed: the same stack");
  assert.deepEqual(kinds([...settle(stack, [chat("a"), chat("c")])]), ["c:failed"]);
});

test("the title counts unseen notices and drops the count at zero", () => {
  assert.equal(titled("MonkeyHub", 2), "(2) MonkeyHub");
  assert.equal(titled("(2) MonkeyHub", 3), "(3) MonkeyHub");
  assert.equal(titled("(3) MonkeyFab", 0), "MonkeyFab");
  assert.equal(titled("(draft) plan", 0), "(draft) plan", "only a count is a count");
});

test("the words name the chat in both languages, short enough for one line", () => {
  const zh = attentionCopy["zh-CN"], en = attentionCopy.en;
  assert.equal(noticeText({ kind: "permission", title: "立面研究" }, zh), "「立面研究」需要你的授权");
  assert.equal(noticeText({ kind: "finished", title: "立面研究" }, zh), "「立面研究」已完成");
  assert.equal(noticeText({ kind: "failed", title: "立面研究" }, zh), "「立面研究」出错了");
  assert.equal(noticeText({ kind: "permission", title: "Facade study" }, en), "“Facade study” needs your permission");
  assert.equal(noticeText({ kind: "finished", title: "Facade study" }, en), "“Facade study” is done");
  assert.equal(noticeText({ kind: "failed", title: "Facade study" }, en), "“Facade study” ran into an error");
  assert.equal(noticeTitle("  ", zh), "新对话");
  assert.equal(noticeTitle("一二三四五六七八九十一二三四五六七八九十一二三四五六", zh), "一二三四五六七八九十一二三四五六七八九十一二三…");
  assert.equal(noticeTitle("a\n  b", en), "a b");
});

test("the stack sits left of the rail and above the composer when they would share columns", () => {
  const viewport = { width: 1440, height: 960 };
  assert.deepEqual(clearance(viewport, null, null), { right: GAP, bottom: GAP }, "no Hub shell: the corner");
  const rail = { left: 1364, right: 1440, top: 0, bottom: 960 };
  // A tool panel is open: the conversation, and its composer, end left of the stack.
  const beside = { left: 244, right: 800, top: 760, bottom: 960 };
  assert.deepEqual(clearance(viewport, rail, beside), { right: 76 + GAP, bottom: GAP }, "a composer left of the stack stays uncovered");
  // No panel: the centred composer reaches under the stack, Send button and all.
  const centred = { left: 400, right: 1208, top: 760, bottom: 960 };
  assert.deepEqual(clearance(viewport, rail, centred), { right: 76 + GAP, bottom: 200 + GAP / 2 });
  const narrow = { width: 700, height: 900 };
  const narrowRail = { left: 632, right: 700, top: 0, bottom: 900 };
  const composer = { left: 12, right: 620, top: 700, bottom: 892 };
  assert.deepEqual(clearance(narrow, narrowRail, composer), { right: 68 + GAP, bottom: 200 + GAP / 2 }, "lifted above a composer below it");
  assert.deepEqual(clearance(narrow, narrowRail, { left: 0, right: 0, top: 0, bottom: 0 }), { right: 68 + GAP, bottom: GAP }, "a hidden composer is not avoided");
  assert.deepEqual(clearance(narrow, { left: 0, right: 60, top: 0, bottom: 900 }, null), { right: GAP, bottom: GAP }, "a rail on the left is not the right rail");
});
