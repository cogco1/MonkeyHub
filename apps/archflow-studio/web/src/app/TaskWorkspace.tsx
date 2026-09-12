import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { studio, asStudioApiError } from "../api/client";
import { connection, type ServerIdentity } from "../api/connection";
import type { ProjectBindingDto } from "../api/generated";
import { useT } from "../i18n/useT";
import type { BoardDesignRequest } from "../workspaces/monkeyboard/boardFeedback";
import App from "./App";
import { ErrorPanel } from "./ErrorPanel";
import { createTaskStore, type StudioTask } from "./tasks";
import "./TaskWorkspace.css";

/** Independent task views on the one project actually bound to this Studio server. */
export function TaskWorkspace({ server, initialDocumentIntent }: {
  server: ServerIdentity; initialDocumentIntent?: BoardDesignRequest;
}) {
  const t = useT();
  const [store] = useState(() => {
    let storage: Storage | null = null;
    try { storage = window.localStorage; } catch { /* Session-only history. */ }
    return createTaskStore(storage, connection.baseUrl || window.location.origin);
  });
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const [project, setProject] = useState<ProjectBindingDto | null>(null);
  const [error, setError] = useState<ReturnType<typeof asStudioApiError> | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const visited = useRef(new Set<string>());
  const documentTask = useRef<string | null>(null);
  const initialized = useRef(false);
  useEffect(() => {
    let live = true;
    void studio.project().then((value) => { if (live) setProject(value); }, (cause) => { if (live) setError(asStudioApiError(cause)); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!project || initialized.current) return;
    initialized.current = true;
    const saved = store.getSnapshot();
    if (initialDocumentIntent) {
      documentTask.current = store.create(project.projectId, t("tasks.new"), {
        runId: initialDocumentIntent.modelSource.runId, sourceStageRef: initialDocumentIntent.sourceStageRef,
        stateDigest: initialDocumentIntent.modelSource.stateDigest,
      });
      return;
    }
    const selected = saved.tasks.find((task) => task.id === saved.activeTaskId && task.projectId === project.projectId && !task.archived)
      ?? saved.tasks.find((task) => task.projectId === project.projectId && !task.archived);
    if (selected) store.select(selected.id, project.projectId);
    else store.create(project.projectId, t("tasks.new"));
  }, [project, store, initialDocumentIntent, t]);

  const active = snapshot.tasks.find((task) => task.id === snapshot.activeTaskId && !task.archived && task.projectId === project?.projectId);
  if (active) visited.current.add(active.id);
  const currentTasks = snapshot.tasks.filter((task) => task.projectId === project?.projectId);
  // Resume read-only candidate polling, never replay model requests after reload.
  currentTasks.filter((task) => !task.archived && store.status(task.id) === "running").forEach((task) => visited.current.add(task.id));
  const newTask = () => { if (project) store.create(project.projectId, t("tasks.new"), active?.view.base); };
  const row = (task: StudioTask) => <li key={task.id} className="task-row" data-active={task.id === active?.id}>
    {renaming === task.id ? <form onSubmit={(event) => { event.preventDefault(); store.rename(task.id, name); setRenaming(null); }}>
      <input aria-label={t("tasks.name")} autoFocus value={name} onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Escape") setRenaming(null); }} />
      <button type="submit">{t("tasks.save")}</button>
    </form> : <>
      <button type="button" className="task-row__select" aria-current={task.id === active?.id ? "page" : undefined}
        disabled={task.archived} onClick={() => store.select(task.id, project!.projectId)} title={task.title}>
        <span className="task-row__title">{task.title}</span>
        <span className="task-row__status">{t(`tasks.status.${store.status(task.id)}`)}</span>
      </button>
      <details className="task-row__menu"><summary aria-label={`${t("tasks.actions")}: ${task.title}`}>···</summary>
        <div>
          <button type="button" onClick={() => { setName(task.title); setRenaming(task.id); }}>{t("tasks.rename")}</button>
          <button type="button" disabled={store.status(task.id) === "running"} onClick={() => {
            if (!store.archive(task.id, !task.archived)) return;
            if (task.archived) store.select(task.id, project!.projectId);
            else if (task.id === active?.id) {
              const next = currentTasks.find((item) => item.id !== task.id && !item.archived);
              if (next) store.select(next.id, project!.projectId);
            }
          }}>{t(task.archived ? "tasks.restore" : "tasks.archive")}</button>
        </div>
      </details>
    </>}
  </li>;
  return <div className="task-workspace" data-collapsed={collapsed}>
    <nav className="task-sidebar" aria-label={t("tasks.navigation")}>
      <button type="button" className="task-sidebar__toggle" aria-expanded={!collapsed} aria-controls="task-sidebar-content"
        onClick={() => setCollapsed(!collapsed)}>{collapsed ? "☰" : t("tasks.navigation")}</button>
      {!collapsed && <div id="task-sidebar-content" className="task-sidebar__content">
        <button type="button" className="btn task-sidebar__new" disabled={!project} onClick={newTask}>+ {t("tasks.new")}</button>
        <p className="task-sidebar__project" title={project?.projectId}>{project?.projectId ?? t("shell.readingBinding")}</p>
        <p className="quiet">{project ? `${project.intentProvider}${project.intentModel ? ` · ${project.intentModel}` : ""}` : ""}</p>
        <ul className="task-list">{currentTasks.filter((task) => !task.archived).map(row)}</ul>
        {currentTasks.some((task) => task.archived) && <details className="task-archive"><summary>{t("tasks.archived")}</summary>
          <ul className="task-list">{currentTasks.filter((task) => task.archived).map(row)}</ul></details>}
        {snapshot.tasks.some((task) => task.projectId !== project?.projectId) && <p className="quiet">{t("tasks.otherProjects")}</p>}
        <p className="quiet task-sidebar__note">{t("tasks.localHistory")}</p>
        {snapshot.storageError && <p role="alert">{t("tasks.storageError")}</p>}
      </div>}
    </nav>
    <div className="task-workspace__views">
      {error && <ErrorPanel error={error} what={t("shell.readingBinding")} />}
      {!active && project && <div className="task-workspace__empty"><button type="button" className="btn" onClick={newTask}>{t("tasks.new")}</button></div>}
      {currentTasks.filter((task) => visited.current.has(task.id)).map((task) => <div key={task.id}
        data-studio-task={task.id} className="task-workspace__view" hidden={task.id !== active?.id} inert={task.id !== active?.id}>
        <App server={server} task={store.handle(task.id)} active={task.id === active?.id}
          initialDocumentIntent={task.id === documentTask.current ? initialDocumentIntent : undefined} />
      </div>)}
    </div>
  </div>;
}
