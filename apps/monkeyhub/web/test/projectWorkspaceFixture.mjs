import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import rhino3dm from "rhino3dm";

// Fixtures for the actual Hub-mounted workspace components. The API boundary,
// project identity and model bytes are real shapes; no substitute page is used.
export async function createProjectWorkspaceFixture(runtimes, sessions) {
  const rhino = await rhino3dm(), projects = new Map(), requests = [];
  // Projects whose runtime keeps a working draft (autosave), by project id:
  // { revisionSha256, current, localDraft, writes, hold, failure }. A test may set
  // `hold` to a promise that delays the next local write and `failure` to refuse it.
  const workingDrafts = new Map();
  const digest = (value) => createHash("sha256").update(value).digest("hex");
  const stateDigestOf = (projectId, runId) => digest(`state:${projectId}:${runId}`);
  const workingDraftDto = (projectId) => {
    const draft = workingDrafts.get(projectId);
    return { projectId, revisionSha256: draft.revisionSha256, current: draft.current, recovery: [], saved: [],
      managedRunIds: [], localDraft: draft.localDraft };
  };
  function project(runtime) {
    if (projects.has(runtime.projectId)) return projects.get(runtime.projectId);
    const id = runtime.projectId, published = { version: 0, stateSha256: digest(`published:${id}`) };
    const home = `home-${id}`, assets = new Map();
    const artifact = (runId) => {
      if (assets.has(runId)) return assets.get(runId).dto;
      const model = new rhino.File3dm(), mesh = new rhino.Mesh(), attributes = new rhino.ObjectAttributes();
      mesh.vertices().add(0, 0, 0); mesh.vertices().add(2, 0, 0); mesh.vertices().add(2, 2, 0); mesh.vertices().add(0, 2, 0);
      mesh.faces().addQuadFace(0, 1, 2, 3); attributes.name = `floor-${id}-${runId}`; model.objects().add(mesh, attributes);
      const bytes = Buffer.from(model.toByteArray()), sha256 = digest(bytes), stateDigest = stateDigestOf(id, runId);
      model.delete(); mesh.delete(); attributes.delete();
      const dto = { artifactId: `${runId}:model`, runId, stageId: "fixture", seatId: "fixture", fileName: `${runId}.3dm`,
        sha256, sizeBytes: bytes.length, available: true, unavailableReason: null, lengthUnit: "meters",
        programRef: null, programDigest: null, designStateDigest: stateDigest, receiptRef: "fixture", format: "3dm",
        representation: "composed", sourceStageRef: null, modelSource: { runId, stateDigest, assetSha256: sha256 } };
      assets.set(runId, { dto, bytes }); return dto;
    };
    artifact(home);
    const value = { runtime, published, home, assets, artifact,
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
      if (name === "/api/protocol") return json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local",
        capabilities: workingDrafts.has(projectId) ? ["working-draft"] : [] });
      if (name === "/api/working-draft" && workingDrafts.has(projectId)) return json(workingDraftDto(projectId));
      if (name === "/api/project") return json({ projectId, projectDir: runtime.projectDir, published: current.published,
        referenceRun: { runId: current.home, baseVersion: 0, baseSha256: current.published.stateSha256 }, intentProvider: "codex", intentModel: "fixture" });
      if (name === "/api/state") {
        const runId = url.searchParams.get("run") ?? current.home;
        return json({ projectId, published: current.published, sourceStageRef: null,
          referenceRun: { runId, baseVersion: 0, baseSha256: current.published.stateSha256 }, referenceRunSource: "fixture",
          referenceReceipt: null, matchesReferenceReceipt: true, recordSource: "fixture", recordDigest: digest(`record:${projectId}:${runId}`),
          stateDigest: stateDigestOf(projectId, runId), activePhase: "stage-2", counts: { entities: 0, components: 0, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
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
      if (name === "/api/documents") return json({ projectId, runId: null, documents: [] });
      if (name === "/api/render/capabilities") return json({ providers: [] });
      if (name === "/api/render/jobs") return json({ projectId, jobs: [] });
      if (name === "/api/design-history") return json({ projectId, branchId: "main", branches: [], stages: [] });
      if (name === "/api/board") return json(current.board);
      // #300: Layout, Board's second mode, reads the project's publication; none is saved yet.
      if (name === "/api/publication") return json({ projectId, revisionSha256: null, title: `Layout ${projectId}`,
        spec: { width: 1280, height: 720, template: "hero" }, pages: [], sources: [] });
      if (name === "/api/drawings/styles") return json({ styles: [] });
      // #271: the head a real runtime would resolve for this fixture, and its read-only Worktree Graph.
      const home = current.assets.get(current.home).dto;
      const head = { runId: current.home, stateDigest: home.modelSource.stateDigest, recordDigest: digest(`record:${projectId}:${current.home}`),
        sourceStageRef: null, branchId: null, accepted: false, origin: "reference", label: null, modelSource: home.modelSource, lineage: [current.home] };
      if (name === "/api/working-source") return json({ projectId, workspace: url.searchParams.get("workspace") ?? "modeling", policy: "live",
        revisionSha256: null, head, compatible: false, source: null, stageRef: null, reason: "This fixture has no exact STEP to draw.", warnings: [] });
      if (name === "/api/worktrees") {
        const results = [...new Set(sessions.filter((row) => row.projectId === projectId).flatMap((row) => row.messages)
          .map((message) => message.candidateId).filter(Boolean))];
        return json({ projectId, head, revisionSha256: null, warnings: [], representations: [], lines: [
          { lineId: `head:${current.home}`, kind: "head", runId: current.home, jobId: null, label: null, baseRunId: null, baseStageRef: null,
            branchId: null, status: "current", relation: "head", reads: [], writes: [], reconcile: "none", conflicts: [], detail: null, updatedAt: null },
          ...results.map((runId) => ({ lineId: `result:${runId}`, kind: "result", runId, jobId: null, label: null, baseRunId: current.home,
            baseStageRef: null, branchId: null, status: "ready", relation: "diverged", reads: [], writes: ["entity:floor"],
            reconcile: "can-combine", conflicts: [], detail: null, updatedAt: null })),
        ] });
      }
      const bytes = name.match(/^\/api\/artifacts\/([^/]+)\/bytes$/);
      if (bytes) {
        const asset = [...current.assets.values()].find((row) => row.dto.sha256 === bytes[1]);
        assert.ok(asset, "model bytes belong to the addressed project");
        requests.at(-1).runId = asset.dto.runId;
        await route.fulfill({ body: asset.bytes, contentType: "application/octet-stream" }); return true;
      }
    }
    const workingDraft = workingDrafts.get(projectId);
    if (workingDraft && method === "PUT" && (name === "/api/working-draft" || name === "/api/working-draft/local")) {
      assert.equal(body.baseRevisionSha256, workingDraft.revisionSha256, "working-draft writes keep their exact retained revision");
      if (name === "/api/working-draft/local") {
        workingDraft.writes.push(body);
        const hold = workingDraft.hold;
        workingDraft.hold = null;
        await hold;
        if (workingDraft.failure) {
          await route.fulfill({ status: 503, json: { code: "WORKING_DRAFT_UNAVAILABLE", detail: workingDraft.failure } });
          return true;
        }
        workingDraft.localDraft = body.draft && { ...body.draft, updatedAt: "2026-09-25T00:00:00Z" };
      } else {
        workingDraft.current = body.runId && { runId: body.runId, sourceStageRef: null, branchId: body.branchId ?? null,
          updatedAt: "2026-09-25T00:00:00Z", label: null };
      }
      workingDraft.revisionSha256 = digest(JSON.stringify([workingDraft.revisionSha256, name, body]));
      return json(workingDraftDto(projectId));
    }
    if (method === "PUT" && name === "/api/board") {
      assert.equal(body.baseRevisionSha256, current.board.revisionSha256, "Board writes preserve their exact retained base");
      current.board = { projectId, title: body.title, elements: body.elements, seenDocuments: body.seenDocuments, revisionSha256: digest(JSON.stringify(body)) };
      return json(current.board);
    }
    throw new Error(`Unexpected project request: ${method} ${projectId} ${name}`);
  }
  return { handle, requests, projects, workingDrafts, stateDigestOf };
}
