/** Personal task history and view pointers. Never sent to the intent compiler or used as project authority. */
import { StudioApiError } from "../api/error";
import type { ModelSourceDto, PendingIntentDto } from "../api/generated";
import { createTranscript, type Entry } from "./transcript";

export interface TaskView {
  draft: string;
  selection: { componentId: string; elementId: string | null } | null;
  base: { runId: string | null; sourceStageRef: string | null; stateDigest: string | null };
}

export type TaskPending = PendingIntentDto & { documentContext?: { projectId: string; modelSource: ModelSourceDto } };
export interface StudioTask {
  id: string;
  projectId: string;
  title: string;
  renamed: boolean;
  createdAt: number;
  updatedAt: number;
  archived: boolean;
  entries: readonly Entry[];
  view: TaskView;
  pending: TaskPending | null;
}

export type TaskStatus = "idle" | "running" | "needsInput" | "ready" | "failed" | "interrupted";
export interface TaskStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; }
export const TASK_STORAGE_KEY = "archflow-studio.tasks.v1";
const ENTRY_KINDS = new Set(["system", "reading", "you", "proposal", "question", "terminal", "refusal", "candidate", "verdict", "compare"]);
const EMPTY_VIEW: TaskView = { draft: "", selection: null, base: { runId: null, sourceStageRef: null, stateDigest: null } };
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const nullableString = (value: unknown) => value === null || typeof value === "string";

function restoredEntry(raw: unknown): Entry | null {
  if (!isRecord(raw) || typeof raw.id !== "string" || typeof raw.kind !== "string" || !ENTRY_KINDS.has(raw.kind)) return null;
  if (raw.kind === "reading") return { kind: "system", id: raw.id,
    text: "The previous page closed before this model request returned. Its result is unknown; it has not been sent again." };
  if (raw.kind === "question") return { kind: "system", id: raw.id,
    text: `This clarification belongs to an earlier page session. Send the request again to continue: ${typeof raw.utterance === "string" ? raw.utterance : ""}` };
  if (["refusal", "question", "terminal"].includes(raw.kind)) {
    if (!isRecord(raw.error) || typeof raw.error.code !== "string" || typeof raw.error.detail !== "string") return null;
    return { ...raw, error: new StudioApiError(raw.error as unknown as ConstructorParameters<typeof StudioApiError>[0]) } as Entry;
  }
  return raw as unknown as Entry;
}

function restoredTask(raw: unknown): StudioTask | null {
  if (!isRecord(raw) || typeof raw.id !== "string" || !/^[\w-]{1,100}$/.test(raw.id) ||
      typeof raw.projectId !== "string" || typeof raw.title !== "string" || !Array.isArray(raw.entries) ||
      !isRecord(raw.view) || !isRecord(raw.view.base) || typeof raw.view.draft !== "string" ||
      !nullableString(raw.view.base.runId) || !nullableString(raw.view.base.sourceStageRef) || !nullableString(raw.view.base.stateDigest)) return null;
  const entries = raw.entries.map(restoredEntry);
  if (entries.some((entry) => entry === null)) return null;
  const selection = raw.view.selection;
  if (selection !== null && (!isRecord(selection) || typeof selection.componentId !== "string" || !nullableString(selection.elementId))) return null;
  return {
    id: raw.id, projectId: raw.projectId, title: raw.title.slice(0, 160), renamed: raw.renamed === true,
    createdAt: typeof raw.createdAt === "number" ? raw.createdAt : 0,
    updatedAt: typeof raw.updatedAt === "number" ? raw.updatedAt : 0, archived: raw.archived === true,
    entries: entries as Entry[], view: raw.view as unknown as TaskView,
    // Continuation capabilities are process-local. Reload does not silently reuse one from a prior server.
    pending: null,
  };
}

export function taskStatus(task: StudioTask, busy = false): TaskStatus {
  if (busy || task.entries.some((entry) => entry.kind === "reading" ||
      (entry.kind === "candidate" && ["queued", "running"].includes(entry.status)))) return "running";
  const last = [...task.entries].reverse().find((entry) => entry.kind !== "system");
  if (last?.kind === "you") return "interrupted";
  if (task.pending || last?.kind === "question") return "needsInput";
  if (last?.kind === "refusal" || last?.kind === "terminal" || (last?.kind === "candidate" && ["failed", "cancelled", "unavailable"].includes(last.status))) return "failed";
  if (last) return "ready";
  return task.entries.some((entry) => entry.kind === "you") ? "interrupted" : "idle";
}

