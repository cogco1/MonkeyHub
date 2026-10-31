import assert from "node:assert/strict";
import test from "node:test";

import { answersKey, cardPictures, chosenOption, comparedRuns, isPending, needsWords, orderedOptions, pendingFor, queueOrder, say } from "../src/notifications/cards/cardText.ts";
import type { AttentionCard, CardOption } from "../src/notifications/cards/types.ts";

const option = (id: string, recommended = false): CardOption => ({
  id, label: id, consequence: `does ${id}`, action: { commandId: "candidate.review.archive", args: {} }, recommended,
});
const card = (id: string, overrides: Partial<AttentionCard> = {}): AttentionCard => ({
  id, projectId: "A", projectDir: "D:\\work\\A", channel: "decide", kind: "choice", state: "open", title: id,
  source: {}, media: [], evidence: [], options: [], opens: [], createdAt: "2026-10-24T10:00:00+00:00", updatedAt: "2026-10-24T10:00:00+00:00", ...overrides,
});

test("a decision's comparison is bound to the runs it opens and to what each answer does", () => {
  const adopt = (id: string, runId: string): CardOption => ({ ...option(id), action: { commandId: "design.go_to_step.continue", args: { runId } } });
  const six = card("study", { options: [adopt("a", "r1"), adopt("b", "r6")], opens: [{ commandId: "chat.open_conversation", args: { sessionId: "s" } },
    { commandId: "candidate.compare.decide", args: { runIds: ["r1", "r6"] } }] });
  const seven = card("study", { options: [adopt("a", "r2"), adopt("b", "r7")], opens: [{ commandId: "candidate.compare.decide", args: { runIds: ["r2", "r7"] } }] });
  assert.deepEqual(comparedRuns(six), ["r1", "r6"]);
  assert.deepEqual(comparedRuns(seven), ["r2", "r7"]);
  assert.deepEqual(comparedRuns(card("x")), [], "a card that opens no comparison weighs no pair");
  assert.notEqual(answersKey(six), answersKey(seven), "Adopt A now adopts another result");
  assert.equal(answersKey(six), answersKey(card("again", { options: [adopt("a", "r1"), adopt("b", "r6")] })));
});

test("a decision's pictures are its exact models, A then B, each the answer that chooses it", () => {
  const model = (run: string) => ({ runId: run, stateDigest: `s-${run}`, assetSha256: `m-${run}` });
  const decision = card("one", {
    options: [option("switch"), option("keep")],
    media: [{ kind: "model_view", runId: "v16", modelSource: model("v16"), optionId: "keep", caption: "A · Current design" },
      { kind: "model_view", runId: "b", modelSource: model("b"), optionId: "switch", caption: "B · New result" },
      { kind: "model_view", runId: "old", caption: "a model view without its model is only named" }],
  });
  assert.deepEqual(cardPictures(decision).map((row) => [row.media.runId, row.option?.id]), [["v16", "keep"], ["b", "switch"]]);
  const unanswered = card("two", { media: [{ kind: "model_view", runId: "b", modelSource: model("b"), optionId: "gone" }] });
  assert.equal(cardPictures(unanswered)[0]?.option, null, "a picture whose answer the card does not offer is only shown");
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
