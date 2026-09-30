/**
 * Continue and its Undo, as the requests they are (FN-5).
 *
 * Continue is the existing working-position write, `PUT /api/working-draft`
 * onto an exact run, compare-and-swapped against the position it read. The
 * position it writes over names the previous Current: its own entry, a run and
 * the line it stood on. Undo is the same write onto that entry, and it holds
 * only while Current still stands on the run the Continue moved it to. A head
 * that no entry named, because the line's accepted Stage or the reference run
 * answered for it, is not put back: returning there is another act (the
 * default, `runId: null`), so no Undo is offered rather than a guess.
 * Pure, with no copy.
 */
import type { WorkingDraftDto, WorkingDraftSelectionDto } from "../../api/generated";

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

/** The Continue write onto `target`, against the working position as it was read. */
export function continueRequest(projectId: string, target: HeadTarget, position: WorkingDraftDto): WorkingDraftSelectionDto {
  return { projectId, runId: target.runId, baseRevisionSha256: position.revisionSha256 ?? null, branchId: target.branchId };
}

/** The previous Current that a Continue onto `continued` wrote over, when the same write can put it back exactly. */
export function continueUndo(replaced: WorkingDraftDto, continued: string): ContinueUndo | null {
  const previous = replaced.current;
  if (!previous?.runId || previous.runId === continued) return null;
  return { runId: previous.runId, branchId: previous.branchId ?? null, continued };
}

/** Undo's write: the same Continue onto the previous Current, or null once Current has moved on from the continued run. */
export function undoRequest(projectId: string, undo: ContinueUndo, position: WorkingDraftDto): WorkingDraftSelectionDto | null {
  return position.current?.runId === undo.continued ? continueRequest(projectId, undo, position) : null;
}
