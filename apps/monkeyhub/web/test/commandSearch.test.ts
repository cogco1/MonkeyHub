import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { indexOf, rankCommands, routeSentence, searchable, searchCommands, searchTerms } from "../src/features/commands/search.ts";
import { COMMAND_TREE } from "../src/features/commands/tree.generated.ts";

/** Sentences people type and what the search answers; the Runtime's capability index reads the same file. */
const CASES = JSON.parse(readFileSync(new URL("../../../../governance/command_search_cases.json", import.meta.url), "utf8")) as {
  cases: { say: string; command?: string; route?: boolean }[]; nothing: string[];
};
const index = indexOf(COMMAND_TREE, searchable(COMMAND_TREE));

test("a sentence finds the command it is about, and a clear one is offered in place of the conversation", () => {
  for (const row of CASES.cases) {
    if (row.command) assert.equal(searchCommands(index, row.say)[0]?.command.id, row.command, row.say);
    if (row.route !== undefined) assert.equal(routeSentence(index, row.say)?.command.id ?? null, row.route ? row.command : null, `${row.say}: offered`);
  }
  for (const said of CASES.nothing) assert.deepEqual(searchCommands(index, said), [], said);
});

test("Chinese is read in character pairs and English in words, the common ones left out", () => {
  assert.deepEqual(searchTerms("把这个面拉高"), ["面拉", "拉高"]);
  assert.deepEqual(searchTerms("Take a screenshot of it"), ["take", "screenshot"]);
});

test("Ctrl K looks a sentence up when no command has every word of it, and keeps two keywords strict", () => {
  assert.equal(rankCommands(COMMAND_TREE, "帮我截一张图", "zh-CN")[0]?.command.id, "model.capture");
  assert.deepEqual(rankCommands(COMMAND_TREE, "导出 楼梯栏杆扶手", "zh-CN"), [], "two parts stay strict");
});

test("a search over the whole tree is a lookup", () => {
  const started = performance.now();
  for (let round = 0; round < 50; round += 1) for (const row of CASES.cases) searchCommands(index, row.say);
  const each = (performance.now() - started) / (50 * CASES.cases.length);
  assert.ok(each < 5, `${each.toFixed(3)} ms a search`);
});
