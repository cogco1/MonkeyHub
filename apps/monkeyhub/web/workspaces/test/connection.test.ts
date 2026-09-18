import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";

import type { WorkingCopyDto } from "../src/api/generated/index.ts";

test("concurrent project clients retain their runtime, token, exact source and responses", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const { createStudioClient, StudioApiError } = await vite.ssrLoadModule("/src/api/client.ts");
  const baseA = "http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio";
  const baseB = "http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789def/studio";
  const connectionA = new ServerConnection(baseA, "project-a-token");
  const studioA = createStudioClient(connectionA);
  let releaseA!: () => void;
  const gateA = new Promise<void>(resolve => { releaseA = resolve; });
  const requests: { request: Request; body: unknown }[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push({ request, body: request.method === "POST" ? await request.json() : null });
    const projectId = request.url.startsWith(baseA) ? "project-a" : "project-b";
    if (request.url.endsWith("/api/protocol")) {
      return Response.json({ protocol: "archflow/2", server: projectId, serverVersion: "0.1.0", mode: "local", capabilities: [] });
    }
    if (request.url.endsWith("/api/proposals")) {
      return Response.json({ code: "STALE_SOURCE", detail: `${projectId} exact source is stale` }, { status: 409 });
    }
    if (projectId === "project-a") await gateA;
    return Response.json({ projectId });
  });
  const sourceA = { sourceRunId: "candidate-a", stateDigest: "a".repeat(64) };
  const pendingA = studioA.state(sourceA.sourceRunId, "massing:stage-a");
  const connectionB = new ServerConnection(baseB);
  const studioB = createStudioClient(connectionB);
  assert.deepEqual(await studioB.state("candidate-b"), { projectId: "project-b" });
  await Promise.all([connectionA.probe(), connectionB.probe()]);
  releaseA();
  assert.deepEqual(await pendingA, { projectId: "project-a" });
  await assert.rejects(studioA.createProposal({ ...sourceA, utterance: "set height to 4" }), (error: unknown) => {
    assert.ok(error instanceof StudioApiError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "STALE_SOURCE");
    assert.equal(error.detail, "project-a exact source is stale");
    return true;
  });
  assert.equal(connectionA.server.server, "project-a");
  assert.equal(connectionB.server.server, "project-b");
  assert.equal(connectionA.url("/api/events"), `${baseA}/api/events`);
  assert.equal(connectionB.url("/api/events"), `${baseB}/api/events`);
  assert.equal(connectionA.authenticated, true);
  assert.equal(connectionB.authenticated, false);
  assert.equal(requests.find(({ request }) => request.url.startsWith(baseA) && request.method === "GET")!.request.url,
    `${baseA}/api/state?run=candidate-a&sourceStageRef=massing%3Astage-a`);
  for (const { request } of requests) {
    assert.equal(request.headers.get("Authorization"), request.url.startsWith(baseA) ? "Bearer project-a-token" : null);
  }
  assert.deepEqual(requests.at(-1)!.body, { ...sourceA, utterance: "set height to 4" });
  assert.match(requests.at(-1)!.request.headers.get("Idempotency-Key")!, /^[0-9a-f-]{36}$/);
});

test("sibling project providers expose independent clients and require an explicit binding", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { createElement, Fragment } = await import("react");
  const { renderToStaticMarkup } = await import("react-dom/server");
  const { ProjectRuntimeProvider, useStudio, useConnection } = await vite.ssrLoadModule("/src/api/ProjectRuntimeContext.tsx");
  const mounted: { studio: unknown; baseUrl: string; authenticated: boolean }[] = [];
  function Workspace() {
    const studio = useStudio(), connection = useConnection();
    mounted.push({ studio, baseUrl: connection.baseUrl, authenticated: connection.authenticated });
    return createElement("span", null, connection.url("/api/events"));
  }
  const markup = renderToStaticMarkup(createElement(Fragment, null,
    createElement(ProjectRuntimeProvider, { baseUrl: "http://project-a.test", token: "a" }, createElement(Workspace)),
    createElement(ProjectRuntimeProvider, { baseUrl: "http://project-b.test" }, createElement(Workspace)),
  ));
  assert.match(markup, /http:\/\/project-a\.test\/api\/events/);
  assert.match(markup, /http:\/\/project-b\.test\/api\/events/);
  assert.notEqual(mounted[0]!.studio, mounted[1]!.studio);
  assert.equal(mounted[0]!.authenticated, true);
  assert.equal(mounted[1]!.authenticated, false);
  assert.throws(() => renderToStaticMarkup(createElement(Workspace)), /project runtime provider is required/);
});

