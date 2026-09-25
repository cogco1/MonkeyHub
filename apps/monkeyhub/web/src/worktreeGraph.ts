/**
 * The project card's reading of the Worktree Graph (#271).
 *
 * The Project Runtime derives the graph from retained project facts: the
 * Working Head, other accepted lines, running work and retained results. The
 * Hub only adds who asked for each piece of work, from its own operation
 * journal and conversations. Nothing here chooses, merges or starts work.
 */

import type { ChatSummary, OperationRecord } from "./api/generated";
import type { WorktreeGraphDto, WorktreeLineDto } from "../workspaces/src/api/generated";

export interface ProjectStatus {
  headLabel: string | null;
  accepted: boolean | null;
  /** Frozen drawings were drawn from a chosen version on purpose; they are not stale. */
  drawings: { current: number; stale: number; frozen: number };
  renders: { current: number; stale: number; running: number };
  /** Work still running or waiting in this project; interrupted work is listed, not counted. */
  background: number;
  /** Finished results that left the current line, and how many of them conflict. */
  separate: number;
  conflicts: number;
  /** Retained project facts the runtime could not read for this view. */
  unreadable: number;
}

export function projectStatus(graph: WorktreeGraphDto): ProjectStatus {
  const count = (kind: "drawing" | "render", state: string) =>
    graph.representations.filter((row) => row.kind === kind && row.state === state).length;
  const results = graph.lines.filter((line) => line.kind === "result");
  return {
    headLabel: graph.head?.label ?? null,
    accepted: graph.head ? graph.head.accepted : null,
    drawings: { current: count("drawing", "current"), stale: count("drawing", "stale"), frozen: count("drawing", "frozen") },
    renders: { current: count("render", "current"), stale: count("render", "stale"), running: count("render", "running") },
    background: graph.lines.filter((line) => line.kind === "running" && line.status !== "interrupted").length + count("render", "running"),
    separate: results.length,
    conflicts: graph.lines.filter((line) => line.reconcile === "conflict").length,
    unreadable: graph.warnings.length,
  };
}

export interface OwnerWords {
  /** A request made through the project tools without a conversation: Modeling or an external tool. */
  tools: string;
  unattributed: string;
}

/** A retained ref as a person reads it: the element, parameter or relation name. */
export const refLabel = (ref: string) => ref.replace(/^(entity|parameter|relation|element|component):/, "");

export interface WorkRow {
  key: string;
  kind: WorktreeLineDto["kind"];
  owner: string | null;
  label: string | null;
  runId: string | null;
  status: WorktreeLineDto["status"];
  relation: WorktreeLineDto["relation"];
  reconcile: WorktreeLineDto["reconcile"];
  conflicts: string[];
  writes: string[];
  detail: string | null;
}

/** Who asked for a line of work, as the Hub journal recorded the request. */
export function ownerOf(runId: string | null, operations: readonly OperationRecord[], sessions: readonly ChatSummary[],
  words: OwnerWords): string {
  const operation = runId ? operations.find((row) => row.candidateId === runId) : undefined;
  if (!operation) return words.unattributed;
  if (operation.sessionId) return sessions.find((row) => row.id === operation.sessionId)?.title || words.unattributed;
  return operation.source === "studio" ? words.tools : words.unattributed;
}

/** Rows for everything that is not the current line itself, most actionable first. */
export function workRows(graph: WorktreeGraphDto, operations: readonly OperationRecord[], sessions: readonly ChatSummary[],
  words: OwnerWords): WorkRow[] {
  const order: Record<WorktreeLineDto["kind"], number> = { running: 0, result: 1, branch: 2, head: 3 };
  return graph.lines.filter((line) => line.kind !== "head")
    .map((line) => ({
      key: line.lineId, kind: line.kind, label: line.label ?? null, runId: line.runId ?? null,
      owner: line.kind === "branch" ? null : ownerOf(line.runId ?? null, operations, sessions, words),
      status: line.status, relation: line.relation, reconcile: line.reconcile,
      conflicts: [...line.conflicts], writes: [...line.writes], detail: line.detail ?? null,
    }))
    .sort((a, b) => order[a.kind] - order[b.kind] || Number(b.reconcile === "conflict") - Number(a.reconcile === "conflict"));
}
