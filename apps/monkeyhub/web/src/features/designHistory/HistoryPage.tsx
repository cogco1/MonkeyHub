/**
 * The side panel's history page (#300, 2-6): each move of the working design, newest first, with
 * "go back to this step" (Switch) and its Undo, a name for any step, and the way back to the current
 * design. The shell owns the panel around it and mounts it only with a project open.
 */
import { useCallback, useEffect, useState } from "react";
import { useStudio } from "../../api/project-runtime/ProjectRuntimeContext";
import { asStudioApiError, type HeadEntryDto } from "../../api/project-runtime/client";
import type { WorkingDraftDto, WorktreeGraphDto } from "../../api/project-runtime/generated";
import { useCommands } from "../commands/CommandProvider";
import { DESIGN_TREE_UNDO_MOVED, DESIGN_TREE_UNSYNCED } from "../designTree/continueUndo";
import { usePreferences } from "../settings/preferences";
import { historyRows, type HistoryRow } from "./historyRows";
import { HISTORY_WORDS, type HistoryWords } from "./words";
import "./historyPage.css";

interface Read {
  readonly rows: readonly HistoryRow[];
  readonly position: WorkingDraftDto;
  readonly warnings: readonly string[];
}

/** The Undo of the last Switch this page made, and the name of where it went. */
interface PageUndo {
  readonly previous: HeadEntryDto;
  readonly continued: string;
  readonly name: string;
}

type Note = { readonly kind: "moved"; readonly undo: PageUndo | null; readonly name: string } | { readonly kind: "undone" }
  | { readonly kind: "refused"; readonly text: string };

function refusal(cause: unknown, words: HistoryWords): string {
  const code = asStudioApiError(cause).code;
  return code === DESIGN_TREE_UNSYNCED ? words.unsynced : code === DESIGN_TREE_UNDO_MOVED ? words.undoMoved : words.failed;
}

/**
 * The Stage row's own actions. Withdrawing the last accepted Stage is 6-5 (decision 7): until its Runtime route
 * lands, a Stage row offers nothing here rather than a control that cannot act.
 */
function StageRowActions(_: { readonly stageRef: string }) {
  return null;
}