test("only Hub forwarding adds idempotency keys while retries preserve supplied keys", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const base = "http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio";
  const { client } = new ServerConnection(base);
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => { requests.push(request); return Response.json({}); });
  await client.get({ url: "/api/state" });
  await client.post({ url: "/api/proposals", headers: { "X-Monkey-Operation": "parent-trace" } });
  await client.post({ url: "/api/proposals", headers: { "X-Monkey-Operation": "parent-trace" } });
  await client.post({ url: "/api/proposals", headers: { "Idempotency-Key": "same-retry", "X-Monkey-Operation": "parent-trace" } });
  assert.equal(requests[0]!.headers.get("Idempotency-Key"), null);
  assert.equal(requests[0]!.url, `${base}/api/state`);
  assert.match(requests[1]!.headers.get("Idempotency-Key")!, /^[0-9a-f-]{36}$/);
  assert.notEqual(requests[1]!.headers.get("Idempotency-Key"), requests[2]!.headers.get("Idempotency-Key"));
  assert.equal(requests[1]!.headers.get("X-Monkey-Operation"), "parent-trace");
  assert.equal(requests[3]!.headers.get("Idempotency-Key"), "same-retry");
  for (const baseUrl of ["https://remote-studio.test", "http://127.0.0.1:18181"]) {
    const { client } = new ServerConnection(baseUrl, "fixture-token");
    await client.post({ url: "/api/proposals", body: { utterance: "set height to 4" } });
    const request = requests.at(-1)!;
    assert.equal(request.headers.get("Idempotency-Key"), null, "direct Studio keeps its existing CORS request headers");
    assert.equal(request.headers.get("Authorization"), "Bearer fixture-token");
    assert.equal(request.url, `${baseUrl}/api/proposals`);
  }
});

test("candidate requests carry their explicit source without changing default requests", async (t) => {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { createStudioClient } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection("http://studio.test"));

  const requests: { path: string; body: unknown }[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push({
      path: url.pathname + url.search,
      body: request.method === "POST" ? await request.json() : null,
    });
    return Response.json({});
  });
  const source = { stateDigest: "a".repeat(64), sourceRunId: "candidate-a" };
  const modelSource = { runId: source.sourceRunId, stateDigest: source.stateDigest, assetSha256: "c".repeat(64) };
  await studio.state(source.sourceRunId);
  await studio.frame(source.sourceRunId);
  await studio.volumes(source.sourceRunId);
  await studio.resolvePick({ ...source, userStrings: {} });
  await studio.closure({ ...source, changedRefs: ["entity:column"] });
  await studio.compileIntent({ ...source, utterance: "set height to 3" });
  await studio.createProposal({ ...source, modelSource, targetComponentId: "portico", utterance: "set height to 3" });
  assert.deepEqual(requests.slice(0, 3).map((r) => r.path), [
    "/api/state?run=candidate-a",
    "/api/state/frame?run=candidate-a",
    "/api/state/volumes?run=candidate-a",
  ]);
  for (const request of requests.slice(3)) {
    assert.deepEqual(
      { stateDigest: (request.body as typeof source).stateDigest, sourceRunId: (request.body as typeof source).sourceRunId },
      source,
    );
  }
  assert.deepEqual((requests.at(-1)!.body as { modelSource: unknown }).modelSource, modelSource);

  // Returning to the default is per request, not a hidden global SDK pointer.
  requests.length = 0;
  await studio.state();
  await studio.frame();
  await studio.volumes();
  await studio.compileIntent({ stateDigest: "b".repeat(64), utterance: "set height to 4" });
  assert.deepEqual(requests.slice(0, 3).map((r) => r.path), [
    "/api/state", "/api/state/frame", "/api/state/volumes",
  ]);
  for (const request of requests.slice(3)) {
    assert.equal(Object.hasOwn(request.body as object, "sourceRunId"), false);
    assert.equal(Object.hasOwn(request.body as object, "modelSource"), false);
  }
});

