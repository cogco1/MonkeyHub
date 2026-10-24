/**
 * The history page's rows (#300, 2-6): each move of the Working Head onto a run, newest first, named the way
 * the person would recognise it. Pure: the moves, the working position and the Worktree Graph go in, rows come out.
 */
import type { ContinueMoveDto } from "../../api/project-runtime/client";
import type { LineStepDto, WorkingDraftDto, WorktreeGraphDto } from "../../api/project-runtime/generated";

/** The surface a move came through when the Hub's Agent made it on the person's words. */
export const AGENT_ORIGIN = "hub-agent";

export interface HistoryRow {
  readonly runId: string;
  /** When the head moved onto this run. */
  readonly occurredAt: string;
  /** The Agent moved it, on the person's words; otherwise the person did. */
  readonly byAgent: boolean;
  /** The name the person gave it, else what the step says it did; null when nothing names it. */
  readonly name: string | null;
  /** Only the name the person gave it, for the naming field. */
  readonly label: string | null;
  /** The accepted Stage this step stands on, when it is one. */
  readonly stageRef: string | null;
  /** The Working Head stands on this run now. */
  readonly current: boolean;
}

type Step = Pick<LineStepDto, "label" | "summary" | "request" | "stageRef">;

/** What the graph says of each run: the head's line and the steps it moved back past, then the other lines' results. */
function stepsOf(graph: WorktreeGraphDto | null): Map<string, Step> {
  const steps = new Map<string, Step>();
  if (!graph) return steps;
  for (const step of [...graph.line, ...graph.later]) if (!steps.has(step.runId)) steps.set(step.runId, step);
  for (const line of graph.lines) {
    if (line.runId && !steps.has(line.runId)) steps.set(line.runId, { label: line.label, summary: null, request: null, stageRef: null });
  }
  return steps;
}

function labelsOf(position: WorkingDraftDto | null): Map<string, string> {
  const labels = new Map<string, string>();
  for (const entry of [...(position?.saved ?? []), ...(position?.current ? [position.current] : [])]) {
    if (entry.label) labels.set(entry.runId, entry.label);
  }
  return labels;
}

const text = (value: string | null | undefined) => value?.trim() || null;

/** One row per move, newest first; a run the head came back to keeps one row per time it did. */
export function historyRows(moves: readonly ContinueMoveDto[], position: WorkingDraftDto | null,
  graph: WorktreeGraphDto | null): HistoryRow[] {
  const steps = stepsOf(graph), labels = labelsOf(position);
  const current = position?.current?.runId ?? graph?.head?.runId ?? null;
  return moves.map((move, index) => {
    const step = steps.get(move.runId);
    const label = labels.get(move.runId) ?? null;
    return {
      runId: move.runId,
      occurredAt: move.occurredAt,
      byAgent: move.origin === AGENT_ORIGIN,
      name: label ?? text(step?.label) ?? text(step?.summary) ?? text(step?.request),
      label,
      stageRef: step?.stageRef ?? null,
      // Only the newest move can be where the head stands; an older visit to the same run is history.
      current: index === 0 && move.runId === current,
    };
  });
}
