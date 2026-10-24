import assert from "node:assert/strict";
import test from "node:test";

import { chosenOption, isPending, needsWords, orderedOptions, pendingFor, queueOrder, say } from "../src/notifications/cards/cardText.ts";
import type { AttentionCard, CardOption } from "../src/notifications/cards/types.ts";

const option = (id: string, recommended = false): CardOption => ({
  id, label: id, consequence: `does ${id}`, action: { commandId: "candidate.review.archive", args: {} }, recommended,
});
const card = (id: string, overrides: Partial<AttentionCard> = {}): AttentionCard => ({
  id, projectId: "A", projectDir: "D:\\work\\A", channel: "decide", kind: "choice", state: "open", title: id,
  source: {}, media: [], evidence: [], options: [], opens: [], createdAt: "2026-10-24T10:00:00+00:00", updatedAt: "2026-10-24T10:00:00+00:00", ...overrides,
});

test("a card reads in Chinese where its writer gave Chinese, and in English otherwise", () => {
  assert.equal(say("Keep V16", "保留 V16", "zh-CN"), "保留 V16");
  assert.equal(say("Keep V16", null, "zh-CN"), "Keep V16");
  assert.equal(say("Keep V16", "保留 V16", "en"), "Keep V16");
});

test("the recommended answer comes first; the others keep their writer's order", () => {
  assert.deepEqual(orderedOptions(card("a", { options: [option("x"), option("y", true), option("z")] })).map((item) => item.id), ["y", "x", "z"]);
  assert.deepEqual(orderedOptions(card("a", { options: [option("x"), option("z")] })).map((item) => item.id), ["x", "z"], "no recommendation, no reordering");
});

test("an answer that sends a message the card does not write asks for the person's words", () => {
  assert.equal(needsWords({ action: { commandId: "chat.composer.send", args: {} } }), true);
  assert.equal(needsWords({ action: { commandId: "chat.composer.send", args: { content: "Use the south entrance." } } }), false, "the card wrote it");
  assert.equal(needsWords({ action: { commandId: "candidate.review.archive", args: {} } }), false);
});

test("a handled card names the answer it was given", () => {
  const handled = card("a", { state: "done", options: [option("x"), option("y")], resolution: { optionId: "y", by: "person", at: "2026-10-24T10:01:00+00:00" } });
  assert.equal(chosenOption(handled)?.id, "y");
  assert.equal(chosenOption(card("b", { state: "done", resolution: { optionId: null, by: "machine", at: "2026-10-24T10:01:00+00:00" } })), null);
});

test("what needs the person comes first in the queue, newest first within each", () => {
  const cards = [
    card("later", { state: "later", updatedAt: "2026-10-24T12:00:00+00:00" }),
    card("old", { updatedAt: "2026-10-24T09:00:00+00:00" }),
    card("working", { state: "working", updatedAt: "2026-10-24T13:00:00+00:00" }),
    card("new", { updatedAt: "2026-10-24T11:00:00+00:00" }),
    card("review", { state: "review", updatedAt: "2026-10-24T10:00:00+00:00" }),
  ];
  assert.deepEqual(queueOrder(cards).map((item) => item.id), ["new", "review", "old", "working", "later"]);
  assert.deepEqual(cards.filter(isPending).map((item) => item.id), ["old", "new", "review"]);
});

test("a project's count comes from the queue's summary, and is unknown without one", () => {
  const summary = { projects: [{ projectId: "A", projectDir: "D:\\work\\A", open: 2, later: 1, working: 0 }], open: 2, later: 1, working: 0 };
  assert.equal(pendingFor(summary, "D:\\work\\A"), 2);
  assert.equal(pendingFor(summary, "D:\\work\\B"), 0, "a project without cards waits on nothing");
  assert.equal(pendingFor(null, "D:\\work\\A"), null);
});
