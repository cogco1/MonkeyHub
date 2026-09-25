/**
 * The editing bases this tab has moved through, in order, as run ids: what
 * Undo and Redo model step through when no local draft owns them.
 *
 * Only a change of editing base is a step: an edit result the shell adopted,
 * an explicit Continue, a Stage or candidate chosen to continue from, or a
 * return to the default base. Showing another run changes the picture and
 * never the base, so it is never recorded here. This is navigation, not a
 * second copy of the design: every run named here stays in the project
 * whether or not this list can still reach it.
 */
export interface ModelHistory {
  readonly runs: readonly string[];
  readonly index: number;
}

export const EMPTY_MODEL_HISTORY: ModelHistory = { runs: [], index: -1 };

/**
 * The history once the editing base has settled on `baseRunId`.
 *
 * `navigatedTo` names the run an Undo or Redo asked for; reaching it moves the
 * position without adding a step. Any other new base is a step from here, and
 * whatever had been undone stops being reachable forwards.
 */
export function recordEditingBase(current: ModelHistory, baseRunId: string | null,
  navigatedTo: string | null = null): ModelHistory {
  if (baseRunId === null) return current;
  const known = current.runs.indexOf(baseRunId);
  if (navigatedTo === baseRunId && known !== -1) {
    return known === current.index ? current : { ...current, index: known };
  }
  if (current.runs[current.index] === baseRunId) return current;
  const kept = current.runs.slice(0, current.index + 1).filter((runId) => runId !== baseRunId);
  return { runs: [...kept, baseRunId], index: kept.length };
}

/** The editing base one Undo returns to, or null when there is none. */
export function undoTarget(history: ModelHistory): string | null {
  return history.index > 0 ? history.runs[history.index - 1] ?? null : null;
}

/** The editing base one Redo returns to, or null when there is none. */
export function redoTarget(history: ModelHistory): string | null {
  return history.index >= 0 ? history.runs[history.index + 1] ?? null : null;
}
