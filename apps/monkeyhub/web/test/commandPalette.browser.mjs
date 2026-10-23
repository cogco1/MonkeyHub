import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";
import { workspaceFixture } from "./workspaceFixture.mjs";

// Ctrl+K over the real Board and Excalidraw, with the real command tree. The fixture shell
// registers a few of the tree's commands the way ChatShell does, plus one command that lives
// on another surface, so running it has to open that surface first.
// COMMAND_PALETTE_SHOTS=<dir> also saves the palette at desktop and phone width, light and dark.
const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const cacheDir = await mkdtemp(path.join(tmpdir(), "command-palette-"));
const shots = process.env.COMMAND_PALETTE_SHOTS ? path.resolve(process.env.COMMAND_PALETTE_SHOTS) : null;
const projectId = "command-palette-fixture";
const board = { projectId, title: "Review board", elements: [], seenDocuments: [], revisionSha256: "b".repeat(64) };
const words = {
  "zh-CN": { create: "新建项目", createLabel: "新建项目…", drawings: "图纸", reason: "先打开一个项目", ask: "楼梯怎么画", askRow: "让 AI 做：楼梯怎么画",
    recent: "最近用过", other: "在模型里改", opens: "在建模里", standing: "只有按钮的功能", locate: "在页面上指给你" },
  en: { create: "new project", createLabel: "New project…", drawings: "drawings", reason: "Open a project first", ask: "how to draw stairs",
    askRow: "Ask the AI: how to draw stairs", recent: "Recently used", other: "change in model", opens: "Opens Modeling",
    standing: "button only function", locate: "Points to it on the page" },
};
const escaped = [];
let vite, browser;
const http = createHttpServer();
try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] },
    logLevel: "error", cacheDir, publicDir: ".generated/public",
    define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [workspaceFixture(), { name: "command-palette-fixture", enforce: "pre", transform(source, id) {
      const filename = id.split("?")[0].replaceAll("\\", "/"), root = webRoot.replaceAll("\\", "/");
      if (filename === `${root}/src/workspaces/monkeyboard/Board.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; }}";
        assert.equal(source.split(callback).length, 2, "Expose the existing Excalidraw callback exactly once");
        return { code: source.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; window.__boardApi = api; }}"), map: null };
      }
      if (filename !== `${root}/test/workspace-fixture.tsx`) return;
      return { code: `
        import { useState } from "react";
        import { createRoot } from "react-dom/client";
        import { convertToExcalidrawElements, CaptureUpdateAction } from "@excalidraw/excalidraw";
        import Board from "/src/workspaces/monkeyboard/Board";
        import { UserPreferencesProvider } from "/test/TestProviders.tsx";
        import { CommandProvider, openSurfaceAction, useCommandActions } from "/src/features/commands/CommandProvider";
        import { COMMAND_TREE } from "/src/features/commands/tree.generated";
        import "/src/styles.css";
        import "/src/app/styles.css";
        window.__ran = [];
        window.__boardHelpers = { convertToExcalidrawElements, CaptureUpdateAction };
        const reason = (zh) => zh ? ${JSON.stringify(words["zh-CN"].reason)} : ${JSON.stringify(words.en.reason)};
        const fixture = { terms: [], channel: "side", tier: "basic", place: null, when: [], shortcut: null, entries: [], args: [] };
        const tree = { capabilities: [...COMMAND_TREE.capabilities], commands: [...COMMAND_TREE.commands, { ...fixture, id: "fixture.modeling_change", capability: "shell.pages",
          label: { en: ${JSON.stringify(words.en.other)}, "zh-CN": ${JSON.stringify(words["zh-CN"].other)} }, surface: "modeling", group: "modeling" },
          { ...fixture, id: "fixture.standing_only", capability: "shell.pages",
          label: { en: ${JSON.stringify(words.en.standing)}, "zh-CN": ${JSON.stringify(words["zh-CN"].standing)} }, surface: "hub", group: "shell" }] };
        function Modeling() {
          useCommandActions({ "fixture.modeling_change": { run: () => window.__ran.push("fixture.modeling_change") } });
          return <p className="fixture-modeling">Modeling</p>;
        }
        function Shell() {
          const [modeling, setModeling] = useState(false);
          useCommandActions({
            ...Object.fromEntries(COMMAND_TREE.commands.map((command) => [command.id, { run: () => window.__ran.push(command.id) }])),
            "project.create.new": { run: () => window.__ran.push("project.create.new") },
            "view.tab.board": { run: () => window.__ran.push("view.tab.board") },
            "view.drawings": { run: () => window.__ran.push("view.drawings"), available: () => reason(document.documentElement.lang === "zh-CN") },
            "chat.ask": { run: ({ text }) => window.__ran.push("ask:" + text) },
            [openSurfaceAction("modeling")]: { run: () => setModeling(true) },
          });
          return <div style={{ display: "grid", gridTemplateRows: "auto 1fr", height: "100vh" }}>
            <div><input aria-label="fixture field" /><button type="button" data-command="project.create.new">fixture standing entry</button>
              <button type="button" data-command="fixture.standing_only">fixture entry without a handler</button>
              <button type="button" role="menuitem" aria-disabled="true" data-command="project.archive.export">fixture unavailable menu item</button>
              <button type="button" disabled data-command="project.create.add_existing">fixture disabled button</button></div>
            {modeling ? <Modeling /> : <Board expectedProjectId=${JSON.stringify(projectId)}
              onSubmit={() => { throw new Error("Unexpected design handoff"); }} onSketch={() => { throw new Error("Unexpected sketch handoff"); }} />}
          </div>;
        }
        createRoot(document.getElementById("root")).render(<UserPreferencesProvider><CommandProvider tree={tree}><Shell /></CommandProvider></UserPreferencesProvider>);
      `, map: null };
    } }, react()], server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  http.on("request", (request, response) => {
    if (request.url?.startsWith("/api/")) { escaped.push(request.url); response.writeHead(405); response.end(); return; }
    vite.middlewares(request, response);
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  if (shots) await mkdir(shots, { recursive: true });

  for (const [language, theme, width, height] of [["zh-CN", "light", 1440, 900], ["en", "dark", 1440, 900], ["zh-CN", "dark", 390, 844], ["en", "light", 390, 844]]) {
    const label = `${language} ${theme} ${width}`;
    const text = words[language];
    const phone = width < 600;
    const context = await browser.newContext({ viewport: { width, height }, ...(phone ? { isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : {}) });
    await context.addInitScript(([language, theme]) => {
      localStorage.setItem("archflow-studio.user-preferences", JSON.stringify({ version: 1, language, theme }));
      window.EXCALIDRAW_ASSET_PATH = `${location.origin}/excalidraw/`;
    }, [language, theme]);
    await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
      escaped.push(route.request().url()); return route.abort("blockedbyclient");
    });
    const page = await context.newPage(); page.setDefaultTimeout(15_000);
    const errors = [];
    let saves = 0;
    page.on("pageerror", (error) => errors.push(error.message));
    // Chrome reports Excalidraw's unload listener as a policy violation; everything else logged as an error fails.
    page.on("console", (message) => {
      if (message.type() === "error" && !message.text().startsWith("Permissions policy violation: unload")) errors.push(message.text());
    });
    await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
      const request = route.request(), url = new URL(request.url());
      if (request.method() === "GET" && url.pathname === "/api/board") return route.fulfill({ json: board });
      if (request.method() === "GET" && url.pathname === "/api/documents") return route.fulfill({ json: { projectId, documents: [] } });
      // The rectangle placed for the Ctrl+K check is saved like any mark; the fixture keeps it.
      if (request.method() === "PUT" && url.pathname === "/api/board") {
        saves += 1;
        return route.fulfill({ json: { ...board, ...request.postDataJSON(), revisionSha256: String(saves).padStart(64, "c") } });
      }
      escaped.push(`${request.method()} ${url.pathname}`);
      return route.fulfill({ status: 405, json: { detail: "Unexpected fixture request" } });
    });
    const palette = page.locator("dialog.command-palette[open]");
    const input = palette.locator("input[role=combobox]");
    const ran = () => page.evaluate(() => [...window.__ran]);
    try {
      await page.goto(origin);
      await page.locator(".monkeyboard-canvas canvas").last().waitFor();
      await page.waitForFunction(() => !document.querySelector(".monkeyboard-initializing"));

      // On the Board the Hub takes Ctrl+K before Excalidraw, whose Ctrl+K edits the selected element's link.
      await page.waitForFunction(() => window.__boardApi);
      await page.evaluate(() => {
        const { convertToExcalidrawElements, CaptureUpdateAction } = window.__boardHelpers;
        window.__boardApi.updateScene({ elements: convertToExcalidrawElements([
          { type: "rectangle", id: "linkable", x: 120, y: 120, width: 120, height: 80, strokeColor: "#1971c2", roughness: 0 },
        ], { regenerateIds: false }), appState: { selectedElementIds: { linkable: true } }, captureUpdate: CaptureUpdateAction.NEVER });
      });
      await page.locator(".monkeyboard-canvas").focus().catch(() => undefined);
      await page.keyboard.press("Control+K");
      await palette.waitFor();
      assert.equal(await page.evaluate(() => window.__boardApi.getAppState().showHyperlinkPopup), false, `${label}: Excalidraw kept its link editor closed`);
      assert.equal(await input.evaluate((element) => element === document.activeElement), true, `${label}: typing goes to the palette`);
      if (shots) await page.screenshot({ path: path.join(shots, `palette-${language}-${theme}-${width}.png`) });

      // Typing a name finds the command; Enter runs it and closes the palette.
      await input.fill(text.create);
      const first = palette.locator("[role=option]").first();
      assert.equal(await first.locator(".command-palette__label").textContent(), text.createLabel, `${label}: best match first`);
      await input.press("Enter");
      await palette.waitFor({ state: "detached" });
      // The palette closes first and the command runs right after, once focus is free.
      await page.waitForFunction(() => window.__ran.length > 0);
      assert.deepEqual(await ran(), ["project.create.new"], `${label}: Enter ran the command`);

      // A command that cannot run here is listed greyed, with why; Enter does nothing.
      await page.keyboard.press("Control+K");
      await input.fill(text.drawings);
      const unavailable = palette.locator("[role=option][aria-disabled=true]").first();
      assert.equal(await unavailable.locator(".command-palette__meta").textContent(), text.reason, `${label}: the reason is shown`);
      if (shots) await page.screenshot({ path: path.join(shots, `palette-unavailable-${language}-${theme}-${width}.png`) });
      await input.press("Enter");
      assert.equal(await palette.count(), 1, `${label}: an unavailable command keeps the palette open`);
      assert.ok(!(await ran()).includes("view.drawings"));

      // Words that name no function go to the AI from the last row.
      await input.fill(text.ask);
      const ask = palette.locator(".command-palette__row--ask");
      assert.equal(await ask.textContent(), text.askRow, `${label}: the ask row`);
      if (shots) await page.screenshot({ path: path.join(shots, `palette-ask-${language}-${theme}-${width}.png`) });
      await input.press("Enter");
      await palette.waitFor({ state: "detached" });
      await page.waitForFunction((words) => window.__ran.includes(`ask:${words}`), text.ask);

      // An empty palette leads with what was used last.
      await page.keyboard.press("Control+K");
      assert.equal(await palette.locator(".command-palette__heading").first().textContent(), text.recent);
      assert.equal(await palette.locator("[role=option] .command-palette__label").first().textContent(), text.createLabel);
      await page.keyboard.press("Escape");
      await palette.waitFor({ state: "detached" });

      // A shortcut runs its command, except while focus is in a text field.
      await page.locator("body").click({ position: { x: 5, y: height - 5 } });
      await page.keyboard.press("Alt+2");
      assert.ok((await ran()).includes("view.tab.board"), `${label}: Alt+2`);
      const before = (await ran()).length;
      await page.getByLabel("fixture field").focus();
      await page.keyboard.press("Alt+2");
      assert.equal((await ran()).length, before, `${label}: Alt+2 while typing does nothing`);
      await page.keyboard.press("Control+K");
      await palette.waitFor();
      await page.keyboard.press("Escape");

      // A standing entry that names its command is counted beside the palette's uses; one that cannot act now is not.
      await page.getByText("fixture unavailable menu item").click({ force: true });
      await page.getByText("fixture disabled button").click({ force: true });
      await page.getByText("fixture standing entry").click();
      const usage = await page.evaluate(() => JSON.parse(localStorage.getItem("monkeyhub.commands.usage@1")));
      assert.deepEqual(usage.counts["project.create.new"], { palette: 1, button: 1 }, `${label}: counts by how the command was reached`);
      assert.equal(usage.counts["project.archive.export"], undefined, `${label}: an aria-disabled menu item is not a use`);
      assert.equal(usage.counts["project.create.add_existing"], undefined, `${label}: a disabled button is not a use`);
      assert.ok(!JSON.stringify(usage).includes(text.ask), `${label}: what was typed is not stored`);

      // A command with no handler but a standing entry on screen is pointed at, not pressed.
      await page.keyboard.press("Control+K");
      await input.fill(text.standing);
      assert.equal(await palette.locator("[role=option]").first().locator(".command-palette__meta").textContent(), text.locate);
      await input.press("Enter");
      await page.waitForFunction(() => document.activeElement?.getAttribute("data-command") === "fixture.standing_only"
        && document.activeElement.classList.contains("command-located"));

      // A command on a surface that is not open opens it, then runs once the surface is there.
      await page.keyboard.press("Control+K");
      await input.fill(text.other);
      assert.equal(await palette.locator("[role=option]").first().locator(".command-palette__meta").textContent(), text.opens);
      await input.press("Enter");
      await page.locator(".fixture-modeling").waitFor();
      await page.waitForFunction(() => window.__ran.includes("fixture.modeling_change"));

      assert.deepEqual(errors, [], `${label}: page errors`);
      console.log(`PASS command palette: ${label}`);
    } finally { await context.close(); }
  }
  assert.deepEqual(escaped, [], "No unexpected API or external service requests");
  console.log("PASS Ctrl+K over the Board: search, run, unavailable reasons, ask the AI, recent, shortcuts, counts, other surfaces");
} finally {
  await browser?.close(); await vite?.close();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
