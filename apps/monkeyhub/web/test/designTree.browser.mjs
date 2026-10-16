/** The Design Tree (#284) in the real ProjectWorkspace and the real Hub rail, on Excalidraw.
 * The Project Runtime is a stub serving the typed fixture (features/designTree/fixture.ts)
 * in the #294 design-history contract until codex/294-admission-record lands; it answers
 * Continue (PUT /api/working-draft) and Accept (POST /api/candidates/{id}/accept) with the
 * existing routes' rules. Arch and Board are replaced by stubs so the tree is what runs.
 * DESIGN_TREE_SCREENSHOTS=<dir> writes review screenshots; --serve keeps the page open.
 */
import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, mkdtemp, rm } from "node:fs/promises";
import { createServer as createHttpServer } from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { deflateSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { createServer } from "vite";

const webRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
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
// #406, #367: the models whose server thumbnail is drawn. S2 and Current are the same model, so they share one image.
// The index holds each drawn one (`projections:<key>`); any other model's is pending until the test draws it.
const PREVIEWED = new Set(["run-massing-b", "run-massing-c", "run-s1-massing", "run-facade-c", "run-s2-layout"]);
const statusReads = [], imageReads = [], lostReads = [];
/** The drawn thumbnails, by model asset. */
const thumbnails = new Map();
// #366: the fixture project's index (its change log: each entity's last move) and the Hub streams that relay its hints.
const index = { revision: 1, facts: null, moved: new Map([["tree", 1], ["working", 1], ["area:working", 1], ["run:run-site", 1]]),
  bodies: new Map() };
const moveIndex = (ids) => { index.revision += 1; for (const id of ids) index.moved.set(id, index.revision); return index.revision; };
const hubStreams = new Set(), treeReads = [];
let indexReads = 0, hubSequence = 1;
const hubSend = (name, body) => {
  hubSequence += 1;
  for (const stream of hubStreams) stream.write(`event: ${name}\ndata: ${JSON.stringify({ serverId: "design-tree-hub", sequence: hubSequence, runtimeId, ...body })}\n\n`);
};
const sha = (value) => createHash("sha256").update(value).digest("hex");
const thumbnailKey = (asset) => sha(`projection:${asset}`);
/**
 * Draw one model's thumbnail, as the projection cache does: the index gains its entity; the revision it moved at.
 * A `lost` blob is named but not served (the cache folder was cleared): the server answers 404 until it is drawn again.
 */
function drawThumbnail(run, asset, revision = null, { blob = sha(`thumbnail:${asset}`), lost = false } = {}) {
  const key = thumbnailKey(asset);
  thumbnails.set(asset, { run, blob, lost });
  index.bodies.set(`projections:${key}`, { key, inputSha256: asset, kind: "model-line-view",
    recipe: { view: "axon", size: 256, style: "lines" }, renderer: "fixture", blobSha256: blob });
  if (revision !== null) { index.moved.set(`projections:${key}`, revision); return revision; }
  return moveIndex([`projections:${key}`]);
}
/** Each run's model, as the tree reads it. */
function modelsByRun() {
  const history = fixture.designHistory("main"), head = fixture.workingSource("modeling").head;
  const sources = [...history.stages.map((stage) => stage.modelSource), ...history.candidates.map((candidate) => candidate.modelSource),
    head?.modelSource];
  return new Map(sources.filter(Boolean).map((source) => [source.runId, source]));
}
/** A small opaque PNG: a sky over a block, tinted per model, so each card's image is its own. */
function previewPng(run) {
  const width = 160, height = 100, hue = parseInt(sha(run).slice(0, 2), 16);
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x += 1) {
      const block = x > 40 && x < 120 && y > 30;
      row.set(block ? [80 + (hue % 120), 90, 110 + (hue >> 2)] : [215, 228, 238 - (y >> 2)], 1 + x * 3);
    }
    rows.push(row);
  }
  const crc = (buffer) => {
    let value = ~0;
    for (const byte of buffer) { value ^= byte; for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ (0xedb88320 & -(value & 1)); }
    return (~value) >>> 0;
  };
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const out = Buffer.alloc(body.length + 8);
    out.writeUInt32BE(data.length, 0); body.copy(out, 4); out.writeUInt32BE(crc(body), body.length + 4);
    return out;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header), chunk("IDAT", deflateSync(Buffer.concat(rows))), chunk("IEND", Buffer.alloc(0))]);
}

const page = (entry) => `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html,body,#root{height:100%;margin:0}</style></head><body><div id="root"></div>
<script type="module">import "/@fs/${slash(path.resolve(webRoot, "../../../packages/web-shared/src/base.css"))}";</script>
<script type="module" src="${entry}"></script></body></html>`;

