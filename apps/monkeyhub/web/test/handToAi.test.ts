import assert from "node:assert/strict";
import test from "node:test";

import { HandToAiError, handToAiErrorText, handToAiMessage, marksFile, pointMarks, withNote } from "../src/features/handToAi/handToAi.ts";

// S08: what one 「交给 AI」 hands over, the same from the Board, a page and the tracing paper.

test("the sentence hangs on the first circle, and only there", () => {
  const marks = withNote([{ id: "arrow", kind: "arrow" }, { id: "a", kind: "ellipse" }, { id: "b", kind: "circle" }], " 这面墙往外推一米 ");
  assert.deepEqual(marks.map((mark) => mark.note), [undefined, "这面墙往外推一米", undefined]);
  assert.equal(withNote([{ id: "t", kind: "text" }, { id: "l", kind: "line" }], "改这里")[0]!.note, "改这里", "with no circle, the first mark takes it");
});

test("the marks file names the surface, the sentence and what the surface adds", async () => {
  const file = marksFile("document", "加一扇窗", [{ id: "1", kind: "circle", bounds: { x: 0.1, y: 0.2, width: 0.3, height: 0.1 } }], { page: { pageNumber: 2 } });
  assert.equal(file.name, "hand-to-ai-document-marks.json");
  assert.equal(file.type, "application/json");
  const body = JSON.parse(await file.text());
  assert.equal(body.kind, "monkeyhub.hand_to_ai");
  assert.equal(body.surface, "document");
  assert.equal(body.marks[0].note, "加一扇窗");
  assert.equal(body.page.pageNumber, 2);
});

test("point marks are boxed in their own space, with their written words", () => {
  const [mark] = pointMarks([{ id: "g", kind: "circle", points: [[0.5, 0.25], [0.75, 0.5], [0.6, 0.3]], label: " 门 " }]);
  assert.deepEqual(mark!.bounds, { x: 0.5, y: 0.25, width: 0.25, height: 0.25 });
  assert.equal(mark!.text, "门");
  assert.equal(pointMarks([{ kind: "text", points: [] }])[0]!.bounds, null);
});

test("the message is the person's sentence first, then one line saying what came with it", () => {
  assert.equal(handToAiMessage("board", "这面墙往外推一米", 2, "zh-CN"), "这面墙往外推一米\n\n（画板 · 圈了 2 处 · 截图和圈到的对象在附件里。）");
  assert.equal(handToAiMessage("model", "Raise it", 1, "en"), "Raise it\n\n(Model · 1 mark · The picture and the marked objects are attached.)");
});

test("a refusal reads as what to do next, never the service's words", () => {
  assert.equal(handToAiErrorText(new HandToAiError("CONVERSATION_BUSY", "x"), "zh-CN"), "AI 还在处理上一条，等它回完再交。");
  const raw = handToAiErrorText(new Error("Value error, seat 'x' /api/chat Traceback"), "zh-CN");
  assert.doesNotMatch(raw, /Value error|seat|\/api\/|Traceback/);
});
