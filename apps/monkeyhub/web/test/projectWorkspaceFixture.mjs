import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import rhino3dm from "rhino3dm";

// Fixtures for the actual Hub-mounted workspace components. The API boundary,
// project identity and model bytes are real shapes; no substitute page is used.
export async function createProjectWorkspaceFixture(runtimes, sessions, { onIndex = () => {} } = {}) {
  const rhino = await rhino3dm(), projects = new Map(), requests = [];
  // Projects whose runtime keeps a working draft (autosave), by project id:
  // { revisionSha256, current, localDraft, writes, hold, failure }. A test may set
  // `hold` to a promise that delays the next local write and `failure` to refuse it.
  const workingDrafts = new Map();
  // #285: projects whose runtime has a Design Tree, by project id: { stages, edits }, the
  // runs accepted as S0, S1, … in order, and the runs Current made after the last one.
  const designTrees = new Map();
  // #450: how a runtime answers /api/state for particular runs, by project id and run id:
  // { baseVersion, baseSha256, matchesReferenceReceipt, stateDigest }. Each named run also
  // lists a model. A run based on an older published version is still a verified design.
  const runStates = new Map();
  // #450: projects whose Working Head is their working draft's saved run while one is saved,
  // as the runtime's working position is; a project not named here keeps its fixed head.
  const headsFollowDraft = new Set();
  // #363: the views a runtime answers conditionally, each tagged by the project's whole retained
  // state, the path and the query, as transport/conditional.py tags them by the project's read token.
  const conditional = new Set(["/api/design-history", "/api/worktrees", "/api/artifacts", "/api/documents",
    "/api/working-source", "/api/board", "/api/render/jobs"]);
  const notModified = [];
  // Projects whose runtime reports its event stream (the Studio's "events" capability), by project id.
  const studioEvents = new Set();
  // #366: each project's index, as the runtime's keeper keeps it: an epoch, and a revision that moves
  // once whenever what the project's views are derived from changed. `onIndex` hears each commit,
  // as the Hub relays it on its stream; a test that changed a project behind the runtime's back
  // calls `commit` for it, as the runtime's layout watch would.
  const indexes = new Map();
  const indexOf = (current) => {
    const id = current.runtime.projectId, token = projectToken(current);
    let index = indexes.get(id);
    if (!index) { index = { epoch: `epoch-${id}-1`, revision: 1, token }; indexes.set(id, index); return { index, moved: false }; }
    if (index.token === token) return { index, moved: false };
    index.revision += 1; index.token = token;
    return { index, moved: true };
  };
  const commit = (projectId) => {
    const current = projects.get(projectId);
    if (!current) return null;
    const { index, moved } = indexOf(current);
    if (moved) onIndex(current.runtime, { epoch: index.epoch, revision: index.revision, domains: ["tree", "working"] });
    return index;
  };
  // A rebuilt index (a new epoch), as a worker restart that found its file unusable leaves it.
  const rebuild = (projectId) => {
    const index = indexes.get(projectId);
    if (!index) return null;
    const next = Number(index.epoch.split("-").at(-1)) + 1;
    Object.assign(index, { epoch: `epoch-${projectId}-${next}`, revision: 1, token: projectToken(projects.get(projectId)) });
    return index;
  };
  // One token covers every fact of the fixture project, so any change is one to its Stages and its working
  // position, as the real index would log for what this fixture changes (a Stage, the head, a run).
  const indexEntities = (current, index) => ["tree", "working"].map((id) => ({ id, domain: id, rev: index.revision,
    body: { token: digest(index.token) } }));
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
      board: { projectId: id, title: `Board ${id}`, elements: [], seenDocuments: [], revisionSha256: null },
      // #300: Layout, Board's second mode, edits the project's publication; none is saved yet.
      publication: { projectId: id, revisionSha256: null, title: `Layout ${id}`, spec: { width: 1280, height: 720, template: "hero" }, pages: [], sources: [] } };
    projects.set(id, value); return value;
  }
  // Everything a conditional view of this fixture is derived from.
  const projectToken = (current) => {
    const id = current.runtime.projectId;
    // Model rows are made on first listing from the Stages and chat results named here, so they are not listed again.
    return JSON.stringify([designTrees.get(id) ?? null, workingDrafts.get(id) ? workingDraftDto(id) : null, current.board, current.publication,
      sessions.filter((row) => row.projectId === id).map((row) => row.messages.map((message) => message.candidateId ?? null))]);
  };
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
    const json = async (value) => {
      if (method !== "GET") {
        // A write names the index revision that holds it, and the index announces the commit.
        const { index, moved } = indexOf(current);
        await route.fulfill({ json: value, headers: { "x-monkey-index": `${index.epoch}:${index.revision}` } });
        if (moved) onIndex(runtime, { epoch: index.epoch, revision: index.revision, domains: ["tree", "working"] });
        return true;
      }
      if (!conditional.has(name)) { await route.fulfill({ json: value }); return true; }
      const tag = `"${digest(JSON.stringify([projectToken(current), name, [...url.searchParams].sort()]))}"`;
      if (route.request().headers()["if-none-match"] === tag) {
        notModified.push({ projectId, name });
        await route.fulfill({ status: 304, headers: { etag: tag, "cache-control": "no-cache" } }); return true;
      }
      await route.fulfill({ json: value, headers: { etag: tag, "cache-control": "no-cache" } }); return true;
    };
    if (method === "GET") {
      if (name === "/api/index") {
        const { index, moved } = indexOf(current);
        if (moved) onIndex(runtime, { epoch: index.epoch, revision: index.revision, domains: ["tree", "working"] });
        const since = url.searchParams.has("since") ? Number(url.searchParams.get("since")) : null;
        const delta = since !== null && url.searchParams.get("epoch") === index.epoch && since <= index.revision;
        const entities = indexEntities(current, index);
        await route.fulfill({ headers: { etag: `"${index.epoch}:${index.revision}"`, "cache-control": "no-cache" },
          json: { projectId, epoch: index.epoch, revision: index.revision, reset: !delta, from: delta ? since : null, to: index.revision,
            upserts: delta && since === index.revision ? [] : entities, deletes: [] } });
        return true;
      }
      if (name === "/api/protocol") return json({ protocol: "archflow/2", server: "fixture", serverVersion: "test", mode: "local",
        capabilities: [...(workingDrafts.has(projectId) ? ["working-draft"] : []), ...(designTrees.has(projectId) ? ["design-history", "working-source"] : []),
          ...(studioEvents.has(projectId) ? ["events"] : [])] });
      if (name === "/api/working-draft" && workingDrafts.has(projectId)) return json(workingDraftDto(projectId));
      if (name === "/api/project") return json({ projectId, projectDir: runtime.projectDir, published: current.published,
        referenceRun: { runId: current.home, baseVersion: 0, baseSha256: current.published.stateSha256 }, intentProvider: "codex", intentModel: "fixture" });
      if (name === "/api/state") {
        const runId = url.searchParams.get("run") ?? current.home;
        const state = runStates.get(projectId)?.get(runId) ?? {};
        // The state of the Stage asked for, as a runtime answers it; no Stage otherwise.
        return json({ projectId, published: current.published, sourceStageRef: url.searchParams.get("sourceStageRef"),
          referenceRun: { runId, baseVersion: state.baseVersion ?? current.published.version, baseSha256: state.baseSha256 ?? current.published.stateSha256 },
          referenceRunSource: "fixture", referenceReceipt: null, matchesReferenceReceipt: state.matchesReferenceReceipt ?? true,
          recordSource: "fixture", recordDigest: digest(`record:${projectId}:${runId}`),
          stateDigest: "stateDigest" in state ? state.stateDigest : stateDigestOf(projectId, runId), activePhase: "stage-2", counts: { entities: 0, components: 0, parameters: 0, relations: 0, obligations: 0, dependencyEdges: 0 },
          componentTree: [], componentTreeError: null, elements: [], parameters: [], dependencyEdges: [], honesty: [], catalog: {
            components: [], elements: [], objects: [], coverage: { objects: 0, bound: 0, unbound: 0, ambiguous: 0, unknownComponent: 0 }, inspectionRun: runId, honesty: [] } });
      }
      if (name === "/api/artifacts") {
        for (const run of designTrees.get(projectId)?.stages ?? []) current.artifact(run);
        for (const run of runStates.get(projectId)?.keys() ?? []) current.artifact(run);
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
      // #367: the Design Tree asks the projection cache for a model's thumbnail; this fixture
      // draws none, so every model stays pending and its card keeps the placeholder.
      if (name === "/api/projections") return json({ key: digest(`projection:${projectId}:${url.search}`), status: "pending",
        kind: "model-axon", recipe: {}, renderer: "fixture", inputSha256: digest(url.search), source: null, blobSha256: null,
        blobUrl: null, attempts: 0, error: null, loadMs: null, renderMs: null });
      if (name === "/api/documents") return json({ projectId, runId: null, documents: [] });
      if (name === "/api/render/capabilities") return json({ providers: [] });
      if (name === "/api/render/jobs") return json({ projectId, jobs: [] });
      if (name === "/api/design-history") {
        const tree = designTrees.get(projectId);
        if (!tree) return json({ projectId, branchId: "main", branches: [], stages: [] });
        const stageRef = (run) => `project://${projectId}/runs/${run}/review/design-stage.json`;
        const stages = tree.stages.map((run, index) => ({ stageRef: stageRef(run), parentStageRef: index ? stageRef(tree.stages[index - 1]) : null,
          branchId: "main", label: `S${index}`, candidateId: run, modelSource: current.artifact(run).modelSource,
          recordDigest: digest(`record:${projectId}:${run}`), acceptedBy: "studio:explicit-user-action", acceptance: null }));
        return json({ projectId, branchId: "main", stages, candidates: [], studies: [], warnings: [], branches: [{ branchId: "main",
          parentBranch: null, forkStageRef: stages[0].stageRef, headStageRef: stages.at(-1).stageRef }] });
      }
      if (name === "/api/board") return json(current.board);
      if (name === "/api/publication") return json(current.publication);
      // #326: a retained model's preview; this fixture retains none, and a runtime without one answers null.
      if (/^\/api\/model-assets\/[0-9a-f]{64}\/preview$/.test(name)) return json(null);
      if (name === "/api/drawings/styles") return json({ styles: [] });
      // #320: Drawing reads a drawing's recipe corrections; this fixture retains none.
      if (name === "/api/drawings/corrections") return json({ projectId, drawingId: url.searchParams.get("drawingId"), pairs: [], suggestions: [] });
      // #271: the head a real runtime would resolve for this fixture, and its read-only Worktree Graph.
      const home = current.assets.get(current.home).dto;
      const tree = designTrees.get(projectId);
      const head = { runId: current.home, stateDigest: home.modelSource.stateDigest, recordDigest: digest(`record:${projectId}:${current.home}`),
        sourceStageRef: null, branchId: null, accepted: false, origin: "reference", label: null, modelSource: home.modelSource, lineage: [current.home],
        // A Design Tree's Current continues its last Stage through the runs it made after it.
        ...(tree ? { sourceStageRef: `project://${projectId}/runs/${tree.stages.at(-1)}/review/design-stage.json`, branchId: "main",
          origin: "working-position", lineage: [current.home, ...tree.edits, tree.stages.at(-1)] } : {}) };
      const saved = headsFollowDraft.has(projectId) ? workingDrafts.get(projectId)?.current?.runId ?? null : null;
      if (saved) Object.assign(head, { runId: saved, stateDigest: stateDigestOf(projectId, saved), recordDigest: digest(`record:${projectId}:${saved}`),
        sourceStageRef: null, branchId: null, accepted: false, origin: "working-position", modelSource: current.artifact(saved).modelSource, lineage: [saved] });
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
    // #332: Layout saves its edits as they are made, on the exact revision it read.
    if (method === "PUT" && name === "/api/publication") {
      assert.equal(body.baseRevisionSha256, current.publication.revisionSha256, "Layout writes preserve their exact retained base");
      current.publication = { ...current.publication, title: body.title, spec: body.spec, pages: body.pages, revisionSha256: digest(JSON.stringify(body)) };
      return json(current.publication);
    }
    if (method === "PUT" && name === "/api/board") {
      assert.equal(body.baseRevisionSha256, current.board.revisionSha256, "Board writes preserve their exact retained base");
      current.board = { projectId, title: body.title, elements: body.elements, seenDocuments: body.seenDocuments, revisionSha256: digest(JSON.stringify(body)) };
      return json(current.board);
    }
    throw new Error(`Unexpected project request: ${method} ${projectId} ${name}`);
  }
  return { handle, requests, notModified, projects, workingDrafts, designTrees, runStates, headsFollowDraft, studioEvents, stateDigestOf, indexes, commit, rebuild };
}
