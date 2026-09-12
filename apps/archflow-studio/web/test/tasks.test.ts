import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";

async function taskHarness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const module = await vite.ssrLoadModule("/src/app/tasks.ts");
  const values = new Map<string, string>();
  const writes: { key: string; value: string }[] = [];
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem(key: string, value: string) { writes.push({ key, value }); values.set(key, value); },
  };
  return { ...module, vite, values, writes, storage, key: `${module.TASK_STORAGE_KEY}:server-a` };
}

test("task creation, project-scoped selection, rename, archive and restore preserve history", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const first = store.create("project-a", "New task");
  const second = store.create("project-a", "Another task");
  const foreign = store.create("project-b", "Other project");
  assert.notEqual(first, second);
  assert.equal(store.getSnapshot().activeTaskId, foreign);
  assert.throws(() => store.select(foreign, "project-a"), /another project/);
  store.select(first, "project-a");
  const handle = store.handle(first);
  handle.transcript.append({ kind: "you", text: "  Revise the\n courtyard wall  " });
  assert.match(handle.getSnapshot().title, /Revise the courtyard wall/);
  store.rename(first, "  Courtyard study  ");
  handle.transcript.append({ kind: "you", text: "Retain the existing opening" });
  assert.equal(handle.getSnapshot().title, "Courtyard study");
  assert.equal(store.archive(first, true), true);
  assert.equal(store.getSnapshot().activeTaskId, null);
  assert.throws(() => store.select(first, "project-a"), /archived/);
  assert.equal(handle.getSnapshot().entries.length, 2);
  assert.equal(store.archive(first, false), true);
  store.select(first, "project-a");
  const restored = h.createTaskStore(h.storage, "server-a");
  assert.equal(restored.getSnapshot().activeTaskId, first);
  assert.equal(restored.handle(first).getSnapshot().title, "Courtyard study");
  assert.equal(restored.handle(first).getSnapshot().entries.length, 2);
  assert.equal(restored.handle(first).getSnapshot().archived, false);
  assert.equal(h.createTaskStore(h.storage, "other-server").getSnapshot().tasks.length, 0);
});

test("running work cannot be archived until its task-owned operation finishes", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const id = store.create("project-a", "Wall study");
  const task = store.handle(id);
  task.setBusy(true);
  assert.equal(store.status(id), "running");
  assert.equal(store.archive(id, true), false);
  task.setBusy(false);
  task.transcript.append({ kind: "candidate", candidateId: "candidate-a", jobId: "job-a", proposalId: "proposal-a", status: "queued" });
  assert.equal(store.archive(id, true), false);
  task.transcript.noteJobStatus("candidate-a", "succeeded");
  assert.equal(store.status(id), "ready");
  assert.equal(store.archive(id, true), true);
});

test("a delayed reply and candidate update stay in their originating task after switching", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const first = store.create("project-a", "First task");
  const origin = store.handle(first);
  origin.transcript.append({ kind: "you", text: "Revise the first wall" });
  const reading = origin.transcript.append({ kind: "reading", subject: "First wall", recordSize: "one wall", provider: "codex", startedAt: 1 });
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  const reply = waiting.then(() => {
    origin.transcript.remove(reading);
    origin.transcript.append({ kind: "candidate", candidateId: "first-candidate", jobId: "first-job", proposalId: "first-proposal", status: "running" });
  });
  const second = store.create("project-a", "Second task");
  store.handle(second).transcript.append({ kind: "you", text: "An independent request" });
  const visibleBefore = store.handle(second).getSnapshot();
  release();
  await reply;
  origin.transcript.noteJobStatus("first-candidate", "succeeded");
  assert.equal(store.getSnapshot().activeTaskId, second);
  assert.equal(store.handle(second).getSnapshot(), visibleBefore);
  assert.equal(origin.getSnapshot().entries.at(-1).candidateId, "first-candidate");
  assert.equal(origin.getSnapshot().entries.at(-1).status, "succeeded");
  assert.equal(origin.getSnapshot().entries.some((entry: { kind: string }) => entry.kind === "reading"), false);
  store.select(first, "project-a");
  assert.equal(store.handle(first), origin);
});

