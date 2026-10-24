import assert from "node:assert/strict";
import test from "node:test";

import type { ChatSummary } from "../src/api/generated";
import type { AttentionCard } from "../src/notifications/cards/types.ts";
import {
  addNotice, attentionCopy, cardNews, clearance, GAP, lasts, MAX_NOTICES, noticeText, noticeTitle, pendingCards, remember, settle, settleCards,
  snapshotOf, titled, transitions,
  type AttentionEvent, type EdgeBox,
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

test("the stack sits left of what stands at the right edge and above what lies along the bottom where they share columns", () => {
  const viewport = { width: 1440, height: 960 };
  const right = (box: Omit<EdgeBox, "edge">): EdgeBox => ({ ...box, edge: "right" });
  const bottom = (box: Omit<EdgeBox, "edge">): EdgeBox => ({ ...box, edge: "bottom" });
  assert.deepEqual(clearance(viewport, []), { right: GAP, bottom: GAP }, "nothing registered: the corner");
  const rail = right({ left: 1364, right: 1440, top: 0, bottom: 960 });
  // A tool panel is open: the conversation, and its composer, end left of the stack.
  const beside = bottom({ left: 244, right: 800, top: 760, bottom: 960 });
  assert.deepEqual(clearance(viewport, [rail, beside]), { right: 76 + GAP, bottom: GAP }, "a composer left of the stack stays uncovered");
  // No panel: the centred composer reaches under the stack, Send button and all.
  const centred = bottom({ left: 400, right: 1208, top: 760, bottom: 960 });
  assert.deepEqual(clearance(viewport, [rail, centred]), { right: 76 + GAP, bottom: 200 + GAP / 2 });
  const narrow = { width: 700, height: 900 };
  const narrowRail = right({ left: 632, right: 700, top: 0, bottom: 900 });
  const composer = bottom({ left: 12, right: 620, top: 700, bottom: 892 });
  assert.deepEqual(clearance(narrow, [narrowRail, composer]), { right: 68 + GAP, bottom: 200 + GAP / 2 }, "lifted above a composer below it");
  assert.deepEqual(clearance(narrow, [narrowRail, bottom({ left: 0, right: 0, top: 0, bottom: 0 })]), { right: 68 + GAP, bottom: GAP }, "a hidden composer is not avoided");
  assert.deepEqual(clearance(narrow, [right({ left: 0, right: 60, top: 0, bottom: 900 })]), { right: GAP, bottom: GAP }, "a rail on the left is not the right rail");
});

test("a side panel and its rail push the stack left; a floating toolbar lifts it where they share columns", () => {
  const viewport = { width: 1440, height: 900 };
  // The attention shell with its side panel open over the model (the #687 review's case).
  const side = { left: 1003, right: 1440, top: 44, bottom: 900, edge: "right" as const };
  const statusAndComposer = { left: 248, right: 1003, top: 790, bottom: 900, edge: "bottom" as const };
  const toolbar = { left: 413, right: 839, top: 697, bottom: 746, edge: "bottom" as const };
  assert.deepEqual(clearance(viewport, [side, statusAndComposer]), { right: 437 + GAP, bottom: 110 + GAP / 2 }, "above the status line and composer");
  assert.deepEqual(clearance(viewport, [side, statusAndComposer, toolbar]), { right: 437 + GAP, bottom: 203 + GAP / 2 }, "and above the toolbar it would cover");
  // Side panel closed: a toolbar that ends left of the stack leaves it in the corner.
  const rail = { left: 1364, right: 1440, top: 44, bottom: 900, edge: "right" as const };
  const toolbarLeftOfStack = { left: 540, right: 960, top: 697, bottom: 746, edge: "bottom" as const };
  assert.deepEqual(clearance(viewport, [rail, toolbarLeftOfStack]), { right: 76 + GAP, bottom: GAP });
});

const card = (id: string, overrides: Partial<AttentionCard> = {}): AttentionCard => ({
  id, projectId: "A", projectDir: "D:\\work\\A", channel: "decide", kind: "ab", state: "open", title: `Pick ${id}`, titleZh: `选 ${id}`,
  source: { chatSessionId: "chat-1" }, media: [], evidence: [], options: [], opens: [],
  createdAt: "2026-10-24T10:00:00+00:00", updatedAt: "2026-10-24T10:00:00+00:00", ...overrides,
});

test("a card that comes to need the person is news once; the first reading is a baseline", () => {
  assert.deepEqual(cardNews(null, [card("a")]), [], "the first reading raises nothing");
  const before = pendingCards([card("a")]);
  const raised = cardNews(before, [card("a"), card("b"), card("c", { state: "later" }), card("d", { state: "working" })]);
  assert.deepEqual(raised.map((event) => event.card?.id), ["b"], "only a card that needs the person now and did not before");
  assert.equal(raised[0]!.kind, "card");
  assert.equal(raised[0]!.chatId, "chat-1", "it opens the conversation it came from where the queue is not on screen");
  assert.deepEqual(cardNews(pendingCards([card("a")]), [card("b", { state: "review" })]).map((event) => event.card?.id), ["b"], "a change list waiting for review counts");
  assert.deepEqual(cardNews(before, [card("e")], new Set(["e"])), [], "a card on screen already raises nothing");
});

test("a card's notice waits when it asks something, and leaves when the card is handled", () => {
  const decide = cardNews(new Set(), [card("a")])[0]!;
  const notice = cardNews(new Set(), [card("n", { channel: "notice", kind: "info" })])[0]!;
  assert.equal(lasts(decide), true);
  assert.equal(lasts(notice), false, "news that only informs fades");
  const stack = addNotice(addNotice([], decide), notice);
  assert.deepEqual(settleCards(stack, [card("a", { state: "done" }), card("n", { channel: "notice", kind: "info" })]).map((event) => event.card?.id), ["n"]);
  assert.equal(settleCards(stack, [card("a"), card("n", { channel: "notice", kind: "info" })]), stack, "nothing changed: the same stack");
  // A card holds one notice, apart from the conversation it came from.
  const permission: AttentionEvent = { key: "chat-1:permission:1", kind: "permission", chatId: "chat-1", projectDir: "D:\\work\\A", title: "Roof" };
  assert.equal(addNotice(addNotice([], permission), decide).length, 2);
  assert.deepEqual(settle([decide], []), [decide], "a reading of the conversations leaves card notices alone");
});

test("a card's notice reads in the person's language", () => {
  const decide = cardNews(new Set(), [card("a")])[0]!;
  assert.equal(noticeText(decide, attentionCopy["zh-CN"]), "要你决定：选 a");
  assert.equal(noticeText(decide, attentionCopy.en), "Your decision: Pick a");
  const english = cardNews(new Set(), [card("b", { titleZh: null, channel: "guess", kind: "ask" })])[0]!;
  assert.equal(noticeText(english, attentionCopy["zh-CN"]), "想问你：Pick b", "the English title where no Chinese one was written");
});
