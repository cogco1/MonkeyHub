import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

import { COMMAND_TREE } from "../src/features/commands/tree.generated.ts";
import { MODEL_TOOLS, MORE_SKETCH_TOOLS, SKETCH_TOOLS, STANDARD_VIEWS, draftToolCommand } from "../src/features/stage/tools.ts";
import { DOCUMENT_TOOLS } from "../src/workspaces/monkeydiagram/documentTools.ts";
import { GESTURE_TOOLS } from "../src/workspaces/monkeyarch/gestureTools.ts";
import { RENDER_VIEWS } from "../src/workspaces/render/renderViews.ts";

// Every command a control renders is in the command tree, so Ctrl+K and the advanced tools reach
// every control on the page and none of them is a second list (plan 1.5, 2-1a).

const SOURCE = new URL("../src/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const registered = new Set(COMMAND_TREE.commands.map((command) => command.id));

function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return entry.name === "node_modules" ? [] : sources(path);
    return entry.name.endsWith(".tsx") ? [path] : [];
  });
}

test("every literal data-command in the web source names a registered command", () => {
  const missing: string[] = [];
  for (const path of sources(SOURCE)) {
    const text = readFileSync(path, "utf8");
    for (const match of text.matchAll(/\b(?:data-command=|command=|command:\s*)"([a-z][a-z0-9_.]*)"/g)) {
      if (!registered.has(match[1])) missing.push(`${relative(SOURCE, path)}: ${match[1]}`);
    }
  }
  assert.deepEqual(missing, []);
});

test("every command a list of tools or views renders is registered", () => {
  const rendered = [
    ...STANDARD_VIEWS.map((view) => `view.model_camera.${view}`),
    ...[...SKETCH_TOOLS, ...MORE_SKETCH_TOOLS, ...MODEL_TOOLS].map(draftToolCommand),
    ...GESTURE_TOOLS.map((tool) => `view.markup_tools.${tool.kind}`),
    ...DOCUMENT_TOOLS.map((tool) => `view.markup_tools.${tool}`),
    ...RENDER_VIEWS.map((view) => `view.render.${view}`),
  ];
  assert.deepEqual(rendered.filter((id) => !registered.has(id)), []);
});
