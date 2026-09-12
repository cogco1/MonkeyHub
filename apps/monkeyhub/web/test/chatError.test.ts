/**
 * The failures here are the ones the installed CLIs actually produced, copied
 * from a real run: Codex answering an unknown model, Claude answering one, a
 * CLI's stderr tail, and a Hub refusal. Nothing is invented.
 */

import assert from "node:assert/strict";
import test from "node:test";
import { presentFailure } from "../src/chatError.ts";

// Observed: codex exec --json -m not-a-real-model-xyz, event {"type":"error", ...}
const codexModel = {
  code: "CHAT_PROVIDER_FAILED",
  detail: '{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The \'not-a-real-model-xyz\' model is not supported when using Codex with a ChatGPT account."}}',
};
// Observed: claude -p --model not-a-real-model-xyz, the result event's text.
const claudeModel = {
  code: "CHAT_PROVIDER_FAILED",
  detail: "There's an issue with the selected model (not-a-real-model-xyz). It may not exist or you may not have access to it. Run --model to pick a different model.",
};

test("a provider's model refusal reads as a sentence and offers the picker", () => {
  for (const failure of [codexModel, claudeModel]) {
    const shown = presentFailure(failure, "zh-CN", "Codex")!;
    assert.ok(!shown.summary.includes("{"), `no JSON in the summary: ${shown.summary}`);
    // The one recognised kind says what to do, in the reader's language.
    assert.equal(shown.summary, "当前模型无法通过 Codex 使用，请更换模型。");
    assert.equal(presentFailure(failure, "en", "Claude Code")!.summary,
      "The current model is not available through Claude Code. Choose another model.");
    assert.equal(shown.modelRejected, true);
    // The original stays whole, code included, for diagnosis.
    assert.ok(shown.technical.includes(failure.detail));
    assert.ok(shown.technical.startsWith("CHAT_PROVIDER_FAILED:"));
  }
});

test("an unknown failure is not guessed at, and keeps its whole text", () => {
  const shown = presentFailure({ code: "SOMETHING_NEW", detail: '{"nested":{"unexpected":true}}' }, "zh-CN")!;
  assert.match(shown.summary, /这一步没有完成/);
  assert.equal(shown.modelRejected, false, "an unrecognised failure is not blamed on the model");
  assert.ok(!/账户|权限|登录/.test(shown.summary), "no account or permission cause is claimed");
  assert.ok(shown.technical.includes('"unexpected":true'));
});

test("a Hub refusal keeps its own short wording and its code", () => {
  const shown = presentFailure({ code: "CHAT_PROJECT_BUSY", detail: "Another project has a running chat. Stop it before switching projects." }, "zh-CN")!;
  assert.equal(shown.summary, "另一个项目还在执行，先等它结束或停止它。");
  assert.equal(shown.modelRejected, false);
  assert.ok(shown.technical.includes("CHAT_PROJECT_BUSY"));
});

test("a CLI's stderr tail stays diagnostics rather than becoming the headline", () => {
  const stderr = [
    "2026-09-11T17:54:43.658492Z  WARN codex_skills::interface: ignoring interface.icon_small",
    "2026-09-11T17:54:43.672594Z  WARN codex_core::shell_snapshot: Failed to create shell snapshot for powershell",
  ].join("\n");
  const shown = presentFailure({ code: "CHAT_PROCESS_FAILED", detail: stderr }, "en")!;
  assert.equal(shown.summary, "The CLI could not finish this turn.");
  assert.ok(shown.technical.includes("shell_snapshot"), "every line stays available");
});

test("both languages answer, and nothing is dropped in either", () => {
  for (const language of ["en", "zh-CN"] as const) {
    const shown = presentFailure(codexModel, language)!;
    assert.ok(shown.summary.length > 0);
    assert.ok(shown.technical.includes("invalid_request_error"));
  }
  assert.equal(presentFailure(null, "en"), null);
});
