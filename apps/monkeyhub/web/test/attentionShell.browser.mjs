import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createProjectWorkspaceFixture } from "./projectWorkspaceFixture.mjs";

// The attention shell (?shell=attention) over the real built Hub: the projects column with what
// waits on the architect, the Attention, Board and Model tabs over the same open surfaces, the
// conversation drawer above the composer (lying over the Board and the model), the side panel's
// pages, the notices clear of the side panel, and the phone drawers. One pass serves the attention queue
// (1-4): its cards on the Attention page, their answers, the handled and set-aside pages, and a new card's
// notice kept above the model's floating toolbar, All as recommended and its undo, and a message sent with no
// conversation open, which goes to the project's attention conversation.
// ATTENTION_SHELL_SHOTS=<dir> saves desktop and phone, light and dark. With ATTENTION_SHELL_STYLE=board the
// shots are drawn in that interface style too, on a build that has it.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const shots = process.env.ATTENTION_SHELL_SHOTS ? path.resolve(process.env.ATTENTION_SHELL_SHOTS) : null;
const now = new Date().toISOString();
const projects = [
  { projectId: "A", projectDir: "D:\\fixture\\A", name: "Thi Hemp 墙身节点", chatCount: 2, version: 3, stage: "S2" },
  { projectId: "B", projectDir: "D:\\fixture\\B", name: "Housing 13 户", chatCount: 1, version: 0, stage: null },
];
const message = (id, role, content) => ({ id, role, content, status: "complete", createdAt: now });
const sessions = [
  { id: "a1", projectId: "A", projectDir: "D:\\fixture\\A", title: "楼板再薄一点", provider: "codex", status: "idle", createdAt: now, updatedAt: now,
    messages: [message("a1-u", "user", "楼板再薄一点"), message("a1-a", "assistant", "我把 V16 的楼板改成只留一层胶合板。")] },
  { id: "a2", projectId: "A", projectDir: "D:\\fixture\\A", title: "斜撑留不留", provider: "codex", status: "idle", createdAt: now, updatedAt: "2026-10-01T00:00:00Z",
    messages: [message("a2-u", "user", "斜撑留不留"), { id: "a2-p", role: "tool", content: "", status: "pending", createdAt: now,
      permission: { id: "perm-1", title: "允许运行 model-split v2 吗？", options: [{ optionId: "once", name: "允许一次" }] } }] },
  { id: "b1", projectId: "B", projectDir: "D:\\fixture\\B", title: "东南角入口", provider: "codex", status: "running", createdAt: now, updatedAt: now, messages: [] },
];
const summary = (session) => ({ ...session, messages: undefined, attention: session.messages.some((item) => item.permission) ? "permission" : null });
const runtimes = new Map();
const workspace = await createProjectWorkspaceFixture(runtimes, sessions);
const apps = new Map();
let origin = "";
const appsFor = (projectDir) => {
  if (!apps.has(projectDir)) apps.set(projectDir, ["monkeyarch", "monkeyboard", "monkeyrender", "monkeyfab", "monkeymonitor"].map((appId) => ({
    appId, title: appId, serviceId: appId === "monkeyfab" ? "hub" : appId === "monkeymonitor" ? "monitor" : "studio", state: "stopped",
    available: true, processId: null, url: null, apiUrl: null, error: null })));
  return apps.get(projectDir);
};
const start = (projectDir) => {
  const runtime = runtimes.get(projectDir);
  for (const app of appsFor(projectDir).filter((row) => row.serviceId === "studio")) {
    Object.assign(app, { state: "running", processId: 2000, url: `${origin}/?view=${app.appId === "monkeyboard" ? "board" : "arch"}&runtimeId=${runtime.runtimeId}`,
      apiUrl: `${origin}/api/runtime/projects/${runtime.runtimeId}/studio/` });
  }
};
const snapshot = () => ({ serverId: "fixture-hub", sequence: 1, workers: [], projects: [...runtimes.values()].map((runtime) => {
  const app = appsFor(runtime.projectDir).find((row) => row.appId === "monkeyarch");
  return { ...runtime, workers: app.processId ? [{ workerId: runtime.runtimeId, serviceId: "studio", projectId: runtime.projectId, projectDir: runtime.projectDir,
    instanceId: "instance-2000", processId: 2000, desiredState: "running", healthy: true, state: "ready", url: app.apiUrl, error: null }] : [],
    sessions: sessions.filter((row) => row.projectId === runtime.projectId).map(summary) };
}) });
// The open event streams, so the fixture can say what changed, as the Hub does.
const streams = new Set();
let sequence = 1;
const announce = (kind) => {
  for (const stream of streams) stream.write(`event: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: ++sequence, kind, runtimeId: null })}\n\n`);
};
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    response.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: 1, kind: "snapshot", snapshot: snapshot() })}\n\n`);
    streams.add(response);
    request.on("close", () => streams.delete(response));
    return;
  }
  const file = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  if (!file.startsWith(root)) { response.writeHead(404); response.end(); return; }
  try {
    response.setHeader("Content-Type", file.endsWith(".js") ? "text/javascript" : file.endsWith(".css") ? "text/css" : file.endsWith(".wasm") ? "application/wasm"
      : file.endsWith(".woff2") ? "font/woff2" : "text/html");
    response.end(await readFile(file));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
origin = `http://127.0.0.1:${server.address().port}`;
const unexpected = [];
// A 4x3 grey picture, standing in for a drawing's first page.
const DRAWING_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAIAAAA7ljmRAAAAE0lEQVR4nGM8ceIEAwwwwVnoHABXVAJetxGYDgAAAABJRU5ErkJggg==", "base64");
// The theme each pass loads with, as the saved preferences say it.
let preferredTheme = "light";
// The projects the Hub lists; one pass empties it to see the Board and the model refuse.
let listed = projects;
// The attention queue (1-4): served in one pass, as the Hub will after 0-2; elsewhere a 404, as main's Hub answers.
let attentionServed = false;
const stamp = () => new Date().toISOString();
const queueCard = (id, projectId, overrides) => ({ id: `att_${id}`, projectId, projectDir: `D:\\fixture\\${projectId}`, state: "open",
  source: {}, media: [], evidence: [], options: [], opens: [], createdAt: now, updatedAt: now, ...overrides });
const answer = (id, label, labelZh, consequenceZh, extra = {}) => ({ id, label, labelZh, consequence: consequenceZh, consequenceZh,
  action: { commandId: "candidate.review.archive", args: {} }, ...extra });
