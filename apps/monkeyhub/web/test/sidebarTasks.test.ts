import assert from "node:assert/strict";
import test from "node:test";

import type { ChatSummary, HubRuntimeDto, OperationRecord, ProjectRuntimeDto } from "../src/api/generated";
import { activeWork, newSchemes, sidebarTasks } from "../src/sidebarTasks.ts";

const chat = (id: string, projectId: string, status: ChatSummary["status"] = "idle"): ChatSummary => ({
  id, projectId, projectDir: `D:\\work\\${projectId}`, title: `Chat ${id}`, provider: "codex", status, createdAt: "", updatedAt: "",
});
const operation = (overrides: Partial<OperationRecord> & Pick<OperationRecord, "operationId" | "status">): OperationRecord => ({
  projectId: "A", kind: "POST /api/proposals/p/candidate", source: "studio", ...overrides,
});
const project = (projectId: string, overrides: Partial<ProjectRuntimeDto> = {}): ProjectRuntimeDto => ({
  runtimeId: `runtime-${projectId}`, projectId, projectDir: `D:\\work\\${projectId}`, state: "open", workers: [], operations: [], sessions: [],
  ...overrides,
});
const runtime = (...projects: ProjectRuntimeDto[]): HubRuntimeDto => ({ serverId: "hub", sequence: 1, workers: [], projects });
const listed = [{ projectId: "A", projectDir: "D:\\work\\A", name: "Harbour study", chatCount: 1 }];

test("running conversations and their operations are one task each, named by project and chat", () => {
  const running = chat("c1", "A", "running");
  const tasks = sidebarTasks(runtime(project("A", {
    sessions: [running, chat("c2", "A")],
    operations: [
      operation({ operationId: "o1", status: "executing", sessionId: "c1" }),
      operation({ operationId: "o2", status: "completed", sessionId: "c2" }),
    ],
  })), listed);
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0]!.chat?.id, "c1");
  assert.equal(tasks[0]!.projectName, "Harbour study");
  assert.equal(tasks[0]!.action, "POST /api/proposals/p/candidate", "the operation says what the running turn is doing");
  assert.equal(tasks[0]!.state, "running");
});

test("queued and unattributed work is listed after running work, across open projects only", () => {
  const tasks = sidebarTasks(runtime(
    project("A", { operations: [operation({ operationId: "q", status: "queued", kind: "POST /api/drawings/sheets" })] }),
    project("B", { sessions: [chat("b1", "B", "running")] }),
    project("C", { state: "closed", sessions: [chat("c9", "C", "running")] }),
  ), listed);
  assert.deepEqual(tasks.map((task) => [task.projectName, task.chat?.id ?? null, task.state]),
    [["B", "b1", "running"], ["Harbour study", null, "queued"]]);
  assert.deepEqual([...activeWork(tasks)], [["D:\\work\\B", 1], ["D:\\work\\A", 1]]);
});

test("a candidate job still running after its request was answered stays listed under its chat", () => {
  const job = { jobId: "j1", status: "running", candidateId: "cand-1", proposalId: "p-7", createdAt: "", startedAt: null, finishedAt: null,
    error: null, wallTimeS: null, lane: "parallel", waitingFor: null, waitingReason: null };
  const tasks = sidebarTasks(runtime(project("A", {
    sessions: [chat("c1", "A")],
    operations: [operation({ operationId: "o1", status: "completed", sessionId: "c1", jobId: "j1", candidateId: "cand-1" })],
    retained: { projectId: "A", projectDir: "D:\\work\\A", published: { version: 0, stateSha256: null }, jobs: [job], candidates: [],
      branches: [], stages: [], errors: [], runsScanned: 0, hasMore: false } as unknown as ProjectRuntimeDto["retained"],
  })), listed);
  assert.equal(tasks.length, 1);
  assert.equal(tasks[0]!.chat?.id, "c1");
  assert.equal(tasks[0]!.action, "POST /api/proposals/p-7/candidate");
});

test("the new-schemes badge waits for admission data instead of guessing", () => {
  assert.equal(newSchemes(project("A")), null);
  assert.deepEqual(sidebarTasks(null, listed), []);
});
