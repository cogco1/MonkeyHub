import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import type { CandidateAcceptedDto, FrameDto, ProposalDto, RuntimeDto } from "../src/api/generated/types.gen.ts";
import type { DraftCommand, DraftSnapshot } from "../src/features/stage/modelDraft.ts";
import { createModelDraftSyncAttempt, createLocalDraftWriter, syncModelDraft,
  type ModelDraftSource, type ModelDraftSyncApi } from "../src/features/stage/syncModelDraft.ts";

const source: ModelDraftSource = { projectId: "project", stateDigest: "original-base", sourceRunId: "base-run", sourceStageRef: "stage:one" };
const frame: FrameDto = { levels: [0, 8].map(elevation => ({ levelId: `level-${elevation}`, elevation,
  role: "datum", elementsOn: [], closure: [] })), axes: [], honesty: [] };
const requestId = "12345678-1234-1234-1234-123456789abc";
const candidateId = "hub-cand-12345678123412341234123456789abc";
const accepted: CandidateAcceptedDto = { jobId: "job-one", candidateId, status: "queued" };
const snapshot = (...commands: DraftCommand[]): DraftSnapshot => ({ commands, objects: new Map() });
const draw = (elementId = "drawn-a"): DraftCommand => ({ kind: "sketch", elementId, componentId: "room",
  action: { profile: [[0, 0], [3, 1], [5, 2]], base: 7, height: 0, closed: false,
    plane: { origin: [10, 20, 7], xAxis: [0, 1, 0], yAxis: [0, 0, 1], normal: [1, 0, 0] } } });
const failure = (code: string) => Object.assign(new Error(code), { code });
const proposal = (proposalId: string) => ({ proposalId }) as ProposalDto;
function fixture() {
  const calls: { kind: string; body: any }[] = [];
  const propose = async (kind: string, body: unknown) => {
    calls.push({ kind, body });
    return proposal(`p${calls.length}`);
  };
  const api: ModelDraftSyncApi = {
    sketch: body => propose("sketch", body), transform: body => propose("transform", body),
    pushPull: body => propose("pushPull", body), removeElement: body => propose("delete", body),
    editElevation: body => propose("elevation", body),
    startCandidate: async (id, trace, key) => { calls.push({ kind: "candidate", body: { id, trace, key } }); return accepted; },
    runtime: async id => { calls.push({ kind: "runtime", body: id }); return { projectId: source.projectId, jobs: [], candidates: [] } as unknown as RuntimeDto; },
  };
  return { api, calls };
}

test("autosave retains its final proposal and request before dispatch; failed storage cannot submit", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId);
  const frozen = snapshot(draw());
  const retained: string[] = [];
  await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api, async () => {
    assert.equal(calls.filter(call => call.kind === "candidate").length, 0);
    retained.push(attempt.finalProposalId!);
    throw new Error("disk unavailable");
  }), /disk unavailable/);
  assert.equal(calls.filter(call => call.kind === "candidate").length, 0);
  const restored = JSON.parse(JSON.stringify(attempt));
  delete restored.inFlight;
  await syncModelDraft(frozen, source, frame, restored, api, async () => { retained.push(restored.finalProposalId!); });
  assert.deepEqual(retained, ["p1", "p1"]);
  assert.equal(calls.filter(call => call.kind === "sketch").length, 1);
  assert.deepEqual(calls.at(-1)?.body, { id: "p1", trace: undefined, key: requestId });
});

test("two windows cannot overwrite or clear each other's local draft even after a fresh index read", async () => {
  let state: any = { projectId: "project", revisionSha256: "initial", localDraft: null };
  let writes = 0;
  const api = { workingDraft: async () => structuredClone(state), retainLocalDraft: async (body: any) => {
    assert.equal(body.baseRevisionSha256, state.revisionSha256);
    state = { ...state, revisionSha256: `r${++writes}`, localDraft: body.draft && { ...body.draft, updatedAt: `t${writes}` } };
    return structuredClone(state);
  } };
  const first = createLocalDraftWriter(api, state), second = createLocalDraftWriter(api, state);
  const draft = { source, commands: [{ kind: "delete", elementId: "a" }] };
  await first(draft);
  await assert.rejects(second(draft), /另一窗口/);
  await assert.rejects(second(null, source), /另一窗口/);
  assert.equal(writes, 1);
  // An index-only candidate update does not change this editor's local witness.
  state.revisionSha256 = "candidate-completed";
  await first(null, source);
  assert.equal(state.localDraft, null);
  assert.equal(writes, 2);
});

