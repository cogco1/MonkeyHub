import assert from "node:assert/strict";
import test from "node:test";

import type { ChatSummary, OperationRecord } from "../src/api/generated";
import type { WorktreeGraphDto, WorktreeLineDto } from "../workspaces/src/api/generated";
import { ownerOf, projectStatus, refLabel, workRows } from "../src/worktreeGraph.ts";

const line = (overrides: Partial<WorktreeLineDto>): WorktreeLineDto => ({
  lineId: "line", kind: "result", runId: null, jobId: null, label: null, baseRunId: null, baseStageRef: null, branchId: null,
  status: "ready", relation: "diverged", reads: [], writes: [], reconcile: "none", conflicts: [], detail: null, updatedAt: null,
  ...overrides,
});
const graph: WorktreeGraphDto = {
  projectId: "p", revisionSha256: "r", warnings: ["Render results could not be read: invalid record"],
  head: { runId: "cand-3", stateDigest: "d", recordDigest: "r", sourceStageRef: "stage", branchId: "main", accepted: false,
    origin: "working-position", label: null, modelSource: null, lineage: ["cand-3", "stage-run"] },
  lines: [
    line({ lineId: "head:cand-3", kind: "head", runId: "cand-3", status: "current", relation: "head" }),
    line({ lineId: "branch:facade-b", kind: "branch", runId: "stage-run", label: "S0", branchId: "facade-b", status: "accepted", relation: "separate" }),
    line({ lineId: "result:cand-4", runId: "cand-4", reconcile: "can-combine", writes: ["parameter:module"] }),
    line({ lineId: "result:cand-5", runId: "cand-5", reconcile: "conflict", conflicts: ["entity:wall-17"], writes: ["entity:wall-17"] }),
    line({ lineId: "running:cand-6", kind: "running", runId: "cand-6", status: "running", relation: "ahead", writes: ["entity:roof"] }),
    line({ lineId: "running:cand-7", kind: "running", runId: "cand-7", status: "interrupted", relation: "ahead" }),
  ],
  representations: [
    { kind: "drawing", itemId: "floor-plan", label: "floor-plan", state: "current", sourceRunId: "cand-3", detail: null },
    { kind: "drawing", itemId: "floor-plan-2", label: "floor-plan-2", state: "frozen", sourceRunId: "stage-run", detail: "Kept on the version it was drawn from." },
    { kind: "render", itemId: "render-1", label: "render.png", state: "stale", sourceRunId: null, detail: "The project model has changed since this was made." },
    { kind: "render", itemId: "render-2", label: "AI Render", state: "running", sourceRunId: null, detail: null },
  ],
};
const operations = [
  { operationId: "o1", projectId: "p", kind: "k", source: "chat", status: "completed", candidateId: "cand-4", sessionId: "chat-1" },
  { operationId: "o2", projectId: "p", kind: "k", source: "studio", status: "completed", candidateId: "cand-5", sessionId: null },
] as OperationRecord[];
const sessions = [{ id: "chat-1", title: "Facade study", provider: "codex" }] as ChatSummary[];
const words = { tools: "Project tools", unattributed: "Unattributed" };

test("the project status counts current, stale, kept and background work without naming candidates", () => {
  assert.deepEqual(projectStatus(graph), {
    headLabel: null, accepted: false,
    drawings: { current: 1, stale: 0, frozen: 1 }, renders: { current: 0, stale: 1, running: 1 },
    background: 2, separate: 2, conflicts: 1, unreadable: 1,
  });
});

test("owners come from the Hub journal: a conversation, the project tools, or unattributed", () => {
  assert.equal(ownerOf("cand-4", operations, sessions, words), "Facade study");
  assert.equal(ownerOf("cand-5", operations, sessions, words), "Project tools");
  assert.equal(ownerOf("cand-9", operations, sessions, words), "Unattributed");
  assert.equal(ownerOf(null, operations, sessions, words), "Unattributed");
});

test("retained refs read as element and parameter names", () => {
  assert.deepEqual(["entity:wall-17", "parameter:module", "relation:r1", "plain"].map(refLabel), ["wall-17", "module", "r1", "plain"]);
});

test("rows list running work first, conflicts before other results, and never the head itself", () => {
  const rows = workRows(graph, operations, sessions, words);
  assert.deepEqual(rows.map((row) => row.key), ["running:cand-6", "running:cand-7", "result:cand-5", "result:cand-4", "branch:facade-b"]);
  assert.equal(rows.find((row) => row.kind === "branch")?.owner, null);
  assert.deepEqual(rows.find((row) => row.key === "result:cand-5")?.conflicts, ["entity:wall-17"]);
});
