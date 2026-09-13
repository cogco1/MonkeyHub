import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";

import type { WorkingCopyDto } from "../src/api/generated/index.ts";

test("embedded API routing accepts only the actual loopback Hub parent", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { embeddedHubBaseUrl } = await vite.ssrLoadModule("/src/api/connection.ts");
  const path = "/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio";
  const base = `http://127.0.0.1:18180${path}`;
  const page = (url: string, embedded = "tool") => `http://127.0.0.1:18181/?${new URLSearchParams({ embedded, hubApi: url })}`;
  assert.equal(embeddedHubBaseUrl(page(base), "http://127.0.0.1:18180/"), base);
  assert.equal(embeddedHubBaseUrl(page(base), "http://127.0.0.1:18180/?view=chat"), base);
  for (const [url, parent, embedded] of [
    [base, "", "tool"], [base, "http://127.0.0.1:19180/", "tool"], [base, "http://127.0.0.1:18180/", "false"],
    [`https://127.0.0.1:18180${path}`, "https://127.0.0.1:18180/", "tool"],
    [`http://example.test${path}`, "http://example.test/", "tool"],
    [`http://user@127.0.0.1:18180${path}`, "http://127.0.0.1:18180/", "tool"],
    [`${base}?token=x`, "http://127.0.0.1:18180/", "tool"], [`${base}#extra`, "http://127.0.0.1:18180/", "tool"],
    [base.replace("/studio", "/other"), "http://127.0.0.1:18180/", "tool"],
    [base.replace("12345678-1234-1234-1234-123456789abc", "other-project"), "http://127.0.0.1:18180/", "tool"],
  ]) assert.equal(embeddedHubBaseUrl(page(url!, embedded), parent), null, `${url} with ${parent}`);
});

test("only Hub forwarding adds idempotency keys while retries preserve supplied keys", async (t) => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const { client } = await vite.ssrLoadModule("/src/api/generated/client.gen.ts");
  const base = "http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio";
  new ServerConnection(base).configure();
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
    new ServerConnection(baseUrl, "fixture-token").configure();
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
  const { studio } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  new ServerConnection("http://studio.test").configure();

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
  await studio.options(source.sourceRunId);
  await studio.program(source.sourceRunId);
  await studio.makeOption({ ...source, modelSource, transform: "add_floor" });
  await studio.applyProgram({ ...source, modelSource, sheet: {} as never });
  await studio.resolvePick({ ...source, userStrings: {} });
  await studio.closure({ ...source, changedRefs: ["entity:column"] });
  await studio.compileIntent({ ...source, utterance: "set height to 3" });
  await studio.createProposal({ ...source, targetComponentId: "portico", utterance: "set height to 3" });
  assert.deepEqual(requests.slice(0, 5).map((r) => r.path), [
    "/api/state?run=candidate-a",
    "/api/state/frame?run=candidate-a",
    "/api/state/volumes?run=candidate-a",
    "/api/options?run=candidate-a",
    "/api/program?run=candidate-a",
  ]);
  for (const request of requests.slice(5)) {
    assert.deepEqual(
      { stateDigest: (request.body as typeof source).stateDigest, sourceRunId: (request.body as typeof source).sourceRunId },
      source,
    );
  }
  for (const request of requests.slice(5, 7)) {
    assert.deepEqual((request.body as { modelSource: unknown }).modelSource, modelSource);
  }

  // Returning to the default is per request, not a hidden global SDK pointer.
  requests.length = 0;
  await studio.state();
  await studio.frame();
  await studio.volumes();
  await studio.options();
  await studio.program();
  await studio.makeOption({ stateDigest: "b".repeat(64), transform: "add_floor" });
  await studio.applyProgram({ stateDigest: "b".repeat(64), sheet: {} as never });
  await studio.compileIntent({ stateDigest: "b".repeat(64), utterance: "set height to 4" });
  assert.deepEqual(requests.slice(0, 5).map((r) => r.path), [
    "/api/state", "/api/state/frame", "/api/state/volumes", "/api/options", "/api/program",
  ]);
  for (const request of requests.slice(5)) {
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
    stateReply: null as null | ((run: string) => Response | Promise<Response>),
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
  const { studio } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const { editingBasePreferences } = await vite.ssrLoadModule("/src/features/settings/preferences.tsx");
  new ServerConnection("http://studio.test").configure();
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
    if (url.pathname === "/api/project") return Response.json({ projectId: control.projectId, published });
    if (url.pathname === "/api/working-copies") {
      return control.workingCopiesReply ? control.workingCopiesReply() : Response.json({ workingCopies: control.workingCopies });
    }
    assert.equal(url.pathname, "/api/state");
    const run = url.searchParams.get("run") ?? control.defaultRun;
    return control.stateReply ? control.stateReply(run) : Response.json(projection(run));
  });
  return { ...sessionModule, studio, editingBasePreferences, requests, control, projection, storage, key, oldPreferences };
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

test("project and server changes restore only their own choice", async (t) => {
  const h = await editingSessionHarness(t);
  const controller = h.createSessionController();
  await controller.reload("chosen-a");
  h.control.projectId = "project-b";
  h.control.defaultRun = "default-b";
  h.requests.length = 0;
  await controller.reload();
  assert.deepEqual(h.requests, ["GET /api/project", "GET /api/state"]);
  assert.equal(controller.getSnapshot().session.value.sourceRunId, null);
  await controller.reload("chosen-b");
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
      connection.configure();

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
