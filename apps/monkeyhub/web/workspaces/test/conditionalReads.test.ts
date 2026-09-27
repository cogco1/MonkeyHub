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

/** A runtime whose views are tagged by one project version, as transport/conditional.py tags them by its read token. */
function runtime(t: TestContext, answer: (path: string, query: URLSearchParams) => unknown) {
  const project = { version: 1, jobs: 1 };
  const requests: { method: string; path: string; ifNoneMatch: string | null }[] = [];
  const gates: Promise<void>[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push({ method: request.method, path: url.pathname + url.search, ifNoneMatch: request.headers.get("If-None-Match") });
    const gate = gates.shift();
    if (gate) await gate;
    if (request.method !== "GET") { project.version += 1; return Response.json({ projectId: "P" }); }
    const tag = `"${project.version}:${url.pathname === "/api/worktrees" ? project.jobs : 0}:${url.pathname}${url.search}"`;
    if (request.headers.get("If-None-Match") === tag) return new Response(null, { status: 304, headers: { ETag: tag } });
    return Response.json(answer(url.pathname, url.searchParams), { headers: { ETag: tag, "Cache-Control": "no-cache" } });
  });
  return { project, requests, gates };
}

test("a polled view answered 304 is the object read before, and one read per view is in flight", async (t) => {
  const { createStudioClient, ServerConnection } = await harness(t);
  const server = runtime(t, () => ({ projectId: "P", title: "Board", elements: [] }));
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180"));
  const first = await studio.board();
  assert.equal(server.requests[0].ifNoneMatch, null);
  const again = await studio.board();
  assert.equal(again, first, "a 304 keeps the state read before, by identity");
  assert.equal(server.requests[1].ifNoneMatch, `"1:0:/api/board"`);

  // Two readers of one view while it is on the way: one request answers both.
  let release!: () => void;
  server.gates.push(new Promise<void>((resolve) => { release = resolve; }));
  const joined = [studio.board(), studio.board()];
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(server.requests.length, 3);
  release();
  const [a, b] = await Promise.all(joined);
  assert.equal(a, first); assert.equal(b, first);

  // A write since the read on the way was sent: the next reader waits for it, then asks again.
  server.gates.push(new Promise<void>((resolve) => { release = resolve; }));
  const stale = studio.board();
  await new Promise((resolve) => setTimeout(resolve, 0));
  await studio.saveBoard({ projectId: "P", title: "Board", elements: [], seenDocuments: [], baseRevisionSha256: null });
  const fresh = studio.board();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(server.requests.map((row) => row.method + " " + row.path).slice(3), ["GET /api/board", "PUT /api/board"],
    "the second reader does not send while the first is on the way");
  release();
  await stale;
  const changed = await fresh;
  assert.notEqual(changed, first, "the read after the write is a fresh 200");
  assert.deepEqual(server.requests.map((row) => row.method + " " + row.path).slice(5), ["GET /api/board"]);

  // An abandoned wait lets its caller go; the read itself still lands for the next one.
  server.gates.push(new Promise<void>((resolve) => { release = resolve; }));
  const controller = new AbortController();
  const abandoned = studio.worktrees(controller.signal);
  controller.abort();
  await assert.rejects(abandoned, (error: { code?: string }) => error.code === "NETWORK_ERROR");
  release();
  const kept = await studio.worktrees();
  assert.equal(await studio.worktrees(), kept);
  assert.equal(server.requests.filter((row) => row.path === "/api/worktrees").length, 2, "the abandoned read still answered the next reader");
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