test("task drafts, selections, exact base pointers and pending input remain independent", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const baseA = { runId: "run-a", sourceStageRef: "stage-a", stateDigest: "a".repeat(64) };
  const first = store.create("project-a", "First", baseA);
  const second = store.create("project-a", "Second");
  const viewA = { draft: "Draft for A", selection: { componentId: "facade-a", elementId: "wall-a" }, base: baseA };
  const viewB = { draft: "Draft for B", selection: { componentId: "facade-b", elementId: null },
    base: { runId: "run-b", sourceStageRef: null, stateDigest: "b".repeat(64) } };
  const pending = { continuationToken: "a-process-local-token", stateDigest: baseA.stateDigest };
  store.handle(first).saveView(viewA, pending);
  store.handle(second).saveView(viewB, null);
  store.select(first, "project-a");
  assert.deepEqual(store.handle(first).getSnapshot().view, viewA);
  assert.deepEqual(store.handle(first).getSnapshot().pending, pending);
  assert.equal(store.status(first), "needsInput");
  assert.deepEqual(store.handle(second).getSnapshot().view, viewB);
  assert.equal(store.handle(second).getSnapshot().pending, null);
  const restored = h.createTaskStore(h.storage, "server-a");
  assert.deepEqual(restored.handle(first).getSnapshot().view, viewA);
  assert.deepEqual(restored.handle(second).getSnapshot().view, viewB);
  assert.equal(restored.handle(first).getSnapshot().pending, null, "reload cannot restore a server-process continuation capability");
});

test("refresh interrupts an unfinished model request without resending it or rewriting storage", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const id = store.create("project-a", "Waiting task");
  const origin = store.handle(id);
  origin.transcript.append({ kind: "you", text: "Change this wall" });
  origin.transcript.append({ kind: "reading", subject: "Wall", recordSize: "one wall", provider: "codex", startedAt: 1 });
  assert.equal(store.status(id), "running");
  const saved = h.values.get(h.key);
  const writes = h.writes.length;
  let fetches = 0;
  t.mock.method(globalThis, "fetch", async () => { fetches += 1; throw new Error("Reload must not send work"); });
  const restored = h.createTaskStore(h.storage, "server-a");
  assert.equal(restored.status(id), "interrupted");
  const entries = restored.handle(id).getSnapshot().entries;
  assert.equal(entries.some((entry: { kind: string }) => entry.kind === "reading"), false);
  assert.match(entries.at(-1).text, /not been sent again/);
  assert.equal(fetches, 0);
  assert.equal(h.writes.length, writes);
  assert.equal(h.values.get(h.key), saved);
});

test("an older completed candidate cannot hide the newly interrupted request on refresh", async (t) => {
  const h = await taskHarness(t);
  const store = h.createTaskStore(h.storage, "server-a");
  const id = store.create("project-a", "Continuing a study");
  const transcript = store.handle(id).transcript;
  transcript.append({ kind: "candidate", candidateId: "old-candidate", jobId: "old-job", proposalId: "old-proposal", status: "succeeded" });
  transcript.append({ kind: "you", text: "Now revise the opening" });
  transcript.append({ kind: "reading", subject: "Opening", recordSize: "one opening", provider: "codex", startedAt: 1 });
  const restored = h.createTaskStore(h.storage, "server-a");
  assert.equal(restored.status(id), "interrupted");
  assert.equal(restored.handle(id).getSnapshot().entries[0].status, "succeeded");
});

test("refresh keeps a clarification as history without restoring its process-local answer capability", async (t) => {
  const h = await taskHarness(t);
  const { StudioApiError } = await h.vite.ssrLoadModule("/src/api/error.ts");
  const store = h.createTaskStore(h.storage, "server-a");
  const id = store.create("project-a", "Awaiting a design choice");
  const handle = store.handle(id);
  const pending = { continuationToken: "old-server-token", stateDigest: "a".repeat(64) };
  handle.transcript.append({ kind: "you", text: "Change the window" });
  handle.transcript.append({ kind: "question", utterance: "Change the window", error: new StudioApiError({
    status: 409, code: "NEEDS_CLARIFICATION", detail: "Which opening should change?", question: "Which opening?", pendingIntent: pending,
  }) });
  handle.saveView(handle.getSnapshot().view, pending);
  assert.equal(store.status(id), "needsInput");
  let fetches = 0;
  t.mock.method(globalThis, "fetch", async () => { fetches += 1; throw new Error("A restored question must not send work"); });
  const restored = h.createTaskStore(h.storage, "server-a");
  assert.equal(restored.handle(id).getSnapshot().pending, null);
  assert.equal(restored.handle(id).getSnapshot().entries.at(-1).kind, "system");
  assert.equal(restored.status(id), "interrupted");
  assert.equal(fetches, 0);
});

