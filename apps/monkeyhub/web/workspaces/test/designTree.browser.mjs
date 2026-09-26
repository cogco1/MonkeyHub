/** The Design Tree (#284) in the real ProjectWorkspace and the real Hub rail, on Excalidraw.
 * The Project Runtime is a stub serving the typed fixture (features/designTree/fixture.ts)
 * in the #294 design-history contract until codex/294-admission-record lands; it answers
 * Continue (PUT /api/working-draft) and Accept (POST /api/candidates/{id}/accept) with the
 * existing routes' rules. Arch and Board are replaced by stubs so the tree is what runs.
 * DESIGN_TREE_SCREENSHOTS=<dir> writes review screenshots; --serve keeps the page open.
 */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const webRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const workspacesRoot = path.join(webRoot, "workspaces");
const slash = (value) => value.replaceAll("\\", "/");
const cacheDir = await mkdtemp(path.join(tmpdir(), "design-tree-"));
const shots = process.env.DESIGN_TREE_SCREENSHOTS ? path.resolve(process.env.DESIGN_TREE_SCREENSHOTS) : null;
const serveOnly = process.argv.includes("--serve");
const errors = [], unexpected = [], external = [], writes = [];
const reviews = new Map();
const dispositionsBeforeArchive = new Map();
let recorded = 0;
const http = createHttpServer();
let vite, browser, fixture, fixtureModule, runtimeId = null;
const PROJECT = "riverside-library";

const page = (entry) => `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body,#root{height:100%;margin:0}</style></head><body><div id="root"></div>
<script type="module">import "/@fs/${slash(path.resolve(webRoot, "../../shared-web/src/base.css"))}";</script>
<script type="module" src="${entry}"></script></body></html>`;

// The tree's runtime reads and the two writes it may make, over the fixture's retained facts.
async function runtime(request, response, url, body) {
  const json = (value, status = 200) => { response.writeHead(status, { "content-type": "application/json" }); response.end(JSON.stringify(value)); };
  const name = url.pathname, method = request.method;
  try {
    if (method === "GET" && name === "/api/protocol") return json({ protocol: "archflow/2", server: "design-tree-fixture", serverVersion: "test", mode: "local",
      capabilities: ["candidate-admission", "candidate-review", "design-history", "working-draft", "working-source"] });
    if (method === "GET" && name === "/api/project") return json({ projectId: PROJECT, projectDir: "D:\\fixture\\riverside-library",
      published: { version: 2, stateSha256: "0".repeat(64) }, referenceRun: { runId: "run-site", baseVersion: 2, baseSha256: "0".repeat(64) },
      intentProvider: "fixture", intentModel: "fixture" });
    if (method === "GET" && name === "/api/working-source") return json(fixture.workingSource(url.searchParams.get("workspace") ?? "modeling"));
    if (method === "GET" && name === "/api/design-history") {
      const history = fixture.designHistory(url.searchParams.get("branchId") ?? "main");
      return json({ ...history,
        stages: history.stages.map(stage => ({ ...stage, review: reviews.get(`stage:${stage.stageRef}`) ?? null })),
        candidates: history.candidates.map(candidate => ({ ...candidate, review: reviews.get(`candidate:${candidate.candidateId}`) ?? null })) });
    }
    if (method === "GET" && name === "/api/worktrees") return json(fixture.worktrees());
    if (method === "GET" && /^\/api\/model-assets\/[0-9a-f]{64}\/preview$/.test(name)) {
      response.writeHead(204);
      response.end();
      return;
    }
    // The project's event stream, which other workspace code subscribes to; the fixture has no events to send.
    if (method === "GET" && name === "/api/events") {
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
      response.write(": fixture\n\n");
      return;
    }
    if (method === "GET" && name === "/api/working-draft") return json(fixture.workingDraft());
    // What Modeling's Record edits and continue does to the working draft here: its edits become recorded.
    if (method === "POST" && name === "/api/fixture/record") { recorded += 1; fixture.state.localDraft = null; return json({}); }
    if (method === "PUT" && name === "/api/working-draft") { writes.push({ method, name, body }); return json(fixture.selectWorkingDraft(body)); }
    if (method === "POST" && name === "/api/candidate-reviews") {
      writes.push({ method, name, body });
      const key = `${body.subjectKind}:${body.subjectRef}`, previous = reviews.get(key);
      const previousDisposition = previous?.disposition ?? "unreviewed";
      if (body.action === "archive" && previousDisposition !== "archived") dispositionsBeforeArchive.set(key, previousDisposition);
      const actorId = body.action === "endorse" ? "Review Architect" : "Archive Architect";
      const occurredAt = body.action === "endorse" ? "2026-09-26T12:00:00Z" : "2026-09-27T15:00:00Z";
      const review = { reviewRef: `project://${PROJECT}/runs/studio-candidate-reviews/review/candidate-review/${"a".repeat(64)}`,
        disposition: body.action === "archive" ? "archived" : body.action === "reject" ? "rejected"
          : body.action === "restore" && previousDisposition === "archived" ? dispositionsBeforeArchive.get(key) ?? "unreviewed" : previousDisposition,
        endorsed: body.action === "endorse" || previous?.endorsed || false, actorId, occurredAt, reason: body.reason,
        endorsedBy: body.action === "endorse" ? actorId : previous?.endorsedBy ?? null,
        endorsedAt: body.action === "endorse" ? occurredAt : previous?.endorsedAt ?? null };
      reviews.set(key, review);
      return json(review, 201);
    }
    const accept = name.match(/^\/api\/candidates\/([^/]+)\/accept$/);
    if (method === "POST" && accept) { writes.push({ method, name, body }); return json(fixture.accept(decodeURIComponent(accept[1]), body)); }
  } catch (error) {
    if (error instanceof fixtureModule.FixtureRefusal) return json({ code: error.code, detail: error.message }, error.status);
    throw error;
  }
  unexpected.push(`${method} ${name}`);
  return json({ code: "FIXTURE_UNEXPECTED", detail: `Unexpected request ${method} ${name}` }, 404);
}

// Just enough of the Hub for its rail to open one project's workspaces.
const hub = { projects: [{ projectId: PROJECT, projectDir: "D:\\fixture\\riverside-library", name: "Riverside Library", chatCount: 0, version: 2, stage: "S2" }] };
const hubApps = (origin) => ["monkeyarch", "monkeyboard", "monkeyrender", "monkeyfab", "monkeymonitor"].map((appId) => ({
  appId, title: appId, serviceId: appId === "monkeyfab" ? "hub" : appId === "monkeymonitor" ? "monitor" : "studio", state: "running", processId: 4242,
  available: true, url: `${origin}/?view=${appId === "monkeyboard" ? "board" : "arch"}${runtimeId ? `&runtimeId=${runtimeId}` : ""}`,
  apiUrl: runtimeId ? `${origin}/api/runtime/projects/${runtimeId}/studio/` : null }));
