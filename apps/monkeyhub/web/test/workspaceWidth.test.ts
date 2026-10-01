/**
 * #283: the Hub frame beside a project workspace. The projects list folds unless it is pinned, Focus folds
 * the conversation as well, and the conversation keeps its own width while the workspace takes the rest.
 * The widths drawn at 1440 px are measured in chatShell.browser.mjs.
 */
import assert from "node:assert/strict";
import test, { after } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const vite = await createServer({
  root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
  logLevel: "silent", server: { middlewareMode: true, watch: null },
});
after(() => vite.close());
const { shellFrame, chatWidthWithin } = await vite.ssrLoadModule("/src/ChatShell.tsx") as typeof import("../src/ChatShell.tsx");

const frame = { workspace: false, narrow: false, pinned: false as boolean | null, focus: false, open: true, peek: null as boolean | null };

test("a project workspace folds the projects list unless it is pinned", () => {
  for (const pinned of [false, true, null]) for (const open of [true, false]) {
    assert.equal(shellFrame({ ...frame, pinned, open }).sidebar, open, "without a workspace the list stays as it was left");
  }
  assert.deepEqual(shellFrame({ ...frame, workspace: true }), { mode: "auto", focused: false, sidebar: false });
  assert.equal(shellFrame({ ...frame, workspace: true, peek: true }).sidebar, true, "opened by hand, it stays open beside the workspace");
  assert.deepEqual(shellFrame({ ...frame, workspace: true, pinned: true }), { mode: "open", focused: false, sidebar: true });
  assert.equal(shellFrame({ ...frame, workspace: true, pinned: true, open: false }).sidebar, false, "a pinned list closed by hand stays closed");
  assert.equal(shellFrame({ ...frame, workspace: true, pinned: null }).sidebar, true, "nothing folds before the pin is read");
});

test("Focus folds the conversation and the projects list, pinned or not, only beside a workspace on a wide window", () => {
  for (const pinned of [false, true]) {
    assert.deepEqual(shellFrame({ ...frame, workspace: true, pinned, focus: true }), { mode: "focus", focused: true, sidebar: false });
    assert.equal(shellFrame({ ...frame, workspace: true, pinned, focus: true, peek: true }).sidebar, true);
  }
  assert.deepEqual(shellFrame({ ...frame, focus: true }), { mode: "open", focused: false, sidebar: true }, "no workspace, no Focus");
  assert.deepEqual(shellFrame({ ...frame, workspace: true, narrow: true, focus: true }), { mode: "auto", focused: false, sidebar: false },
    "below 900 px the workspace covers the conversation, and Focus does not apply");
});

test("the conversation keeps its width beside a workspace, within its floor and the workspace's", () => {
  // 1304 px between a folded projects list and the rail of a 1440 px window.
  assert.equal(chatWidthWithin(420, 1304), 420);
  assert.equal(chatWidthWithin(419.6, 1304), 420, "a dragged width is drawn in whole pixels");
  assert.equal(chatWidthWithin(120, 1304), 360, "the conversation never shrinks past 360 px");
  assert.equal(chatWidthWithin(1200, 1304), 1304 - 5 - 320, "nor leaves the workspace under 320 px");
  assert.equal(chatWidthWithin(420, 600), 360, "in a narrow window the conversation's floor comes first");
});