// The tree's runtime reads and the two writes it may make, over the fixture's retained facts.
async function runtime(request, response, url, body) {
  const json = (value, status = 200) => { response.writeHead(status, { "content-type": "application/json" }); response.end(JSON.stringify(value)); };
  const name = url.pathname, method = request.method;
  try {
    if (method === "GET" && name === "/api/protocol") return json({ protocol: "archflow/2", server: "design-tree-fixture", serverVersion: "test", mode: "local",
      capabilities: ["candidate-admission", "candidate-review", "design-history", "project-trash", "working-draft", "working-source"] });
    if (method === "GET" && name === "/api/project") return json({ projectId: PROJECT, projectDir: "D:\\fixture\\riverside-library",
      published: { version: 2, stateSha256: "0".repeat(64) }, referenceRun: { runId: "run-site", baseVersion: 2, baseSha256: "0".repeat(64) },
      intentProvider: "fixture", intentModel: "fixture" });
    if (method === "GET" && name === "/api/working-source") { treeReads.push(name); return json(fixture.workingSource(url.searchParams.get("workspace") ?? "modeling")); }
    if (method === "GET" && name === "/api/design-history") {
      treeReads.push(name);
      const history = fixture.designHistory(url.searchParams.get("branchId") ?? "main");
      return json({ ...history,
        stages: history.stages.map(stage => ({ ...stage, review: reviews.get(`stage:${stage.stageRef}`) ?? null })),
        candidates: history.candidates.map(candidate => ({ ...candidate, review: reviews.get(`candidate:${candidate.candidateId}`) ?? null })) });
    }
    if (method === "GET" && name === "/api/worktrees") { treeReads.push(name); return json(fixture.worktrees()); }
    // #575: the project trash, and Restore of one cleaned draft.
    if (method === "GET" && name === "/api/trash") return json(fixture.trash());
    if (method === "POST" && name === "/api/trash/restore") { writes.push({ method, name, body }); return json(fixture.restoreTrashed(body)); }
    // #367: a model's thumbnail status (a miss queues it; here, it stays pending until the test draws it) and its bytes.
    if (method === "GET" && name === "/api/projections") {
      const run = url.searchParams.get("runId"), asset = url.searchParams.get("assetSha256"), drawn = thumbnails.get(asset);
      statusReads.push(run);
      response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
      response.end(JSON.stringify({ key: thumbnailKey(asset), status: drawn ? "done" : "pending", kind: "model-line-view",
        recipe: { view: "axon", size: Number(url.searchParams.get("size")), style: "lines" }, renderer: "fixture", inputSha256: asset,
        source: { runId: run, stateDigest: url.searchParams.get("stateDigest"), assetSha256: asset },
        blobSha256: drawn?.blob ?? null, blobUrl: drawn ? `/api/projections/blobs/${drawn.blob}` : null, attempts: drawn ? 1 : 0,
        error: null, loadMs: null, renderMs: null }));
      return;
    }
    const blob = name.match(/^\/api\/projections\/blobs\/([0-9a-f]{64})$/);
    if (method === "GET" && blob) {
      const drawn = [...thumbnails.values()].find((thumbnail) => thumbnail.blob === blob[1]);
      if (drawn?.lost) {
        lostReads.push(drawn.run);
        response.writeHead(404, { "content-type": "application/json", "cache-control": "no-store" });
        response.end(JSON.stringify({ code: "PROJECTION_BLOB_NOT_FOUND", detail: "No projection has these bytes; read its status again." }));
        return;
      }
      if (drawn) {
        imageReads.push(drawn.run);
        response.writeHead(200, { "content-type": "image/png", "cache-control": "private, max-age=31536000, immutable" });
        response.end(previewPng(drawn.run));
        return;
      }
    }
    // The project's event stream, which other workspace code subscribes to; the fixture has no events to send.
    if (method === "GET" && name === "/api/events") {
      response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" });
      response.write(": fixture\n\n");
      return;
    }
    // #366: the project's index, which the Hub's project store reads once the Hub's stream has subscribed.
    // A change of the fixture's facts moves the tree and the working position; the test moves others itself.
    // Most of this fixture's changes come with no index event; the page reads them on focus.
    if (method === "GET" && name === "/api/index") {
      indexReads += 1;
      if (index.facts !== fixture.state.revision) { index.facts = fixture.state.revision; moveIndex(["tree", "working"]); }
      const since = Number(url.searchParams.get("since"));
      const changes = url.searchParams.get("epoch") === "fixture" && since >= 1 && since <= index.revision;
      const entity = ([id, rev]) => ({ id, domain: id.split(":")[0], rev, body: index.bodies.get(id) ?? {} });
      return json({ projectId: PROJECT, epoch: "fixture", revision: index.revision, reset: !changes, from: changes ? since : null,
        to: index.revision, upserts: [...index.moved].filter(([, rev]) => !changes || rev > since).map(entity), deletes: [] });
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
    hubStreams.add(response);
    response.on("close", () => hubStreams.delete(response));
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
      const file = slash(id.split("?")[0]), root = slash(webRoot);
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
      // tree's inspector does. The test wires it until ProjectWorkspace passes it itself; then this is a no-op.
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
  fixtureModule = await vite.ssrLoadModule("/src/features/designTree/fixture.ts");
  fixture = fixtureModule.createDesignTreeFixture();
  for (const [run, source] of modelsByRun()) if (PREVIEWED.has(run)) drawThumbnail(run, source.assetSha256, 1);
  const handle = async (request, response) => {
    const url = new URL(request.url, "http://fixture.test");
    if (url.pathname === "/tree-workspace") {
      response.setHeader("content-type", "text/html");
      response.end(await vite.transformIndexHtml(url.pathname, page("/test/workspace-fixture.tsx"))); return;
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
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_EXECUTABLE
    ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : { channel: "chrome" }) });
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

  // #302: the notice's View opens the tree on the ready option's Study, with its inspector.
  const inChinese = async (name) => {
    await tab.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
    await tab.waitForFunction(() => (document.querySelector(".stage-chip")?.textContent ?? "").includes("当前"));
    await shoot(tab, name);
    await tab.evaluate(() => window.__workspaceFixture.setLanguage("en"));
    await tab.waitForFunction(() => (document.querySelector(".stage-chip")?.textContent ?? "").includes("Current"));
  };
  if (shots) await inChinese("01a-ready-notice-zh");
  await ready.click();
  await tab.locator('[data-project-surface="tree"] .design-tree-inspector[data-node="candidate:run-entrance-a"]').waitFor();
  assert.equal(await chip.getAttribute("aria-pressed"), "true");
  await shoot(tab, "01b-ready-notice-opens-study");
  const people = tab.getByRole("navigation", { name: "People’s working lines" });
  await people.getByRole("button", { name: "Alice · Alice's study" }).waitFor();
  await people.getByRole("button", { name: "Bob · Courtyard study" }).waitFor();
  const beforePeerView = writes.length;
  await people.getByRole("button", { name: "Bob · Courtyard study" }).click();
  await tab.getByTestId("arch-stub").waitFor();
  assert.equal(writes.length, beforePeerView, "viewing another person's line never moves a head or writes a project");
  await tab.getByRole("button", { name: "Back to Current", exact: true }).click();
  assert.equal(writes.length, beforePeerView, "returning from a peer view is also read-only");
  await chip.click();
  await people.waitFor();
  await shoot(tab, "01d-actor-working-heads");

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
  /**
   * A real click on a node's card, where the canvas draws it now; with `reveal: false` the node must already be in view.
   * A fold (#575) opens rather than selects: with `inspect: false` nothing waits for an inspector.
   */
  const clickCanvasNode = async (target, host, id, { reveal = true, inspect = true } = {}) => {
    // #353: the inspector docks at the right and the canvas gives it room; close it so the canvas has its whole width.
    if (await host.locator(".design-tree-inspector").count()) {
      await target.keyboard.press("Escape");
      await host.locator(".design-tree-inspector").waitFor({ state: "detached" });
    }
    // A surface shown again, or widened, is measured by Excalidraw a frame later; click where the canvas is now.
    const measured = () => target.waitForFunction(() => {
      const state = window.__treeApi?.getAppState();
      const box = [...document.querySelectorAll(".design-tree__canvas .excalidraw")].find((element) => element.getClientRects().length)
        ?.getBoundingClientRect();
      return Boolean(state && box && state.width > 40 && Math.abs(state.offsetLeft - box.left) < 1 && Math.abs(state.offsetTop - box.top) < 1
        && Math.abs(state.width - box.width) < 1) && { width: box.width, height: box.height };
    }).then((handle) => handle.jsonValue());
    const box = await measured();
    let current = await scene(target);
    const card = current.elements.find((element) => element.id === `${id}:card`);
    assert.ok(card, `${id} is on the canvas`);
    const at = (view) => [(card.x + card.width / 2 + view.scrollX) * view.zoom, (card.y + card.height / 2 + view.scrollY) * view.zoom];
    let [x, y] = at(current);
    // A node the view leaves out, or under the column headers, is brought into sight first, the way a viewer pans to it.
    if (x < 24 || x > box.width - 24 || y < 60 || y > box.height - 24) {
      assert.ok(reveal, `${id} is in view`);
      await target.evaluate(({ x: sceneX, y: sceneY }) => {
        const api = window.__treeApi, state = api.getAppState();
        api.updateScene({ appState: { scrollX: state.width / (2 * state.zoom.value) - sceneX, scrollY: state.height / (2 * state.zoom.value) - sceneY } });
      }, { x: card.x + card.width / 2, y: card.y + card.height / 2 });
      current = await scene(target);
      [x, y] = at(current);
    }
    await target.mouse.click(x + current.offsetLeft, y + current.offsetTop);
    if (!inspect) return null;
    const opened = host.locator(`.design-tree-inspector[data-node="${id}"]`);
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
  // #353: each Stage heads a column of the options that led to it; Current's work trails them, towards S3.
  const headers = () => surface.locator(".design-tree-column").evaluateAll((columns) => columns.map((column) =>
    [...column.querySelectorAll(".design-tree-column__id, .design-tree-column__name, .design-tree-column__count")].map((part) => part.textContent).join(" ")));
  assert.deepEqual(await headers(), ["S0 Site", "S1 Massing 5", "S2 Layout 4", "S3 Next Stage 1"]);
  await shoot(tab, "02-tree-fit");
  // Panned until a column's left edge has passed the canvas's, its header's words stay pinned at the left.
  const canvasBox = await surface.locator(".design-tree__canvas").boundingBox();
  const massingColumn = surface.locator(`.design-tree-column[data-node="${S1}"]`);
  const panBy = (await massingColumn.boundingBox()).x - canvasBox.x + 120;
  await tab.evaluate((pixels) => {
    const api = window.__treeApi, state = api.getAppState();
    api.updateScene({ appState: { scrollX: state.scrollX - pixels / state.zoom.value } });
  }, panBy);
  await tab.waitForFunction((left) => {
    const column = [...document.querySelectorAll(".design-tree-column")].find((element) => element.querySelector(".design-tree-column__name")?.textContent === "Massing");
    return column && column.getBoundingClientRect().x < left - 100;
  }, canvasBox.x);
  const pinned = await massingColumn.locator(".design-tree-column__title").boundingBox();
  assert.ok(Math.abs(pinned.x - (canvasBox.x + 10)) < 1.5 && pinned.width > 40, `the Massing header stays in view: ${JSON.stringify(pinned)}`);
  assert.equal(await massingColumn.locator(".design-tree-column__name").isVisible(), true);
  await shoot(tab, "02c-header-pinned");
  await bar.getByRole("button", { name: "Fit", exact: true }).click();
  await tab.waitForFunction((left) => {
    const box = [...document.querySelectorAll(".design-tree-column")][1]?.getBoundingClientRect();
    return box && box.x > left;
  }, canvasBox.x);

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

  // Semantic zoom (#406): far shows dots, the trunk and counts; middle, text cards; close, summaries and status, and on the
  // cards in view the server thumbnail of their model (#367), each asked for and downloaded once. This page has no Hub
  // store, so it asks the projection cache itself; nothing is read at far or middle, or off screen.
  const zoomShots = await mkdtemp(path.join(tmpdir(), "design-tree-zoom-"));
  const shootLevel = async (name) => {
    await tab.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await surface.locator(".design-tree__canvas").screenshot({ path: path.join(zoomShots, `${name}.png`) });
  };
  if (await surface.locator(".design-tree-inspector").count()) {
    await tab.keyboard.press("Escape");
    await surface.locator(".design-tree-inspector").waitFor({ state: "detached" });
  }
  statusReads.length = 0;
  imageReads.length = 0;
  const images = async () => (await scene()).elements.filter((element) => element.type === "image");
  const runOf = (id) => id === "current" ? fixture.state.head : id.startsWith("candidate:") ? id.slice("candidate:".length)
    : fixture.state.stages.find((row) => `stage:${row.ref}` === id)?.run ?? null;
  /** The nodes whose cards are in the canvas's view now. */
  const inView = () => tab.evaluate(() => {
    const api = window.__treeApi, state = api.getAppState(), zoom = state.zoom.value;
    const left = -state.scrollX, top = -state.scrollY, right = left + state.width / zoom, bottom = top + state.height / zoom;
    return api.getSceneElements().filter((element) => element.id.endsWith(":card") && element.x < right && element.x + element.width > left
      && element.y < bottom && element.y + element.height > top).map((element) => element.customData.tree.node);
  });
  await shootLevel("mid");
  assert.equal(await level(), "mid");
  assert.deepEqual(await images(), [], "middle: text cards, no images");
  const box = await surface.locator(".design-tree__canvas").boundingBox();
  await tab.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await tab.mouse.wheel(0, 320);
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "far");
  view = await scene();
  assert.ok(view.elements.some((element) => element.data?.role === "dot") && view.elements.some((element) => element.data?.role === "fork"), "far: dots and counts");
  assert.equal(view.elements.filter((element) => element.data?.role === "letter").length, 0, "far: no option cards");
  assert.ok(view.elements.some((element) => element.data?.role === "fork" && /5 options · 1 continued/.test(element.text ?? "")));
  assert.deepEqual(await images(), [], "far: no images");
  await shootLevel("far");
  await shoot(tab, "03-tree-far");
  assert.deepEqual([...statusReads, ...imageReads], [], "nothing is read at the middle or far level");
  // The hysteresis: back just over the far edge stays far, a little further is the middle level.
  const zoomTo = (zoom) => tab.evaluate((value) => {
    const api = window.__treeApi, state = api.getAppState();
    const cx = state.width / (2 * state.zoom.value) - state.scrollX, cy = state.height / (2 * state.zoom.value) - state.scrollY;
    api.updateScene({ appState: { zoom: { value }, scrollX: state.width / (2 * value) - cx, scrollY: state.height / (2 * value) - cy } });
  }, zoom);
  const settleFrames = () => tab.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await zoomTo(0.5); await settleFrames();
  assert.equal(await level(), "far", "50 % is still far after far");
  await zoomTo(0.6);
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "mid");
  await zoomTo(1.25); await settleFrames();
  assert.equal(await level(), "mid", "125 % is still the middle level coming from it");
  assert.deepEqual([...statusReads, ...imageReads], [], "nothing is read below the close level");
  // Close, centred on Massing B: the cards in view whose thumbnail is drawn get their images, and only cards in view are read.
  const centre = (id, zoom) => tab.evaluate(({ id: node, zoom: value }) => {
    const api = window.__treeApi, state = api.getAppState();
    const card = api.getSceneElements().find((element) => element.id === `${node}:card`);
    api.updateScene({ appState: { zoom: { value }, scrollX: state.width / (2 * value) - (card.x + card.width / 2),
      scrollY: (state.height + 36) / (2 * value) - (card.y + card.height / 2) } });
  }, { id, zoom });
  await centre("candidate:run-massing-b", 1.5);
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "close");
  /** Every previewed card in view shows its image; an image read earlier may stay on a card the view has left. */
  const expectImages = async () => {
    const visible = await inView();
    const wanted = visible.filter((id) => PREVIEWED.has(runOf(id))).sort();
    await tab.waitForFunction((ids) => {
      const drawn = new Set(window.__treeApi.getSceneElements().filter((element) => element.type === "image").map((element) => element.customData.tree.node));
      return ids.every((id) => drawn.has(id));
    }, wanted);
    const drawn = (await images()).map((element) => element.data.node);
    assert.ok(drawn.every((id) => PREVIEWED.has(runOf(id))), `images only where a thumbnail is drawn: ${drawn}`);
    assert.equal(new Set(drawn).size, drawn.length, "one image per card");
    return { visible, wanted, drawn };
  };
  let shown = await expectImages();
  assert.ok(shown.wanted.includes("candidate:run-massing-b") && shown.wanted.length >= 2, `close: images on the cards in view: ${shown.wanted}`);
  assert.deepEqual(shown.drawn.sort(), shown.wanted, "the first close view draws exactly its previewed cards");
  const offScreen = [...PREVIEWED].filter((run) => !shown.visible.some((id) => runOf(id) === run));
  assert.ok(offScreen.length > 0, "some previewed models are off screen");
  assert.ok(statusReads.every((run) => shown.visible.some((id) => runOf(id) === run)), `only cards in view are read: ${statusReads}`);
  assert.ok(offScreen.every((run) => !statusReads.includes(run) && !imageReads.includes(run)), "off-screen models are not read");
  const pending = shown.visible.filter((id) => id.startsWith("candidate:") && !PREVIEWED.has(runOf(id)));
  assert.ok(pending.length > 0 && pending.every((id) => statusReads.includes(runOf(id))),
    "a card in view whose thumbnail is not drawn yet asked for it, and keeps its words meanwhile");
  view = await scene();
  assert.ok(view.elements.some((element) => element.data?.role === "summary"), "close: summaries on the cards without images");
  assert.ok(view.elements.some((element) => element.data?.role === "status"), "close: status");
  assert.ok(await tab.evaluate(() => Object.keys(window.__treeApi.getFiles()).length > 0), "the images are the canvas's files");
  await tab.waitForTimeout(500); // Excalidraw decodes a new file before it draws it.
  await shootLevel("close");
  await shoot(tab, "04-tree-close");
  // Along the trunk to Current: S2 and Current share one model, so one download gives both their image.
  await centre("current", 1.5);
  shown = await expectImages();
  assert.ok(shown.wanted.includes("current"), `Current shows its model: ${shown.wanted}`);
  await centre("candidate:run-massing-b", 1.5);
  await expectImages();
  await settleFrames();
  const counts = (reads) => reads.reduce((all, run) => ({ ...all, [run]: (all[run] ?? 0) + 1 }), {});
  assert.ok(Object.values(counts(statusReads)).every((count) => count === 1), `one question per model: ${JSON.stringify(counts(statusReads))}`);
  assert.ok(Object.values(counts(imageReads)).every((count) => count === 1), `one download per image: ${JSON.stringify(counts(imageReads))}`);
  // A click on the image opens the card's inspector, as a click on its words does. (S1's, so no option counts as seen.)
  const picture = (await images()).find((element) => element.data.node === S1);
  const now = await scene();
  await tab.mouse.click((picture.x + picture.width / 2 + now.scrollX) * now.zoom + now.offsetLeft, (picture.y + picture.height / 2 + now.scrollY) * now.zoom + now.offsetTop);
  const inspector = surface.locator(`.design-tree-inspector[data-node="${S1}"]`);
  await inspector.waitFor();
  // #409: the inspector's thumbnail is the canvas's image, downloaded once for both.
  await inspector.locator(".model-thumbnail img").waitFor();
  assert.equal(imageReads.filter((run) => run === runOf(S1)).length, 1, "the inspector does not download its card's image again");
  await tab.keyboard.press("Escape");
  await surface.locator(".design-tree-inspector").waitFor({ state: "detached" });
  // Out again: no images, and panning the middle level over drawn cards reads nothing more.
  await tab.waitForTimeout(300);
  const reads = statusReads.length + imageReads.length;
  await bar.getByRole("button", { name: "Fit", exact: true }).click();
  await tab.waitForFunction(() => document.querySelector(".design-tree__canvas")?.dataset.level === "mid");
  await tab.waitForFunction(() => !window.__treeApi.getSceneElements().some((element) => element.type === "image"));
  await centre("candidate:run-facade-c", 1.05);
  await settleFrames();
  assert.equal(await level(), "mid");
  assert.deepEqual(await images(), []);
  assert.equal(statusReads.length + imageReads.length, reads, "zoomed out, nothing more is read");
  console.log(`design tree zoom levels: ${zoomShots} (far.png, mid.png, close.png)`);
  // Fit again; the level is already the middle one, so wait for the fitted view itself before anything clicks on it.
  const fitted = await tab.evaluate(() => window.__treeApi.getAppState().zoom.value);
  await bar.getByRole("button", { name: "Fit", exact: true }).click();
  await tab.waitForFunction((before) => window.__treeApi.getAppState().zoom.value !== before, fitted);
  await tab.waitForFunction(() => new Promise((resolve) => {
    const read = () => { const state = window.__treeApi.getAppState(); return `${state.zoom.value},${state.scrollX},${state.scrollY}`; };
    const first = read();
    requestAnimationFrame(() => requestAnimationFrame(() => resolve(read() === first)));
  }));
  assert.equal(await level(), "mid");

  // Clicking a node shows its inspector; Accept exists on Current only.
  const clickNode = (id) => clickCanvasNode(tab, surface, id);
  let card = await clickNode("candidate:run-massing-d");
  assert.equal(await card.locator("strong").innerText(), "D · Terraced wedge");
  assert.match(await card.innerText(), /Option · Massing Study/);
  assert.match(await card.innerText(), /Admitted by\s*Agent \(on your words in chat\)/);
  assert.equal(await card.getByRole("button", { name: "View", exact: true }).isEnabled(), true);
  assert.equal(await card.getByRole("button", { name: "Compare with original", exact: true }).isEnabled(), true,
    "an option opens beside the model it was made from (#284)");
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
  // The header strip takes its own clicks: nothing under it is picked, and the open inspector stays.
  const strip = await surface.locator(".design-tree__strip").boundingBox();
  await tab.mouse.click(strip.x + strip.width / 3, strip.y + strip.height / 2);
  await tab.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await surface.locator('.design-tree-inspector[data-node="candidate:run-massing-d"]').count(), 1, "a click on the headers leaves the selection");
  card = await clickNode(S1);
  assert.match(await card.innerText(), /Stage · accepted checkpoint/);
  assert.equal(await card.locator('[data-action="accept"]').count(), 0, "a Stage cannot be accepted again");
  await card.getByRole("button", { name: "Endorse direction", exact: true }).click();
  await card.getByText("Endorsed direction", { exact: true }).waitFor();
  assert.match(await card.innerText(), /Endorsed by\s*Review Architect/);
  writes.length = 0;
  card = await clickNode("candidate:run-facade-c");
  assert.match(await card.locator(".design-tree-inspector__warning").innerText(), /review checks did not pass \(1\)/,
    "an option admitted although review checks did not pass says so in its inspector");
  assert.match(await card.innerText(), /Checks\s*1 violated/, "its Checks row says what its red bar shows");
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
  // The toast is the one confirmation: the inspector's refusal is gone and says nothing in its place.
  assert.equal(await toast.count(), 1, "one toast confirms the Continue");
  await card.locator('[role="alert"]').waitFor({ state: "detached" });
  assert.equal(await card.getByText(/Current is now|Model edits in Modeling/).count(), 0, "the inspector repeats neither the refusal nor the toast");
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

  // Continue on the newest Stage's option, then Accept as next Stage from Current only. That option was asked for
  // alone (#575 rule 3): continued, it is Current itself, one more step after S2, and no card of its own.
  card = await clickNode("candidate:run-entrance-a");
  assert.match(await card.innerText(), /Study\s+Study from S2 · Layout/, "a Study without a name is named after where it started");
  await card.getByRole("button", { name: "Continue from here", exact: true }).click();
  await toast.filter({ hasText: "Current is now “Courtyard gate on the south bar”" }).waitFor();
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 6);
  checkTree(await scene(), [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, "current"], { "study-massing": 4, "study-facade": 2 });
  assert.equal((await scene()).elements.some((element) => element.id === "candidate:run-entrance-a:card"), false, "the lone option is Current");
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
  await tab.waitForFunction(() => window.__treeApi.getSceneElements().find((element) => element.customData?.tree?.role === "trunk")?.points.length === 7);
  checkTree(await scene(), [S0, "candidate:run-massing-c", S1, "candidate:run-facade-b", S2, S3, "current"], { "study-massing": 4, "study-facade": 2 });
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
  // Record edits and continue behaves here as in the inspector: Modeling records the edits once, then the same Continue.
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
  // Four Stages, nine admitted options (the tenth, asked for alone, is S3 itself now), two running lines and Current.
  const nodeCount = await tab.evaluate(() => document.querySelectorAll('.design-tree-list [role="treeitem"]').length);
  assert.equal(nodeCount, 16, "every node of the tree is a list item");
  await items.first().focus();
  await tab.keyboard.press("ArrowDown");
  assert.equal(await tab.evaluate(() => document.activeElement?.dataset.node), await items.nth(1).getAttribute("data-node"));
  await tab.keyboard.press("End");
  assert.equal(await tab.evaluate(() => document.activeElement?.dataset.node), await items.last().getAttribute("data-node"));
  await tab.keyboard.press("Enter");
  await surface.locator(".design-tree-inspector").waitFor();
  await shoot(tab, "10-list");
  // #353: docked beside the list, the inspector leaves focus on the row. Narrower than 560 px it covers the list,
  // so focus goes into it, and back to the row when it closes.
  const lastRow = await items.last().getAttribute("data-node");
  assert.equal(await tab.evaluate(() => document.activeElement?.dataset.node), lastRow);
  await tab.keyboard.press("Escape");
  await surface.locator(".design-tree-inspector").waitFor({ state: "detached" });
  await tab.setViewportSize({ width: 520, height: 900 });
  await items.last().focus();
  await tab.keyboard.press("Enter");
  const overlay = surface.locator(".design-tree-inspector");
  await overlay.waitFor();
  assert.equal(await overlay.evaluate((element) => getComputedStyle(element).position), "absolute", "under 560 px the inspector covers the list");
  await tab.waitForFunction(() => document.activeElement?.classList.contains("design-tree-inspector"));
  await shoot(tab, "10b-list-overlay");
  await tab.keyboard.press("Escape");
  await overlay.waitFor({ state: "detached" });
  await tab.waitForFunction((row) => document.activeElement?.dataset.node === row, lastRow);
  await tab.setViewportSize({ width: 1440, height: 900 });
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
  card = surface.locator('.design-tree-inspector[data-node="candidate:run-massing-a"]');
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
  // A narrow panel opens on the growing tip: Current's card is in view, below the headers, before anything moves it.
  const tip = await hubPage.evaluate(() => {
    const api = window.__treeApi, state = api.getAppState(), zoom = state.zoom.value;
    const card = api.getSceneElements().find((element) => element.id === "current:card");
    const canvas = document.querySelector(".chat-project-workspace:not([hidden]) .design-tree__canvas").getBoundingClientRect();
    return { left: (card.x + state.scrollX) * zoom, right: (card.x + card.width + state.scrollX) * zoom, top: (card.y + state.scrollY) * zoom,
      bottom: (card.y + card.height + state.scrollY) * zoom, width: canvas.width, height: canvas.height };
  });
  assert.ok(tip.left >= 0 && tip.right <= tip.width && tip.top >= 36 && tip.bottom <= tip.height, `Current is in view: ${JSON.stringify(tip)}`);
  const hubCard = await clickCanvasNode(hubPage, hubSurface, "current", { reveal: false });
  assert.match(await hubCard.innerText(), /Current is exactly S3/, "a narrow panel opens on the growing tip, and its nodes answer clicks");
  await shoot(hubPage, "12-hub-rail-tree");
  // Closed, the inspector gives this narrow Hub panel its whole width back.
  await hubPage.keyboard.press("Escape");
  await hubCard.waitFor({ state: "detached" });
  await hubBar.getByRole("button", { name: "Show processed (1)", exact: true }).click();
  await hubBar.getByRole("button", { name: "List", exact: true }).click();
  await hubSurface.locator('[role="treeitem"][data-node="candidate:run-massing-a"]').click();
  const reopenedReview = hubSurface.locator('.design-tree-inspector[data-node="candidate:run-massing-a"]');
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
  // #367, #409: in the Hub the store already names every drawn thumbnail, so opening the tree close up asks the projection
  // cache nothing for them, and each image is downloaded once. A thumbnail drawn later reaches its card through the
  // index (`index.committed`, domain `projections`): its card asked once and is told, with no polling.
  statusReads.length = 0;
  imageReads.length = 0;
  const hubLevel = (value) => hubPage.waitForFunction((wanted) =>
    document.querySelector(".chat-project-workspace:not([hidden]) .design-tree__canvas")?.dataset.level === wanted, value);
  const hubView = await hubPage.evaluate(() => {
    const state = window.__treeApi.getAppState();
    return { zoom: state.zoom, scrollX: state.scrollX, scrollY: state.scrollY };
  });
  await hubPage.evaluate(({ node, value }) => {
    const api = window.__treeApi, state = api.getAppState();
    const card = api.getSceneElements().find((element) => element.id === `${node}:card`);
    api.updateScene({ appState: { zoom: { value }, scrollX: state.width / (2 * value) - (card.x + card.width / 2),
      scrollY: (state.height + 36) / (2 * value) - (card.y + card.height / 2) } });
  }, { node: "candidate:run-massing-b", value: 1.5 });
  await hubLevel("close");
  const hubInView = await hubPage.evaluate(() => {
    const api = window.__treeApi, state = api.getAppState(), zoom = state.zoom.value;
    const left = -state.scrollX, top = -state.scrollY, right = left + state.width / zoom, bottom = top + state.height / zoom;
    return api.getSceneElements().filter((element) => element.id.endsWith(":card") && element.x < right && element.x + element.width > left
      && element.y < bottom && element.y + element.height > top).map((element) => element.customData.tree.node);
  });
  const hubImageOn = (ids) => hubPage.waitForFunction((wanted) => wanted.every((id) => window.__treeApi.getSceneElements()
    .some((element) => element.type === "image" && element.customData?.tree?.node === id)), ids);
  const drawnInView = hubInView.filter((id) => PREVIEWED.has(runOf(id)));
  assert.ok(drawnInView.length >= 2, `drawn thumbnails in view: ${drawnInView}`);
  await hubImageOn(drawnInView);
  assert.deepEqual(statusReads.filter((run) => PREVIEWED.has(run)), [], "no status request for a thumbnail the store names");
  const arriving = hubInView.find((id) => id.startsWith("candidate:") && !PREVIEWED.has(runOf(id)));
  assert.ok(arriving, "a card in view whose thumbnail is not drawn yet");
  const arrivingRun = runOf(arriving);
  for (let round = 0; round < 100 && !statusReads.includes(arrivingRun); round += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.ok(statusReads.includes(arrivingRun), "the card in view asked for its thumbnail: a miss queues it");
  assert.equal((await scene(hubPage)).elements.some((element) => element.type === "image" && element.data?.node === arriving), false,
    "pending: the card keeps its words");
  const drawnAt = drawThumbnail(arrivingRun, modelsByRun().get(arrivingRun).assetSha256);
  hubSend("index", { index: { epoch: "fixture", revision: drawnAt, domains: ["projections"] } });
  await hubImageOn([arriving]);
  assert.equal(statusReads.filter((run) => run === arrivingRun).length, 1, "asked once, then told by the index");
  assert.equal(imageReads.filter((run) => run === arrivingRun).length, 1, "its image is downloaded once");
  const hubCounts = imageReads.reduce((all, run) => ({ ...all, [run]: (all[run] ?? 0) + 1 }), {});
  assert.ok(Object.values(hubCounts).every((count) => count === 1), `one download per image: ${JSON.stringify(hubCounts)}`);
  // #367: a blob the server lost (its cache folder was cleared) answers 404 while the server draws it again. The mounted
  // card shows its words meanwhile, and the image once the index names the redrawn projection, with no remount.
  const mounted = await hubPage.evaluate(() => { window.__treeMounted = window.__treeApi; return true; });
  const arrivingAsset = modelsByRun().get(arrivingRun).assetSha256, redrawn = sha(`thumbnail-redrawn:${arrivingAsset}`);
  const lostAt = drawThumbnail(arrivingRun, arrivingAsset, null, { blob: redrawn, lost: true });
  hubSend("index", { index: { epoch: "fixture", revision: lostAt, domains: ["projections"] } });
  for (let round = 0; round < 100 && !lostReads.includes(arrivingRun); round += 1) await new Promise((resolve) => setTimeout(resolve, 50));
  assert.ok(mounted && lostReads.includes(arrivingRun), "the card read the redrawn blob, which the server has lost");
  await hubPage.waitForFunction((id) => !window.__treeApi.getSceneElements()
    .some((element) => element.type === "image" && element.customData?.tree?.node === id), arriving);
  thumbnails.get(arrivingAsset).lost = false;
  const redrawnAt = moveIndex([`projections:${thumbnailKey(arrivingAsset)}`]);
  hubSend("index", { index: { epoch: "fixture", revision: redrawnAt, domains: ["projections"] } });
  await hubPage.waitForFunction(({ id, file }) => window.__treeApi.getSceneElements().some((element) => element.type === "image"
    && element.customData?.tree?.node === id && String(element.fileId).includes(file)), { id: arriving, file: redrawn.slice(0, 32) });
  assert.equal(await hubPage.evaluate(() => window.__treeApi === window.__treeMounted), true, "the same canvas: no remount");
  assert.equal(lostReads.filter((run) => run === arrivingRun).length, 1, "the lost blob was read once, not retried in a loop");
  console.log(JSON.stringify({ hubTreeOpenedClose: { statusRequestsForDrawnThumbnails: 0, imagesDownloaded: imageReads.length,
    arrivingThumbnail: { statusRequests: 1, downloads: 1 }, lostBlobRedrawn: { failedReads: 1, remounted: false } } }));
  await hubPage.evaluate((view) => window.__treeApi.updateScene({ appState: view }), hubView);
  await hubLevel("mid");
  await hubPage.locator(".chat-project-workspace:not([hidden]) .stage-chip").click();
  await hubPage.getByTestId("arch-stub").waitFor();

  // #366: Modeling saves its local recovery 250 ms after each edit, the Board its scene 700 ms after each
  // change, and a drawing page its annotations once per stroke. The index moves the working pointer's file
  // (`area:working`) for the first, and what the Board's or the document's run keeps aside (`aside:<run>`)
  // for the others, never the run itself; the Hub relays each hint. The tree, mounted for the whole
  // project, shows none of them, so twenty of each read it no more. A new candidate or a Stage reads it
  // once. Its reads never overlap, a job that moved reads it once, and a replayed job event not at all.
  const quiet = async (label) => {
    let seen = -1;
    for (let round = 0; round < 40 && seen !== treeReads.length + indexReads; round += 1) {
      seen = treeReads.length + indexReads;
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    assert.equal(seen, treeReads.length + indexReads, `${label}: the page settled`);
  };
  const hint = (revision, domains) => hubSend("index", { index: { epoch: "fixture", revision, domains } });
  const count = () => ({ worktrees: treeReads.filter((name) => name === "/api/worktrees").length,
    history: treeReads.filter((name) => name === "/api/design-history").length,
    workingSource: treeReads.filter((name) => name === "/api/working-source").length, index: indexReads });
  const since = (before) => Object.fromEntries(Object.entries(count()).map(([key, value]) => [key, value - before[key]]));
  await quiet("before the edits");
  let before = count();
  const saves = {};
  for (const [label, moves, domains, every] of [
    ["twentyAutosaves", ["area:working"], ["area"], 250],
    ["twentyBoardSaves", ["aside:studio-board"], ["aside"], 100],
    ["twentyAnnotationSaves", ["aside:run-site"], ["aside"], 100],
  ]) {
    before = count();
    for (let save = 0; save < 20; save += 1) {
      hint(moveIndex(moves), domains);
      await new Promise((resolve) => setTimeout(resolve, every));
    }
    await quiet(`after ${label}`);
    saves[label] = since(before);
    assert.deepEqual({ ...saves[label], index: undefined }, { worktrees: 0, history: 0, workingSource: 0, index: undefined },
      `${label}: nothing the tree shows moved, so no tree read, no Worktree Graph`);
    assert.ok(saves[label].index >= 1 && saves[label].index <= 20, `${label}: the store follows the hints, one request at a time: ${saves[label].index}`);
  }
  console.log(JSON.stringify(saves));
  const once = {};
  for (const [label, moves, domains] of [
    ["continueElsewhere", ["working"], ["working"]],
    ["newCandidate", ["run:run-new"], ["run"]],
    ["newStage", ["tree", "run:run-new"], ["tree", "run"]],
  ]) {
    before = count();
    hint(moveIndex(moves), domains);
    await quiet(`after ${label}`);
    once[label] = since(before).worktrees;
    assert.equal(once[label], 1, `${label}: the tree moved, and is read exactly once`);
  }
  console.log(JSON.stringify(once));
  before = count();
  hubSend("studio", { stream: "worker-0", replay: true, studio: { seq: 9, at: "2026-09-28T00:00:00Z", type: "candidate.succeeded", candidateId: "run-old" } });
  await quiet("after a replayed job event");
  assert.equal(since(before).worktrees, 0, "a job event the Hub replays as a connection opens is not news");
  // The panel closed: the project's workspace stays mounted but hidden, and its tree reads nothing for a
  // job until it is shown again, then once.
  await railButton("Modeling").click();
  await hubPage.locator(".chat-project-workspace[hidden]").waitFor({ state: "attached" });
  before = count();
  for (const [seq, type] of [[7, "candidate.queued"], [8, "candidate.running"], [9, "candidate.succeeded"]]) {
    hubSend("studio", { stream: "worker-1", studio: { seq, at: "2026-09-28T00:00:00Z", type, candidateId: "run-hidden" } });
  }
  await quiet("after three job events off screen");
  const hidden = since(before).worktrees;
  assert.equal(hidden, 0, "a hidden tree reads nothing for a job");
  await railButton("Modeling").click();
  await hubPage.getByTestId("arch-stub").waitFor();
  await quiet("after showing the project again");
  const shownAgain = since(before).worktrees;
  assert.equal(shownAgain, 1, "shown again, it catches up once");
  console.log(JSON.stringify({ jobEventsWhileHidden: hidden, onShowingAgain: shownAgain }));
  before = count();
  hubSend("studio", { stream: "worker-1", studio: { seq: 1, at: "2026-09-28T00:00:00Z", type: "candidate.queued", candidateId: "run-new" } });
  await quiet("after a job queued");
  assert.equal(since(before).worktrees, 1, "a job's lifecycle reads the tree's running work, which no commit announces");
  before = count();
  for (const [seq, type] of [[2, "candidate.running"], [3, "candidate.succeeded"], [4, "candidate.queued"], [5, "candidate.running"], [6, "candidate.failed"]]) {
    hubSend("studio", { stream: "worker-1", studio: { seq, at: "2026-09-28T00:00:00Z", type, candidateId: "run-new" } });
  }
  await quiet("after five job events at once");
  const burst = since(before).worktrees;
  assert.ok(burst >= 1 && burst <= 2, `five events at once: one read and at most one after it, never overlapping: ${burst}`);
  console.log(JSON.stringify({ jobQueued: 1, fiveJobEventsAtOnce: burst }));
  await hubContext.close();

  // ------------------------------------------------------------------ Part C: one line plus nodes (#575).
  // A project that admitted and staged nothing, shaped like the owner's: Current's line of continued runs and two
  // earlier attempts at V3 that the line superseded. The tree draws the line as one chain whose earlier steps fold
  // behind one control, the drafts fold where the line moved on, and returning to a step is the existing Continue.
  fixture = fixtureModule.createDesignTreeFixture(fixtureModule.unadmittedLineFacts());
  const lineContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "en-US" });
  await lineContext.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    external.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const linePage = await lineContext.newPage();
  linePage.setDefaultTimeout(20_000);
  linePage.on("pageerror", (error) => errors.push(`line: ${error.message}`));
  linePage.on("console", (message) => { if (message.type() === "error") errors.push(`line: ${message.text()}`); });
  await linePage.goto(`${origin}/tree-workspace?lang=en&theme=light&view=tree`, { waitUntil: "domcontentloaded", timeout: 240_000 });
  const lineSurface = linePage.locator('[data-project-surface="tree"]');
  const lineBar = linePage.locator(".project-bar");
  await lineSurface.locator(".design-tree__canvas canvas").first().waitFor({ timeout: 120_000 });
  const trunkPoints = (count) => linePage.waitForFunction((wanted) => window.__treeApi?.getSceneElements()
    .find((element) => element.customData?.tree?.role === "trunk")?.points.length === wanted, count);
  await trunkPoints(3);
  const texts = (view, node) => view.elements.filter((element) => element.type === "text" && element.data?.node === node).map((element) => element.text);
  let lineView = await scene(linePage);
  checkTree(lineView, ["origin", "fold", "current"], {});
  assert.deepEqual(texts(lineView, "fold"), ["4 earlier steps", "2 superseded drafts inside"], "the earlier steps fold behind one control");
  assert.ok(!lineView.elements.some((element) => /run-|studio-projection|conflict|diverged|[0-9a-f]{16}/i.test(element.text ?? "")),
    "no ids, conflicts or diverged lines on the canvas");
  // #575 rule 5: Current says the words that asked for it first, then where it stands, each clipped to the card.
  assert.match(texts(lineView, "current").join(" ").replace(/\s+/g, " "), /“Give V3's walls and ribs their… 1 edit after V3 - ribbed roof,/);
  assert.deepEqual(await lineBar.getByRole("button", { name: /earlier steps|superseded drafts/ }).allInnerTexts(),
    ["Unfold 4 earlier steps", "Show superseded drafts (2)"]);
  await shoot(linePage, "13-line-folded");
  if (shots) {
    await linePage.evaluate(() => window.__workspaceFixture.setLanguage("zh-CN"));
    await linePage.waitForFunction(() => window.__treeApi.getSceneElements().some((element) => element.text === "之前 4 步"));
    await shoot(linePage, "13a-line-folded-zh");
    await linePage.evaluate(() => window.__workspaceFixture.setLanguage("en"));
    await linePage.waitForFunction(() => window.__treeApi.getSceneElements().some((element) => element.text === "4 earlier steps"));
  }
  // A click on the fold opens the steps, oldest first; the drafts fold where the line moved on through V3.
  await clickCanvasNode(linePage, lineSurface, "fold", { inspect: false });
  await trunkPoints(6);
  lineView = await scene(linePage);
  const lineSteps = ["run-site-massing", "run-v1", "run-v2", "run-v3"].map((run) => `step:${run}`);
  checkTree(lineView, ["origin", ...lineSteps, "current"], {});
  assert.deepEqual(lineSteps.map((id) => texts(lineView, id).join(" ")),
    ["Step 1", "V1 - timber frame and hemp walls", "V2 - pitched roof, open gable ends", "V3 - ribbed roof, open side triangles"]);
  const draftsGroup = lineView.elements.find((element) => element.id === "drafts:step:run-v2:card");
  assert.ok(draftsGroup && draftsGroup.opacity < 100 && draftsGroup.strokeStyle === "dashed", "the drafts fold quietly under V2");
  assert.deepEqual(texts(lineView, "drafts:step:run-v2"), ["Superseded drafts · 2", "Click to show them"]);
  await shoot(linePage, "14-line-open");
  await clickCanvasNode(linePage, lineSurface, "drafts:step:run-v2", { inspect: false });
  await linePage.waitForFunction(() => window.__treeApi.getSceneElements().some((element) => element.id === "draft:run-v3-closed:card"));
  lineView = await scene(linePage);
  for (const id of ["draft:run-v3-closed", "draft:run-v3-glazed"]) {
    assert.ok(lineView.elements.find((element) => element.id === `${id}:card`).opacity < 100, `${id} is drawn quietly`);
  }
  checkTree(lineView, ["origin", ...lineSteps, "current"], {});
  await shoot(linePage, "15-drafts-open");
  let lineCard = await clickCanvasNode(linePage, lineSurface, "draft:run-v3-closed");
  assert.match(await lineCard.innerText(), /Superseded draft · kept for now/);
  assert.match(await lineCard.innerText(), /Superseded by\s*V3 - ribbed roof, open side triangles/);
  // #575: a draft nothing refers to goes to the project trash at the next open or Continue, restorable for 30 days.
  assert.match(await lineCard.innerText(), /moves it into the project trash, restorable for 30 days/);
  assert.equal(await lineCard.locator('[data-action="continue"]').isEnabled(), true, "a draft is kept and can be returned to");
  await lineBar.getByRole("button", { name: "Hide superseded drafts", exact: true }).click();
  await lineCard.waitFor({ state: "detached" });
  // Returning to V2 is the existing Continue (PUT /api/working-draft), and its Undo puts Current back.
  lineCard = await clickCanvasNode(linePage, lineSurface, "step:run-v2");
  assert.match(await lineCard.innerText(), /Step · on the current line/);
  assert.match(await lineCard.innerText(), /Grew from\s*V1 - timber frame and hemp walls/);
  assert.deepEqual(await lineCard.locator(".design-tree-inspector__actions").first().getByRole("button").allInnerTexts(), ["View", "Continue from here"]);
  assert.equal(await lineCard.getByRole("button", { name: "Endorse direction" }).count(), 0, "a step is no admitted option to review");
  const lineRevision = fixture.state.revision;
  await lineCard.getByRole("button", { name: "Continue from here", exact: true }).click();
  const lineToast = linePage.locator(".design-tree-toast");
  await lineToast.filter({ hasText: "Current is now “V2 - pitched roof, open gable ends”" }).waitFor();
  assert.deepEqual(writes.at(-1), { method: "PUT", name: "/api/working-draft",
    body: { projectId: PROJECT, runId: "run-v2", baseRevisionSha256: rev(lineRevision), branchId: null } });
  await trunkPoints(4);
  checkTree(await scene(linePage), ["origin", "step:run-site-massing", "step:run-v1", "current"], {});
  assert.equal((await scene(linePage)).elements.some((element) => element.id.startsWith("drafts:") || element.id.startsWith("draft:")), false,
    "built on V2, the drafts continue Current now: nothing is superseded");
  await shoot(linePage, "16-returned-to-v2");
  await lineToast.getByRole("button", { name: "Undo", exact: true }).click();
  await lineToast.filter({ hasText: "Undone" }).waitFor();
  assert.deepEqual(writes.at(-1).body, { projectId: PROJECT, runId: "run-v3-materials", baseRevisionSha256: rev(lineRevision + 1), branchId: null });
  await trunkPoints(6);
  // Folded again from the menu; in the list the fold is a row that Enter opens.
  await lineBar.getByRole("button", { name: "Fold earlier steps", exact: true }).click();
  await trunkPoints(3);
  await lineBar.getByRole("button", { name: "List", exact: true }).click();
  const foldRow = lineSurface.locator('[role="treeitem"][data-node="fold"]');
  await foldRow.focus();
  assert.match(await foldRow.innerText(), /4 earlier steps/);
  await linePage.keyboard.press("Enter");
  await lineSurface.locator('[role="treeitem"][data-node="step:run-v2"]').waitFor();
  assert.deepEqual(await lineSurface.getByRole("treeitem").evaluateAll((rows) => rows.map((row) => row.dataset.node)),
    ["origin", ...lineSteps.slice(0, 3), "drafts:step:run-v2", lineSteps[3], "current"]);
  await shoot(linePage, "17-line-list");
  // #575 slice 4: the runtime cleaned both drafts into the project trash. Their row says so quietly, Enter opens
  // the inspector that lists them, and Restore brings one back as a kept draft, through POST /api/trash/restore.
  fixture.clean(["run-v3-closed", "run-v3-glazed"]);
  await linePage.evaluate(() => window.dispatchEvent(new Event("focus")));
  const cleanedRow = lineSurface.locator('[role="treeitem"][data-node="drafts:step:run-v2"]');
  await cleanedRow.filter({ hasText: "Superseded drafts cleaned · 2" }).waitFor();
  assert.match(await cleanedRow.innerText(), /Restorable for 30 days/);
  await cleanedRow.focus();
  await linePage.keyboard.press("Enter");
  const cleanedCard = lineSurface.locator('.design-tree-inspector[data-kind="drafts"]');
  await cleanedCard.waitFor();
  assert.match(await cleanedCard.innerText(), /Superseded drafts cleaned · 2 · restorable for 30 days/);
  assert.match(await cleanedCard.innerText(), /Superseded by V3 - ribbed roof, open side triangles/);
  assert.deepEqual(await cleanedCard.locator(".design-tree-cleaned li").evaluateAll((rows) => rows.map((row) => row.dataset.run)),
    ["run-v3-glazed", "run-v3-closed"], "newest first");
  await shoot(linePage, "18-cleaned-drafts");
  await cleanedCard.locator('li[data-run="run-v3-closed"] [data-action="restore"]').click();
  await cleanedCard.locator('li[data-run="run-v3-closed"]').waitFor({ state: "detached" });
  assert.deepEqual(writes.at(-1), { method: "POST", name: "/api/trash/restore", body: { projectId: PROJECT, runId: "run-v3-closed" } });
  await cleanedRow.filter({ hasText: "Superseded drafts · 1" }).waitFor();
  assert.match(await cleanedRow.innerText(), /1 cleaned · restorable for 30 days/);
  assert.deepEqual(await cleanedCard.locator(".design-tree-cleaned li").evaluateAll((rows) => rows.map((row) => row.dataset.run)), ["run-v3-glazed"]);
  await shoot(linePage, "19-draft-restored");
  // On the canvas the card says the same, quietly, and a click on it opens the same inspector.
  await lineBar.getByRole("button", { name: "Canvas", exact: true }).click();
  await linePage.waitForFunction(() => window.__treeApi?.getSceneElements().some((element) => element.id === "drafts:step:run-v2:card"));
  // Beside the open inspector the canvas is narrower, and a card's words are clipped to it.
  const cleanedTexts = texts(await scene(linePage), "drafts:step:run-v2");
  assert.ok(cleanedTexts.length === 2 && cleanedTexts[0].startsWith("Superseded drafts") && cleanedTexts[1].startsWith("1 cleaned"), cleanedTexts.join(" / "));
  await shoot(linePage, "20-cleaned-canvas");
  await lineContext.close();

  // ------------------------------------------------------------------ Part D: changes that landed, and a return (#575 rules 3-5).
  // Three changes the person asked for in chat, each admitted alone by the Hub Agent on their words and continued. They
  // fold into the earlier steps like plain ones; Current is the latest step and says the words that asked for it, and
  // every step goes by its words. Returning to the roof keeps the two steps after it drawn faintly after Current, and a
  // later step is continued again from its own inspector.
  fixture = fixtureModule.createDesignTreeFixture(fixtureModule.landedChangesFacts());
  const landedContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  await landedContext.route((url) => ["http:", "https:"].includes(url.protocol) && url.origin !== origin, (route) => {
    external.push(route.request().url()); return route.abort("blockedbyclient");
  });
  const landedPage = await landedContext.newPage();
  landedPage.setDefaultTimeout(20_000);
  landedPage.on("pageerror", (error) => errors.push(`landed: ${error.message}`));
  landedPage.on("console", (message) => { if (message.type() === "error") errors.push(`landed: ${message.text()}`); });
  await landedPage.goto(`${origin}/tree-workspace?lang=zh-CN&theme=light&view=tree`, { waitUntil: "domcontentloaded", timeout: 240_000 });
  const landedSurface = landedPage.locator('[data-project-surface="tree"]');
  const landedBar = landedPage.locator(".project-bar");
  await landedSurface.locator(".design-tree__canvas canvas").first().waitFor({ timeout: 120_000 });
  const landedTrunk = (count) => landedPage.waitForFunction((wanted) => window.__treeApi?.getSceneElements()
    .find((element) => element.customData?.tree?.role === "trunk")?.points.length === wanted, count);
  await landedTrunk(3);
  let landedView = await scene(landedPage);
  checkTree(landedView, ["origin", "fold", "current"], {});
  assert.equal(landedView.elements.some((element) => element.id.startsWith("candidate:")), false, "no landed change is a card of its own");
  assert.deepEqual(texts(landedView, "fold"), ["之前 3 步", "点击展开"]);
  assert.match(texts(landedView, "current").join(" "), /「给 238 个构件定材质」/, "Current says the words that asked for it");
  assert.ok(!landedView.elements.some((element) => /run-|studio-projection|[0-9a-f]{16}/i.test(element.text ?? "")), "no ids on the canvas");
  await shoot(landedPage, "21-landed-folded");
  await clickCanvasNode(landedPage, landedSurface, "fold", { inspect: false });
  await landedTrunk(5);
  landedView = await scene(landedPage);
  checkTree(landedView, ["origin", "step:run-frame", "step:run-roof", "step:run-glazing", "current"], {});
  assert.deepEqual(["step:run-frame", "step:run-roof", "step:run-glazing"].map((id) => texts(landedView, id).join(" ")),
    ["Timber frame with hemp-lime walls", "把屋顶改成木肋拱", "南立面改成通高玻璃"], "each step goes by the words that asked for it");
  await shoot(landedPage, "22-landed-open");
  // Returning to the roof is the existing Continue; its toast names the step by its words.
  let landedCard = await clickCanvasNode(landedPage, landedSurface, "step:run-roof");
  assert.match(await landedCard.innerText(), /名称\s*Ribbed timber roof/, "the name it was also given is said beside its words");
  await landedCard.getByRole("button", { name: "从这里继续", exact: true }).click();
  const landedToast = landedPage.locator(".design-tree-toast");
  await landedToast.filter({ hasText: "当前已改为「把屋顶改成木肋拱」" }).waitFor();
  await landedTrunk(3);
  await landedPage.waitForFunction(() => window.__treeApi.getSceneElements().some((element) => element.id === "later:run-materials:card"));
  landedView = await scene(landedPage);
  checkTree(landedView, ["origin", "step:run-frame", "current"], {});
  const laterCards = ["later:run-glazing", "later:run-materials"].map((id) => landedView.elements.find((element) => element.id === `${id}:card`));
  const currentCard = landedView.elements.find((element) => element.id === "current:card");
  assert.ok(laterCards.every((card) => card.opacity < 100 && card.strokeStyle === "dashed"), "the steps it left are drawn faintly");
  assert.ok(laterCards[0].x > currentCard.x + currentCard.width && laterCards[1].x > laterCards[0].x + laterCards[0].width &&
    laterCards.every((card) => Math.abs(card.y + card.height / 2 - (currentCard.y + currentCard.height / 2)) < 1), "after Current, along its row");
  assert.deepEqual(["later:run-glazing", "later:run-materials"].map((id) => texts(landedView, id).join(" ")), ["南立面改成通高玻璃", "给 238 个构件定材质"]);
  assert.match(texts(landedView, "current").join(" "), /「把屋顶改成木肋拱」/);
  await shoot(landedPage, "23-returned-later-steps");
  // In the list the steps it left are a group of their own, after Current.
  await landedBar.getByRole("button", { name: "列表", exact: true }).click();
  await landedSurface.locator('[role="treeitem"][data-node="later:run-materials"]').waitFor();
  assert.deepEqual(await landedSurface.getByRole("treeitem").evaluateAll((rows) => rows.map((row) => [row.dataset.node, row.dataset.group])),
    [["origin", "trunk"], ["step:run-frame", "trunk"], ["current", "trunk"], ["later:run-glazing", "later"], ["later:run-materials", "later"]]);
  assert.match(await landedSurface.locator('[role="treeitem"][data-node="current"]').innerText(), /「把屋顶改成木肋拱」/);
  await shoot(landedPage, "24-returned-list");
  await landedBar.getByRole("button", { name: "画布", exact: true }).click();
  await landedPage.waitForFunction(() => window.__treeApi?.getSceneElements().some((element) => element.id === "later:run-materials:card"));
  // A later step is continued again from its own inspector, not only through Undo.
  landedCard = await clickCanvasNode(landedPage, landedSurface, "later:run-materials");
  assert.match(await landedCard.innerText(), /后续步骤 · 当前回到之前时保留/);
  assert.match(await landedCard.innerText(), /「从这里继续」会让当前再走到这里/);
  const landedRevision = fixture.state.revision;
  await landedCard.getByRole("button", { name: "从这里继续", exact: true }).click();
  await landedToast.filter({ hasText: "当前已改为「给 238 个构件定材质」" }).waitFor();
  assert.deepEqual(writes.at(-1), { method: "PUT", name: "/api/working-draft",
    body: { projectId: PROJECT, runId: "run-materials", baseRevisionSha256: rev(landedRevision), branchId: null } });
  await landedTrunk(5);
  landedView = await scene(landedPage);
  checkTree(landedView, ["origin", "step:run-frame", "step:run-roof", "step:run-glazing", "current"], {});
  assert.equal(landedView.elements.some((element) => element.id.startsWith("later:")), false, "back on the line's last step, nothing is left after it");
  await shoot(landedPage, "25-later-step-continued");
  await landedContext.close();

  assert.deepEqual(unexpected, [], "the tree reads only what it declares");
  assert.deepEqual(external, [], "no external request");
  assert.deepEqual(errors.filter((message) => !/Failed to load resource: the server responded with a status of 404/.test(message)), []);
  console.log(JSON.stringify({ passed: "chip → tree, trunk, twigs, planar, Current's line as one chain with its earlier steps folded and opened, landed changes folded like plain steps and named by their words, a return keeping the line it left after Current and a later step continued again, superseded drafts folded where the line moved on, cleaned drafts listed and one restored, return to a step via Continue and Undo, three zoom levels with close-card server thumbnails read on demand and arriving through the index, inspector, review-open warning, View read-only, Continue re-roots via PUT /api/working-draft, its toast's Undo puts the previous Current back through the same PUT, Accept on Current only via POST accept with a toast and no Undo, a toast stays while hovered and then fades, no toast on refusal, the chip's viewing state continues from here, a rejected Current cannot be accepted, keyboard list, return to previous surface, zh copy, Hub rail entry and deep link",
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
// #364: Modeling's opening, and a model opened again without its bytes or a second rhino3dm, run in this CI step.
if (!serveOnly && !process.exitCode) await import("./modelingOpen.browser.mjs");