// GH-293: the revision is only a concurrency token. A write that lost the
// position between its read and its write reads it again; the witness and the
// server's expected source still guard the content.
const stale = () => Object.assign(failure("WORKING_DRAFT_STALE"), { status: 409 });
const draftOf = (elementId: string) => ({ source, commands: [{ kind: "delete", elementId }] });

test("a write that lost the position between its read and its write reads it again and lands", async () => {
  let state: any = { projectId: "project", revisionSha256: "r0", localDraft: null };
  const bases: string[] = [];
  let reads = 0, moved = false;
  const api = { workingDraft: async () => { reads += 1; return structuredClone(state); }, retainLocalDraft: async (body: any) => {
    bases.push(body.baseRevisionSha256);
    // A candidate starts between this editor's read and its write.
    if (!moved) { moved = true; state = { ...state, revisionSha256: "candidate-started" }; throw stale(); }
    assert.equal(body.baseRevisionSha256, state.revisionSha256);
    state = { ...state, revisionSha256: "r1", localDraft: body.draft && { ...body.draft, updatedAt: "t1" } };
    return structuredClone(state);
  } };
  const write = createLocalDraftWriter(api, state);
  assert.equal((await write(draftOf("a"))).revisionSha256, "r1");
  assert.equal(reads, 2);
  assert.deepEqual(bases, ["r0", "candidate-started"]);
  // The retried write is this editor's own: its next write is not another window's.
  await write(null, source);
  assert.equal(state.localDraft, null);
});

test("the read before a retry still refuses a local draft another window wrote in between", async () => {
  let state: any = { projectId: "project", revisionSha256: "r0", localDraft: null };
  let reads = 0, writes = 0;
  const api = { workingDraft: async () => { reads += 1; return structuredClone(state); }, retainLocalDraft: async () => {
    writes += 1;
    state = { ...state, revisionSha256: "other", localDraft: { ...draftOf("b"), updatedAt: "t" } };
    throw stale();
  } };
  await assert.rejects(createLocalDraftWriter(api, state)(draftOf("a")), /另一窗口/);
  assert.deepEqual([reads, writes], [2, 1]);
  assert.deepEqual(state.localDraft.commands, [{ kind: "delete", elementId: "b" }]);
});

test("only a stale position is read again, at most three times; any other refusal answers at once", async () => {
  for (const [code, attempts] of [["WORKING_DRAFT_STALE", 3], ["WORKING_DRAFT_SOURCE_CHANGED", 1], ["NETWORK_ERROR", 1]] as const) {
    const state = { projectId: "project", revisionSha256: "r0", localDraft: null } as any;
    let reads = 0, writes = 0;
    const api = { workingDraft: async () => { reads += 1; return structuredClone(state); },
      retainLocalDraft: async () => { writes += 1; throw Object.assign(failure(code), { status: 409 }); } };
    await assert.rejects(createLocalDraftWriter(api, state)(draftOf("a")), { code, status: 409 });
    assert.deepEqual([reads, writes], [attempts, attempts], code);
  }
});

