import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";

type Client = typeof import("../src/api/client.ts");
type Connection = typeof import("../src/api/connection.ts");
type DesignTree = typeof import("../src/features/designTree/useDesignTree.ts");

async function harness(t: TestContext) {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  return {
    ...await vite.ssrLoadModule("/src/api/client.ts") as Client,
    ...await vite.ssrLoadModule("/src/api/connection.ts") as Connection,
    ...await vite.ssrLoadModule("/src/features/designTree/useDesignTree.ts") as DesignTree,
  };
}

/**
 * A runtime whose views are tagged by one project version, as transport/conditional.py tags them by its read token.
 * A read answers the project as it was when the read arrived; a write changes it when it is let through.
 */
function runtime(t: TestContext, answer: (path: string, query: URLSearchParams) => unknown) {
  const project = { version: 1, jobs: 1 };
  const requests: { method: string; path: string; ifNoneMatch: string | null }[] = [];
  const gates: Promise<void>[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push({ method: request.method, path: url.pathname + url.search, ifNoneMatch: request.headers.get("If-None-Match") });
    const gate = gates.shift();
    if (request.method !== "GET") { if (gate) await gate; project.version += 1; return Response.json({ projectId: "P" }); }
    const tag = `"${project.version}:${url.pathname === "/api/worktrees" ? project.jobs : 0}:${url.pathname}${url.search}"`;
    const body = { ...answer(url.pathname, url.searchParams) as object, version: project.version };
    if (gate) await gate;
    if (request.headers.get("If-None-Match") === tag) return new Response(null, { status: 304, headers: { ETag: tag } });
    return Response.json(body, { headers: { ETag: tag, "Cache-Control": "no-cache" } });
  });
  return { project, requests, gates };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
function gate(server: { gates: Promise<void>[] }) {
  let release!: () => void;
  server.gates.push(new Promise<void>((resolve) => { release = resolve; }));
  return () => release();
}

test("a polled view answered 304 is the object read before, and one read per view is in flight", async (t) => {
  const { createStudioClient, ServerConnection } = await harness(t);
  const server = runtime(t, () => ({ projectId: "P", title: "Board", elements: [] }));
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  const sent = () => server.requests.splice(0).map((row) => `${row.method} ${row.path}${row.ifNoneMatch ? " " + row.ifNoneMatch : ""}`);
  const first = await studio.board();
  const again = await studio.board();
  assert.equal(again, first, "a 304 keeps the state read before, by identity");
  assert.deepEqual(sent(), ["GET /api/board", `GET /api/board "1:0:/api/board"`]);

  // Readers who ask while a read is on the way share one conditional read sent after it answers.
  const release = gate(server);
  const reading = studio.board();
  const joined = [studio.board(), studio.board()];
  await tick();
  assert.deepEqual(sent(), [`GET /api/board "1:0:/api/board"`], "the later readers do not send while the first is on the way");
  release();
  assert.equal(await reading, first);
  const [a, b] = await Promise.all(joined);
  assert.equal(a, first); assert.equal(b, first);
  assert.deepEqual(sent(), [`GET /api/board "1:0:/api/board"`], "one read answers both later readers");

  // The abandoned wait lets its caller go; the read itself still lands for the next one.
  const releaseGraph = gate(server);
  const controller = new AbortController();
  const abandoned = studio.worktrees(controller.signal);
  controller.abort();
  await assert.rejects(abandoned, (error: { code?: string }) => error.code === "NETWORK_ERROR");
  releaseGraph();
  await tick();
  const kept = await studio.worktrees();
  assert.deepEqual(sent(), ["GET /api/worktrees", `GET /api/worktrees "1:1:/api/worktrees"`], "the abandoned read's answer is the one kept");
  assert.equal(await studio.worktrees(), kept);
});

test("a read asked for after a change is never answered by a read sent before it", async (t) => {
  const { createStudioClient, ServerConnection } = await harness(t);
  const server = runtime(t, (path) => path === "/api/artifacts" ? { projectId: "P", artifacts: [] } : { projectId: "P", elements: [] });
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  const first = await studio.artifacts();
  server.requests.splice(0);

  // An agent registers a model while the list is on the way; the event's re-read sees it.
  let release = gate(server);
  const before = studio.artifacts();
  await tick();
  server.project.version += 1;
  const afterEvent = studio.artifacts();
  release();
  assert.equal(await before, first, "the read sent before the change answers as it was");
  const seen = await afterEvent as unknown as { version: number };
  assert.notEqual(seen, first);
  assert.equal(seen.version, 2, "the re-read after the event reads the registered model");
  assert.deepEqual(server.requests.splice(0).map((row) => row.path), ["/api/artifacts", "/api/artifacts"]);

  // A write on the way when a read was sent, finished before the next reader asks.
  const releaseWrite = gate(server);
  release = gate(server);
  const writing = studio.saveBoard({ projectId: "P", title: "Board", elements: [], seenDocuments: [], baseRevisionSha256: null });
  await tick();
  const during = studio.board();
  await tick();
  releaseWrite();
  await writing;
  const afterWrite = studio.board();
  release();
  assert.equal((await during as unknown as { version: number }).version, 2, "the read sent during the write answers as before it");
  assert.equal((await afterWrite as unknown as { version: number }).version, 3, "the read asked for after the write reads it");
  assert.deepEqual(server.requests.map((row) => row.method + " " + row.path), ["PUT /api/board", "GET /api/board", "GET /api/board"]);
});

