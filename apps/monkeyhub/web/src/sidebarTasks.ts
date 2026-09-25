/**
 * The sidebar's "Tasks" entry (#300): Agent work running or waiting in every
 * open project, read from the runtime snapshot the Hub already receives.
 *
 * A conversation turn, a project operation and a retained job can all be the
 * same piece of work; each is listed once, under the conversation that asked
 * for it when there is one. Nothing here starts, stops or reorders work.
 */

import type { ChatProject, ChatSummary, HubRuntimeDto, ProjectRuntimeDto } from "./api/generated";

export type TaskState = "running" | "queued";

export interface SidebarTask {
  /** Stable for rendering only; never shown. */
  key: string;
  projectDir: string;
  projectId: string;
  projectName: string;
  /** The conversation to open, when the work belongs to one. */
  chat: ChatSummary | null;
  /** The request the work carries out (`METHOD /path`), named in plain words where it is shown. */
  action: string | null;
  state: TaskState;
}

const ACTIVE_OPERATIONS = new Set(["queued", "planning", "validated", "executing", "committing"]);
const ACTIVE_JOBS = new Set(["queued", "running"]);

const projectName = (runtime: ProjectRuntimeDto, projects: readonly ChatProject[]) =>
  projects.find((row) => row.projectDir === runtime.projectDir && row.projectId === runtime.projectId)?.name
  ?? runtime.projectDir.split(/[\\/]/).filter(Boolean).at(-1) ?? runtime.projectId;

export function sidebarTasks(runtime: HubRuntimeDto | null, projects: readonly ChatProject[]): SidebarTask[] {
  const tasks: SidebarTask[] = [];
  for (const project of runtime?.projects ?? []) {
    if (project.state === "closed") continue;
    const base = { projectDir: project.projectDir, projectId: project.projectId, projectName: projectName(project, projects) };
    const sessions = project.sessions ?? [], operations = project.operations ?? [];
    const byChat = new Map<string, SidebarTask>();
    const add = (task: SidebarTask) => { tasks.push(task); if (task.chat) byChat.set(task.chat.id, task); };
    const merge = (chat: ChatSummary | null, state: TaskState, action: string, key: string) => {
      const listed = chat ? byChat.get(chat.id) : undefined;
      if (!listed) { add({ key, ...base, chat, action, state }); return; }
      if (state === "running") listed.state = "running";
      listed.action ??= action;
    };
    for (const session of sessions) {
      if (session.status === "running") add({ key: `chat:${session.id}`, ...base, chat: session, action: null, state: "running" });
    }
    for (const operation of operations) {
      if (!ACTIVE_OPERATIONS.has(operation.status)) continue;
      const chat = operation.sessionId ? sessions.find((row) => row.id === operation.sessionId) ?? null : null;
      merge(chat, operation.status === "queued" ? "queued" : "running", operation.kind, `operation:${operation.operationId}`);
    }
    // A candidate job keeps running after the request that admitted it was answered.
    for (const job of project.retained?.jobs ?? []) {
      if (!ACTIVE_JOBS.has(job.status)) continue;
      const operation = operations.find((row) => (row.jobId && row.jobId === job.jobId) || (row.candidateId && row.candidateId === job.candidateId));
      if (operation && ACTIVE_OPERATIONS.has(operation.status)) continue;
      const chat = operation?.sessionId ? sessions.find((row) => row.id === operation.sessionId) ?? null : null;
      merge(chat, job.status === "queued" ? "queued" : "running", `POST /api/proposals/${job.proposalId}/candidate`, `job:${job.jobId}`);
    }
  }
  // Running work first; otherwise the order the runtime reported.
  return tasks.map((task, index) => ({ task, index }))
    .sort((a, b) => Number(a.task.state === "queued") - Number(b.task.state === "queued") || a.index - b.index)
    .map(({ task }) => task);
}

/** How much work each open project has running or waiting, by project folder. */
export function activeWork(tasks: readonly SidebarTask[]): ReadonlyMap<string, number> {
  const counts = new Map<string, number>();
  for (const task of tasks) counts.set(task.projectDir, (counts.get(task.projectDir) ?? 0) + 1);
  return counts;
}

/**
 * Hook for #300's "N new" schemes badge, waiting on #294 (candidate admission
 * S1–S2): how many schemes a project received since the architect last looked.
 * The runtime reports no admission records yet, so this answers null and the
 * project row shows no badge; it never counts or guesses a number meanwhile.
 */
export function newSchemes(_project: ProjectRuntimeDto | undefined): number | null {
  return null;
}
