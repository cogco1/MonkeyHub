import assert from "node:assert/strict";
import test from "node:test";

import {
  applicationUrl, applyAppearance, appearanceFromSearch, DEFAULT_APPEARANCE, isUiStyle, resolveAppearance, uiFamily,
} from "../../../../packages/web-shared/src/appearance.js";

test("the four saved styles resolve; Critique Table waits for the settings to accept it", () => {
  for (const style of ["classic", "quiet", "titleblock", "night"]) assert.ok(isUiStyle(style), style);
  for (const style of ["board", "neon"]) assert.equal(resolveAppearance({ uiStyle: style }).uiStyle, DEFAULT_APPEARANCE.uiStyle, style);
});

test("each style belongs to the family its component rules key on", () => {
  assert.deepEqual(["classic", "quiet", "titleblock", "night", "board", undefined].map((style) => uiFamily(style)),
    ["classic", "instrument", "instrument", "instrument", "board", "classic"]);
});

test("an application opened from the Hub inherits the style", () => {
  const url = applicationUrl("http://127.0.0.1:8765/studio/", { ...DEFAULT_APPEARANCE, uiStyle: "titleblock" });
  assert.equal(appearanceFromSearch(new URL(url).search).uiStyle, "titleblock");
});

test("applying an appearance names both the style and its family on the root", () => {
  const dataset: Record<string, string> = {};
  const properties = new Map<string, string>();
  const root = { dataset, lang: "", style: { setProperty: (name: string, value: string) => properties.set(name, value) } };
  const previous = (globalThis as { document?: unknown }).document;
  (globalThis as { document?: unknown }).document = { documentElement: root };
  try {
    for (const [style, family] of [["night", "instrument"], ["classic", "classic"]] as const) {
      applyAppearance({ ...DEFAULT_APPEARANCE, uiStyle: style });
      assert.deepEqual([dataset.uiStyle, dataset.uiFamily], [style, family]);
    }
    assert.equal(properties.get("--font-scale"), "1");
  } finally {
    (globalThis as { document?: unknown }).document = previous;
  }
});
