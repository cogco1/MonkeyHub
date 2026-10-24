import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

test("a result offers download, the Board and the layout, in that order, and holds them while it is unsaved (ADR-014 B5)", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { ResultActions } = await vite.ssrLoadModule("/src/features/results/ResultActions.tsx");
  const { UserPreferencesProvider } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const render = (props: Record<string, unknown>, language = "en") => renderToStaticMarkup(
    createElement(UserPreferencesProvider, { appearance: { language, theme: "light", fontScale: 1 } }, createElement(ResultActions, props)));
  const commands = (html: string) => [...html.matchAll(/data-command="([^"]+)"/g)].map((match) => match[1]);
  const go = () => undefined;

  const file = { command: "view.render.download", label: "Download", href: "blob:result", fileName: "result.png" };
  const ready = render({ download: file, onBoard: go, onLayout: go });
  assert.deepEqual(commands(ready), ["view.render.download", "board.place_image", "view.layout.place_result"]);
  assert.match(ready, /<a data-command="view.render.download" href="blob:result" download="result.png">Download<\/a>/);
  assert.doesNotMatch(ready, /disabled/);
  assert.match(render({ download: file, onBoard: go, onLayout: go }, "zh-CN"), />送到画板<.*>放进排版</);

  // A file still being read cannot be followed yet; an unsaved result leaves through none of the three.
  assert.match(render({ download: { ...file, href: null }, onBoard: go }), /<button data-command="view.render.download" type="button" disabled="">/);
  const unsaved = render({ download: { command: "drawing.export.svg", label: "Download SVG", onDownload: go }, onBoard: go, onLayout: go, disabled: true });
  assert.equal(unsaved.match(/disabled=""/g)?.length, 3);

  // A page with no file of its own for this result, or no layout to put it in, offers only what it can.
  assert.deepEqual(commands(render({ onBoard: go, onLayout: go })), ["board.place_image", "view.layout.place_result"]);
  assert.deepEqual(commands(render({ download: file, onBoard: go })), ["view.render.download", "board.place_image"]);
});
