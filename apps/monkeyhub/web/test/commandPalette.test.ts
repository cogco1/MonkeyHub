import assert from "node:assert/strict";
import test from "node:test";

import type { Command, CommandTree } from "../src/features/commands/tree.ts";
import { queryParts, rankCommands, RECENT_SHOWN } from "../src/features/commands/search.ts";
import { hubMayTake, keyName, normalizeShortcut, PALETTE_SHORTCUT, shortcutOf, typesText, type KeyPress } from "../src/features/commands/keys.ts";
import { readUsage, recordUse, RECENT_KEPT, timesUsed, USAGE_KEY } from "../src/features/commands/usage.ts";

const command = (id: string, en: string, zh: string, more: Partial<Command> = {}): Command => ({
  id, group: more.group ?? "model.export", label: { en, "zh-CN": zh }, terms: [], surface: "modeling",
  section: "modeling", channel: "side", place: null, shortcut: null, entries: [], args: [], ...more,
});
const tree: CommandTree = {
  groups: [
    { id: "model.export", owner: "project_runtime.documents", kind: "output", status: "PRODUCTION",
      purpose: { en: "Export the model", "zh-CN": "导出模型" }, terms: ["export the model", "导出模型", "download"] },
    { id: "drawing.plan", owner: "project_runtime.documents", kind: "output", status: "PRODUCTION",
      purpose: { en: "Cut a plan", "zh-CN": "出平面" }, terms: ["floor plan", "平面图"] },
  ],
  commands: [
    command("model.export_3dm", "Export editable 3DM", "导出可编辑 3dm", { terms: ["rhino"] }),
    command("model.export_stl", "Export STL", "导出 STL"),
    command("drawing.plan_new", "New section plan", "新建剖切平面", { group: "drawing.plan", section: "drawings" }),
    command("view.reference_model", "Reference model", "参考模型", { group: "model.export", channel: "background" }),
    command("chat.open_conversation", "Open conversation", "打开对话", { group: "model.export", args: ["sessionId"] }),
  ],
};
const ids = (ranked: ReturnType<typeof rankCommands>) => ranked.map((row) => row.command.id);

test("a query part matches the label in either language, the terms or the id", () => {
  assert.deepEqual(ids(rankCommands(tree, "导出", "zh-CN")), ["model.export_3dm", "model.export_stl"]);
  assert.deepEqual(ids(rankCommands(tree, "stl", "zh-CN")), ["model.export_stl"]);
  assert.deepEqual(ids(rankCommands(tree, "rhino", "en")), ["model.export_3dm"], "a command's own alias");
  assert.deepEqual(ids(rankCommands(tree, "平面图", "zh-CN")), ["drawing.plan_new"], "its group's terms");
  assert.deepEqual(ids(rankCommands(tree, "plan_new", "en")), ["drawing.plan_new"], "the id");
});

test("a group's goals speak for its command only when it has one", () => {
  const pages: CommandTree = {
    groups: [{ id: "shell.pages", owner: "hub.shell", kind: "shell", status: "PRODUCTION",
      purpose: { en: "Open a page", "zh-CN": "打开整页" }, terms: ["open drawings", "打开图纸", "see usage"] }],
    commands: [
      command("view.drawings", "Drawings", "图纸", { group: "shell.pages" }),
      command("view.usage", "Usage", "用量", { group: "shell.pages" }),
    ],
  };
  assert.deepEqual(ids(rankCommands(pages, "drawings", "en")), ["view.drawings"]);
  assert.deepEqual(ids(rankCommands(pages, "打开图纸", "zh-CN")), [], "a sibling's goal is not this command's");
});

test("every part has to match, and the shown label outranks the rest", () => {
  assert.deepEqual(ids(rankCommands(tree, "导出 3dm", "zh-CN")), ["model.export_3dm"]);
  assert.deepEqual(ids(rankCommands(tree, "导出　stl", "zh-CN")), ["model.export_stl"], "a full-width space separates too");
  assert.deepEqual(ids(rankCommands(tree, "export", "en")).slice(0, 2), ["model.export_3dm", "model.export_stl"]);
  assert.deepEqual(rankCommands(tree, "导出 楼梯", "zh-CN"), []);
  assert.deepEqual(queryParts(" Export，3DM "), ["export", "3dm"]);
});

test("a background command is the machine's, so the palette never offers it", () => {
  assert.equal(ids(rankCommands(tree, "参考", "zh-CN")).length, 0);
  assert.ok(!ids(rankCommands(tree, "", "zh-CN")).includes("view.reference_model"));
});

test("a command that needs arguments is not offered bare", () => {
  assert.equal(ids(rankCommands(tree, "打开对话", "zh-CN")).length, 0);
  assert.ok(!ids(rankCommands(tree, "", "en")).includes("chat.open_conversation"));
});

