import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Real built Hub UI; all provider and project calls are local, synthetic fixtures.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-chat-ui-"));
const toolLoads = [];
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/tool") {
    toolLoads.push(req.url);
    res.setHeader("Content-Type", "text/html");
    // The embedded page asks through the real shared contract module.
    res.end(`<h1>Project tool fixture</h1><label>Unsaved workspace note<textarea id="note"></textarea></label><button id="ask">Start modeling in the conversation</button>
      <script type="module">
        import { requestStartModeling } from "/shared/hostBridge.js";
        document.querySelector("#ask").addEventListener("click", () => requestStartModeling());
      </script>`);
    return;
  }
  if (pathname === "/shared/hostBridge.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(await readFile(fileURLToPath(new URL("../../../shared-web/src/hostBridge.js", import.meta.url))));
    return;
  }
  const filename = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  if (!filename.startsWith(root)) { res.writeHead(404); res.end(); return; }
  try { res.setHeader("Content-Type", filename.endsWith(".js") ? "text/javascript" : filename.endsWith(".css") ? "text/css" : "text/html"); res.end(await readFile(filename)); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE ?? "C:/Users/asus/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs").href);
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(12000);
const errors = [], writes = [], sessions = [], providerReads = [];
let permissionResponseGate = Promise.resolve(), permissionFailure = null;
let modelingResponseGate = Promise.resolve();
let modelingFailure = null;
let chatCreationFailureFor = null;
let projectListGate = null;
let settings = { projectDir: "D:\\fixture\\A", referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: 18788 };
// The one saved preferences document: appearance and the new-conversation defaults.
let preferences = { language: "en", theme: "light", fontScale: 1 };
const projects = [
  { projectId: "A", projectDir: "D:\\fixture\\A", name: "Project A", chatCount: 0, version: 3, stage: "S2" },
  { projectId: "B", projectDir: "D:\\fixture\\B", name: "Project B", chatCount: 0, version: 0, stage: null },
];
const apps = ["monkeyarch", "monkeydiagram", "monkeyboard", "monkeyfab", "monkeymonitor"].map((appId) => ({ appId, title: appId, serviceId: appId === "monkeyfab" ? "hub" : appId === "monkeymonitor" ? "monitor" : "studio", state: "running", processId: 1234, available: true, url: `${origin}/tool?app=${appId}` }));
const projectApps = new Map();
const appsFor = (target) => {
  if (!projectApps.has(target)) projectApps.set(target, apps.map((app) => app.serviceId === "studio" ? { ...app, state: "stopped", processId: null, url: `${app.url}&project=${encodeURIComponent(target)}` } : app));
  return projectApps.get(target);
};
page.on("pageerror", (error) => errors.push(error.message));
await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
  const req = route.request(), url = new URL(req.url()), method = req.method();
  const data = () => req.postDataJSON();
  const json = (body, status = 200) => route.fulfill({ json: body, status });
  if (method !== "GET") writes.push([method, url.pathname, data(), url.searchParams.get("projectDir")]);
  if (url.pathname === "/api/settings/apps") { if (method === "PUT") settings = data(); return json(settings); }
  if (url.pathname === "/api/settings/user") { if (method === "PUT") preferences = data(); return json(preferences); }
  if (url.pathname === "/api/apps") return json(url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : apps);
  if (url.pathname === "/api/project/modeling") {
    const selected = projects.find((item) => item.projectDir === url.searchParams.get("projectDir"));
    assert.equal(data().projectId, selected?.projectId);
    const currentApps = appsFor(selected.projectDir).filter((item) => item.serviceId === "studio");
    for (const item of currentApps) item.state = "starting";
    await modelingResponseGate;
    for (const item of currentApps) { item.state = "running"; item.processId = 2000 + [...projectApps.keys()].indexOf(selected.projectDir); }
    return modelingFailure ? json(modelingFailure, 409) : json({ projectId: selected.projectId, initialized: true });
  }
  if (url.pathname.startsWith("/api/apps/")) {
    const [, , , id, action] = url.pathname.split("/");
    const currentApps = url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : apps;
    const app = currentApps.find((item) => item.appId === id);
    if (!app.available) return json(app);
    for (const item of currentApps.filter((item) => item.serviceId === app.serviceId)) { item.state = action === "start" ? "running" : "stopped"; item.processId = action === "start" ? 1234 : null; }
    return json(app);
  }
  if (url.pathname === "/api/chat/providers") {
    providerReads.push(url.searchParams.get("refresh"));
    return json([
      { id: "codex", label: "Codex CLI", available: true, detail: "Fixture only", installed: true, signedIn: true,
        models: ["fixture-model-a", "fixture-model-b"], modelCatalog: "ready", modelDetail: "Listed by the fixture CLI." },
      { id: "claude", label: "Claude Code", available: true, detail: "Fixture only", installed: true, signedIn: true,
        models: [], modelCatalog: "unavailable", modelDetail: "This CLI offers no model list; enter a model id." },
      { id: "coding-plan", label: "Coding Plan", available: false, detail: "Not configured", installed: true, signedIn: null,
        models: [], modelCatalog: "unavailable", modelDetail: "Configure the endpoint first." },
    ]);
  }
  if (url.pathname === "/api/chat/workspace") return json({ workspaceDir: "D:\\fixture", configured: true, projects: projects.map((row) => row.projectId) });
  if (url.pathname === "/api/chat/projects") {
    if (method === "GET") {
      const listed = [...projects], gate = projectListGate;
      projectListGate = null;
      if (gate) await gate;
      return json(listed);
    }
    const name = data().name;
    if (projects.some((row) => row.projectId === name)) return json({ code: "PROJECT_EXISTS", detail: "A folder of that name already exists here and was left untouched." }, 409);
    const made = { projectId: name, projectDir: ["D:\\fixture", name].join("\\"), name, chatCount: 0, version: 0, stage: null };
    projects.push(made); return json(made, 201);
  }
  if (url.pathname === "/api/chat/sessions") {
    if (method === "GET") return json(sessions.filter((session) => Boolean(session.archived) === (url.searchParams.get("archived") === "true")));
    const body = data();
    if (body.projectDir === chatCreationFailureFor) return json({ code: "CHAT_PROVIDER_UNAVAILABLE", detail: "The CLI connection is temporarily unavailable." }, 503);
    let project = projects.find((item) => item.projectDir === body.projectDir);
    if (!project && body.projectDir === "D:\\fixture\\C") {
      project = { projectId: "C", projectDir: body.projectDir, name: "Project C", chatCount: 0, version: 0, stage: null };
      projects.push(project);
    }
    const session = { ...body, id: `chat-${sessions.length + 1}`, projectId: project.projectId, title: "New chat", status: "idle", archived: false, createdAt: "2026-09-11", updatedAt: "2026-09-11", messages: [] };
    sessions.unshift(session); return json(session);
  }
  const permissionMatch = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/permissions\/([^/]+)$/);
  if (permissionMatch) {
    assert.equal(method, "POST");
    const session = sessions.find((item) => item.id === permissionMatch[1]);
    assert.equal(data().projectId, session.projectId);
    if (permissionFailure) return json({ code: "CHAT_PERMISSION_EXPIRED", detail: "This permission request is no longer pending." }, permissionFailure);
    await permissionResponseGate;
    const message = session.messages.find((item) => item.permission?.id === decodeURIComponent(permissionMatch[2]));
    assert.ok(message, "only a pending permission can be answered");
    assert.ok(data().optionId === null || message.permission.options.some((option) => option.optionId === data().optionId));
    message.permission = null;
    return json(session);
  }
  const match = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)(?:\/(messages|stop|model|archive|fail))?$/);
  if (match) {
    const session = sessions.find((item) => item.id === match[1]);
    if (match[2] === "messages") {
      assert.equal(Boolean(session.archived), false, "archived chats must be restored before sending");
      assert.equal(data().projectId, session.projectId);
      assert.equal(appsFor(session.projectDir).find((item) => item.appId === "monkeyarch").state, "running");
      session.messages.push({ id: `u-${session.messages.length}`, role: "user", content: data().content, status: "complete" });
      session.title = session.messages[0].content; session.status = "running";
      // What the API saves while the CLI works: one row per MCP call, a failed
      // one, and the finished candidate that call reported.
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "streaming",
        content: "studio_schema · GET /api/state · in_progress" });
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "failed",
        content: "studio_request · POST /api/issue · failed\nHubFailure(422): This action is not exposed to the chat." });
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "complete",
        candidateId: `cand-${session.projectId}-1`,
        content: "studio_request · GET /api/jobs/job-1 · completed\ncandidateId: cand-" + session.projectId + "-1\nstatus: succeeded" });
    } else if (match[2] === "stop") session.status = "interrupted";
    else if (match[2] === "fail") {
      // Recorded from real runs: Codex answering an unknown model, and a
      // failure whose code this page has never seen.
      session.status = "failed";
      session.error = data().kind === "model"
        ? { code: "CHAT_PROVIDER_FAILED", detail: '{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The \'not-a-real-model-xyz\' model is not supported when using Codex with a ChatGPT account."}}' }
        : { code: "CHAT_SOMETHING_NEW", detail: '{"trace":"unrecognised","status":523}' };
      // A message whose text happens to be JSON is still a message.
      session.messages.push({ id: `a-${session.messages.length}`, role: "assistant", status: "complete",
        content: '{"note":"this is ordinary content, not a failure"}' });
    }
    else if (match[2] === "model") {
      assert.notEqual(session.status, "running", "a running turn never has its model changed");
      session.model = data().model;
    }
    else if (match[2] === "archive") {
      assert.equal(method, "PUT");
      if (session.status === "running") return json({ code: "CHAT_RUNNING", detail: "Wait for this reply to finish or stop it before archiving the chat." }, 409);
      session.archived = data().archived;
    }
    return json(session);
  }
  errors.push(`Unexpected request: ${method} ${url.pathname}`); return json({ detail: "Unexpected fixture request" }, 404);
});
const railWidth = () => page.evaluate(() => document.querySelector(".chat-rail").getBoundingClientRect().width);
const boxOf = (selector) => page.evaluate((value) => {
  const node = document.querySelector(value);
  return node ? node.getBoundingClientRect().width : 0;
}, selector);
const activityRows = (expected) => page.waitForFunction(
  (count) => document.querySelectorAll(".chat-activity").length === count, expected);
