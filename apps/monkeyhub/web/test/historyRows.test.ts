import assert from "node:assert/strict";
import test from "node:test";

import type { ContinueMoveDto } from "../src/api/project-runtime/client.ts";
import type { WorkingDraftDto, WorktreeGraphDto } from "../src/api/project-runtime/generated/types.gen.ts";
import { AGENT_ORIGIN, historyRows } from "../src/features/designHistory/historyRows.ts";

const move = (runId: string, previousRunId: string | null, occurredAt: string, origin = "hub"): ContinueMoveDto =>
  ({ occurredAt, runId, previousRunId, actorId: "studio:explicit-user-action", origin });
const entry = (runId: string, label: string | null = null) =>
  ({ runId, label, updatedAt: "2026-10-24T08:00:00Z", sourceStageRef: null, branchId: null });
const step = (runId: string, fields: Partial<{ label: string; summary: string; request: string; stageRef: string }> = {}) =>
  ({ runId, baseRunId: null, label: null, request: null, summary: null, stageRef: null, updatedAt: null, ...fields });

const position = (current: string | null, saved: ReturnType<typeof entry>[] = []) => ({
  projectId: "p", revisionSha256: "r", current: current ? entry(current) : null, recovery: [], saved, managedRunIds: [], localDraft: null,
}) as WorkingDraftDto;
const graph = (line: ReturnType<typeof step>[], later: ReturnType<typeof step>[] = [],
  lines: { runId: string | null; label: string | null }[] = []) => ({ projectId: "p", head: null, line, later, lines }) as unknown as WorktreeGraphDto;

test("each move is a row, newest first, named by the person's name, else what the step did", () => {
  const rows = historyRows(
    [move("c", "b", "2026-10-24T10:00:00Z"), move("b", "a", "2026-10-24T09:00:00Z", AGENT_ORIGIN), move("a", null, "2026-10-24T08:00:00Z")],
    position("c", [entry("b", "Wider courtyard")]),
    graph([step("a", { summary: "First massing", stageRef: "stage:s1" }), step("b", { summary: "Courtyard 12 m" })], [step("c", { request: "lower the roof" })]));
  assert.deepEqual(rows.map((row) => [row.runId, row.name, row.current, row.byAgent, row.stageRef]), [
    ["c", "lower the roof", true, false, null],
    ["b", "Wider courtyard", false, true, null],
    ["a", "First massing", false, false, "stage:s1"],
  ]);
  assert.equal(rows[1]!.label, "Wider courtyard", "only a name the person gave fills the naming field");
  assert.equal(rows[0]!.label, null);
});

test("a run the head came back to is current only on its newest visit, and an unnamed step has no name", () => {
  const rows = historyRows([move("a", "b", "2026-10-24T11:00:00Z"), move("b", "a", "2026-10-24T10:00:00Z"), move("a", null, "2026-10-24T09:00:00Z")],
    position("a"), graph([], [], [{ runId: "b", label: "Result B" }]));
  assert.deepEqual(rows.map((row) => [row.runId, row.current, row.name]), [["a", true, null], ["b", false, "Result B"], ["a", false, null]]);
});

test("with no working position the graph's head says which row is current", () => {
  const head = { projectId: "p", head: { runId: "b" }, line: [], later: [], lines: [] } as unknown as WorktreeGraphDto;
  assert.equal(historyRows([move("b", "a", "2026-10-24T10:00:00Z")], position(null), head)[0]!.current, true);
  assert.equal(historyRows([move("b", "a", "2026-10-24T10:00:00Z")], null, null)[0]!.current, false);
});