test("mixed actions preserve their exact base and stable copy dependency; only the last proposal runs", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId);
  const frozen = snapshot(draw(),
    { kind: "direct", elementId: "drawn-a", action: { kind: "pushPull", distance: 2, normal: [1, 2, 3] } },
    { kind: "direct", elementId: "drawn-a", copyElementId: "copy-b", action: { kind: "copy", translation: [4, 5, 6] } },
    { kind: "direct", elementId: "copy-b", action: { kind: "rotate", angleDegrees: 30, axis: [1, 2, 3] } },
    { kind: "direct", elementId: "copy-b", action: { kind: "scale", scale: [2, 3, 4] } },
    { kind: "direct", elementId: "copy-b", action: { kind: "move", translation: [7, 8, 9] } },
    { kind: "delete", elementId: "drawn-a" });
  assert.equal(await syncModelDraft(frozen, source, frame, attempt, api), accepted);
  for (const [index, call] of calls.slice(0, -1).entries()) {
    for (const [key, value] of Object.entries(source)) assert.equal(call.body[key], value);
    assert.equal(call.body.sourceProposalId, index ? `p${index}` : null);
  }
  assert.deepEqual(calls[0]!.body.profile, [[0, 0], [3, 1], [5, 2]]);
  assert.equal(calls[0]!.body.closed, false);
  assert.equal(calls[0]!.body.baseLevel, "level-8");
  assert.deepEqual(calls[0]!.body.plane, { origin: [10, -1, 20], xAxis: [0, 0, 1], yAxis: [0, 1, 0], normal: [1, 0, 0] });
  assert.deepEqual(calls[1]!.body.normal, [1, 3, 2]);
  assert.deepEqual(calls[2]!.body.translation, [4, 6, 5]);
  assert.equal(calls[2]!.body.copyElementId, "copy-b");
  assert.equal(calls[3]!.body.elementId, "copy-b");
  assert.equal(calls[3]!.body.angleDegrees, -30);
  assert.deepEqual(calls[3]!.body.axis, [1, 3, 2]);
  assert.deepEqual(calls[4]!.body.scale, [2, 4, 3]);
  assert.deepEqual(calls[5]!.body.translation, [7, 9, 8]);
  assert.deepEqual(calls.at(-1), { kind: "candidate", body: { id: "p7", trace: undefined, key: requestId } });
  await syncModelDraft(frozen, source, frame, attempt, api);
  assert.equal(calls.length, 8, "an accepted attempt never submits again");
});

test("default horizontal drawings keep signed extrusion, closure default and level-relative elevation", async () => {
  const { api, calls } = fixture();
  await syncModelDraft(snapshot({ kind: "sketch", elementId: "flat", componentId: "room",
    action: { profile: [[0, 0], [2, 0], [0, 2]], base: 2, height: -3 } }), source, frame, createModelDraftSyncAttempt(), api);
  assert.equal(calls[0]!.body.height, -3);
  assert.equal(calls[0]!.body.closed, true);
  assert.deepEqual(calls[0]!.body.plane, { origin: [0, 2, 0], xAxis: [1, 0, 0], yAxis: [0, 0, 1], normal: [0, 1, 0] });
});

test("net-zero prefixes clear the old proposal and later edits start from the original base", async () => {
  for (const later of [false, true]) {
    const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt();
    api.removeElement = async body => { calls.push({ kind: "delete", body }); throw failure("PROPOSAL_CHAIN_NO_CHANGE"); };
    const frozen = snapshot(draw(), { kind: "delete", elementId: "drawn-a" }, ...(later ? [draw("drawn-c")] : []));
    const result = await syncModelDraft(frozen, source, frame, attempt, api);
    assert.equal(result, later ? accepted : null);
    assert.equal(calls.filter(c => c.kind === "candidate").length, later ? 1 : 0);
    if (later) assert.equal(calls[2]!.body.sourceProposalId, null);
    else assert.equal(attempt.sourceProposalId, null);
  }
  const { api, calls } = fixture();
  assert.equal(await syncModelDraft(snapshot(), source, frame, createModelDraftSyncAttempt(), api), null);
  assert.deepEqual(calls, []);
});

test("an interrupted proposal retries only its unconfirmed action against the same prefix", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt();
  const remove = api.removeElement;
  api.removeElement = async body => { calls.push({ kind: "lost-delete", body }); throw failure("NETWORK_ERROR"); };
  const frozen = snapshot(draw(), { kind: "delete", elementId: "existing" });
  await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api), /NETWORK_ERROR/);
  assert.equal(attempt.nextCommand, 1);
  api.removeElement = remove;
  await syncModelDraft(frozen, source, frame, attempt, api);
  assert.equal(calls.filter(c => c.kind === "sketch").length, 1);
  assert.equal(calls[2]!.body.sourceProposalId, "p1");
});

