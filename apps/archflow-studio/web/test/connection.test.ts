import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";

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
  await studio.state(source.sourceRunId);
  await studio.frame(source.sourceRunId);
  await studio.volumes(source.sourceRunId);
  await studio.resolvePick({ ...source, userStrings: {} });
  await studio.closure({ ...source, changedRefs: ["entity:column"] });
  await studio.compileIntent({ ...source, utterance: "set height to 3" });
  await studio.createProposal({ ...source, targetComponentId: "portico", utterance: "set height to 3" });
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

  // Returning to the default is per request, not a hidden global SDK pointer.
  requests.length = 0;
  await studio.state();
  await studio.frame();
  await studio.volumes();
  await studio.compileIntent({ stateDigest: "b".repeat(64), utterance: "set height to 4" });
  assert.deepEqual(requests.slice(0, 3).map((r) => r.path), [
    "/api/state", "/api/state/frame", "/api/state/volumes",
  ]);
  assert.equal(Object.hasOwn(requests[3].body as object, "sourceRunId"), false);
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
