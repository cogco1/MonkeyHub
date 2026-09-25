import assert from "node:assert/strict";
import test from "node:test";

import type { ChatMessage } from "../src/api/generated";
import { clock, currentStep, describeCall, describeStep, stepText, turnsOf, workedSeconds, type ProcessWords } from "../src/chatProcess.ts";
import { chatCopy as en } from "../src/i18n/messages.en.ts";
import { chatCopy as zh } from "../src/i18n/messages.zh-CN.ts";

const words = (t: typeof en | typeof zh): ProcessWords => ({ steps: t.processStep, prepare: t.processPrepare,
  webSearch: t.processWebSearch, desktop: t.processDesktop, permission: t.processPermission });
const read = (line: string, t: typeof en | typeof zh = zh) => stepText(describeCall(line), words(t));
const message = (overrides: Partial<ChatMessage> & Pick<ChatMessage, "id" | "role">): ChatMessage => ({
  content: "", createdAt: "2026-09-25T10:00:00Z", status: "complete", ...overrides,
});

test("the calls the Agent makes read as plain actions in both languages", () => {
  const cases: Array<[string, string, string]> = [
    ["studio_request · GET /api/board · completed", "读取画板", "Read the board"],
    ["studio_request · PUT /api/board · completed", "更新画板", "Update the board"],
    ["studio_request · POST /api/intents/context · completed", "读取设计上下文", "Read the design context"],
    ["studio_request · POST /api/proposals/prop-7/candidate · completed", "生成方案", "Generate a scheme"],
    ["studio_request · GET /api/drawings/model-view?runId=r&view=front · completed", "查看模型视图", "View the model"],
    ["studio_request · POST /api/drawings/sheets · completed", "出图", "Make a drawing sheet"],
    ["studio_request · POST /api/board/export · completed", "导出画板", "Export board pages"],
    ["studio_request · GET /api/state?run=cand-1 · completed", "读取模型状态", "Read the model state"],
    ["studio_request · GET /api/documents?runId=r · completed", "读取项目资料", "Read project documents"],
    ["studio_request · POST /api/issue · failed", "更新项目数据", "Update project data"],
    ["studio_schema · GET /api/state · in_progress", "准备读取模型状态", "Prepare to read the model state"],
    ["studio_schema · POST /api/intents/context · completed", "准备读取设计上下文", "Prepare to read the design context"],
    ["Web search: timber roof precedents · completed", "网络搜索：timber roof precedents", "Web search: timber roof precedents"],
    ["rg -n Stage C:\\Users\\me\\MEMORY.md · failed", "查阅资料", "Look through files"],
    ["Select-String -Path notes.md -Pattern Stage · completed", "查阅资料", "Look through files"],
    ["Get-Content README.md · completed", "查阅资料", "Look through files"],
    ["Read project files · in_progress", "查阅资料", "Look through files"],
    ["Bash · completed", "运行命令", "Run a command"],
    ["WebSearch · completed", "网络搜索", "Web search"],
    ["Edit · completed", "修改文件", "Edit files"],
    ["fab_request · POST /api/fab/send · completed", "检查打印文件", "Check the print file"],
    ["CLICK — Save ✓", "桌面操作：Save", "Desktop: Save"],
    ["mystery_tool · completed", "其他操作", "Other step"],
    // A runtime operation names its request the same way.
    ["POST /api/proposals/abc/candidate", "生成方案", "Generate a scheme"],
  ];
  for (const [line, chinese, english] of cases) {
    assert.equal(read(line, zh), chinese, line);
    assert.equal(read(line, en), english, line);
  }
});

test("a permission row names the choice that answered it", () => {
  const pending = message({ id: "u-0:permission:p1", role: "tool", status: "streaming", content: "Allow the requested command?" });
  const answered = { ...pending, status: "complete" as const, content: "Allow the requested command? · Allow once" };
  assert.equal(stepText(describeStep(pending), words(zh)), "权限请求");
  assert.equal(stepText(describeStep(answered), words(en)), "Permission: Allow once");
});