async function editingSessionHarness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const key = "archflow-studio.user-preferences";
  const oldPreferences = { version: 1, language: "zh-CN", theme: "light", fontScale: 1.1, eventStreamVisible: false };
  const storage = new Map([[key, JSON.stringify(oldPreferences)]]);
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const workingCopy: WorkingCopyDto = {
    projectId: "project-a", groupId: "massing-options", label: "Massing options", stageId: "massing",
    commonBase: { runId: "default-a", stateDigest: "b".repeat(64), assetSha256: "1".repeat(64) },
    scope: ["entity:building"],
    options: [
      { id: "option-a", label: "A", modelSource: { runId: "chosen-a", stateDigest: "a".repeat(64), assetSha256: "2".repeat(64) } },
      { id: "option-b", label: "B", modelSource: { runId: "candidate-b", stateDigest: "b".repeat(64), assetSha256: "3".repeat(64) } },
    ],
    selectedOptionId: "option-b", revisionSha256: "4".repeat(64),
  };
  const control = {
    projectId: "project-a", defaultRun: "default-a", storageBlocked: false, storageReadBlocked: false,
    projectReply: null as null | (() => Response | Promise<Response>),
    stateReply: null as null | ((run: string) => Response | Promise<Response>),
    designHistoryReply: null as null | (() => Response | Promise<Response>),
    workingCopies: [workingCopy],
    workingCopiesReply: null as null | (() => Response | Promise<Response>),
  };
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://studio.test/", search: "" },
    localStorage: {
      getItem: (name: string) => {
        if (control.storageReadBlocked) throw new Error("Site storage cannot be read");
        return storage.get(name) ?? null;
      },
      setItem: (name: string, value: string) => {
        if (control.storageBlocked) throw new Error("Site storage is disabled");
        storage.set(name, value);
      },
    },
  } });
  t.after(() => {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  });
  const sessionModule = await vite.ssrLoadModule("/src/app/useSession.ts");
  const { createStudioClient } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const { editingBasePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  const studio = createStudioClient(new ServerConnection("http://studio.test"));
  const requests: string[] = [];
  const published = { version: 0, stateSha256: "c".repeat(64) };
  const projection = (run: string) => ({
    projectId: control.projectId, published,
    referenceRun: { runId: run, baseVersion: 0, baseSha256: published.stateSha256 },
    stateDigest: run === "chosen-a" ? "a".repeat(64) : "b".repeat(64),
    matchesReferenceReceipt: true, honesty: [],
  });
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push(`${request.method} ${url.pathname}${url.search}`);
    assert.equal(request.method, "GET", "restoring a choice must not submit design work");
    if (url.pathname === "/api/project") return control.projectReply ? control.projectReply() : Response.json({ projectId: control.projectId, published });
    if (url.pathname === "/api/working-copies") {
      return control.workingCopiesReply ? control.workingCopiesReply() : Response.json({ workingCopies: control.workingCopies });
    }
    if (url.pathname === "/api/design-history") {
      return control.designHistoryReply ? control.designHistoryReply() : Response.json({ branchId: "main", branches: [], stages: [] });
    }
    assert.equal(url.pathname, "/api/state");
    const run = url.searchParams.get("run") ?? control.defaultRun;
    return control.stateReply ? control.stateReply(run) : Response.json(projection(run));
  });
  return {
    ...sessionModule,
    createSessionController: (...args: unknown[]) => sessionModule.createSessionController(studio, ...args),
    studio, editingBasePreferences, requests, control, projection, storage, key, oldPreferences,
  };
}

test("working-copy capability permits a cold list read without selecting its option as the editing base", async (t) => {
  const h = await editingSessionHarness(t);
  const legacy = h.createSessionController("", ["events"]);
  await legacy.reload();
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state"]);
  assert.deepEqual(legacy.getSnapshot().session.value.workingCopies, []);

  h.requests.length = 0;
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload();
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/working-copies", "GET /api/state"]);
  assert.deepEqual(controller.getSnapshot().session.value.workingCopies, h.control.workingCopies);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, null);
  assert.equal(controller.getSnapshot().session.value.projection.referenceRun.runId, "default-a");
  assert.deepEqual(JSON.parse(h.storage.get(h.key)!), h.oldPreferences);
});

test("fresh working-copy selections restore on reload and reopen while an explicit candidate remains the editing base", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  assert.equal(controller.getSnapshot().session.value.workingCopies[0].selectedOptionId, "option-b");
  const savedChoice = h.storage.get(h.key);

  const group = h.control.workingCopies[0];
  h.control.workingCopies = [{
    ...group, selectedOptionId: "option-c", revisionSha256: "5".repeat(64),
    options: [...group.options, {
      id: "option-c", label: "C",
      modelSource: { runId: "candidate-c", stateDigest: "c".repeat(64), assetSha256: "6".repeat(64) },
    }],
  }];
  for (const session of [controller, h.createSessionController("", ["working-copies"])]) {
    h.requests.length = 0;
    await session.reload();
    assert.deepEqual(h.requests, ["GET /api/project", "GET /api/working-copies", "GET /api/state?run=chosen-a"]);
    assert.deepEqual(session.getSnapshot().session.value.workingCopies, h.control.workingCopies);
    assert.equal(session.getSnapshot().session.value.sourceRunId, "chosen-a");
    assert.equal(session.getSnapshot().session.value.projection.referenceRun.runId, "chosen-a");
    assert.equal(h.storage.get(h.key), savedChoice, "reading a server selection must not rewrite the explicit base");
  }
});