test("a lost prefix is replayed once from the unchanged base, before any candidate submission", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt();
  const remove = api.removeElement;
  let unavailable = true;
  api.removeElement = async body => {
    if (unavailable) { unavailable = false; throw failure("PROPOSAL_NOT_FOUND"); }
    return remove(body);
  };
  await syncModelDraft(snapshot(draw(), { kind: "delete", elementId: "existing" }), source, frame, attempt, api);
  assert.equal(calls.filter(c => c.kind === "sketch").length, 2);
  assert.equal(calls[1]!.body.sourceProposalId, null);
  assert.equal(calls[2]!.body.sourceProposalId, "p2");
  assert.equal(calls.filter(c => c.kind === "candidate").length, 1);
});

test("candidate response loss freezes proposal and request IDs across retries", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId), frozen = snapshot(draw());
  const start = api.startCandidate;
  api.startCandidate = async (id, trace, key) => { calls.push({ kind: "lost-candidate", body: { id, trace, key } }); throw failure("NETWORK_ERROR"); };
  await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api), /NETWORK_ERROR/);
  assert.equal(attempt.finalProposalId, "p1");
  api.startCandidate = start;
  await syncModelDraft(frozen, source, frame, attempt, api);
  assert.equal(calls.filter(c => c.kind === "sketch").length, 1);
  assert.deepEqual(calls.at(-1)!.body, calls[1]!.body);
});

test("candidate recovery returns an existing job, but not an unverified runtime placeholder", async () => {
  for (const mode of ["matching", "missing", "foreign-proposal"] as const) {
    const { api } = fixture(), attempt = createModelDraftSyncAttempt(requestId);
    api.startCandidate = async () => { throw failure("OPERATION_NEEDS_RECOVERY"); };
    api.runtime = async () => ({ projectId: source.projectId,
      jobs: mode === "missing" ? [] : [{ jobId: "already-running", candidateId, status: "running",
        proposalId: mode === "matching" ? "p1" : "another-proposal" }],
      candidates: [{ candidateId, status: "needs_recovery" }],
    }) as unknown as RuntimeDto;
    const result = syncModelDraft(snapshot(draw()), source, frame, attempt, api);
    if (mode === "matching") assert.deepEqual(await result, { jobId: "already-running", candidateId, status: "running" });
    else await assert.rejects(result, /OPERATION_NEEDS_RECOVERY/);
  }
});

test("a missing final proposal never changes an already attempted candidate request", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId), frozen = snapshot(draw());
  api.startCandidate = async () => { throw failure("PROPOSAL_NOT_FOUND"); };
  for (let i = 0; i < 2; i++) await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api), /PROPOSAL_NOT_FOUND/);
  assert.equal(attempt.finalProposalId, "p1");
  assert.equal(calls.length, 1);
});

test("simultaneous Sync calls share the same attempt and candidate submission", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId), frozen = snapshot(draw());
  const first = syncModelDraft(frozen, source, frame, attempt, api);
  const second = syncModelDraft(frozen, source, frame, attempt, api);
  assert.equal(first, second);
  await Promise.all([first, second]);
  assert.equal(calls.filter(c => c.kind === "candidate").length, 1);
});

test("incomplete frozen inputs fail before their API request", async () => {
  for (const command of [
    { kind: "direct", elementId: "a", action: { kind: "pushPull", distance: 1 } },
    { kind: "direct", elementId: "a", action: { kind: "copy", translation: [1, 0, 0] } },
  ] as DraftCommand[]) {
    const { api, calls } = fixture();
    await assert.rejects(syncModelDraft(snapshot(command), source, frame, createModelDraftSyncAttempt(), api));
    assert.deepEqual(calls, []);
  }
  const { api, calls } = fixture();
  await assert.rejects(syncModelDraft(snapshot(draw()), source, { ...frame, levels: [] }, createModelDraftSyncAttempt(), api), /base level/);
  assert.deepEqual(calls, []);
});

test("client keeps the explicit Sync idempotency key, diagnostic trace and targeted runtime read", async t => {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  const { createStudioClient } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  const studio = createStudioClient(new ServerConnection("http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio"));
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => { requests.push(request); return Response.json(accepted); });
  for (let i = 0; i < 2; i++) await studio.startCandidate("final-p", { operationId: "trace", parentEventId: "parent" }, requestId);
  await studio.runtime(candidateId);
  assert.equal(requests[0]!.headers.get("Idempotency-Key"), requestId);
  assert.equal(requests[1]!.headers.get("Idempotency-Key"), requestId);
  assert.equal(requests[0]!.headers.get("X-Monkey-Operation"), "trace");
  assert.equal(requests[0]!.headers.get("X-Monkey-Parent"), "parent");
  assert.equal(new URL(requests[2]!.url).searchParams.get("candidateId"), candidateId);
  assert.equal(requests[2]!.method, "GET");
});

