/**
 * Following the project's Working Head (#271).
 *
 * The Project Runtime resolves the head from retained facts
 * (`GET /api/working-source`); nothing here guesses a newest candidate. These
 * rules only decide when a mounted workspace may follow the head without
 * taking work away from the person using it, and what puts a followed head
 * back (#575).
 */

import type { StudioClient } from "../api/project-runtime/client";
import { asStudioApiError, StudioApiError } from "../api/project-runtime/error";
import type { WorkingDraftDto, WorkingSourceDto } from "../api/project-runtime/generated";
import { continueUndo, undoRequest, type ContinueUndo } from "../features/designTree/continueUndo";

export interface HeadRef {
  readonly runId: string;
  readonly stateDigest: string;
  /** The head run first, then each exact retained source it continued. */
  readonly lineage: readonly string[];
}

export function headOf(source: WorkingSourceDto | null | undefined): HeadRef | null {
  const head = source?.head;
  return head ? { runId: head.runId, stateDigest: head.stateDigest, lineage: head.lineage } : null;
}

export interface FollowGate {
  /** The run the session is actually editing from. */
  readonly baseRunId: string | null;
  readonly head: HeadRef | null;
  /** A proposal, candidate, sync, Stage action or base change of this tab is in flight. */
  readonly busy: boolean;
  /** Unsynced local commands belong to the current base. */
  readonly localEdits: boolean;
}

export type FollowStep = "stay" | "follow" | "defer";

export function followStep(gate: FollowGate): FollowStep {
  if (gate.head === null || gate.baseRunId === gate.head.runId) return "stay";
  if (gate.busy || gate.localEdits) return "defer";
  return "follow";
}

/**
 * A host pin names a candidate. A delivered or restored result follows the head
 * through the same gate as any follow; an explicit open is a view-only
 * comparison, even of one of the head's own ancestors.
 */
export function pinStep(followsHead: boolean, gate: FollowGate): FollowStep | "view" {
  return followsHead ? followStep(gate) : "view";
}

/** The viewer moves with the base only when it was showing that base, or nothing yet. */
export function viewerFollows(viewedRunId: string | null, previousBaseRunId: string | null): boolean {
  return viewedRunId === null || viewedRunId === previousBaseRunId;
}

/**
 * The 撤销 a followed head offers (#575): Continue's own Undo (continueUndo.ts), onto the base this tab
 * followed from, exactly as the working position it last read named that base: a run and its line. A base
 * no entry named, because a line's accepted Stage or the reference run answered for it, or one that
 * position had already left, offers none: putting it back would be a guess.
 */
export function followUndo(read: WorkingDraftDto | null | undefined, baseRunId: string | null, followed: string): ContinueUndo | null {
  return read && baseRunId !== null && read.current?.runId === baseRunId ? continueUndo(read, followed) : null;
}

/** A move of the Working Head the project's own Design Tree made: a Continue or its Undo, confirmed by the tree's toast. */
export interface TreeHeadMove {
  readonly runId: string;
  readonly id: number;
}

/**
 * Whether following the head onto `head` follows the Design Tree's own move (#575). The tree's toast already
 * confirms that write with its 撤销, so the follow says nothing in the project bar: one write offers one 撤销. A move
 * counts once (`followed` is the id of the last one followed that way), and only onto the run the tree moved Current
 * to; a move made anywhere else is followed with its own notice.
 */
export function followsTreeMove(move: TreeHeadMove | null | undefined, followed: number | null, head: string): boolean {
  return move != null && move.id !== followed && move.runId === head;
}

/** What 撤销 did: put the base back, or nothing, because Current moved on or holds unrecorded model edits. */
export type FollowUndoOutcome = "undone" | "moved-on" | "unsynced";

/**
 * 撤销 itself: the Design Tree's Undo write (`undoRequest`), against the working position as it is read
 * now. It holds only while Current still stands on the followed run, and unrecorded model edits keep their
 * own source, so it waits for them. A write that lost a race with an autosave reads again, up to three times.
 */
export async function undoFollow(studio: Pick<StudioClient, "workingDraft" | "selectWorkingDraft">, projectId: string,
  undo: ContinueUndo): Promise<FollowUndoOutcome> {
  for (let attempt = 1; ; attempt += 1) {
    const position = await studio.workingDraft();
    if (position.projectId !== projectId) {
      throw new StudioApiError({ status: 0, code: "EDITING_PROJECT_CHANGED",
        detail: "The working position belongs to another project. Reconnect this project's runtime before undoing." });
    }
    if (position.localDraft) return "unsynced";
    const request = undoRequest(projectId, undo, position);
    if (request === null) return "moved-on";
    try {
      await studio.selectWorkingDraft(request);
      return "undone";
    } catch (cause) {
      // An autosave moved the position between the read and this write.
      if (asStudioApiError(cause).code !== "WORKING_DRAFT_STALE" || attempt >= 3) throw cause;
    }
  }
}
