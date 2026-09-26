import assert from "node:assert/strict";
import test from "node:test";

import { chatCopy as chatEn, hubCopy as hubEn, messagesEn } from "../src/i18n/messages.en.ts";
import { chatCopy as chatZhCN, hubCopy as hubZhCN, messagesZhCN } from "../src/i18n/messages.zh-CN.ts";

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