const hubRuntime = (origin) => ({ serverId: "design-tree-hub", sequence: 1, workers: [], projects: runtimeId === null ? [] : [{
  runtimeId, projectDir: hub.projects[0].projectDir, projectId: PROJECT, state: "open", operations: [], retained: null, projection: "ready", clients: 1, error: null,
  workers: [{ workerId: runtimeId, serviceId: "studio", projectId: PROJECT, projectDir: hub.projects[0].projectDir, instanceId: "instance-1", processId: 4242,
    desiredState: "running", healthy: true, state: "ready", url: `${origin}/api/runtime/projects/${runtimeId}/studio/`, error: null }], sessions: [] }] });
async function hubApi(request, response, url, body, origin) {
  const json = (value, status = 200) => { response.writeHead(status, { "content-type": "application/json" }); response.end(JSON.stringify(value)); };
  const name = url.pathname;
  if (name === "/api/runtime/events") {
    response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
    response.write(`retry: 500\nevent: runtime\ndata: ${JSON.stringify({ serverId: "design-tree-hub", sequence: 1, kind: "snapshot", snapshot: hubRuntime(origin) })}\n\n`);
    return;
  }
  if (name === "/api/apps") return json(hubApps(origin));
  if (name === "/api/settings/user") return json({ language: "en", theme: "light", fontScale: 1 });
  if (name === "/api/settings/apps") return json({ projectDir: hub.projects[0].projectDir, referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18790 });
  if (request.method === "GET" && name === "/api/credentials") return json(["gemini", "coding-plan"].map((id) => ({
    id, configured: false, source: null, variable: null, saved: false, storeAvailable: true })));
  if (name === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture only", installed: true, signedIn: true, models: [], modelCatalog: "ready", modelDetail: "" }]);
  if (name === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: [PROJECT] });
  if (name === "/api/chat/projects") return json(hub.projects);
  if (name === "/api/chat/sessions") return json([]);
  if (name === "/api/runtime") return json(hubRuntime(origin));
  if (name === "/api/runtime/projects/open") {
    assert.equal(body.projectId, PROJECT);
    runtimeId ??= randomUUID();
    return json(hubRuntime(origin).projects[0]);
  }
  if (name === "/api/updates/status") return json({ currentVersion: "fixture", currentRevision: "f".repeat(40), mode: "local", state: "idle", prepared: null, canApply: false, message: null, error: null });
  unexpected.push(`hub ${request.method} ${name}`);
  return json({ code: "FIXTURE_UNEXPECTED", detail: name }, 404);
}

const readBody = (request) => new Promise((resolve) => {
  const chunks = [];
  request.on("data", (chunk) => chunks.push(chunk));
  request.on("end", () => resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : null));
});

