import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createServer } from "vite";
import { cmd, commandProblems } from "./commandLocator.mjs";

// The side panel's history page (#300, 2-6), mounted alone as the shell mounts it: moves newest first, going back
// to a step through Switch, its Undo, naming a step, and the way back to the current design.
const root = fileURLToPath(new URL("..", import.meta.url)).replaceAll("\\", "/").replace(/\/$/, "");
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const scratch = await mkdtemp(join(tmpdir(), "history-page-"));
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {HistoryPage} from '/src/features/designHistory/HistoryPage';
import {CommandProvider} from '/src/features/commands/CommandProvider';
import {UserPreferencesProvider} from '/test/TestProviders.tsx';
import '/src/app/styles.css';
import '/@fs/${root}/../../../packages/web-shared/src/base.css';
createRoot(document.getElementById('root')).render(<UserPreferencesProvider><CommandProvider><div style={{width:360,padding:12}}><HistoryPage projectDir="D:/fixture/history" active /></div></CommandProvider></UserPreferencesProvider>);`;
const server = await createServer({ root, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, publicDir: false, logLevel: "error", cacheDir: join(scratch, "vite"),
  optimizeDeps: { noDiscovery: true, include: ["react", "react-dom", "react-dom/client", "react/jsx-runtime", "react/jsx-dev-runtime"] },
  server: { host: "127.0.0.1", port: 0, strictPort: true }, plugins: [{ name: "history-fixture",
    resolveId(id) { if (id === "/history-fixture.tsx") return `${root}/history-fixture.tsx`; },
    load(id) { if (id === `${root}/history-fixture.tsx`) return fixture; },
    configureServer(vite) { vite.middlewares.use((request, response, next) => {
      if (new URL(request.url, "http://fixture.local").pathname !== "/") return next();
      response.setHeader("Content-Type", "text/html");
      response.end('<html><head><meta name="viewport" content="width=device-width, initial-scale=1"/></head><body><div id="root"></div><script type="module" src="/history-fixture.tsx"></script></body></html>');
    }); },
  }],
});

const PROJECT = "history-project";
// The project as the Runtime keeps it: the head moved a → b → c, and the person named b.
const state = { current: "c", revision: 1, labels: new Map([["b", "Wider courtyard"]]), unsynced: false,
  moves: [{ runId: "c", previousRunId: "b", at: "2026-10-24T10:00:00Z", origin: "hub" },
    { runId: "b", previousRunId: "a", at: "2026-10-24T09:00:00Z", origin: "hub-agent" },
    { runId: "a", previousRunId: null, at: "2026-10-24T08:00:00Z", origin: "hub" }] };
const position = () => ({ projectId: PROJECT, revisionSha256: `rev-${state.revision}`,
  current: state.current ? { runId: state.current, label: state.labels.get(state.current) ?? null, updatedAt: "2026-10-24T10:00:00Z", sourceStageRef: null, branchId: null } : null,
  recovery: [], saved: [...state.labels].map(([runId, label]) => ({ runId, label, updatedAt: "2026-10-24T09:00:00Z", sourceStageRef: null, branchId: null })),
  managedRunIds: [], localDraft: null });
const graph = () => ({ projectId: PROJECT, head: null, revisionSha256: `rev-${state.revision}`, representations: [], warnings: [], lines: [], later: [],
  line: [{ runId: "a", baseRunId: null, label: null, request: null, summary: "First massing", stageRef: "stage:s1", updatedAt: null },
    { runId: "b", baseRunId: "a", label: null, request: null, summary: "Courtyard 12 m", stageRef: null, updatedAt: null },
    { runId: "c", baseRunId: "b", label: null, request: "lower the east roof", summary: null, stageRef: null, updatedAt: null }] });
const move = (runId) => { state.moves.unshift({ runId, previousRunId: state.current, at: new Date(Date.parse(state.moves[0].at) + 3600_000).toISOString(), origin: "hub" });
  state.current = runId; state.revision += 1; };

let browser, page;
const writes = [], errors = [];
try {
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  page = await browser.newPage({ viewport: { width: 420, height: 760 } });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const answer = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const body = method === "GET" ? null : request.postDataJSON();
    if (method !== "GET") writes.push({ method, path: url.pathname, body });
    if (url.pathname === "/api/working-design/moves") return answer({ projectId: PROJECT, warnings: [], moves: state.moves.map((row) =>
      ({ occurredAt: row.at, runId: row.runId, previousRunId: row.previousRunId, actorId: "studio:explicit-user-action", origin: row.origin })) });
    if (url.pathname === "/api/working-draft" && method === "GET") return answer(position());
    if (url.pathname === "/api/worktrees") return answer(graph());
    if (url.pathname === "/api/working-design/switch") {
      if (state.unsynced) return answer({ code: "DESIGN_TREE_UNSYNCED_EDITS", detail: "Modeling holds unrecorded edits." }, 409);
      const previous = state.current; move(body.runId);
      return answer({ position: position(), undo: { previous: previous ? { runId: previous, branchId: null } : null, continued: body.runId, revisionAfter: `rev-${state.revision}` } });
    }
    if (url.pathname === "/api/working-design/undo") {
      if (state.current !== body.continued) return answer({ code: "DESIGN_TREE_UNDO_MOVED", detail: "Moved on.", currentRunId: state.current }, 409);
      move(body.previous.runId); return answer(position());
    }
    if (url.pathname === "/api/working-draft" && method === "PUT") { state.current = body.runId; state.revision += 1; return answer(position()); }
    if (url.pathname === "/api/working-draft/save") { state.labels.set(body.runId, body.label); state.revision += 1; return answer(position()); }
    return answer({ code: "UNEXPECTED", detail: url.pathname }, 404);
  });
  await page.goto(server.resolvedUrls.local[0]);
  const rows = page.locator(".design-history__row");
  await rows.nth(2).waitFor();
  assert.deepEqual(await rows.locator(".design-history__name").allInnerTexts(), ["lower the east roof", "Wider courtyard", "First massing"],
    "newest first, each named by the person's name or what the step did");
  assert.equal(await rows.nth(0).getAttribute("data-current"), "true");
  assert.match(await rows.nth(1).locator(".design-history__meta").innerText(), /· AI$/, "the Agent's move says so");
  assert.equal(await rows.nth(2).locator(".design-history__badge--stage").count(), 1, "a Stage step is marked");
  assert.equal(await cmd(rows.nth(0), "design.go_to_step.continue", { includeHidden: true }).count(), 0, "the current step offers no way back to itself");
  assert.equal(await rows.nth(2).locator("button").filter({ hasText: /withdraw|撤回/i }).count(), 0, "a Stage row offers no withdraw before 6-5");
  assert.deepEqual(await commandProblems(page.locator(".design-history")), [], "every control names its command");

  // Going back to a step is Switch from the position as read, and its Undo puts the replaced Current back.
  await cmd(page, "design.go_to_step.continue", { arg: "b" }).click();
  await page.locator(".design-history__note").filter({ hasText: "Wider courtyard" }).waitFor();
  assert.deepEqual(writes.at(-1), { method: "POST", path: "/api/working-design/switch", body: { projectId: PROJECT, runId: "b", baseRevisionSha256: "rev-1" } });
  await page.waitForFunction(() => document.querySelector(".design-history__row")?.textContent?.includes("Wider courtyard"));
  await cmd(page, "design.undo_last").click();
  await page.locator(".design-history__note").filter({ hasText: /Undone|已撤销/ }).waitFor();
  assert.deepEqual(writes.at(-1), { method: "POST", path: "/api/working-design/undo",
    body: { projectId: PROJECT, previous: { runId: "c", branchId: null }, continued: "b" } });
  assert.equal(state.current, "c");

  // An Undo after the head moved elsewhere says so and puts nothing back.
  await cmd(page, "design.go_to_step.continue", { arg: "a" }).first().click();
  await cmd(page, "design.undo_last").waitFor();
  move("b");
  await cmd(page, "design.undo_last").click();
  await page.getByRole("alert").filter({ hasText: /changed again|又换过/ }).waitFor();
  assert.equal(state.current, "b");

  // Unrecorded Modeling edits hold a switch back, with the reason.
  state.unsynced = true;
  await cmd(page, "design.go_to_step.continue", { arg: "c" }).first().click();
  await page.getByRole("alert").filter({ hasText: /unrecorded|没记录/ }).waitFor();
  state.unsynced = false;

  // Naming a step saves the person's name; back to the current design is the return to the default.
  const named = rows.filter({ hasText: "First massing" }).first();
  await cmd(named, "design.go_to_step.save_milestone").click();
  await named.getByRole("textbox").fill("Massing study");
  await named.getByRole("textbox").press("Enter");
  await page.locator(".design-history__name").filter({ hasText: "Massing study" }).first().waitFor();
  assert.deepEqual(writes.at(-1), { method: "POST", path: "/api/working-draft/save",
    body: { projectId: PROJECT, runId: "a", label: "Massing study", baseRevisionSha256: writes.at(-1).body.baseRevisionSha256, savedBy: "person" } });
  await cmd(page, "design.go_to_step.default_base").click();
  await page.waitForFunction(() => !document.querySelector('[data-command="design.go_to_step.default_base"]'));
  assert.deepEqual(writes.at(-1), { method: "PUT", path: "/api/working-draft", body: { projectId: PROJECT, runId: null, baseRevisionSha256: writes.at(-1).body.baseRevisionSha256 } });
  await page.screenshot({ path: join(scratch, "history-page.png") });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, screenshots: scratch }));
} finally {
  await browser?.close();
  await server.close();
}