test("background session reload keeps the ready base unlocked until the exact candidate arrives", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", [], false);
  await controller.reload("chosen-a");
  const previous = controller.getSnapshot().session;
  let release!: (response: Response) => void;
  let start!: () => void;
  const response = new Promise<Response>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { start = resolve; });
  h.control.stateReply = () => { start(); return response; };
  const transitions: { status: string; changingBase: boolean }[] = [];
  const unsubscribe = controller.subscribe(() => {
    const value = controller.getSnapshot();
    transitions.push({ status: value.session.status, changingBase: value.changingBase });
  });
  const pending = controller.reload("candidate-b", undefined, undefined, true);
  assert.equal(controller.getSnapshot().session, previous);
  assert.equal(controller.getSnapshot().changingBase, false);
  await started;
  assert.equal(controller.getSnapshot().session, previous);
  assert.equal(h.editingDigestForView(previous, controller.getSnapshot().changingBase, "chosen-a", false, false), "a".repeat(64));
  release(Response.json(h.projection("candidate-b")));
  const next = await pending;
  unsubscribe();
  assert.equal(next.sourceRunId, "candidate-b");
  assert.equal(next.projection.referenceRun.runId, "candidate-b");
  assert.equal(next.projection.stateDigest, "b".repeat(64));
  assert.equal(controller.getSnapshot().session.value, next);
  assert.ok(transitions.every(value => value.status === "ready" && value.changingBase === false));
});

test("a later explicit base switch wins over a delayed background session reload", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  let release!: (response: Response) => void;
  let start!: () => void;
  const response = new Promise<Response>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { start = resolve; });
  h.control.stateReply = run => run === "candidate-b" ? (start(), response) : Response.json(h.projection(run));
  const background = controller.reload("candidate-b", undefined, undefined, true);
  await started;
  const explicit = controller.reload("chosen-c");
  assert.equal(controller.getSnapshot().changingBase, true, "ordinary explicit switching still locks the editing base");
  await explicit;
  const chosen = controller.getSnapshot();
  release(Response.json(h.projection("candidate-b")));
  assert.equal(await background, null);
  assert.equal(controller.getSnapshot(), chosen);
  assert.equal(chosen.session.value.sourceRunId, "chosen-c");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-c");
});

test("background reload still refuses an unverified candidate and retains the previous explicit base", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  const previous = controller.getSnapshot().session;
  h.control.stateReply = run => Response.json({ ...h.projection(run), matchesReferenceReceipt: false });
  assert.equal(await controller.reload("candidate-b", undefined, undefined, true), null);
  const after = controller.getSnapshot();
  assert.equal(after.session, previous);
  assert.equal(after.changingBase, false);
  assert.equal(after.baseError.code, "EDITING_BASE_UNAVAILABLE");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
});

test("quiet candidate reload reuses its one state read and preserves default selection policy", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"], false);
  await controller.reload("chosen-a");
  const preference = h.storage.get(h.key);
  h.requests.length = 0;
  const projection = await h.studio.state("candidate-b");
  const next = await controller.reload("candidate-b", undefined, undefined, true, projection);
  assert.equal(next.projection, projection);
  assert.deepEqual(h.requests, ["GET /api/state?run=candidate-b", "GET /api/project", "GET /api/working-copies"]);
  assert.equal(h.storage.get(h.key), preference, "reusing a read must not enable a disabled preference writer");
  for (const run of [undefined, null]) {
    h.requests.length = 0;
    const selected = await controller.reload(run, undefined, undefined, true, projection);
    assert.equal(h.requests.filter(path => path.startsWith("GET /api/state")).length, 1,
      "default and implicit refresh still read their own source");
    assert.notEqual(selected.projection, projection);
  }
});

test("a temporary background project read preserves the ready editing context while a rebound project is refused", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  const original = controller.getSnapshot();
  const stored = h.storage.get(h.key);
  h.control.projectReply = () => Response.json({ code: "RUNTIME_UNAVAILABLE", detail: "Try again." }, { status: 503 });
  h.requests.length = 0;
  assert.equal(await controller.reload(undefined, undefined, undefined, true), null);
  assert.deepEqual(h.requests, ["GET /api/project"]);
  assert.equal(controller.getSnapshot().session, original.session);
  assert.equal(controller.getSnapshot().binding, original.binding);
  assert.equal(controller.getSnapshot().baseError.status, 503);
  assert.equal(controller.getSnapshot().changingBase, false);
  assert.equal(h.storage.get(h.key), stored);

  h.control.projectReply = null;
  assert.equal((await controller.reload(undefined, undefined, undefined, true)).sourceRunId, "chosen-a");
  assert.equal(controller.getSnapshot().baseError, null);
  assert.equal(h.storage.get(h.key), stored, "a successful retry restores without recording another selection");

  h.control.projectId = "another-project";
  h.requests.length = 0;
  assert.equal(await controller.reload(undefined, undefined, undefined, true), null);
  assert.equal(controller.getSnapshot().session.status, "failed");
  assert.equal(controller.getSnapshot().baseError.code, "EDITING_PROJECT_CHANGED");
  assert.equal(controller.getSnapshot().binding.projectId, "project-a");
  assert.deepEqual(h.requests, ["GET /api/project"], "the previous run is never queried against another project");
  assert.equal(h.storage.get(h.key), stored);
});

