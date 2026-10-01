/**
 * The component information card as drawn (#549): each item shows its key information, and what
 * explains it (a note, a link, a source, a section's or group's notes) folds under a small arrow,
 * shut until asked. Synthetic fixtures only (test/fixtures/component-info).
 */

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";

import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

type Info = typeof import("../src/features/componentInfo/componentInfo.ts");
type Board = typeof import("../src/features/componentInfo/boardDatasets.ts");
type Card = typeof import("../src/features/componentInfo/ComponentInfoCard.tsx");

const fixture = async (name: string) => readFile(new URL(`./fixtures/component-info/${name}.json`, import.meta.url), "utf8");
const SHOWN_B = { projectId: "fixture-project", runId: "run-b", stateDigest: "b".repeat(64) };
const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);

/**
 * The text a reader sees before opening anything: what lies outside every element marked `hidden`,
 * and of a shut `<details>` (the technical section) only its summary.
 */
function visibleText(markup: string): string {
  const shown: string[] = [];
  const hidden: boolean[] = [];
  let depth = 0;
  const unopened = markup.replace(/<details\b(?![^>]*\sopen)[^>]*>\s*(<summary\b[\s\S]*?<\/summary>)[\s\S]*?<\/details>/gi, "$1");
  for (const [, closing, name, attributes, selfClosing, text] of unopened.matchAll(/<(\/)?([a-z][a-z0-9]*)\b([^>]*?)(\/)?>|([^<]+)/gi)) {
    if (text !== undefined) {
      if (depth === 0) shown.push(text);
    } else if (closing) {
      if (hidden.pop()) depth -= 1;
    } else if (!selfClosing && !VOID.has(name!.toLowerCase())) {
      const folded = /\shidden(=|\s|$)/.test(attributes!);
      hidden.push(folded);
      if (folded) depth += 1;
    }
  }
  return shown.join(" ");
}

async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const info = await vite.ssrLoadModule("/src/features/componentInfo/componentInfo.ts") as Info;
  const board = await vite.ssrLoadModule("/src/features/componentInfo/boardDatasets.ts") as Board;
  const { ComponentInfoPanel } = await vite.ssrLoadModule("/src/features/componentInfo/ComponentInfoCard.tsx") as Card;
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const { messagesEn } = await vite.ssrLoadModule("/src/i18n/messages.en.ts") as typeof import("../src/i18n/messages.en.ts");
  const en = (key: string, parameters?: Readonly<Record<string, string | number>>) =>
    (messagesEn as Record<string, string>)[key]!.replace(/\{(\w+)\}/g, (_, name: string) => String(parameters?.[name] ?? `{${name}}`));
  const datasets = (await Promise.all(["basics", "supply"].map(fixture))).flatMap((text) => info.parseDatasetImport(text));
  const elements = datasets.map((dataset) => ({ id: board.cardElementId(dataset.id), type: "rectangle",
    customData: { [board.COMPONENT_INFO_KEY]: dataset } }));
  const ready = { status: "ready" as const, projectId: "fixture-project", revisionSha256: "r".repeat(64), ...board.readBoardDatasets(elements) };
  const card = info.buildComponentCard({ componentId: "post-1", elementId: "post-1-body", modelLabel: "Post 1",
    shown: SHOWN_B, pickMatchesShown: true, board: ready }, ((status: string) => en(`componentInfo.status.${status}`)) as never);
  const draw = (shown: typeof card) => renderToStaticMarkup(createElement(UserPreferencesProvider as (props: { appearance: unknown; children: ReactNode }) => ReactNode,
    { appearance: { language: "en", theme: "light", fontScale: 1 } },
    createElement(ComponentInfoPanel, { subject: { kind: "card", card: shown }, onClose() {} })));
  return { info, card, draw, en };
}

test("each item shows its key information and folds its notes, links and sources", async (t) => {
  const { card, draw } = await harness(t);
  assert.equal(card.state, "ready");
  const markup = draw(card);
  const seen = visibleText(markup);

  for (const value of ["timber", "1:4", "pieces", "stock A, ripped", "Timber stock A", "Example Timber", "5 pieces $25", "Budget estimate"]) {
    assert.ok(seen.includes(value), `${value} is shown`);
  }
  for (const folded of ["building part", "parts list (model inches)", "Allowances only, not quotes.", "https://example.com/stock-a",
    "supplier page", "initial budget sheet", "Shared stock; no cost per piece is allocated.", "Copy link"]) {
    assert.ok(markup.includes(folded), `${folded} is in the card`);
    assert.ok(!seen.includes(folded), `${folded} is folded`);
  }

  // One arrow for each item that has something to fold: Scale (note), Made size (source), the supply
  // section's note, the stock group's note, Supplier (link and source) and Budget (source).
  const toggles = [...markup.matchAll(/<button[^>]*class="component-info__fold"[^>]*>/g)].map(([tag]) => tag);
  assert.equal(toggles.length, 6);
  for (const toggle of toggles) {
    assert.match(toggle, /aria-expanded="false"/);
    assert.match(toggle, /aria-label="Show notes"/);
    const controls = /aria-controls="([^"]+)"/.exec(toggle)![1]!;
    assert.match(markup, new RegExp(`id="${controls.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"[^>]*\\shidden=""`), `${controls} starts shut`);
  }
  assert.equal(new Set(toggles.map((toggle) => /aria-controls="([^"]+)"/.exec(toggle)![1])).size, 6, "every arrow opens its own notes");
  // Items without anything to explain carry no arrow.
  assert.doesNotMatch(markup, /stock A, ripped<\/span><button/);
});

test("a block without a heading names its fold, and Copy keeps what is folded", async (t) => {
  const { info, card, draw, en } = await harness(t);
  const noted = { ...card, summary: card.summary.map((block) => ({ ...block, note: "Model inches throughout." })) };
  const markup = draw(noted);
  const seen = visibleText(markup);

  assert.ok(markup.includes("Model inches throughout."));
  assert.ok(!seen.includes("Model inches throughout."), "the summary's note is folded");
  assert.match(markup, /<button[^>]*class="component-info__fold"[^>]*aria-expanded="false"[^>]*>Notes<svg/, "its arrow says what it opens");

  const text = info.componentCardText(card, en as never);
  for (const folded of ["building part", "Allowances only, not quotes.", "https://example.com/stock-a", "Shared stock; no cost per piece is allocated."]) {
    assert.ok(text.includes(folded), `Copy keeps ${folded}`);
  }
});
