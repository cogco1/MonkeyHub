import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test, { type TestContext } from "node:test";

import { createServer } from "vite";
import type { WorkingDraftDto } from "../src/api/project-runtime/generated/index.ts";

const projectId = "thi-hemp-study-01";
const runId = "studio-cand-v3";

async function runtimeClient(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)),
    configFile: false,
    logLevel: "silent",
    server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { configurable: true, value: {
    location: { href: "http://studio.test/" },
    btoa: globalThis.btoa,
  } });
  t.after(() => {
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else Reflect.deleteProperty(globalThis, "window");
  });
  const { createStudioClient } = await vite.ssrLoadModule("/src/api/project-runtime/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/project-runtime/connection.ts");
  return createStudioClient(new ServerConnection("http://studio.test", "saved-version-token"));
}

// #575: only a name the person saved keeps a superseded draft out of the project trash. The history panel's
// 保存 is the person naming a version, so its call says so; agents save names over the plain API without it.
test("the history panel's save says the person saved the name", async (t) => {
  const studio = await runtimeClient(t);
  const position: WorkingDraftDto = {
    projectId, revisionSha256: "rev-2", current: null, recovery: [], managedRunIds: [runId], localDraft: null,
    saved: [{ runId, label: "V3", updatedAt: "2026-10-01T09:00:00+00:00", sourceStageRef: null, branchId: null }],
  };
  const requests: { method: string; path: string; body: unknown }[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const url = new URL(request.url);
    requests.push({ method: request.method, path: url.pathname, body: await request.json() });
    return Response.json(position);
  });

  const answer = await studio.savePersonsVersion({ projectId, runId, label: "V3", baseRevisionSha256: "rev-1" });
  assert.deepEqual(answer, position);
  assert.deepEqual(requests, [{
    method: "POST", path: "/api/working-draft/save",
    body: { projectId, runId, label: "V3", baseRevisionSha256: "rev-1", savedBy: "person" },
  }]);
});