try {
  vite = await createServer({ root: webRoot, configFile: false, resolve: { dedupe: ["react", "react-dom"] }, logLevel: "error", cacheDir,
    publicDir: ".generated/public", define: { "import.meta.env.VITE_ARCHFLOW_API_URL": JSON.stringify("") },
    plugins: [{ name: "design-tree-fixture", enforce: "pre", transform(source, id) {
      const file = slash(id.split("?")[0]), root = slash(workspacesRoot);
      if (file === `${root}/src/app/App.tsx`) return { code: `
        import { useEffect } from "react";
        export default function App(props) {
          window.__arch = { initialRunId: props.initialRunId ?? null, followsHead: Boolean(props.initialRunFollowsHead), refreshKey: props.refreshKey, active: props.active };
          // Modeling hands the host its Record edits and continue (#302).
          useEffect(() => {
            props.onRecorder?.(() => fetch("/api/fixture/record", { method: "POST" }).then(() => undefined));
            return () => props.onRecorder?.(null);
          }, [props.onRecorder]);
          return <div data-testid="arch-stub" style={{ padding: 24 }}>Modeling · {props.initialRunId ?? "Working Head"}</div>;
        }`, map: null };
      if (file === `${root}/src/workspaces/monkeyboard/Board.tsx`) return { code: `export default function Board() { return <div data-testid="board-stub">Board</div>; }`, map: null };
      // The hand-off GH-284 leaves ProjectWorkspace (its planning card): the chip gets Modeling's recorder, as the
      // tree's side card does. The test wires it until ProjectWorkspace passes it itself; then this is a no-op.
      if (file === `${root}/src/app/ProjectWorkspace.tsx`) {
        const bar = source.match(/<DesignTreeBar\b[\s\S]*?\/>/)?.[0];
        assert.ok(bar, "ProjectWorkspace mounts the Stage chip");
        if (!bar.includes("onRecordEdits=")) {
          return { code: source.replace(bar, bar.replace("<DesignTreeBar", "<DesignTreeBar onRecordEdits={recordEdits}")), map: null };
        }
      }
      if (file === `${root}/src/features/designTree/DesignTreeCanvas.tsx`) {
        const callback = "excalidrawAPI={(api) => { canvas.current = api; setReady(true); }}";
        assert.equal(source.split(callback).length, 2, "the test reads the tree canvas through its one Excalidraw callback");
        return { code: source.replace(callback, "excalidrawAPI={(api) => { canvas.current = api; Object.assign(window, { __treeApi: api }); setReady(true); }}"), map: null };
      }
    } }, react()],
    server: { middlewareMode: true, hmr: false, ws: { server: http }, watch: null } });
  fixtureModule = await vite.ssrLoadModule("/workspaces/src/features/designTree/fixture.ts");
  fixture = fixtureModule.createDesignTreeFixture();
  const handle = async (request, response) => {
    const url = new URL(request.url, "http://fixture.test");
    if (url.pathname === "/tree-workspace") {
      response.setHeader("content-type", "text/html");
      response.end(await vite.transformIndexHtml(url.pathname, page("/workspaces/test/workspace-fixture.tsx"))); return;
    }
    if (url.pathname === "/" && !url.searchParams.has("raw")) {
      response.setHeader("content-type", "text/html");
      response.end(await vite.transformIndexHtml(url.pathname, page("/src/main.tsx"))); return;
    }
    const body = request.method === "GET" || request.method === "HEAD" ? null : await readBody(request);
    const forwarded = url.pathname.match(/^\/api\/runtime\/projects\/([^/]+)\/studio(\/api\/.*)$/);
    if (forwarded) {
      assert.equal(forwarded[1], runtimeId, "workspace requests use the attached project runtime");
      return runtime(request, response, new URL(`${forwarded[2]}${url.search}`, "http://fixture.test"), body);
    }
    if (url.pathname.startsWith("/api/")) {
      const hubRoute = /^\/api\/(apps|settings|credentials|chat|runtime|updates)(\/|$)/.test(url.pathname);
      return hubRoute ? hubApi(request, response, url, body, `http://127.0.0.1:${http.address().port}`) : runtime(request, response, url, body);
    }
    vite.middlewares(request, response);
  };
  http.on("request", (request, response) => {
    handle(request, response).catch((error) => {
      errors.push(`server: ${error?.stack ?? error}`);
      if (!response.headersSent) response.writeHead(500, { "content-type": "text/plain" });
      response.end(String(error));
    });
  });
  await new Promise((resolve) => http.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${http.address().port}`;
  if (serveOnly) {
    console.log(`Design tree fixture: ${origin}/tree-workspace?lang=en   Hub: ${origin}/`);
    await new Promise(() => {});
  }
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
  browser = await chromium.launch({ headless: true, channel: "chrome" });
  if (shots) await mkdir(shots, { recursive: true });
  // A toast is shot once it has faded in.
  const settled = (target) => target.evaluate(() => Promise.all(document.getAnimations()
    .filter((animation) => animation.effect?.target?.matches?.(".design-tree-toast")).map((animation) => animation.finished.catch(() => undefined))));
  const shoot = async (target, name) => {
    if (!shots) return;
    await settled(target);
    await target.screenshot({ path: path.join(shots, `${name}.png`) });
    console.log(`design tree: ${name}.png`);
  };

  // ------------------------------------------------------------------ Part A: the tree in ProjectWorkspace.
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await context.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    external.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const tab = await context.newPage();
  tab.setDefaultTimeout(20_000);
  tab.on("pageerror", (error) => errors.push(error.message));
  tab.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  const loaded = [];
  tab.on("request", (request) => loaded.push(request.url()));
  await tab.goto(`${origin}/tree-workspace?lang=en&theme=light`, { waitUntil: "domcontentloaded", timeout: 240_000 });
  const chip = tab.locator(".stage-chip");
  await chip.waitFor({ timeout: 120_000 });
  await tab.getByTestId("arch-stub").waitFor();
  await tab.waitForFunction(() => /S2 · Layout — Current/.test(document.querySelector(".stage-chip")?.textContent ?? ""));
  assert.equal((await chip.innerText()).replace(/\s+/g, " ").trim(), "S2 · Layout — Current · 2 running", "the chip reads Stage, position and running work");
  const ready = tab.locator(".stage-chip__ready");
  assert.equal((await ready.innerText()).replace(/\s+/g, " ").trim(), "1 option ready · View", "options nobody opened are the chip's notice");
  assert.equal(await chip.getAttribute("aria-pressed"), "false");
  assert.ok(!loaded.some((url) => /excalidraw/i.test(url)), "Excalidraw is not loaded until the tree opens");
  assert.equal(await tab.locator(".chat-rail").count(), 0, "the entry is project chrome, not a rail of its own here");
  await shoot(tab, "01-chip-over-modeling");

  // #302: the notice's View opens the tree on the ready option's Study, with its side card.
  const inChinese = async (name) => {
    await tab.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
    await tab.waitForFunction(() => (document.querySelector(".stage-chip")?.textContent ?? "").includes("当前"));
    await shoot(tab, name);
    await tab.evaluate(() => window.__workspaceFixture.setLanguage("en"));
    await tab.waitForFunction(() => (document.querySelector(".stage-chip")?.textContent ?? "").includes("Current"));
  };
  if (shots) await inChinese("01a-ready-notice-zh");
  await ready.click();
  await tab.locator('[data-project-surface="tree"] .design-tree-card[data-node="candidate:run-entrance-a"]').waitFor();
  assert.equal(await chip.getAttribute("aria-pressed"), "true");
  await shoot(tab, "01b-ready-notice-opens-study");
  if (shots) await inChinese("01c-ready-notice-opens-study-zh");
  // #337: the tree's menus sit in the project bar, over whichever surface is open.
  const bar = tab.locator(".project-bar");
  await bar.getByRole("button", { name: "Back to Modeling" }).click();
  await tab.getByTestId("arch-stub").waitFor();
  assert.equal(await ready.count(), 0, "an option that was opened is no longer new");

  // The chip opens the tree surface and keeps itself.
  await chip.click();
  const surface = tab.locator('[data-project-surface="tree"]');
  await surface.locator(".design-tree__canvas canvas").first().waitFor();
  await tab.waitForFunction(() => window.__treeApi?.getSceneElements().length > 20);
  // The notice left the canvas centred on the ready option; Fit shows the whole tree again.
  await bar.getByRole("button", { name: "Fit", exact: true }).click();
  await tab.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(resolve)))));
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "mid");
  assert.equal(await chip.getAttribute("aria-pressed"), "true");
  // #337: one bar, not two: the tree's menus on its left, the Stage position at its right end.
  assert.equal(await surface.locator(".surface-bar").count(), 0, "the tree has no bar of its own under the project bar");
  const [fitBox, chipBox, barBox] = await Promise.all([bar.getByRole("button", { name: "Fit", exact: true }).boundingBox(), chip.boundingBox(), bar.boundingBox()]);
  assert.ok(chipBox.x > fitBox.x + fitBox.width && barBox.x + barBox.width - (chipBox.x + chipBox.width) < 40, "the position sits at the bar's right end");
  assert.equal(await tab.getByTestId("arch-stub").isVisible(), false, "the tree replaces the view, it is not drawn over it");
  assert.ok(loaded.some((url) => /excalidraw/i.test(url)), "the canvas loads with the tree");
  const scene = (target = tab) => target.evaluate(() => {
    const api = window.__treeApi, state = api.getAppState();
    return { elements: api.getSceneElements().map((element) => ({ id: element.id, type: element.type, x: element.x, y: element.y, width: element.width,
      height: element.height, points: element.points ?? null, opacity: element.opacity, strokeStyle: element.strokeStyle, text: element.text ?? null,
      data: element.customData?.tree ?? null })), zoom: state.zoom.value, scrollX: state.scrollX, scrollY: state.scrollY,
      offsetLeft: state.offsetLeft, offsetTop: state.offsetTop };
  });
  /** A real click on a node's card, where the canvas draws it now. */
  const clickCanvasNode = async (target, host, id) => {
    // The side card sits over the canvas; close it so it cannot cover the node.
    if (await host.locator(".design-tree-card").count()) {
      await target.keyboard.press("Escape");
      await host.locator(".design-tree-card").waitFor({ state: "detached" });
    }
    // A surface shown again is measured by Excalidraw a frame later; click where the canvas is now.
    await target.waitForFunction(() => {
      const state = window.__treeApi?.getAppState();
      const box = [...document.querySelectorAll(".design-tree__canvas .excalidraw")].find((element) => element.getClientRects().length)
        ?.getBoundingClientRect();
      return Boolean(state && box && state.width > 40 && Math.abs(state.offsetLeft - box.left) < 1 && Math.abs(state.offsetTop - box.top) < 1);
    });
    const current = await scene(target);
    const card = current.elements.find((element) => element.id === `${id}:card`);
    assert.ok(card, `${id} is on the canvas`);
    await target.mouse.click((card.x + card.width / 2 + current.scrollX) * current.zoom + current.offsetLeft,
      (card.y + card.height / 2 + current.scrollY) * current.zoom + current.offsetTop);
    const opened = host.locator(`.design-tree-card[data-node="${id}"]`);
    await opened.waitFor();
    return opened;
  };
  const level = () => surface.locator(".design-tree__canvas").getAttribute("data-level");
  const stage = (run) => `stage:${fixture.state.stages.find((row) => row.run === run).ref}`;
  const S0 = stage("run-site"), S1 = stage("run-s1-massing"), S2 = stage("run-s2-layout");

  /** One trunk stroke left to right through the trunk cards; N-1 twigs per Study; no crossings or overlaps. */
  const checkTree = (view, trunkNodes, studies) => {
    const trunks = view.elements.filter((element) => element.data?.role === "trunk");
    assert.equal(trunks.length, 1, "the trunk is one stroke");
    const points = trunks[0].points.map(([x, y]) => [trunks[0].x + x, trunks[0].y + y]);
    for (let index = 1; index < points.length; index += 1) {
      assert.ok(points[index][0] > points[index - 1][0] && Math.abs(points[index][1] - points[0][1]) < 1e-6, `the trunk turns back at ${index}`);
    }
    const centres = trunkNodes.map((id) => {
      const card = view.elements.find((element) => element.id === `${id}:card`);
      assert.ok(card, `${id} is drawn`);
      return card.x + card.width / 2;
    });
    assert.deepEqual(points.map(([x]) => Math.round(x)), centres.map(Math.round), "the trunk runs through the chosen path, in order");
    for (const [study, twigs] of Object.entries(studies)) {
      const members = fixture.state.studies.find((row) => row.id === study).candidateIds.map((run) => `candidate:${run}`);
      const drawn = view.elements.filter((element) => element.data?.role === "edge" && element.data.edge === "twig" && members.includes(element.data.node)).length;
      assert.equal(drawn, twigs, `${study}: ${drawn} twigs for ${members.length} options`);
    }
    const segments = view.elements.filter((element) => element.type === "line" && element.points).flatMap((element) => {
      const abs = element.points.map(([x, y]) => [element.x + x, element.y + y]);
      return abs.slice(1).map((point, index) => [abs[index], point]);
    });
    const inside = (value, a, b) => value > Math.min(a, b) + 1e-6 && value < Math.max(a, b) - 1e-6;
    const shared = (a1, a2, b1, b2) => Math.min(Math.max(a1, a2), Math.max(b1, b2)) - Math.max(Math.min(a1, a2), Math.min(b1, b2));
    let crossings = 0;
    for (let i = 0; i < segments.length; i += 1) for (let j = i + 1; j < segments.length; j += 1) {
      const [a, b] = [segments[i], segments[j]];
      const aH = Math.abs(a[0][1] - a[1][1]) < 1e-6, bH = Math.abs(b[0][1] - b[1][1]) < 1e-6;
      if (aH !== bH) { const [h, v] = aH ? [a, b] : [b, a]; if (inside(v[0][0], h[0][0], h[1][0]) && inside(h[0][1], v[0][1], v[1][1])) crossings += 1; }
      else if (aH && Math.abs(a[0][1] - b[0][1]) < 1e-6 && shared(a[0][0], a[1][0], b[0][0], b[1][0]) > 1e-6) crossings += 1;
      else if (!aH && Math.abs(a[0][0] - b[0][0]) < 1e-6 && shared(a[0][1], a[1][1], b[0][1], b[1][1]) > 1e-6) crossings += 1;
    }
    assert.equal(crossings, 0, "no two strokes cross");
    const cards = view.elements.filter((element) => element.id.endsWith(":card"));
    for (let i = 0; i < cards.length; i += 1) for (let j = i + 1; j < cards.length; j += 1) {
      const [p, q] = [cards[i], cards[j]];
      assert.ok(!(p.x < q.x + q.width && q.x < p.x + p.width && p.y < q.y + q.height && q.y < p.y + p.height), `${p.id} overlaps ${q.id}`);
    }
  };
  let view = await scene();
  assert.equal(await level(), "mid", "the tree fits at the middle level: letters and short names");
  checkTree(view, [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, "current"], { "study-massing": 4, "study-facade": 2, "study-entrance": 1 });
  assert.equal(view.elements.filter((element) => element.data?.edge === "pending").length, 2, "running and queued work are placeholders");
  assert.equal(view.elements.filter((element) => element.data?.role === "summary" && element.data.node !== "current").length, 0, "no summaries at this distance");
  assert.ok(!view.elements.some((element) => /run-|rev-|project:\/\/|[0-9a-f]{16}/.test(element.text ?? "")), "no ids or hashes on the canvas");
  await shoot(tab, "02-tree-fit");

  // #337: the canvas draws in the Hub's tokens and follows the theme and the interface
  // style; Excalidraw's dark-theme inversion is not applied to it.
  const follows = async (label) => {
    await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.strokeColor
      === getComputedStyle(document.documentElement).getPropertyValue("--accent").trim().toLowerCase());
    const now = await tab.evaluate(() => ({ trunk: window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk").strokeColor,
      ground: window.__treeApi.getAppState().viewBackgroundColor, token: getComputedStyle(document.documentElement).getPropertyValue("--ground").trim().toLowerCase(),
      filter: getComputedStyle(document.querySelector(".design-tree__canvas canvas")).filter }));
    assert.equal(now.ground, now.token, `${label}: the canvas ground is the Hub's`);
    assert.equal(now.filter, "none", `${label}: the canvas is drawn in its own colours, not inverted`);
    // The page's own colour transitions finish before a review shot.
    await tab.evaluate(() => Promise.all(document.getAnimations().filter((animation) => animation instanceof CSSTransition)
      .map((animation) => animation.finished.catch(() => undefined))));
    return now;
  };
  const lightColours = await follows("light");
  const styles = async (theme) => {
    for (const style of ["quiet", "titleblock", "night"]) {
      await tab.evaluate((value) => { document.documentElement.dataset.uiStyle = value; }, style);
      await follows(`${theme}, ${style}`);
      await shoot(tab, `02b-tree-${theme}-${style}`);
    }
  };
  await tab.evaluate(() => window.__workspaceFixture.setTheme("dark"));
  assert.notEqual((await follows("dark")).trunk, lightColours.trunk, "the trunk changes with the theme");
  await shoot(tab, "02a-tree-dark");
  await styles("dark");
  // Setting the theme applies the fixture's whole appearance again, Classic included.
  await tab.evaluate(() => window.__workspaceFixture.setTheme("light"));
  assert.equal((await follows("light again")).trunk, lightColours.trunk);
  assert.equal(await tab.evaluate(() => document.documentElement.dataset.uiStyle), "classic");
  await styles("light");
  await tab.evaluate(() => { document.documentElement.dataset.uiStyle = "classic"; });
  assert.equal((await follows("light, classic")).trunk, lightColours.trunk);

  // Semantic zoom: far shows the trunk and counts; close adds summaries and status.
  const box = await surface.locator(".design-tree__canvas").boundingBox();
  await tab.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await tab.mouse.wheel(0, 320);
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "far");
  view = await scene();
  assert.ok(view.elements.some((element) => element.data?.role === "dot") && view.elements.some((element) => element.data?.role === "fork"), "far: dots and counts");
  assert.equal(view.elements.filter((element) => element.data?.role === "letter").length, 0, "far: no option cards");
  assert.ok(view.elements.some((element) => element.data?.role === "fork" && /5 options · 1 continued/.test(element.text ?? "")));
  await shoot(tab, "03-tree-far");
  await tab.mouse.wheel(0, -760);
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "close");
  view = await scene();
  assert.ok(view.elements.filter((element) => element.data?.role === "summary").length >= 3, "close: summaries");
  assert.ok(view.elements.some((element) => element.data?.role === "status"), "close: status");
  await shoot(tab, "04-tree-close");
  await bar.getByRole("button", { name: "Fit", exact: true }).click();
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "mid");

  // Clicking a node shows its side card; Accept exists on Current only.
  const clickNode = (id) => clickCanvasNode(tab, surface, id);
  let card = await clickNode("candidate:run-massing-d");
  assert.equal(await card.locator("strong").innerText(), "D · Terraced wedge");
  assert.match(await card.innerText(), /Option · Massing Study/);
  assert.match(await card.innerText(), /Admitted by\s*Arch Agent/);
  assert.equal(await card.getByRole("button", { name: "View", exact: true }).isEnabled(), true);
  assert.equal(await card.getByRole("button", { name: /Compare this Study/ }).isDisabled(), true, "Compare is marked for later");
  assert.equal(await card.locator('[data-action="accept"]').count(), 0, "an option cannot be accepted");
  await card.getByRole("button", { name: "Endorse direction", exact: true }).click();
  await tab.waitForTimeout(100);
  assert.deepEqual(writes.at(-1), { method: "POST", name: "/api/candidate-reviews", body: {
    projectId: PROJECT, subjectKind: "candidate", subjectRef: "run-massing-d", action: "endorse", reason: null } });
  await card.getByText("Endorsed direction", { exact: true }).waitFor();
  assert.match(await card.innerText(), /Endorsed by\s*Review Architect/);
  const endorsementTime = await card.getByText("Endorsed", { exact: true }).locator("..").locator("dd").innerText();
  assert.ok(endorsementTime.length > 0, "the endorsement has a visible time");
  const beforeArchive = fixture.workingDraft();
  await card.getByRole("button", { name: "Archive", exact: true }).click();
  await card.waitFor({ state: "detached" });
  assert.equal((await scene()).elements.some((element) => element.data?.node === "candidate:run-massing-d"), false,
    "an archived Candidate leaves the default canvas");
  await bar.getByRole("button", { name: "Show processed (1)", exact: true }).click();
  card = await clickNode("candidate:run-massing-d");
  await card.getByRole("button", { name: "Undo archive", exact: true }).waitFor();
  assert.match(await card.innerText(), /Endorsed by\s*Review Architect/);
  assert.match(await card.innerText(), /Last reviewed by\s*Archive Architect/);
  assert.equal(await card.getByText("Endorsed", { exact: true }).locator("..").locator("dd").innerText(), endorsementTime,
    "archiving keeps the original endorsement's attribution and time");
  assert.equal(await card.locator('[data-action="continue"]').isEnabled(), true, "archiving does not add a new Continue restriction");
  await card.getByRole("button", { name: "Undo archive", exact: true }).click();
  await card.getByRole("button", { name: "Archive", exact: true }).waitFor();
  assert.equal(await bar.getByRole("button", { name: /Show processed/ }).count(), 0, "restore returns to the default projection");
  assert.equal(await card.locator("strong").innerText(), "D · Terraced wedge", "cancelling archive retains the selected Study option");
  assert.deepEqual(fixture.workingDraft(), beforeArchive, "archive, filtering and restore leave Working Head unchanged");
  writes.length = 0;
  await shoot(tab, "05-side-card");
  card = await clickNode(S1);
  assert.match(await card.innerText(), /Stage · accepted checkpoint/);
  assert.equal(await card.locator('[data-action="accept"]').count(), 0, "a Stage cannot be accepted again");
  await card.getByRole("button", { name: "Endorse direction", exact: true }).click();
  await card.getByText("Endorsed direction", { exact: true }).waitFor();
  assert.match(await card.innerText(), /Endorsed by\s*Review Architect/);
  writes.length = 0;
  card = await clickNode("candidate:run-facade-c");
  assert.match(await card.locator(".design-tree-card__warning").innerText(), /review checks were still open \(1\)/,
    "an option admitted with review checks open says so in its side card");
  assert.equal(await surface.locator(".design-tree__notice").count(), 0, "this runtime reports admitted options");
  card = await clickNode("current");
  assert.equal(await card.locator('[data-action="accept"]').count(), 1, "Current offers Accept");
  assert.equal(await card.locator('[data-action="accept"]').isDisabled(), true, "Current is exactly S2, so there is nothing new to accept");
  assert.match(await card.innerText(), /Current is exactly S2 · Layout/);

  // View is read-only: Modeling opens it, the Working Head stays.
  card = await clickNode("candidate:run-massing-d");
  const headBefore = fixture.state.head;
  await card.getByRole("button", { name: "View", exact: true }).click();
  await tab.getByTestId("arch-stub").filter({ hasText: "run-massing-d" }).waitFor();
  assert.deepEqual(await tab.evaluate(() => [window.__arch.initialRunId, window.__arch.followsHead]), ["run-massing-d", false]);
  assert.equal(fixture.state.head, headBefore, "View never moves the Working Head");
  assert.equal(writes.length, 0);
  await tab.locator(".stage-chip__viewing").filter({ hasText: "Viewing D · Terraced wedge · read-only" }).waitFor();
  // R2, the chip's half: the viewing state offers the way back and Continue from here.
  assert.deepEqual(await tab.locator(".stage-chip__viewing").getByRole("button").allInnerTexts(), ["Back to Current", "Continue from here"]);
  await shoot(tab, "06-viewing-read-only");
  if (shots) await inChinese("06a-chip-viewing-zh");
  await tab.getByRole("button", { name: "Back to Current", exact: true }).click();
  await tab.waitForFunction(() => window.__arch.initialRunId === "run-s2-layout");
  assert.equal(await tab.locator(".stage-chip__viewing").count(), 0);

  // Continue moves the Working Head through PUT /api/working-draft, and the trunk re-roots. Unrecorded
  // Modeling edits refuse it first, beside one click that records them and continues (#302).
  await chip.click();
  card = await clickNode("candidate:run-massing-d");
  const archRefresh = await tab.evaluate(() => window.__arch.refreshKey);
  fixture.state.localDraft = { source: { projectId: PROJECT, stateDigest: "d".repeat(64), sourceRunId: "run-s2-layout", sourceStageRef: null },
    commands: [], attempt: null, updatedAt: "2026-09-25T22:00:00Z" };
  await card.getByRole("button", { name: "Continue from here", exact: true }).click();
  await card.getByText("Model edits in Modeling are not recorded yet; they are kept. Record them and continue, or undo them in Modeling.").waitFor();
  assert.equal(fixture.state.head, headBefore, "a Continue refused by unrecorded edits moves nothing");
  assert.equal(writes.length, 0);
  const toast = tab.locator(".design-tree-toast");
  assert.equal(await toast.count(), 0, "a refused Continue keeps its inline message and shows no toast");
  await shoot(tab, "06b-record-and-continue");
  await card.locator('[data-action="record"]').click();
  // FN-5: the Continue confirms itself beside the chip, with Undo; hovering keeps the toast while it is checked.
  await toast.filter({ hasText: "Current is now “D · Terraced wedge”" }).waitFor();
  await toast.hover();
  assert.equal((await toast.innerText()).replace(/\s+/g, " ").trim(), "Current is now “D · Terraced wedge” · Undo");
  assert.equal(await card.locator(".design-tree-card__outcome").count(), 0, "the toast is the one confirmation");
  assert.equal(recorded, 1, "Record edits and continue records the edits once, then continues");
  assert.deepEqual(writes.at(-1), { method: "PUT", name: "/api/working-draft",
    body: { projectId: PROJECT, runId: "run-massing-d", baseRevisionSha256: "rev-0001", branchId: null } });
  assert.equal(fixture.state.head, "run-massing-d");
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 3);
  view = await scene();
  checkTree(view, [S0, "candidate:run-massing-d", "current"], { "study-massing": 4 });
  assert.ok(view.elements.find((element) => element.id === `${S2}:card`).opacity < 100, "the future left behind stays, faded");
  assert.ok(await tab.evaluate((before) => window.__arch.refreshKey > before, archRefresh), "Modeling re-reads the moved head");
  await shoot(tab, "06c-continue-toast");
  if (shots) await inChinese("06d-continue-toast-zh");

  // Undo continues back to the exact previous Current: the same PUT, onto the source recorded before the Continue.
  const rev = (value) => `rev-${String(value).padStart(4, "0")}`;
  let revision = fixture.state.revision;
  await toast.getByRole("button", { name: "Undo", exact: true }).click();
  await toast.filter({ hasText: "Undone · Current is back where it was" }).waitFor();
  assert.deepEqual(writes.at(-1), { method: "PUT", name: "/api/working-draft",
    body: { projectId: PROJECT, runId: "run-s2-layout", baseRevisionSha256: rev(revision), branchId: "main" } });
  assert.equal(fixture.state.head, "run-s2-layout", "Undo put the previous Current back");
  assert.equal(await toast.getByRole("button").count(), 0, "an Undo offers no second Undo");
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 6);
  checkTree(await scene(), [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, "current"], { "study-massing": 4, "study-facade": 2, "study-entrance": 1 });
  assert.equal((await chip.innerText()).replace(/\s+/g, " ").trim(), "S2 · Layout — Current · 2 running");

  // Continue from D again; the tour goes on from there.
  revision = fixture.state.revision;
  await card.getByRole("button", { name: "Continue from here", exact: true }).click();
  await toast.filter({ hasText: "Current is now “D · Terraced wedge”" }).waitFor();
  assert.deepEqual(writes.at(-1).body, { projectId: PROJECT, runId: "run-massing-d", baseRevisionSha256: rev(revision), branchId: null });
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 3);
  assert.equal((await chip.innerText()).replace(/\s+/g, " ").trim(), "S0 · Site — Current · 2 running");
  assert.equal((await ready.innerText()).replace(/\s+/g, " ").trim(), "3 options ready · View");
  card = await clickNode("current");
  assert.equal(await card.locator('[data-action="accept"]').isDisabled(), true);
  assert.match(await card.innerText(), /Current comes from S0 · Site, but this line's newest Stage is S2 · Layout/);
  await shoot(tab, "07-continued-rerooted");

  // Continue on the newest Stage's option, then Accept as next Stage from Current only.
  card = await clickNode("candidate:run-entrance-a");
  assert.match(await card.innerText(), /Study\s+Study from S2 · Layout/, "a Study without a name is named after where it started");
  await card.getByRole("button", { name: "Continue from here", exact: true }).click();
  await toast.filter({ hasText: "Current is now “Courtyard gate on the south bar”" }).waitFor();
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 7);
  checkTree(await scene(), [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, "candidate:run-entrance-a", "current"], { "study-massing": 4, "study-facade": 2 });
  card = await clickNode("current");
  const acceptButton = card.locator('[data-action="accept"]');
  assert.equal(await acceptButton.innerText(), "Accept as S3");
  await acceptButton.click();
  await card.getByText("Accept Current as S3? This creates an immutable Stage from the current work.").waitFor();
  await shoot(tab, "08-accept-confirm");
  await card.locator('[data-action="accept-confirm"]').click();
  // Accept confirms itself too, with no Undo: acceptance is a retained fact.
  await toast.filter({ hasText: "Accepted as S3" }).waitFor();
  await toast.hover();
  assert.equal((await toast.innerText()).trim(), "Accepted as S3");
  assert.equal(await toast.getByRole("button").count(), 0, "an accepted Stage has no Undo");
  assert.deepEqual(writes.at(-1), { method: "POST", name: "/api/candidates/run-entrance-a/accept",
    body: { projectId: PROJECT, branchId: "main", expectedHeadStageRef: fixture.state.stages.find((row) => row.run === "run-s2-layout").ref } });
  const S3 = `stage:${fixture.state.branchHead}`;
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 8);
  checkTree(await scene(), [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, "candidate:run-entrance-a", S3, "current"], { "study-massing": 4, "study-facade": 2 });
  assert.equal((await chip.innerText()).replace(/\s+/g, " ").trim(), "S3 — Current · 2 running");
  await shoot(tab, "09-accepted-s3");
  if (shots) await inChinese("09a-accept-toast-zh");
  // A toast stays while hovered (about 8 s otherwise), then fades once the pointer leaves.
  await toast.hover();
  await tab.waitForTimeout(9_000);
  assert.equal(await toast.count(), 1, "hovering keeps the toast");
  await tab.mouse.move(8, 8);
  await toast.waitFor({ state: "detached", timeout: 6_000 });

  // R2, the chip's half: while another model is viewed, the chip continues from it through the same Continue.
  card = await clickNode("candidate:run-massing-b");
  await card.getByRole("button", { name: "View", exact: true }).click();
  await tab.getByTestId("arch-stub").filter({ hasText: "run-massing-b" }).waitFor();
  const viewingState = tab.locator(".stage-chip__viewing");
  await viewingState.filter({ hasText: "Viewing B · Twin towers on a podium · read-only" }).waitFor();
  const chipContinue = viewingState.getByRole("button", { name: "Continue from here", exact: true });
  // Refused by unrecorded edits, it says so in the bar and shows no toast.
  fixture.state.localDraft = { source: { projectId: PROJECT, stateDigest: "e".repeat(64), sourceRunId: "run-entrance-a", sourceStageRef: null },
    commands: [], attempt: null, updatedAt: "2026-09-25T22:20:00Z" };
  const beforeRefusal = writes.length;
  await chipContinue.click();
  await tab.locator(".design-tree-bar__refusal")
    .getByText("Model edits in Modeling are not recorded yet; they are kept. Record them and continue, or undo them in Modeling.").waitFor();
  assert.equal(await toast.count(), 0, "a refused Continue from the chip shows no toast");
  assert.equal(writes.length, beforeRefusal);
  assert.equal(fixture.state.head, "run-entrance-a");
  await shoot(tab, "09b-chip-refusal");
  // Record edits and continue behaves here as in the side card: Modeling records the edits once, then the same Continue.
  revision = fixture.state.revision;
  const recordedBefore = recorded;
  await tab.locator('.design-tree-bar__refusal [data-action="record"]').click();
  await toast.filter({ hasText: "Current is now “B · Twin towers on a podium”" }).waitFor();
  assert.equal(recorded, recordedBefore + 1, "the chip records the edits once, then continues");
  assert.deepEqual(writes.at(-1), { method: "PUT", name: "/api/working-draft",
    body: { projectId: PROJECT, runId: "run-massing-b", baseRevisionSha256: rev(revision), branchId: null } });
  await viewingState.waitFor({ state: "detached" });
  assert.equal(await tab.locator(".design-tree-bar__refusal").count(), 0, "B is Current now: nothing else is viewed");
  await shoot(tab, "09c-chip-continued");
  // Its Undo returns to S3, the Current it replaced.
  revision = fixture.state.revision;
  await toast.getByRole("button", { name: "Undo", exact: true }).click();
  await toast.filter({ hasText: "Undone" }).waitFor();
  assert.deepEqual(writes.at(-1).body, { projectId: PROJECT, runId: "run-entrance-a", baseRevisionSha256: rev(revision), branchId: "main" });
  assert.equal(fixture.state.head, "run-entrance-a");
  await tab.waitForFunction(() => /^S3 — Current/.test((document.querySelector(".stage-chip")?.textContent ?? "").replace(/\s+/g, " ").trim()));
  await chip.click();
  await surface.locator(".design-tree__canvas canvas").first().waitFor();

  // The list shows the same nodes to the keyboard.
  await bar.getByRole("button", { name: "List", exact: true }).click();
  const items = surface.getByRole("treeitem");
  await items.first().waitFor();
  // Four Stages, ten admitted options, two running lines and Current.
  const nodeCount = await tab.evaluate(() => document.querySelectorAll('.design-tree-list [role="treeitem"]').length);
  assert.equal(nodeCount, 17, "every node of the tree is a list item");
  await items.first().focus();
  await tab.keyboard.press("ArrowDown");
  assert.equal(await tab.evaluate(() => document.activeElement?.dataset.node), await items.nth(1).getAttribute("data-node"));
  await tab.keyboard.press("End");
  assert.equal(await tab.evaluate(() => document.activeElement?.dataset.node), await items.last().getAttribute("data-node"));
  await tab.keyboard.press("Enter");
  await surface.locator(".design-tree-card").waitFor();
  await shoot(tab, "10-list");
  await bar.getByRole("button", { name: "Canvas", exact: true }).click();
  await tab.waitForFunction(() => window.__treeApi?.getSceneElements().length > 20);

  // Leaving returns to the previous surface; the chip reopens the tree.
  await bar.getByRole("button", { name: "Back to Modeling" }).click();
  await tab.getByTestId("arch-stub").waitFor();
  assert.equal(await chip.getAttribute("aria-pressed"), "false");
  await tab.getByTestId("workspace-board").click();
  await tab.getByTestId("board-stub").waitFor();
  // Board | Layout are text tabs in the same bar, beside the position.
  const boardModes = bar.getByRole("radiogroup", { name: "Board mode", exact: true });
  assert.deepEqual(await boardModes.getByRole("radio").allInnerTexts(), ["Board", "Layout"]);
  assert.equal(await bar.getByRole("button", { name: "Back to Modeling" }).count(), 0, "the tree's menus leave the bar with the tree");
  await shoot(tab, "10a-board-bar");
  await chip.click();
  await bar.getByRole("button", { name: "Back to Board" }).click();
  await tab.getByTestId("board-stub").waitFor();
  await chip.click();
  await bar.getByRole("button", { name: "Back to Board" }).waitFor();
  await chip.click();
  await tab.getByTestId("board-stub").waitFor();

  // The same copy in Chinese.
  await tab.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
  await tab.waitForFunction(() => (document.querySelector(".stage-chip")?.textContent ?? "").includes("当前"));
  assert.equal((await chip.innerText()).replace(/\s+/g, " ").trim(), "S3 · 当前 · 2 个运行中");
  await chip.click();
  await surface.getByRole("heading", { name: "状态树" }).waitFor();
  await bar.getByRole("button", { name: "列表", exact: true }).waitFor();
  await bar.getByRole("button", { name: "返回画板" }).waitFor();
  await shoot(tab, "11-zh");

  // A result the architect turned down cannot become a Stage: Current's card says so before anyone asks.
  const acceptedHead = fixture.state.head;
  fixture.state.runs.set("run-entrance-x", { parent: acceptedHead, sourceStage: fixture.state.branchHead });
  fixture.state.rejected.add("run-entrance-x");
  fixture.state.head = "run-entrance-x";
  fixture.state.revision += 1;
  await tab.evaluate(() => window.dispatchEvent(new Event("focus")));
  card = await clickNode("current");
  await card.getByText("当前是已被否定的结果，不能成为阶段。请先从已准入的方案继续。").waitFor();
  assert.equal(await card.locator('[data-action="accept"]').isDisabled(), true);
  fixture.state.head = acceptedHead;
  fixture.state.revision += 1;

  // Cancelling an archive restores the previous rejection, never an unreviewed direction.
  await tab.evaluate(() => { window.__workspaceFixture.setLanguage("en"); window.dispatchEvent(new Event("focus")); });
  await card.getByText(/Current is exactly S3/).waitFor();
  const beforeRejectedReview = structuredClone({ draft: fixture.workingDraft(), stages: fixture.designHistory().stages });
  const reviewWritesStart = writes.length;
  // Current's restored trunk can centre the canvas on S3, outside this earlier Study.
  // Review actions use the same node's accessible list row, independent of that view.
  await tab.keyboard.press("Escape");
  await card.waitFor({ state: "detached" });
  await bar.getByRole("button", { name: "List", exact: true }).click();
  const rejectedOption = surface.locator('[role="treeitem"][data-node="candidate:run-massing-a"]');
  await rejectedOption.click();
  card = surface.locator('.design-tree-card[data-node="candidate:run-massing-a"]');
  await card.waitFor();
  await card.getByRole("button", { name: "Reject", exact: true }).click();
  await card.waitFor({ state: "detached" });
  await bar.getByRole("button", { name: "Show processed (1)", exact: true }).click();
  await rejectedOption.click();
  await card.getByText(/Rejected/).waitFor();
  assert.equal(await card.getByRole("button", { name: "Undo archive", exact: true }).count(), 0,
    "a rejection is not presented as an archive that can be cancelled");
  await card.getByRole("button", { name: "Archive", exact: true }).click();
  await card.getByRole("button", { name: "Undo archive", exact: true }).click();
  await card.getByRole("button", { name: "Archive", exact: true }).waitFor();
  await card.getByText(/Rejected/).waitFor();
  assert.equal(reviews.get("candidate:run-massing-a").disposition, "rejected");
  assert.equal(await card.locator("strong").innerText(), "A · Slab bar along the river");
  assert.equal(await bar.getByRole("button", { name: "Hide processed", exact: true }).getAttribute("aria-pressed"), "true",
    "the restored rejection remains visible in processed items");
  assert.deepEqual(writes.slice(reviewWritesStart).map(({ name, body }) => [name, body.action]),
    ["reject", "archive", "restore"].map((action) => ["/api/candidate-reviews", action]));
  assert.deepEqual({ draft: fixture.workingDraft(), stages: fixture.designHistory().stages }, beforeRejectedReview,
    "reject, archive and restore leave Working Head and Stage history unchanged");
  await bar.getByRole("button", { name: "Hide processed", exact: true }).click();
  await card.waitFor({ state: "detached" });
  await context.close();

  // ------------------------------------------------------------------ Part B: the Hub rail's 状态树 entry.
  const hubContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await hubContext.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    external.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const hubPage = await hubContext.newPage();
  hubPage.setDefaultTimeout(20_000);
  hubPage.on("pageerror", (error) => errors.push(`hub: ${error.message}`));
  await hubPage.goto(origin, { waitUntil: "domcontentloaded", timeout: 240_000 });
  const rail = hubPage.getByRole("navigation", { name: "Project tools" });
  await rail.locator('.chat-rail__tool[aria-label="Modeling"]').waitFor({ timeout: 120_000 });
  const workspaces = await rail.getByRole("group", { name: "Surfaces", exact: true }).locator(".chat-rail__tool").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
  assert.ok(workspaces.includes("Design tree"), `the rail's primary group offers the Design tree: ${workspaces}`);
  const railButton = (name) => rail.getByRole("button", { name, exact: true });
  await hubPage.waitForFunction(() => ["Modeling", "Design tree"].every((label) => {
    const button = document.querySelector(`.chat-rail__tool[aria-label="${label}"]`);
    return button && !button.disabled && button.dataset.state === "running";
  }));
  await railButton("Modeling").click();
  await hubPage.getByTestId("arch-stub").waitFor();
  await railButton("Design tree").click();
  const hubSurface = hubPage.locator('.chat-project-workspace:not([hidden]) [data-project-surface="tree"]');
  const hubBar = hubPage.locator('.chat-project-workspace:not([hidden]) .project-bar');
  await hubSurface.locator(".design-tree__canvas canvas").first().waitFor();
  assert.equal(await railButton("Design tree").getAttribute("aria-pressed"), "true");
  assert.match(hubPage.url(), /view=tree/, "the tree has its own deep link");
  await hubPage.waitForFunction(() => window.__treeApi?.getSceneElements().length > 20);
  await hubPage.waitForFunction(() => document.querySelector('.chat-project-workspace:not([hidden]) .design-tree__canvas')?.dataset.level === "mid");
  assert.equal((await scene(hubPage)).elements.some((element) => element.data?.node === "candidate:run-massing-a"), false,
    "a fresh workspace read keeps the restored rejection out of the default projection");
  const hubCard = await clickCanvasNode(hubPage, hubSurface, "current");
  assert.match(await hubCard.innerText(), /Current is exactly S3/, "a narrow panel opens on the growing tip, and its nodes answer clicks");
  await shoot(hubPage, "12-hub-rail-tree");
  // The same floating card covers list rows in this narrow Hub panel until closed.
  await hubPage.keyboard.press("Escape");
  await hubCard.waitFor({ state: "detached" });
  await hubBar.getByRole("button", { name: "Show processed (1)", exact: true }).click();
  await hubBar.getByRole("button", { name: "List", exact: true }).click();
  await hubSurface.locator('[role="treeitem"][data-node="candidate:run-massing-a"]').click();
  const reopenedReview = hubSurface.locator('.design-tree-card[data-node="candidate:run-massing-a"]');
  await reopenedReview.getByText(/Rejected/).waitFor();
  assert.equal(await reopenedReview.locator("strong").innerText(), "A · Slab bar along the river");
  await hubBar.getByRole("button", { name: "Hide processed", exact: true }).click();
  await reopenedReview.waitFor({ state: "detached" });
  await hubBar.getByRole("button", { name: "Canvas", exact: true }).click();
  await hubSurface.locator(".design-tree__canvas canvas").first().waitFor();
  await hubBar.getByRole("button", { name: "Back to Modeling" }).click();
  await hubPage.getByTestId("arch-stub").waitFor();
  assert.equal(await railButton("Modeling").getAttribute("aria-pressed"), "true", "leaving returns to the surface it came from");
  await hubPage.locator(".chat-project-workspace:not([hidden]) .stage-chip").click();
  await hubSurface.locator(".design-tree__canvas canvas").first().waitFor();
  assert.equal(await railButton("Design tree").getAttribute("aria-pressed"), "true", "the chip opens the same surface");
  await hubPage.locator(".chat-project-workspace:not([hidden]) .stage-chip").click();
  await hubPage.getByTestId("arch-stub").waitFor();
  await hubContext.close();

  assert.deepEqual(unexpected, [], "the tree reads only what it declares");
  assert.deepEqual(external, [], "no external request");
  assert.deepEqual(errors.filter((message) => !/Failed to load resource: the server responded with a status of 404/.test(message)), []);
  console.log(JSON.stringify({ passed: "chip → tree, trunk, twigs, planar, three zoom levels, side card, review-open warning, View read-only, Continue re-roots via PUT /api/working-draft, its toast's Undo puts the previous Current back through the same PUT, Accept on Current only via POST accept with a toast and no Undo, a toast stays while hovered and then fades, no toast on refusal, the chip's viewing state continues from here, a rejected Current cannot be accepted, keyboard list, return to previous surface, zh copy, Hub rail entry and deep link",
    writes: writes.map((row) => `${row.method} ${row.name}`) }));
} catch (error) {
  console.error("FAILED:", error);
  console.error(JSON.stringify({ errors, unexpected, external, writes: writes.map((row) => `${row.method} ${row.name}`) }, null, 1));
  process.exitCode = 1;
} finally {
  await browser?.close();
  await vite?.close();
  http.closeAllConnections?.();
  await new Promise((resolve) => http.close(resolve));
  await rm(cacheDir, { recursive: true, force: true });
}
