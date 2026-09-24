import assert from "node:assert/strict";
import { createProjectWorkspaceFixture } from "./projectWorkspaceFixture.mjs";
import { createServer } from "node:http";
import { readFile, mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

// Real built Hub UI; all provider and project calls are local, synthetic fixtures.
const root = path.resolve(process.env.MONKEYHUB_WEB_DIST ?? fileURLToPath(new URL("../dist/", import.meta.url)));
const temporary = await mkdtemp(path.join(tmpdir(), "monkeyhub-chat-ui-"));
const toolLoads = [];
const streams = new Set();
let runtimeSequence = 0, runtimeReads = 0, allowRuntimeEvents = true;
let confirmedStageForChat = null;
const preparedPatch = { targetVersion: "fixture-next-desktop", targetRevision: "e".repeat(40), changedBytes: 1048576, changedFiles: 3, removedFiles: 1, reusedFiles: 21 };
let updateStatus = { currentVersion: "fixture-current-desktop", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null, canApply: false, message: null, error: null };
let patchPolls = 0, appliedPatches = 0, updateApplyFailure = false;
let updateReadFailures = 0;
let updateApplyResponse = "normal";
const patchUploads = [];
const emitRuntime = () => {
  const event = { serverId: "fixture-hub", sequence: ++runtimeSequence, kind: "changed", snapshot: runtimeSnapshot() };
  for (const stream of streams) stream.write(`event: runtime\ndata: ${JSON.stringify(event)}\n\n`);
};
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, "http://localhost").pathname;
  if (pathname === "/api/runtime/events") {
    if (!allowRuntimeEvents) { res.writeHead(503); res.end(); return; }
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    streams.add(res);
    res.write(`retry: 250\nevent: runtime\ndata: ${JSON.stringify({ serverId: "fixture-hub", sequence: runtimeSequence, kind: "snapshot", snapshot: runtimeSnapshot() })}\n\n`);
    req.on("close", () => streams.delete(res)); return;
  }
  // Chromium's native download bypasses page.route. Serve only the exact
  // synthetic artifact already registered in this addressed runtime fixture.
  const download = /^\/api\/runtime\/projects\/([^/]+)\/studio\/api\/artifacts\/([^/]+)\/bytes$/.exec(pathname);
  if (download) {
    const runtime = [...runtimes.values()].find((item) => item.runtimeId === download[1]);
    const asset = runtime && [...(workspaceFixture.projects.get(runtime.projectId)?.assets.values() ?? [])].find((item) => item.dto.sha256 === download[2]);
    if (!asset) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": asset.bytes.length });
    res.end(asset.bytes); return;
  }
  const chatAttachment = /^\/api\/chat\/sessions\/([^/]+)\/attachments\/([^/]+)$/.exec(pathname);
  if (chatAttachment) {
    const file = uploadedAttachments.get(chatAttachment[2]);
    if (!file || file.sessionId !== chatAttachment[1]) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": file.mimeType, "Content-Disposition": `attachment; filename="${file.name}"` });
    res.end(Buffer.from(file.data, "base64")); return;
  }
  const chatDocument = /^\/api\/chat\/sessions\/([^/]+)\/documents\/([^/]+)\/(\d+)$/.exec(pathname);
  if (chatDocument) {
    const session = sessions.find((item) => item.id === chatDocument[1]);
    const document = session?.messages.find((item) => item.id === chatDocument[2])?.documents?.[Number(chatDocument[3])];
    if (!document) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "Content-Type": document.mimeType, "Content-Disposition": `attachment; filename="${document.fileName}"` });
    res.end(Buffer.from(externalImage, "base64")); return;
  }
  if (pathname === "/tool") {
    toolLoads.push(req.url);
    res.setHeader("Content-Type", "text/html");
    res.end(`<h1>Independent tool fixture</h1>`);
    return;
  }
  const filename = path.resolve(root, `.${pathname === "/" ? "/index.html" : pathname}`);
  if (!filename.startsWith(root)) { res.writeHead(404); res.end(); return; }
  try { res.setHeader("Content-Type", filename.endsWith(".js") ? "text/javascript" : filename.endsWith(".css") ? "text/css" : filename.endsWith(".wasm") ? "application/wasm" : filename.endsWith(".woff2") ? "font/woff2" : "text/html"); res.end(await readFile(filename)); }
  catch { res.writeHead(404); res.end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const { chromium } = await import((process.env.PLAYWRIGHT_MODULE ? pathToFileURL(process.env.PLAYWRIGHT_MODULE).href : "playwright"));
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
page.setDefaultTimeout(12000);
const errors = [], writes = [], sessions = [], providerReads = [];
const monitorReads = [];
const monitorTokens = { input_tokens: 200, cached_input_tokens: 50, output_tokens: 30,
  cache_write_input_tokens: 0, cache_write_1h_input_tokens: 0, reasoning_output_tokens: 0 };
const monitorEvents = [
  ...Array.from({ length: 4 }, (_, i) => ({ event_id: `turn-${i}`, source: "codex", provider: "openai", model: "test",
    phase: "agent_turn", timing_scope: "agent_turn", model_call: null, status: "completed", started_at: "2026-09-20T00:00:00Z",
    tokens: Object.fromEntries(Object.keys(monitorTokens).map((key) => [key, null])) })),
  ...Array.from({ length: 37 }, (_, i) => ({ event_id: `call-${i}`, source: "codex", provider: "openai", model: "test",
    phase: "agent", model_call: true, status: "observed", started_at: "2026-09-20T00:00:00Z", tokens: monitorTokens })),
];
const monitorTrace = { trace_id: "finished-with-missing-end", started_at: "2026-09-20T00:00:00Z", ended_at: "2026-09-20T00:00:02Z",
  status: "succeeded", summary: { elapsed_ms: 2000, first_candidate_ms: null, verified_ms: 1200 }, spans: [
    { span_id: "missing-end", label: "Model request", lane: "model", status: "incomplete", offset_ms: 100, duration_ms: null },
  ] };
const monitorCandidateTrace = { trace_id: "candidate-readback", started_at: "2026-09-20T00:01:00Z", status: "succeeded",
  summary: { elapsed_ms: 80000, first_candidate_ms: 46241, verified_ms: null }, spans: [] };
const monitorLegacyTrace = { trace_id: "legacy-no-candidate-timing", started_at: "2026-09-20T00:02:00Z", status: "succeeded",
  summary: { elapsed_ms: 2000, verified_ms: 1500 }, spans: [] };
let monitorFailure = false, monitorReadLocked = false, monitorReadConflicts = 0, documentLoads = 0;
page.on("request", (request) => {
  if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documentLoads++;
});
let permissionResponseGate = Promise.resolve(), permissionFailure = null;
let modelingResponseGate = Promise.resolve();
let modelingFailure = null;
let chatCreationFailureFor = null;
let chatMessageFailureFor = null;
let chatMessageResponseGate = Promise.resolve();
const uploadedAttachments = new Map();
const externalImage = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB8sAAAAASUVORK5CYII=";
let projectListGate = null;
let runtimeOpenGate = null;
let runtimeOpenCaptured = null;
let runtimeReadGate = null;
let settings = { projectDir: "D:\\fixture\\A", referenceRun: null, cadExport: "off", studioPort: 18789, monitorPort: server.address().port };
// The one saved preferences document: appearance and the new-conversation defaults.
let preferences = { language: "en", theme: "light", fontScale: 1 };
const projects = [
  { projectId: "A", projectDir: "D:\\fixture\\A", name: "Project A", chatCount: 0, version: 3, stage: "S2" },
  { projectId: "B", projectDir: "D:\\fixture\\B", name: "Project B", chatCount: 0, version: 0, stage: null },
];
// What the archive layer always leaves out, in its own words; the Hub carries
// this list through untouched, so the dialogs must show all five lines.
const archiveOmissions = [
  "credentials/tokens",
  "process/runtime state",
  "runtime caches",
  "rebuildable previews and unbounded telemetry/logs",
  "unreferenced exports: exports/ travels only where a retained record names a file in it",
];
const archiveSummaryFor = (projectId, projectDir, archivePath) => ({
  projectId, formatVersion: 3, version: 3, stateSha256: "b".repeat(64), runCount: 7, fileCount: 42,
  retainedBytes: 7340032, categories: { artifact: 18, authored_input: 3, design: 4, envelope: 17 },
  omissions: archiveOmissions, externalDependencies: [],
  archivePath, archiveBytes: 5242880, archiveSha256: "c".repeat(64), verified: true, projectDir,
});
const apps = ["monkeyarch", "monkeyboard", "monkeyrender", "monkeyfab", "monkeymonitor"].map((appId) => ({ appId, title: appId, serviceId: appId === "monkeyfab" ? "hub" : appId === "monkeymonitor" ? "monitor" : "studio", state: "running", processId: 1234, available: true, url: `${origin}/tool?app=${appId}` }));
// Exercise the actual Hub Monitor page, including its navigation and effects.
// A static /tool fixture concealed Monitor's former top-window redirect loop.
Object.assign(apps.find((app) => app.appId === "monkeymonitor"), { url: `${origin}/?view=monitor`, apiUrl: `${origin}/` });
const projectApps = new Map();
const runtimes = new Map();
const workspaceFixture = await createProjectWorkspaceFixture(runtimes, sessions);
const appsFor = (target) => {
  if (!projectApps.has(target)) projectApps.set(target, apps.map((app) => app.serviceId === "studio" ? { ...app, state: "stopped", processId: null, url: null, apiUrl: null } : app));
  return projectApps.get(target);
};
const runtimeSnapshot = () => ({ serverId: "fixture-hub", sequence: runtimeSequence, workers: [], projects: [...runtimes.values()].map((runtime) => {
  const app = appsFor(runtime.projectDir).find((app) => app.appId === "monkeyarch");
  return { ...runtime, workers: app.processId || app.state === "error" ? [{ workerId: runtime.runtimeId, serviceId: "studio", projectId: runtime.projectId,
    projectDir: runtime.projectDir, instanceId: `instance-${app.processId}`, processId: app.processId, desiredState: "running", healthy: app.state === "running",
    state: app.state === "error" ? "crashed" : app.state === "running" ? "ready" : app.state, url: app.apiUrl ?? app.url, error: app.error ?? null }] : [],
    sessions: sessions.filter((session) => session.projectId === runtime.projectId) };
}) });
page.on("pageerror", (error) => errors.push(error.message));
await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
  const req = route.request(), url = new URL(req.url()), method = req.method();
  if (url.pathname === "/api/runtime/events") return route.continue();
  if (/^\/api\/runtime\/projects\/[^/]+\/studio\//.test(url.pathname)) {
    try { if (await workspaceFixture.handle(route, url)) return; }
    catch (error) { errors.push(error.message); return route.fulfill({ status: 500, json: { code: "FIXTURE_UNEXPECTED", detail: error.message } }); }
  }
  const data = () => req.postDataJSON();
  const json = async (body, status = 200) => { await route.fulfill({ json: body, status }); if (method !== "GET") emitRuntime(); };
  if (url.pathname === "/api/updates/status") {
    if (updateReadFailures > 0) { updateReadFailures--; return route.abort("connectionreset"); }
    if (updateStatus.state === "preparing" && ++patchPolls >= 2) updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
    return json(updateStatus);
  }
  if (url.pathname === "/api/updates/prepare") {
    assert.equal(method, "POST");
    assert.equal(req.headers()["content-type"], "application/octet-stream");
    assert.equal(req.headers()["x-monkeyhub-local-patch"], "1");
    patchUploads.push(req.postDataBuffer()); patchPolls = 0;
    updateStatus = { ...updateStatus, state: "preparing", prepared: null, canApply: false, error: null };
    return json(updateStatus, 202);
  }
  if (url.pathname === "/api/updates/apply") {
    assert.equal(method, "POST"); assert.deepEqual(data(), {}); appliedPatches++;
    if (updateApplyFailure) return json({ code: "UPDATE_BUSY", detail: "A task started before restart. Wait and retry." }, 409);
    if (updateApplyResponse === "not-received") return route.abort("connectionreset");
    updateStatus = { ...updateStatus, state: "applying", canApply: false };
    if (updateApplyResponse !== "normal") {
      // The backend accepted the restart, but neither its reply nor the first
      // status read reaches the page. Admission must never be replayed.
      updateReadFailures = 1;
      if (updateApplyResponse === "lost") return route.abort("connectionreset");
      return route.fulfill({ status: 202, contentType: "application/json", body: "{" });
    }
    return json(updateStatus);
  }
  if (method !== "GET") writes.push([method, url.pathname, data(), url.searchParams.get("projectDir")]);
  if (["/api/events", "/api/traces", "/api/rates", "/api/sources/codex"].includes(url.pathname)) {
    monitorReads.push(url.pathname);
    if (url.pathname === "/api/events" && monitorFailure) return json({ detail: "Monitor fixture is temporarily unavailable." }, 500);
    if (["/api/events", "/api/traces"].includes(url.pathname)) {
      // Both diagnostics share Monitor's non-concurrent store read. Parallel
      // requests reproduce the live 503; unrelated rates/sources can overlap.
      if (monitorReadLocked) {
        monitorReadConflicts++;
        return json({ detail: "Monitor fixture diagnostics read is already locked." }, 503);
      }
      monitorReadLocked = true;
      try {
        await new Promise((resolve) => setTimeout(resolve, 40));
        return await json(url.pathname === "/api/events" ? { events: monitorEvents, warnings: [] } : { traces: [monitorTrace, monitorCandidateTrace, monitorLegacyTrace], warnings: [] });
      } finally { monitorReadLocked = false; }
    }
    if (url.pathname === "/api/rates") return json({ rates: [] });
    return json({ paths: ["D:\\fixture\\usage.jsonl"] });
  }
  if (url.pathname === "/api/settings/apps") { if (method === "PUT") settings = data(); return json(settings); }
  if (url.pathname === "/api/settings/user") { if (method === "PUT") preferences = data(); return json(preferences); }
  if (url.pathname === "/api/apps") return json(url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : apps);
  if (url.pathname === "/api/runtime") { runtimeReads++; if (runtimeReadGate) await runtimeReadGate; return json(runtimeSnapshot()); }
  if (url.pathname === "/api/runtime/projects/open") {
    const body = data(), project = projects.find((item) => item.projectDir === body.projectDir && item.projectId === body.projectId);
    assert.ok(project, "runtime attachment needs the exact project");
    if (!runtimes.has(body.projectDir)) runtimes.set(body.projectDir, { runtimeId: randomUUID(), ...body, state: "open", operations: [], retained: null, projection: project.projectId === "needs-review" ? "unknown" : "ready", clients: 1, error: null });
    if (runtimeOpenGate) {
      const reply = structuredClone(runtimeSnapshot().projects.find((item) => item.projectDir === body.projectDir)), gate = runtimeOpenGate;
      runtimeOpenGate = null;
      runtimeOpenCaptured?.(); runtimeOpenCaptured = null;
      await gate;
      // Reattaching an already-open runtime emits no state change.
      return route.fulfill({ json: reply });
    }
    return json(runtimeSnapshot().projects.find((item) => item.projectDir === body.projectDir));
  }
  if (/^\/api\/runtime\/projects\/[^/]+\/recover$/.test(url.pathname)) {
    const runtime = [...runtimes.values()].find((item) => url.pathname.includes(item.runtimeId));
    assert.equal(data().projectId, runtime.projectId);
    for (const app of appsFor(runtime.projectDir).filter((app) => app.serviceId === "studio")) {
      assert.equal(app.state, "error", "only a crashed worker is recovered");
      app.state = "running"; app.processId += 1000; app.error = null;
    }
    return json(runtimeSnapshot().projects.find((item) => item.runtimeId === runtime.runtimeId));
  }
  if (url.pathname === "/api/project/modeling") {
    const selected = projects.find((item) => item.projectDir === url.searchParams.get("projectDir"));
    assert.equal(data().projectId, selected?.projectId);
    const currentApps = appsFor(selected.projectDir).filter((item) => item.serviceId === "studio");
    for (const item of currentApps) item.state = "starting";
    await modelingResponseGate;
    for (const item of currentApps) {
      item.state = "running"; item.processId = 2000 + [...projectApps.keys()].indexOf(selected.projectDir);
      const runtimeId = runtimes.get(selected.projectDir).runtimeId;
      item.url = `${origin}/?view=${item.appId === "monkeyboard" ? "board" : "arch"}&runtimeId=${runtimeId}`;
      item.apiUrl = `${origin}/api/runtime/projects/${runtimeId}/studio/`;
    }
    runtimes.get(selected.projectDir).projection = modelingFailure ? "unknown" : "ready";
    return modelingFailure ? json(modelingFailure, 409) : json({ projectId: selected.projectId, initialized: true });
  }
  if (url.pathname === "/api/project/archive/export") {
    const body = data(), selected = projects.find((item) => item.projectDir === body.projectDir);
    assert.ok(selected, "an archive is only written for a project this Hub lists");
    return json(archiveSummaryFor(selected.projectId, selected.projectDir, body.archivePath), 201);
  }
  if (url.pathname === "/api/project/archive/restore") {
    const body = data();
    const parent = body.targetParent ?? "D:\\fixture";
    const restored = { projectId: "restored-demo", projectDir: [parent, "restored-demo"].join("\\"),
      name: "restored-demo", chatCount: 0, version: 3, stage: "S2" };
    projects.push(restored);
    return json({ summary: archiveSummaryFor(restored.projectId, restored.projectDir, body.archivePath), project: restored }, 201);
  }
  if (url.pathname.startsWith("/api/apps/")) {
    const [, , , id, action] = url.pathname.split("/");
    const currentApps = url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : apps;
    const app = currentApps.find((item) => item.appId === id);
    if (!app.available) return json(app);
    for (const item of currentApps.filter((item) => item.serviceId === app.serviceId)) {
      item.state = action === "start" ? "running" : "stopped";
      item.processId = action === "start" ? (app.serviceId === "studio" ? 2000 + [...projectApps.keys()].indexOf(url.searchParams.get("projectDir")) : 1234) : null;
      if (app.serviceId === "studio") {
        const runtimeId = runtimes.get(url.searchParams.get("projectDir")).runtimeId;
        item.url = `${origin}/?view=${item.appId === "monkeyboard" ? "board" : item.appId === "monkeyrender" ? "render" : "arch"}&runtimeId=${runtimeId}`;
        item.apiUrl = `${origin}/api/runtime/projects/${runtimeId}/studio/`;
      }
    }
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
  const attachmentMatch = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/attachments\/([^/]+)$/);
  if (attachmentMatch) {
    const file = uploadedAttachments.get(attachmentMatch[2]);
    assert.equal(file?.sessionId, attachmentMatch[1]);
    return route.fulfill({ body: Buffer.from(file.data, "base64"), contentType: file.mimeType,
      headers: { "Content-Disposition": `${url.searchParams.get("inline") === "true" ? "inline" : "attachment"}; filename="${file.name}"` } });
  }
  const documentMatch = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/documents\/([^/]+)\/(\d+)$/);
  if (documentMatch) {
    const session = sessions.find((item) => item.id === documentMatch[1]);
    const document = session?.messages.find((item) => item.id === documentMatch[2])?.documents?.[Number(documentMatch[3])];
    assert.ok(document, "only a document retained on this chat message can be displayed");
    return route.fulfill({ body: Buffer.from(externalImage, "base64"), contentType: document.mimeType });
  }
  const match = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)(?:\/(messages|stop|model|archive|fail))?$/);
  if (match) {
    const session = sessions.find((item) => item.id === match[1]);
    if (!session) return json({ code: "CHAT_NOT_FOUND", detail: "This conversation is no longer available." }, 404);
    if (match[2] === "messages") {
      assert.equal(Boolean(session.archived), false, "archived chats must be restored before sending");
      assert.equal(data().projectId, session.projectId);
      assert.equal(appsFor(session.projectDir).find((item) => item.appId === "monkeyarch").state, "running");
      if (session.projectDir === chatMessageFailureFor) return json({ code: "CHAT_SEND_FAILED", detail: "Fixture upload failed. Try again." }, 503);
      await chatMessageResponseGate;
      const attachments = (data().attachments ?? []).map((file, index) => {
        const id = `attachment-${session.id}-${session.messages.length}-${index}`;
        uploadedAttachments.set(id, { ...file, sessionId: session.id });
        return { id, name: file.name, mimeType: file.mimeType, size: Buffer.from(file.data, "base64").length };
      });
      const stageHandoff = data().contextMode === "stage" && confirmedStageForChat
        && data().designContext?.sourceRunId === confirmedStageForChat.runId;
      session.messages.push({ id: `u-${session.messages.length}`, role: "user", content: data().content, status: "complete", attachments,
        contextMode: stageHandoff ? "stage" : data().contextMode === "project" ? "project" : "continue",
        ...(stageHandoff ? { confirmedStageRef: confirmedStageForChat.stageRef, confirmedStageLabel: confirmedStageForChat.label } : {}) });
      session.title = session.messages[0].content || session.messages[0].attachments?.[0]?.name; session.status = "running";
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
const visibleWorkspace = () => page.locator('.chat-project-workspace:not([hidden])');
const waitWorkspace = async (kind = "arch") => {
  await visibleWorkspace().locator(`[data-project-surface="${kind}"]:not([hidden])`).waitFor();
  await visibleWorkspace().locator(kind === "board" ? ".monkeyboard-canvas canvas" : kind === "drawing" ? ".drawing-workspace" : kind === "render" ? ".render-workspace" : ".stage canvas").first().waitFor();
  assert.equal(await page.locator(".chat-project-workspace iframe").count(), 0, "project workspaces mount directly in the Hub");
};
const waitCandidate = async (runId) => {
  await waitWorkspace();
  await page.waitForFunction(() => !document.querySelector('.chat-project-workspace:not([hidden]) .boot'));
  const until = Date.now() + 15000;
  while (!workspaceFixture.requests.some((row) => row.name.endsWith("/bytes") && row.runId === runId)) {
    if (Date.now() > until) assert.fail(`Candidate model not read: ${runId}; ${JSON.stringify(workspaceFixture.requests.slice(-12))}`);
    await page.waitForTimeout(50);
  }
  await visibleWorkspace().locator(".boot").waitFor({ state: "hidden" });
  // Read the actual viewer selection, not only a completed model download.
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await visibleWorkspace().locator('.vcard__export[aria-pressed="true"]').filter({ hasText: `${runId}.3dm` }).waitFor();
  if (/^cand-[AB]-/.test(runId)) {
    assert.equal((await visibleWorkspace().locator(".editing-base__name").innerText()).trim(), `home-${runId.split("-")[1]}.3dm`,
      "automatic candidate viewing preserves the exact editing base");
    assert.equal(await visibleWorkspace().locator('.editing-base').getAttribute("data-source-match"), "different");
  }
  if (runId === "cand-A-1") {
    const editable = workspaceFixture.projects.get("A").assets.get("home-A");
    const save = visibleWorkspace().locator("[data-work-model-save]").first();
    assert.equal(await save.getAttribute("href"), `${origin}/api/runtime/projects/${runtimes.get("D:\\fixture\\A").runtimeId}/studio/api/artifacts/${editable.dto.sha256}/bytes`);
    const downloadReady = page.waitForEvent("download");
    await save.click();
    const downloaded = await downloadReady;
    assert.equal(downloaded.suggestedFilename(), "editable-A.3dm");
    assert.deepEqual(await readFile(await downloaded.path()), editable.bytes, "version download returns the addressed project's bytes");
    await page.screenshot({ path: path.join(temporary, "hub-versions.png") });
  }
  await visibleWorkspace().locator(".stage__versions-toggle").click();
};
const studioReady = () => page.waitForFunction(() => ["Modeling", "Board"].every(label =>
  document.querySelector(`.chat-rail__tool[aria-label="${label}"]`)?.dataset.state === "running"));
const waitMonitor = async () => {
  await page.locator(".chat-browser .monitor-page").getByRole("heading", { name: "Usage and task records" }).waitFor();
  await page.locator(".chat-browser .monitor-health").getByText("Monitoring service online", { exact: true }).waitFor();
  assert.equal(await page.locator('.chat-browser .monitor-page [role="alert"]').count(), 0,
    "the Monitor's actual data reads completed without a service error");
  assert.equal(monitorReadConflicts, 0, "events and traces do not compete for the shared diagnostics read lock");
  assert.equal(await page.locator('iframe[src*="view=monitor"], iframe[src*="app=monkeymonitor"]').count(), 0,
    "Monitor mounts in the Hub panel without a second application document");
  assert.equal(await page.getByRole("navigation", { name: "Project tools" }).isVisible(), true);
};
try {
  if (process.env.MONKEYHUB_UI_FOCUS !== "updates") {
  await page.goto(origin);
  await page.getByRole("heading", { name: "Start a project conversation" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Enter workspace" }).count(), 0);

  // A — one vertical rail on the far right holds every tool entry, and there
  // is no second copy of them anywhere.
  const rail = page.getByRole("navigation", { name: "Project tools" });
  await rail.waitFor();
  for (const label of ["Modeling", "Drawings", "Board", "Fabrication", "Usage"]) {
    assert.equal(await page.getByRole("button", { name: label, exact: true }).count(), 1, `${label} appears once`);
  }
  assert.ok(await railWidth() > 40, "the rail stays on screen while the tool content is closed");
  assert.equal(await page.locator(".chat-browser:visible").count(), 0);
  await page.waitForFunction(() => !document.querySelector('.chat-composer input[type="checkbox"]')?.disabled);
  assert.equal(await page.locator(".stage canvas").count(), 0, "reading initial project context does not initialize a hidden viewport");
  assert.equal(workspaceFixture.requests.some((row) => row.name.endsWith("/bytes")), false,
    "initial chat reads its editing state without loading model files");
  await studioReady();
  assert.equal(writes.filter(([, pathname, , target]) => pathname === "/api/project/modeling" && target === "D:\\fixture\\A").length, 0,
    "selecting a project starts its Runtime without seeding modeling");
  assert.ok(!writes.some(([, pathname]) => /\/api\/apps\/monkey(arch|diagram|board)\/start$/.test(pathname)),
    "project preparation starts the shared Runtime through Render without seeding Arch");

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
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "harbour-study").length, 0);
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeyfab"));
  assert.ok(!writes.some(([, pathname]) => pathname === "/api/project/modeling"),
    "new-project creation and independent tools leave modeling inputs alone");
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  const beforeIndependent = writes.length;
  const beforeIndependentProject = settings.projectDir;
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeyfab"));
  const beforeMonitorNavigation = documentLoads;
  const composer = page.getByRole("textbox", { name: "What would you like to do in this project?" });
  await composer.fill("Keep this conversation while viewing usage");
  await page.getByRole("button", { name: "Usage", exact: true }).click();
  await waitMonitor();
  const callCard = page.locator(".monitor-stat").filter({ has: page.getByText("Model calls", { exact: true }) }).locator("strong");
  assert.equal(await callCard.innerText(), "37", "four Codex task boundaries are not model calls");
  await page.getByText("Showing 20 of 37 records", { exact: true }).waitFor();
  assert.equal(await page.locator(".monitor-table tbody tr").count(), 20);
  const displayedTokens = page.locator(".monitor-table tfoot tr").filter({ hasText: "Displayed model-call subtotal" }).locator("td").first();
  const allTokens = page.locator(".monitor-table tfoot tr").filter({ hasText: "All model-call totals" }).locator("td").first();
  assert.match(await displayedTokens.innerText(), /^1,000\s+20 recorded$/);
  assert.match(await allTokens.innerText(), /^1,850\s+37 recorded$/);
  assert.equal(await page.getByLabel("Total input", { exact: true }).inputValue(), "7400", "task rows do not erase the estimate's known counters");
  await page.getByRole("button", { name: "Show 20 more", exact: true }).click();
  await page.getByText("Showing 37 of 37 records", { exact: true }).waitFor();
  assert.equal(await displayedTokens.innerText(), await allTokens.innerText(), "all visible model rows sum to the dashboard total");
  await page.getByLabel("Include tools and local operations").check();
  await page.getByText("Showing 20 of 41 records", { exact: true }).waitFor();
  assert.match(await displayedTokens.innerText(), /^800\s+16 recorded$/);
  assert.equal(await callCard.innerText(), "37", "expanding diagnostics does not change model-call totals");
  await page.getByLabel("Include tools and local operations").uncheck();
  const firstCandidateCard = page.locator(".monitor-trace-summary > span").filter({ has: page.getByText("First candidate", { exact: true }) });
  assert.equal(await firstCandidateCard.locator("strong").innerText(), "—", "missing candidate readback timing must not fall back to verification time");
  await page.locator(".monitor-section__head select").selectOption(monitorCandidateTrace.trace_id);
  assert.equal(await firstCandidateCard.locator("strong").innerText(), "46 s", "first candidate comes from the retained readback metric without requiring verification");
  await page.locator(".monitor-section__head select").selectOption(monitorLegacyTrace.trace_id);
  assert.equal(await firstCandidateCard.locator("strong").innerText(), "—", "older traces without the candidate field remain unknown");
  await page.locator(".monitor-section__head select").selectOption(monitorTrace.trace_id);
  const missingEndSpan = page.locator(".monitor-span").filter({ hasText: "Model request" });
  await missingEndSpan.locator("summary").getByText("Model request · End not observed", { exact: true }).waitFor();
  assert.equal(await missingEndSpan.locator("summary > span").last().innerText(), "—", "an unclosed span has unknown duration, not zero or a live timer");
  await missingEndSpan.locator("summary").click();
  assert.equal(await missingEndSpan.locator("dd").first().innerText(), "End not observed");
  await page.screenshot({ path: path.join(temporary, "monitor-panel.png") });
  assert.equal(await composer.inputValue(), "Keep this conversation while viewing usage");
  assert.equal(documentLoads, beforeMonitorNavigation, "opening Monitor keeps the current Hub document and conversation");
  assert.equal(settings.projectDir, beforeIndependentProject, "independent tools leave the shared Studio on its existing project");
  assert.ok(writes.slice(beforeIndependent).every(([, pathname]) => ["/api/apps/monkeyfab/start", "/api/apps/monkeymonitor/start"].includes(pathname)),
    "independent tools neither stop Studio nor rewrite its project configuration");
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.locator("#language").selectOption("zh-CN");
  assert.equal(await missingEndSpan.locator("dd").first().textContent(), "结束时间未观测");
  assert.equal(await page.locator(".monitor-trace-summary > span").filter({ has: page.getByText("首个候选", { exact: true }) }).locator("strong").textContent(), "—");
  await page.locator("#language").selectOption("en");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  // Failed data reads stay visible and do not drive a base/effect retry loop.
  monitorFailure = true;
  const beforeFailedRead = monitorReads.filter((route) => route === "/api/events").length;
  await page.locator(".monitor-page").getByRole("button", { name: "Refresh", exact: true }).click();
  await page.locator(".monitor-page").getByRole("alert").filter({ hasText: "Monitor fixture is temporarily unavailable." }).waitFor();
  await page.waitForTimeout(750);
  assert.ok(monitorReads.filter((route) => route === "/api/events").length - beforeFailedRead <= 2,
    "a failed Monitor read does not immediately restart itself through effect dependencies");
  monitorFailure = false;
  await page.locator(".monitor-page").getByRole("button", { name: "Reconnect", exact: true }).click();
  await waitMonitor();
  // A hidden mounted Monitor must not poll or take top-level navigation back.
  await page.getByRole("button", { name: "Hide tools" }).click();
  await page.locator(".monitor-page").waitFor({ state: "hidden" });
  const hiddenMonitorReads = monitorReads.length;
  await page.waitForTimeout(5200);
  assert.equal(monitorReads.length, hiddenMonitorReads, "Monitor suspends its five-second polling while the panel is hidden");
  assert.equal(documentLoads, beforeMonitorNavigation, "hiding Monitor cannot reload or redirect the Hub");
  await page.getByRole("button", { name: "Show tools" }).click();
  await waitMonitor();
  assert.equal(await page.locator(".monitor-page").getByRole("button", { name: "Back to chat", exact: true }).count(), 0);
  await page.getByRole("button", { name: "Hide tools" }).click();
  await page.locator(".monitor-page").waitFor({ state: "hidden" });
  assert.equal(await composer.inputValue(), "Keep this conversation while viewing usage",
    "returning from Monitor preserves the existing conversation draft");
  assert.equal(documentLoads, beforeMonitorNavigation, "closing the tool panel does not reload the Hub into a restored Monitor tab");
  await page.getByRole("button", { name: "Usage", exact: true }).click();
  await waitMonitor();
  for (const [label, workspace] of [["Board", "board"], ["Modeling", "arch"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await waitWorkspace(workspace);
    assert.equal(await page.locator(".monitor-page").isVisible(), false);
    assert.equal(documentLoads, beforeMonitorNavigation, "switching from Monitor to a project workspace preserves the Hub document");
  }
  await page.screenshot({ path: path.join(temporary, "new-project.png") });
  // Back to the first project's conversation for the rest of this walk.
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();

  await page.getByRole("textbox", { name: "What would you like to do in this project?" }).fill("Widen the courtyard");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(sessions.length, 2);
  const firstContext = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1)[2].designContext;
  assert.equal(firstContext.sourceRunId, "home-A", "the first task is bound before the architect opens any modeling workspace");
  assert.equal(firstContext.stateDigest, workspaceFixture.projects.get("A").assets.get("home-A").dto.designStateDigest);
  assert.ok(!writes.some(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "A"),
    "an already readable modeling base needs no initialization write");
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "A").length, 0,
    "returning and sending a message do not initialize an existing modeling base");
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
  emitRuntime();
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
  emitRuntime();
  await choices.waitFor();
  await choices.getByRole("button", { name: "Cancel", exact: true }).click();
  await choices.waitFor({ state: "hidden" });
  assert.deepEqual(permissionWrites().at(-1)[2], { projectId: "A", optionId: null });

  pendingActivity.permission = { ...permission, id: "permission-expired" };
  permissionFailure = 409;
  emitRuntime();
  await choices.waitFor();
  await choices.getByRole("button", { name: "Reject", exact: true }).click();
  const permissionError = page.getByRole("alert").filter({ hasText: "CHAT_PERMISSION_EXPIRED" });
  await permissionError.waitFor();
  assert.equal(await choices.getByRole("button", { name: "Reject", exact: true }).isEnabled(), true);
  await permissionError.getByRole("button", { name: "Close", exact: true }).click();
  pendingActivity.permission = null; permissionFailure = null;
  emitRuntime();
  await choices.waitFor({ state: "hidden" });

  // The candidate that step produced opens beside the conversation, which the
  // panel keeps as it was.
  await page.getByRole("button", { name: "Open this candidate on the right" }).first().click();
  await waitCandidate("cand-A-1");
  const originalPanelWidth = Number(await page.locator('.chat-resizer').getAttribute('aria-valuenow'));
  await page.setViewportSize({ width: 1920, height: 960 });
  const resizePanel = async (width) => {
    const separator = page.locator('.chat-resizer');
    for (let step = 0; step < 32; step++) {
      const current = Number(await separator.getAttribute('aria-valuenow'));
      if (Math.abs(current - width) < 16) break;
      await separator.press(current < width ? 'ArrowLeft' : 'ArrowRight');
    }
    await page.evaluate(() => new Promise(requestAnimationFrame));
  };
  for (const width of [940, 620, 332]) {
    await resizePanel(width);
    const layout = await visibleWorkspace().locator('.model-tools').evaluate(node => {
      const box = node.getBoundingClientRect();
      return { width: box.width, height: box.height, overflow: [...node.children].some(child => { const rect=child.getBoundingClientRect(); return rect.x < box.x - 1 || rect.right > box.right + 1; }),
        groups: [...node.children].map(group => ({ height: group.getBoundingClientRect().height, overflow: [...group.children].some(child => { const box=child.getBoundingClientRect(), parent=group.getBoundingClientRect(); return box.width > 0 && (box.x < parent.x - 1 || box.right > parent.right + 1); }) })) };
    });
    console.log(JSON.stringify({ toolbar: width, layout }));
    assert.ok(!layout.overflow && layout.groups.every(group => !group.overflow), 'toolbar fits the project pane');
    assert.ok(layout.groups.every(group => group.height <= 50), 'each toolbar group stays on one line');
    if (width === 940) assert.ok(layout.height <= 52, 'wide toolbar is one strip');
    await visibleWorkspace().locator('.stage').screenshot({ path: path.join(temporary, `toolbar-${width}.png`) });
  }
  await resizePanel(originalPanelWidth);
  await page.setViewportSize({ width: 1440, height: 960 });
  const savedEditingBases = await page.evaluate(() => localStorage.getItem("archflow-studio.user-preferences"));
  assert.ok(!savedEditingBases?.includes("cand-A-1"), "viewing a candidate does not save it as an editing choice");
  await visibleWorkspace().evaluate((element) => { element.switchMarker = "retained"; element.retainedCanvas = element.querySelector(".stage canvas"); });
  await visibleWorkspace().locator('button[aria-controls="view-tools"]').click();
  await visibleWorkspace().locator("#view-tools").getByRole("button", { name: "Top", exact: true }).click();
  await visibleWorkspace().locator('button[aria-controls="view-tools"]').click();
  await page.mouse.move(10, 10);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const preservedView = await visibleWorkspace().locator(".stage canvas").first().screenshot();
  const beforePeerWorkspaces = writes.length;
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  assert.equal(await visibleWorkspace().locator('.monkeyboard-heading input').count(), 1);
  assert.equal(await visibleWorkspace().locator('.monkeyboard-brand').count(), 0);
  const upload = visibleWorkspace().locator('.monkeyboard-welcome-upload');
  await upload.waitFor();
  const alignment = await upload.evaluate(node => {
    const button = node.getBoundingClientRect(), center = node.closest('.welcome-screen-center').getBoundingClientRect();
    return Math.abs(button.x + button.width / 2 - center.x - center.width / 2);
  });
  assert.ok(alignment <= 2, `welcome upload centered: ${alignment}`);
  const chooser = page.waitForEvent('filechooser');
  await upload.click(); await chooser;
  await visibleWorkspace().locator('.monkeyboard').screenshot({ path: path.join(temporary, 'board-welcome.png') });
  await visibleWorkspace().getByLabel("Board title", { exact: true }).fill("Board A retained");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  for (const [label, kind] of [["Drawings", "drawing"], ["Board", "board"], ["Modeling", "arch"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await waitWorkspace(kind);
    if (kind === "board") assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board A retained");
    if (kind === "drawing") {
      assert.equal(new URL(page.url()).searchParams.get("view"), "drawing");
      assert.equal(await visibleWorkspace().locator('[data-project-surface="arch"]').isVisible(), false);
      assert.equal(await visibleWorkspace().locator('[data-project-surface="board"]').isVisible(), false);
    }
  }
  assert.equal(writes.length, beforePeerWorkspaces, "workspace switches never restart or prepare the project");
  assert.equal(await visibleWorkspace().evaluate((element) => element.retainedCanvas === element.querySelector(".stage canvas")), true);
  await page.mouse.move(10, 10);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.deepEqual(await visibleWorkspace().locator(".stage canvas").first().screenshot(), preservedView, "the chosen camera view survives Board/model switches");
  await page.screenshot({ path: path.join(temporary, "hub-arch.png") });
  assert.equal(await visibleWorkspace().evaluate((element) => element.switchMarker), "retained", "both workspaces share one mounted project");
  assert.equal(await page.evaluate(() => localStorage.getItem("archflow-studio.user-preferences")), savedEditingBases, "candidate readback and workspace switches do not change editing consent");
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
  assert.match(await connection.locator("#tool-url").inputValue(), /runtimeId=/);
  await connection.getByRole("button", { name: "Reload page" }).click();
  await connection.getByRole("button", { name: "Close" }).click();
  await waitWorkspace();

  // A — dragging really moves the boundary: the conversation and the tool page
  // both change width, and the conversation keeps its floor.
  const before = { chat: await boxOf(".chat-main"), frame: await boxOf(".chat-project-workspace:not([hidden])") };
  const handle = page.getByRole("separator", { name: "Resize right panel" });
  const bounds = await handle.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x - 260, bounds.y + bounds.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForFunction((width) => document.querySelector(".chat-project-workspace:not([hidden])").getBoundingClientRect().width > width + 100, before.frame);
  const wider = { chat: await boxOf(".chat-main"), frame: await boxOf(".chat-project-workspace:not([hidden])") };
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
  await waitWorkspace();
  const mountedProjects = await page.locator(".chat-project-workspace").count();
  await visibleWorkspace().evaluate((element) => { element.collapseMarker = "retained"; });
  await page.getByRole("button", { name: "Hide tools" }).click();
  await page.locator(".chat-browser").waitFor({ state: "hidden" });
  assert.equal(await page.locator(".chat-project-workspace").count(), mountedProjects, "collapsing retains project components");
  assert.ok(await railWidth() > 40);
  await page.getByRole("button", { name: "Show tools" }).click();
  await waitWorkspace();
  assert.equal(await visibleWorkspace().evaluate((element) => element.collapseMarker), "retained");

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
  await page.locator("#render-provider").selectOption("gemini");
  await page.locator("#render-model").fill("gemini-3.1-flash-image");
  await page.locator("#render-timeout").fill("75");
  assert.equal(await page.locator('input[type="password"]').count(), 0, "render credentials never enter settings UI");
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
  assert.equal(savedPreferences[2].renderProvider, "gemini");
  assert.equal(savedPreferences[2].renderModel, "gemini-3.1-flash-image");
  assert.equal(savedPreferences[2].renderTimeoutS, 75);

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
  assert.equal(writes.slice(beforeSwitchWrites).filter(([, pathname, , target]) => pathname === "/api/apps/monkeyrender/start" && target === "D:\\fixture\\B").length, 1);

  // C — the gear follows the conversation's project rather than keeping the old one.
  await page.getByRole("button", { name: /Project B/ }).last().click();
  const second = page.getByRole("dialog", { name: "Project" });
  await second.getByText("D:\\fixture\\B").waitFor();
  await second.getByText("Version 0").waitFor();
  await second.getByText("No confirmed Stage").waitFor();
  assert.equal(await second.getByText("D:\\fixture\\A").count(), 0, "no stale project is left in the card");
  await second.getByRole("button", { name: "Close" }).click();

  // Two mounted projects keep separate Board state and project API clients.
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B");
  await visibleWorkspace().getByLabel("Board title", { exact: true }).fill("Board B retained");
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board A retained");
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B retained");
  assert.equal(runningA.status, "running", "Board navigation does not interrupt another project's task");
  const modelReadsBeforeRender = workspaceFixture.requests.filter((row) => /\/api\/artifacts/.test(row.name)).length;
  await page.getByRole("button", { name: "Render", exact: true }).click(); await waitWorkspace("render");
  await visibleWorkspace().getByRole("textbox", { name: "Visual direction", exact: true }).fill("Keep this Render draft");
  await page.getByRole("button", { name: "Board", exact: true }).click(); await waitWorkspace("board");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B retained");
  await page.getByRole("button", { name: "Render", exact: true }).click(); await waitWorkspace("render");
  assert.equal(await visibleWorkspace().getByRole("textbox", { name: "Visual direction", exact: true }).inputValue(), "Keep this Render draft");
  assert.equal(workspaceFixture.requests.filter((row) => /\/api\/artifacts/.test(row.name)).length, modelReadsBeforeRender, "Render and Board switches keep the loaded model");
  assert.equal(await page.locator('.chat-project-workspace:not([hidden])').count(), 1);
  await page.getByRole("button", { name: "Board", exact: true }).click(); await waitWorkspace("board");
  assert.deepEqual(new Set(workspaceFixture.requests.filter((row) => row.name === "/api/board").map((row) => row.projectId)), new Set(["A", "B"]));
  await page.screenshot({ path: path.join(temporary, "hub-board.png") });
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();

  // A successful candidate opens immediately while the same turn continues
  // working. Neither repeated clicks nor terminal-state polling reload it.
  const completing = sessions[0];
  const beforeReadbackStarts = writes.filter(([, pathname]) => pathname.endsWith("/start")).length;
  completing.messages.push({ id: "final-checkpoint", role: "tool", status: "complete", candidateId: "cand-B-final", content: "Final checkpoint completed" });
  emitRuntime();
  assert.equal(completing.status, "running");
  await waitCandidate("cand-B-final");
  await waitWorkspace();
  await visibleWorkspace().evaluate((element) => { element.completionMarker = "once"; });
  await page.waitForTimeout(1600);
  assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once", "later transcript polls do not reload the completed checkpoint");
  await page.locator(".chat-activity").filter({ hasText: "Final checkpoint completed" }).getByRole("button", { name: "Open this candidate on the right" }).click();
  assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once", "clicking the same candidate preserves the mounted model");
  completing.status = "idle";
  emitRuntime();
  await page.getByRole("button", { name: "Send", exact: true }).waitFor();
  assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once", "finishing the chat does not reload the candidate");
  assert.equal(writes.filter(([, pathname]) => pathname.endsWith("/start")).length, beforeReadbackStarts, "showing a candidate in its existing project never starts the app again");

  // Refreshing a completed readback preserves the user's later camera view.
  await visibleWorkspace().locator('button[aria-controls="view-tools"]').click();
  await visibleWorkspace().locator("#view-tools").getByRole("button", { name: "Top", exact: true }).click();
  await visibleWorkspace().locator('button[aria-controls="view-tools"]').click();
  await page.mouse.move(10, 10);
  const beforeRefreshCanvas = await visibleWorkspace().locator(".stage canvas").first().screenshot();
  const beforeRefreshBytes = workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length;
  await page.getByRole("button", { name: /Project B/ }).last().click();
  const refreshConnection = page.getByRole("dialog", { name: "Project", exact: true });
  await refreshConnection.getByText("Connection details").click();
  const refreshedListing = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/studio/api/artifacts"));
  await refreshConnection.getByRole("button", { name: "Reload page", exact: true }).click();
  await refreshedListing;
  await refreshConnection.getByRole("button", { name: "Close", exact: true }).click();
  await page.mouse.move(10, 10);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once");
  assert.equal(workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length, beforeRefreshBytes,
    "refreshing an already shown candidate does not reinstall its model");
  assert.deepEqual(await visibleWorkspace().locator(".stage canvas").first().screenshot(), beforeRefreshCanvas,
    "refreshing keeps the camera chosen after the candidate appeared");

  // Headless API jobs have no chat message. Their completed results update the
  // mounted project's candidate without taking the architect out of the Board.
  const runtimeB = runtimes.get("D:\\fixture\\B");
  const headlessJob = (candidateId, minute, status = "succeeded") => ({ jobId: `job-${candidateId}`, candidateId,
    proposalId: `proposal-${candidateId}`, status, createdAt: `2026-09-20T01:${String(minute).padStart(2, "0")}:00Z` });
  const headlessOperation = (job, overrides = {}) => ({ operationId: `operation-${job.candidateId}`, projectId: "B",
    kind: "POST /api/proposals/fixture/candidate", source: "studio", sessionId: null, committed: false,
    status: job.status === "succeeded" ? "completed" : "executing", candidateId: job.candidateId,
    jobId: job.jobId, resultDigest: "d".repeat(64), admissionSequence: Number(job.createdAt.slice(14, 16)), ...overrides });
  const headlessCandidate = (job, overrides = {}) => ({ candidateId: job.candidateId,
    status: job.status === "succeeded" ? "completed" : job.status, resultStateDigest: "d".repeat(64),
    receiptRef: `project://B/runs/${job.candidateId}/records/fixture.json`, ...overrides });
  const oldJob = headlessJob("cand-B-headless-old", 10), newJob = headlessJob("cand-B-headless-new", 20);
  const pendingJob = headlessJob("cand-B-headless-pending", 30, "running");
  const failedJob = headlessJob("cand-B-headless-failed", 40, "failed");
  const projectBFixture = workspaceFixture.projects.get("B");
  for (const job of [oldJob, newJob, pendingJob]) projectBFixture.artifact(job.candidateId);
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  await visibleWorkspace().evaluate((element) => { element.headlessBoardMarker = "retained"; });
  const boardBeforeHeadless = structuredClone(projectBFixture.board);
  const writesBeforeHeadless = workspaceFixture.requests.filter((row) => row.method !== "GET").length;
  runtimeB.retained = { projectId: "B", projectDir: runtimeB.projectDir, jobs: [pendingJob, oldJob, failedJob, newJob],
    candidates: [pendingJob, oldJob, failedJob, newJob].map((job) => headlessCandidate(job)) };
  runtimeB.operations = [headlessOperation(newJob), headlessOperation(oldJob), headlessOperation(pendingJob),
    headlessOperation(failedJob, { status: "completed" }),
    headlessOperation(headlessJob("cand-A-wrong-project", 40), { projectId: "A" })];
  emitRuntime();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("monkeyhub.chat-view.v1"))?.tools
    .some((tool) => tool.candidate === "cand-B-headless-new"));
  assert.equal(await page.getByRole("button", { name: "Board", exact: true }).getAttribute("aria-pressed"), "true");
  assert.equal(await visibleWorkspace().evaluate((element) => element.headlessBoardMarker), "retained");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B retained");
  assert.deepEqual(projectBFixture.board, boardBeforeHeadless, "model completion preserves the Board and its marks");
  assert.equal(workspaceFixture.requests.filter((row) => row.method !== "GET").length, writesBeforeHeadless,
    "automatic preview makes no project write or editing-base change");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitCandidate(newJob.candidateId);
  assert.ok(!workspaceFixture.requests.some((row) => row.name.endsWith("/bytes") && row.runId === pendingJob.candidateId),
    "a later unfinished job cannot become the displayed candidate");

  // A headless result is also visible when no tool tab was ever saved. The
  // hidden chat-context workspace becomes the real tab without another worker.
  await page.evaluate(() => {
    const key = "monkeyhub.chat-view.v1", view = JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify({ ...view, tools: [], activeTool: null, panel: false }));
  });
  const beforeHeadlessReopen = writes.length;
  await page.reload();
  await waitWorkspace();
  await waitCandidate(newJob.candidateId);
  assert.equal(await page.locator(".chat-shell").getAttribute("data-panel"), "true",
    "a retained headless delivery opens its project panel without a saved tool tab");
  assert.equal(await page.getByRole("button", { name: "Modeling", exact: true }).getAttribute("aria-pressed"), "true");
  assert.deepEqual(projectBFixture.board, boardBeforeHeadless, "automatic navigation keeps all Board marks");
  assert.ok(writes.slice(beforeHeadlessReopen).every(([, pathname]) => pathname === "/api/runtime/projects/open"),
    "showing the retained delivery only reattaches its existing project runtime");

  // A manual historical preview survives repeated snapshots. Reopening the app
  // starts at the newest reliably ordered headless result, even with an old tab.
  await page.locator(".chat-activity").filter({ hasText: "Final checkpoint completed" }).getByRole("button", { name: "Open this candidate on the right" }).click();
  await waitCandidate("cand-B-final");
  const manualBytes = workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length;
  emitRuntime(); emitRuntime();
  await page.waitForTimeout(600);
  assert.equal(workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length, manualBytes,
    "polling does not steal the architect's explicit historical preview");
  runtimeB.retained.jobs = [];
  await page.reload();
  await waitWorkspace();
  await waitCandidate(newJob.candidateId);

  // Slow older requests and unordered completion observations do not guess a
  // new winner. The projected journal order works without in-memory job times.
  const slowOldJob = headlessJob("cand-B-headless-slow-old", 15);
  const tiedJobs = [headlessJob("cand-B-headless-tie-a", 35), headlessJob("cand-B-headless-tie-b", 35)];
  const unordered = headlessJob("cand-B-headless-unordered", 45);
  for (const job of [slowOldJob, ...tiedJobs, unordered]) projectBFixture.artifact(job.candidateId);
  const stableBytes = workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length;
  runtimeB.retained.candidates.push(headlessCandidate(slowOldJob)); runtimeB.operations.push(headlessOperation(slowOldJob)); emitRuntime();
  await page.waitForTimeout(400);
  runtimeB.retained.candidates.push(...tiedJobs.map((job) => headlessCandidate(job))); runtimeB.operations.push(...tiedJobs.map((job) => headlessOperation(job))); emitRuntime();
  await page.waitForTimeout(400);
  runtimeB.retained.candidates.push(headlessCandidate(unordered)); runtimeB.operations.push(headlessOperation(unordered, { admissionSequence: null })); emitRuntime();
  await page.waitForTimeout(400);
  assert.equal(workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length, stableBytes,
    "older, tied, or unordered results never replace the known latest candidate");
  runtimeB.operations = []; runtimeB.retained = null; emitRuntime();
  await page.locator(".chat-activity").filter({ hasText: "Final checkpoint completed" }).getByRole("button", { name: "Open this candidate on the right" }).click();
  await waitCandidate("cand-B-final");
  await visibleWorkspace().evaluate((element) => { element.completionMarker = "once"; });

  // Refresh and reconnect read the retained operation, without replaying a
  // modification. A crashed worker needs the person's explicit recovery.
  runtimeB.operations = [{ operationId: "committed-operation", projectId: "B", kind: "candidate.commit", source: "studio",
    status: "completed", committed: true, resultRevision: 1, candidateId: "cand-B-final" }];
  let releaseRuntimeOpen;
  runtimeOpenGate = new Promise((resolve) => { releaseRuntimeOpen = resolve; });
  const runtimeOpenReady = new Promise((resolve) => { runtimeOpenCaptured = resolve; });
  await page.evaluate(() => {
    const key = "monkeyhub.chat-view.v1", view = JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify({ ...view, tools: [], activeTool: null, panel: false }));
  });
  await Promise.all([runtimeOpenReady, page.reload()]);
  const beforeLateOpen = writes.length;
  let releaseRuntimeReads;
  runtimeReadGate = new Promise((resolve) => { releaseRuntimeReads = resolve; });
  for (const app of appsFor(runtimeB.projectDir).filter((app) => app.serviceId === "studio")) {
    app.state = "error"; app.error = { code: "WORKER_EXITED", detail: "Fixture worker exited unexpectedly." };
  }
  emitRuntime();
  await page.getByRole("button", { name: "Recover project service", exact: true }).waitFor();
  const lateOpenReply = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/runtime/projects/open");
  releaseRuntimeOpen();
  await lateOpenReply;
  // Later GET snapshots stay pending so they cannot repair an overwritten
  // event before this assertion. Let the released response render first.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole("button", { name: "Recover project service", exact: true }).isVisible(), true,
    "an unversioned late open response cannot hide a newer crashed SSE snapshot");
  assert.equal(writes.length, beforeLateOpen, "a stale open reply cannot start or modify the crashed project");
  runtimeReadGate = null; releaseRuntimeReads();
  await page.locator(".chat-error").waitFor();
  await page.getByRole("button", { name: "Recover project service", exact: true }).click();
  await studioReady();
  await page.locator(".chat-activity").filter({ hasText: "Final checkpoint completed" }).getByRole("button", { name: "Open this candidate on the right" }).click();
  await waitCandidate("cand-B-final");
  const beforeCrash = writes.length;
  for (const app of appsFor(runtimeB.projectDir).filter((app) => app.serviceId === "studio")) {
    app.state = "error"; app.error = { code: "WORKER_EXITED", detail: "Fixture worker exited unexpectedly." };
  }
  emitRuntime();
  await page.getByRole("button", { name: "Recover project service", exact: true }).waitFor();
  assert.equal(writes.length, beforeCrash, "a crash event only reads state");
  await page.reload();
  await page.getByRole("button", { name: "Recover project service", exact: true }).waitFor();
  assert.ok(writes.slice(beforeCrash).every(([, pathname]) => pathname === "/api/runtime/projects/open"), "refreshing a crashed project only reattaches it");
  await page.getByRole("button", { name: "Recover project service", exact: true }).click();
  await waitCandidate("cand-B-final");
  await waitWorkspace();
  assert.ok(workspaceFixture.requests.some((row) => row.runtimeId === runtimeB.runtimeId && row.name === "/api/protocol"), "restored workspace uses the same project runtime prefix");
  assert.equal(writes.slice(beforeCrash).filter(([, pathname]) => pathname.endsWith("/recover")).length, 1);
  assert.ok(writes.slice(beforeCrash).every(([, pathname]) => pathname === "/api/runtime/projects/open" || pathname.endsWith("/recover")),
    "recovery never replays modeling, messages, proposals, or commits");
  await studioReady();
  await visibleWorkspace().evaluate((element) => { element.oldInstanceMarker = true; });
  const beforeRecoverReads = workspaceFixture.requests.length;
  for (const app of appsFor(runtimeB.projectDir).filter((app) => app.serviceId === "studio")) app.state = "error";
  emitRuntime();
  await page.getByRole("button", { name: "Recover project service", exact: true }).click();
  await waitCandidate("cand-B-final");
  assert.equal(await visibleWorkspace().evaluate((element) => element.oldInstanceMarker), true, "runtime recovery preserves the mounted workspace and draft");
  assert.ok(workspaceFixture.requests.slice(beforeRecoverReads).every((row) => row.method === "GET"), "recovery only refreshes workspace reads");
  assert.equal(writes.slice(beforeCrash).filter(([, pathname]) => pathname.endsWith("/recover")).length, 2);
  assert.ok(writes.slice(beforeCrash).every(([, pathname]) => pathname === "/api/runtime/projects/open" || pathname.endsWith("/recover")));
  await page.screenshot({ path: path.join(temporary, "runtime-recovered.png") });
  await page.getByRole("button", { name: /Project B/ }).last().click();
  await page.getByRole("dialog", { name: "Project", exact: true }).getByText("Committed", { exact: true }).waitFor();
  await page.getByRole("dialog", { name: "Project", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
  const beforeReconnect = writes.length;
  allowRuntimeEvents = false;
  for (const stream of streams) stream.end();
  await page.getByText("Connection interrupted. Reading the current project state…", { exact: true }).waitFor();
  await page.screenshot({ path: path.join(temporary, "runtime-disconnected.png") });
  runtimeB.operations.push({ operationId: "unfinished-operation", projectId: "B", kind: "candidate.commit", source: "studio", status: "needs_recovery", committed: false });
  allowRuntimeEvents = true;
  await page.getByText("An operation needs recovery review", { exact: true }).waitFor();
  await page.getByText("Connection interrupted. Reading the current project state…", { exact: true }).waitFor({ state: "hidden" });
  assert.equal(writes.length, beforeReconnect, "SSE reconnection is read-only");
  await page.waitForTimeout(150);
  const quietReads = runtimeReads;
  await page.waitForTimeout(1700);
  assert.equal(runtimeReads, quietReads, "a connected quiet runtime is not polled every 1.2 seconds");
  runtimeB.operations = []; emitRuntime();

  // An edited work copy the project would not take is the architect's own save
  // going nowhere: it is said on this same strip, in the owner's own words, and
  // it goes away when the copy registers something. Other runtime error codes
  // keep the behaviour they had — this strip is not a general error console.
  const workCopyNotice = page.getByText("A work copy's change was not registered as a new revision", { exact: true });
  runtimeB.error = { code: "WORK_COPY_DOCUMENT_REVISION_ALREADY_REGISTERED",
    detail: "plan.png: These bytes are already a registered revision of this page." };
  emitRuntime();
  await workCopyNotice.waitFor();
  await page.getByText(/plan\.png: These bytes are already a registered revision of this page\./).waitFor();
  runtimeB.error = { code: "RUNTIME_READ_FAILED", detail: "an unrelated retained read failed" };
  emitRuntime();
  await workCopyNotice.waitFor({ state: "hidden" });
  assert.equal(await page.getByText("an unrelated retained read failed").count(), 0,
    "the work-copy notice must not turn this strip into a general runtime error console");
  runtimeB.error = null; emitRuntime();

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

  // Workspaces share the host document and have no second settings surface.
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  assert.equal(await page.getByRole("button", { name: "Hub settings", exact: true }).count(), 1);
  assert.equal(workspaceFixture.requests.filter((row) => row.name === "/api/settings/user").length, 0);

  await page.screenshot({ path: path.join(temporary, "desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.getByText("More launch options", { exact: true }).click();
  const diagnostics = page.getByRole("checkbox", { name: "Developer / Research Mode", exact: true });
  const checkBox = await diagnostics.boundingBox();
  const labelBox = await diagnostics.locator('..').boundingBox();
  assert.ok(checkBox.width <= 20 && checkBox.height <= 20, 'checkbox stays compact');
  assert.ok(Math.abs(checkBox.y + checkBox.height / 2 - labelBox.y - labelBox.height / 2) < 2, 'checkbox aligns with its label');
  await page.getByRole('dialog').screenshot({ path: path.join(temporary, 'settings-checkbox.png') });
  await diagnostics.check();
  assert.equal(await page.getByRole("checkbox", { name: "Event stream", exact: true }).isVisible(), true);
  await diagnostics.uncheck();
  const englishStageLabel = await visibleWorkspace().locator(".stage").getAttribute("aria-label");
  await page.locator("#theme").selectOption("dark");
  await page.locator("#language").selectOption("zh-CN");
  assert.equal(await page.locator("html").getAttribute("lang"), "zh-CN");
  assert.notEqual(await visibleWorkspace().locator(".stage").getAttribute("aria-label"), englishStageLabel, "workspace language follows Hub context");
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await page.locator("#language").selectOption("en");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({ path: path.join(temporary, "dark.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Hide projects", exact: true }).first().click();
  await page.screenshot({ path: path.join(temporary, "mobile.png"), fullPage: true });
  assert.ok(await railWidth() > 40, "the rail survives a phone-sized window");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  // Modeling refusal occurs only on explicit Arch entry; project creation
  // and the first Render visit remain usable without modeling inputs.
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "Show projects", exact: true }).first().click();
  modelingFailure = { code: "PROJECT_INPUTS_CONFLICT", detail: "Existing inputs need review." };
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  await page.locator("#new-project-name").fill("needs-review");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await studioReady();
  await page.getByRole("button", { name: "Render", exact: true }).click(); await waitWorkspace("render");
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "needs-review").length, 0);
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
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
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await delayedStart;
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  releaseModeling(); modelingResponseGate = Promise.resolve();
  await page.waitForFunction(() => document.querySelector('.chat-composer-note')?.textContent === "");
  assert.equal(await page.locator(".chat-project-workspace:visible").count(), 0, "a late response cannot open the old chat workspace");
  // Archiving removes only the sidebar entry. The retained chat is readable,
  // survives a page reload and is explicitly restored before it can continue.
  const archivable = sessions.find((row) => row.projectId === "B");
  const activeBeforeArchive = sessions.find((row) => row.projectId === "A");
  activeBeforeArchive.status = "running";
  emitRuntime();
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
  assert.equal(writes.slice(beforeAdd).filter(([, pathname, , target]) => pathname === "/api/project/modeling" && target === "D:\\fixture\\C").length, 0);
  assert.ok(!writes.slice(beforeAdd).some(([, pathname]) => pathname === "/api/chat/projects" || pathname === "/api/project/modeling"));
  assert.equal(await page.locator("iframe").count(), 0, "prepared services do not eagerly mount three expensive pages");
  for (const item of appsFor("D:\\fixture\\C").filter(item => item.serviceId === "studio")) { item.state = "stopped"; item.processId = null; }
  runtimes.get("D:\\fixture\\C").projection = "unknown";
  emitRuntime();
  await page.waitForFunction(() => document.querySelector('.chat-rail__tool[aria-label="Modeling"]')?.dataset.state === "stopped");
  const beforeReopen = writes.length;
  for (const [label, id] of [["Modeling", "arch"], ["Board", "board"]]) {
    await page.getByRole("button", { name: label, exact: true }).click();
    await waitWorkspace(id);
  }
  assert.equal(writes.slice(beforeReopen).filter(([, pathname]) => pathname === "/api/project/modeling").length, 1,
    "an externally stopped Studio is prepared again once and shared by both workspaces");
  // Creation succeeds independently of a temporarily unavailable CLI. An
  // older in-flight discovery response cannot discard the newly selected project.
  let releaseProjectList;
  projectListGate = new Promise(resolve => { releaseProjectList = resolve; });
  await Promise.all([page.waitForRequest(request => request.method() === "GET" && new URL(request.url()).pathname === "/api/chat/projects"), Promise.resolve().then(emitRuntime)]);
  chatCreationFailureFor = "D:\\fixture\\provider-recovery";
  await page.getByRole("button", { name: "New project", exact: true }).first().click();
  await page.locator("#new-project-name").fill("provider-recovery");
  await create.getByRole("button", { name: "Create and start chatting" }).click();
  await page.locator(".chat-error").filter({ hasText: "The CLI connection is temporarily unavailable." }).waitFor();
  assert.equal(await create.isVisible(), false);
  releaseProjectList();
  for (let refresh = 0; refresh < 2; refresh++) await Promise.all([page.waitForResponse(response =>
    response.request().method() === "GET" && new URL(response.url()).pathname === "/api/chat/projects"), Promise.resolve().then(emitRuntime)]);
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
  assert.equal(writes.filter(([, pathname, body]) => pathname === "/api/project/modeling" && body.projectId === "provider-recovery").length, 0);
  // Attachments stay in the selected draft until its message is sent. Every
  // file here is synthetic; these routes never call a provider or model.
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  await page.locator("#chat-input").fill("Attachment draft A");
  const beforeAttachmentDrafts = writes.filter(([, pathname]) => pathname.endsWith("/messages")).length;
  const fileChooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Add attachments", exact: true }).click();
  await (await fileChooser).setFiles([
    { name: "outline.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic outline A") },
    { name: "remove-me.txt", mimeType: "text/plain", buffer: Buffer.from("Remove this draft file") },
  ]);
  await page.getByRole("button", { name: "Remove attachment: remove-me.txt", exact: true }).click();
  assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 1);
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 0);
  await page.locator("#chat-input").fill("Attachment draft B");
  await page.locator("#chat-input").evaluate((input) => {
    const clipboardData = new DataTransfer();
    clipboardData.items.add(new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], "clipboard.png", { type: "image/png" }));
    input.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData }));
  });
  await page.locator(".chat-composer").evaluate((composer) => {
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(new File(["Dropped notes B"], "notes.txt", { type: "text/plain" }));
    composer.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
  });
  await page.getByRole("button", { name: "Remove attachment: notes.txt", exact: true }).waitFor();
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.equal(await page.locator("#chat-input").inputValue(), "Attachment draft A");
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.equal(await page.locator("#chat-input").inputValue(), "Attachment draft B");
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["clipboard.png", "notes.txt"]);
  assert.equal(writes.filter(([, pathname]) => pathname.endsWith("/messages")).length, beforeAttachmentDrafts);
  // A staged update cannot discard drafts, including a different project's
  // hidden composer. Opening/closing settings must preserve the actual files.
  updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.getByText("A conversation has unsent text or attachments. Send or remove them before restarting.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true);
  assert.equal(appliedPatches, 0);
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  assert.equal(await page.locator("#chat-input").inputValue(), "Attachment draft B");
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["clipboard.png", "notes.txt"]);
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
  await page.locator("#chat-input").fill("");
  chatMessageFailureFor = "D:\\fixture\\B";
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.locator(".chat-error").filter({ hasText: "Fixture upload failed. Try again." }).waitFor();
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["clipboard.png", "notes.txt"]);
  assert.equal(await page.getByRole("button", { name: "Send", exact: true }).isEnabled(), true);
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["clipboard.png", "notes.txt"]);
  chatMessageFailureFor = null;
  let releaseAttachmentPost;
  chatMessageResponseGate = new Promise((resolve) => { releaseAttachmentPost = resolve; });
  const attachmentRequest = page.waitForRequest((req) => req.method() === "POST" && new URL(req.url()).pathname.endsWith("/messages"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const sentAttachmentRequest = await attachmentRequest;
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  const attachmentReply = page.waitForResponse((response) => response.request() === sentAttachmentRequest);
  releaseAttachmentPost(); chatMessageResponseGate = Promise.resolve();
  await attachmentReply;
  await page.waitForFunction(() => !document.querySelector("#chat-input")?.disabled);
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project A");
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 0);
  updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
  assert.equal(await page.locator("#chat-input").inputValue(), "");
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.getByText("A conversation has unsent text or attachments. Send or remove them before restarting.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true,
    "Project A's hidden draft blocks restart even though B's visible composer is empty");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
  const attachmentPost = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1);
  assert.equal(attachmentPost[2].content, "");
  assert.equal(attachmentPost[2].projectId, "B");
  assert.equal(attachmentPost[2].designContext.sourceRunId, "home-B", "switching projects cannot inherit A's editing base or B's viewed candidate");
  assert.equal(attachmentPost[2].designContext.stateDigest, workspaceFixture.projects.get("B").assets.get("home-B").dto.designStateDigest);
  assert.equal(attachmentPost[2].designContext.elementId, undefined, "whole-design requests do not invent a focus");
  assert.deepEqual(attachmentPost[2].attachments, [
    { name: "clipboard.png", mimeType: "image/png", data: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).toString("base64") },
    { name: "notes.txt", mimeType: "text/plain", data: Buffer.from("Dropped notes B").toString("base64") },
  ]);
  const savedAttachments = page.locator(".chat-attachments--saved > li > a");
  assert.equal(await savedAttachments.count(), 2);
  const attachedSession = sessions.find((session) => session.title === "clipboard.png");
  assert.equal(await savedAttachments.first().getAttribute("href"), `/api/chat/sessions/${attachedSession.id}/attachments/${attachedSession.messages[0].attachments[0].id}`);
  const downloaded = page.waitForEvent("download");
  await savedAttachments.first().click();
  assert.equal((await downloaded).suggestedFilename(), "clipboard.png");
  await page.screenshot({ path: path.join(temporary, "chat-attachments.png"), fullPage: true });
  // Public progress is readable without expanding tool diagnostics, including
  // multi-line streamed summaries that used to be hidden after the first line.
  const publicProgress = { id: "attachment-turn:progress:provider-summary", role: "tool", status: "streaming",
    content: "Reading the uploaded drawings.\nChecking the marked opening against the project model." };
  attachedSession.messages.push(publicProgress, { id: "attachment-turn:read-files", role: "tool", status: "streaming",
    content: "Read project files · in_progress\nSynthetic tool diagnostics" });
  emitRuntime();
  const progressCard = page.locator(".chat-progress").filter({ hasText: "Reading the uploaded drawings." });
  await progressCard.getByText("Checking the marked opening against the project model.", { exact: false }).waitFor();
  assert.equal(await progressCard.locator("details").count(), 0);
  const toolDetails = page.locator(".chat-activity details").filter({ hasText: "Read project files" });
  assert.equal(await toolDetails.getAttribute("open"), null);
  await toolDetails.locator("summary").click();
  assert.equal(await toolDetails.locator("pre").innerText(), "Synthetic tool diagnostics");
  publicProgress.content += "\nThe opening comparison is ready to review.";
  publicProgress.status = "complete";
  emitRuntime();
  await progressCard.getByText("The opening comparison is ready to review.", { exact: false }).waitFor();
  await page.getByRole("button", { name: "Hide tools", exact: true }).click();
  await page.getByRole("button", { name: "Hide projects", exact: true }).first().click();
  const originalViewport = page.viewportSize();
  for (const width of [1440, 900, 375]) {
    await page.setViewportSize({ width, height: 960 });
    await progressCard.scrollIntoViewIfNeeded();
    const uploadButton = page.getByRole("button", { name: "Add attachments", exact: true });
    const bounds = await uploadButton.boundingBox();
    assert.ok(bounds && bounds.width >= 44 && bounds.height >= 44 && bounds.x >= 0 && bounds.x + bounds.width <= width);
    assert.ok(await progressCard.evaluate((node) => node.scrollWidth <= node.clientWidth + 1));
    await uploadButton.focus();
    const chooser = page.waitForEvent("filechooser");
    await page.keyboard.press("Enter");
    await (await chooser).setFiles({ name: "next-turn.txt", mimeType: "text/plain", buffer: Buffer.from("Next turn attachment") });
    await page.getByRole("button", { name: "Remove attachment: next-turn.txt", exact: true }).click();
    await page.screenshot({ path: path.join(temporary, `chat-progress-upload-${width}.png`), fullPage: true });
  }
  await page.setViewportSize(originalViewport);
  await page.getByRole("button", { name: "Show projects", exact: true }).first().click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  // Local validation leaves the already-selected draft untouched.
  await page.locator('.chat-composer input[type="file"]').setInputFiles(Array.from({ length: 8 }, (_, index) => ({ name: `extra-${index}.txt`, mimeType: "text/plain", buffer: Buffer.from("x") })));
  await page.locator(".chat-error").filter({ hasText: "Add up to 8 attachments per message." }).waitFor();
  for (const [sizes, detail] of [[[20 * 1024 * 1024 + 1], "Each attachment must be 20 MiB or smaller."], [[20 * 1024 * 1024, 20 * 1024 * 1024], "Attachments must total 40 MiB or less."]]) {
    await page.locator(".chat-composer").evaluate((composer, sizes) => {
      const dataTransfer = new DataTransfer();
      sizes.forEach((size, index) => dataTransfer.items.add(new File([new Uint8Array(size)], `large-${index}.bin`, { type: "application/octet-stream" })));
      composer.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer }));
    }, sizes);
    await page.locator(".chat-error").filter({ hasText: detail }).waitFor();
  }
  assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["outline.txt"]);
  // Navigation URLs describe the selected surface. A previous project's
  // workspace link must not win over a newly selected project or machine tool.
  for (const session of sessions) session.status = "idle";
  await page.evaluate(() => localStorage.removeItem("monkeyhub.chat-view.v1"));
  await page.goto(origin);
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  await page.waitForFunction((runtimeId) => new URLSearchParams(location.search).get("runtimeId") === runtimeId, runtimes.get("D:\\fixture\\A").runtimeId);
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.waitForFunction(() => !new URLSearchParams(location.search).has("runtimeId"));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('.chat-project[data-selected="true"] .chat-project__name')?.textContent === "Project B");
  assert.equal(await visibleWorkspace().count(), 0, "an unopened B workspace stays unopened after refreshing from A");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeyfab"));
  assert.equal(new URL(page.url()).searchParams.has("runtimeId"), false);
  await page.reload();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("app=monkeyfab"));
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project B");
  await page.getByRole("button", { name: "Usage", exact: true }).click();
  await waitMonitor();
  await page.reload();
  await waitMonitor();
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project B",
    "restoring Monitor also retains its surrounding project navigation");
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  // A deliberate system-page link wins even with a retained headless model.
  runtimeB.retained = { projectId: "B", projectDir: runtimeB.projectDir, jobs: [], candidates: [headlessCandidate(newJob)] };
  runtimeB.operations = [headlessOperation(newJob)];
  await page.goto(`${origin}/?view=monitor`);
  await waitMonitor();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("monkeyhub.chat-view.v1"))?.tools
    .some((tool) => tool.candidate === "cand-B-headless-new"));
  assert.equal(await page.getByRole("button", { name: "Usage", exact: true }).getAttribute("aria-pressed"), "true",
    "automatic candidate delivery must not override an explicit Monitor deep link");
  runtimeB.retained = null; runtimeB.operations = []; emitRuntime();
  assert.equal(await page.getByRole("textbox", { name: "What would you like to do in this project?" }).isVisible(), true,
    "a legacy Monitor deep link opens a panel without replacing the conversation");
  const afterMonitorLink = documentLoads;
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  assert.equal(documentLoads, afterMonitorLink, "a legacy Monitor link can be left without navigating the document");
  // An explicit link is deliberate navigation, and overrides the previous B/Fab view.
  await page.goto(`${origin}/?view=board&runtimeId=${runtimes.get("D:\\fixture\\A").runtimeId}`);
  await waitWorkspace("board");
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project A");
  await page.waitForFunction(() => !document.querySelector('.chat-composer input[type="checkbox"]')?.disabled);
  assert.equal(await visibleWorkspace().locator(".stage canvas").count(), 0, "Board-first context uses the same session without opening Arch");
  assert.equal(await page.getByRole("button", { name: "Board", exact: true }).getAttribute("aria-pressed"), "true");
  assert.equal(new URL(page.url()).searchParams.get("view"), "board");

  // archive export and restore dialogs show the summary — a whole project
  // leaves and comes back as one file, and each dialog states what that file
  // actually holds rather than only that something was written.
  await page.getByRole("button", { name: /Project A/ }).last().click();
  const archiveCard = page.getByRole("dialog", { name: "Project", exact: true });
  await archiveCard.getByRole("button", { name: "Export archive…" }).click();
  const exported = page.getByRole("dialog").filter({ hasText: "Export project archive" });
  await exported.getByText("Project A · Version 3").waitFor();
  // Explorer copies a path with its quotes; the API is given the plain path.
  await exported.getByLabel("Archive file").fill('"D:\\backups\\fixture-A.monkeyhub.zip"');
  await exported.getByRole("button", { name: "Export", exact: true }).click();
  await exported.getByText("Archive written").waitFor();
  assert.deepEqual(writes.at(-1).slice(0, 3), ["POST", "/api/project/archive/export",
    { projectDir: "D:\\fixture\\A", archivePath: "D:\\backups\\fixture-A.monkeyhub.zip" }]);
  await exported.getByText("5.0 MiB").waitFor();
  await exported.getByText("42 retained files").waitFor();
  await exported.getByText("7 retained runs").waitFor();
  assert.deepEqual(await exported.locator(".chat-archive-summary li").allInnerTexts(), archiveOmissions);
  await exported.getByText("None", { exact: true }).waitFor();
  await exported.getByText("Verified by re-reading the archive").waitFor();
  await page.screenshot({ path: path.join(temporary, "archive-export.png") });
  await exported.getByRole("button", { name: "Close", exact: true }).last().click();

  await archiveCard.getByRole("button", { name: "Restore archive…" }).click();
  const restored = page.getByRole("dialog").filter({ hasText: "Restore project archive" });
  assert.equal(await restored.getByLabel("Restore into folder").inputValue(), "D:\\fixture",
    "the restore target starts at the workspace new projects are made in");
  await restored.getByLabel("Archive file").fill("D:\\backups\\fixture-A.monkeyhub.zip");
  await restored.getByRole("button", { name: "Restore", exact: true }).click();
  await restored.getByText("Project restored and verified through normal project readers").waitFor();
  assert.deepEqual(writes.at(-1).slice(0, 3), ["POST", "/api/project/archive/restore",
    { archivePath: "D:\\backups\\fixture-A.monkeyhub.zip", targetParent: "D:\\fixture" }]);
  await restored.getByText("D:\\fixture\\restored-demo").waitFor();
  await restored.getByRole("button", { name: "Open restored project" }).waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll(".chat-project__name")]
    .some((item) => item.textContent === "restored-demo"));
  await page.screenshot({ path: path.join(temporary, "archive-restored.png") });
  await restored.getByRole("button", { name: "Close", exact: true }).last().click();
  await archiveCard.getByRole("button", { name: "Close", exact: true }).click();

  // The user may reset model context without deleting the visible conversation.
  // Viewing a prior result stays separate from deliberately continuing it.
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  const projectContext = page.getByRole("checkbox", { name: "Continue from project state (without previous conversation context)" });
  await page.waitForFunction(() => !document.querySelector('.chat-composer input[type="checkbox"]')?.disabled);
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await visibleWorkspace().locator('.vcard__export').filter({ hasText: "cand-A-1.3dm" }).click();
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await projectContext.check();
  await page.locator("#chat-input").fill("Compare the courtyard from the saved project state");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  const resetPost = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1)[2];
  assert.equal(resetPost.contextMode, "project");
  assert.equal(resetPost.designContext.sourceRunId, "home-A", "history browsing does not silently become the editing base");
  assert.equal(resetPost.designContext.stateDigest, workspaceFixture.projects.get("A").assets.get("home-A").dto.designStateDigest);
  await page.locator(".chat-message--user").getByText("Project context", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await visibleWorkspace().getByRole("button", { name: "Continue from this version", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.chat-project-workspace:not([hidden]) .editing-base')?.dataset.sourceMatch === "same");
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  assert.equal(await projectContext.isChecked(), false, "the new-context option applies to one message");
  await page.locator("#chat-input").fill("Revise this candidate now");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  const continuePost = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1)[2];
  assert.equal(continuePost.contextMode, "stage", "the default checks for an accepted boundary without forcing a candidate reset");
  assert.equal(continuePost.designContext.sourceRunId, "cand-A-1", "explicit continuation changes the bound context");
  assert.equal(continuePost.designContext.stateDigest, workspaceFixture.projects.get("A").assets.get("cand-A-1").dto.designStateDigest);
  assert.equal(await page.locator(".chat-message--user").count(), 2, "starting model context retains the visible chat");
  assert.equal(await page.getByText("Continuing from confirmed stage:", { exact: false }).count(), 0,
    "requesting automatic handoff alone does not label a candidate as accepted");
  await page.getByRole("button", { name: "Stop", exact: true }).click();

  await page.reload();
  await waitWorkspace();
  await page.waitForFunction(() => !document.querySelector('.chat-composer input[type="checkbox"]')?.disabled);
  await page.locator("#chat-input").fill("Continue the saved candidate after reopening");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  const reopenedPost = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1)[2];
  assert.equal(reopenedPost.designContext.sourceRunId, "cand-A-1", "reopening retains the explicitly chosen candidate as the chat base");
  assert.equal(reopenedPost.designContext.stateDigest, continuePost.designContext.stateDigest);
  await page.getByRole("button", { name: "Stop", exact: true }).click();

  // The Runtime/ChatStore tests verify acceptance and rotation. Here the UI
  // receives the actual confirmed boundary, not a client-inferred acceptance.
  confirmedStageForChat = { runId: "cand-A-1", stageRef: "confirmed-massing", label: "Massing approved" };
  await page.getByText("After you confirm a stage, its saved result starts the next model context. Candidate revisions keep the same conversation.", { exact: true }).waitFor();
  await page.locator("#chat-input").fill("Continue with the walls from the confirmed massing");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  const stagePost = writes.filter(([, pathname]) => pathname.endsWith("/messages")).at(-1)[2];
  assert.equal(stagePost.contextMode, "stage");
  assert.equal(stagePost.designContext.sourceRunId, "cand-A-1");
  await page.getByText("Continuing from confirmed stage: Massing approved", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.reload();
  await page.getByText("Continuing from confirmed stage: Massing approved", { exact: true }).waitFor();
  assert.equal(await page.locator(".chat-message--user").count(), 4, "the stage handoff remains visible after reopening");
  confirmedStageForChat = null;

  // A source application owns the external session; Hub shows its retained
  // Markdown and media without accidentally starting a second provider turn.
  const externalSession = { id: "external-214", projectId: "B", projectDir: "D:\\fixture\\B", title: "Exterior review",
    provider: "codex", sourceSessionId: "source-task-214", status: "idle", archived: false,
    createdAt: "2026-09-20", updatedAt: "2026-09-20", messages: [
      { id: "external-progress", role: "tool", status: "complete", content: "Facade comparison ready\nPublic progress summary" },
      { id: "external-result", role: "assistant", status: "complete", content: [
        "## Facade comparison", "", "A **retained** candidate with `same base`.", "", "- First option", "- Second option", "",
        "| Option | Decision |", "| --- | --- |", "| A | Review |", "", "```js", "const accepted = false;", "```", "",
        "[Source](https://example.com/reference) [unsafe](javascript:alert(1))",
        '<img src="https://invalid.example/untrusted.png" onerror="alert(1)">',
        "![Remote image](https://example.com/remote.png)",
      ].join("\n"), attachments: [
        { id: "external-image", name: "facade.png", mimeType: "image/png", size: Buffer.from(externalImage, "base64").length },
        { id: "external-broken", name: "broken.png", mimeType: "image/png", size: 6 },
        { id: "external-svg", name: "diagram.svg", mimeType: "image/svg+xml", size: 11 },
      ], documents: [{ runId: "document-B", assetSha256: "d".repeat(64), revisionRef: "revision-B", pageIndex: 0,
        fileName: "registered.png", mimeType: "image/png" }] },
    ] };
  uploadedAttachments.set("external-image", { sessionId: externalSession.id, name: "facade.png", mimeType: "image/png", data: externalImage });
  uploadedAttachments.set("external-broken", { sessionId: externalSession.id, name: "broken.png", mimeType: "image/png", data: Buffer.from("broken").toString("base64") });
  uploadedAttachments.set("external-svg", { sessionId: externalSession.id, name: "diagram.svg", mimeType: "image/svg+xml", data: Buffer.from("<svg></svg>").toString("base64") });
  sessions.unshift(externalSession);
  const beforeExternalTurns = writes.filter(([, name]) => /\/(messages|stop|model)$/.test(name)).length;
  await page.goto(`${origin}/?chatId=${externalSession.id}`);
  await page.locator(".chat-header h1").filter({ hasText: "Exterior review" }).waitFor();
  await page.locator(".chat-external-notice").getByText("External conversation", { exact: true }).waitFor();
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project B",
    "a deep link takes its project from the selected chat, not stale local preferences");
  assert.equal(await page.locator("#chat-input").count(), 0);
  assert.equal(await page.getByRole("button", { name: "Send", exact: true }).count(), 0);
  assert.equal(await page.locator(".chat-prose strong").innerText(), "retained");
  assert.deepEqual(await page.locator(".chat-prose li").allTextContents(), ["First option", "Second option"]);
  assert.equal(await page.locator(".chat-prose table tbody").innerText(), "A\tReview");
  assert.equal(await page.locator(".chat-code code").innerText(), "const accepted = false;");
  assert.equal(await page.locator(".chat-prose img, .chat-prose script").count(), 0, "model markup and remote image syntax cannot introduce image requests or executable HTML");
  assert.equal(await page.locator('.chat-prose a[href^="javascript:"]').count(), 0);
  assert.equal(await page.getByRole("link", { name: "Source", exact: true }).getAttribute("rel"), "noopener noreferrer");
  await page.getByText("This image could not be loaded. You can still download the file.", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Enlarge image: diagram.svg", exact: true }).count(), 0);
  const preview = page.getByRole("button", { name: "Enlarge image: facade.png", exact: true });
  await preview.click();
  const imageDialog = page.getByRole("dialog", { name: "facade.png", exact: true });
  await imageDialog.waitFor();
  assert.equal(await imageDialog.getByRole("img").evaluate((image) => image.complete && image.naturalWidth > 0), true);
  const imageDownload = page.waitForEvent("download");
  await imageDialog.getByRole("link", { name: "Download", exact: true }).click();
  const savedImage = await imageDownload;
  assert.equal(savedImage.suggestedFilename(), "facade.png");
  assert.deepEqual(await readFile(await savedImage.path()), Buffer.from(externalImage, "base64"));
  await page.keyboard.press("Escape");
  await imageDialog.waitFor({ state: "hidden" });
  assert.equal(await preview.evaluate((button) => button === document.activeElement), true);
  await page.getByRole("button", { name: "Enlarge image: registered.png", exact: true }).click();
  const documentDialog = page.getByRole("dialog", { name: "registered.png", exact: true });
  assert.equal(await documentDialog.getByRole("link", { name: "Download", exact: true }).getAttribute("href"),
    `/api/chat/sessions/${externalSession.id}/documents/external-result/0?download=true`);
  const documentDownload = page.waitForEvent("download");
  await documentDialog.getByRole("link", { name: "Download", exact: true }).click();
  const savedDocument = await documentDownload;
  assert.equal(savedDocument.suggestedFilename(), "registered.png");
  assert.deepEqual(await readFile(await savedDocument.path()), Buffer.from(externalImage, "base64"));
  await documentDialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({ path: path.join(temporary, "external-presentation.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".chat-sidebar__top .chat-icon").click();
  await preview.click();
  const previewBounds = await imageDialog.boundingBox();
  assert.ok(previewBounds.x >= 0 && previewBounds.x + previewBounds.width <= 390, "the image dialog fits a narrow viewport");
  await page.screenshot({ path: path.join(temporary, "external-image-mobile.png") });
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator(".chat-sidebar__top .chat-icon").click();
  await page.reload();
  await page.getByRole("button", { name: "Enlarge image: facade.png", exact: true }).waitFor();
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator("#chat-input").waitFor();
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-external-notice").waitFor();
  assert.equal(writes.filter(([, name]) => /\/(messages|stop|model)$/.test(name)).length, beforeExternalTurns);

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
  await waitMonitor();
  assert.ok(writes.slice(beforeNoProject).every(([, pathname]) => ["/api/apps/monkeyfab/start", "/api/apps/monkeymonitor/start"].includes(pathname)));
  await page.reload();
  await waitMonitor();
  assert.equal(await page.getByRole("button", { name: "Modeling", exact: true }).isDisabled(), true,
    "restoring the system Monitor panel does not require or invent a project");
  } else {
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector("#chat-input") && !document.querySelector("#chat-input").disabled);
    await page.locator("#chat-input").fill("Keep this hidden project draft");
    await page.locator('.chat-composer input[type="file"]').setInputFiles({ name: "keep.txt", mimeType: "text/plain", buffer: Buffer.from("Retain these exact draft bytes") });
    await page.getByRole("button", { name: "Project B", exact: true }).first().click();
    await page.waitForFunction(() => document.querySelector("#chat-input")?.value === "");
    assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 0);
    updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
    await page.getByRole("button", { name: "Hub settings", exact: true }).click();
    await page.getByText("A conversation has unsent text or attachments. Send or remove them before restarting.").waitFor();
    assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true);
    assert.equal(appliedPatches, 0);
    await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "Project A", exact: true }).first().click();
    assert.equal(await page.locator("#chat-input").inputValue(), "Keep this hidden project draft");
    assert.deepEqual(await page.locator(".chat-composer .chat-attachment__name").allTextContents(), ["keep.txt"]);
    // This is an explicit fixture reset, never a production update reload.
    projects.splice(0); sessions.splice(0); settings.projectDir = null;
    updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
    await page.evaluate(() => localStorage.removeItem("monkeyhub.chat-view.v1")); await page.reload();
  }
  // Patch preparation transfers the ZIP bytes exactly, polls until ready,
  // preserves settings edits, and delegates restart without a document reload.
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.getByText("fixture-current-desktop", { exact: true }).waitFor();
  await page.getByText("Local patch mode · Automatic downloads are not configured.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Check for updates", exact: true }).count(), 0);
  updateStatus = { ...updateStatus, mode: "unsupported" };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText("Patch updates require the installed MonkeyHub desktop app.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).count(), 0);
  updateStatus = { ...updateStatus, mode: "local" };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).waitFor();
  await page.locator('.software-update input[type="file"]').setInputFiles({ name: "not-a-patch.txt", mimeType: "text/plain", buffer: Buffer.from("not a patch") });
  await page.getByText("Choose a patch ZIP file.", { exact: true }).waitFor();
  assert.equal(patchUploads.length, 0, "invalid local selection never reaches the update API");
  const patchBytes = Buffer.from([80, 75, 3, 4, 0, 255, 13, 10, 42]);
  const patchChooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).click();
  await (await patchChooser).setFiles({ name: "fixture-update.zip", mimeType: "application/zip", buffer: patchBytes });
  await page.getByText("Verifying and preparing the patch…", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).isDisabled(), true);
  await page.getByText("fixture-next-desktop", { exact: true }).waitFor();
  assert.equal(patchUploads.length, 1); assert.deepEqual(patchUploads[0], patchBytes);
  assert.ok(patchPolls >= 2); await page.getByText("1.0 MiB", { exact: true }).waitFor();
  await page.locator("#theme").selectOption("dark");
  await page.getByText("Save your settings changes before restarting.", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true);
  await page.locator("#save-appearance").click();
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find((button) => button.textContent === "Restart to update")?.disabled);
  // The desktop dialog is usable at a small viewport, in dark mode, with
  // larger text and reduced motion, without horizontal clipping.
  await page.locator("#font-scale").selectOption("1.1");
  await page.locator("#save-appearance").click();
  await page.emulateMedia({ reducedMotion: "reduce" }); await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".software-update").scrollIntoViewIfNeeded();
  assert.equal(await page.locator(".software-update").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true);
  assert.equal(await page.getByRole("dialog").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true);
  for (const button of await page.locator(".software-update__actions button").all()) assert.ok((await button.boundingBox()).height >= 44);
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-small-dark.png") });
  await page.locator("#language").selectOption("zh-CN"); await page.locator("#save-appearance").click();
  await page.getByRole("heading", { name: "软件更新", exact: true }).waitFor();
  await page.locator(".software-update").scrollIntoViewIfNeeded();
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-small-zh.png") });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-wide-zh.png") });
  await page.locator("#language").selectOption("en"); await page.locator("#save-appearance").click();
  updateApplyFailure = true;
  await page.getByRole("button", { name: "Restart to update", exact: true }).click();
  await page.getByText("A task started before restart. Wait and retry.", { exact: true }).waitFor();
  assert.equal(appliedPatches, 1);
  await page.waitForFunction(() => !document.querySelector('.chat-dialog--settings button[aria-label="Close"]')?.disabled);
  assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isEnabled(), true);
  updateApplyFailure = false;
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText("A task started before restart. Wait and retry.", { exact: true }).waitFor({ state: "hidden" });
  const beforeUpdateLoads = documentLoads;
  await page.getByRole("button", { name: "Restart to update", exact: true }).click();
  await page.getByText("Restarting to update…", { exact: true }).waitFor();
  assert.equal(appliedPatches, 2); assert.equal(documentLoads, beforeUpdateLoads);
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isDisabled(), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("dialog").isVisible(), true, "accepted restart cannot expose a composer for new unsaved work");
  // The desktop can reject a prepared target after /apply was accepted. A disconnected old
  // server is expected while restarting; an explicit failed status restores
  // the old window without discarding state or forcing any navigation.
  const disconnectedStatus = page.waitForEvent("requestfailed", { predicate: (request) => new URL(request.url()).pathname === "/api/updates/status" });
  updateReadFailures = 1; await disconnectedStatus;
  await page.waitForResponse((response) => new URL(response.url()).pathname === "/api/updates/status" && response.ok());
  assert.equal(await page.locator(".software-update .error-message").count(), 0);
  assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isDisabled(), true);
  updateStatus = { ...updateStatus, state: "failed", canApply: false, error: { code: "UPDATE_ROLLED_BACK", detail: "The new application could not start. The previous desktop version has been restored." } };
  await page.getByText("The new application could not start. The previous desktop version has been restored.", { exact: true }).waitFor();
  assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isEnabled(), true);
  assert.equal(await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).isEnabled(), true);
  assert.equal(documentLoads, beforeUpdateLoads, "desktop rollback preserves the current application document");
  for (const response of ["lost", "malformed"]) {
    updateStatus = { ...updateStatus, state: "ready", canApply: true, error: null };
    await page.getByRole("button", { name: "Refresh status", exact: true }).click();
    await page.getByText("Patch ready. Restart when your work is saved.", { exact: true }).waitFor();
    updateApplyResponse = response;
    const previousApplies = appliedPatches;
    const failedRead = page.waitForEvent("requestfailed", { predicate: (request) => new URL(request.url()).pathname === "/api/updates/status" });
    await page.getByRole("button", { name: "Restart to update", exact: true }).click();
    await failedRead;
    assert.equal(appliedPatches, previousApplies + 1);
    assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isDisabled(), true,
      `accepted apply with ${response} response must keep the restart lock`);
    await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
    assert.equal(await page.getByRole("dialog").isVisible(), true);
    await page.waitForResponse((result) => new URL(result.url()).pathname === "/api/updates/status" && result.ok());
    assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isDisabled(), true);
    assert.equal(appliedPatches, previousApplies + 1, "uncertain apply is reconciled by reads, never resubmitted");
    updateStatus = { ...updateStatus, state: "failed", canApply: false, error: { code: "UPDATE_ROLLED_BACK", detail: `Recovered after ${response} apply response.` } };
    await page.getByText(`Recovered after ${response} apply response.`, { exact: true }).waitFor();
    assert.equal(await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).isEnabled(), true);
    assert.equal(documentLoads, beforeUpdateLoads);
  }
  // A request that never reached admission can unlock only after a successful
  // read confirms the previous ready state; the page still does not retry it.
  updateStatus = { ...updateStatus, state: "ready", canApply: true, error: null };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText("Patch ready. Restart when your work is saved.", { exact: true }).waitFor();
  updateApplyResponse = "not-received";
  const previousApplies = appliedPatches;
  const rejectedApply = page.waitForEvent("requestfailed", { predicate: (request) => new URL(request.url()).pathname === "/api/updates/apply" });
  await page.getByRole("button", { name: "Restart to update", exact: true }).click();
  await rejectedApply;
  await page.waitForFunction(() => !document.querySelector('.chat-dialog--settings button[aria-label="Close"]')?.disabled);
  assert.equal(appliedPatches, previousApplies + 1);
  assert.equal(documentLoads, beforeUpdateLoads);
  updateApplyResponse = "normal";
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, sessions: sessions.length, writes: writes.length, screenshots: temporary }));
} catch (error) { console.error(JSON.stringify({ screenshots: temporary, errors, workspaceRequests: workspaceFixture.requests.slice(-15) }));
  await page.screenshot({ path: path.join(temporary, "failure.png") }); throw error;
} finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
