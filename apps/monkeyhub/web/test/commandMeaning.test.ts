import assert from "node:assert/strict";
import test from "node:test";

import { meaningRows, offerFor, type ArgChoices } from "../src/features/commands/meaning.ts";
import { COMMAND_TREE } from "../src/features/commands/tree.generated.ts";

const choices: ArgChoices = {
  project: [{ value: "Housing", label: "Housing" }, { value: "Housing 13 户", label: "Housing 13 户" }, { value: "Thi Hemp", label: "Thi Hemp" }],
};

test("a command a sentence means is offered with what the sentence named, and asks only what it left out", () => {
  assert.deepEqual(offerFor(COMMAND_TREE, "draft.tools.push_pull", "把这面墙再往外挪一些", choices, false)?.args, {});
  const named = offerFor(COMMAND_TREE, "project.open", "切到 housing 13 户", choices, false);
  assert.deepEqual(named?.args, { project: "Housing 13 户" }, "the longest name the sentence holds");
  assert.equal(named?.missing, null);
  const asked = offerFor(COMMAND_TREE, "project.open", "切到另一个项目", choices, false);
  assert.deepEqual(asked?.args, {});
  assert.equal(asked?.missing?.name, "project");
  assert.equal(asked?.missing?.choices.length, 3);
});

test("a command that cannot be offered here is not: no way to choose its argument, only meaningful in place, unknown", () => {
  assert.equal(offerFor(COMMAND_TREE, "project.open", "切到另一个项目", {}, false), null, "nothing to choose from");
  assert.equal(offerFor(COMMAND_TREE, "chat.composer.send", "发一条消息", choices, false), null, "two arguments nobody can choose");
  const inPlace = COMMAND_TREE.commands.find((command) => command.inPlace);
  assert.ok(inPlace);
  assert.equal(offerFor(COMMAND_TREE, inPlace.id, "随便一句", choices, false), null);
  assert.equal(offerFor(COMMAND_TREE, "no.such.command", "随便一句", choices, false), null);
});

test("Ctrl K lists close meaning hits it does not already show, commands listed bare only", () => {
  const answer = { state: "ready" as const, route: null, hits: [
    { id: "draft.tools.push_pull", score: 0.79, text: "把墙往外推一点" },
    { id: "view.canvas.zoom_in", score: 0.7, text: "页面拉近一些" },
    { id: "project.open", score: 0.69, text: "打开项目" },
    { id: "draft.tools.move", score: 0.55, text: "移动" },
  ] };
  assert.deepEqual(meaningRows(answer, COMMAND_TREE, new Set(["view.canvas.zoom_in"]), false).map((command) => command.id), ["draft.tools.push_pull"]);
  assert.deepEqual(meaningRows(null, COMMAND_TREE, new Set(), false), []);
});