export function HistoryPage({ projectDir, active }: { readonly projectDir: string; readonly active: boolean }) {
  const studio = useStudio();
  const commands = useCommands();
  const { language } = usePreferences();
  const words = HISTORY_WORDS[language];
  const [read, setRead] = useState<Read | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [naming, setNaming] = useState<{ readonly runId: string; readonly text: string } | null>(null);

  const reload = useCallback(async () => {
    const [moves, position, graph] = await Promise.all([studio.workingDesignMoves(), studio.workingDraft(),
      studio.worktrees().catch((): WorktreeGraphDto | null => null)]);
    setRead({ rows: historyRows(moves.moves, position, graph), position, warnings: moves.warnings });
  }, [studio]);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setRead(null); setNote(null); setNaming(null);
    reload().catch((cause) => { if (!cancelled) setNote({ kind: "refused", text: refusal(cause, words) }); });
    return () => { cancelled = true; };
    // A new project or reopening the page reads again; the words follow the language without a read.
  }, [active, projectDir, reload]);

  const act = useCallback(async (work: () => Promise<Note | null>) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await work();
      await reload();
      setNote(next);
    } catch (cause) {
      setNote({ kind: "refused", text: refusal(cause, words) });
      await reload().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }, [busy, reload, words]);

  const goTo = (row: HistoryRow) => act(async () => {
    // Read the position now: the page may have been open while another window moved the head.
    const position = await studio.workingDraft();
    const answer = await studio.switchWorkingDesign({ projectId: position.projectId, runId: row.runId,
      baseRevisionSha256: position.revisionSha256 ?? null });
    const name = row.name ?? words.unnamed;
    const previous = answer.undo.previous;
    return { kind: "moved", name, undo: previous ? { previous, continued: answer.undo.continued, name } : null };
  });

  const undo = (handle: PageUndo) => act(async () => {
    const position = await studio.workingDraft();
    await studio.undoWorkingDesign({ projectId: position.projectId, previous: handle.previous, continued: handle.continued });
    return { kind: "undone" };
  });

  const backToDefault = () => act(async () => {
    const position = await studio.workingDraft();
    await studio.selectWorkingDraft({ projectId: position.projectId, runId: null, baseRevisionSha256: position.revisionSha256 ?? null });
    return null;
  });

  const saveName = (runId: string, label: string) => act(async () => {
    const position = await studio.workingDraft();
    await studio.savePersonsVersion({ projectId: position.projectId, runId, label: label.trim() || null,
      baseRevisionSha256: position.revisionSha256 ?? null });
    setNaming(null);
    return null;
  });

  const time = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value
      : date.toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  return <section className="design-history" aria-busy={busy || read === null || undefined}>
    <div className="design-history__head">
      {read?.position.current && <button type="button" className="btn btn--small" data-command="design.go_to_step.default_base"
        title={words.backToDefaultHint} disabled={busy} onClick={() => void backToDefault()}>{words.backToDefault}</button>}
      <button type="button" className="btn btn--small" data-command="view.design_tree.open"
        onClick={() => void commands.run("view.design_tree.open", "button")}>{words.wholeTree}</button>
    </div>
    {note && <p className="design-history__note" role={note.kind === "refused" ? "alert" : "status"}>
      {note.kind === "moved" ? words.movedTo(note.name) : note.kind === "undone" ? words.undone : note.text}
      {note.kind === "moved" && note.undo && <button type="button" className="btn btn--link" data-command="design.undo_last"
        disabled={busy} onClick={() => void undo(note.undo!)}>{words.undo}</button>}
    </p>}
    {read === null ? <p className="design-history__empty" role="status">{words.loading}</p>
      : read.rows.length === 0 ? <p className="design-history__empty">{words.empty}</p>
      : <ol className="design-history__rows">
        {read.rows.map((row, index) => <li key={`${row.runId}:${row.occurredAt}:${index}`} className="design-history__row"
          data-current={row.current || undefined} data-stage={row.stageRef ? true : undefined}>
          <div className="design-history__line">
            <strong className="design-history__name">{row.name ?? words.unnamed}</strong>
            {row.current && <span className="design-history__badge">{words.current}</span>}
            {row.stageRef && <span className="design-history__badge design-history__badge--stage">{words.stage}</span>}
          </div>
          <p className="design-history__meta">{time(row.occurredAt)} · {row.byAgent ? words.agent : words.you}</p>
          {naming?.runId === row.runId ? <form className="design-history__naming" onSubmit={(event) => {
            event.preventDefault(); void saveName(row.runId, naming.text);
          }}>
            <input aria-label={words.nameLabel} autoFocus value={naming.text} maxLength={200}
              onChange={(event) => setNaming({ runId: row.runId, text: event.target.value })} />
            <button type="submit" className="btn btn--small" data-command="design.go_to_step.save_milestone" disabled={busy}>{words.save}</button>
            <button type="button" className="btn btn--small" data-command="view.dialog.close" onClick={() => setNaming(null)}>{words.cancel}</button>
          </form> : <div className="design-history__actions">
            {!row.current && <button type="button" className="btn btn--small" data-command="design.go_to_step.continue"
              data-command-arg={row.runId} disabled={busy} onClick={() => void goTo(row)}>{words.goTo}</button>}
            <button type="button" className="btn btn--link" data-command="design.go_to_step.save_milestone" disabled={busy}
              onClick={() => setNaming({ runId: row.runId, text: row.label ?? "" })}>{words.name}</button>
            {row.stageRef && <StageRowActions stageRef={row.stageRef} />}
          </div>}
        </li>)}
      </ol>}
    {read && read.warnings.length > 0 && <p className="design-history__empty">{words.unreadable}</p>}
  </section>;
}
