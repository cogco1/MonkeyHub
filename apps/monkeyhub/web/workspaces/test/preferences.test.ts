import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

test("workspace appearance follows the host and never reads or persists display defaults", async t => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { UserPreferencesProvider, usePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    get localStorage() { throw new Error("Workspace display must not access storage"); },
    get location() { throw new Error("Workspace display must not choose launch appearance"); },
  } });
  t.after(() => originalWindow ? Object.defineProperty(globalThis, "window", originalWindow) : Reflect.deleteProperty(globalThis, "window"));
  function Consumer() {
    const preferences = usePreferences();
    assert.equal("setLanguage" in preferences, false);
    return createElement("span", null, `${preferences.language}/${preferences.theme}/${preferences.fontScale}`);
  }
  for (const appearance of [
    { language: "en", theme: "light", fontScale: 1 },
    { language: "zh-CN", theme: "dark", fontScale: 1.1 },
  ]) {
    assert.equal(renderToStaticMarkup(createElement(UserPreferencesProvider, { appearance }, createElement(Consumer))),
      `<span>${appearance.language}/${appearance.theme}/${appearance.fontScale}</span>`);
  }
});

test("editing pointers preserve old records and new writes do not create appearance defaults", async t => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { editingBasePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  let stored: string | null = null;
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { localStorage: {
    getItem: () => stored, setItem: (_key: string, value: string) => { stored = value; },
  } } });
  t.after(() => originalWindow ? Object.defineProperty(globalThis, "window", originalWindow) : Reflect.deleteProperty(globalThis, "window"));
  assert.equal(editingBasePreferences.write("runtime-a", "project-a", "run-a"), true);
  assert.deepEqual(JSON.parse(stored!), { version: 1, editingBases: { '["runtime-a","project-a"]': "run-a" } });
  assert.equal(editingBasePreferences.read("runtime-b", "project-a"), null);
  assert.equal(editingBasePreferences.read("runtime-a", "project-b"), null);
  const old = { version: 1, language: "zh-CN", theme: "dark", fontScale: 1.1, eventStreamVisible: false, editingBases: JSON.parse(stored!).editingBases };
  stored = JSON.stringify(old);
  assert.equal(editingBasePreferences.read("runtime-a", "project-a"), "run-a");
  assert.deepEqual(editingBasePreferences.readChoice("runtime-a", "project-a"), { runId: "run-a" });
  assert.equal(editingBasePreferences.write("runtime-a", "project-a", "run-next"), true);
  assert.deepEqual(JSON.parse(stored!), { ...old, editingBases: { '["runtime-a","project-a"]': "run-next" } });
  stored = "{ damaged";
  assert.throws(() => editingBasePreferences.read("runtime-a", "project-a"), /could not be read/);
  assert.throws(() => editingBasePreferences.write("runtime-a", "project-a", "run-third"), /could not be read/);
  assert.equal(stored, "{ damaged");
});

test("exact Stage choices reopen within their runtime and project without rewriting storage", async t => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { editingBasePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  let stored: string | null = null;
  let writes = 0;
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: { localStorage: {
    getItem: () => stored, setItem: (_key: string, value: string) => { stored = value; writes++; },
  } } });
  t.after(() => originalWindow ? Object.defineProperty(globalThis, "window", originalWindow) : Reflect.deleteProperty(globalThis, "window"));

  const historical = { runId: "run-s0", sourceStageRef: "stage-s0", branchId: "alternate" };
  const other = { runId: "run-s1", sourceStageRef: "stage-s1", branchId: "main" };
  assert.equal(editingBasePreferences.writeChoice("runtime-a", "project-a", historical), true);
  assert.equal(editingBasePreferences.writeChoice("runtime-a", "project-b", other), true);
  assert.equal(editingBasePreferences.write("runtime-b", "project-a", "legacy-run"), true);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-a", "project-a"), historical);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-a", "project-b"), other);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-b", "project-a"), { runId: "legacy-run" });
  assert.equal(editingBasePreferences.read("runtime-a", "project-a"), "run-s0");
  assert.equal(editingBasePreferences.readChoice("runtime-b", "project-b"), null);
  assert.equal(writes, 3, "restoring a saved choice does not mutate the record");

  assert.equal(editingBasePreferences.writeChoice("runtime-a", "project-a", null), true);
  assert.equal(editingBasePreferences.readChoice("runtime-a", "project-a"), null);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-a", "project-b"), other);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-b", "project-a"), { runId: "legacy-run" });
  const unversioned = { runId: "unversioned-run", sourceStageRef: null, branchId: null };
  assert.equal(editingBasePreferences.writeChoice("runtime-a", "project-a", unversioned), true);
  assert.deepEqual(editingBasePreferences.readChoice("runtime-a", "project-a"), unversioned);

  for (const invalid of [
    { runId: "run-s0", sourceStageRef: 1, branchId: "alternate" },
    { runId: "run-s0", sourceStageRef: "stage-s0", branchId: " " },
    { runId: "", sourceStageRef: "stage-s0" },
  ]) {
    stored = JSON.stringify({ version: 1, editingBases: { '["runtime-a","project-a"]': invalid } });
    const before = stored;
    assert.throws(() => editingBasePreferences.readChoice("runtime-a", "project-a"), /unreadable/);
    assert.equal(stored, before, "an invalid saved exact choice must not silently select a default");
    assert.throws(() => editingBasePreferences.writeChoice("runtime-a", "project-a", invalid), /unreadable/);
    assert.equal(stored, before);
  }
  for (const invalidRecord of [null, [], { version: 2 }, { version: 1, editingBases: [] }]) {
    stored = JSON.stringify(invalidRecord);
    const before = stored;
    assert.throws(() => editingBasePreferences.readChoice("runtime-a", "project-a"), /unreadable|could not be read/);
    assert.throws(() => editingBasePreferences.writeChoice("runtime-a", "project-a", historical), /unreadable|could not be read/);
    assert.equal(stored, before, "a damaged record is not overwritten");
  }
});
