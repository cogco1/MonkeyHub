/**
 * The Notes page (side panel): the architect's standing instructions and what was remembered for them.
 *
 * Two boxes of instructions, every conversation's and this project's, saved when the box loses focus; every turn
 * reads them. Below, one line per remembered thing with its remove button: the notes an Agent kept (memory_note)
 * and, while the project's Studio runs, the project's design memory (where things are, where to look first, which
 * skill a task follows). A removed note can be put back from the same line; a design memory is revoked, its
 * history kept in the project.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { useT } from "../i18n/useT";
import "./hubNotes.css";

type Note = { id: string; text: string; createdAt: string; source: "agent" | "person"; chatId?: string | null; removedAt?: string | null };
type Scope = { scope: "global" | "project"; projectDir?: string | null; instructions: string; memories: Note[]; updatedAt?: string | null };
type View = { global: Scope; project?: Scope | null };
type DesignMemory = {
  projectId: string; memoryId: string; revisionRef: string; status: string; kind: "locator" | "source_policy" | "recipe";
  value: { label?: string; topic?: string; prefer?: string[]; avoid?: string[]; task?: string; skill?: string };
};
type Row = { key: string; text: string; scope: "all" | "project" | "design"; removed: boolean; remove(): Promise<void>; restore?(): Promise<void> };

async function call<T>(url: string, body?: unknown, method = body === undefined ? "GET" : "POST"): Promise<T> {
  const response = await fetch(url, { method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : `HTTP ${response.status}`);
  return value as T;
}

export interface NotesPanelProps {
  projectDir: string | null;
  projectName: string | null;
  /** The open project's runtime, whose Studio holds its design memory; null while it is not running. */
  runtimeId: string | null;
  /** A plain folder a conversation works in: its own box is the folder's, named as such. */
  folder?: boolean;
}

