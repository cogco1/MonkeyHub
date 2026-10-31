import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";

test("design mode labels an identified object without displaying its raw id", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { designObjectLabel } = await vite.ssrLoadModule("/src/app/format.ts");
  assert.equal(designObjectLabel("south-east-column-return-finish"), "South East Column Return Finish");
  assert.equal(designObjectLabel(null), null);
  // A design made from an imported model (#300 item 22 B): its components and objects read as the part they are.
  assert.equal(designObjectLabel("imported-wall-45", true), "墙 45");
  assert.equal(designObjectLabel("imported-private-room", true), "个人房间");
  assert.equal(designObjectLabel("imported-stair-3", false), "Stair 3");
  assert.equal(designObjectLabel("imported-annex-2"), "Imported Annex 2");
});
