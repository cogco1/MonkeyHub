/**
 * Continue and its Undo (FN-5), through the Runtime's Switch and Undo (#300, 2-6).
 *
 * Continue is `POST /api/working-design/switch` onto an exact run, from the
 * position as read: the Runtime refuses it while Modeling holds unrecorded
 * edits, and answers with the Undo that puts back the Current it replaced, an
 * entry naming a run and the line it stood on. Undo is
 * `POST /api/working-design/undo` with that entry; it holds only while Current
 * still stands on the run the Continue moved it to. A head that no entry named,
 * because the line's accepted Stage or the reference run answered for it, is
 * not put back: returning there is another act (the default), so no Undo is
 * offered rather than a guess. The history page, the Design Tree, the project
 * bar's follow notice and the attention executor share these two rules.
 */
import type { StudioClient } from "../../api/project-runtime/client";
import { asStudioApiError, StudioApiError } from "../../api/project-runtime/client";
import type { WorkingDraftDto } from "../../api/project-runtime/generated";

/** A retained local draft belongs to its exact source; Continue waits until it is recorded or undone. */
export const DESIGN_TREE_UNSYNCED = "DESIGN_TREE_UNSYNCED_EDITS";
/** Undo found Current moved on from where its Continue left it, and put nothing back. */
export const DESIGN_TREE_UNDO_MOVED = "DESIGN_TREE_UNDO_MOVED";

/** Where a Continue puts Current: an exact run, on a named line, or (null) on the line that run was recorded on. */
export interface HeadTarget {
  readonly runId: string;
  readonly branchId: string | null;
}

/** What one Continue's Undo puts back: the previous Current, as its working-position entry named it. */
export interface ContinueUndo extends HeadTarget {
  /** The run that Continue moved Current onto; Undo holds only while Current is still there. */
  readonly continued: string;
}

/** What an Undo did: put the previous Current back, or nothing, because Current moved on or holds unrecorded edits. */
export type UndoOutcome = "undone" | "moved-on" | "unsynced";

/**
 * The Undo of a move made elsewhere (another window, an Agent): the previous Current a position named, when the
 * same Undo can put it back exactly. Pure.
 */
export function continueUndo(replaced: WorkingDraftDto, continued: string): ContinueUndo | null {
  const previous = replaced.current;
  if (!previous?.runId || previous.runId === continued) return null;
  return { runId: previous.runId, branchId: previous.branchId ?? null, continued };
}

const STALE = "WORKING_DRAFT_STALE";

function projectChanged(): StudioApiError {
  return new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED",
    detail: "The working position belongs to another project. Reconnect this project's runtime before moving it." });
}

/**
 * Continue onto `target` from the working position as read now, and its Undo. A write that lost a race with
 * another window or an autosave reads again, up to three times.
 */
export async function switchHead(studio: Pick<StudioClient, "workingDraft" | "switchWorkingDesign">, projectId: string,
  target: HeadTarget): Promise<ContinueUndo | null> {
  for (let attempt = 1; ; attempt += 1) {
    const position = await studio.workingDraft();
    if (position.projectId !== projectId) throw projectChanged();
    try {
      const answer = await studio.switchWorkingDesign({ projectId, runId: target.runId, branchId: target.branchId,
        baseRevisionSha256: position.revisionSha256 ?? null });
      const previous = answer.undo.previous;
      return previous ? { runId: previous.runId, branchId: previous.branchId ?? null, continued: answer.undo.continued } : null;
    } catch (cause) {
      if (asStudioApiError(cause).code !== STALE || attempt >= 3) throw cause;
    }
  }
}

/** One Continue's Undo. An Undo already written counts as done; a lost race reads again, up to three times. */
export async function undoSwitch(studio: Pick<StudioClient, "undoWorkingDesign">, projectId: string,
  undo: ContinueUndo): Promise<UndoOutcome> {
  for (let attempt = 1; ; attempt += 1) {
    try {
      await studio.undoWorkingDesign({ projectId, previous: { runId: undo.runId, branchId: undo.branchId }, continued: undo.continued });
      return "undone";
    } catch (cause) {
      const code = asStudioApiError(cause).code;
      if (code === DESIGN_TREE_UNDO_MOVED) return "moved-on";
      if (code === DESIGN_TREE_UNSYNCED) return "unsynced";
      if (code !== STALE || attempt >= 3) throw cause;
    }
  }
}