test("model bytes opened again come from memory, by digest", async (t) => {
  const { createStudioClient, ServerConnection } = await harness(t);
  let reads = 0;
  t.mock.method(globalThis, "fetch", async () => { reads += 1; return new Response(new Uint8Array([1, 2, 3]), { headers: { "Content-Type": "application/octet-stream" } }); });
  const sha = "a".repeat(64);
  const first = await createStudioClient(new ServerConnection("http://127.0.0.1:18180")).artifactFile(sha, "first.3dm");
  const second = await createStudioClient(new ServerConnection("http://127.0.0.1:18180")).artifactFile(sha, "second.3dm");
  assert.equal(reads, 1);
  assert.equal(second.name, "second.3dm");
  assert.deepEqual(new Uint8Array(await second.arrayBuffer()), new Uint8Array(await first.arrayBuffer()));

  // Two openings of a model on the way share its download; one letting go does not stop the other's.
  const other = "b".repeat(64), studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  const controller = new AbortController();
  const [left, stays] = [studio.artifactFile(other, "left.3dm", undefined, controller.signal), studio.artifactFile(other, "stays.3dm")];
  controller.abort();
  await assert.rejects(left, (error: { code?: string }) => error.code === "NETWORK_ERROR");
  assert.equal((await stays).name, "stays.3dm");
  assert.equal(reads, 2);
});

test("the tags kept are those of the views read most recently", async (t) => {
  const { createStudioClient, ServerConnection } = await harness(t);
  const server = runtime(t, (_path, query) => ({ projectId: "P", runId: query.get("runId"), documents: [] }));
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  for (let run = 0; run < 100; run += 1) await studio.documents(`run-${run}`);
  server.requests.splice(0);
  await studio.documents("run-99");
  await studio.documents("run-0");
  assert.deepEqual(server.requests.map((row) => row.ifNoneMatch !== null), [true, false], "a run read long ago is asked for afresh");
});

test("an unchanged project refreshes its Design Tree in one request", async (t) => {
  const { createStudioClient, ServerConnection, readDesignTreeSource } = await harness(t);
  const branches = [{ branchId: "main", parentBranch: null, forkStageRef: null, headStageRef: null },
    { branchId: "study", parentBranch: "main", forkStageRef: null, headStageRef: null }];
  const server = runtime(t, (path, query) => path === "/api/working-source" ? { projectId: "P", head: { branchId: "main" } }
    : path === "/api/worktrees" ? { projectId: "P", lines: [] }
    : { projectId: "P", branchId: query.get("branchId"), branches, stages: [], candidates: [], studies: [], warnings: [] });
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  const paths = () => server.requests.splice(0).map((row) => row.path);
  const first = await readDesignTreeSource(studio, "P");
  assert.deepEqual(paths(), ["/api/working-source?workspace=modeling", "/api/worktrees", "/api/design-history?branchId=main",
    "/api/design-history?branchId=study"]);
  // The first read asked for the Graph and the working source together, so the Graph cannot vouch for it yet.
  const second = await readDesignTreeSource(studio, "P", undefined, first);
  assert.equal(second, first);
  assert.deepEqual(paths(), ["/api/worktrees", "/api/working-source?workspace=modeling"]);
  const third = await readDesignTreeSource(studio, "P", undefined, second);
  assert.equal(third, first);
  assert.deepEqual(paths(), ["/api/worktrees"]);
  // Running work moved; the project did not: the working source vouches for every line's history.
  server.project.jobs += 1;
  const moved = await readDesignTreeSource(studio, "P", undefined, third);
  assert.notEqual(moved.worktrees, first.worktrees);
  assert.equal(moved.history, first.history);
  assert.deepEqual(paths(), ["/api/worktrees", "/api/working-source?workspace=modeling"]);
  // The project changed: every line is read again, after the view that noticed.
  server.project.version += 1;
  const changed = await readDesignTreeSource(studio, "P", undefined, moved);
  assert.notEqual(changed.history, moved.history);
  assert.deepEqual(paths(), ["/api/worktrees", "/api/working-source?workspace=modeling", "/api/design-history?branchId=main",
    "/api/design-history?branchId=study"]);
  assert.equal(await readDesignTreeSource(studio, "P", undefined, changed), changed);
  assert.deepEqual(paths(), ["/api/worktrees"]);
});