test("an empty palette leads with what was used last, then the tree in order", () => {
  const ranked = rankCommands(tree, "", "en", ["drawing.plan_new", "missing.command", "model.export_stl"]);
  assert.deepEqual(ids(ranked), ["drawing.plan_new", "model.export_stl", "model.export_3dm"]);
  assert.deepEqual(ranked.map((row) => row.recent), [true, true, false]);
  const many = Array.from({ length: 9 }, () => "model.export_3dm");
  assert.ok(rankCommands(tree, "", "en", many).filter((row) => row.recent).length <= RECENT_SHOWN);
});

test("a recent command wins a tie", () => {
  assert.deepEqual(ids(rankCommands(tree, "导出", "zh-CN", ["model.export_stl"])), ["model.export_stl", "model.export_3dm"]);
});

const press = (code: string, more: Partial<KeyPress> = {}): KeyPress =>
  ({ code, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...more });

test("a shortcut is read from the physical key, so an input method or layout does not change it", () => {
  assert.equal(shortcutOf(press("KeyK", { ctrlKey: true })), PALETTE_SHORTCUT);
  assert.equal(shortcutOf(press("KeyK", { metaKey: true })), PALETTE_SHORTCUT, "Cmd is Ctrl");
  assert.equal(shortcutOf(press("Digit2", { altKey: true })), "Alt+2");
  assert.equal(shortcutOf(press("Numpad3", { altKey: true })), "Alt+3");
  assert.equal(shortcutOf(press("KeyP", { altKey: true })), "Alt+P");
  assert.equal(shortcutOf(press("KeyZ", { ctrlKey: true, shiftKey: true })), "Ctrl+Shift+Z");
  assert.equal(shortcutOf(press("ShiftLeft", { shiftKey: true })), null);
  assert.equal(keyName("Escape"), "Esc");
  assert.equal(normalizeShortcut("alt + p"), "Alt+P");
  assert.equal(normalizeShortcut("cmd+k"), PALETTE_SHORTCUT);
  assert.equal(normalizeShortcut("Esc"), "Esc");
});

test("only Ctrl+K reaches into a text field; the rest wait until typing stops", () => {
  const field = { tagName: "INPUT", type: "text" };
  const area = { tagName: "TEXTAREA" };
  const editor = { tagName: "DIV", isContentEditable: true };
  const button = { tagName: "INPUT", type: "checkbox" };
  for (const target of [field, area, editor]) {
    assert.ok(typesText(target));
    assert.ok(hubMayTake(press("KeyK", { ctrlKey: true }), PALETTE_SHORTCUT, target));
    assert.ok(!hubMayTake(press("KeyP", { altKey: true }), "Alt+P", target));
  }
  assert.ok(!typesText(button));
  assert.ok(hubMayTake(press("KeyP", { altKey: true }), "Alt+P", button));
  assert.ok(!hubMayTake(press("KeyK", { ctrlKey: true, isComposing: true }), PALETTE_SHORTCUT, null), "mid-composition");
  assert.ok(!hubMayTake(press("Digit1", { altKey: true, repeat: true }), "Alt+1", null), "a held key");
});

const memory = () => {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => void values.set(key, value), values };
};

test("uses are counted per command and per way it was reached, most recent first", () => {
  const store = memory();
  recordUse(store, "model.export_3dm", "palette");
  recordUse(store, "drawing.plan_new", "button");
  const usage = recordUse(store, "model.export_3dm", "shortcut");
  assert.deepEqual(usage.recent, ["model.export_3dm", "drawing.plan_new"]);
  assert.deepEqual(usage.counts["model.export_3dm"], { palette: 1, shortcut: 1 });
  assert.equal(timesUsed(usage, "model.export_3dm"), 2);
  assert.deepEqual(readUsage(store), usage);
  assert.deepEqual(Object.keys(JSON.parse(store.values.get(USAGE_KEY)!)).sort(), ["counts", "recent"], "ids and counts only");
});

test("the recent list is bounded, and a broken or refusing store only loses counts", () => {
  const store = memory();
  for (let n = 0; n < RECENT_KEPT + 5; n += 1) recordUse(store, `c${n}`, "palette");
  assert.equal(readUsage(store).recent.length, RECENT_KEPT);
  store.setItem(USAGE_KEY, "{not json");
  assert.deepEqual(readUsage(store), { counts: {}, recent: [] });
  const refusing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("quota"); } };
  assert.equal(recordUse(refusing, "c1", "palette").recent[0], "c1");
  assert.deepEqual(readUsage(null), { counts: {}, recent: [] });
});