test("a reused projection still rejects a foreign project, run, published base, receipt or Stage", async (t) => {
  const h = await editingSessionHarness(t);
  const valid = h.projection("candidate-b");
  const cases = [
    { ...valid, projectId: "another-project" },
    { ...valid, referenceRun: { ...valid.referenceRun, runId: "another-run" } },
    { ...valid, published: { ...valid.published, version: 99 } },
    { ...valid, published: { ...valid.published, stateSha256: "wrong-published-sha" } },
    { ...valid, stateDigest: null },
    { ...valid, matchesReferenceReceipt: false },
    { ...valid, referenceRun: { ...valid.referenceRun, baseVersion: 99 } },
    { ...valid, referenceRun: { ...valid.referenceRun, baseSha256: "wrong-base-sha" } },
  ];
  for (const forged of cases) {
    const controller = h.createSessionController();
    await controller.reload("chosen-a");
    assert.equal(await controller.reload("candidate-b", undefined, undefined, true, forged), null);
    assert.ok(controller.getSnapshot().baseError);
    assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  }
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  assert.equal(await controller.reload("candidate-b", "stage:requested", undefined, true,
    { ...valid, sourceStageRef: "stage:other" }), null);
  assert.equal(controller.getSnapshot().baseError.code, "EDITING_PROJECT_CHANGED");
  const matching = { ...valid, sourceStageRef: "stage:requested" };
  assert.equal((await controller.reload("candidate-b", "stage:requested", undefined, true, matching)).projection, matching);
});

test("session metadata reads start together after project identity is checked", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["design-history", "working-copies"], false);
  await controller.reload("chosen-a");
  let releaseHistory!: (response: Response) => void;
  let releaseCopies!: (response: Response) => void;
  let copiesStarted!: () => void;
  const history = new Promise<Response>(resolve => { releaseHistory = resolve; });
  const copies = new Promise<Response>(resolve => { releaseCopies = resolve; });
  const started = new Promise<void>(resolve => { copiesStarted = resolve; });
  h.control.designHistoryReply = () => history;
  h.control.workingCopiesReply = () => { copiesStarted(); return copies; };
  h.requests.length = 0;
  const pending = controller.reload("candidate-b", undefined, undefined, true, h.projection("candidate-b"));
  await started;
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/design-history?branchId=main", "GET /api/working-copies"]);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(controller.getSnapshot().changingBase, false);
  releaseHistory(Response.json({ branchId: "main", branches: [], stages: [] }));
  releaseCopies(Response.json({ workingCopies: h.control.workingCopies }));
  assert.equal((await pending).sourceRunId, "candidate-b");
});

test("a late reused projection cannot supersede a newer explicit run or its preference", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  let release!: (response: Response) => void;
  let start!: () => void;
  const response = new Promise<Response>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { start = resolve; });
  h.control.workingCopiesReply = () => { start(); return response; };
  const background = controller.reload("candidate-b", undefined, undefined, true, h.projection("candidate-b"));
  await started;
  h.control.workingCopiesReply = null;
  await controller.reload("chosen-c");
  const chosen = controller.getSnapshot();
  release(Response.json({ workingCopies: h.control.workingCopies }));
  assert.equal(await background, null);
  assert.equal(controller.getSnapshot(), chosen);
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-c");
  assert.equal(h.requests.includes("GET /api/state?run=candidate-b"), false);
});

test("background version refresh changes only the list and never reloads or selects the editing base", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  const before = controller.getSnapshot();
  const preference = h.storage.get(h.key);
  h.control.workingCopies = [{ ...h.control.workingCopies[0], label: "New version available", revisionSha256: "5".repeat(64) }];
  const transitions: { status: string; changingBase: boolean }[] = [];
  const unsubscribe = controller.subscribe(() => {
    const snapshot = controller.getSnapshot();
    transitions.push({ status: snapshot.session.status, changingBase: snapshot.changingBase });
  });
  h.requests.length = 0;
  assert.deepEqual(await controller.refreshWorkingCopies(), h.control.workingCopies);
  unsubscribe();
  const after = controller.getSnapshot();
  assert.deepEqual(h.requests, ["GET /api/working-copies"]);
  assert.deepEqual(transitions, [{ status: "ready", changingBase: false }]);
  assert.equal(after.session.value.project, before.session.value.project);
  assert.equal(after.session.value.projection, before.session.value.projection);
  assert.equal(after.session.value.sourceRunId, "chosen-a");
  assert.equal(after.baseError, before.baseError);
  assert.equal(after.persistenceFailed, before.persistenceFailed);
  assert.equal(h.storage.get(h.key), preference);
  const legacy = h.createSessionController();
  await legacy.reload();
  h.requests.length = 0;
  assert.equal(await legacy.refreshWorkingCopies(), null);
  assert.deepEqual(h.requests, []);
});

