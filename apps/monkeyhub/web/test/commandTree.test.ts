import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { normalizeShortcut, PALETTE_SHORTCUT } from "../src/features/commands/keys.ts";
import { COMMAND_TREE } from "../src/features/commands/tree.generated.ts";
// @ts-expect-error -- a plain .mjs module without declarations
import { render } from "../scripts/command-tree.mjs";

const ID = /^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$/;
const SECTIONS = new Set(["shell", "settings", "project_bar", "modeling", "board", "layout", "document", "drawings", "render",
  "design_tree", "fabrication", "usage"]);
const SURFACES = new Set(["hub", ...[...SECTIONS].filter((section) => !["shell", "settings", "project_bar"].includes(section))]);

// Keys the browser, Rhino 8, the Board's Excalidraw, Windows and the input method already use
// (02 常用操作提速, "避开了哪些键"). Ctrl+K is taken from Excalidraw on purpose (ADR-014 point 6).
const RESERVED = new Set([
  ..."TWNRLDFPSHJOU".split("").map((key) => `Ctrl+${key}`), ..."123456789".split("").map((key) => `Ctrl+${key}`),
  ..."NPTIJ".split("").map((key) => `Ctrl+Shift+${key}`), "Ctrl+Shift+Delete",
  ..."DEFVSBTH".split("").map((key) => `Alt+${key}`), "Alt+Left", "Alt+Right", "Alt+Home", "Alt+Space", "Alt+Esc", "Alt+F4",
  ..."ACVXZYSONPGHJLT".split("").map((key) => `Ctrl+${key}`), "Ctrl+Shift+G", "Ctrl+Alt+H", "Ctrl+Shift+Z",
  ...Array.from({ length: 12 }, (_, n) => `F${n + 1}`), "Home", "End", "PageUp", "PageDown",
  ..."RSVZ".split("").map((key) => `Alt+${key}`), "Alt+/", "Alt+Shift+C", "Alt+Shift+D", "Ctrl+'", "Ctrl+[", "Ctrl+]",
  "Shift+1", "Shift+2", "Shift+3", "Ctrl+Space", "Ctrl+.", "Shift+Space", "Ctrl+Esc", "Ctrl+Shift+Esc",
]);

test("the committed tree is the registry's current view", () => {
  const registry = JSON.parse(readFileSync(new URL("../../../../governance/module_registry.json", import.meta.url), "utf8"));
  const committed = readFileSync(new URL("../src/features/commands/tree.generated.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  assert.equal(committed, render(registry), "run `npm run api:generate`");
});

test("every command has one well-formed id, a group, a section and a surface the palette knows", () => {
  const groups = new Set(COMMAND_TREE.groups.map((group) => group.id));
  const seen = new Set<string>();
  for (const command of COMMAND_TREE.commands) {
    assert.match(command.id, ID, command.id);
    assert.ok(!seen.has(command.id), `${command.id} is registered twice`);
    seen.add(command.id);
    assert.ok(groups.has(command.group), `${command.id}: group ${command.group}`);
    assert.ok(SECTIONS.has(command.section), `${command.id}: section ${command.section}`);
    assert.ok(SURFACES.has(command.surface), `${command.id}: surface ${command.surface}`);
    assert.ok(command.label.en.trim() && command.label["zh-CN"].trim(), `${command.id}: both labels`);
    assert.notEqual(command.label.en, command.label["zh-CN"], `${command.id}: the Chinese label is not the English one`);
  }
  for (const group of COMMAND_TREE.groups) assert.match(group.id, ID, group.id);
});

test("a shortcut has a modifier, is the only one of its kind, and stays clear of keys already taken", () => {
  const taken = new Map<string, string>();
  for (const command of COMMAND_TREE.commands) {
    if (!command.shortcut) continue;
    const shortcut = normalizeShortcut(command.shortcut);
    assert.equal(command.shortcut, shortcut, `${command.id}: write it as ${shortcut}`);
    assert.notEqual(shortcut, PALETTE_SHORTCUT, `${command.id}: Ctrl+K opens the palette`);
    assert.ok(/^(Ctrl|Alt)\+/.test(shortcut), `${command.id}: a bare key would fire while typing`);
    assert.ok(!RESERVED.has(shortcut), `${command.id}: ${shortcut} is already taken`);
    assert.ok(!taken.has(shortcut), `${command.id} and ${taken.get(shortcut)} share ${shortcut}`);
    taken.set(shortcut, command.id);
  }
});