test("corrupt task history is reported and never overwritten by new in-memory work", async (t) => {
  const h = await taskHarness(t);
  for (const text of ["{broken-json", JSON.stringify({ version: 1, tasks: [{ id: "invalid" }] }), JSON.stringify({ version: 2, tasks: [] })]) {
    h.values.set(h.key, text);
    const writes = h.writes.length;
    const store = h.createTaskStore(h.storage, "server-a");
    assert.equal(store.getSnapshot().storageError, true);
    const id = store.create("project-a", "Temporary work");
    store.handle(id).transcript.append({ kind: "you", text: "Keep this in memory" });
    store.rename(id, "Retained locally");
    assert.equal(store.handle(id).getSnapshot().entries.length, 1);
    assert.equal(h.values.get(h.key), text);
    assert.equal(h.writes.length, writes);
  }
});

test("task sessions keep independent runs and never read or write global editingBases", async (t) => {
  const h = await taskHarness(t);
  const preferenceKey = "archflow-studio.user-preferences";
  const saved = JSON.stringify({ version: 1, language: "en", theme: "light", fontScale: 1, eventStreamVisible: false,
    editingBases: { '["","project-a"]': "global-choice" } });
  h.values.set(preferenceKey, saved);
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  let preferenceReads = 0;
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://studio.test/", search: "" },
    localStorage: { ...h.storage, getItem: (key: string) => { if (key === preferenceKey) preferenceReads += 1; return h.storage.getItem(key); } },
  } });
  t.after(() => {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  });
  const { createSessionController } = await h.vite.ssrLoadModule("/src/app/useSession.ts");
  const { ServerConnection } = await h.vite.ssrLoadModule("/src/api/connection.ts");
  new ServerConnection("http://studio.test").configure();
  const readsBefore = preferenceReads;
  const requests: string[] = [];
  const published = { version: 0, stateSha256: "c".repeat(64) };
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push(`${request.method} ${url.pathname}${url.search}`);
    assert.equal(request.method, "GET");
    if (url.pathname === "/api/project") return Response.json({ projectId: "project-a", published });
    assert.equal(url.pathname, "/api/state");
    return Response.json({ projectId: "project-a", published,
      referenceRun: { runId: url.searchParams.get("run") ?? "default-run", baseVersion: 0, baseSha256: published.stateSha256 },
      stateDigest: "a".repeat(64), matchesReferenceReceipt: true, honesty: [],
    });
  });
  const first = createSessionController("", [], false);
  const second = createSessionController("", [], false);
  await first.reload("task-a-run");
  await second.reload("task-b-run");
  await first.reload();
  assert.equal(first.getSnapshot().session.value.sourceRunId, "task-a-run");
  assert.equal(second.getSnapshot().session.value.sourceRunId, "task-b-run");
  const fresh = createSessionController("", [], false);
  await fresh.reload();
  assert.equal(fresh.getSnapshot().session.value.sourceRunId, null);
  assert.equal(fresh.getSnapshot().session.value.projection.referenceRun.runId, "default-run");
  await first.reload(null);
  assert.equal(first.getSnapshot().session.value.sourceRunId, null);
  assert.equal(h.values.get(preferenceKey), saved);
  assert.equal(preferenceReads, readsBefore);
  assert.equal(h.writes.some((write) => write.key === preferenceKey), false);
  assert.equal(requests.some((path) => path.includes("global-choice")), false);
  const readsBeforeRebind = requests.length;
  const foreignTask = createSessionController("", [], false, "another-project");
  await foreignTask.reload("task-a-run");
  assert.equal(foreignTask.getSnapshot().session.status, "failed");
  assert.equal(foreignTask.getSnapshot().baseError.code, "EDITING_PROJECT_CHANGED");
  assert.deepEqual(requests.slice(readsBeforeRebind), ["GET /api/project"], "A task cannot load another project's similarly named run");
});