export function createTaskStore(storage: TaskStorage | null, serverKey: string) {
  const key = `${TASK_STORAGE_KEY}:${serverKey}`;
  let tasks: readonly StudioTask[] = [];
  let activeTaskId: string | null = null;
  let storageError = storage === null;
  const busy = new Map<string, boolean>();
  const listeners = new Set<() => void>();
  try {
    const text = storage?.getItem(key);
    if (text) {
      const saved: unknown = JSON.parse(text);
      if (!isRecord(saved) || saved.version !== 1 || !Array.isArray(saved.tasks)) throw new Error("Invalid task history");
      const restored = saved.tasks.map(restoredTask);
      if (restored.some((task) => task === null)) throw new Error("Invalid task history");
      tasks = restored as StudioTask[];
      activeTaskId = typeof saved.activeTaskId === "string" ? saved.activeTaskId : null;
    }
  } catch { storageError = true; }
  let snapshot = { tasks, activeTaskId, storageError };
  const publish = (persist = true) => {
    if (persist && storage && !storageError) {
      try { storage.setItem(key, JSON.stringify({ version: 1, tasks, activeTaskId })); }
      catch { storageError = true; }
    }
    snapshot = { tasks, activeTaskId, storageError };
    listeners.forEach((listener) => listener());
  };
  const read = (id: string) => {
    const task = tasks.find((task) => task.id === id);
    if (!task) throw new Error("Task not found");
    return task;
  };
  const patch = (id: string, value: Partial<StudioTask>) => {
    tasks = tasks.map((task) => task.id === id ? { ...task, ...value, id: task.id, projectId: task.projectId } : task);
    publish();
  };
  const handles = new Map<string, ReturnType<typeof makeHandle>>();
  function makeHandle(id: string) {
    const transcript = createTranscript(read(id).entries, (entries) => {
      const current = read(id);
      const firstWords = entries.find((entry) => entry.kind === "you");
      patch(id, { entries, updatedAt: Date.now(),
        ...(!current.renamed && firstWords?.kind === "you" ? { title: firstWords.text.replace(/\s+/g, " ").slice(0, 80) } : {}) });
    });
    return {
      id, transcript,
      getSnapshot: () => read(id),
      saveView(view: TaskView, pending: TaskPending | null) {
        const current = read(id);
        if (JSON.stringify(current.view) === JSON.stringify(view) && JSON.stringify(current.pending) === JSON.stringify(pending)) return;
        patch(id, { view, pending });
      },
      setBusy(value: boolean) { if (busy.get(id) === value) return; busy.set(id, value); publish(false); },
    };
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    handle(id: string) {
      if (!handles.has(id)) handles.set(id, makeHandle(id));
      return handles.get(id)!;
    },
    create(projectId: string, title: string, base?: TaskView["base"]): string {
      const id = crypto.randomUUID();
      const time = Date.now();
      tasks = [...tasks, { id, projectId, title, renamed: false, createdAt: time, updatedAt: time,
        archived: false, entries: [], view: { ...EMPTY_VIEW, base: base ? { ...base } : { ...EMPTY_VIEW.base } }, pending: null }];
      activeTaskId = id;
      publish();
      return id;
    },
    select(id: string, projectId: string) {
      const task = read(id);
      if (task.projectId !== projectId || task.archived) throw new Error("Task belongs to another project or is archived");
      activeTaskId = id; publish();
    },
    rename(id: string, title: string) { const clean = title.trim(); if (clean) patch(id, { title: clean.slice(0, 160), renamed: true }); },
    archive(id: string, archived: boolean) {
      if (taskStatus(read(id), busy.get(id)) === "running") return false;
      if (activeTaskId === id && archived) activeTaskId = null;
      patch(id, { archived });
      return true;
    },
    status(id: string) { return taskStatus(read(id), busy.get(id)); },
  };
}

export type TaskStore = ReturnType<typeof createTaskStore>;
export type StudioTaskHandle = ReturnType<TaskStore["handle"]>;
