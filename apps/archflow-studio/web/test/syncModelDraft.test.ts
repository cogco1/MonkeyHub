import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import type { CandidateAcceptedDto, FrameDto, ProposalDto, RuntimeDto } from "../src/api/generated/types.gen.ts";
import type { DraftCommand, DraftSnapshot } from "../src/features/stage/modelDraft.ts";
import { createModelDraftSyncAttempt, syncModelDraft,
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
    startCandidate: async (id, trace, key) => { calls.push({ kind: "candidate", body: { id, trace, key } }); return accepted; },
    runtime: async id => { calls.push({ kind: "runtime", body: id }); return { projectId: source.projectId, jobs: [], candidates: [] } as unknown as RuntimeDto; },
  };
  return { api, calls };
}

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
  const { studio } = await vite.ssrLoadModule("/src/api/client.ts");
  const { ServerConnection } = await vite.ssrLoadModule("/src/api/connection.ts");
  new ServerConnection("http://127.0.0.1:18180/api/runtime/projects/12345678-1234-1234-1234-123456789abc/studio").configure();
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