const studioReady = () => page.waitForFunction(() => ["Modeling", "Drawings", "Board"].every(label =>
  document.querySelector(`.chat-rail__tool[aria-label="${label}"]`)?.dataset.state === "running"));
try {
  await page.goto(origin);
  await page.getByRole("heading", { name: "Start with an idea" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Enter workspace" }).count(), 0);

  // A — one vertical rail on the far right holds every tool entry, and there
  // is no second copy of them anywhere.
  const rail = page.getByRole("navigation", { name: "Project tools" });
  await rail.waitFor();
  for (const label of ["Modeling", "Drawings", "Board", "Fabrication", "Usage"]) {
    assert.equal(await page.getByRole("button", { name: label, exact: true }).count(), 1, `${label} appears once`);
  }
  assert.ok(await railWidth() > 40, "the rail stays on screen while the tool content is closed");
  assert.equal(await page.locator(".chat-browser").count(), 0);
  await studioReady();
  assert.equal(writes.filter(([, pathname, , target]) => pathname === "/api/project/modeling" && target === "D:\\fixture\\A").length, 1,
    "entering the configured project prepares one Studio before the first workspace click or chat");
  assert.ok(!writes.some(([, pathname]) => /\/api\/apps\/monkey(arch|diagram|board)\/start$/.test(pathname)),
    "project preparation owns Studio startup without a second frontend start call");

  // A new project is made in the workspace and opens straight into a chat.
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  const create = page.getByRole("dialog").filter({ hasText: "New project" });
  assert.equal(await create.locator("#new-project-workspace").inputValue(), "D:\\fixture");
  await create.locator("#new-project-name").fill("harbour study");
  await create.getByText("harbour study", { exact: false }).first().waitFor();
  await create.locator("#new-project-name").fill("A");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await create.getByRole("alert").filter({ hasText: "already exists" }).waitFor();
  await create.locator("#new-project-name").fill("harbour-study");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await page.waitForFunction(() => document.querySelectorAll(".chat-project").length === 3);
  assert.equal(sessions.length, 1, "the new project opened its own conversation");
  assert.equal(sessions[0].projectDir, "D:\\fixture\\harbour-study");
  assert.equal(sessions[0].provider, "codex", "a new conversation takes the saved default connection");
  await studioReady();
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "harbour-study").length, 1);
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeyfab"));
  assert.ok(writes.some(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "harbour-study"),
    "new-project creation prepares the first modeling base before the first message");
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  const beforeIndependent = writes.length;
  const beforeIndependentProject = settings.projectDir;
  for (const [label, id] of [["Fabrication", "monkeyfab"], ["Usage", "monkeymonitor"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForFunction((app) => document.querySelector("iframe:not([hidden])")?.src.includes(`app=${app}`), id);
  }
  assert.equal(settings.projectDir, beforeIndependentProject, "independent tools leave the shared Studio on its existing project");
  assert.ok(writes.slice(beforeIndependent).every(([, pathname]) => ["/api/apps/monkeyfab/start", "/api/apps/monkeymonitor/start"].includes(pathname)),
    "independent tools neither stop Studio nor rewrite its project configuration");
  await page.screenshot({ path: path.join(temporary, "new-project.png") });
  // Back to the first project's conversation for the rest of this walk.
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();

  await page.getByRole("textbox", { name: "What would you like to do in this project?" }).fill("Widen the courtyard");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(sessions.length, 2);
  assert.ok(writes.some(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "A"),
    "selecting an existing project prepares its base before its first task connects");
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "A").length, 1,
    "returning to a prepared project and sending a message share the original preparation");
  assert.equal(sessions[0].provider, "codex", "a new chat takes the saved default connection");
  // The composer names the connection and lets this conversation pick a model;
  // the connection itself is still chosen once, in Hub settings.
  await page.locator(".chat-connection").filter({ hasText: "Codex CLI" }).waitFor();
  assert.equal(await page.locator("#chat-provider").count(), 0);
  const modelPicker = page.locator("#chat-model");
  assert.deepEqual(await modelPicker.locator("option").allInnerTexts(),
    ["CLI default model", "fixture-model-a", "fixture-model-b", "Custom model id…"]);
  assert.equal(await modelPicker.isDisabled(), true, "a running turn keeps the model it started with");

  // The MCP activity is readable, its diagnostics stay collapsed until asked
  // for, and the failed call is visible rather than silent.
  await activityRows(3);
  await page.getByText("studio_request · POST /api/issue · failed").waitFor();
  assert.equal(await page.getByText("HubFailure(422): This action is not exposed to the chat.").isVisible(), false);
  await page.getByText("studio_request · POST /api/issue · failed").click();
  await page.getByText("HubFailure(422): This action is not exposed to the chat.").waitFor();

  // The agent's permission choices appear in its activity row and wait for an
  // actual choice. Repeated clicks cannot answer the same request twice.
  const pendingActivity = sessions[0].messages.find((message) => message.role === "tool" && message.status === "streaming");
  const permission = { id: "permission-1", title: "Allow the requested command?", options: [
    { optionId: "allow-once", name: "Allow once", kind: "allow_once" },
    { optionId: "reject-once", name: "Reject", kind: "reject_once" },
  ] };
  const permissionWrites = () => writes.filter((entry) => entry[1].includes("/permissions/"));
  pendingActivity.permission = permission;
  const choices = page.getByRole("group", { name: permission.title });
  await choices.waitFor();
  assert.deepEqual(await choices.getByRole("button").allInnerTexts(), ["Allow once", "Reject", "Cancel"]);
  assert.equal(permissionWrites().length, 0, "rendering a permission never agrees to it");
  await page.screenshot({ path: path.join(temporary, "permission-pending.png") });
  let releasePermission;
  permissionResponseGate = new Promise((resolve) => { releasePermission = resolve; });
  const selected = page.waitForRequest((req) => req.url().endsWith("/permissions/permission-1"));
  await choices.getByRole("button", { name: "Allow once" }).evaluate((button) => { button.click(); button.click(); });
  await selected;
  await page.waitForFunction(() => [...document.querySelectorAll(".chat-permission button")].every((button) => button.disabled));
  assert.equal(permissionWrites().length, 1);
  assert.deepEqual(permissionWrites()[0][2], { projectId: "A", optionId: "allow-once" });
  releasePermission();
  await choices.waitFor({ state: "hidden" });
  permissionResponseGate = Promise.resolve();

  pendingActivity.permission = { ...permission, id: "permission-cancel" };
  await choices.waitFor();
  await choices.getByRole("button", { name: "Cancel", exact: true }).click();
  await choices.waitFor({ state: "hidden" });
  assert.deepEqual(permissionWrites().at(-1)[2], { projectId: "A", optionId: null });

  pendingActivity.permission = { ...permission, id: "permission-expired" };
  permissionFailure = 409;
  await choices.waitFor();
  await choices.getByRole("button", { name: "Reject", exact: true }).click();
  const permissionError = page.getByRole("alert").filter({ hasText: "CHAT_PERMISSION_EXPIRED" });
  await permissionError.waitFor();
  assert.equal(await choices.getByRole("button", { name: "Reject", exact: true }).isEnabled(), true);
  await permissionError.getByRole("button", { name: "Close", exact: true }).click();
  pendingActivity.permission = null; permissionFailure = null;
  await choices.waitFor({ state: "hidden" });

  // The candidate that step produced opens beside the conversation, which the
  // panel keeps as it was.
  await page.getByRole("button", { name: "Open this candidate on the right" }).first().click();
  await page.frameLocator('iframe:not([hidden])').getByRole("heading", { name: "Project tool fixture" }).waitFor();
  const opened = await page.locator('iframe:not([hidden])').getAttribute("src");
  assert.match(opened, /candidate=cand-A-1/);
  assert.match(opened, /embedded=tool/);
  const beforePeerWorkspaces = writes.length;
  for (const [label, id] of [["Drawings", "monkeydiagram"], ["Board", "monkeyboard"], ["Modeling", "monkeyarch"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForFunction((app) => document.querySelector("iframe:not([hidden])")?.src.includes(`app=${app}`), id);
  }
  assert.equal(writes.length, beforePeerWorkspaces, "the other workspaces reuse the project's running Studio without repeated preparation");
  await page.frameLocator('iframe:not([hidden])').getByRole("heading", { name: "Project tool fixture" }).waitFor();
  await page.locator('iframe:not([hidden])').evaluate((frame) => { frame.contentWindow.switchMarker = "retained"; });
  await page.waitForFunction(() => [...document.querySelectorAll("iframe")].every((frame) => frame.contentDocument?.querySelector("#note")));
  for (const frame of await page.locator("iframe").all()) await frame.evaluate((element) => {
    element.contentDocument.querySelector("#note").value = new URL(element.src).searchParams.get("app");
  });
  const beforeCachedLoads = toolLoads.length;
  const beforeTabSwitch = writes.length;
  for (const [label, id] of [["Drawings", "monkeydiagram"], ["Board", "monkeyboard"], ["Modeling", "monkeyarch"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForFunction((app) => document.querySelector("iframe:not([hidden])")?.src.includes(`app=${app}`), id);
  }
  assert.equal(writes.length, beforeTabSwitch, "cached tool switches never restart or prepare the project");
  assert.equal(toolLoads.length, beforeCachedLoads, "cached workspaces do not reload their documents");
  for (const frame of await page.locator("iframe").all()) {
    const dimensions = await frame.evaluate((element) => ({ width: element.getBoundingClientRect().width,
      height: element.getBoundingClientRect().height, note: element.contentDocument.querySelector("#note").value,
      app: new URL(element.src).searchParams.get("app") }));
    assert.ok(dimensions.width > 100 && dimensions.height > 100, "inactive workspace keeps its viewport instead of shrinking to zero");
    assert.equal(dimensions.note, dimensions.app, "each workspace retains its own unfinished input");
  }
  assert.equal(await page.locator('iframe:not([hidden])').evaluate((frame) => frame.contentWindow.switchMarker), "retained", "switches retain the loaded modeling page");
  await page.getByRole("heading", { name: "Widen the courtyard", exact: true }).waitFor();
  await activityRows(3);
  // The page's own local address is a connection detail, not permanent chrome:
  // it is not on screen, and it is in the project card when asked for.
  assert.equal(await page.locator(".chat-browser__address").count(), 0);
  assert.equal(await page.getByText(origin, { exact: false }).count(), 0, "no raw tool URL is on screen");
  await page.getByRole("button", { name: /Project A/ }).last().click();
  const connection = page.getByRole("dialog", { name: "Project" });
  await connection.getByText("Connection details").waitFor();
  assert.equal(await connection.locator("#tool-url").isVisible(), false, "the address stays folded away");
  await connection.getByText("Connection details").click();
  assert.match(await connection.locator("#tool-url").inputValue(), /candidate=cand-A-1/);
  await connection.getByRole("button", { name: "Reload page" }).click();
  await connection.getByRole("button", { name: "Close" }).click();
  await page.frameLocator('iframe:not([hidden])').getByRole("heading", { name: "Project tool fixture" }).waitFor();

  // A — dragging really moves the boundary: the conversation and the tool page
  // both change width, and the conversation keeps its floor.
  const before = { chat: await boxOf(".chat-main"), frame: await boxOf("iframe:not([hidden])") };
  const handle = page.getByRole("separator", { name: "Resize right panel" });
  const bounds = await handle.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x - 260, bounds.y + bounds.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForFunction((width) => document.querySelector("iframe:not([hidden])").getBoundingClientRect().width > width + 100, before.frame);
  const wider = { chat: await boxOf(".chat-main"), frame: await boxOf("iframe:not([hidden])") };
  assert.ok(wider.chat < before.chat - 100, "the conversation gives up the width the panel takes");
  assert.ok(wider.frame > before.frame + 100, "the embedded page actually becomes wider");
  // Past the floor the conversation stops shrinking.
  const floorHandle = await handle.boundingBox();
  await page.mouse.move(floorHandle.x + floorHandle.width / 2, floorHandle.y + 200);
  await page.mouse.down();
  await page.mouse.move(80, floorHandle.y + 200, { steps: 12 });
  await page.mouse.up();
  assert.ok(await boxOf(".chat-main") >= 355, `the conversation keeps its 360px floor, got ${await boxOf(".chat-main")}`);

  // Expanding projects, narrowing the window and restoring a wider saved
  // panel must resize the content, never push the tool rail off screen.
  const assertFitted = async () => {
    const layout = await page.evaluate(() => {
      const rail = document.querySelector(".chat-rail").getBoundingClientRect();
      const panel = document.querySelector(".chat-browser").getBoundingClientRect();
      return { right: rail.right, panelRight: panel.right, railLeft: rail.left,
        scrollWidth: document.querySelector(".chat-shell").scrollWidth, viewport: innerWidth };
    });
    assert.ok(Math.abs(layout.right - layout.viewport) <= 1, `the rail stays at the viewport edge: ${JSON.stringify(layout)}`);
    assert.ok(layout.panelRight <= layout.railLeft + 1, "the embedded tool ends before the rail");
    assert.ok(layout.scrollWidth <= layout.viewport + 1, "the shell has no hidden horizontal overflow");
  };
  await page.getByRole("button", { name: "Hide projects", exact: true }).first().click();
  const collapsedHandle = await handle.boundingBox();
  await page.mouse.move(collapsedHandle.x + collapsedHandle.width / 2, collapsedHandle.y + 200);
  await page.mouse.down();
  await page.mouse.move(80, collapsedHandle.y + 200, { steps: 12 });
  await page.mouse.up();
  const collapsedFrame = await boxOf(".chat-browser");
  await page.getByRole("button", { name: "Show projects", exact: true }).first().click();
  await assertFitted();
  assert.ok(await boxOf(".chat-browser") < collapsedFrame - 100, "an expanded sidebar leaves less space for the tool");
  for (const width of [1180, 1000, 901]) {
    await page.setViewportSize({ width, height: 960 });
    await assertFitted();
    assert.ok(await boxOf(".chat-main") >= 355, "the conversation remains usable at the desktop breakpoint");
  }
  await page.reload();
  await page.locator(".chat-browser").waitFor();
  await assertFitted();
  await page.setViewportSize({ width: 1440, height: 960 });
  await assertFitted();

  // A — the rail's own control closes and reopens the tool content.
  await page.frameLocator('iframe:not([hidden])').getByRole("heading", { name: "Project tool fixture" }).waitFor();
  const collapsedFrameCount = await page.locator("iframe").count(), beforeCollapseLoads = toolLoads.length;
  const beforeCollapseSize = await page.locator('iframe:not([hidden])').evaluate((frame) => {
    frame.contentDocument.querySelector("#note").value = "Keep the candidate camera, drawing marks and board edits";
    frame.contentWindow.collapseMarker = "retained";
    return { width: frame.clientWidth, height: frame.clientHeight };
  });
  await page.getByRole("button", { name: "Hide tools" }).click();
  await page.locator(".chat-browser").waitFor({ state: "hidden" });
  assert.equal(await page.locator("iframe").count(), collapsedFrameCount, "collapsing keeps the existing workspace documents mounted");
  assert.deepEqual(await page.locator('iframe:not([hidden])').evaluate((frame) => ({ width: frame.clientWidth, height: frame.clientHeight })),
    beforeCollapseSize, "collapsing does not resize the workspace to zero");
  assert.ok(await railWidth() > 40, "the rail remains after the tool content is closed");
  await page.getByRole("button", { name: "Show tools" }).click();
  await page.locator(".chat-browser").waitFor();
  assert.equal(toolLoads.length, beforeCollapseLoads, `reopening does not reload any workspace: ${JSON.stringify(toolLoads.slice(beforeCollapseLoads))}`);
  assert.equal(await page.locator('iframe:not([hidden])').evaluate((frame) => frame.contentWindow.collapseMarker), "retained");
  assert.equal(await page.frameLocator('iframe:not([hidden])').getByLabel("Unsaved workspace note").inputValue(),
    "Keep the candidate camera, drawing marks and board edits", "unfinished workspace input survives collapsing and reopening");

  // C — the project gear answers for the bound project, not for the tools.
  await page.getByRole("button", { name: /Project A/ }).last().click();
  const card = page.getByRole("dialog", { name: "Project" });
  await card.waitFor();
  await card.getByText("D:\\fixture\\A").waitFor();
  await card.getByText("Version 3").waitFor();
  await card.getByText("S2", { exact: true }).waitFor();
  await card.getByText("cand-A-1").waitFor();
  await card.getByText("Candidate — not endorsed, not issued").waitFor();
  assert.equal(await card.getByRole("button", { name: /Accept|Issue|Endorse/ }).count(), 0);
  await card.getByRole("button", { name: "Close" }).click();

  // The saved frame comes back after a reload, with the conversation.
  const savedWidth = await boxOf(".chat-browser");
  await page.reload();
  await page.getByText("studio_request · GET /api/jobs/job-1 · completed").waitFor();
  await activityRows(3);
  await page.locator(".chat-browser").waitFor();
  assert.ok(Math.abs(await boxOf(".chat-browser") - savedWidth) < 12, "the panel width is restored");

  // B — the global defaults live in the bottom-left Hub settings only.
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({ hasText: "Hub settings (global)" });
  await dialog.getByRole("heading", { name: "New conversation defaults" }).waitFor();
  await dialog.getByText("The model list comes from this CLI's own catalogue.").waitFor();
  assert.deepEqual(await page.locator("#default-chat-model option").allInnerTexts(),
    ["CLI default model", "fixture-model-a", "fixture-model-b", "Custom model id…"]);
  // Each connection is named with what was actually found about it.
  assert.deepEqual(await page.locator("#default-chat-provider option").allInnerTexts(),
    ["Not set (use Codex CLI)", "Codex CLI · Signed in", "Claude Code · Signed in", "Coding Plan · Not configured"]);
  await page.locator("#default-chat-provider").selectOption("claude");
  // The sentence follows the chosen connection, and a connection with no
  // catalogue says so instead of showing an empty list.
  await dialog.getByText("This CLI offers no model list; a model id can be entered by hand.").waitFor();
  await page.locator("#default-chat-model").selectOption("__custom__");
  await page.locator("#default-chat-model-custom").fill("claude-opus-5");
  const rechecks = providerReads.filter((value) => value === "true").length;
  await page.locator("#recheck-connections").click();
  await page.waitForFunction((count) => true, rechecks);
  await page.locator("#save-appearance").click();
  await page.waitForFunction(() => document.querySelector("#save-appearance")?.disabled === true);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  assert.ok(providerReads.filter((value) => value === "true").length > rechecks, "checking again asks the CLIs again");
  assert.ok(providerReads.filter((value) => value !== "true").length > 3, "the polling path never asks for a new check");
  const savedPreferences = writes.filter(([method, pathname]) => method === "PUT" && pathname === "/api/settings/user").at(-1);
  assert.equal(savedPreferences[2].chatProvider, "claude");
  assert.equal(savedPreferences[2].chatModel, "claude-opus-5");
  assert.equal(savedPreferences[2].theme, "light", "appearance is saved in the same document");

  // The existing conversation keeps the connection it was created with.
  await page.locator(".chat-connection").filter({ hasText: "Codex CLI" }).waitFor();
  await page.reload();
  await page.locator(".chat-connection").filter({ hasText: "Codex CLI" }).waitFor();

  // A new conversation takes the new default; the old one is untouched.
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("button", { name: "Send", exact: true }).waitFor();

  // This conversation picks its own model: saved for it, not for everyone.
  const picker = page.locator("#chat-model");
  await page.waitForFunction(() => document.querySelector("#chat-model")?.disabled === false);
  const globalWrites = writes.filter(([method, pathname]) => method === "PUT" && pathname === "/api/settings/user").length;
  await picker.selectOption("fixture-model-b");
  await page.waitForFunction(() => document.querySelector("#chat-model")?.value === "fixture-model-b");
  const saved = writes.filter(([method, pathname]) => method === "PUT" && pathname.endsWith("/model")).at(-1);
  assert.equal(saved[2].model, "fixture-model-b");
  assert.match(saved[1], /^\/api\/chat\/sessions\/chat-2\/model$/, "saved on this conversation only");
  assert.equal(sessions.find((row) => row.id === "chat-2").model, "fixture-model-b");
  assert.equal(writes.filter(([method, pathname]) => method === "PUT" && pathname === "/api/settings/user").length, globalWrites,
    "choosing a model in a chat does not rewrite the global default");
  await page.reload();
  await page.waitForFunction(() => document.querySelector("#chat-model")?.value === "fixture-model-b");
  // A model this connection does not list can still be entered by hand.
  await picker.selectOption("__custom__");
  await page.locator("#chat-custom-model").fill("hand-entered-model");
  await page.getByRole("button", { name: "Use", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#chat-model")?.value === "hand-entered-model");
  assert.equal(sessions.find((row) => row.id === "chat-2").model, "hand-entered-model");
  const runningA = sessions.find((row) => row.projectId === "A");
  runningA.status = "running";
  const beforeSwitchWrites = writes.length;
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-connection").filter({ hasText: "Claude Code" }).waitFor();
  await page.getByRole("textbox", { name: "What would you like to do in this project?" }).fill("Keep this draft");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(sessions.length, 3);
  assert.equal(sessions[0].provider, "claude");
  assert.equal(sessions[0].model, "claude-opus-5");
  assert.ok(sessions.slice(1).every((row) => row.provider === "codex"), "the earlier conversations were not rewritten");
  assert.equal(settings.projectDir, "D:\\fixture\\A", "cross-project chat leaves the default project unchanged");
  assert.equal(runningA.status, "running", "A continues while B starts its own turn");
  assert.ok(!writes.slice(beforeSwitchWrites).some(([method, pathname]) => pathname.endsWith("/stop") || (method === "PUT" && pathname === "/api/settings/apps")), "switching never stops A or rewrites its configuration");
  assert.equal(writes.slice(beforeSwitchWrites).filter(([, pathname, , target]) => pathname === "/api/project/modeling" && target === "D:\\fixture\\B").length, 1);

  // C — the gear follows the conversation's project rather than keeping the old one.
  await page.getByRole("button", { name: /Project B/ }).last().click();
  const second = page.getByRole("dialog", { name: "Project" });
  await second.getByText("D:\\fixture\\B").waitFor();
  await second.getByText("Version 0").waitFor();
  await second.getByText("No confirmed Stage").waitFor();
  assert.equal(await second.getByText("D:\\fixture\\A").count(), 0, "no stale project is left in the card");
  await second.getByRole("button", { name: "Close" }).click();

  // A successful candidate opens immediately while the same turn continues
  // working. Neither repeated clicks nor terminal-state polling reload it.
  const completing = sessions[0];
  const beforeReadbackStarts = writes.filter(([, pathname]) => pathname.endsWith("/start")).length;
  completing.messages.push({ id: "final-checkpoint", role: "tool", status: "complete", candidateId: "cand-B-final", content: "Final checkpoint completed" });
  assert.equal(completing.status, "running");
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("candidate=cand-B-final"));
  await page.frameLocator('iframe:not([hidden])').getByRole("heading", { name: "Project tool fixture" }).waitFor();
  await page.locator('iframe:not([hidden])').evaluate((frame) => { frame.contentWindow.completionMarker = "once"; });
  await page.waitForTimeout(1600);
  assert.equal(await page.locator('iframe:not([hidden])').evaluate((frame) => frame.contentWindow.completionMarker), "once", "later transcript polls do not reload the completed checkpoint");
  await page.locator(".chat-activity").filter({ hasText: "Final checkpoint completed" }).getByRole("button", { name: "Open this candidate on the right" }).click();
  assert.equal(await page.locator('iframe:not([hidden])').evaluate((frame) => frame.contentWindow.completionMarker), "once", "clicking the same candidate preserves the iframe");
  completing.status = "idle";
  await page.getByRole("button", { name: "Send", exact: true }).waitFor();
  assert.equal(await page.locator('iframe:not([hidden])').evaluate((frame) => frame.contentWindow.completionMarker), "once", "finishing the chat does not reload the candidate");
  assert.equal(writes.filter(([, pathname]) => pathname.endsWith("/start")).length, beforeReadbackStarts, "showing a candidate in its existing project never starts the app again");

  // 2 — a failure reads as a sentence, keeps its original text for diagnosis,
  // and only offers the model picker when the failure named the model.
  const failed = sessions[0].id;
  const post = (kind) => page.evaluate(([id, body]) => fetch(`/api/chat/sessions/${id}/fail`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }), [failed, { kind }]);
  await post("model");
  const banner = page.locator(".chat-error");
  await banner.waitFor();
  const summary = await banner.locator("p").first().innerText();
  assert.equal(summary, "The current model is not available through Claude Code. Choose another model.");
  assert.ok(!summary.includes("{"), `no JSON body is used as the message: ${summary}`);
  assert.equal(await banner.locator("pre").isVisible(), false, "the technical detail stays folded away");
  await banner.getByText("Technical details").click();
  const technical = await banner.locator("pre").innerText();
  assert.match(technical, /^CHAT_PROVIDER_FAILED:/, technical);
  assert.match(technical, /invalid_request_error/, "the whole original error is kept");
  // An ordinary message that happens to be JSON is still an ordinary message.
  const ordinary = page.locator(".chat-message--assistant").filter({ hasText: "ordinary content" });
  await ordinary.waitFor();
  assert.equal(await ordinary.locator("xpath=ancestor::*[contains(@class,'chat-error')]").count(), 0,
    "JSON-looking content is not handled as a failure");
  // The offer moves to the picker this conversation already has; it switches nothing.
  const modelBefore = sessions.find((row) => row.id === failed).model;
  await banner.getByRole("button", { name: "Change the model" }).click();
  assert.equal(await page.evaluate(() => document.activeElement?.id), "chat-model");
  assert.equal(sessions.find((row) => row.id === failed).model, modelBefore, "nothing is switched for the person");

  await post("unknown");
  await page.waitForFunction(() => document.querySelector(".chat-error pre") === null
    || !document.querySelector(".chat-error pre").textContent.includes("invalid_request_error"));
  const unknownSummary = await banner.locator("p").first().innerText();
  assert.match(unknownSummary, /This step did not finish/, unknownSummary);
  assert.equal(await banner.getByRole("button", { name: "Change the model" }).count(), 0,
    "an unrecognised failure is not blamed on the model");
  await banner.getByText("Technical details").click();
  assert.match(await banner.locator("pre").innerText(), /CHAT_SOMETHING_NEW: \{"trace":"unrecognised"/);
  await page.screenshot({ path: path.join(temporary, "failure.png") });

  // 3 — an embedded page asks this host for the conversation. It is told which
  // origin embedded it, the request arrives, and nothing is sent.
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("embedded=tool"));
  const embeddedSrc = await page.locator("iframe:not([hidden])").getAttribute("src");
  assert.ok(embeddedSrc.includes(`host=${encodeURIComponent(origin)}`), embeddedSrc);
  const sentBefore = writes.filter(([method, pathname]) => pathname.endsWith("/messages")).length;
  await page.frameLocator("iframe:not([hidden])").getByRole("button", { name: "Start modeling in the conversation" }).click();
  await page.waitForFunction(() => document.activeElement?.id === "chat-input");
  const prefilled = await page.locator("#chat-input").inputValue();
  assert.match(prefilled, /Help me start a massing/, prefilled);
  assert.equal(await page.locator("#chat-input").isEditable(), true, "the example is the person's to edit");
  assert.equal(writes.filter(([method, pathname]) => pathname.endsWith("/messages")).length, sentBefore,
    "asking to start modeling sends nothing");
  await page.screenshot({ path: path.join(temporary, "handoff.png") });
  await page.locator("#chat-input").fill("");

  await page.screenshot({ path: path.join(temporary, "desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.locator("#theme").selectOption("dark");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({ path: path.join(temporary, "dark.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Hide projects", exact: true }).first().click();
  await page.screenshot({ path: path.join(temporary, "mobile.png"), fullPage: true });
  assert.ok(await railWidth() > 40, "the rail survives a phone-sized window");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  // A prepared project can be refused by Studio without losing the project
  // or leaving the creation dialog inviting a duplicate creation attempt.
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "Show projects", exact: true }).first().click();
  modelingFailure = { code: "PROJECT_INPUTS_CONFLICT", detail: "Existing inputs need review." };
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  await page.locator("#new-project-name").fill("needs-review");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await page.locator(".chat-error").filter({ hasText: "Existing inputs need review." }).waitFor();
  assert.equal(await create.isVisible(), false);
  assert.equal(projects.filter((item) => item.projectId === "needs-review").length, 1);
  assert.equal(sessions.filter((item) => item.projectId === "needs-review").length, 1);
  modelingFailure = null;
  // A slow workspace opening belongs to the selected chat even when another
  // chat in the same project is chosen before the response arrives.
  let releaseModeling;
  modelingResponseGate = new Promise((resolve) => { releaseModeling = resolve; });
  const delayedStart = page.waitForRequest((req) => req.url().includes("/api/project/modeling"));
  await page.getByRole("button", { name: "Drawings", exact: true }).click();
  await delayedStart;
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  releaseModeling(); modelingResponseGate = Promise.resolve();
  await page.waitForFunction(() => document.querySelector('.chat-composer-note')?.textContent === "");
  assert.equal(await page.locator('iframe[src*="app=monkeydiagram"]').count(), 0, "a late response cannot open the old chat's workspace");
  // Archiving removes only the sidebar entry. The retained chat is readable,
  // survives a page reload and is explicitly restored before it can continue.
  const archivable = sessions.find((row) => row.projectId === "B");
  const activeBeforeArchive = sessions.find((row) => row.projectId === "A");
  activeBeforeArchive.status = "running";
  const archiveRunning = page.getByRole("button", { name: `Archive: ${activeBeforeArchive.title}`, exact: true });
  await archiveRunning.waitFor();
  await page.waitForFunction((title) => [...document.querySelectorAll(".chat-thread-action")].some((button) => button.getAttribute("aria-label") === `Archive: ${title}` && button.disabled), activeBeforeArchive.title);
  assert.equal(await archiveRunning.isDisabled(), true);
  const keptTranscript = JSON.stringify(archivable.messages), archiveWrites = writes.length;
  await page.locator(".chat-thread").filter({ hasText: archivable.title }).click();
  await page.getByRole("heading", { level: 1, name: archivable.title, exact: true }).waitFor();
  await page.getByRole("button", { name: `Archive: ${archivable.title}`, exact: true }).click();
  await page.getByText("This chat is archived. Its messages and candidates are kept. Restore it to continue.", { exact: true }).waitFor();
  await page.waitForFunction((title) => ![...document.querySelectorAll(".chat-thread")].some((node) => node.textContent === title), archivable.title);
  assert.equal(await page.locator("#chat-input").count(), 0, "an archived chat has no send composer");
  assert.equal(JSON.stringify(archivable.messages), keptTranscript);
  assert.equal(activeBeforeArchive.status, "running");
  assert.ok(!writes.slice(archiveWrites).some(([, pathname]) => pathname.endsWith("/stop") || pathname.endsWith("/messages")), "archiving never stops or starts accepted work");
  await page.getByRole("button", { name: "Archived chats", exact: true }).click();
  const archivedEntry = page.locator(".chat-thread").filter({ hasText: archivable.title });
  await archivedEntry.waitFor();
  await archivedEntry.click();
  await page.locator(".chat-message--user").filter({ hasText: archivable.messages[0].content }).waitFor();
  await page.screenshot({ path: path.join(temporary, "archived-chat.png"), fullPage: true });
  await page.reload();
  await page.getByRole("button", { name: "Restore this chat", exact: true }).waitFor();
  assert.equal(JSON.stringify(archivable.messages), keptTranscript);
  await page.getByRole("button", { name: "Archived chats", exact: true }).click();
  await page.getByRole("button", { name: `Restore: ${archivable.title}`, exact: true }).click();
  await page.locator("#chat-input").waitFor();
  await page.locator(".chat-thread").filter({ hasText: archivable.title }).waitFor();
  await page.screenshot({ path: path.join(temporary, "restored-chat.png"), fullPage: true });
  assert.equal(archivable.archived, false);
  assert.equal(JSON.stringify(archivable.messages), keptTranscript);
  assert.equal(activeBeforeArchive.status, "running");
  await page.getByRole("button", { name: "Archived chats", exact: true }).click();
  await page.getByText("No archived chats.", { exact: true }).waitFor();
  // Adding an existing cold project prepares its three workspaces before any
  // page is opened. A later externally stopped Studio invalidates that result.
  const beforeAdd = writes.length;
  await page.getByRole("button", { name: "Add an existing project", exact: true }).click();
  const add = page.getByRole("dialog").filter({ hasText: "Add an existing project" });
  await add.getByLabel("Project folder", { exact: true }).fill("D:\\fixture\\C");
  await add.getByRole("button", { name: "Add project", exact: true }).click();
  await page.getByRole("button", { name: "Project C", exact: true }).first().waitFor();
  await studioReady();
  assert.equal(writes.slice(beforeAdd).filter(([, pathname, , target]) => pathname === "/api/project/modeling" && target === "D:\\fixture\\C").length, 1);
  assert.ok(!writes.slice(beforeAdd).some(([, pathname]) => pathname === "/api/chat/projects" || pathname.endsWith("/start")));
  assert.equal(await page.locator("iframe").count(), 0, "prepared services do not eagerly mount three expensive pages");
  for (const item of appsFor("D:\\fixture\\C").filter(item => item.serviceId === "studio")) { item.state = "stopped"; item.processId = null; }
  await page.waitForFunction(() => document.querySelector('.chat-rail__tool[aria-label="Modeling"]')?.dataset.state === "stopped");
  const beforeReopen = writes.length;
  for (const [label, id] of [["Modeling", "monkeyarch"], ["Drawings", "monkeydiagram"], ["Board", "monkeyboard"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await page.waitForFunction(app => document.querySelector("iframe:not([hidden])")?.src.includes(`app=${app}`), id);
  }
  assert.equal(writes.slice(beforeReopen).filter(([, pathname]) => pathname === "/api/project/modeling").length, 1,
    "an externally stopped Studio is prepared again once and shared by all three workspaces");
  // Creation succeeds independently of a temporarily unavailable CLI. An
  // older in-flight discovery response cannot discard the newly selected project.
  let releaseProjectList;
  projectListGate = new Promise(resolve => { releaseProjectList = resolve; });
  await page.waitForRequest(request => request.method() === "GET" && new URL(request.url()).pathname === "/api/chat/projects");
  chatCreationFailureFor = "D:\\fixture\\provider-recovery";
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  await page.locator("#new-project-name").fill("provider-recovery");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await page.locator(".chat-error").filter({ hasText: "The CLI connection is temporarily unavailable." }).waitFor();
  assert.equal(await create.isVisible(), false);
  releaseProjectList();
  for (let refresh = 0; refresh < 2; refresh++) await page.waitForResponse(response =>
    response.request().method() === "GET" && new URL(response.url()).pathname === "/api/chat/projects");
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "provider-recovery");
  assert.equal(sessions.filter(item => item.projectId === "provider-recovery").length, 0);
  assert.equal(projects.filter(item => item.projectId === "provider-recovery").length, 1);
  chatCreationFailureFor = null;
  await page.getByRole("textbox", { name: "What would you like to do in this project?" }).fill("Continue in the project that was already created");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(sessions.filter(item => item.projectId === "provider-recovery").length, 1);
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/chat/projects" && body.name === "provider-recovery").length, 1,
    "retrying the connection never repeats project creation");
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "provider-recovery").length, 1);
  // With no building project, machine tools remain available and report a
  // missing dependency directly instead of asking the person to bind Studio.
  projects.splice(0); sessions.splice(0); settings.projectDir = null;
  await page.evaluate(() => localStorage.removeItem("monkeyhub.chat-view.v1"));
  const fab = apps.find((item) => item.appId === "monkeyfab");
  Object.assign(fab, { state: "unavailable", available: false, url: null, processId: null,
    error: { code: "FAB_UNAVAILABLE", detail: "MonkeyFab fixture dependency is missing." } });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('.chat-rail__tool[aria-label="Fabrication"]')?.textContent.includes("Unavailable"));
  assert.equal(await page.getByRole("button", { name: "Modeling", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Fabrication", exact: true }).isEnabled(), true);
  const beforeNoProject = writes.length;
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.locator(".chat-error").filter({ hasText: "MonkeyFab fixture dependency is missing." }).waitFor();
  await page.getByRole("button", { name: "Usage", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeymonitor"));
  assert.ok(writes.slice(beforeNoProject).every(([, pathname]) => ["/api/apps/monkeyfab/start", "/api/apps/monkeymonitor/start"].includes(pathname)));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, sessions: sessions.length, writes: writes.length, screenshots: temporary }));
} finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