test("a transcript folds each turn's calls and keeps text, progress, results and waiting prompts visible", () => {
  const messages: ChatMessage[] = [
    message({ id: "u-0", role: "user", content: "Widen the courtyard", createdAt: "2026-09-25T10:00:00Z" }),
    message({ id: "u-0:t1", role: "tool", content: "studio_request · GET /api/board · completed", createdAt: "2026-09-25T10:00:05Z" }),
    message({ id: "u-0:t2", role: "tool", status: "failed", content: "rg -n x · failed\nexit 1", createdAt: "2026-09-25T10:00:20Z" }),
    message({ id: "u-0:answer", role: "assistant", content: "Done.", createdAt: "2026-09-25T10:01:10Z" }),
    message({ id: "u-0:t3", role: "tool", candidateId: "cand-1", content: "studio_request · GET /api/jobs/j · completed", createdAt: "2026-09-25T10:01:18Z" }),
    message({ id: "u-4", role: "user", content: "Now the roof", createdAt: "2026-09-25T10:05:00Z" }),
    message({ id: "u-4:progress:summary", role: "tool", status: "streaming", content: "Reading the roof.", createdAt: "2026-09-25T10:05:03Z" }),
    message({ id: "u-4:t4", role: "tool", status: "streaming", content: "studio_request · POST /api/intents/context · in_progress", createdAt: "2026-09-25T10:05:04Z",
      permission: { id: "p1", title: "Allow?", options: [] } }),
  ];
  const [first, second] = turnsOf(messages, true);
  assert.equal(first!.key, "u-0");
  assert.deepEqual(first!.steps.map((row) => row.id), ["u-0:t1", "u-0:t2", "u-0:t3"]);
  assert.deepEqual(first!.visible.map((row) => row.id), ["u-0:answer"]);
  assert.deepEqual(first!.results.map((row) => row.id), ["u-0:t3"]);
  assert.equal(first!.failed, 1);
  assert.equal(workedSeconds(first!), 78);
  assert.equal(en.processWorked(en.elapsed(78), first!.steps.length), "Worked 1m 18s · 3 steps");
  assert.equal(zh.processWorked(zh.elapsed(78), first!.steps.length), "用时 1分18秒 · 3 步");
  assert.equal(zh.processFailed(first!.failed), "1 步失败");
  // Public progress is text; a waiting permission prompt stays in view while the turn runs.
  assert.deepEqual(second!.visible.map((row) => row.id), ["u-4:progress:summary", "u-4:t4"]);
  assert.deepEqual(second!.steps.map((row) => row.id), ["u-4:t4"]);
  assert.equal(currentStep(second!)?.id, "u-4:t4");
  assert.deepEqual(turnsOf(messages, false)[1]!.visible.map((row) => row.id), ["u-4:progress:summary"],
    "a finished turn has no prompt left to answer");
  // Messages before any user turn, such as an external presentation, still form one turn.
  assert.equal(turnsOf([message({ id: "external", role: "assistant" })], false)[0]!.key, "start");
});

test("an interjection the running turn takes in stays inside that turn; a restarted one starts the next", () => {
  const messages: ChatMessage[] = [
    message({ id: "u-0", role: "user", content: "Widen the courtyard", createdAt: "2026-09-25T10:00:00Z" }),
    message({ id: "u-0:t1", role: "tool", content: "studio_request · GET /api/board · completed", createdAt: "2026-09-25T10:00:05Z" }),
    message({ id: "u-1", role: "user", content: "Keep it square", interjection: "delivered", createdAt: "2026-09-25T10:00:07Z" }),
    message({ id: "u-0:t2", role: "tool", status: "streaming", content: "studio_request · POST /api/intents/context · in_progress", createdAt: "2026-09-25T10:00:09Z" }),
    message({ id: "u-2", role: "user", content: "Stop the roof instead", interjection: "restarted", createdAt: "2026-09-25T10:00:30Z" }),
  ];
  const turns = turnsOf(messages, true);
  assert.equal(turns.length, 2, "the delivered interjection does not split the turn; the restarted one opens the next");
  assert.deepEqual(turns[0]!.steps.map((row) => row.id), ["u-0:t1", "u-0:t2"], "calls on both sides of the interjection fold into one row");
  assert.deepEqual(turns[0]!.visible.map((row) => row.id), ["u-1"], "the interjection shows inline in its turn");
  assert.equal(turns[1]!.key, "u-2");
});

test("elapsed time reads naturally and unreadable times are not guessed", () => {
  assert.equal(clock(42), "0:42");
  assert.equal(clock(725), "12:05");
  assert.equal(clock(3723), "1:02:03");
  assert.equal(en.elapsed(42), "42s");
  assert.equal(en.elapsed(3900), "1h 5m");
  assert.equal(zh.elapsed(120), "2分");
  assert.equal(zh.elapsed(3900), "1小时5分");
  const [turn] = turnsOf([message({ id: "u", role: "user", createdAt: "not a time" }), message({ id: "u:t", role: "tool", content: "Bash · completed" })], false);
  assert.equal(workedSeconds(turn!), null);
  assert.equal(en.processSteps(turn!.steps.length), "Process · 1 step");
});