test("a failed or foreign background version list preserves the ready session and saved choice", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  const before = controller.getSnapshot();
  const preference = h.storage.get(h.key);
  h.control.workingCopiesReply = () => new Response("Storage unavailable", { status: 503 });
  await assert.rejects(controller.refreshWorkingCopies(), { status: 503 });
  assert.equal(controller.getSnapshot(), before);
  h.control.workingCopiesReply = () => Response.json({ workingCopies: [{ ...h.control.workingCopies[0], projectId: "another-project" }] });
  await assert.rejects(controller.refreshWorkingCopies(), { code: "EDITING_PROJECT_CHANGED" });
  assert.equal(controller.getSnapshot(), before);
  assert.equal(h.storage.get(h.key), preference);
});

test("late background lists cannot replace a newer list, an explicit continuation, or a cancelled session", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController("", ["working-copies"]);
  await controller.reload("chosen-a");
  const original = structuredClone(h.control.workingCopies);
  const hold = () => {
    let release!: (response: Response) => void;
    let start!: () => void;
    const response = new Promise<Response>((resolve) => { release = resolve; });
    const started = new Promise<void>((resolve) => { start = resolve; });
    h.control.workingCopiesReply = () => { start(); return response; };
    return { release, started };
  };
  const first = hold();
  const lateList = controller.refreshWorkingCopies();
  await first.started;
  h.control.workingCopiesReply = null;
  h.control.workingCopies = [{ ...original[0], label: "Latest list" }];
  await controller.refreshWorkingCopies();
  first.release(Response.json({ workingCopies: original }));
  assert.equal(await lateList, null);
  assert.equal(controller.getSnapshot().session.value.workingCopies[0].label, "Latest list");

  const second = hold();
  const previousBase = controller.refreshWorkingCopies();
  await second.started;
  h.control.workingCopiesReply = null;
  await controller.reload("candidate-b");
  const continued = controller.getSnapshot();
  second.release(Response.json({ workingCopies: original }));
  assert.equal(await previousBase, null);
  assert.equal(controller.getSnapshot(), continued);
  assert.equal(continued.session.value.sourceRunId, "candidate-b");

  const third = hold();
  const cancelled = controller.refreshWorkingCopies();
  await third.started;
  controller.cancel();
  third.release(new Response("Late failure", { status: 503 }));
  assert.equal(await cancelled, null);
  assert.equal(controller.getSnapshot(), continued);
});

test("a failed working-copy list read remains visible and retry preserves the saved explicit base", async (t) => {
  const h = await editingSessionHarness(t);
  await h.createSessionController().reload("chosen-a");
  h.control.workingCopiesReply = () => new Response("Working-copy storage unavailable", { status: 503 });
  h.requests.length = 0;
  const controller = h.createSessionController("", ["working-copies"]);
  assert.equal(await controller.reload(), null);
  assert.equal(controller.getSnapshot().session.status, "failed");
  assert.equal(controller.getSnapshot().baseError.status, 503);
  assert.match(controller.getSnapshot().baseError.detail, /working-copies.*503/);
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/working-copies"]);
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");

  h.control.workingCopiesReply = null;
  h.requests.length = 0;
  await controller.reload();
  assert.equal(controller.getSnapshot().baseError, null);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.deepEqual(controller.getSnapshot().session.value.workingCopies, h.control.workingCopies);
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/working-copies", "GET /api/state?run=chosen-a"]);
});