const seedCards = () => [
  queueCard("decide", "A", { channel: "decide", kind: "ab", title: "Keep V16 or V17", titleZh: "V16 和 V17 留哪个",
    summary: "The two differ only in floor thickness.", summaryZh: "两版只差楼板厚度。",
    evidence: [{ key: "floor", label: "Floor", labelZh: "楼板", text: "V17 is 40 mm thinner", textZh: "V17 薄 40 mm", status: "ok" }],
    options: [answer("v16", "Keep V16", "留 V16", "V17 归档"), answer("v17", "Keep V17", "留 V17", "V16 归档", { recommended: true, undoable: true })],
    recommendation: { optionId: "v17", reason: "Thinner, and every check passes", reasonZh: "更薄，检查都过了" },
    opens: [{ commandId: "chat.open_conversation", args: { sessionId: "a1" } }, { commandId: "candidate.compare.decide", args: { runIds: ["v16", "v17"] } }] }),
  queueCard("guess", "B", { channel: "guess", kind: "ask", title: "Which side for the entrance", titleZh: "入口放在哪边",
    options: [answer("south", "South", "南边", "按南边入口改"), answer("east", "East", "东边", "按东边入口改")] }),
  queueCard("notice", "A", { channel: "notice", kind: "info", title: "The section is ready", titleZh: "剖面图出好了" }),
  queueCard("ask", "A", { channel: "guess", kind: "ask", title: "How wide should the stair be", titleZh: "楼梯宽度你想怎么定",
    options: [answer("say", "Say it", "我来说", "把你写的话交给对话", { action: { commandId: "chat.composer.send", args: {} }, undoable: true })] }),
];
let cards = [];
const bulks = new Map();
// Cards whose undo finds the project moved on: the Hub leaves them handled and asks instead.
const movedOnUndo = new Set(["att_v11b"]);
const attentionActions = [], attentionNotes = [], chatPosts = [];
const route = async (handler) => {
  const request = handler.request(), url = new URL(request.url()), method = request.method();
  if (url.pathname === "/api/runtime/events") return handler.continue();
  // A drawing's first page, as the project service serves it beside the snapshot's recent drawings.
  if (url.pathname.endsWith("/api/drawings/d-section/thumbnail.png")) return handler.fulfill({ status: 200, contentType: "image/png", body: DRAWING_PNG });
  // The page the tile opens on the Board has no comments yet.
  if (url.pathname.endsWith("/api/document-comments")) return handler.fulfill({ json: { comments: [], kind: "document",
    runId: url.searchParams.get("runId") ?? "run-v17", revisionRef: url.searchParams.get("revisionRef") } });
  // The comparison a decision card opens reads the project's candidates; this project has none retained (2-5).
  if (/\/studio\/api\/runtime$/.test(url.pathname)) return handler.fulfill({ json: { projectId: "A", candidates: [] } });
  if (await workspace.handle(handler, url)) return;
  const json = (body, status = 200) => handler.fulfill({ json: body, status });
  const data = () => request.postDataJSON();
  // 一句话直接办: the Hub's meaning search, answered for the sentences this test types; the rest mean nothing listed.
  if (url.pathname === "/api/commands/meaning" && method === "POST") {
    const meant = { "后台现在在忙什么呀": "view.background.toggle", "切到另一个项目": "project.open", "切到 Housing 13 户": "project.open" }[data().text] ?? null;
    // Ctrl K lists what a phrase means below what its words find.
    if (data().text === "那个东西挪远点") return json({ state: "ready", hits: [{ id: "draft.tools.move", score: 0.71, text: "把物体挪个位置" }], route: null });
    return json({ state: "ready", hits: meant ? [{ id: meant, score: 0.8, text: meant }] : [], route: meant });
  }
  if (url.pathname === "/api/settings/user") return json({ language: "zh-CN", theme: preferredTheme, fontScale: 1, sidebarPinned: true });
  if (url.pathname === "/api/settings/apps") return json({ projectDir: "D:\\fixture\\A", referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18790 });
  if (url.pathname === "/api/credentials") return json([]);
  if (url.pathname === "/api/updates/status") return json({ currentVersion: "fixture", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null,
    canApply: false, message: null, error: null, channel: "unsigned-prerelease", autoUpdate: false, nextLaunch: false,
    check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } });
  if (url.pathname === "/api/apps") return json(url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : appsFor("hub"));
  const app = url.pathname.match(/^\/api\/apps\/([^/]+)\/start$/);
  if (app && method === "POST") { start(url.searchParams.get("projectDir")); return json(appsFor(url.searchParams.get("projectDir")).find((row) => row.appId === app[1])); }
  if (url.pathname === "/api/runtime") return json(snapshot());
  if (url.pathname === "/api/runtime/projects/open" && method === "POST") {
    const body = data();
    if (!runtimes.has(body.projectDir)) runtimes.set(body.projectDir, { runtimeId: randomUUID(), ...body, state: "open", operations: [], retained: null,
      projection: "ready", clients: 1, error: null });
    return json(snapshot().projects.find((row) => row.projectDir === body.projectDir));
  }
  if (url.pathname === "/api/project/modeling" && method === "POST") {
    const projectDir = url.searchParams.get("projectDir");
    start(projectDir);
    return json({ projectId: projects.find((row) => row.projectDir === projectDir).projectId, initialized: true });
  }
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: listed.map((row) => row.projectId) });
  if (url.pathname === "/api/chat/projects") return json(listed);
  if (url.pathname === "/api/chat/providers") return json([{ id: "codex", label: "Codex CLI", available: true, detail: "Fixture", installed: true, signedIn: true,
    models: ["fixture-model"], modelCatalog: "ready", modelDetail: "Fixture" }]);
  if (url.pathname === "/api/chat/sessions" && method === "GET") return json(listed !== projects ? []
    : sessions.filter((row) => Boolean(row.archived) === (url.searchParams.get("archived") === "true")).map(summary));
  if (url.pathname === "/api/chat/sessions" && method === "POST") {
    const project = projects.find((row) => row.projectDir === data().projectDir);
    const session = { id: `new-${sessions.length}`, projectId: project.projectId, projectDir: project.projectDir, title: "新对话", provider: "codex",
      status: "idle", createdAt: stamp(), updatedAt: stamp(), messages: [] };
    sessions.unshift(session);
    return json({ ...session, attention: summary(session).attention });
  }
  const archive = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/archive$/);
  if (archive && method === "PUT") {
    const session = sessions.find((row) => row.id === archive[1]);
    Object.assign(session, { archived: data().archived, updatedAt: new Date().toISOString() });
    return json({ ...session, attention: summary(session).attention });
  }
  const post = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/messages$/);
  if (post && method === "POST") {
    const session = sessions.find((row) => row.id === post[1]);
    chatPosts.push([session.id, data().content]);
    session.messages.push(message(`${session.id}-${session.messages.length}`, "user", data().content));
    session.updatedAt = stamp();
    return json({ ...session, attention: summary(session).attention });
  }
  const chat = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)$/);
  if (chat && method === "GET") { const session = sessions.find((row) => row.id === chat[1]); return session ? json({ ...session, attention: summary(session).attention }) : json({ code: "CHAT_NOT_FOUND", detail: "No such chat" }, 404); }
  if (url.pathname.startsWith("/api/attention")) {
    if (!attentionServed) return json({ code: "NOT_FOUND", detail: "This Hub does not serve the attention queue yet." }, 404);
    if (url.pathname === "/api/attention" && method === "GET") {
      const states = url.searchParams.getAll("state");
      const wanted = states.length ? states : ["open", "review", "later", "working"];
      return json(cards.filter((card) => wanted.includes(card.state)));
    }
    if (url.pathname === "/api/attention/summary") {
      const count = (projectId, states) => cards.filter((card) => card.projectId === projectId && states.includes(card.state)).length;
      const rows = projects.map((project) => ({ projectId: project.projectId, projectDir: project.projectDir,
        open: count(project.projectId, ["open", "review"]), later: count(project.projectId, ["later"]), working: count(project.projectId, ["working"]) }));
      const total = (key) => rows.reduce((sum, row) => sum + row[key], 0);
      return json({ projects: rows, hub: null, open: total("open"), later: total("later"), working: total("working") });
    }
    // The project's attention conversation, opened on first need.
    if (url.pathname === "/api/attention/conversation" && method === "POST") {
      const project = projects.find((row) => row.projectDir === data().projectDir);
      const id = `att-conv-${project.projectId}`;
      if (!sessions.some((row) => row.id === id)) sessions.unshift({ id, projectId: project.projectId, projectDir: project.projectDir, title: "关注",
        provider: "codex", status: "idle", createdAt: stamp(), updatedAt: stamp(), messages: [] });
      return json({ sessionId: id });
    }
    if (url.pathname === "/api/attention/bulk" && method === "POST") {
      const items = data().ids.map((id) => cards.find((card) => card.id === id));
      for (const item of items) Object.assign(item, { state: "done", updatedAt: stamp(),
        resolution: { optionId: item.recommendation.optionId, by: "bulk", at: stamp(), undo: { action: item.options[0].action } } });
      attentionActions.push(["bulk", items.map((item) => item.id).join(","), null]);
      bulks.set("bulk-1", items.map((item) => item.id));
      announce("attention/changed");
      return json({ bulkId: "bulk-1", items, skipped: [] });
    }
    const bulkUndo = url.pathname.match(/^\/api\/attention\/bulk\/([^/]+)\/undo$/);
    if (bulkUndo && method === "POST") {
      const ids = bulks.get(bulkUndo[1]);
      const items = ids.filter((id) => !movedOnUndo.has(id)).map((id) => cards.find((card) => card.id === id));
      for (const item of items) Object.assign(item, { state: "open", resolution: null, updatedAt: stamp() });
      attentionActions.push(["bulk_undo", bulkUndo[1], null]);
      announce("attention/changed");
      return json({ bulkId: bulkUndo[1], items,
        skipped: ids.filter((id) => movedOnUndo.has(id)).map((id) => ({ id, code: "ATTENTION_UNDO_MOVED", detail: "The project moved on" })) });
    }
    const act = url.pathname.match(/^\/api\/attention\/([^/]+)\/(resolve|later|dismiss|reopen|undo)$/);
    if (act && method === "POST") {
      const item = cards.find((card) => card.id === act[1]);
      const option = act[2] === "resolve" ? item.options.find((row) => row.id === data().optionId) : null;
      attentionActions.push([act[2], item.id, option?.id ?? null]);
      if (act[2] === "resolve" && data().note !== undefined) attentionNotes.push(data().note);
      // Taking the stair answer back finds the project moved on: the Hub asks instead, and the card stays handled.
      const followUp = act[2] === "undo" && item.id === "att_ask" ? queueCard("ask_next", "A", { channel: "decide", kind: "choice",
        title: "The stair changed since: go back anyway", titleZh: "楼梯已经改过了，还要回到原来吗", options: [answer("yes", "Go back", "回去", "撤回楼梯的改动")],
        createdAt: stamp(), updatedAt: stamp() }) : null;
      if (followUp) cards.push(followUp);
      else Object.assign(item, { updatedAt: stamp() }, act[2] === "resolve"
        ? { state: "done", resolution: { optionId: option.id, by: "person", at: stamp(), undo: option.undoable ? { action: option.action } : null } }
        : act[2] === "later" ? { state: "later" } : act[2] === "dismiss" ? { state: "dismissed" } : { state: "open", resolution: null });
      // As the Hub does: the change is published when it is stored, before the answer goes back.
      announce("attention/changed");
      return json(act[2] === "resolve" || act[2] === "undo" ? { item, followUp } : item);
    }
  }
  unexpected.push(`${method} ${url.pathname}`);
  return json({ code: "FIXTURE_NOT_SERVED", detail: `${method} ${url.pathname}` }, 404);
};

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
if (shots) await mkdir(shots, { recursive: true });
const errors = [];
const shoot = async (page, name) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`) }); };
const box = (locator) => locator.boundingBox();
const style = (page, value, family) => page.evaluate(([value, family]) => {
  document.documentElement.dataset.uiStyle = value; document.documentElement.dataset.uiFamily = family; }, [value, family]);
const look = process.env.ATTENTION_SHELL_STYLE ?? null;
/** Open the shell once the saved preferences have arrived and been applied, then put on the preview style. */
const open = async (page) => {
  const settled = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/settings/user");
  await page.goto(`${origin}/?shell=attention`);
  await settled;
  await page.waitForFunction((theme) => document.documentElement.dataset.theme === theme && document.querySelector(".attention-shell"), preferredTheme);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  if (look) await style(page, look, look);
};
try {
  // Desktop.
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage(); page.setDefaultTimeout(15_000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route((url) => url.pathname.startsWith("/api/"), route);
  await open(page);
  assert.equal(await page.locator(".chat-shell").count(), 0, "the workbench layout is not drawn");
  // The projects column: Attention with what waits on the architect, each project and its latest conversations.
  const attentionRow = page.locator(".attention-shell__row--attention");
  await attentionRow.locator(".attention-shell__count").filter({ hasText: "1" }).waitFor();
  await page.locator(".attention-shell__projects .attention-shell__row").filter({ hasText: "Thi Hemp" }).locator(".attention-shell__count").filter({ hasText: "1" }).waitFor();
  assert.deepEqual(await page.locator(".attention-shell__recent").first().locator(".chat-thread__title").allTextContents(), ["楼板再薄一点", "斜撑留不留"],
    "a project's conversations, newest first");
  assert.deepEqual(await page.locator(".attention-shell__projects .attention-shell__version").allTextContents(), ["当前 V3 · S2"],
    "under its name each project says where it stands; a project nothing was published in says nothing");
  assert.equal(await page.locator(".menu-tab[aria-pressed=true]").textContent(), "关注");
  await page.getByText("现在没有要你处理的").or(page.getByText("1 项需要关注")).first().waitFor();
  assert.ok(await page.locator(".attention-shell__drawer").isHidden(), "the conversation waits in its drawer");
  assert.ok(await page.locator(".attention-shell .chat-composer").isVisible(), "the composer is always there");
  await shoot(page, "attention-desktop-light");

  // A conversation opens its drawer above the composer.
  await page.locator(".attention-shell__recent .chat-thread").filter({ hasText: "楼板再薄一点" }).click();
  await page.locator(".attention-shell__drawer").getByText("我把 V16 的楼板改成只留一层胶合板。").waitFor();

  // A project's row starts a new conversation there; a conversation's row archives it, and the archived list restores it.
  await page.getByRole("button", { name: "在Housing 13 户里开新对话" }).click();
  await page.waitForFunction(() => document.querySelector(".attention-shell__projects .attention-shell__row[aria-current]")?.textContent.includes("Housing"));
  assert.equal(await page.locator(".attention-shell__recent .chat-thread[aria-current]").count(), 0, "a new conversation has no row yet");
  assert.ok(await page.getByRole("button", { name: "归档: 东南角入口" }).isDisabled(), "a running conversation cannot be archived");
  await page.getByRole("button", { name: "归档: 楼板再薄一点" }).click();
  await page.locator(".attention-shell__recent .chat-thread").filter({ hasText: "楼板再薄一点" }).waitFor({ state: "detached" });
  await page.locator(".attention-shell__caption").getByRole("button", { name: "已归档的对话" }).click();
  await page.locator(".attention-shell__caption").getByText("已归档的对话").waitFor();
  await page.getByRole("button", { name: "恢复: 楼板再薄一点" }).click();
  await page.locator(".attention-shell__recent .chat-thread[aria-current]").filter({ hasText: "楼板再薄一点" }).waitFor();
  await page.locator(".attention-shell__caption").getByText("项目").waitFor();
  assert.deepEqual(await page.locator(".attention-shell__recent").first().locator(".chat-thread__title").allTextContents(), ["楼板再薄一点", "斜撑留不留"]);

  // Board and Model are the same open surfaces as the workbench's; Alt+1 returns to Attention.
  await page.locator(".attention-shell__tabs .menu-tab").filter({ hasText: "画板" }).click();
  await page.locator(".attention-shell__surfaces .chat-project-workspace:not([hidden])").waitFor();
  await page.locator(".attention-shell__surfaces .monkeyboard-canvas canvas").last().waitFor();
  await page.waitForFunction(() => !document.querySelector(".attention-shell__surfaces .boot"));
  assert.equal(await page.locator(".menu-tab[aria-pressed=true]").textContent(), "画板");
  assert.ok(await page.locator(".attention-shell__queue").isHidden());
  // Embedded, the Board's bar keeps its front commands; the rest are in the advanced tools and Ctrl K (G1).
  const bar = page.locator(".attention-shell__surfaces .project-bar").first();
  await bar.locator('[data-command="document.upload"]').waitFor();
  for (const hidden of ["board.add_document_page.toggle_list", "board.manage.import_component_info"]) {
    assert.equal(await bar.locator(`[data-command="${hidden}"]`).count(), 0, `the Board bar leaves out ${hidden}`);
  }
  assert.equal(await bar.locator(".monkeyboard-title").count(), 0, "and the board's title field");
  await bar.locator('[data-command-arg="board_more"]').click();
  assert.deepEqual(await bar.locator(".monkeyboard-actions-panel [data-command]").evaluateAll((items) => items.map((item) => item.getAttribute("data-command"))),
    ["view.canvas.fit", "sketch.to_model.new_frame"], "More keeps Fit and New sketch frame");
  await bar.locator('[data-command-arg="board_more"]').click();
  await shoot(page, "attention-board-desktop-light");
  await page.locator("body").click({ position: { x: 700, y: 870 } }).catch(() => undefined);
  await page.locator(".attention-shell__top").click({ position: { x: 1200, y: 20 } });
  await page.keyboard.press("Alt+1");
  await page.locator(".attention-shell__queue:not([hidden])").waitFor();
  await page.keyboard.press("Alt+3");
  await page.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "模型");

  // Over the model the open drawer lies on the surface, and the composer keeps to one line until it is used.
  const body = page.locator(".attention-shell__body"), composer = page.locator(".attention-shell .chat-composer");
  assert.ok(await page.locator(".attention-shell__drawer").isVisible(), "the drawer stays open across tabs");
  const withDrawer = await box(body);
  const drawer = await box(page.locator(".attention-shell__drawer"));
  assert.ok(drawer.y < withDrawer.y + withDrawer.height, "the drawer lies over the surface");
  assert.ok((await box(composer)).height < 60, `the idle composer is one line (${(await box(composer)).height}px)`);
  await page.waitForFunction(() => [...document.querySelectorAll(".attention-shell__surfaces canvas")].some((canvas) => canvas.offsetParent && canvas.width > 0));
  await page.waitForFunction(() => !document.querySelector(".attention-shell__surfaces .boot"));
  // The model's bar: More keeps the views, Fit selected and Screenshot; Versions and Export are in the advanced tools (G1).
  const modelBar = page.locator(".attention-shell__surfaces .chat-project-workspace:not([hidden]) .project-bar");
  for (const hidden of ['[data-command="design.go_to_step.versions"]', '[data-command-arg="modeling_export"]']) {
    assert.equal(await modelBar.locator(hidden).count(), 0, `the model bar leaves out ${hidden}`);
  }
  await modelBar.locator('[data-command-arg="modeling_more"]').click();
  const more = modelBar.locator("#stage-more-menu");
  assert.deepEqual(await more.locator("[data-command]").evaluateAll((items) => items.map((item) => item.getAttribute("data-command"))),
    ["view.model_camera.top", "view.model_camera.front", "view.model_camera.right", "view.model_camera.iso", "view.model_camera.perspective",
      "view.model_camera.fit_selected", "model.capture"], "More keeps the views, Fit selected and Screenshot");
  assert.equal(await more.locator("[data-display-style]").count(), 0, "a field that is not a command is left out too");
  await modelBar.locator('[data-command-arg="modeling_more"]').click();
  await shoot(page, "attention-model-drawer-desktop-light");
  await page.getByRole("button", { name: "收起对话" }).click();
  assert.equal((await box(body)).height, withDrawer.height, "opening the drawer does not push the surface up");
  await page.locator(".attention-shell #chat-input").focus();
  await page.waitForFunction(() => document.querySelector(".attention-shell .chat-composer").getBoundingClientRect().height > 90);
  await page.locator(".attention-shell #chat-input").blur();
  await page.waitForFunction(() => document.querySelector(".attention-shell .chat-composer").getBoundingClientRect().height < 60);

  // The side panel: background, and the advanced tools that run the command tree.
  await page.locator(".attention-shell__rail-item").filter({ hasText: "高级工具" }).click();
  await page.locator(".advanced-tools__group h3").first().waitFor();
  assert.ok((await page.locator(".advanced-tools__group h3").allTextContents()).includes("外壳：文件、视图、对话"));
  await shoot(page, "attention-tools-desktop-light");
  await page.locator(".advanced-tools__command").filter({ hasText: /^设置/ }).first().click();
  await page.locator("dialog.chat-dialog--settings[open]").waitFor();
  await page.keyboard.press("Escape");
  await page.locator(".attention-shell__rail-item").filter({ hasText: "后台" }).click();
  await page.locator(".attention-shell__panel .chat-tasks").waitFor();
  const sideTabs = await page.locator(".attention-shell__side-tabs .menu-tab").evaluateAll((tabs) => tabs.map((tab) => tab.scrollWidth <= tab.clientWidth + 1
    && tab.getBoundingClientRect().right <= tab.parentElement.getBoundingClientRect().right + 1));
  assert.deepEqual(sideTabs, [true, true, true, true, true], "every side page's name shows in full");

  // A notice in this shell keeps clear of the side panel and its rail, and above the composer.
  sessions.find((row) => row.id === "b1").messages.push({ id: "b1-p", role: "tool", content: "", status: "pending", createdAt: now,
    permission: { id: "perm-2", title: "允许改东南角入口的门宽吗？", options: [{ optionId: "once", name: "允许一次" }] } });
  await page.locator('.attention-toast[data-kind="permission"]').waitFor({ timeout: 12_000 });
  const clear = async (what) => {
    const stack = await box(page.locator(".attention-toasts")), side = await box(page.locator(".attention-shell__side"));
    const bottom = await box(page.locator(".attention-shell__bottom"));
    assert.ok(stack.x + stack.width <= side.x, `${what}: the notice ends left of the side (${stack.x + stack.width} > ${side.x})`);
    assert.ok(stack.y + stack.height <= bottom.y + 1, `${what}: the notice sits above the status line and the composer`);
  };
  await clear("panel open");
  await shoot(page, "attention-toast-desktop-light");
  await page.getByRole("button", { name: "收起侧栏" }).first().click();
  await page.locator(".attention-shell__panel").waitFor({ state: "detached" });
  await page.waitForFunction(() => {
    const stack = document.querySelector(".attention-toasts")?.getBoundingClientRect(), rail = document.querySelector(".attention-shell__side")?.getBoundingClientRect();
    return stack && rail && stack.right <= rail.left;
  });
  await clear("rail only");
  await page.locator(".attention-shell__rail-item").filter({ hasText: "后台" }).click();
  // Ctrl+K from the top row's search; it opens a side page by its command, as the rail does.
  assert.deepEqual(await page.locator(".attention-shell__rail-item").evaluateAll((items) => items.map((item) => item.getAttribute("data-command"))),
    ["view.background.toggle", "view.design_history.toggle", "view.handled.toggle", "view.ignored.toggle", "view.advanced_tools.toggle"]);
  await page.locator(".attention-shell__search").click();
  await page.locator("dialog.command-palette[open]").waitFor();
  await page.locator("dialog.command-palette[open] input").fill("查看已忽略的卡");
  await page.keyboard.press("Enter");
  await page.locator(".attention-shell__side-tabs .menu-tab[aria-selected=true]").filter({ hasText: "已忽略" }).waitFor();
  await page.getByRole("button", { name: "收起侧栏" }).first().click();

  // 08: a sentence that names one command is offered as that command before it goes to the model.
  const said = page.locator(".attention-shell #chat-input"), posted = chatPosts.length;
  await said.fill("打开设置");
  await said.press("Enter");
  const offer = page.locator(".chat-route");
  await offer.getByText("这句话可以直接做：「设置」").waitFor();
  await offer.getByRole("button", { name: "直接做" }).click();
  await page.locator("dialog.chat-dialog--settings[open]").waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await said.inputValue(), "", "done, the words are gone");
  assert.equal(chatPosts.length, posted, "and nothing went to the model");
  // Sent as written, it goes to the conversation.
  await said.fill("打开设置");
  await said.press("Enter");
  await offer.getByRole("button", { name: "还是发给 AI" }).click();
  await offer.waitFor({ state: "detached" });
  for (let wait = 0; chatPosts.length === posted && wait < 100; wait += 1) await page.waitForTimeout(50);
  assert.equal(chatPosts.at(-1)?.[1], "打开设置", "sent as written");
  // A sentence that is a request, not a command, goes to the conversation without an offer.
  await said.fill("这个方案怎么样");
  await said.press("Enter");
  for (let wait = 0; chatPosts.at(-1)?.[1] !== "这个方案怎么样" && wait < 100; wait += 1) await page.waitForTimeout(50);
  assert.equal(chatPosts.at(-1)?.[1], "这个方案怎么样");
  assert.equal(await offer.count(), 0);
  // 一句话直接办: a sentence no phrasing says is found by its meaning, and one click does it.
  const before = chatPosts.length;
  await said.fill("后台现在在忙什么呀");
  await said.press("Enter");
  await offer.getByText("这句话可以直接做：「展开任务列表」").waitFor();
  await offer.getByRole("button", { name: "直接做" }).click();
  await page.locator(".attention-shell__side-tabs .menu-tab[aria-selected=true]").filter({ hasText: "后台" }).waitFor();
  assert.equal(await said.inputValue(), "");
  await page.getByRole("button", { name: "收起侧栏" }).first().click();
  // A sentence that names the project needs no question; one that leaves it out is asked that one thing, as buttons.
  await said.fill("切到 Housing 13 户");
  await said.press("Enter");
  await offer.getByRole("button", { name: "直接做" }).waitFor();
  assert.equal(await offer.getByText("哪个项目？").count(), 0);
  await offer.getByRole("button", { name: "还是发给 AI" }).click();
  for (let wait = 0; chatPosts.length === before && wait < 100; wait += 1) await page.waitForTimeout(50);
  assert.deepEqual(chatPosts.slice(before).map(([, content]) => content), ["切到 Housing 13 户"], "only what was sent as written reached the model");
  await said.fill("切到另一个项目");
  await said.press("Enter");
  await offer.getByText("哪个项目？").waitFor();
  assert.equal(await offer.getByRole("button", { name: "直接做" }).count(), 0, "the choice is the click that does it");
  await offer.getByRole("button", { name: "Housing 13 户" }).click();
  await page.waitForFunction(() => document.querySelector(".attention-shell__projects .attention-shell__row[aria-current]")?.textContent.includes("Housing"));
  await page.locator(".attention-shell__search").click();
  await page.locator("dialog.command-palette[open] input").fill("那个东西挪远点");
  const byMeaning = page.locator("dialog.command-palette[open] .command-palette__heading").filter({ hasText: "按意思" });
  await byMeaning.waitFor();
  assert.equal(await page.locator("dialog.command-palette[open] [role=option]").first().locator(".command-palette__label").textContent(), "移动",
    "what the phrase means comes first when its words find nothing");
  await page.keyboard.press("Escape");
  // The later passes count what reached the conversations from here on.
  const sent = chatPosts.splice(posted);
  for (const [id] of sent) sessions.find((row) => row.id === id)?.messages.pop();

  await context.close();

  // 2-3c: the Hub opens in the new shell; 回旧版 goes back to the workbench at once and for next time, and the
  // workbench's 回新版 comes back.
  const chosen = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const hub = await chosen.newPage(); hub.setDefaultTimeout(15_000);
  hub.on("pageerror", (error) => errors.push(error.message));
  await hub.route((url) => url.pathname.startsWith("/api/"), route);
  await hub.goto(`${origin}/`);
  await hub.locator(".attention-shell").waitFor();
  await hub.locator(".attention-shell__search").click();
  await hub.locator("dialog.command-palette[open] input").fill("回旧版");
  await hub.keyboard.press("Enter");
  await hub.locator(".chat-shell").waitFor();
  assert.equal(await hub.evaluate(() => localStorage.getItem("monkeyhub.shell")), "workbench");
  await hub.reload();
  await hub.locator(".chat-shell").waitFor();
  await shoot(hub, "workbench-back-to-new-desktop-light");
  await hub.locator('.chat-menubar [data-command="view.shell.attention"]').click();
  await hub.locator(".attention-shell").waitFor();
  await hub.reload();
  await hub.locator(".attention-shell").waitFor();
  // A link that names a shell shows it without changing the choice.
  await hub.goto(`${origin}/?shell=workbench`);
  await hub.locator(".chat-shell").waitFor();
  assert.equal(await hub.evaluate(() => localStorage.getItem("monkeyhub.shell")), "attention");
  await chosen.close();
  preferredTheme = "dark";
  const dark = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const darkPage = await dark.newPage(); darkPage.setDefaultTimeout(15_000);
  darkPage.on("pageerror", (error) => errors.push(error.message));
  await darkPage.route((url) => url.pathname.startsWith("/api/"), route);
  await open(darkPage);
  await darkPage.locator(".attention-shell__row--attention .attention-shell__count").waitFor();
  await shoot(darkPage, "attention-desktop-dark");
  await dark.close();

  // With no project yet, the Board and the model tabs say why they cannot open.
  listed = [];
  const empty = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const emptyPage = await empty.newPage(); emptyPage.setDefaultTimeout(15_000);
  emptyPage.on("pageerror", (error) => errors.push(error.message));
  await emptyPage.route((url) => url.pathname.startsWith("/api/"), route);
  await open(emptyPage);
  await emptyPage.getByText("还没有项目。").waitFor();
  const blocked = emptyPage.locator(".attention-shell__tabs .menu-tab").filter({ hasText: "画板" });
  assert.equal(await blocked.getAttribute("aria-disabled"), "true");
  assert.ok(await blocked.getAttribute("title"), "a blocked tab says why");
  await blocked.click({ force: true });
  assert.equal(await emptyPage.locator(".menu-tab[aria-pressed=true]").textContent(), "关注", "a blocked tab does not open");
  await empty.close();
  listed = projects;

  // The attention queue, where the Hub serves it (1-4).
  attentionServed = true; cards = seedCards(); preferredTheme = "light";
  const served = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const queuePage = await served.newPage(); queuePage.setDefaultTimeout(15_000);
  queuePage.on("pageerror", (error) => errors.push(error.message));
  await queuePage.route((url) => url.pathname.startsWith("/api/"), route);
  await open(queuePage);
  const queueCards = queuePage.locator(".attention-shell__queue .attention-card");
  const queued = (text) => queueCards.filter({ hasText: text });
  await queueCards.nth(3).waitFor();
  sessions.find((row) => row.id === "b1").status = "idle";
  await queuePage.locator(".attention-toast").filter({ hasText: "「东南角入口」已完成" }).waitFor({ timeout: 12_000 });
  assert.equal(await queuePage.locator('.attention-toast[data-kind="permission"]').count(), 0, "a waiting permission is a card here, not a second notice");
  assert.deepEqual(await queueCards.evaluateAll((nodes) => nodes.map((node) => node.dataset.channel)), ["decide", "guess", "notice", "guess"]);
  // The counts are the queue's own.
  await queuePage.locator(".attention-shell__row--attention .attention-shell__count").filter({ hasText: "4" }).waitFor();
  const projectCount = (name) => queuePage.locator(".attention-shell__projects .attention-shell__row").filter({ hasText: name }).locator(".attention-shell__count");
  assert.equal(await projectCount("Thi Hemp").textContent(), "3");
  assert.equal(await projectCount("Housing").textContent(), "1");
  // A decision: what each answer does, the recommended one first with its reason, and what it opens.
  const decide = queued("V16 和 V17 留哪个");
  assert.match(await decide.locator(".attention-card__answer").first().innerText(), /^留 V17\s*建议\s*V16 归档$/);
  await decide.getByText("更薄，检查都过了").waitFor();
  await decide.getByText("V17 薄 40 mm").waitFor();
  assert.equal(await decide.getByRole("button", { name: "打开对话" }).getAttribute("aria-disabled"), null, "a registered command it opens is offered");
  assert.equal(await decide.getByRole("button", { name: "比较并决定" }).getAttribute("aria-disabled"), null, "its two results can be compared");
  await shoot(queuePage, "attention-queue-desktop-light");
  // Later keeps a card in the queue, put off, and takes it out of the count.
  await queued("入口放在哪边").getByRole("button", { name: "稍后" }).click();
  await queued("入口放在哪边").getByRole("button", { name: "找回" }).waitFor();
  await queuePage.waitForFunction(() => ![...document.querySelectorAll(".attention-shell__projects .attention-shell__row")]
    .find((row) => row.textContent.includes("Housing"))?.querySelector(".attention-shell__count"));
  // 比较并决定 (2-5): the card opens its two results side by side with itself under them. Answered there, as on
  // this page, it closes the comparison and the Attention page is back; the answer goes to the Hub, which carries it out.
  await decide.getByRole("button", { name: "比较并决定" }).click();
  const comparing = queuePage.locator('[data-project-surface="compare"]');
  const under = comparing.locator(".model-compare__decide .attention-card");
  await under.filter({ hasText: "V16 和 V17 留哪个" }).waitFor();
  assert.equal(await under.locator(".attention-card__opens").count(), 0, "the card under its comparison does not offer it again");
  await under.getByText("V17 薄 40 mm").waitFor();
  await under.locator(".attention-card__answer").first().click();
  await queuePage.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "关注");
  await comparing.waitFor({ state: "detached" });
  // The card moves to Handled, where it can be undone.
  await decide.waitFor({ state: "detached" });
  assert.deepEqual(attentionActions.slice(0, 2), [["later", "att_guess", null], ["resolve", "att_decide", "v17"]]);
  await queuePage.locator(".attention-shell__rail-item").filter({ hasText: "已处理" }).click();
  const handled = queuePage.locator(".attention-shell__panel .attention-card").filter({ hasText: "V16 和 V17 留哪个" });
  await handled.getByText("你选了：留 V17").waitFor();
  await handled.getByRole("button", { name: "撤销" }).click();
  await queued("V16 和 V17 留哪个").waitFor();
  assert.equal(await queuePage.locator(".attention-toast").filter({ hasText: "V16 和 V17 留哪个" }).count(), 0, "a card brought back here is no news");
  // Set aside leaves the queue for the set-aside page, which brings it back.
  await queued("剖面图出好了").getByRole("button", { name: "忽略" }).click();
  await queued("剖面图出好了").waitFor({ state: "detached" });
  await queuePage.locator(".attention-shell__rail-item").filter({ hasText: "已忽略" }).click();
  await queuePage.locator(".attention-shell__panel .attention-card").filter({ hasText: "剖面图出好了" }).getByRole("button", { name: "找回" }).click();
  await queued("剖面图出好了").waitFor();
  // An answer that sends the architect's own words waits for them.
  const ask = queued("楼梯宽度你想怎么定");
  assert.equal(await ask.locator('[data-option="say"]').isDisabled(), true, "nothing to send yet");
  await ask.locator("textarea").fill("1.2 米，和走廊一样宽");
  await ask.locator('[data-option="say"]').click();
  await ask.waitFor({ state: "detached" });
  assert.deepEqual(attentionNotes, ["1.2 米，和走廊一样宽"]);
  // Taking it back after the project moved on: the card the Hub raised instead is shown, with no notice.
  await queuePage.locator(".attention-shell__rail-item").filter({ hasText: "已处理" }).click();
  await queuePage.locator(".attention-shell__panel .attention-card").filter({ hasText: "楼梯宽度你想怎么定" }).getByRole("button", { name: "撤销" }).click();
  await queuePage.waitForFunction(() => document.activeElement?.id === "attention-card-att_ask_next");
  assert.equal(await queuePage.locator(".attention-toast").filter({ hasText: "楼梯已经改过了" }).count(), 0, "nor one the undo raised");
  await queuePage.getByRole("button", { name: "收起侧栏" }).first().click();
  // Two or more decisions whose recommendation can be taken back are answered at once, and taken back as one; V13's
  // archive could not be, so it is left to the person.
  const archiveCard = (id, titleZh, undoable, projectId = "A") => queueCard(id, projectId, { channel: "decide", kind: "choice", title: titleZh, titleZh,
    options: [answer("archive", "Archive", "归档", "归档这一版", { recommended: true, undoable }), answer("keep", "Keep", "留着", "先不动")],
    recommendation: { optionId: "archive", reason: "Conflicts with the current design", reasonZh: "和当前方案冲突" }, createdAt: stamp(), updatedAt: stamp() });
  cards.push(archiveCard("v11b", "V11b 和当前冲突 78 处，归档吗", true), archiveCard("v13", "V13 和 V11b 一样，归档吗", false));
  announce("attention/changed");
  // On the Attention page they arrive marked in the list, not as notices.
  await queued("V11b 和当前冲突 78 处，归档吗").and(queuePage.locator("[data-fresh]")).waitFor();
  assert.equal(await queuePage.locator(".attention-toast").filter({ hasText: "V11b" }).count(), 0, "a card arriving in the list on screen is no notice");
  const bulk = queuePage.locator(".attention-queue__bulk");
  await bulk.getByRole("button", { name: "全部按建议（2）" }).waitFor();
  const names = await bulk.locator(".attention-queue__bulk-names").textContent();
  assert.ok(names.includes("V16 和 V17 留哪个") && names.includes("V11b 和当前冲突 78 处，归档吗") && !names.includes("V13"), `the button names what it answers: ${names}`);
  await shoot(queuePage, "attention-queue-bulk-desktop-light");
  await bulk.getByRole("button", { name: "全部按建议（2）" }).click();
  await bulk.getByText("已按建议处理 2 张").waitFor();
  await queued("V11b 和当前冲突 78 处，归档吗").waitFor({ state: "detached" });
  assert.ok(await queued("V13 和 V11b 一样，归档吗").isVisible(), "the one that could not be taken back waits for the person");
  const [kind, bulkIds] = attentionActions.at(-1);
  assert.deepEqual([kind, bulkIds.split(",").sort()], ["bulk", ["att_decide", "att_v11b"]], "the two recommended decisions that can be taken back");
  // The undo says what it could not take back.
  await bulk.getByRole("button", { name: "撤销" }).click();
  await bulk.getByText("撤回了 1 张；1 张因为项目已经变了没撤，另起了卡问你").waitFor();
  await queued("V16 和 V17 留哪个").waitFor();
  assert.deepEqual(attentionActions.at(-1), ["bulk_undo", "bulk-1", null]);
  assert.equal(await queuePage.locator(".attention-toast").filter({ hasText: "V16 和 V17" }).count(), 0, "what the undo brought back is no news");
  // Another decision that can be taken back makes two again, and the button is back; across two projects it names them.
  cards.push(archiveCard("v14", "Housing 的 V14 和 V13 一样，归档吗", true, "B"));
  announce("attention/changed");
  await bulk.getByRole("button", { name: "全部按建议（2）" }).waitFor();
  const across = await bulk.locator(".attention-queue__bulk-names").textContent();
  assert.ok(across.includes("Thi Hemp 墙身节点·V16 和 V17 留哪个") && across.includes("Housing 13 户·Housing 的 V14") && across.endsWith("，共涉及 2 个项目"),
    `each card with its project: ${across}`);
  // With no conversation chosen, a message goes to the project's attention conversation, which then opens.
  assert.ok(await queuePage.locator(".attention-shell__drawer").isHidden());
  assert.equal(await queuePage.locator(".attention-shell__recent .chat-thread[aria-current]").count(), 0, "no conversation is chosen");
  assert.equal(await queuePage.locator(".attention-shell .chat-target__to").textContent(), "发到：关注对话");
  await queuePage.locator(".attention-shell #chat-input").fill("楼板再薄一点");
  await queuePage.keyboard.press("Enter");
  await queuePage.locator(".attention-shell__drawer").getByText("楼板再薄一点").waitFor();
  assert.deepEqual(chatPosts, [["att-conv-A", "楼板再薄一点"]]);
  // A conversation chosen in the column takes the next message, with its drawer closed too; the line above the
  // composer says where it goes.
  const sendTo = queuePage.locator(".attention-shell .chat-target__to");
  const composerInput = queuePage.locator(".attention-shell #chat-input");
  const sendLine = async (text) => { await composerInput.fill(text); await composerInput.press("Enter");
    await queuePage.locator(".attention-shell__drawer").getByText(text).waitFor(); };
  await queuePage.locator(".attention-shell__recent .chat-thread").filter({ hasText: /^楼板再薄一点/ }).click();
  await queuePage.locator(".attention-shell__drawer").getByText("我把 V16 的楼板改成只留一层胶合板。").waitFor();
  await queuePage.getByRole("button", { name: "收起对话" }).click();
  await queuePage.locator(".attention-shell__drawer").waitFor({ state: "hidden" });
  assert.equal(await sendTo.textContent(), "发到：楼板再薄一点");
  await sendLine("再薄 5 毫米");
  assert.deepEqual(chatPosts.at(-1), ["a1", "再薄 5 毫米"], "the chosen conversation, not the attention one");
  // A new conversation the person asked for is started by its first message.
  await queuePage.getByRole("button", { name: "在Thi Hemp 墙身节点里开新对话" }).click();
  assert.equal(await sendTo.textContent(), "发到：新对话");
  await sendLine("另开一条");
  assert.match(chatPosts.at(-1)[0], /^new-/, "a new conversation");
  // The column's Attention chooses none again: the next message goes to the attention conversation.
  await queuePage.locator(".attention-shell__row--attention").click();
  assert.equal(await sendTo.textContent(), "发到：关注对话");
  // A card that comes to need the architect is a notice; over the model, with the side panel open, it keeps
  // left of the side and above the floating toolbar where they share columns.
  await queuePage.keyboard.press("Alt+3");
  await queuePage.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "模型");
  await queuePage.locator(".attention-shell__surfaces .viewtools.model-tools").waitFor();
  await queuePage.locator(".attention-shell__rail-item").filter({ hasText: "后台" }).click();
  cards.push(queueCard("stairs", "B", { channel: "decide", kind: "choice", title: "Move the stair east", titleZh: "要不要把楼梯挪到东侧",
    options: [answer("move", "Move it", "挪", "楼梯挪到东侧"), answer("keep", "Keep it", "不挪", "楼梯不动")], createdAt: stamp(), updatedAt: stamp() }));
  announce("attention/changed");
  const cardNotice = queuePage.locator('.attention-toast[data-kind="card"]').filter({ hasText: "要你决定：要不要把楼梯挪到东侧" });
  await cardNotice.waitFor();
  await queuePage.waitForFunction(() => {
    const stack = document.querySelector(".attention-toasts")?.getBoundingClientRect(), side = document.querySelector(".attention-shell__side")?.getBoundingClientRect();
    const tools = document.querySelector(".attention-shell__surfaces .viewtools.model-tools")?.getBoundingClientRect();
    if (!stack || !side || !tools || !stack.height) return false;
    const shared = tools.left < stack.right && tools.right > stack.left;
    return stack.right <= side.left && (!shared || stack.bottom <= tools.top);
  });
  await shoot(queuePage, "attention-card-notice-model-desktop-light");
  // Its View shows the card in the queue.
  await cardNotice.getByRole("button", { name: "查看" }).click();
  await queuePage.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "关注");
  await queuePage.waitForFunction(() => document.activeElement?.id === "attention-card-att_stairs");
  await served.close();
  for (const [colour, viewport, name] of [["dark", { width: 1440, height: 900 }, "attention-queue-desktop-dark"], ["light", { width: 390, height: 844 }, "attention-queue-phone-light"]]) {
    preferredTheme = colour; cards = seedCards();
    const look = await browser.newContext({ viewport, ...(viewport.width < 500 ? { isMobile: true, hasTouch: true, deviceScaleFactor: 2 } : {}) });
    const lookPage = await look.newPage(); lookPage.setDefaultTimeout(15_000);
    lookPage.on("pageerror", (error) => errors.push(error.message));
    await lookPage.route((url) => url.pathname.startsWith("/api/"), route);
    await open(lookPage);
    await lookPage.locator(".attention-shell__queue .attention-card").nth(2).waitFor();
    const width = await lookPage.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(width <= viewport.width, `${name}: no sideways scroll (${width}px)`);
    await shoot(lookPage, name);
    await look.close();
  }
  attentionServed = false; cards = [];

  // Waiting (ADR-011 §8): one drawing job seen at 0.5 s, 3 s and 15 s. Under ten seconds the background says only that it
  // runs; past it, a bar and the time left, measured against this project's earlier jobs in the lane (40 s and 60 s: 50 s).
  const projectA = runtimes.get(projects[0].projectDir);
  const jobRow = (jobId, status, wallTimeS, startedAt = null, finishedAt = null) => ({ jobId, status, candidateId: `cand-${jobId}`, proposalId: "p-7", createdAt: now, startedAt,
    finishedAt, error: null, wallTimeS, lane: "exclusive", waitingFor: null, waitingReason: null });
  const started = Date.parse("2026-10-24T10:00:00Z");
  for (const colour of ["light", "dark"]) {
    preferredTheme = colour;
    for (const [age, caption] of [[0.5, "进行中"], [3, "进行中"], [15, "约还要 35 秒"]]) {
      projectA.retained = { projectId: "A", projectDir: projectA.projectDir, published: { version: 3, stateSha256: null }, candidates: [], branches: [], stages: [],
        errors: [], runsScanned: 3, hasMore: false,
        jobs: [jobRow("now", "running", null, new Date(started).toISOString()), jobRow("j1", "succeeded", 40, null, "2026-10-24T09:57:00Z"), jobRow("j2", "succeeded", 60, null, "2026-10-24T08:00:00Z")],
        // At 15 s a section has come out: the tile beside the conclusion shows it instead of the finished job.
        drawings: age < 10 ? [] : [{ drawingId: "d-section", name: "A-A 剖面.pdf", kind: "cut-plan", runId: "run-v17", sourceStageRef: "S2",
          generatedAt: "2026-10-24T09:58:00Z", assetSha256: "e".repeat(64), revisionRef: "rev-1", thumbnailPath: "/api/drawings/d-section/thumbnail.png" }] };
      const wait = await browser.newContext({ viewport: { width: 1440, height: 900 } });
      await wait.clock.setFixedTime(new Date(started + age * 1000));
      const waitPage = await wait.newPage(); waitPage.setDefaultTimeout(15_000);
      waitPage.on("pageerror", (error) => errors.push(error.message));
      await waitPage.route((url) => url.pathname.startsWith("/api/"), route);
      await open(waitPage);
      // The Attention page leads with its conclusion; the running job and the latest result sit beside it.
      const overview = waitPage.locator(".attention-overview");
      await overview.locator(".attention-overview__tile").filter({ hasText: "1 个在跑" }).filter({ hasText: age < 10 ? "" : caption }).waitFor();
      const recent = age < 10 ? overview.locator(".attention-overview__tile").filter({ hasText: "最近完成" }).filter({ hasText: "Thi Hemp 墙身节点 · 3 分钟前" })
        : overview.getByRole("button", { name: "最近出的图：A-A 剖面.pdf" });
      await recent.waitFor();
      if (age >= 10) {
        assert.match(await recent.textContent(), /Thi Hemp 墙身节点 · 2 分钟前/);
        await recent.locator("img").evaluate((picture) => picture.complete && picture.naturalWidth > 0 || new Promise((resolve) => picture.addEventListener("load", resolve, { once: true })));
      }
      await waitPage.getByRole("button", { name: "展开后台" }).click();
      const row = waitPage.locator(".attention-shell__tasks .chat-task").filter({ hasText: "Thi Hemp" });
      await row.getByText(caption).waitFor();
      assert.equal(await row.locator(".chat-task__progress").count(), age < 10 ? 0 : 1, `${age} s: a bar only past ten seconds`);
      if (age >= 10) assert.equal(await row.locator(".chat-task__progress").getAttribute("aria-valuenow"), "30", "15 s of a 50 s job");
      await waitPage.locator(".attention-shell__panel").evaluate((panel) => Promise.all(panel.getAnimations({ subtree: true })
        .filter((animation) => animation.effect?.getTiming().iterations !== Infinity).map((animation) => animation.finished)));
      await shoot(waitPage, `attention-wait-${String(age).replace(".", "_")}s-${colour}`);
      if (age >= 10 && colour === "light") {
        // One click opens the drawing on the Board.
        await waitPage.getByRole("button", { name: "收起侧栏" }).first().click();
        await recent.click();
        await waitPage.waitForFunction(() => document.querySelector(".menu-tab[aria-pressed=true]")?.textContent === "画板");
      }
      await wait.close();
    }
  }
  projectA.retained = null;

  // Phone: the projects column and the side panel open over the center.
  for (const colour of ["light", "dark"]) {
    preferredTheme = colour;
    const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    const small = await phone.newPage(); small.setDefaultTimeout(15_000);
    small.on("pageerror", (error) => errors.push(error.message));
    await small.route((url) => url.pathname.startsWith("/api/"), route);
    await open(small);
    assert.ok(await small.locator(".attention-shell__left").isHidden(), `${colour}: the projects column folds away`);
    assert.ok(await small.locator(".attention-shell .chat-composer").isVisible(), `${colour}: the composer stays`);
    const width = await small.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(width <= 390, `${colour}: no sideways scroll (${width}px)`);
    await shoot(small, `attention-phone-${colour}`);
    await small.locator(".attention-shell__left-toggle").click();
    await small.locator(".attention-shell__left").waitFor();
    await shoot(small, `attention-phone-projects-${colour}`);
    await small.locator(".attention-shell__scrim--left").click({ position: { x: 370, y: 400 } });
    await small.locator(".attention-shell__left").waitFor({ state: "hidden" });
    await small.getByRole("button", { name: "展开后台" }).click();
    await small.locator(".attention-shell__panel").waitFor();
    await shoot(small, `attention-phone-side-${colour}`);
    await phone.close();
  }
  assert.deepEqual(errors, [], "page errors");
  console.log(JSON.stringify({ unexpected: [...new Set(unexpected)] }));
  console.log("PASS attention shell: projects column, tabs over the open surfaces, conversation drawer, side panel, phone drawers");
} finally {
  await browser.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
