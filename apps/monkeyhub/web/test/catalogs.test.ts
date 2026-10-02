import assert from "node:assert/strict";
import test from "node:test";

import { chatCopy as chatEn, hubCopy as hubEn, messagesEn } from "../src/i18n/messages.en.ts";
import { chatCopy as chatZhCN, hubCopy as hubZhCN, messagesZhCN } from "../src/i18n/messages.zh-CN.ts";
import { translateMessage } from "../../../../packages/web-shared/src/i18n.js";

const placeholders = (message: string) => [...message.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();

const sharedValues = new Map<string, string>([
  ["messages:workspace.monkeyarch", "MonkeyArch is a product name and 3D is an established format label."],
  ["messages:conversation.who.studio", "Studio is a product name."],
  ["messages:artifact.kind.exact3dm", "3dm is a file-format extension."],
  ["messages:terminal.identity", "This value contains placeholders and separators only."],
  ["messages:capability.bounds", "This value contains placeholders and a range symbol only."],
  ["messages:settings.options.en", "The language selector names English in English."],
  ["hub:occt", "OCCT is a library name."],
  ["chat:stage", "Stage is the product's formal approval term."],
]);

const catalogs = [
  ["messages", messagesEn, messagesZhCN],
  ["hub", hubEn, hubZhCN],
  ["chat", chatEn, chatZhCN],
] as const;

test("English and Chinese catalogs keep the same keys and interpolation parameters", () => {
  for (const [name, english, chinese] of catalogs) {
    assert.deepEqual(Object.keys(chinese).sort(), Object.keys(english).sort(), `${name} keys`);
    for (const key of Object.keys(english) as Array<keyof typeof english>) {
      const englishValue = english[key];
      const chineseValue = chinese[key];
      if (typeof englishValue === "string" && typeof chineseValue === "string") {
        assert.deepEqual(placeholders(chineseValue), placeholders(englishValue), `${name}:${String(key)} placeholders`);
      }
    }
  }
});

test("identical English and Chinese copy is limited to named product, format, unit, or Stage terms", () => {
  const actual = catalogs.flatMap(([name, english, chinese]) =>
    (Object.keys(english) as Array<keyof typeof english>)
      .filter((key) => english[key] === chinese[key])
      .map((key) => `${name}:${String(key)}`),
  );
  assert.deepEqual(actual.sort(), [...sharedValues.keys()].sort());
  for (const key of actual) assert.ok(sharedValues.get(key), `${key} needs an explicit reason`);
});

test("the tracing-paper review controls are clear in both languages", () => {
  assert.deepEqual(
    [messagesEn["stage.tools.annotate"], messagesEn["stage.tracingPaper.send"], messagesEn["stage.tracingPaper.sent"]],
    ["Tracing paper", "Send review to Board", "Review sent to Board"],
  );
  assert.deepEqual(
    [messagesZhCN["stage.tools.annotate"], messagesZhCN["stage.tracingPaper.send"], messagesZhCN["stage.tracingPaper.sent"]],
    ["描图纸", "将审阅意见发送到画板", "审阅意见已发送到画板"],
  );
});

test("Board lifecycle, export and replacement consistently name the Board in each catalog", () => {
  const keys = ["board.loading", "board.loadFailed", "board.title", "board.conflict", "board.export", "board.exportEmpty", "board.replacement.hint"] as const;
  for (const key of keys) {
    assert.match(messagesEn[key], /\bboard\b/i, key);
    assert.match(messagesZhCN[key], /画板/, key);
    assert.doesNotMatch(messagesZhCN[key], /画布|图墙|白板/, key);
    assert.doesNotMatch(messagesEn[key], /canvas|whiteboard|drawing wall/i, key);
  }
  assert.equal(messagesEn["board.retry"], "Retry");
  assert.equal(messagesZhCN["board.retry"], "重试");
});

test("Board replacement notices interpolate names and counts without interpreting filename placeholders", () => {
  const name = "Review {count} {name}.png";
  assert.equal(translateMessage(messagesEn, "board.replacement.updated", { name }), `«${name}» updated`);
  assert.equal(translateMessage(messagesZhCN, "board.replacement.updated", { name }), `«${name}» 已更新`);
  assert.equal(translateMessage(messagesEn, "board.replacement.updatedMore", { name, count: 2 }), `«${name}» updated · 2 more updated`);
  assert.equal(translateMessage(messagesZhCN, "board.replacement.updatedMore", { name, count: 2 }), `«${name}» 已更新 · 另有 2 页已更新`);
  assert.deepEqual(placeholders(messagesEn["board.replacement.updatedMore"]), ["count", "name"]);
});
