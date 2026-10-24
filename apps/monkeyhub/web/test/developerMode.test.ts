import assert from "node:assert/strict";
import test from "node:test";

import { DEVELOPER_DEFAULTS, developerPreferences } from "../src/features/settings/developer.ts";

test("developer mode starts off, with the event stream it shows on", () => {
  assert.deepEqual(developerPreferences(null), { mode: false, eventStream: true });
  assert.deepEqual(DEVELOPER_DEFAULTS, { mode: false, eventStream: true });
});

test("the saved switches are read from their own key", () => {
  assert.deepEqual(developerPreferences({ version: 1, developer: { mode: true, eventStream: false } }), { mode: true, eventStream: false });
  assert.deepEqual(developerPreferences({ version: 1, developer: { mode: true } }), { mode: true, eventStream: true });
});

test("a legacy display field does not switch anything", () => {
  const legacy = { version: 1, language: "zh-CN", theme: "light", fontScale: 1.1, eventStreamVisible: false, developerMode: true };
  assert.deepEqual(developerPreferences(legacy), DEVELOPER_DEFAULTS);
});

test("an unreadable saved value is the default, never a guess", () => {
  for (const developer of ["on", 1, [true], null]) {
    assert.deepEqual(developerPreferences({ version: 1, developer }), DEVELOPER_DEFAULTS, String(developer));
  }
  assert.deepEqual(developerPreferences({ version: 1, developer: { mode: "yes", eventStream: 0 } }), { mode: false, eventStream: true });
  assert.deepEqual(developerPreferences("not a record"), DEVELOPER_DEFAULTS);
});