test("explicit continuation survives reopening, while browsing never records a choice", async (t) => {
  const h = await editingSessionHarness(t);
  const first = h.createSessionController();
  await first.reload();
  assert.equal(first.getSnapshot().session.value.sourceRunId, null);
  assert.equal(h.editingBasePreferences.read("", "project-a"), null);
  await first.reload("chosen-a");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  await h.studio.state("looked-at-b");
  assert.equal(first.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  assert.equal(h.editingDigestForView(first.getSnapshot().session, false, "looked-at-b", false, false), null);
  assert.equal(h.editingDigestForView(first.getSnapshot().session, false, "chosen-a", false, false), "a".repeat(64));
  assert.equal(h.editingDigestForView(first.getSnapshot().session, false, null, true, false), null);
  assert.equal(h.editingDigestForView(first.getSnapshot().session, false, "chosen-a", false, true), null);
  h.requests.length = 0;
  const reopened = h.createSessionController();
  await reopened.reload();
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state?run=chosen-a"]);
  assert.equal(reopened.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(reopened.getSnapshot().session.value.projection.referenceRun.runId, "chosen-a");
  const saved = JSON.parse(h.storage.get(h.key)!);
  for (const [key, value] of Object.entries(h.oldPreferences)) assert.equal(saved[key], value);
  assert.equal(h.storage.size, 1, "choices share the existing preference record");
});

test("failed restoration retains the choice and blocks editing until retry or explicit default", async (t) => {
  const h = await editingSessionHarness(t);
  await h.createSessionController().reload("chosen-a");
  h.control.stateReply = () => Response.json({ code: "RUN_NOT_FOUND", detail: "chosen-a is unavailable" }, { status: 404 });
  h.requests.length = 0;
  const reopened = h.createSessionController();
  assert.equal(await reopened.reload(), null);
  assert.equal(reopened.getSnapshot().session.status, "failed");
  assert.equal(reopened.getSnapshot().baseError.code, "RUN_NOT_FOUND");
  assert.equal(h.editingDigestForView(reopened.getSnapshot().session, false, "chosen-a", false, false), null);
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state?run=chosen-a"]);
  h.control.stateReply = null;
  await reopened.reload();
  assert.equal(reopened.getSnapshot().session.value.sourceRunId, "chosen-a");
  await reopened.reload(null);
  assert.equal(h.editingBasePreferences.read("", "project-a"), null);
  h.control.defaultRun = "new-default";
  const nextTab = h.createSessionController();
  await nextTab.reload();
  assert.equal(nextTab.getSnapshot().session.value.projection.referenceRun.runId, "new-default");
  assert.equal(nextTab.getSnapshot().session.value.sourceRunId, null);
});

test("a storage read exception blocks restoration until retry or an explicit default choice", async (t) => {
  const h = await editingSessionHarness(t);
  await h.createSessionController().reload("chosen-a");
  const saved = h.storage.get(h.key);
  h.control.storageReadBlocked = true;
  h.requests.length = 0;
  const reopened = h.createSessionController();
  assert.equal(await reopened.reload(), null);
  assert.equal(reopened.getSnapshot().session.status, "failed");
  assert.ok(reopened.getSnapshot().baseError);
  assert.equal(h.editingDigestForView(reopened.getSnapshot().session, false, null, false, false), null);
  assert.deepEqual(h.requests, ["GET /api/project"], "an unknown saved choice must not request the default state");
  assert.equal(h.storage.get(h.key), saved);

  h.control.storageReadBlocked = false;
  h.requests.length = 0;
  await reopened.reload();
  assert.equal(reopened.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state?run=chosen-a"]);

  h.control.storageReadBlocked = true;
  const anotherTab = h.createSessionController();
  await anotherTab.reload();
  h.requests.length = 0;
  await anotherTab.reload(null);
  assert.equal(anotherTab.getSnapshot().session.value.sourceRunId, null);
  assert.equal(anotherTab.getSnapshot().session.value.projection.referenceRun.runId, "default-a");
  assert.equal(anotherTab.getSnapshot().persistenceFailed, true);
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state"]);
  assert.equal(h.storage.get(h.key), saved, "do not overwrite unreadable choices from this or other projects");

  h.control.storageReadBlocked = false;
  await anotherTab.reload(null);
  assert.equal(anotherTab.getSnapshot().persistenceFailed, false);
  assert.equal(h.editingBasePreferences.read("", "project-a"), null);
  const nextTab = h.createSessionController();
  await nextTab.reload();
  assert.equal(nextTab.getSnapshot().session.value.sourceRunId, null);
});

test("an inspectable but unverified or stale record is not restored as an editing choice", async (t) => {
  const h = await editingSessionHarness(t);
  await h.createSessionController().reload("chosen-a");
  for (const invalid of [
    { stateDigest: null },
    { matchesReferenceReceipt: false },
    { matchesReferenceReceipt: null },
    { published: { version: 1, stateSha256: "d".repeat(64) } },
    { projectId: "another-project" },
    { referenceRun: { runId: "another-run", baseVersion: 0, baseSha256: "c".repeat(64) } },
  ]) {
    h.control.stateReply = (run) => Response.json({ ...h.projection(run), ...invalid });
    const controller = h.createSessionController();
    assert.equal(await controller.reload(), null);
    assert.equal(controller.getSnapshot().session.status, "failed");
    assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  }
});

test("a mounted runtime refuses project rebinding and new runtimes restore only their own choice", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  h.control.projectId = "project-b";
  h.control.defaultRun = "default-b";
  h.requests.length = 0;
  assert.equal(await controller.reload(), null);
  assert.deepEqual(h.requests, ["GET /api/project"]);
  assert.equal(controller.getSnapshot().session.error.code, "EDITING_PROJECT_CHANGED");
  assert.equal(controller.getSnapshot().binding.projectId, "project-a");
  assert.equal(h.editingBasePreferences.read("", "project-b"), null);
  const projectB = h.createSessionController();
  await projectB.reload();
  assert.equal(projectB.getSnapshot().session.value.sourceRunId, null);
  await projectB.reload("chosen-b");
  h.control.projectId = "project-a";
  await controller.reload();
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  const otherServer = h.createSessionController("https://other.test");
  await otherServer.reload();
  assert.equal(otherServer.getSnapshot().session.value.sourceRunId, null);
  await otherServer.reload("remote-choice");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  assert.equal(h.editingBasePreferences.read("https://other.test", "project-a"), "remote-choice");
  h.control.projectId = "project-b";
  h.requests.length = 0;
  assert.equal(await controller.reload("old-project-click"), null);
  assert.deepEqual(h.requests, ["GET /api/project"]);
  assert.equal(h.editingBasePreferences.read("", "project-b"), "chosen-b");
});

test("late and cancelled reads cannot replace a newer explicit choice", async (t) => {
  const h = await editingSessionHarness(t);
  let release!: (value: Response) => void;
  let started!: () => void;
  const waiting = new Promise<Response>((resolve) => { release = resolve; });
  const reading = new Promise<void>((resolve) => { started = resolve; });
  h.control.stateReply = (run) => {
    if (run === "slow-choice") { started(); return waiting; }
    return Response.json(h.projection(run));
  };
  const controller = h.createSessionController();
  const slow = controller.reload("slow-choice");
  await reading;
  await controller.reload("chosen-a");
  release(Response.json(h.projection("slow-choice")));
  assert.equal(await slow, null);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  const cancelled = controller.reload("cancelled-choice");
  controller.cancel();
  assert.equal(await cancelled, null);
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
});

test("legacy preferences and cleared site data use the default; unreadable pointers offer recovery", async (t) => {
  const h = await editingSessionHarness(t);
  await h.createSessionController().reload();
  assert.deepEqual(JSON.parse(h.storage.get(h.key)!), h.oldPreferences);
  h.storage.set(h.key, JSON.stringify({ ...h.oldPreferences, editingBases: { '["","project-a"]': 42 } }));
  const controller = h.createSessionController();
  assert.equal(await controller.reload(), null);
  assert.match(controller.getSnapshot().baseError.detail, /unreadable/);
  await controller.reload(null);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, null);
  await controller.reload("chosen-a");
  h.storage.clear();
  const reopened = h.createSessionController();
  await reopened.reload();
  assert.equal(reopened.getSnapshot().session.value.sourceRunId, null);
});

test("a refused explicit switch keeps the previous base; failed revalidation does not", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  h.control.stateReply = () => Response.json({ code: "STATE_RECORD_INVALID", detail: "record is unreadable" }, { status: 422 });
  assert.equal(await controller.reload("bad-choice"), null);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(h.editingBasePreferences.read("", "project-a"), "chosen-a");
  assert.equal(await controller.reload(), null);
  assert.equal(controller.getSnapshot().session.status, "failed");
});

