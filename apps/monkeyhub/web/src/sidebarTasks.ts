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
  /** When the work started (its job's start, else its request's), and how long this project's finished jobs in its lane took (the median). */
  startedAt?: string | null;
  expectedSeconds?: number | null;
  /** How many times the Hub has sent the request, when it had to send it again (F046). */
  attempts?: number;
}

/** The median of what this project's finished jobs took, in the job's lane where it has any. */
function expectedSeconds(jobs: readonly { status: string; lane: string; wallTimeS: number | null }[], lane: string): number | null {
  const done = jobs.filter((job) => job.status === "succeeded" && job.wallTimeS !== null && job.wallTimeS > 0);
  const sameLane = done.filter((job) => job.lane === lane);
  const times = (sameLane.length ? sameLane : done).map((job) => job.wallTimeS!).sort((a, b) => a - b);
  if (!times.length) return null;
  const middle = Math.floor(times.length / 2);
  return times.length % 2 ? times[middle]! : (times[middle - 1]! + times[middle]!) / 2;
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
    const merge = (chat: ChatSummary | null, state: TaskState, action: string, key: string, timing: Pick<SidebarTask, "startedAt" | "expectedSeconds" | "attempts"> = {}) => {
      const listed = chat ? byChat.get(chat.id) : undefined;
      if (!listed) { add({ key, ...base, chat, action, state, ...timing }); return; }
      if (state === "running") listed.state = "running";
      listed.action ??= action;
      listed.startedAt ??= timing.startedAt; listed.expectedSeconds ??= timing.expectedSeconds; listed.attempts ??= timing.attempts;
    };
    for (const session of sessions) {
      if (session.status === "running") add({ key: `chat:${session.id}`, ...base, chat: session, action: null, state: "running" });
    }
    const jobs = project.retained?.jobs ?? [];
    for (const operation of operations) {
      if (!ACTIVE_OPERATIONS.has(operation.status)) continue;
      const chat = operation.sessionId ? sessions.find((row) => row.id === operation.sessionId) ?? null : null;
      // A drawing or export says how long it has run; a candidate's job also how long such jobs take here.
      const job = operation.jobId ? jobs.find((row) => row.jobId === operation.jobId) : undefined;
      merge(chat, operation.status === "queued" ? "queued" : "running", operation.kind, `operation:${operation.operationId}`,
        { startedAt: job?.startedAt ?? operation.createdAt ?? null, expectedSeconds: job ? expectedSeconds(jobs, job.lane) : null,
          ...((operation.attempts ?? 1) > 1 ? { attempts: operation.attempts } : {}) });
    }
    // A candidate job keeps running after the request that admitted it was answered.
    for (const job of jobs) {
      if (!ACTIVE_JOBS.has(job.status)) continue;
      const operation = operations.find((row) => (row.jobId && row.jobId === job.jobId) || (row.candidateId && row.candidateId === job.candidateId));
      if (operation && ACTIVE_OPERATIONS.has(operation.status)) continue;
      const chat = operation?.sessionId ? sessions.find((row) => row.id === operation.sessionId) ?? null : null;
      merge(chat, job.status === "queued" ? "queued" : "running", `POST /api/proposals/${job.proposalId}/candidate`, `job:${job.jobId}`,
        { startedAt: job.startedAt, expectedSeconds: expectedSeconds(jobs, job.lane) });
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