export function NotesPanel({ projectDir, projectName, runtimeId, folder = false }: NotesPanelProps) {
  const t = useT();
  const [view, setView] = useState<View | null>(null);
  const [design, setDesign] = useState<DesignMemory[]>([]);
  const [drafts, setDrafts] = useState<{ global: string | null; project: string | null }>({ global: null, project: null });
  const [state, setState] = useState<{ saving: boolean; saved: boolean; error: string | null }>({ saving: false, saved: false, error: null });
  // Removed on this page while it is open: those lines stay, with their undo.
  const [removedHere, setRemovedHere] = useState<ReadonlySet<string>>(new Set());
  const reading = useRef(0);

  const read = useCallback(async () => {
    const ticket = ++reading.current;
    try {
      const next = await call<View>(`/api/notes${projectDir ? `?${new URLSearchParams({ projectDir })}` : ""}`);
      if (ticket === reading.current) setView(next);
    } catch (cause) {
      if (ticket === reading.current) setState((value) => ({ ...value, error: cause instanceof Error ? cause.message : String(cause) }));
    }
    if (!runtimeId) { setDesign([]); return; }
    try {
      const answer = await call<{ memory: DesignMemory[] }>(`/api/runtime/projects/${encodeURIComponent(runtimeId)}/studio/api/memory`);
      if (ticket === reading.current) setDesign(answer.memory.filter((row) => row.status === "active"));
    } catch { /* No Studio answer: the design memory is not listed, rather than listed stale. */ }
  }, [projectDir, runtimeId]);

  useEffect(() => {
    setDrafts({ global: null, project: null });
    setRemovedHere(new Set());
    void read();
    // An Agent keeps notes while the page is open; they appear within seconds.
    const timer = window.setInterval(() => void read(), 15_000);
    return () => window.clearInterval(timer);
  }, [read]);

  const save = async (which: "global" | "project") => {
    const text = drafts[which];
    const saved = which === "global" ? view?.global.instructions : view?.project?.instructions;
    if (text === null || text.trim() === (saved ?? "").trim()) return;
    setState({ saving: true, saved: false, error: null });
    try {
      await call<Scope>("/api/notes/instructions", { projectDir: which === "project" ? projectDir : null, text }, "PUT");
      setDrafts((value) => ({ ...value, [which]: null }));
      setState({ saving: false, saved: true, error: null });
      await read();
    } catch (cause) {
      setState({ saving: false, saved: false, error: cause instanceof Error ? cause.message : String(cause) });
    }
  };

  const noteRows = (scope: Scope | null | undefined, kind: "all" | "project"): Row[] => (scope?.memories ?? [])
    .filter((note) => !note.removedAt || removedHere.has(note.id))
    .map((note) => {
      const body = { projectDir: kind === "project" ? projectDir : null };
      return {
        key: note.id, text: note.text, scope: kind, removed: Boolean(note.removedAt),
        remove: async () => { await call(`/api/notes/memories/${note.id}/remove`, body); setRemovedHere((value) => new Set(value).add(note.id)); },
        restore: async () => { await call(`/api/notes/memories/${note.id}/restore`, body); },
      };
    });
  const designText = (row: DesignMemory) => row.kind === "locator" ? t("notes.locator", { label: row.value.label ?? "" })
    : row.kind === "recipe" ? t("notes.recipe", { task: row.value.task ?? "", skill: row.value.skill ?? "" })
    : t("notes.policy", { topic: row.value.topic ?? "", prefer: (row.value.prefer ?? []).join(" / ") || "—" })
      + (row.value.avoid?.length ? t("notes.policyAvoid", { avoid: row.value.avoid.join(" / ") }) : "");
  const rows: Row[] = [
    ...noteRows(view?.project, "project"), ...noteRows(view?.global, "all"),
    ...design.map((row): Row => ({
      key: row.memoryId, text: designText(row), scope: "design", removed: false,
      remove: async () => {
        await call(`/api/runtime/projects/${encodeURIComponent(runtimeId ?? "")}/studio/api/memory/${encodeURIComponent(row.memoryId)}/revisions`,
          { projectId: row.projectId, expectedRevisionRef: row.revisionRef, action: "revoke", reason: "Removed on the Hub's Notes page." });
      },
    })),
  ];
  const act = async (run: () => Promise<void>) => {
    try { await run(); setState((value) => ({ ...value, error: null })); } catch (cause) {
      setState((value) => ({ ...value, error: cause instanceof Error ? cause.message : String(cause) }));
    }
    await read();
  };

  const box = (which: "global" | "project", label: string, placeholder: string) => {
    const saved = which === "global" ? view?.global.instructions ?? "" : view?.project?.instructions ?? "";
    return <label className="hub-notes__box">
      <span className="hub-notes__label">{label}</span>
      <textarea rows={Math.min(12, Math.max(3, (drafts[which] ?? saved).split("\n").length + 1))} value={drafts[which] ?? saved}
        placeholder={placeholder} disabled={!view} onChange={(event) => setDrafts((value) => ({ ...value, [which]: event.target.value }))}
        onBlur={() => void save(which)} />
    </label>;
  };

  return <div className="hub-notes">
    <section className="hub-notes__section" aria-labelledby="hub-notes-instructions">
      <h3 id="hub-notes-instructions">{t("notes.instructions")}<small>{state.saving ? t("notes.saving") : state.saved ? t("notes.saved") : t("notes.instructionsHint")}</small></h3>
      {box("global", t("notes.allProjects"), t("notes.placeholderAll"))}
      {projectDir && box("project", t(folder ? "notes.thisFolder" : "notes.thisProject", { name: projectName ?? "" }),
        t(folder ? "notes.placeholderFolder" : "notes.placeholderProject"))}
    </section>
    <section className="hub-notes__section" aria-labelledby="hub-notes-remembered">
      <h3 id="hub-notes-remembered">{t("notes.remembered")}</h3>
      {rows.length === 0 ? <p className="hub-notes__empty">{t("notes.rememberedEmpty")}</p>
        : <ul className="hub-notes__list">{rows.map((row) => <li key={row.key} data-removed={row.removed || undefined}>
          <span className="hub-notes__text">{row.text}</span>
          <span className="hub-notes__scope">{row.removed ? t("notes.removed") : row.scope === "all" ? t("notes.scopeAll") : row.scope === "project" ? t("notes.scopeProject") : t("notes.scopeDesign")}</span>
          {row.removed && row.restore
            ? <button type="button" className="hub-notes__action" data-command="chat.note.restore" data-command-arg={row.key}
              onClick={() => void act(async () => { await row.restore!(); setRemovedHere((value) => { const next = new Set(value); next.delete(row.key); return next; }); })}>{t("notes.restore")}</button>
            : <button type="button" className="hub-notes__action" data-command="chat.note.remove" data-command-arg={row.key}
              aria-label={`${t("notes.remove")}: ${row.text}`} title={t("notes.remove")} onClick={() => void act(row.remove)}>×</button>}
        </li>)}</ul>}
    </section>
    {state.error && <p className="hub-notes__error" role="alert">{state.error}</p>}
  </div>;
}
