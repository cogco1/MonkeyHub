/**
 * Following the project's Working Head (#271).
 *
 * The Project Runtime resolves the head from retained facts
 * (`GET /api/working-source`); nothing here guesses a newest candidate. These
 * rules only decide when a mounted workspace may follow the head without
 * taking work away from the person using it.
 */

import type { WorkingSourceDto } from "../api/generated";

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
