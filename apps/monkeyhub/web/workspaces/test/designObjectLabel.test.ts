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
});