test("storage failure is reported without losing the valid in-tab editing choice", async (t) => {
  const h = await editingSessionHarness(t);
  h.control.storageBlocked = true;
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(controller.getSnapshot().persistenceFailed, true);
  await controller.reload();
  assert.equal(controller.getSnapshot().session.value.sourceRunId, "chosen-a");
  assert.equal(h.editingBasePreferences.read("", "project-a"), null);
});

test("the protocol handshake accepts major 2 and refuses major 1", async (t) => {
  // Vite resolves the generated SDK imports and import.meta.env as it does
  // for the app, while the request below stays in the test's fetch mock.
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const { ServerConnection, ProtocolRefusal, PROTOCOL_MISMATCH } =
    await vite.ssrLoadModule("/src/api/connection.ts");

  for (const major of [2, 1]) {
    await t.test(`archflow/${major}`, async (t) => {
      const identity = {
        protocol: `archflow/${major}`,
        server: "monkeyarch-api",
        serverVersion: "0.1.0",
        mode: "local",
        capabilities: ["events", "validation"],
      };
      const requests: string[] = [];
      t.mock.method(globalThis, "fetch", async (request: Request) => {
        requests.push(`${request.method} ${request.url}`);
        return Response.json(identity);
      });
      const connection = new ServerConnection("http://studio.test");

      if (major === 2) {
        assert.deepEqual(await connection.probe(), identity);
        assert.deepEqual(connection.server, identity);
      } else {
        await assert.rejects(connection.probe(), (error: unknown) => {
          assert.ok(error instanceof ProtocolRefusal);
          assert.equal(error.code, PROTOCOL_MISMATCH);
          assert.deepEqual(error.identity, identity);
          assert.match(error.detail, /speaks archflow\/1; this client speaks archflow\/2/);
          return true;
        });
        assert.equal(connection.server, null);
      }

      assert.deepEqual(requests, ["GET http://studio.test/api/protocol"]);
    });
  }
});
