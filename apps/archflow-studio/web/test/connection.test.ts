import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createServer } from "vite";

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
