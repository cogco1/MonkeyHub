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
let updateStatus = { currentVersion: "fixture-current-desktop", currentRevision: "d".repeat(40), mode: "local", state: "idle", prepared: null, canApply: false, message: null, error: null,
  channel: "unsigned-prerelease", autoUpdate: true, nextLaunch: false, check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } };
let patchPolls = 0, appliedPatches = 0, updateApplyFailure = false;
// Automatic update checks move checking -> downloading -> ready on successive status reads.
const autoUpdateWrites = [];
let updateChecks = 0, checkPolls = 0;
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
// Keep chooser interception installed across keyboard passes. Repeatedly
// enabling it at keydown can race Chromium's native dialog cancellation.
page.on("filechooser", () => {});
const errors = [], writes = [], sessions = [], providerReads = [];
const monitorReads = [];
const monitorTokens = { input_tokens: 200, cached_input_tokens: 50, output_tokens: 30,
  cache_write_input_tokens: 0, cache_write_1h_input_tokens: 0, reasoning_output_tokens: 0 };
const monitorDay = new Date(Date.now() - 86_400_000).toISOString();
const monitorEvents = [
  ...Array.from({ length: 4 }, (_, i) => ({ event_id: `turn-${i}`, source: "codex", provider: "openai", model: "test",
    phase: "agent_turn", timing_scope: "agent_turn", model_call: null, status: "completed", started_at: monitorDay,
    tokens: Object.fromEntries(Object.keys(monitorTokens).map((key) => [key, null])) })),
  ...Array.from({ length: 37 }, (_, i) => ({ event_id: `call-${i}`, source: "codex", provider: "openai", model: "test",
    phase: "agent", model_call: true, status: "observed", started_at: monitorDay, tokens: monitorTokens })),
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
// GH-302: settings save themselves; a test holds one write to see what waits for it.
let settingsWriteGate = null;
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
// Fab uses the built Hub page so its settings button exercises the real iframe bridge.
Object.assign(apps.find((app) => app.appId === "monkeyfab"), { url: `${origin}/?view=fab`, apiUrl: `${origin}/` });
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
// #300: a summary says whether its conversation waits on the architect, read from its transcript as the Hub does.
const summaryOf = (session) => ({ ...session, attention: session.messages?.some((message) => message.permission) ? "permission" : null });
const runtimeSnapshot = () => ({ serverId: "fixture-hub", sequence: runtimeSequence, workers: [], projects: [...runtimes.values()].map((runtime) => {
  const app = appsFor(runtime.projectDir).find((app) => app.appId === "monkeyarch");
  return { ...runtime, workers: app.processId || app.state === "error" ? [{ workerId: runtime.runtimeId, serviceId: "studio", projectId: runtime.projectId,
    projectDir: runtime.projectDir, instanceId: `instance-${app.processId}`, processId: app.processId, desiredState: "running", healthy: app.state === "running",
    state: app.state === "error" ? "crashed" : app.state === "running" ? "ready" : app.state, url: app.apiUrl ?? app.url, error: app.error ?? null }] : [],
    sessions: sessions.filter((session) => session.projectId === runtime.projectId).map(summaryOf) };
}) });
// PP-1: while set, a project service asked to start stays "starting" until releaseStudioStarts().
let studioStartHold = null;
const runStudio = (target) => {
  const runtimeId = runtimes.get(target).runtimeId;
  for (const item of appsFor(target).filter((row) => row.serviceId === "studio")) {
    item.state = "running"; item.processId = 2000 + [...projectApps.keys()].indexOf(target);
    item.url = `${origin}/?view=${item.appId === "monkeyboard" ? "board" : item.appId === "monkeyrender" ? "render" : "arch"}&runtimeId=${runtimeId}`;
    item.apiUrl = `${origin}/api/runtime/projects/${runtimeId}/studio/`;
  }
};
const releaseStudioStarts = () => {
  const held = studioStartHold?.held ?? [];
  studioStartHold = null;
  for (const target of held) runStudio(target);
  emitRuntime();
};
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
    if (updateStatus.check.state === "checking" && ++checkPolls >= 1) updateStatus = { ...updateStatus, check: { ...updateStatus.check, state: "downloading", latestVersion: "0.1.6" } };
    else if (updateStatus.check.state === "downloading" && ++checkPolls >= 3) updateStatus = { ...updateStatus, state: "ready", canApply: true, nextLaunch: updateStatus.autoUpdate,
      prepared: { ...preparedPatch, releaseVersion: "0.1.6" }, check: { ...updateStatus.check, state: "ready", checkedAt: "2026-09-25T10:02:00+00:00" } };
    return json(updateStatus);
  }
  if (url.pathname === "/api/updates/check") {
    assert.equal(method, "POST"); updateChecks++; checkPolls = 0;
    updateStatus = { ...updateStatus, check: { ...updateStatus.check, state: "checking", detail: null } };
    return json(updateStatus, 202);
  }
  if (url.pathname === "/api/updates/settings") {
    assert.equal(method, "PUT"); autoUpdateWrites.push(data());
    updateStatus = { ...updateStatus, autoUpdate: data().autoUpdate, nextLaunch: updateStatus.state === "ready" && data().autoUpdate };
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
  if (url.pathname === "/api/settings/user") {
    if (method === "PUT") { const body = data(); if (settingsWriteGate) await settingsWriteGate; preferences = body; }
    return json(preferences);
  }
  if (url.pathname === "/api/apps") return json(url.searchParams.has("projectDir") ? appsFor(url.searchParams.get("projectDir")) : apps);
  if (method === "GET" && url.pathname === "/api/fab/profiles") return json({ h2s: {
    key: "h2s", label: "Fixture H2S", nominal_volume_mm: [300, 300, 300], usable_origin_mm: [0, 0, 0],
    usable_volume_mm: [290, 290, 290], notes: "Synthetic browser fixture", sources: [],
  } });
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
  const dismissal = url.pathname.match(/^\/api\/runtime\/operations\/([^/]+)\/acknowledge$/);
  if (dismissal) {
    assert.equal(method, "POST");
    const runtime = [...runtimes.values()].find((item) => item.runtimeId === data().runtimeId);
    assert.equal(data().projectId, runtime?.projectId, "a dismissal names its runtime and that runtime's project");
    const operation = runtime.operations.find((item) => item.operationId === decodeURIComponent(dismissal[1]));
    if (!operation) return json({ code: "OPERATION_NOT_FOUND", detail: "This project runtime has no operation with that id." }, 404);
    // GH-58: one that needs recovery can be dismissed only when the Hub marks it unrecoverable.
    if (!["failed", "stale"].includes(operation.status) && !(operation.status === "needs_recovery" && operation.recoverable === false)) {
      return json({ code: "OPERATION_NOT_ACKNOWLEDGEABLE",
        detail: "Only a failed or stale operation can be dismissed. An operation that needs recovery stays until it is recovered." }, 409);
    }
    operation.acknowledgedAt ??= new Date().toISOString();
    return json(operation);
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
    if (action === "start" && app.serviceId === "studio" && studioStartHold) {
      for (const item of currentApps.filter((row) => row.serviceId === "studio")) { item.state = "starting"; item.processId = 3000; item.url = null; item.apiUrl = null; }
      studioStartHold.held.push(url.searchParams.get("projectDir"));
      return json(app);
    }
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
    if (method === "GET") return json(sessions.filter((session) => Boolean(session.archived) === (url.searchParams.get("archived") === "true")).map(summaryOf));
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
      if (session.status === "running") {
        // #301: what the API does with a message sent while a turn runs.
        assert.equal(data().attachments, undefined, "an interjection carries no files");
        assert.equal(data().designContext, undefined, "an interjection carries no new design source");
        session.messages.push({ id: `u-${session.messages.length}`, role: "user", content: data().content, status: "complete",
          attachments: [], contextMode: "continue", interjection: "pending" });
        return json(session, 202);
      }
      await chatMessageResponseGate;
      const attachments = (data().attachments ?? []).map((file, index) => {
        const id = `attachment-${session.id}-${session.messages.length}-${index}`;
        uploadedAttachments.set(id, { ...file, sessionId: session.id });
        return { id, name: file.name, mimeType: file.mimeType, size: Buffer.from(file.data, "base64").length };
      });
      const stageHandoff = data().contextMode === "stage" && confirmedStageForChat
        && data().designContext?.sourceRunId === confirmedStageForChat.runId;
      // Saved times as the Hub writes them: the turn's calls follow its message.
      const turnStart = Date.now(), at = (seconds) => new Date(turnStart + seconds * 1000).toISOString();
      session.messages.push({ id: `u-${session.messages.length}`, role: "user", content: data().content, status: "complete", attachments, createdAt: at(0),
        contextMode: stageHandoff ? "stage" : data().contextMode === "project" ? "project" : "continue",
        ...(stageHandoff ? { confirmedStageRef: confirmedStageForChat.stageRef, confirmedStageLabel: confirmedStageForChat.label } : {}) });
      session.title = session.messages[0].content || session.messages[0].attachments?.[0]?.name; session.status = "running";
      // What the API saves while the CLI works: one row per MCP call, a failed
      // one, and the finished candidate that call reported.
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "streaming", createdAt: at(2),
        content: "studio_schema · GET /api/state · in_progress" });
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "failed", createdAt: at(20),
        content: "studio_request · POST /api/issue · failed\nHubFailure(422): This action is not exposed to the chat." });
      session.messages.push({ id: `t-${session.messages.length}`, role: "tool", status: "complete", createdAt: at(78),
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
// GH-302: Hub settings have no Save button; each change saves itself and the status line says Saved.
const settingsSaved = () => page.waitForFunction(() => document.querySelector("#settings-save-state")?.dataset.state === "saved");
// #328: Settings is paged; each page opens from the list beside it.
const settingsPage = (name) => page.getByRole("dialog").getByRole("tab", { name, exact: true }).click();
const boxOf = (selector) => page.evaluate((value) => {
  const node = document.querySelector(value);
  return node ? node.getBoundingClientRect().width : 0;
}, selector);
// #285: a turn's calls fold into one process row that states how many there were.
const activityRows = (expected) => page.waitForFunction((count) => [...document.querySelectorAll(".chat-process__row")]
  .some((row) => row.textContent.includes(`${count} steps`)), expected);
const visibleWorkspace = () => page.locator('.chat-project-workspace:not([hidden])');
// #285: the composer's + menu holds Add attachments and New topic; the composer
// names the design context the next message carries.
const composerMenu = (name = "Attachments and new topic") => page.getByRole("button", { name, exact: true });
const addAttachments = async (files) => {
  const chooser = page.waitForEvent("filechooser");
  await composerMenu().click();
  await page.getByRole("menuitem", { name: "Add attachments", exact: true }).click();
  await (await chooser).setFiles(files);
};
const contextReady = () => page.waitForFunction(() => document.querySelector(".chat-composer")?.dataset.context === "ready");
const waitWorkspace = async (kind = "arch") => {
  await visibleWorkspace().locator(`[data-project-surface="${kind}"]:not([hidden])`).waitFor();
  await visibleWorkspace().locator(kind === "board" ? ".monkeyboard-canvas canvas" : kind === "drawing" ? ".drawing-workspace" : kind === "render" ? ".render-workspace" : ".stage canvas").first().waitFor();
  assert.equal(await page.locator(".chat-project-workspace iframe").count(), 0, "project workspaces mount directly in the Hub");
};
/**
 * IA-6: pressing the rail entry on screen steps back, so a walk that only needs an entry on
 * screen presses it when it is not. A restore that lands just before the press makes it the
 * entry on screen, which the press then closes; one more press brings it back.
 */
const showEntry = async (name) => {
  const entry = page.getByRole("button", { name, exact: true });
  await page.waitForFunction((label) => {
    const node = document.querySelector(`.chat-rail__tool[aria-label="${label}"]`);
    return node && !node.disabled && !node.hasAttribute("aria-busy");
  }, name);
  if (await entry.getAttribute("aria-pressed") === "true") return;
  await entry.click();
  if (await page.locator(".chat-shell").getAttribute("data-panel") === "false") await entry.click();
};
/** The architect opens a result read-only from Modeling's Versions: results never open themselves (#302). */
const viewCandidate = async (runId) => {
  await showEntry("Modeling");
  await waitWorkspace();
  await page.waitForFunction(() => !document.querySelector('.chat-project-workspace:not([hidden]) .boot'));
  const toggle = visibleWorkspace().locator(".stage__versions-toggle");
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  await visibleWorkspace().locator(".vcard__export").filter({ hasText: `${runId}.3dm` }).first().click();
  await toggle.click();
  await waitCandidate(runId);
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
// GH-234: model edits the project's working draft already holds come back after
// an update restart, so they never block "Restart to update"; edits whose
// autosave is still being written, or was refused, do. Either way chat still
// asks for them to be recorded (#302: Record, formerly Sync) before it starts from project state.
const autosavedModelRestart = async () => {
  // Explicit fixture reset: no conversation is running, and the reload below
  // empties every composer, so only the model edits can hold the restart.
  for (const session of sessions) if (session.status === "running") session.status = "interrupted";
  projects.push({ projectId: "D", projectDir: "D:\\fixture\\D", name: "Project D", chatCount: 0, version: 0, stage: null });
  // What autosave retained seconds before the update: a drawn mass and two push/pulls.
  const commands = [
    { kind: "sketch", elementId: "local-mass", componentId: "fixture-mass", action: { profile: [[0, 0], [3, 0], [3, 2], [0, 2]], height: 1.5, base: 0 } },
    { kind: "direct", elementId: "local-mass", action: { kind: "pushPull", distance: 0.5, normal: [0, 0, 1] } },
    { kind: "direct", elementId: "local-mass", action: { kind: "pushPull", distance: 0.25, normal: [0, 0, 1] } },
  ];
  const draft = { revisionSha256: "a".repeat(64), current: null, writes: [], hold: null, failure: null, localDraft: {
    source: { projectId: "D", stateDigest: workspaceFixture.stateDigestOf("D", "home-D"), sourceRunId: "home-D", sourceStageRef: null },
    commands, attempt: { syncedCommands: [], pending: null }, updatedAt: "2026-09-25T00:00:00Z" } };
  workspaceFixture.workingDrafts.set("D", draft);
  updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
  const until = async (done, message) => {
    for (const end = Date.now() + 12000; !done(); await page.waitForTimeout(50)) if (Date.now() > end) assert.fail(message);
  };
  const blocker = "A model draft is still being saved. Wait a moment before restarting.";
  const restart = page.getByRole("button", { name: "Restart to update", exact: true });
  const openSettings = async () => { await page.getByRole("button", { name: "Hub settings", exact: true }).click(); await settingsPage("Software update"); };
  const closeSettings = () => page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  const restartAllowed = async (message) => {
    await page.waitForFunction(() => [...document.querySelectorAll("button")].find((button) => button.textContent === "Restart to update")?.disabled === false);
    assert.equal(await page.locator("#software-update-blocker").count(), 0, message);
  };
  const restartBlocked = async (message) => {
    await page.getByText(blocker, { exact: true }).waitFor();
    assert.equal(await restart.isDisabled(), true, message);
  };

  await page.evaluate(() => localStorage.removeItem("monkeyhub.chat-view.v1"));
  await page.reload();
  await page.getByRole("button", { name: "Project D", exact: true }).first().click();
  await studioReady();
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  const status = visibleWorkspace().locator(".model-tools__sync-status");
  await status.filter({ hasText: /^Unrecorded edits · saved automatically$/ }).waitFor();
  // Reopening restored the retained commands and saved them back unchanged.
  await until(() => draft.writes.length === 1, "the restored draft was never retained again");
  assert.deepEqual(draft.writes[0].draft.commands, commands);
  assert.deepEqual(draft.writes[0].draft.source, draft.localDraft.source);
  assert.equal(await visibleWorkspace().getByRole("button", { name: "Record", exact: true }).isEnabled(), true);
  // Restored edits are still not a candidate: a New topic asks for them to be recorded first,
  // and offers the one click that does it (#302) instead of a dead end.
  await page.waitForFunction(() => document.querySelector(".chat-composer")?.dataset.context === "unsynced");
  await composerMenu().click();
  const newTopic = page.getByRole("menuitemcheckbox", { name: "New topic", exact: true });
  assert.equal(await newTopic.getAttribute("aria-description"), "Model edits are not recorded yet. Record them to start a new context from project state, or undo them in Modeling. You can still continue this conversation.");
  await newTopic.click();
  const recordOffer = page.locator(".chat-composer .chat-record");
  assert.equal(await recordOffer.innerText(), "Record edits and continue", "the refused New topic offers Record edits and continue");
  assert.equal(await page.locator(".chat-composer [role=status]").filter({ hasText: "Model edits are not recorded yet." }).count(), 1);
  await page.getByRole("button", { name: "Remove New topic", exact: true }).click();
  await recordOffer.waitFor({ state: "detached" });
  await openSettings();
  await restartAllowed("edits the working draft already holds do not block the update restart");
  await closeSettings();

  // An autosave still being written holds the restart until it lands.
  let releaseSave;
  draft.hold = new Promise((resolve) => { releaseSave = resolve; });
  await visibleWorkspace().getByRole("button", { name: "Undo model", exact: true }).click();
  await until(() => draft.writes.length === 2, "undoing a push/pull was not autosaved");
  await openSettings();
  await restartBlocked("an autosave still being written blocks the update restart");
  releaseSave();
  await page.getByText(blocker, { exact: true }).waitFor({ state: "hidden" });
  await restartAllowed("the landed autosave releases the update restart");
  assert.deepEqual(draft.localDraft.commands, commands.slice(0, 2));
  await closeSettings();

  // A refused autosave holds it too, and the next accepted save releases it.
  draft.failure = "Fixture working draft is unavailable.";
  await visibleWorkspace().getByRole("button", { name: "Redo model", exact: true }).click();
  await status.filter({ hasText: "Fixture working draft is unavailable." }).waitFor();
  await openSettings();
  await restartBlocked("a refused autosave blocks the update restart");
  await closeSettings();
  draft.failure = null;
  await visibleWorkspace().getByRole("button", { name: "Undo model", exact: true }).click();
  await visibleWorkspace().getByRole("button", { name: "Redo model", exact: true }).click();
  await until(() => draft.localDraft.commands.length === commands.length, "the redone push/pull was not autosaved");
  await status.filter({ hasText: /^Unrecorded edits · saved automatically$/ }).waitFor();
  assert.deepEqual(draft.localDraft.commands, commands);
  await openSettings();
  await restartAllowed("the next accepted autosave releases the update restart");
  assert.equal(appliedPatches, 0, "checking the restart never applies the patch");
  await closeSettings();
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
  await page.evaluate(() => localStorage.removeItem("monkeyhub.chat-view.v1"));
};
try {
  if (process.env.MONKEYHUB_UI_FOCUS === "accessibility") {
    const cdp = await page.context().newCDPSession(page);
    const accessible = async (role, name) => {
      const { nodes } = await cdp.send("Accessibility.getFullAXTree");
      const node = nodes.find((entry) => !entry.ignored && entry.role?.value === role && entry.name?.value === name);
      assert.ok(node, `${role} ${name} is present in the browser accessibility tree`);
      return node;
    };
    const focused = async (locator) => assert.equal(await locator.evaluate((node) => node === document.activeElement), true);
    for (const language of ["en", "zh-CN"]) {
      preferences = { ...preferences, language };
      await page.goto(origin);
      await page.waitForFunction(() => document.querySelector("#chat-input") && !document.querySelector("#chat-input").disabled);
      const names = language === "en" ? {
        archive: "Archived chats", settings: "Hub settings", project: "This project: Project A", close: "Close",
        menu: "Attachments and new topic", topic: "New topic", send: "Send", empty: "Write a message or add an attachment first.",
      } : {
        archive: "已归档对话", settings: "Hub 设置", project: "此项目: Project A", close: "关闭",
        menu: "附件与新话题", topic: "新话题", send: "发送", empty: "请先输入消息或添加附件。",
      };
      const sidebarToggle = page.locator(".chat-sidebar__top button");
      if (await page.locator(".chat-shell").getAttribute("data-sidebar") === "true") await sidebarToggle.click();
      for (const name of [names.archive, names.settings]) {
        const button = page.getByRole("button", { name, exact: true });
        await button.focus(); await focused(button);
        await accessible("button", name);
      }
      await page.getByRole("button", { name: names.archive, exact: true }).press("Enter");
      const active = page.locator(".chat-archive-toggle");
      assert.equal(await active.getAttribute("aria-pressed"), "true");
      await accessible("button", await active.getAttribute("aria-label"));
      await active.press("Enter");
      await page.getByRole("button", { name: names.settings, exact: true }).press("Enter");
      await page.locator(".chat-dialog--settings").waitFor({ state: "visible" });
      await page.keyboard.press("Escape");
      await page.locator(".chat-dialog--settings").waitFor({ state: "hidden" });

      const projectButton = page.getByRole("button", { name: names.project, exact: true });
      const projectName = await accessible("button", names.project);
      assert.equal(projectName.name.value.includes(projects[0].projectDir), false);
      await projectButton.focus(); await projectButton.press("Enter");
      const close = page.locator(".chat-project-card__head").getByRole("button", { name: names.close, exact: true });
      await focused(close);
      assert.equal(await projectButton.getAttribute("aria-controls"), "chat-project-info");
      await page.keyboard.press("Escape");
      assert.equal(await page.locator(".chat-project-card").count(), 0);
      await focused(projectButton);
      // Escape also closes after Tab leaves the card, or focus returns to its trigger.
      await projectButton.press("Enter");
      await page.keyboard.press("Tab");
      await page.keyboard.press("Escape");
      await focused(projectButton);
      await projectButton.press("Enter");
      await projectButton.focus(); await page.keyboard.press("Escape");
      await focused(projectButton);
      await projectButton.press("Enter");
      await close.press("Enter"); await focused(projectButton);
      // A modal launched from this nonmodal card owns the first Escape.
      await projectButton.press("Enter");
      await page.locator(".chat-project-card__archive button").first().click();
      await page.locator("dialog.chat-dialog--archive").first().waitFor({ state: "visible" });
      await page.keyboard.press("Escape");
      await page.locator("dialog.chat-dialog--archive").first().waitFor({ state: "hidden" });
      assert.equal(await page.locator(".chat-project-card").count(), 1);
      await page.keyboard.press("Escape"); await focused(projectButton);

      const send = page.getByRole("button", { name: names.send, exact: true });
      assert.equal(await send.isDisabled(), true);
      assert.equal((await accessible("button", names.send)).description.value, names.empty);
      assert.equal(await send.getAttribute("title"), names.empty);
      assert.equal(await page.locator("#chat-input").getAttribute("aria-keyshortcuts"), "Enter Shift+Enter");
      assert.equal((await accessible("button", names.send)).properties.find((property) => property.name === "keyshortcuts").value.value, "Enter");
      await page.locator("#chat-input").fill("First line");
      await page.locator("#chat-input").press("Shift+Enter");
      assert.equal(await page.locator("#chat-input").inputValue(), "First line\n");
      assert.equal(writes.filter(([, route]) => route.endsWith("/messages")).length, 0);
      await page.locator("#chat-input").fill("");
      const menu = composerMenu(names.menu);
      await menu.focus(); await menu.press("Enter");
      await page.keyboard.press("ArrowDown");
      const topic = page.getByRole("menuitemcheckbox", { name: names.topic, exact: true });
      await focused(topic);
      const topicAX = await accessible("menuitemcheckbox", names.topic);
      assert.equal(topicAX.properties.find((property) => property.name === "checked").value.value, "false");
      assert.ok(topicAX.description.value.length > 0);
      await page.keyboard.press("Escape"); await focused(menu);
      await sidebarToggle.click();
    }
    preferences = { ...preferences, language: "en" };
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector("#chat-input") && !document.querySelector("#chat-input").disabled);
    await page.locator("#chat-input").fill("Check keyboard sending");
    await page.locator("#chat-input").press("Enter");
    await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
    assert.equal(writes.filter(([, route]) => route.endsWith("/messages")).length, 1, "Enter sends once");
    assert.equal((await accessible("combobox", "Model")).description.value, "The model can be changed once this reply finishes.");
    assert.equal((await accessible("button", "Interject")).description.value, "Write a message to interject first.");
    const archive = page.locator(".chat-thread-action").first();
    assert.equal(await archive.isDisabled(), true);
    assert.ok((await accessible("button", await archive.getAttribute("aria-label"))).description.value.length > 0);
    await composerMenu().click();
    const blockedTopic = page.getByRole("menuitemcheckbox", { name: "New topic", exact: true });
    assert.equal((await accessible("menuitemcheckbox", "New topic")).description.value, "Wait for this reply to finish before starting a new topic.");
    await blockedTopic.focus(); await page.keyboard.press("Enter");
    assert.equal(await blockedTopic.getAttribute("aria-checked"), "false", "the disabled choice does not activate by keyboard");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Stop", exact: true }).click();

    projects.splice(0); sessions.splice(0); settings.projectDir = null;
    await page.evaluate(() => { localStorage.removeItem("monkeyhub.chat-view.v1"); localStorage.removeItem("monkeyhub.chat-drafts.v1"); });
    await page.goto(origin);
    await page.getByRole("button", { name: "This project", exact: true }).waitFor();
    for (const name of ["Send", "Attachments and new topic", "Modeling", "Board", "Drawings", "Render", "Design tree"]) {
      const button = page.getByRole("button", { name, exact: true });
      assert.equal(await button.isDisabled(), true);
      assert.equal((await accessible("button", name)).description.value, "Add or choose a project first.");
    }
    await cdp.detach();
  } else if (process.env.MONKEYHUB_UI_FOCUS === "attachments") {
    const restoredFocus = [];
    for (const theme of ["dark", "light"]) {
      for (const fontScale of [0.9, 1, 1.1]) {
        preferences = { ...preferences, theme, fontScale };
        await page.goto(origin);
        await page.waitForFunction(() => document.querySelector("#chat-input") && !document.querySelector("#chat-input").disabled);
        if (await page.getByRole("button", { name: "Hide projects", exact: true }).first().isVisible()) {
          await page.getByRole("button", { name: "Hide projects", exact: true }).first().click();
        }
        for (const width of [1440, 900, 375]) {
          await page.setViewportSize({ width, height: 960 });
          const composer = page.locator(".chat-composer");
          const attach = composerMenu();
          const type = await page.evaluate(() => {
            const read = (selector) => { const style = getComputedStyle(document.querySelector(selector)); return { size: parseFloat(style.fontSize), weight: style.fontWeight, family: style.fontFamily }; };
            return { ui: ["#chat-input", ".chat-connection__name", "#chat-model"].map(read) };
          });
          assert.ok(type.ui.every((text) => Math.abs(text.size - 14 * fontScale) < 0.05 && text.family === type.ui[0].family && text.weight === "400"), `${theme}/${width}/${fontScale}: equal-role text follows the chosen size together: ${JSON.stringify(type)}`);
          assert.equal(await composer.locator(":scope > .chat-muted").count(), 0);
          await composer.screenshot({ path: path.join(temporary, `composer-${theme}-${width}-${fontScale}.png`) });
          await attach.focus();
          await page.keyboard.press("Tab");
          await page.keyboard.press("Shift+Tab");
          assert.ok(await attach.evaluate((node) => node === document.activeElement && node.matches(":focus-visible") && getComputedStyle(node).outlineStyle !== "none"));
          const chooser = page.waitForEvent("filechooser");
          await page.keyboard.press("Enter");
          // The + menu opens on its first entry, Add attachments.
          await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Add attachments");
          await page.keyboard.press("Enter");
          await (await chooser).setFiles([
            { name: "roof-section-review-with-a-very-long-file-name.pdf", mimeType: "application/pdf", buffer: Buffer.from("Synthetic attachment") },
            { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Review notes") },
          ]);
          assert.equal(await composer.locator(".chat-attachments li").count(), 2);
          assert.equal(await composer.locator(".chat-attachment__name").first().getAttribute("title"), "roof-section-review-with-a-very-long-file-name.pdf");
          const layout = await composer.evaluate((node) => {
            const bounds = node.getBoundingClientRect();
            const tools = [...node.querySelectorAll(".chat-composer__bottom button, .chat-composer__bottom select")];
            return { overflow: node.scrollWidth > node.clientWidth + 1,
              clipped: tools.some((tool) => { const b = tool.getBoundingClientRect(); return b.left < bounds.left || b.right > bounds.right; }),
              attachmentOverflow: [...node.querySelectorAll(".chat-attachments, .chat-attachments li")].some((item) => item.scrollWidth > item.clientWidth + 1) };
          });
          assert.deepEqual(layout, { overflow: false, clipped: false, attachmentOverflow: false }, `${theme} at ${width}px`);
          await composer.screenshot({ path: path.join(temporary, `attachments-${theme}-${width}-${fontScale}.png`) });
          const remove = page.getByRole("button", { name: "Remove attachment: notes.txt", exact: true });
          await remove.focus();
          await page.keyboard.press("Enter");
          assert.equal(await composer.locator(".chat-attachments li").count(), 1);
          restoredFocus.push(await page.locator("#chat-input").evaluate((node) => node === document.activeElement));
          await composer.getByRole("button", { name: "Remove attachment: roof-section-review-with-a-very-long-file-name.pdf", exact: true }).click();
          assert.equal(await composer.locator(".chat-attachments li").count(), 0);
        }
      }
    }
    assert.ok(restoredFocus.every(Boolean), "removing an attachment returns keyboard focus to the draft");
    assert.equal(writes.filter(([, route]) => route.endsWith("/messages")).length, 0, "picking and removing attachments never sends a message");
    preferences = { ...preferences, language: "zh-CN", theme: "dark", fontScale: 1 };
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.goto(origin);
    await composerMenu("附件与新话题").waitFor();
    await page.locator(".chat-composer").screenshot({ path: path.join(temporary, "composer-zh.png") });
  } else {
  if (process.env.MONKEYHUB_UI_FOCUS !== "updates") {
  await page.goto(origin);
  await page.getByRole("heading", { name: "Start a project conversation" }).waitFor();
  assert.equal(await page.getByRole("link", { name: "Enter workspace" }).count(), 0);

  // A — one vertical rail on the far right holds every tool entry, and there
  // is no second copy of them anywhere.
  const rail = page.getByRole("navigation", { name: "Project tools" });
  await rail.waitFor();
  for (const label of ["Modeling", "Drawings", "Render", "Board", "Fabrication", "Design tree", "Usage"]) {
    assert.equal(await page.getByRole("button", { name: label, exact: true }).count(), 1, `${label} appears once`);
  }
  // #295, regrouped for #300, with #284: the project's surfaces are Modeling,
  // Board and the Design tree; everything that produces output over the project,
  // or reports on the machine, is a Tool. Layout is Board's mode and has no rail entry.
  const railEntries = (group) => rail.getByRole("group", { name: group, exact: true }).locator(".chat-rail__tool")
    .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label")));
  assert.deepEqual(await railEntries("Surfaces"), ["Modeling", "Board", "Design tree"], "the surfaces are Modeling, Board and the Design tree");
  assert.deepEqual(await railEntries("Tools"), ["Drawings", "Render", "Fabrication", "Usage"], "output tools and Usage share Tools");
  assert.equal(await rail.getByRole("button", { name: /Publish|Layout/ }).count(), 0, "Layout is not a rail entry");
  assert.ok(await railWidth() > 40, "the rail stays on screen while the tool content is closed");
  assert.equal(await page.locator(".chat-browser:visible").count(), 0);
  await contextReady();
  assert.equal(await page.locator(".chat-composer .chat-target").innerText(), "Changes: Current",
    "DC-9: without a Design Tree the composer still names what a message changes");
  assert.equal(await page.locator(".stage canvas").count(), 0, "reading initial project context does not initialize a hidden viewport");
  assert.equal(workspaceFixture.requests.some((row) => row.name.endsWith("/bytes")), false,
    "initial chat reads its editing state without loading model files");
  await studioReady();
  assert.equal(await page.locator('.chat-rail__tool[data-state="running"] small').count(), 0,
    "ready workspaces show their names without redundant availability labels");
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
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("view=fab"));
  const fabFrame = page.frameLocator('iframe:not([hidden])');
  await fabFrame.locator('#fab-printer option[value="h2s"]').waitFor({ state: "attached" });
  assert.equal(await fabFrame.locator('#fab-printer option[value="h2s"]').innerText(), "Fixture H2S");
  assert.equal(await fabFrame.locator("#fab-source").isEnabled(), true, "the real Fab page loaded its printer profiles from the API fixture");
  await fabFrame.getByRole("button", { name: "Display settings", exact: true }).click();
  const hubSettings = page.getByRole("dialog").filter({ hasText: "Hub settings (global)" });
  await hubSettings.waitFor();
  assert.equal(await hubSettings.getByRole("tab", { name: "Display", exact: true }).getAttribute("aria-selected"), "true",
    "Fab opens the existing Hub Display settings page");
  assert.equal(await fabFrame.getByRole("heading", { name: "MonkeyFab", exact: true }).count(), 1,
    "opening settings leaves the hosted Fab page mounted instead of loading a second Hub in its iframe");
  await hubSettings.getByRole("button", { name: "Close", exact: true }).click();
  assert.ok(!writes.some(([, pathname]) => pathname === "/api/project/modeling"),
    "new-project creation and independent tools leave modeling inputs alone");
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  const beforeIndependent = writes.length;
  const beforeIndependentProject = settings.projectDir;
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("view=fab"));
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
  assert.deepEqual(await page.locator('.chat-rail__group[aria-label="工作面"] .chat-rail__tool').evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("aria-label"))), ["建模", "画板", "状态树"], "the Surfaces group is named in the Chinese catalog too");
  assert.deepEqual(await page.locator('.chat-rail__group[aria-label="工具"] .chat-rail__tool').evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("aria-label"))), ["图纸", "渲染", "制作", "用量"], "the Tools group is named in the Chinese catalog too");
  assert.equal(await page.locator(".chat-rail").getByRole("button", { name: "排版", exact: true }).count(), 0, "no rail 排版");
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

  // #302: the architect is marking up on Board when the Agent's result arrives.
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  const surfaceNow = () => page.evaluate(() => ({ panel: document.querySelector(".chat-shell")?.dataset.panel,
    pressed: [...document.querySelectorAll(".chat-rail__tool[aria-pressed=true]")].map((node) => node.getAttribute("aria-label")) }));
  const surfaceBeforeResult = await surfaceNow();
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

  // #301: while a turn runs the composer stays usable. A message sent now is an
  // interjection: it reaches the API as text alone, appears at once with its
  // label, and Stop stays beside it as the secondary action.
  const steered = sessions.find((row) => row.messages.some((message) => message.content === "Widen the courtyard"));
  const composerInput = page.getByRole("textbox", { name: "What would you like to do in this project?" });
  assert.equal(await composerInput.isEnabled(), true, "the composer stays usable while the Agent works");
  assert.equal(await page.getByRole("button", { name: "Stop", exact: true }).isVisible(), true);
  assert.equal(await page.getByRole("button", { name: "Interject", exact: true }).isDisabled(), true, "an empty draft interjects nothing");
  const messagesBefore = writes.filter(([, pathname]) => pathname.endsWith("/messages")).length;
  await composerInput.fill("Keep the courtyard square instead");
  await composerInput.press("Enter");
  const interjected = page.locator(".chat-message--user").filter({ hasText: "Keep the courtyard square instead" });
  await interjected.getByText("Interjected · Delivers at the next step", { exact: true }).waitFor();
  const interjectionWrites = writes.filter(([, pathname]) => pathname.endsWith("/messages"));
  assert.equal(interjectionWrites.length, messagesBefore + 1);
  assert.deepEqual(interjectionWrites.at(-1).slice(0, 3), ["POST", `/api/chat/sessions/${steered.id}/messages`,
    { content: "Keep the courtyard square instead", projectId: steered.projectId }]);
  assert.equal(await composerInput.inputValue(), "", "the composer clears and stays open for the next message");
  assert.equal(await composerInput.isEnabled(), true);
  assert.equal(steered.status, "running", "interjecting never stops the turn");
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  steered.messages.find((message) => message.content === "Keep the courtyard square instead").interjection = "delivered";
  emitRuntime();
  await interjected.getByText("Interjected · Delivered", { exact: true }).waitFor();

  // #285: the turn's calls fold into one row that says what it is doing now in
  // plain words; the failed call is counted, not hidden, and the raw request
  // lines are technical detail under that row.
  await activityRows(3);
  // #302: the result the turn read back takes nothing on screen. The request's one
  // Study card lists it instead of a button per result.
  const study = page.locator(".chat-study");
  await study.waitFor();
  await page.waitForTimeout(600);
  assert.equal(await study.count(), 1, "one Study card for the request");
  assert.equal(await study.getAttribute("data-candidates"), "cand-A-1");
  assert.equal(await study.locator(".chat-study__text").innerText(), "This request · 1 option ready");
  assert.equal(await page.locator(".chat-activity__result").count(), 0, "no per-result button remains");
  assert.deepEqual(await surfaceNow(), surfaceBeforeResult, "a result arriving never switches the surface or the panel");
  await visibleWorkspace().locator('[data-project-surface="board"]:not([hidden])').waitFor();
  assert.ok(!workspaceFixture.requests.some((row) => row.name.endsWith("/bytes") && row.runId === "cand-A-1"),
    "a result arriving never loads its model on its own");
  await page.screenshot({ path: path.join(temporary, "result-keeps-board.png") });
  const firstProcess = page.locator(".chat-process").last();
  assert.equal(await page.locator(".chat-process").count(), 1, "one process row for the turn");
  await firstProcess.locator(".chat-process__current").filter({ hasText: "Prepare to read the model state" }).waitFor();
  assert.match((await firstProcess.locator(".chat-process__row").innerText()).replace(/\s+/g, " "),
    /^Working · \d+:\d{2} · Prepare to read the model state… · 3 steps · 1 failed$/);
  assert.equal(await firstProcess.locator(".chat-process__row").getAttribute("aria-expanded"), "false", "a running turn stays folded");
  assert.equal(await page.getByText("studio_request · POST /api/issue · failed").count(), 0, "no raw request line in the conversation");
  await firstProcess.locator(".chat-process__row").click();
  assert.deepEqual(await firstProcess.locator(".chat-process__steps li > span:last-child").allInnerTexts(),
    ["Prepare to read the model state", "Update project data · failed", "Check progress"]);
  assert.equal(await page.getByText("HubFailure(422): This action is not exposed to the chat.").isVisible(), false);
  await firstProcess.locator(".chat-process__technical summary").click();
  await firstProcess.locator(".chat-process__technical").getByText("studio_request · POST /api/issue · failed", { exact: true }).waitFor();
  await page.getByText("HubFailure(422): This action is not exposed to the chat.").waitFor();
  await firstProcess.locator(".chat-process__row").click();
  assert.equal(await firstProcess.locator(".chat-process__body").count(), 0, "folding leaves nothing behind");

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
  // FN-2: the waiting conversation's row says so in words and colour; its project row carries a smaller
  // mark in place of Running. Both are on buttons a keyboard reaches, whose descriptions say it aloud.
  const waitingThread = page.locator(".chat-thread-row").filter({ hasText: "Widen the courtyard" });
  await waitingThread.locator(".chat-needs").filter({ hasText: /^Needs you$/ }).waitFor();
  assert.equal(await waitingThread.locator(".chat-thread").getAttribute("aria-description"), "Needs your permission");
  const projectAHead = page.locator(".chat-project__head").filter({ has: page.getByRole("button", { name: "Project A", exact: true }) });
  assert.equal(await projectAHead.locator('.chat-project__badge[data-kind="needs"]').innerText(), "Needs you");
  assert.equal(await projectAHead.locator('.chat-project__badge[data-kind="running"]').count(), 0, "the mark stands in for Running");
  assert.match(await projectAHead.getByRole("button", { name: "Project A", exact: true }).getAttribute("aria-description"), /1 chat needs you$/);
  await waitingThread.locator(".chat-thread").focus();
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-description")), "Needs your permission");
  assert.equal(await page.locator(".chat-thread-row[data-attention]").count(), 1, "only the conversation that asked is marked");
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
  // Answered, the marks leave with the request.
  await waitingThread.locator(".chat-needs").waitFor({ state: "detached" });
  await projectAHead.locator('.chat-project__badge[data-kind="needs"]').waitFor({ state: "detached" });

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

  // The Study card's View opens the Design Tree on the request's options; the
  // option itself opens read-only when the architect chooses it.
  await study.getByRole("button", { name: "View", exact: true }).click();
  await visibleWorkspace().locator('[data-project-surface="tree"]:not([hidden])').waitFor();
  assert.equal(await page.getByRole("button", { name: "Design tree", exact: true }).getAttribute("aria-pressed"), "true");
  assert.equal(new URL(page.url()).searchParams.get("view"), "tree");
  await viewCandidate("cand-A-1");
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
  // #295: Drawing opens over the surface already shown, in this same mounted
  // project workspace, and reads this project's Working Head. Pressing it again
  // leaves it for that surface, with the editing base and the Board unchanged.
  const runtimeA = runtimes.get("D:\\fixture\\A").runtimeId;
  const drawingTool = page.getByRole("button", { name: "Drawings", exact: true });
  const editingBase = async () => [(await visibleWorkspace().locator(".editing-base__name").innerText()).trim(),
    await visibleWorkspace().locator(".editing-base").getAttribute("data-source-match")];
  const baseBeforeDrawing = await editingBase();
  for (const [label, kind] of [["Board", "board"], ["Modeling", "arch"]]) {
    const surface = page.getByRole("button", { name: label, exact: true });
    await surface.click();
    await waitWorkspace(kind);
    const readsBefore = workspaceFixture.requests.length;
    await drawingTool.click();
    await waitWorkspace("drawing");
    assert.equal(await drawingTool.getAttribute("aria-pressed"), "true");
    assert.equal(await surface.getAttribute("aria-pressed"), "false");
    assert.equal(await drawingTool.getAttribute("title"), `Close Drawings and return to ${label}`);
    assert.equal(new URL(page.url()).searchParams.get("runtimeId"), runtimeA, "Drawing stays on this project's runtime");
    assert.equal(await page.locator(".chat-header__project").innerText(), "Project A");
    assert.equal(await visibleWorkspace().evaluate((element) => element.switchMarker), "retained",
      "Drawing is the same mounted project workspace, not a new project context");
    const headReads = () => workspaceFixture.requests.slice(readsBefore)
      .filter((row) => row.name === "/api/working-source" && row.query.workspace === "drawing");
    for (const until = Date.now() + 5000; !headReads().length;) {
      if (Date.now() > until) assert.fail("Drawing did not read the project's Working Head");
      await page.waitForTimeout(50);
    }
    assert.ok(headReads().every((row) => row.runtimeId === runtimeA && row.projectId === "A"),
      "Drawing reads the Working Head of this same project");
    await drawingTool.click();
    await waitWorkspace(kind);
    assert.equal(await surface.getAttribute("aria-pressed"), "true", `leaving Drawing returns to ${label}`);
    assert.equal(await drawingTool.getAttribute("aria-pressed"), "false");
    assert.equal(new URL(page.url()).searchParams.get("view"), kind);
    assert.equal(new URL(page.url()).searchParams.get("runtimeId"), runtimeA);
    if (kind === "board") assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board A retained");
  }
  assert.deepEqual(await editingBase(), baseBeforeDrawing, "visiting Drawing leaves the editing base and the viewed model as they were");
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

  // IA-6: pressing the entry on screen steps back one level. A surface leaves the panel and keeps its
  // workspace; a Tool returns to the surface it was opened over, or leaves the panel when it was
  // opened from the conversation. The keyboard does the same, and says what a press will do.
  const entry = (name) => page.getByRole("button", { name, exact: true });
  const panelClosed = () => page.waitForFunction(() => document.querySelector(".chat-shell")?.dataset.panel === "false");
  const treeShown = () => visibleWorkspace().locator('[data-project-surface="tree"]:not([hidden])').waitFor();
  assert.equal(await entry("Modeling").getAttribute("aria-description"), "Close Modeling");
  await entry("Modeling").click();
  await panelClosed();
  assert.equal(await entry("Modeling").getAttribute("aria-pressed"), "false");
  assert.equal(await entry("Modeling").getAttribute("aria-description"), null);
  assert.equal(await page.locator(".chat-project-workspace").count(), mountedProjects, "stepping out keeps the workspace");
  await entry("Modeling").click();
  await waitWorkspace();
  assert.equal(await visibleWorkspace().evaluate((element) => element.collapseMarker), "retained");
  for (const [label, shownNow] of [["Design tree", treeShown], ["Board", () => waitWorkspace("board")]]) {
    await entry(label).click();
    await shownNow();
    await entry(label).focus();
    await page.keyboard.press("Enter");
    await panelClosed();
    assert.equal(await entry(label).getAttribute("aria-pressed"), "false", `${label} steps out of the panel`);
    assert.ok(await entry(label).evaluate((node) => node === document.activeElement), "focus stays on the entry");
    await page.keyboard.press("Enter");
    await shownNow();
    assert.equal(await entry(label).getAttribute("aria-pressed"), "true", `${label} comes back from the keyboard`);
  }
  // Board is on screen: Usage over it returns to it.
  await entry("Usage").click();
  await waitMonitor();
  assert.equal(await entry("Usage").getAttribute("aria-description"), "Close Usage and return to Board");
  assert.equal(await entry("Usage").getAttribute("title"), "Close Usage and return to Board");
  await entry("Usage").focus();
  await page.keyboard.press("Space");
  await waitWorkspace("board");
  assert.equal(await entry("Board").getAttribute("aria-pressed"), "true");
  assert.equal(await entry("Usage").getAttribute("aria-pressed"), "false");
  // A Tool opened over another Tool returns to the surface under both.
  await entry("Drawings").click();
  await waitWorkspace("drawing");
  await entry("Fabrication").click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("view=fab"));
  assert.equal(await entry("Fabrication").getAttribute("aria-description"), "Close Fabrication and return to Board");
  await entry("Fabrication").click();
  await waitWorkspace("board");
  assert.equal(await entry("Board").getAttribute("aria-pressed"), "true");
  // Opened from the conversation, a Tool leaves the panel again.
  await entry("Board").click();
  await panelClosed();
  await entry("Usage").click();
  await waitMonitor();
  assert.equal(await entry("Usage").getAttribute("aria-description"), "Close Usage");
  await entry("Usage").click();
  await panelClosed();
  assert.equal(await entry("Usage").getAttribute("aria-pressed"), "false");
  await entry("Modeling").click();
  await waitWorkspace();

  // C — the project gear answers for the bound project, not for the tools.
  await page.getByRole("button", { name: /Project A/ }).last().click();
  const card = page.getByRole("dialog", { name: "Project" });
  await card.waitFor();
  await card.getByText("D:\\fixture\\A").waitFor();
  await card.getByText("Version 3").waitFor();
  await card.getByText("S2", { exact: true }).waitFor();
  // #271: the card speaks about the current project and its work, never candidate ids.
  await card.getByText("Modeling follows the current project").waitFor();
  assert.equal(await card.getByText("cand-A-1").count(), 0, "candidate ids stay internal to the Worktree Graph");
  assert.equal(await card.getByRole("button", { name: /Accept|Issue|Endorse/ }).count(), 0);
  // #302: the card's work lines retired behind one link to the Design Tree, the one history entry.
  assert.equal(await card.getByText(/Finished result|can be combined/).count(), 0, "no second list of work beside the Design Tree");
  await card.getByRole("button", { name: "Open in Design tree", exact: true }).click();
  await card.waitFor({ state: "detached" });
  await visibleWorkspace().locator('[data-project-surface="tree"]:not([hidden])').waitFor();
  assert.equal(await page.getByRole("button", { name: "Design tree", exact: true }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();

  // The saved frame comes back after a reload, with the conversation.
  const savedWidth = await boxOf(".chat-browser");
  await page.reload();
  await activityRows(3);
  assert.equal(await page.locator('.chat-process__row[aria-expanded="true"]').count(), 0, "a reloaded chat shows its turns folded");
  await page.locator(".chat-browser").waitFor();
  assert.ok(Math.abs(await boxOf(".chat-browser") - savedWidth) < 12, "the panel width is restored");

  // #337: the menu row. File opens from the keyboard and names what it does; Escape
  // returns to the word; View changes the theme through the same saved preferences.
  const fileWord = page.getByRole("menuitem", { name: "File", exact: true });
  await fileWord.focus();
  await page.keyboard.press("ArrowDown");
  const fileMenu = page.getByRole("menu", { name: "File", exact: true });
  assert.deepEqual((await fileMenu.getByRole("menuitem").allInnerTexts()).map((text) => text.trim()),
    ["New chat", "New project…", "Add existing project…", "Export project archive…", "Restore project archive…", "Settings…"]);
  await page.keyboard.press("Escape");
  await fileMenu.waitFor({ state: "detached" });
  await page.waitForFunction(() => document.activeElement?.id === "hub-menu-file"); // Escape returns to the menu's word
  await page.getByRole("menuitem", { name: "View", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Dark", exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  await page.screenshot({ path: path.join(temporary, "menu-row-dark.png") });
  await page.getByRole("menuitem", { name: "View", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Light", exact: true }).click();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  // Every interface style draws the same row; the crops are for review by eye.
  for (const [style, name] of [["quiet", "Quiet instrument"], ["titleblock", "Title block"], ["night", "Night flight"], ["classic", "Classic"]]) {
    await page.getByRole("menuitem", { name: "View", exact: true }).click();
    await page.getByRole("menuitemradio", { name, exact: true }).click();
    await page.waitForFunction((value) => (document.documentElement.getAttribute("data-ui-style") ?? "classic") === value, style);
    await page.screenshot({ path: path.join(temporary, `menu-row-${style}.png`), clip: { x: 0, y: 0, width: 760, height: 180 } });
  }

  // B — the global defaults live in the bottom-left Hub settings only.
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({ hasText: "Hub settings (global)" });
  await settingsPage("Conversations");
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
  await settingsPage("AI Render");
  await page.locator("#render-provider").selectOption("gemini");
  await page.locator("#render-model").fill("gemini-3.1-flash-image");
  await page.locator("#render-timeout").fill("75");
  assert.equal(await page.locator('input[type="password"]').count(), 0, "render credentials never enter settings UI");
  const rechecks = providerReads.filter((value) => value === "true").length;
  await settingsPage("Conversations");
  await page.locator("#recheck-connections").click();
  await page.waitForFunction((count) => true, rechecks);
  assert.equal(await page.locator("#save-appearance, #save-launch").count(), 0, "Hub settings have no Save buttons");
  await page.waitForFunction(() => document.querySelector("#render-timeout")?.value === "75");
  await settingsSaved();
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
  // Back in B, its Board is on screen again; pressing it now would step out of the panel (IA-6).
  await showEntry("Board");
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
  // #300: Layout is Board's second mode. The Board | Layout switch moves between
  // the two mounted surfaces; the rail keeps Board pressed and has no Layout entry.
  const boardModes = visibleWorkspace().getByRole("radiogroup", { name: "Board mode", exact: true });
  assert.deepEqual(await boardModes.getByRole("radio").allInnerTexts(), ["Board", "Layout"]);
  assert.equal(await boardModes.getByRole("radio", { name: "Board", exact: true }).getAttribute("aria-checked"), "true");
  await boardModes.getByRole("radio", { name: "Layout", exact: true }).click();
  const layoutTitle = visibleWorkspace().getByLabel("Publication title", { exact: true });
  await layoutTitle.waitFor();
  assert.equal(new URL(page.url()).searchParams.get("view"), "publish");
  assert.equal(await page.getByRole("button", { name: "Board", exact: true }).getAttribute("aria-pressed"), "true", "Board stays pressed in Layout");
  assert.equal(await boardModes.getByRole("radio", { name: "Layout", exact: true }).getAttribute("aria-checked"), "true");
  await layoutTitle.fill("Layout B draft");
  await page.screenshot({ path: path.join(temporary, "board-layout-mode.png") });
  await boardModes.getByRole("radio", { name: "Layout", exact: true }).press("ArrowLeft");
  await waitWorkspace("board");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B retained", "Board keeps its state across Layout");
  await boardModes.getByRole("radio", { name: "Layout", exact: true }).click();
  await layoutTitle.waitFor();
  assert.equal(await layoutTitle.inputValue(), "Layout B draft", "Layout keeps its unsaved draft across Board");
  await boardModes.getByRole("radio", { name: "Board", exact: true }).click();
  await waitWorkspace("board");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();

  // #302: a result read back while the same turn keeps working takes nothing on
  // screen either; the request's Study card lists it. Neither polling nor the turn
  // ending loads it. The architect opens it, in the same mounted workspace.
  const completing = sessions[0];
  const beforeReadbackStarts = writes.filter(([, pathname]) => pathname.endsWith("/start")).length;
  const bytesBeforeResult = workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length;
  await visibleWorkspace().evaluate((element) => { element.completionMarker = "once"; });
  completing.messages.push({ id: "final-checkpoint", role: "tool", status: "complete", candidateId: "cand-B-final", content: "Final checkpoint completed" });
  emitRuntime();
  assert.equal(completing.status, "running");
  await page.locator('.chat-study[data-candidates~="cand-B-final"]').waitFor();
  const resultLeftViewAlone = async (message) => {
    await page.waitForTimeout(800);
    assert.equal(workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes")).length, bytesBeforeResult, message);
    assert.equal(await page.getByRole("button", { name: "Modeling", exact: true }).getAttribute("aria-pressed"), "true", message);
    assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once", message);
  };
  await resultLeftViewAlone("a result read back during the turn loads and switches nothing");
  completing.status = "idle";
  emitRuntime();
  await page.getByRole("button", { name: "Send", exact: true }).waitFor();
  await resultLeftViewAlone("finishing the turn does not open its result either");
  await viewCandidate("cand-B-final");
  assert.equal(await visibleWorkspace().evaluate((element) => element.completionMarker), "once", "opening the result keeps the mounted workspace");
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

  // #302: headless API jobs have no chat message, and their completed results take
  // nothing on screen either: no surface, panel, pinned candidate or model load.
  // The Board and its marks stay exactly as they were.
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
  const headlessBytes = () => workspaceFixture.requests.filter((row) => row.name.endsWith("/bytes") && row.runId?.startsWith("cand-B-headless")).length;
  const headlessPinned = () => page.evaluate(() => (JSON.parse(localStorage.getItem("monkeyhub.chat-view.v1"))?.tools ?? [])
    .some((tool) => tool.candidate?.startsWith("cand-B-headless")));
  runtimeB.retained = { projectId: "B", projectDir: runtimeB.projectDir, jobs: [pendingJob, oldJob, failedJob, newJob],
    candidates: [pendingJob, oldJob, failedJob, newJob].map((job) => headlessCandidate(job)) };
  runtimeB.operations = [headlessOperation(newJob), headlessOperation(oldJob), headlessOperation(pendingJob),
    headlessOperation(failedJob, { status: "completed" }),
    headlessOperation(headlessJob("cand-A-wrong-project", 40), { projectId: "A" })];
  emitRuntime();
  await page.waitForTimeout(1000);
  assert.equal(await page.getByRole("button", { name: "Board", exact: true }).getAttribute("aria-pressed"), "true",
    "a headless result never switches the surface");
  assert.equal(await visibleWorkspace().evaluate((element) => element.headlessBoardMarker), "retained");
  assert.equal(await visibleWorkspace().getByLabel("Board title", { exact: true }).inputValue(), "Board B retained");
  assert.deepEqual(projectBFixture.board, boardBeforeHeadless, "model completion preserves the Board and its marks");
  assert.equal(workspaceFixture.requests.filter((row) => row.method !== "GET").length, writesBeforeHeadless,
    "a result arriving makes no project write or editing-base change");
  assert.equal(headlessBytes(), 0, "a headless result never loads its model");
  assert.equal(await headlessPinned(), false, "a headless result never pins itself to the workspace");
  await page.screenshot({ path: path.join(temporary, "headless-keeps-board.png") });

  // Nor does a retained headless result open a closed panel after a reload.
  await page.evaluate(() => {
    const key = "monkeyhub.chat-view.v1", view = JSON.parse(localStorage.getItem(key));
    localStorage.setItem(key, JSON.stringify({ ...view, tools: [], activeTool: null, panel: false }));
  });
  const beforeHeadlessReopen = writes.length;
  // Reopen on the conversation alone: no workspace link in the address asks for the panel.
  const reopened = new URL(page.url());
  reopened.searchParams.delete("runtimeId"); reopened.searchParams.delete("view");
  await page.goto(reopened.href);
  await page.locator(".chat-study").first().waitFor();
  emitRuntime();
  await page.waitForTimeout(1000);
  assert.equal(await page.locator(".chat-shell").getAttribute("data-panel"), "false",
    "a retained headless result never opens the closed panel");
  assert.equal(headlessBytes(), 0);
  assert.equal(await headlessPinned(), false);
  assert.deepEqual(projectBFixture.board, boardBeforeHeadless, "reopening keeps all Board marks");
  assert.ok(writes.slice(beforeHeadlessReopen).every(([, pathname]) => pathname === "/api/runtime/projects/open"),
    "reopening only reattaches the existing project runtime");
  runtimeB.operations = []; runtimeB.retained = null; emitRuntime();
  await viewCandidate("cand-B-final");
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
  await viewCandidate("cand-B-final");
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
  await viewCandidate("cand-B-final");
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
  // Completed operations are recovery details, disclosed on request (#271).
  await page.getByRole("dialog", { name: "Project", exact: true }).getByText("Recovery details", { exact: true }).click();
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
  await showEntry("Modeling");
  await waitWorkspace();
  assert.equal(await page.getByRole("button", { name: "Hub settings", exact: true }).count(), 1);
  assert.equal(workspaceFixture.requests.filter((row) => row.name === "/api/settings/user").length, 0);

  await page.screenshot({ path: path.join(temporary, "desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await settingsPage("Workspace");
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
  await settingsPage("Display");
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
  const modelingEntry = page.getByRole("button", { name: "Modeling", exact: true });
  await modelingEntry.click();
  await delayedStart;
  // PP-1: over the surface on screen, after a moment, the skeleton of the one being opened names the step it waits on.
  const openingSkeleton = page.locator(".chat-skeleton");
  await openingSkeleton.getByText("Opening model…", { exact: true }).waitFor();
  assert.equal(await openingSkeleton.locator("h2").innerText(), "Modeling");
  assert.equal(await page.locator(".chat-composer-note").textContent(), "", "the composer no longer says it is connecting");
  assert.equal(await modelingEntry.getAttribute("aria-pressed"), "true");
  assert.equal(await page.getByRole("button", { name: "Render", exact: true }).getAttribute("aria-pressed"), "false");
  // Pressing the entry being opened cancels it and gives back the surface under it (IA-6).
  assert.equal(await modelingEntry.getAttribute("aria-description"), "Cancel opening Modeling");
  await modelingEntry.click();
  await openingSkeleton.waitFor({ state: "detached" });
  await waitWorkspace("render");
  assert.equal(await page.getByRole("button", { name: "Render", exact: true }).getAttribute("aria-pressed"), "true");
  assert.equal(await modelingEntry.getAttribute("aria-busy"), null);
  // Opening it again waits on the same modeling start, which Cancel never withdrew.
  const modelingStarts = () => writes.filter(([, pathname]) => pathname === "/api/project/modeling").length;
  const startsBeforeReopen = modelingStarts();
  await modelingEntry.click();
  await openingSkeleton.getByText("Opening model…", { exact: true }).waitFor();
  assert.equal(modelingStarts(), startsBeforeReopen, "the reopened tool waits on the start already asked for");
  await page.locator(".chat-new").getByText("New chat", { exact: true }).click();
  // The skeleton belongs to the conversation it was opened for.
  await openingSkeleton.waitFor({ state: "detached" });
  releaseModeling(); modelingResponseGate = Promise.resolve();
  await page.waitForFunction(() => !document.querySelector(".chat-rail__tool[aria-busy]"));
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
  await addAttachments([
    { name: "outline.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic outline A") },
    { name: "remove-me.txt", mimeType: "text/plain", buffer: Buffer.from("Remove this draft file") },
  ]);
  await page.getByRole("button", { name: "Remove attachment: remove-me.txt", exact: true }).click();
  assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 1);
  assert.ok(await page.locator("#chat-input").evaluate((node) => node === document.activeElement));
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
  await settingsPage("Software update");
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
  await settingsPage("Software update");
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
  // #285: the running turn names the step it is on in plain words; the CLI's
  // own line and diagnostics wait under Technical details.
  const readingTurn = page.locator(".chat-process").last();
  await readingTurn.locator(".chat-process__current").filter({ hasText: "Look through files" }).waitFor();
  assert.match(await readingTurn.locator(".chat-process__row").innerText(), /4 steps/);
  assert.equal(await readingTurn.locator(".chat-process__body").count(), 0);
  await readingTurn.locator(".chat-process__row").click();
  const toolDetails = readingTurn.locator(".chat-process__technical");
  assert.equal(await toolDetails.getAttribute("open"), null);
  await toolDetails.locator("summary").click();
  assert.equal(await toolDetails.locator("li").filter({ hasText: "Read project files · in_progress" }).locator("pre").innerText(), "Synthetic tool diagnostics");
  await readingTurn.locator(".chat-process__row").click();
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
    const uploadButton = composerMenu();
    const bounds = await uploadButton.boundingBox();
    assert.ok(bounds && bounds.width >= 44 && bounds.height >= 44 && bounds.x >= 0 && bounds.x + bounds.width <= width);
    assert.ok(await progressCard.evaluate((node) => node.scrollWidth <= node.clientWidth + 1));
    await uploadButton.focus();
    const chooser = page.waitForEvent("filechooser");
    await page.keyboard.press("Enter");
    const menuBox = await page.getByRole("menu", { name: "Attachments and new topic", exact: true }).boundingBox();
    assert.ok(menuBox && menuBox.x >= 0 && menuBox.x + menuBox.width <= width, "the + menu fits the window at " + width + "px");
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
  // #295: opened straight from the conversation, Drawing has no surface to go
  // back to; leaving it closes the panel again and keeps the project selected.
  const drawingsB = page.getByRole("button", { name: "Drawings", exact: true });
  await drawingsB.click();
  await waitWorkspace("drawing");
  assert.equal(await drawingsB.getAttribute("title"), "Close Drawings");
  await drawingsB.click();
  await page.waitForFunction(() => document.querySelector(".chat-shell")?.dataset.panel === "false");
  assert.equal(await drawingsB.getAttribute("aria-pressed"), "false");
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project B");
  await page.getByRole("button", { name: "Modeling", exact: true }).click();
  await waitWorkspace();
  await page.getByRole("button", { name: "Fabrication", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("view=fab"));
  assert.equal(new URL(page.url()).searchParams.has("runtimeId"), false);
  await page.reload();
  await page.waitForFunction(() => document.querySelector("iframe:not([hidden])")?.src.includes("view=fab"));
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
  emitRuntime();
  await page.waitForTimeout(600);
  assert.equal(await page.getByRole("button", { name: "Usage", exact: true }).getAttribute("aria-pressed"), "true",
    "a retained headless result never overrides an explicit Monitor deep link");
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
  await contextReady();
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
  await contextReady();
  assert.equal(await page.locator('.chat-composer > .chat-muted').count(), 0,
    "ordinary chat does not show instructions for the optional new project context");
  assert.equal(await page.locator(".chat-composer input[type=checkbox], .chat-composer .chat-topic").count(), 0,
    "DC-5: no standing checkbox; New topic lives in the + menu");
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await visibleWorkspace().locator('.vcard__export').filter({ hasText: "cand-A-1.3dm" }).click();
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  await composerMenu().click();
  const newTopic = page.getByRole("menuitemcheckbox", { name: "New topic", exact: true });
  assert.equal(await newTopic.getAttribute("aria-checked"), "false");
  assert.equal(await newTopic.getAttribute("aria-description"), "The next message starts from the project state, without this conversation's context");
  await newTopic.click();
  const topicChip = page.locator(".chat-composer .chat-topic");
  assert.equal((await topicChip.innerText()).trim(), "New topic");
  assert.equal(await topicChip.getAttribute("title"), "The next message starts a new model context from the saved editing state. This conversation stays visible.");
  assert.equal(await page.locator(".chat-composer .chat-topic-refusal").count(), 0, "a New topic with a saved state to start from has no refusal to show");
  // The chip is removable, and the menu shows the choice as checked while it stands.
  await page.getByRole("button", { name: "Remove New topic", exact: true }).click();
  await topicChip.waitFor({ state: "detached" });
  await composerMenu().click();
  await newTopic.click();
  await composerMenu().click();
  assert.equal(await newTopic.getAttribute("aria-checked"), "true");
  await page.keyboard.press("Escape");
  await page.getByRole("menu").waitFor({ state: "detached" });
  assert.ok(await composerMenu().evaluate((node) => node === document.activeElement), "Escape returns to the + button");
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
  await visibleWorkspace().getByRole("button", { name: "Continue from here", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.chat-project-workspace:not([hidden]) .editing-base')?.dataset.sourceMatch === "same");
  await visibleWorkspace().locator(".stage__versions-toggle").click();
  assert.equal(await page.locator(".chat-composer .chat-topic").count(), 0, "New topic applies to one message");
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
  await contextReady();
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
  assert.equal(await page.locator('.chat-composer > .chat-muted').count(), 0,
    "stage handoff works without a permanent explanatory caption");
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
  await page.waitForFunction(() => document.querySelectorAll(".chat-message--user").length === 4);
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
  await page.locator(".chat-menubar").getByRole("button", { name: /^(Hide|Show) projects$/ }).click();
  await preview.click();
  const previewBounds = await imageDialog.boundingBox();
  assert.ok(previewBounds.x >= 0 && previewBounds.x + previewBounds.width <= 390, "the image dialog fits a narrow viewport");
  await page.screenshot({ path: path.join(temporary, "external-image-mobile.png") });
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.locator(".chat-menubar").getByRole("button", { name: /^(Hide|Show) projects$/ }).click();
  await page.reload();
  await page.getByRole("button", { name: "Enlarge image: facade.png", exact: true }).waitFor();
  await page.getByRole("button", { name: "Project A", exact: true }).first().click();
  await page.locator("#chat-input").waitFor();
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-external-notice").waitFor();
  assert.equal(writes.filter(([, name]) => /\/(messages|stop|model)$/.test(name)).length, beforeExternalTurns);
  await autosavedModelRestart();

  // #285: a long conversation opens at its latest message. Each finished turn
  // is one folded row above the Agent's answer; the steps read in plain words,
  // the CLI's lines only under Technical details. A reader who scrolls up stays
  // there while the Agent writes, and one button brings them back.
  const longStart = Date.parse("2026-09-25T09:00:00Z"), longAt = (seconds) => new Date(longStart + seconds * 1000).toISOString();
  const longSession = { id: "long-review", projectId: "A", projectDir: "D:\\fixture\\A", title: "Long review", provider: "codex",
    status: "idle", archived: false, createdAt: longAt(0), updatedAt: longAt(0), messages: [] };
  for (let turn = 0; turn < 12; turn++) {
    const start = turn * 600, id = `lu-${turn}`;
    longSession.messages.push(
      { id, role: "user", status: "complete", createdAt: longAt(start), content: `Revision ${turn + 1}: move the entrance to the south side` },
      { id: `${id}:schema`, role: "tool", status: "complete", createdAt: longAt(start + 2), content: "studio_schema · POST /api/intents/context · completed" },
      { id: `${id}:context`, role: "tool", status: "complete", createdAt: longAt(start + 5), content: "studio_request · POST /api/intents/context · completed\nstatus: ok" },
      { id: `${id}:search`, role: "tool", status: "failed", createdAt: longAt(start + 20), content: "rg -n entrance C:\\notes\\MEMORY.md · failed\nexit code 1" },
      { id: `${id}:board`, role: "tool", status: "complete", createdAt: longAt(start + 40), content: "studio_request · GET /api/board · completed" },
      { id: `${id}:answer`, role: "assistant", status: "complete", createdAt: longAt(start + 78),
        content: `Moved the main entrance to the middle of the south facade (revision ${turn + 1}).\n\nThe east stair follows it by 1.2 m, and the canopy is 3 m deep.` },
    );
  }
  sessions.unshift(longSession);
  const longList = page.locator(".chat-messages");
  const atLatest = () => page.waitForFunction(() => {
    const node = document.querySelector(".chat-messages");
    return node && node.scrollHeight > node.clientHeight * 2 && node.scrollHeight - node.scrollTop - node.clientHeight < 2;
  });
  await page.goto(`${origin}/?chatId=${longSession.id}`);
  await page.locator(".chat-header h1").filter({ hasText: "Long review" }).waitFor();
  await atLatest();
  assert.equal(await page.locator(".chat-jump").count(), 0, "a chat opened at its latest message offers no jump");
  assert.equal(await page.locator(".chat-process").count(), 12, "one process row per turn");
  const lastTurn = page.locator(".chat-process").last(), lastRow = lastTurn.locator(".chat-process__row");
  assert.equal((await lastRow.innerText()).replace(/\s+/g, " ").trim(), "Worked 1m 18s · 4 steps · 1 failed");
  assert.equal(await lastRow.getAttribute("aria-expanded"), "false", "a finished turn starts folded");
  await page.locator(".chat-message--assistant").filter({ hasText: "revision 12" }).waitFor();
  assert.equal(await page.getByText("studio_request · GET /api/board · completed").count(), 0, "raw request lines stay out of the conversation");
  const rowTop = () => lastRow.evaluate((node) => node.getBoundingClientRect().top);
  const beforeOpen = await rowTop();
  await lastRow.click();
  assert.deepEqual(await lastTurn.locator(".chat-process__steps li > span:last-child").allInnerTexts(),
    ["Prepare to read the design context", "Read the design context", "Look through files · failed", "Read the board"]);
  assert.ok(Math.abs(await rowTop() - beforeOpen) < 1, "opening a turn does not move it");
  const longTechnical = lastTurn.locator(".chat-process__technical");
  assert.equal(await longTechnical.locator("code").first().isVisible(), false, "the raw lines wait under Technical details");
  await longTechnical.locator("summary").click();
  assert.deepEqual(await longTechnical.locator("code").allInnerTexts(), ["studio_schema · POST /api/intents/context · completed",
    "studio_request · POST /api/intents/context · completed", "rg -n entrance C:\\notes\\MEMORY.md · failed", "studio_request · GET /api/board · completed"]);
  await page.screenshot({ path: path.join(temporary, "process-open-en-1440.png") });
  const beforeFold = await rowTop();
  await lastRow.click();
  assert.equal(await lastTurn.locator(".chat-process__body").count(), 0, "folding leaves no space behind");
  assert.ok(Math.abs(await rowTop() - beforeFold) < 1 || await longList.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight < 2),
    "folding keeps the row in place unless the list ends above it");
  // A turn in the middle of the conversation opens and folds in place as well.
  const middleRow = page.locator(".chat-process").nth(5).locator(".chat-process__row");
  await middleRow.evaluate((node) => node.scrollIntoView({ block: "center", behavior: "instant" }));
  const middleTop = () => middleRow.evaluate((node) => node.getBoundingClientRect().top);
  const middleBefore = await middleTop();
  await middleRow.click();
  assert.ok(Math.abs(await middleTop() - middleBefore) < 1, "opening a middle turn keeps it where it was");
  await middleRow.click();
  assert.ok(Math.abs(await middleTop() - middleBefore) < 1, "folding a middle turn keeps it where it was");
  // Switching away and back lands at the latest message again, folded.
  await lastRow.click();
  await page.getByRole("button", { name: "Project B", exact: true }).first().click();
  await page.locator(".chat-thread").filter({ hasText: "Long review" }).click();
  await page.locator(".chat-header h1").filter({ hasText: "Long review" }).waitFor();
  await atLatest();
  assert.equal(await page.locator('.chat-process__row[aria-expanded="true"]').count(), 0, "reopening a chat shows it folded");
  // Reading earlier turns: new output leaves the reader where they are and is counted.
  await longList.evaluate((node) => node.scrollTo({ top: 0, behavior: "instant" }));
  const jump = page.locator(".chat-jump");
  await jump.waitFor();
  assert.equal(await jump.getAttribute("aria-label"), "Jump to the latest message");
  const readingAt = await longList.evaluate((node) => node.scrollTop);
  longSession.status = "running";
  longSession.messages.push({ id: "lu-11:follow-up", role: "assistant", status: "streaming", createdAt: longAt(11 * 600 + 90), content: "Checking the stair headroom next." });
  emitRuntime();
  await page.locator(".chat-jump__count").filter({ hasText: /^1$/ }).waitFor();
  assert.equal(await jump.getAttribute("aria-label"), "Jump to the latest message (1 new)");
  await page.screenshot({ path: path.join(temporary, "process-jump-en-1440.png") });
  assert.equal(await longList.evaluate((node) => node.scrollTop), readingAt, "a reader who scrolled up is not moved");
  const jumpBox = await jump.boundingBox(), composerBox = await page.locator(".chat-composer").boundingBox();
  assert.ok(jumpBox.y + jumpBox.height <= composerBox.y && Math.abs(jumpBox.x + jumpBox.width / 2 - (composerBox.x + composerBox.width / 2)) < 2,
    "the jump sits centred just above the composer");
  await jump.click();
  await atLatest();
  await jump.waitFor({ state: "hidden" });
  // At the latest message, streamed text keeps the reader there.
  longSession.messages.at(-1).content += "\n\nThe headroom under the landing is 2.3 m, so the stair can stay.";
  emitRuntime();
  await page.getByText("The headroom under the landing is 2.3 m", { exact: false }).waitFor();
  await atLatest();
  assert.equal(await page.locator(".chat-jump").count(), 0);
  // In a phone-width column the running row shortens its current step, never its counts.
  await page.setViewportSize({ width: 375, height: 812 });
  const narrowRow = page.locator('.chat-process[data-running="true"] .chat-process__row');
  assert.deepEqual(await narrowRow.evaluate((row) => {
    const box = row.getBoundingClientRect();
    return [...row.querySelectorAll(".chat-process__count, .chat-process__failed")].map((part) => {
      const shown = part.getBoundingClientRect();
      return [part.textContent.trim(), shown.width > 0 && shown.left >= box.left - 1 && shown.right <= box.right + 1];
    });
  }), [["· 4 steps", true], ["· 1 failed", true]], "the step and failure counts stay in view");
  await page.setViewportSize({ width: 1440, height: 960 });
  longSession.status = "idle"; longSession.messages.at(-1).status = "complete";
  emitRuntime();
  await page.waitForFunction(() => !document.querySelector('.chat-process[data-running="true"]'));
  // The same conversation in Chinese, at desktop and phone widths.
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.locator("#language").selectOption("zh-CN");
  await page.getByRole("dialog").getByRole("button", { name: "关闭", exact: true }).click();
  await atLatest();
  assert.equal((await lastRow.innerText()).replace(/\s+/g, " ").trim(), "用时 1分30秒 · 4 步 · 1 步失败");
  await lastRow.click();
  assert.deepEqual(await lastTurn.locator(".chat-process__steps li > span:last-child").allInnerTexts(),
    ["准备读取设计上下文", "读取设计上下文", "查阅资料 · 失败", "读取画板"]);
  await longList.evaluate((node) => node.scrollBy({ top: -260, behavior: "instant" }));
  await jump.waitFor();
  assert.equal(await jump.getAttribute("aria-label"), "跳到最新消息");
  await page.screenshot({ path: path.join(temporary, "process-zh-1440.png") });
  await page.setViewportSize({ width: 375, height: 812 });
  if (await page.getByRole("button", { name: "收起项目栏", exact: true }).first().isVisible()) {
    await page.getByRole("button", { name: "收起项目栏", exact: true }).first().click();
  }
  await page.screenshot({ path: path.join(temporary, "process-zh-375.png") });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "no sideways scroll at 375 px");
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "展开项目栏", exact: true }).first().click();
  await lastRow.click();
  await page.getByRole("button", { name: "Hub 设置", exact: true }).click();
  await page.locator("#language").selectOption("en");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();

  // #300: Tasks lists the Agent work running or waiting in every open project,
  // and opens its project and conversation; project rows carry a running badge.
  // The footer shows the last 7 days of usage from Monitor and a ready update.
  for (const session of sessions) if (session.status === "running") session.status = "idle";
  const tasksB = runtimes.get("D:\\fixture\\B"), tasksA = runtimes.get("D:\\fixture\\A");
  const bChat = sessions.find((row) => row.projectId === "B" && !row.sourceSessionId && !row.archived);
  tasksB.operations.push({ operationId: "task-running", projectId: "B", kind: "POST /api/proposals/prop-9/candidate", source: "hub",
    status: "executing", committed: false, sessionId: bChat.id });
  tasksA.operations.push({ operationId: "task-queued", projectId: "A", kind: "POST /api/drawings/sheets", source: "studio", status: "queued", committed: false });
  await page.goto(`${origin}/?chatId=${longSession.id}`);
  await page.locator(".chat-header h1").filter({ hasText: "Long review" }).waitFor();
  const tasksEntry = page.locator(".chat-tasks-toggle");
  await page.waitForFunction(() => document.querySelector(".chat-tasks-toggle .chat-count")?.textContent === "2");
  assert.equal(await tasksEntry.getAttribute("aria-expanded"), "false", "Tasks starts folded");
  assert.match(await tasksEntry.innerText(), /^Tasks/);
  const projectBadge = (name) => page.locator(".chat-project__head").filter({ has: page.getByRole("button", { name, exact: true }) }).locator('.chat-project__badge[data-kind="running"]');
  await projectBadge("Project B").waitFor();
  await projectBadge("Project A").waitFor();
  assert.equal(await projectBadge("harbour-study").count(), 0, "an idle project has no badge");
  assert.equal(await page.locator('.chat-project__badge[data-kind="new"]').count(), 0, "no new-schemes number is guessed before #294");
  assert.equal(await page.getByRole("button", { name: "Project B", exact: true }).first().getAttribute("aria-description"), "1 task running or waiting");
  await tasksEntry.click();
  const taskList = page.getByRole("list", { name: "Tasks", exact: true });
  assert.deepEqual(await taskList.locator(".chat-task__title").allInnerTexts(), [bChat.title, "Make a drawing sheet"]);
  assert.deepEqual(await taskList.locator(".chat-task small").allInnerTexts(), ["Project B · Running · Generate a scheme", "Project A · Waiting to start"]);
  assert.equal(await taskList.getByText(/task-running|task-queued|prop-9/).count(), 0, "no raw ids in Tasks");
  // Footer: usage from Monitor's own records, and no update while none is ready.
  const usageEntry = page.locator(".chat-usage");
  await page.waitForFunction(() => document.querySelector(".chat-usage")?.getAttribute("aria-label") === "Usage: 8,510 tokens in the last 7 days");
  assert.equal(await usageEntry.locator(".chat-usage__figure").innerText(), "8.5K tokens · 7 days");
  assert.equal(await page.locator(".chat-update").count(), 0, "no update is announced while none is ready");
  await page.screenshot({ path: path.join(temporary, "sidebar-tasks-en-1440.png") });
  await taskList.locator(".chat-task").first().click();
  await page.locator(".chat-header h1").filter({ hasText: bChat.title }).waitFor();
  assert.equal(await page.locator('.chat-project[data-selected="true"] .chat-project__name').innerText(), "Project B", "the task opened its project");
  await taskList.locator(".chat-task").filter({ hasText: "Make a drawing sheet" }).click();
  await page.waitForFunction(() => document.querySelector('.chat-project[data-selected="true"] .chat-project__name')?.textContent === "Project A");
  // Finished work leaves Tasks and its project's badge.
  tasksB.operations = tasksB.operations.filter((row) => row.operationId !== "task-running");
  tasksA.operations = tasksA.operations.filter((row) => row.operationId !== "task-queued");
  emitRuntime();
  await projectBadge("Project B").waitFor({ state: "detached" });
  await taskList.getByText("Nothing is running or waiting.", { exact: true }).waitFor();
  assert.equal(await page.locator(".chat-tasks-toggle .chat-count").count(), 0);
  await tasksEntry.click();
  // A ready update is one click from Software Update.
  updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
  await page.reload();
  const updateEntry = page.getByRole("button", { name: "New version ready", exact: true });
  await updateEntry.waitFor();
  await page.locator(".chat-sidebar").screenshot({ path: path.join(temporary, "sidebar-footer-en.png") });
  await updateEntry.click();
  const updateHeading = page.getByRole("dialog").getByRole("heading", { name: "Software update", exact: true });
  await updateHeading.waitFor();
  await page.waitForFunction(() => {
    const heading = document.getElementById("software-update-heading"), dialog = heading?.closest("dialog");
    if (!heading || !dialog) return false;
    const box = heading.getBoundingClientRect(), frame = dialog.getBoundingClientRect();
    return box.top >= frame.top - 1 && box.bottom <= frame.bottom + 1;
  });
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await updateEntry.waitFor({ state: "detached" });
  // The footer's usage opens the Usage tool.
  await usageEntry.click();
  await waitMonitor();
  // A Layout link still lands on Layout, with Board pressed in the rail.
  await page.goto(`${origin}/?${new URLSearchParams({ runtimeId: tasksB.runtimeId, view: "publish" })}`);
  await visibleWorkspace().getByLabel("Publication title", { exact: true }).waitFor();
  assert.equal(await visibleWorkspace().getByRole("radio", { name: "Layout", exact: true }).getAttribute("aria-checked"), "true");
  assert.equal(await page.getByRole("button", { name: "Board", exact: true }).getAttribute("aria-pressed"), "true");
  // The same sidebar in Chinese, at desktop and phone widths.
  tasksB.operations.push({ operationId: "task-running-zh", projectId: "B", kind: "POST /api/proposals/prop-9/candidate", source: "hub",
    status: "executing", committed: false, sessionId: bChat.id });
  emitRuntime();
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.locator("#language").selectOption("zh-CN");
  await page.getByRole("dialog").getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "收起工具", exact: true }).click();
  await tasksEntry.click();
  await page.getByRole("list", { name: "任务", exact: true }).locator(".chat-task small").filter({ hasText: "Project B · 进行中 · 生成方案" }).waitFor();
  await page.waitForFunction(() => document.querySelector(".chat-usage")?.getAttribute("aria-label") === "用量：近 7 天 8,510 tokens");
  assert.equal(await usageEntry.locator(".chat-usage__figure").innerText(), "近 7 天 8510 tokens");
  assert.equal(await projectBadge("Project B").innerText(), "运行中");
  await page.screenshot({ path: path.join(temporary, "sidebar-zh-1440.png") });
  await page.setViewportSize({ width: 375, height: 812 });
  if (!(await page.locator(".chat-sidebar").isVisible())) await page.getByRole("button", { name: "展开项目栏", exact: true }).first().click();
  await page.screenshot({ path: path.join(temporary, "sidebar-zh-375.png") });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "no sideways scroll at 375 px");
  await page.setViewportSize({ width: 1440, height: 960 });
  await tasksEntry.click();
  tasksB.operations = tasksB.operations.filter((row) => row.operationId !== "task-running-zh");
  emitRuntime();
  await page.getByRole("button", { name: "Hub 设置", exact: true }).click();
  await page.locator("#language").selectOption("en");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();

  // #285, on a project whose runtime has a Design Tree. DC-9: the composer names what the
  // next message changes and where Current stands, as the Stage chip does, and says so
  // while another model is open read-only; the message still changes Current.
  projects.push({ projectId: "T", projectDir: "D:\\fixture\\T", name: "Tree project", chatCount: 0, version: 2, stage: "S2" });
  workspaceFixture.designTrees.set("T", { stages: ["tree-s0", "tree-s1", "tree-s2"], edits: ["tree-e2", "tree-e1"] });
  emitRuntime();
  await page.getByRole("button", { name: "Tree project", exact: true }).first().click();
  await studioReady();
  await contextReady();
  const target = page.locator(".chat-composer .chat-target");
  await target.filter({ hasText: "Changes: Current · 3 edits after S2" }).waitFor();
  assert.equal(await target.getAttribute("data-viewing"), "false");
  assert.equal(await page.locator("#chat-input").getAttribute("aria-describedby"), "chat-target", "the input is described by what it changes");
  assert.doesNotMatch(await target.innerText(), /tree-|project:\/\/|[0-9a-f]{12}/, "the target label shows no raw ids");
  await page.getByRole("button", { name: "Design tree", exact: true }).click();
  const treeSurface = visibleWorkspace().locator(".design-tree");
  await treeSurface.waitFor();
  await treeSurface.getByRole("button", { name: "List", exact: true }).click();
  await treeSurface.locator('[role="treeitem"][data-node="stage:project://T/runs/tree-s0/review/design-stage.json"]').click();
  await treeSurface.getByRole("button", { name: "View", exact: true }).click();
  await target.locator(".chat-target__viewing").filter({ hasText: "Viewing S0; this message still changes Current" }).waitFor();
  assert.equal(await target.getAttribute("data-viewing"), "true");
  assert.equal(await target.locator(".chat-target__text").innerText(), "Changes: Current · 3 edits after S2");
  await visibleWorkspace().getByRole("button", { name: "Back to Current", exact: true }).click();
  await target.locator(".chat-target__viewing").waitFor({ state: "detached" });
  assert.equal(await target.getAttribute("data-viewing"), "false");
  // PP-3: a send says Sending… while its message is on the way; connecting a project keeps its own words.
  let releaseSend;
  chatMessageResponseGate = new Promise((resolve) => { releaseSend = resolve; });
  await page.locator("#chat-input").fill("Widen the reading room by one bay");
  const heldSend = page.waitForRequest((req) => req.method() === "POST" && new URL(req.url()).pathname.endsWith("/messages"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await heldSend;
  await page.waitForFunction(() => document.querySelector(".chat-composer-note")?.textContent === "Sending…");
  releaseSend(); chatMessageResponseGate = Promise.resolve();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".chat-composer-note")?.textContent === "");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  // SS-9: unsent text is kept per conversation in this browser and comes back after a
  // reload; it no longer holds the update restart (chosen files still do). Sending clears it.
  const treeChat = sessions.find((row) => row.projectId === "T");
  const keptDraft = (id) => page.evaluate((key) => JSON.parse(localStorage.getItem("monkeyhub.chat-drafts.v1") ?? "{}")[key]?.text ?? null, id);
  await page.locator("#chat-input").fill("Keep this sentence through a reload");
  await page.waitForFunction((id) => JSON.parse(localStorage.getItem("monkeyhub.chat-drafts.v1") ?? "{}")[id]?.text === "Keep this sentence through a reload", treeChat.id);
  await page.reload();
  await page.waitForFunction(() => document.querySelector("#chat-input")?.value === "Keep this sentence through a reload");
  updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await settingsPage("Software update");
  await page.getByRole("button", { name: "Restart to update", exact: true }).waitFor();
  assert.equal(await page.getByText("A conversation has unsent text or attachments. Send or remove them before restarting.").count(), 0,
    "kept text alone does not hold the update restart");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false };
  assert.equal(appliedPatches, 0);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).waitFor();
  assert.equal(await keptDraft(treeChat.id), null, "a sent message leaves no kept draft");
  await page.getByRole("button", { name: "Stop", exact: true }).click();

  // #285: the unfinished-operation notice names the operation and when it was asked for,
  // links the chat that asked, and Dismiss is recorded with the operation in the Hub
  // runtime, so it stays dismissed after a restart. One that needs recovery stays.
  const runtimeT = runtimes.get("D:\\fixture\\T");
  sessions.push({ id: "tree-earlier", projectId: "T", projectDir: "D:\\fixture\\T", title: "Earlier drawing request", provider: "codex",
    model: null, status: "idle", archived: false, createdAt: "2026-09-25", updatedAt: "2026-09-25", messages: [] });
  const sheetAskedAt = "2026-09-25T06:02:00Z";
  runtimeT.operations = [
    { operationId: "candidate-waiting", projectId: "T", kind: "POST /api/proposals/prop-1/candidate", source: "chat", status: "needs_recovery",
      committed: false, admissionSequence: 1, createdAt: "2026-09-25T05:00:00Z" },
    // Admitted before admission times were kept: no time is shown for it.
    { operationId: "proposal-stale", projectId: "T", kind: "POST /api/proposals", source: "studio", status: "stale", committed: false, admissionSequence: 2 },
    { operationId: "sheet-failed", projectId: "T", kind: "POST /api/drawings/sheets", source: "chat", status: "failed", committed: false,
      admissionSequence: 3, createdAt: sheetAskedAt, sessionId: "tree-earlier" },
  ];
  emitRuntime();
  const notice = page.locator(".chat-runtime--operation");
  const askedAt = await page.evaluate((value) => new Date(value).toLocaleString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }), sheetAskedAt);
  await notice.locator("p").filter({ hasText: "An operation did not complete" }).waitFor();
  assert.equal(await notice.locator("small").innerText(), `Make a drawing sheet · ${askedAt} · 2 more`);
  assert.doesNotMatch(await notice.innerText(), /sheet-failed|POST|\/api\//, "the notice names the operation in words, not its id or route");
  await notice.getByRole("button", { name: "Open chat: Earlier drawing request", exact: true }).click();
  await page.locator(".chat-header h1").filter({ hasText: "Earlier drawing request" }).waitFor();
  assert.equal(await notice.getByRole("button", { name: /^Open chat/ }).count(), 0, "the chat it came from is the one on screen");
  const beforeDismiss = writes.length;
  await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
  await notice.locator("p").filter({ hasText: "An operation has an outdated base" }).waitFor();
  assert.equal(await notice.locator("small").innerText(), "Draft a model change · 1 more");
  assert.deepEqual(writes.slice(beforeDismiss).filter(([, pathname]) => pathname.includes("/acknowledge")),
    [["POST", "/api/runtime/operations/sheet-failed/acknowledge", { runtimeId: runtimeT.runtimeId, projectId: "T" }, null]]);
  await page.reload();
  await notice.locator("p").filter({ hasText: "An operation has an outdated base" }).waitFor();
  await notice.getByRole("button", { name: "Dismiss", exact: true }).click();
  await notice.locator("p").filter({ hasText: "An operation needs recovery review" }).waitFor();
  assert.equal(await notice.locator("small").innerText(), `Generate a scheme · ${await page.evaluate(() => new Date("2026-09-25T05:00:00Z")
    .toLocaleString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }))}`);
  assert.equal(await notice.getByRole("button", { name: "Dismiss", exact: true }).count(), 0, "one that needs recovery cannot be dismissed");
  assert.ok(runtimeT.operations.filter((row) => row.status !== "needs_recovery").every((row) => row.acknowledgedAt));
  // GH-58: one the Hub marks unrecoverable says so beside Dismiss, and once dismissed it leaves; the
  // one that can still be recovered stays without Dismiss. Rows that say nothing keep today's rule.
  runtimeT.operations.push({ operationId: "candidate-lost", projectId: "T", kind: "POST /api/proposals/prop-2/candidate", source: "chat",
    status: "needs_recovery", recoverable: false, committed: false, admissionSequence: 5, createdAt: sheetAskedAt });
  emitRuntime();
  const unrecoverableNote = notice.getByText("Cannot be recovered automatically", { exact: true });
  await unrecoverableNote.waitFor();
  assert.equal(await notice.locator("p").innerText(), "An operation needs recovery review");
  const dismissLost = notice.getByRole("button", { name: "Dismiss", exact: true });
  assert.equal(await dismissLost.getAttribute("aria-describedby"), await unrecoverableNote.getAttribute("id"), "Dismiss is described by why");
  const beforeLost = writes.length;
  await dismissLost.click();
  await unrecoverableNote.waitFor({ state: "detached" });
  assert.deepEqual(writes.slice(beforeLost).filter(([, pathname]) => pathname.includes("/acknowledge")).map(([, pathname]) => pathname),
    ["/api/runtime/operations/candidate-lost/acknowledge"]);
  await notice.locator("p").filter({ hasText: "An operation needs recovery review" }).waitFor();
  assert.equal(await notice.getByRole("button", { name: "Dismiss", exact: true }).count(), 0, "the one that can still be recovered stays");

  // The same composer states and notice in Chinese, for review.
  await page.getByRole("button", { name: "Hub settings", exact: true }).click();
  await page.locator("#language").selectOption("zh-CN");
  await page.getByRole("dialog").getByRole("button", { name: "关闭", exact: true }).click();
  runtimeT.operations.push({ operationId: "sheet-failed-again", projectId: "T", kind: "POST /api/drawings/sheets", source: "chat",
    status: "failed", committed: false, admissionSequence: 4, createdAt: sheetAskedAt, sessionId: treeChat.id });
  emitRuntime();
  const askedAtZh = await page.evaluate((value) => new Date(value).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }), sheetAskedAt);
  await notice.locator("p").filter({ hasText: "有操作未完成" }).waitFor();
  assert.equal(await notice.locator("small").innerText(), `出图 · ${askedAtZh} · 另有 1 个`);
  await notice.getByRole("button", { name: `打开对话: ${treeChat.title}`, exact: true }).waitFor();
  await notice.getByRole("button", { name: "知道了", exact: true }).waitFor();
  await contextReady();
  await page.locator(".chat-composer .chat-target").filter({ hasText: "将修改：当前 · S2 之后 3 次修改" }).waitFor();
  // A loading model's status shows through a hidden panel (BilingualText sets visibility: visible); wait it out.
  const modelSettled = () => page.waitForFunction(() => !document.querySelector(".chat-browser")?.innerText.includes("Parsing"));
  await modelSettled();
  if (await page.getByRole("button", { name: "收起工具", exact: true }).count()) await page.getByRole("button", { name: "收起工具", exact: true }).click();
  await page.locator(".chat-main").screenshot({ path: path.join(temporary, "composer-notice-zh.png") });
  await composerMenu("附件与新话题").click();
  await page.getByRole("menuitemcheckbox", { name: "新话题", exact: true }).waitFor();
  await page.locator(".chat-main").screenshot({ path: path.join(temporary, "composer-menu-zh.png") });
  await page.getByRole("menuitemcheckbox", { name: "新话题", exact: true }).click();
  await page.locator(".chat-composer .chat-topic").filter({ hasText: "新话题" }).waitFor();
  await page.getByRole("button", { name: "状态树", exact: true }).click();
  const treeSurfaceZh = visibleWorkspace().locator(".design-tree");
  await treeSurfaceZh.getByRole("button", { name: "列表", exact: true }).click();
  await treeSurfaceZh.locator('[role="treeitem"][data-node="stage:project://T/runs/tree-s0/review/design-stage.json"]').click();
  await treeSurfaceZh.getByRole("button", { name: "查看", exact: true }).click();
  await page.locator(".chat-composer .chat-target__viewing").filter({ hasText: "正在查看 S0，这条消息仍会修改当前" }).waitFor();
  await visibleWorkspace().locator(".boot").waitFor({ state: "hidden" });
  await modelSettled();
  await page.getByRole("button", { name: "收起工具", exact: true }).click();
  await page.locator(".chat-main").screenshot({ path: path.join(temporary, "composer-viewing-topic-zh.png") });
  await page.getByRole("button", { name: "展开工具", exact: true }).click();
  await visibleWorkspace().getByRole("button", { name: "回到当前", exact: true }).click();
  await page.locator(".chat-composer .chat-target__viewing").waitFor({ state: "detached" });
  await page.getByRole("button", { name: "移除新话题", exact: true }).click();
  await page.getByRole("button", { name: "收起工具", exact: true }).click();
  let releaseZhSend;
  chatMessageResponseGate = new Promise((resolve) => { releaseZhSend = resolve; });
  await page.locator("#chat-input").fill("把阅览室加宽一跨");
  const heldZhSend = page.waitForRequest((req) => req.method() === "POST" && new URL(req.url()).pathname.endsWith("/messages"));
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await heldZhSend;
  await page.waitForFunction(() => document.querySelector(".chat-composer-note")?.textContent === "发送中…");
  await page.locator(".chat-main").screenshot({ path: path.join(temporary, "composer-sending-zh.png") });
  releaseZhSend(); chatMessageResponseGate = Promise.resolve();
  await page.getByRole("button", { name: "停止", exact: true }).waitFor();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  runtimeT.operations = []; emitRuntime();
  await notice.waitFor({ state: "detached" });

  // GH-300 batch F, in Chinese for review. GH-58: an operation that needs recovery the Hub cannot
  // give it says so beside 知道了, and can be dismissed.
  runtimeT.operations = [{ operationId: "candidate-lost-zh", projectId: "T", kind: "POST /api/proposals/prop-6/candidate", source: "chat",
    status: "needs_recovery", recoverable: false, committed: false, admissionSequence: 9, createdAt: sheetAskedAt }];
  emitRuntime();
  await notice.getByText("无法自动恢复", { exact: true }).waitFor();
  assert.equal(await notice.locator("p").innerText(), "有操作需要检查恢复结果");
  await page.locator(".chat-main").screenshot({ path: path.join(temporary, "operation-unrecoverable-zh.png") });
  await notice.getByRole("button", { name: "知道了", exact: true }).click();
  await notice.waitFor({ state: "detached" });

  // FN-2: a conversation waiting on the architect's permission is marked 需要你 in words and colour,
  // and its project row carries the smaller mark.
  const shownChatId = new URL(page.url()).searchParams.get("chatId");
  const waitingChat = sessions.find((row) => row.projectId === "T" && row.id !== shownChatId && !row.archived);
  waitingChat.status = "running";
  waitingChat.messages.push({ id: "needs-you-permission", role: "tool", status: "streaming", content: "studio_request · POST /api/proposals · in_progress",
    permission: { id: "permission-needs-you", title: "允许修改模型？", options: [{ optionId: "allow", name: "允许一次", kind: "allow_once" }] } });
  emitRuntime();
  const waitingRowZh = page.locator(".chat-thread-row").filter({ hasText: waitingChat.title });
  await waitingRowZh.locator(".chat-needs").filter({ hasText: /^需要你$/ }).waitFor();
  assert.equal(await waitingRowZh.locator(".chat-thread").getAttribute("aria-description"), "需要你的授权");
  const treeHead = page.locator(".chat-project__head").filter({ has: page.getByRole("button", { name: "Tree project", exact: true }) });
  assert.equal(await treeHead.locator('.chat-project__badge[data-kind="needs"]').innerText(), "需要你");
  assert.match(await treeHead.getByRole("button", { name: "Tree project", exact: true }).getAttribute("aria-description"), /1 个对话需要你$/);
  // The notice layer announces the same moment; it is only waited for here, for the picture.
  await page.locator('.attention-toast[data-kind="permission"]').waitFor({ timeout: 6000 }).catch(() => {});
  await treeHead.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(temporary, "needs-you-zh-1440.png") });
  waitingChat.messages.at(-1).permission = null; waitingChat.status = "idle";
  emitRuntime();
  await waitingRowZh.locator(".chat-needs").waitFor({ state: "detached" });
  await treeHead.locator('.chat-project__badge[data-kind="needs"]').waitFor({ state: "detached" });
  await page.locator(".attention-toast").waitFor({ state: "detached", timeout: 10000 }).catch(() => {});

  // PP-1: opening a tool shows the skeleton of its surface at once and names the step it waits on;
  // after 3 s, how long and 取消. Cancel stops waiting; the project service it asked for goes on starting.
  projects.push({ projectId: "S", projectDir: "D:\\fixture\\S", name: "Slow project", chatCount: 0, version: 0, stage: null });
  studioStartHold = { held: [] };
  emitRuntime();
  await page.getByRole("button", { name: "Slow project", exact: true }).first().click();
  for (const until = Date.now() + 12000; !studioStartHold.held.length; await page.waitForTimeout(50)) {
    if (Date.now() > until) assert.fail("the project service was never asked to start");
  }
  const modelingZh = page.getByRole("button", { name: "建模", exact: true }), skeletonZh = page.locator(".chat-skeleton");
  await modelingZh.click();
  await skeletonZh.waitFor();
  assert.equal(await skeletonZh.locator("h2").innerText(), "建模");
  await skeletonZh.getByText("正在启动项目服务…", { exact: true }).waitFor();
  assert.equal(await modelingZh.getAttribute("aria-pressed"), "true");
  assert.equal(await modelingZh.getAttribute("aria-busy"), "true");
  assert.equal(await modelingZh.getAttribute("aria-description"), "取消打开建模");
  assert.equal(await skeletonZh.getByRole("button", { name: "取消", exact: true }).count(), 0, "no Cancel in the first 3 s");
  await skeletonZh.getByRole("button", { name: "取消", exact: true }).waitFor();
  assert.match(await skeletonZh.locator(".chat-skeleton__slow").innerText(), /^已等待 \d+秒/);
  await page.screenshot({ path: path.join(temporary, "tool-skeleton-zh.png") });
  const beforeCancel = writes.length;
  await skeletonZh.getByRole("button", { name: "取消", exact: true }).click();
  await skeletonZh.waitFor({ state: "detached" });
  assert.equal(await page.locator(".chat-shell").getAttribute("data-panel"), "false", "Cancel returns to the conversation the tool was opened from");
  assert.equal(await modelingZh.getAttribute("aria-pressed"), "false");
  assert.equal(await modelingZh.getAttribute("aria-busy"), null);
  assert.deepEqual(writes.slice(beforeCancel), [], "Cancel asks the Hub for nothing: the starting service is left to start");
  releaseStudioStarts();
  await page.waitForFunction(() => document.querySelector('.chat-rail__tool[aria-label="建模"]')?.dataset.state === "running");
  await modelingZh.click();
  await waitWorkspace();

  // NA-2: below 900 px the chat header stays above an open panel and names the project; the Stage chip
  // is in whichever header is on top: the project surface's own bar when one is open, else this header.
  await page.getByRole("button", { name: "Tree project", exact: true }).first().click();
  const onTop = (locator) => locator.evaluate((node) => {
    const box = node.getBoundingClientRect();
    if (!box.width || !box.height) return false;
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return Boolean(hit) && (hit === node || node.contains(hit));
  });
  const headerProject = page.locator(".chat-header__project"), headerChipZh = page.locator(".chat-header__chip");
  const barChip = visibleWorkspace().locator(".stage-chip");
  for (const [width, height] of [[800, 900], [390, 844]]) {
    await page.setViewportSize({ width, height });
    if (await page.locator(".chat-sidebar").isVisible()) await page.getByRole("button", { name: "收起项目栏", exact: true }).first().click();
    if (await page.locator(".chat-shell").getAttribute("data-panel") === "true") await page.getByRole("button", { name: "收起工具", exact: true }).click();
    await headerChipZh.waitFor();
    assert.equal(await headerProject.innerText(), "Tree project");
    const chipWords = await headerChipZh.innerText();
    assert.match(chipWords, /^S2\b.* · 当前$/);
    assert.ok(await onTop(headerProject) && await onTop(headerChipZh), `${width} px: the conversation's header names the project and carries the chip`);
    if (width === 390) await page.screenshot({ path: path.join(temporary, "narrow-chat-390-zh.png") });
    await page.getByRole("button", { name: "建模", exact: true }).click();
    await waitWorkspace();
    await barChip.waitFor();
    assert.equal(await headerChipZh.count(), 0, `${width} px: one chip on screen`);
    assert.equal(await barChip.locator(".stage-chip__position").innerText(), chipWords, "the header says what the chip says");
    assert.ok(await onTop(headerProject), `${width} px: the project stays in view over Modeling`);
    assert.ok(await onTop(barChip), `${width} px: the Stage chip stays in view over Modeling`);
    await page.screenshot({ path: path.join(temporary, `narrow-modeling-${width}-zh.png`) });
    // Over a Tool without a project bar the chip is back in the header; IA-6 then returns to Modeling.
    await page.getByRole("button", { name: "用量", exact: true }).click();
    await page.locator(".chat-browser .monitor-page").waitFor();
    await headerChipZh.waitFor();
    assert.ok(await onTop(headerProject) && await onTop(headerChipZh), `${width} px: over Usage the chip is in the header`);
    await page.getByRole("button", { name: "用量", exact: true }).click();
    await waitWorkspace();
    await page.getByRole("button", { name: "收起工具", exact: true }).click();
  }
  // The header's chip opens the Design Tree, as the chip does.
  await headerChipZh.click();
  await visibleWorkspace().locator('[data-project-surface="tree"]:not([hidden])').waitFor();
  assert.equal(await page.getByRole("button", { name: "状态树", exact: true }).getAttribute("aria-pressed"), "true");
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "展开项目栏", exact: true }).first().click();
  await page.getByRole("button", { name: "Hub 设置", exact: true }).click();
  await page.locator("#language").selectOption("en");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();

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
    await autosavedModelRestart();
    // A fresh page: reloading would follow the address's link to Project D's Modeling, which
    // selects D again until it has opened, and could undo the click on Project A below.
    await page.goto(origin);
    await page.getByRole("button", { name: "Project A", exact: true }).first().click();
    await page.waitForFunction(() => document.querySelector("#chat-input") && !document.querySelector("#chat-input").disabled);
    await page.waitForFunction(() => document.querySelector(".chat-header__project")?.textContent === "Project A");
    await page.locator("#chat-input").fill("Keep this hidden project draft");
    await page.locator('.chat-composer input[type="file"]').setInputFiles({ name: "keep.txt", mimeType: "text/plain", buffer: Buffer.from("Retain these exact draft bytes") });
    await page.getByRole("button", { name: "Project B", exact: true }).first().click();
    await page.waitForFunction(() => document.querySelector("#chat-input")?.value === "");
    assert.equal(await page.locator(".chat-composer .chat-attachments li").count(), 0);
    updateStatus = { ...updateStatus, state: "ready", prepared: preparedPatch, canApply: true };
    await page.getByRole("button", { name: "Hub settings", exact: true }).click();
    await settingsPage("Software update");
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
  await settingsPage("Software update");
  await page.getByText("fixture-current-desktop", { exact: true }).waitFor();
  // Automatic updates are on by default and named as the unsigned prerelease
  // channel. The switch saves at once; checking never restarts the page.
  const autoSwitch = page.getByRole("switch", { name: /^Automatic updates:/ });
  await page.getByText("Automatic updates: On · Unsigned prerelease channel", { exact: true }).waitFor();
  assert.equal(await autoSwitch.isChecked(), true);
  await page.getByText("Last check: not yet", { exact: true }).waitFor();
  await autoSwitch.click();
  await page.getByText("Automatic updates: Off · Unsigned prerelease channel", { exact: true }).waitFor();
  assert.equal(await autoSwitch.isChecked(), false);
  await autoSwitch.click();
  await page.getByText("Automatic updates: On · Unsigned prerelease channel", { exact: true }).waitFor();
  assert.deepEqual(autoUpdateWrites, [{ autoUpdate: false }, { autoUpdate: true }]);
  const beforeCheckLoads = documentLoads;
  await page.getByRole("button", { name: "Check now", exact: true }).click();
  await page.getByText("Last check: downloading 0.1.6…", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Check now", exact: true }).isDisabled(), true);
  await page.getByText("0.1.6 is ready and takes effect the next time MonkeyHub starts.", { exact: true }).waitFor();
  await page.getByText(/^Last check: .+ · 0\.1\.6 prepared$/).waitFor();
  await page.getByText("0.1.6 · fixture-next-desktop", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isEnabled(), true, "restart now stays available");
  assert.equal(updateChecks, 1); assert.equal(documentLoads, beforeCheckLoads, "a check never reloads the page");
  updateStatus = { ...updateStatus, state: "idle", prepared: null, canApply: false, nextLaunch: false,
    check: { state: "needs-full-update", checkedAt: "2026-09-25T10:05:00+00:00", latestVersion: "0.1.9", detail: null, releaseUrl: "https://github.com/cogco1/MonkeyHub/releases/tag/v0.1.9" } };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText(/^Last check: .+ · 0\.1\.9 needs a full update$/).waitFor();
  await page.getByText("https://github.com/cogco1/MonkeyHub/releases/tag/v0.1.9", { exact: true }).waitFor();
  updateStatus = { ...updateStatus, check: { ...updateStatus.check, state: "error", detail: "GitHub releases could not be read: fixture offline" } };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText(/^Last check: .+ · did not succeed$/).waitFor();
  await page.getByText("GitHub releases could not be read: fixture offline", { exact: true }).waitFor();
  updateStatus = { ...updateStatus, check: { state: "never", checkedAt: null, latestVersion: null, detail: null, releaseUrl: null } };
  updateStatus = { ...updateStatus, mode: "unsupported" };
  await page.getByRole("button", { name: "Refresh status", exact: true }).click();
  await page.getByText("Patch updates require the installed MonkeyHub desktop app.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Choose patch ZIP", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: "Check now", exact: true }).count(), 0);
  assert.equal(await page.getByRole("switch").count(), 0);
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
  let releaseSettings; settingsWriteGate = new Promise((resolve) => { releaseSettings = resolve; });
  // Every earlier choice in this test saved itself, so the theme may already be dark.
  await settingsPage("Display");
  await page.locator("#theme").selectOption(await page.locator("#theme").inputValue() === "dark" ? "light" : "dark");
  await settingsPage("Software update");
  await page.getByText("A settings change is still being saved or could not be saved. Wait, or fix the marked setting, before restarting.", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Restart to update", exact: true }).isDisabled(), true);
  settingsWriteGate = null; releaseSettings();
  await settingsSaved();
  await settingsPage("Display");
  await page.locator("#theme").selectOption("dark"); await settingsSaved();
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find((button) => button.textContent === "Restart to update")?.disabled);
  // The desktop dialog is usable at a small viewport, in dark mode, with
  // larger text and reduced motion, without horizontal clipping.
  await page.locator("#font-scale").selectOption("1.1");
  await settingsSaved();
  await page.emulateMedia({ reducedMotion: "reduce" }); await page.setViewportSize({ width: 390, height: 844 });
  await settingsPage("Software update");
  await page.locator(".software-update").scrollIntoViewIfNeeded();
  assert.equal(await page.locator(".software-update").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true);
  assert.equal(await page.getByRole("dialog").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true);
  for (const button of await page.locator(".software-update__actions button").all()) assert.ok((await button.boundingBox()).height >= 44);
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-small-dark.png") });
  await settingsPage("Display");
  await page.locator("#language").selectOption("zh-CN"); await settingsSaved();
  await settingsPage("软件更新");
  await page.getByRole("heading", { name: "软件更新", exact: true }).waitFor();
  await page.getByText("自动更新：开 · 未签名预发布通道", { exact: true }).waitFor();
  await page.locator(".software-update").scrollIntoViewIfNeeded();
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-small-zh.png") });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("dialog").screenshot({ path: path.join(temporary, "software-update-wide-zh.png") });
  await settingsPage("显示");
  await page.locator("#language").selectOption("en"); await settingsSaved();
  await settingsPage("Software update");
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
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ passed: true, sessions: sessions.length, writes: writes.length, screenshots: temporary }));
} catch (error) { console.error(JSON.stringify({ screenshots: temporary, errors, workspaceRequests: workspaceFixture.requests.slice(-15) }));
  await page.screenshot({ path: path.join(temporary, "failure.png") }); throw error;
} finally { await browser.close(); await new Promise((resolve) => server.close(resolve)); }
