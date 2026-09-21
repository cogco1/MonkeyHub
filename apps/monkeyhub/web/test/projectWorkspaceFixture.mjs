import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import rhino3dm from "rhino3dm";

// Fixtures for the actual Hub-mounted workspace components. The API boundary,
// project identity and model bytes are real shapes; no substitute page is used.
export async function createProjectWorkspaceFixture(runtimes, sessions) {
  const rhino = await rhino3dm(), projects = new Map(), requests = [];
  const digest = (value) => createHash("sha256").update(value).digest("hex");
  function project(runtime) {
    if (projects.has(runtime.projectId)) return projects.get(runtime.projectId);
    const id = runtime.projectId, published = { version: 0, stateSha256: digest(`published:${id}`) };
    const home = `home-${id}`, assets = new Map();
    const artifact = (runId) => {
      if (assets.has(runId)) return assets.get(runId).dto;
      const model = new rhino.File3dm(), mesh = new rhino.Mesh(), attributes = new rhino.ObjectAttributes();
      mesh.vertices().add(0, 0, 0); mesh.vertices().add(2, 0, 0); mesh.vertices().add(2, 2, 0); mesh.vertices().add(0, 2, 0);
      mesh.faces().addQuadFace(0, 1, 2, 3); attributes.name = `floor-${id}-${runId}`; model.objects().add(mesh, attributes);
      const bytes = Buffer.from(model.toByteArray()), sha256 = digest(bytes), stateDigest = digest(`state:${id}:${runId}`);
      model.delete(); mesh.delete(); attributes.delete();
      const dto = { artifactId: `${runId}:model`, runId, stageId: "fixture", seatId: "fixture", fileName: `${runId}.3dm`,
        sha256, sizeBytes: bytes.length, available: true, unavailableReason: null, lengthUnit: "meters",
        programRef: null, programDigest: null, designStateDigest: stateDigest, receiptRef: "fixture", format: "3dm",
        representation: "composed", sourceStageRef: null, modelSource: { runId, stateDigest, assetSha256: sha256 } };
      assets.set(runId, { dto, bytes }); return dto;
    };
    artifact(home);
    const imageBytes = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jB8sAAAAASUVORK5CYII=", "base64");
    const renderDocument = { projectId: id, runId: home, assetSha256: digest(imageBytes),
      fileName: `render-${id}.png`, mimeType: "image/png", sizeBytes: imageBytes.length,
      pageCount: 1, pages: [{ pageIndex: 0, width: 1, height: 1, rotation: 0 }], modelSource: assets.get(home).dto.modelSource };
    const value = { runtime, published, home, assets, artifact, documents: [], renderDocument, imageBytes, imageReadFailures: 0,
      board: { projectId: id, title: `Board ${id}`, elements: [], seenDocuments: [], revisionSha256: null } };
    projects.set(id, value); return value;
  }
  async function handle(route, url) {
    const match = url.pathname.match(/^\/api\/runtime\/projects\/([^/]+)\/studio(\/.*)$/);
    if (!match) return false;
    const runtime = [...runtimes.values()].find((row) => row.runtimeId === match[1]);
    assert.ok(runtime, `Request is bound to a known project runtime: ${url.pathname}`);
    const current = project(runtime), projectId = runtime.projectId, name = match[2], method = route.request().method();
    const body = method === "GET" || method === "HEAD" ? null : route.request().postDataJSON();
    requests.push({ runtimeId: runtime.runtimeId, projectId, name, method, body, query: Object.fromEntries(url.searchParams) });
    if (body?.projectId) assert.equal(body.projectId, projectId, "workspace writes stay bound to their own project");
    if (method !== "GET" && method !== "HEAD") assert.match(route.request().headers()["idempotency-key"] ?? "", /^[0-9a-f-]{36}$/i, "workspace writes carry their own idempotency key");
    const json = async (value) => { await route.fulfill({ json: value }); return true; };
    if (method === "GET") {
      if (name === "/api/protocol") return json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local", capabilities: [] });
      if (name === "/api/project") return json({ projectId, projectDir: runtime.projectDir, published: current.published,
        referenceRun: { runId: current.home, baseVersion: 0, baseSha256: current.published.stateSha256 }, intentProvider: "codex", intentModel: "fixture" });
      if (name === "/api/state") {
        const runId = url.searchParams.get("run") ?? current.home;
        return json({ projectId, published: current.published, sourceStageRef: null,
          referenceRun: { runId, baseVersion: 0, baseSha256: current.published.stateSha256 }, referenceRunSource: "fixture",
          referenceReceipt: null, matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: digest(`record:${projectId}:${runId}`),
          stateDigest: digest(`state:${projectId}:${runId}`), activePhase: "stage-2", counts: { entities: 0, components: 0, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
          componentTree: [], componentTreeError: null, elements: [], parameters: [], dependencyEdges: [], honesty: [], catalog: {
            components: [], elements: [], objects: [], coverage: { objects: 0, bound: 0, unbound: 0, ambiguous: 0, unknownComponent: 0 }, inspectionRun: runId, honesty: [] } });
      }
      if (name === "/api/artifacts") {
        for (const session of sessions.filter((row) => row.projectId === projectId)) for (const message of session.messages) {
          if (message.candidateId) current.artifact(message.candidateId);
        }
        const artifacts = [...current.assets.values()].map((row) => row.dto);
        const home = current.assets.get(current.home).dto;
        artifacts.push({ ...home, artifactId: `${current.home}:work-model`, fileName: `editable-${projectId}.3dm`,
          representation: "exact", modelSource: null, sourceStepSha256: digest(`source-step:${projectId}`), status: "succeeded" });
        return json({ projectId, artifacts, skippedRuns: [] });
      }
      if (name === "/api/state/frame") return json({ levels: [], axes: [], honesty: [] });
      if (name === "/api/documents") return json({ projectId, runId: null, documents: current.documents });
      if (name === `/api/documents/${current.documents[0]?.assetSha256}/bytes`) {
        assert.equal(url.searchParams.get("runId"), current.home);
        if (current.imageReadFailures > 0) {
          current.imageReadFailures--;
          await route.fulfill({ status: 503, json: { code: "IMAGE_UNAVAILABLE", detail: "Image bytes temporarily unavailable" } });
        } else await route.fulfill({ body: current.imageBytes, contentType: "image/png" });
        return true;
      }
      if (name === "/api/board") return json(current.board);
      if (name === "/api/drawings/styles") return json({ styles: [] });
      const bytes = name.match(/^\/api\/artifacts\/([^/]+)\/bytes$/);
      if (bytes) {
        const asset = [...current.assets.values()].find((row) => row.dto.sha256 === bytes[1]);
        assert.ok(asset, "model bytes belong to the addressed project");
        requests.at(-1).runId = asset.dto.runId;
        await route.fulfill({ body: asset.bytes, contentType: "application/octet-stream" }); return true;
      }
    }
    if (method === "PUT" && name === "/api/board") {
      assert.equal(body.baseRevisionSha256, current.board.revisionSha256, "Board writes preserve their exact retained base");
      current.board = { projectId, title: body.title, elements: body.elements, seenDocuments: body.seenDocuments, revisionSha256: digest(JSON.stringify(body)) };
      return json(current.board);
    }
    throw new Error(`Unexpected project request: ${method} ${projectId} ${name}`);
  }
  return { handle, requests, projects };
}