test("new upright masses are detached before later explicit bindings and only final elevation proposal runs", async () => {
  const { api, calls } = fixture();
  const commands: DraftCommand[] = [
    { kind: "sketch", elementId: "lower", componentId: "room", action: { profile: [[0,0],[4,0],[4,3],[0,3]], base: -1, height: 3 } },
    { kind: "elevation", elementId: "upper", action: "bind-base", reference: { kind: "element-top", id: "lower", offset: 0.5 } },
    { kind: "elevation", elementId: "lower", action: "set-height", value: 4.5 },
    { kind: "elevation", elementId: "lower", action: "set-datum", levelId: "roof", name: "Roof ref", value: 12 },
  ];
  const attempt = createModelDraftSyncAttempt(requestId);
  await syncModelDraft(snapshot(...commands), source, frame, attempt, api);
  assert.deepEqual(calls.map(row => row.kind), ["sketch", "elevation", "elevation", "elevation", "elevation", "candidate"]);
  assert.equal(calls[1]!.body.action, "detach-base");
  assert.equal(calls[1]!.body.sourceProposalId, "p1");
  assert.equal(calls[2]!.body.sourceProposalId, "p2");
  assert.deepEqual(calls[2]!.body.reference, { kind: "element-top", id: "lower", offset: 0.5 });
  assert.equal(calls.at(-1)!.body.id, "p5");
  assert.equal("elementId" in calls[4]!.body, false, "datum DTO accepts levelId rather than the contextual UI selection");
  assert.equal(attempt.nextCommand, commands.length);
  for (const row of calls.slice(0, -1)) assert.equal(row.body.stateDigest, source.stateDigest);
});

test("rejected elevation Sync preserves its confirmed prefix without candidate submission", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId);
  const original = api.editElevation;
  api.editElevation = async body => {
    if (body.action === "set-height") throw failure("ELEVATION_EDIT_INVALID");
    return original(body);
  };
  const frozen = snapshot(
    { kind: "elevation", elementId: "upper", action: "bind-base", reference: { kind: "element-top", id: "lower", offset: 0 } },
    { kind: "elevation", elementId: "lower", action: "set-height", value: -1 });
  await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api), { code: "ELEVATION_EDIT_INVALID" });
  assert.equal(attempt.nextCommand, 1); assert.equal(attempt.sourceProposalId, "p1");
  assert.equal(attempt.finalProposalId, null); assert.equal(calls.length, 1);
  assert.equal(frozen.commands.length, 2);
});

test("sketch uses the datum at its replay prefix, including retries, rather than old or final levels", async () => {
  const { api, calls } = fixture(), attempt = createModelDraftSyncAttempt(requestId);
  const original = api.sketch; let refuse = true;
  api.sketch = async body => { if (refuse) { refuse = false; throw failure("NETWORK_ERROR"); } return original(body); };
  const frozen = snapshot(
    { kind: "elevation", elementId: "mass", action: "set-datum", levelId: "level-0", name: "Ground", value: 2 },
    { kind: "sketch", elementId: "mass", componentId: "room", action: { profile: [[0,0],[4,0],[4,3],[0,3]], base: 0, height: 2 } },
    { kind: "elevation", elementId: "mass", action: "set-datum", levelId: "level-0", name: "Ground", value: 6 });
  await assert.rejects(syncModelDraft(frozen, source, frame, attempt, api), { code: "NETWORK_ERROR" });
  assert.equal(attempt.nextCommand, 1);
  await syncModelDraft(frozen, source, frame, attempt, api);
  const sketch = calls.find(row => row.kind === "sketch")!;
  assert.equal(sketch.body.baseLevel, "level-0");
  assert.equal(sketch.body.plane.origin[1], -2);
  assert.equal(calls[2]!.body.action, "detach-base");
  assert.equal(frame.levels[0]!.elevation, 0, "the original source frame is immutable");
});
