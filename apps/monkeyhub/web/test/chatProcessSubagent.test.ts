import assert from "node:assert/strict";
import test from "node:test";

import type { ChatMessage } from "../src/api/generated";
import { describeCall, stepText, stepTree, type ProcessWords } from "../src/chatProcess.ts";
import { chatCopy as en } from "../src/i18n/messages.en.ts";
import { chatCopy as zh } from "../src/i18n/messages.zh-CN.ts";

const words = (t: typeof en | typeof zh): ProcessWords => ({ steps: t.processStep, prepare: t.processPrepare,
  webSearch: t.processWebSearch, desktop: t.processDesktop, permission: t.processPermission, subagent: t.processSubagent });
const step = (id: string, content: string, parentCallId?: string): ChatMessage => ({
  id, role: "tool", content, createdAt: "2026-10-29T10:00:00Z", status: "complete", ...(parentCallId ? { parentCallId } : {}),
});

test("a subagent is named by its task and its own calls by what they did, without the mark", () => {
  assert.equal(stepText(describeCall("Agent · survey the folder · completed"), words(en)), "Helper: survey the folder");
  assert.equal(stepText(describeCall("Agent · survey the folder · completed"), words(zh)), "助手：survey the folder");
  assert.equal(stepText(describeCall("↳ Read · completed"), words(en)), stepText(describeCall("Read · completed"), words(en)));
});

test("a subagent's calls fold under the call that started it; one whose parent is not shown stays a step", () => {
  const tree = stepTree([
    step("u:a", "Agent · survey · completed"),
    step("u:b", "↳ Read · completed", "u:a"),
    step("u:c", "Grep · completed"),
    step("u:d", "↳ Grep · completed", "u:a"),
    step("u:e", "↳ Read · completed", "u:gone"),
  ]);
  assert.deepEqual(tree.map((node) => [node.step.id, node.children.map((child) => child.id)]),
    [["u:a", ["u:b", "u:d"]], ["u:c", []], ["u:e", []]]);
});
