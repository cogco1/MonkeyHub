import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

import type { WorkingDraftDto } from "../src/api/project-runtime/generated";

type WorkingHead = typeof import("../src/app/workingHead.ts");
type Errors = typeof import("../src/api/project-runtime/error.ts");

// workingHead imports the Design Tree's Undo, so it is loaded the way the app resolves its modules.
async function harness(t: TestContext) {
  const vite = await createServer({
    root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null },
  });
  t.after(() => vite.close());
  return {
    ...await vite.ssrLoadModule("/src/app/workingHead.ts") as WorkingHead,
    ...await vite.ssrLoadModule("/src/api/project-runtime/error.ts") as Errors,
  };
}

type Api = Awaited<ReturnType<typeof harness>>;
type HeadRef = import("../src/app/workingHead.ts").HeadRef;

const head: HeadRef = { runId: "cand-3", stateDigest: "d3", lineage: ["cand-3", "cand-2", "stage-run"] };
const project = "riverside-library";

/**
 * One project's working position as the runtime keeps it: Current, compare-and-swapped on its revision, and the
 * Runtime's Undo over it. `move` is another writer (the Agent's Continue, another window); `autosave` moves only the
 * revision. `calls` keeps every Undo asked for, `writes` only those the runtime wrote.
 */
function runtimePosition(api: Api, current: { runId: string; branchId: string | null } | null) {
  let revision = 1;
  let entry = current && { ...current, updatedAt: "2026-10-01T00:00:00Z" };
  let localDraft: WorkingDraftDto["localDraft"] = null;
  let raceOnce = false;
  type UndoBody = { projectId: string; previous: { runId: string; branchId: string | null }; continued: string };
  const writes: UndoBody[] = [], calls: UndoBody[] = [];
  const read = (): WorkingDraftDto => ({ projectId: project, revisionSha256: `rev-${revision}`, current: entry, localDraft });
  const move = (runId: string, branchId: string | null = null) => {
    entry = { runId, branchId, updatedAt: "2026-10-01T00:01:00Z" };
    revision += 1;
  };
  return {
    writes,
    calls,
    get head() { return entry?.runId ?? null; },
    move,
    autosave() { revision += 1; },
    /** The next write loses a race with an autosave made between its read and itself. */
    raceNextWrite() { raceOnce = true; },
    holdLocalDraft() {
      localDraft = { source: { projectId: project, stateDigest: "d", sourceRunId: entry?.runId ?? null }, commands: [], updatedAt: "2026-10-01T00:02:00Z" };
    },
    async workingDraft() { return read(); },
    /** POST /api/working-design/undo, as the Runtime answers it. */
    async undoWorkingDesign(body: UndoBody) {
      calls.push(body);
      const current = entry?.runId ?? null;
      if (current === body.previous.runId) return read();
      if (current !== body.continued) {
        throw new api.StudioApiError({ status: 409, code: "DESIGN_TREE_UNDO_MOVED", detail: "The working design moved on since that switch." });
      }
      if (localDraft) throw new api.StudioApiError({ status: 409, code: "DESIGN_TREE_UNSYNCED_EDITS", detail: "Unrecorded Modeling edits." });
      // An autosave between the Runtime's read and its write loses the compare-and-swap.
      if (raceOnce) {
        raceOnce = false; revision += 1;
        throw new api.StudioApiError({ status: 409, code: "WORKING_DRAFT_STALE", detail: "The working position changed." });
      }
      move(body.previous.runId, body.previous.branchId);
      writes.push(body);
      return read();
    },
  };
}

test("headOf reads only the resolved head", async (t) => {
  const { headOf } = await harness(t);
  assert.equal(headOf(null), null);
  assert.equal(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: false, head: null, warnings: [] }), null);
  assert.deepEqual(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: true, warnings: [],
    head: { runId: "cand-3", stateDigest: "d3", recordDigest: "r3", accepted: false, origin: "working-position", lineage: head.lineage } }), head);
});

test("a delivered pin follows the head through the same gate; an explicit open stays a comparison", async (t) => {
  const { pinStep } = await harness(t);
  const idle = { baseRunId: "cand-2", head, busy: false, localEdits: false };
  assert.equal(pinStep(true, idle), "follow");
  assert.equal(pinStep(true, { ...idle, baseRunId: "cand-3" }), "stay");
  assert.equal(pinStep(true, { ...idle, localEdits: true }), "defer", "unsynced local edits keep their base and view");
  assert.equal(pinStep(true, { ...idle, busy: true }), "defer");
  assert.equal(pinStep(false, idle), "view", "opening an earlier turn shows that turn, even on the head's own line");
});

test("the base follows the head unless this tab is working or holds unsynced edits", async (t) => {
  const { followStep } = await harness(t);
  const idle = { baseRunId: "cand-2", head, busy: false, localEdits: false };
  assert.equal(followStep(idle), "follow");
  assert.equal(followStep({ ...idle, baseRunId: "cand-3" }), "stay");
  assert.equal(followStep({ ...idle, head: null }), "stay");
  assert.equal(followStep({ ...idle, busy: true }), "defer");
  assert.equal(followStep({ ...idle, localEdits: true }), "defer");
  assert.equal(followStep({ ...idle, baseRunId: null }), "follow");
});

test("the viewer follows only when it was showing the base", async (t) => {
  const { viewerFollows } = await harness(t);
  assert.equal(viewerFollows(null, "cand-2"), true);
  assert.equal(viewerFollows("cand-2", "cand-2"), true);
  assert.equal(viewerFollows("history-run", "cand-2"), false, "an explicitly opened history view is kept");
});

test("#575 撤销 on a followed head is the Runtime's Undo: it moves the head back onto the base and its line", async (t) => {
  const api = await harness(t);
  // This tab edits from cand-2 on main, as the position it read names it; the Agent then continues to cand-3.
  const runtime = runtimePosition(api, { runId: "cand-2", branchId: "main" });
  const read = await runtime.workingDraft();
  runtime.move("cand-3");
  const undo = api.followUndo(read, "cand-2", "cand-3");
  assert.deepEqual(undo, { runId: "cand-2", branchId: "main", continued: "cand-3" });
  // An Undo that lost a race with an autosave is asked again, not given up.
  runtime.autosave();
  runtime.raceNextWrite();
  assert.equal(await api.undoFollow(runtime, project, undo!), "undone");
  assert.equal(runtime.head, "cand-2", "the head is back on the base the follow came from");
  assert.deepEqual(runtime.writes, [{ projectId: project, previous: { runId: "cand-2", branchId: "main" }, continued: "cand-3" }],
    "the same Undo as the Design Tree's: the base's entry, while Current stands on the followed run");
  assert.equal(runtime.calls.length, 2, "the race was asked again once");
});

test("#575 no 撤销 when nothing names the previous position, and none once Current moved on", async (t) => {
  const api = await harness(t);
  // A line's accepted Stage answered for the base: no position entry names it, so there is nothing to put back.
  const stageHead = runtimePosition(api, null);
  assert.equal(api.followUndo(await stageHead.workingDraft(), "stage-run", "cand-3"), null);
  // The position this tab read had already left its base, or named the followed run itself: putting it back would be a guess.
  const left = runtimePosition(api, { runId: "cand-1", branchId: "main" });
  assert.equal(api.followUndo(await left.workingDraft(), "cand-2", "cand-3"), null);
  const same = runtimePosition(api, { runId: "cand-3", branchId: null });
  assert.equal(api.followUndo(await same.workingDraft(), "cand-3", "cand-3"), null);
  assert.equal(api.followUndo(null, "cand-2", "cand-3"), null);
  assert.equal(api.followUndo(await left.workingDraft(), null, "cand-3"), null);

  // Offered, then Current moved on before 撤销: nothing is written.
  const moved = runtimePosition(api, { runId: "cand-2", branchId: null });
  const read = await moved.workingDraft();
  moved.move("cand-3");
  const undo = api.followUndo(read, "cand-2", "cand-3")!;
  moved.move("cand-4");
  assert.equal(await api.undoFollow(moved, project, undo), "moved-on");
  assert.deepEqual([moved.head, moved.writes], ["cand-4", []]);
  // Unrecorded model edits keep their own source: 撤销 waits for them and writes nothing.
  const held = runtimePosition(api, { runId: "cand-2", branchId: null });
  const before = await held.workingDraft();
  held.move("cand-3");
  held.holdLocalDraft();
  assert.equal(await api.undoFollow(held, project, api.followUndo(before, "cand-2", "cand-3")!), "unsynced");
  assert.deepEqual([held.head, held.writes], ["cand-3", []]);
  // Another project's position is refused, not written.
  await assert.rejects(api.undoFollow(held, "another-project", undo), (error: { code?: string }) => error.code === "EDITING_PROJECT_CHANGED");
});

test("#575 a Continue from the Design Tree is followed without a second 撤销: its toast already offers the one that write has", async (t) => {
  const { followsTreeMove } = await harness(t);
  const move = { runId: "cand-3", id: 7 };
  // Modeling follows the tree's own move onto cand-3: the tree's toast confirms it, so the follow says nothing.
  assert.equal(followsTreeMove(move, null, "cand-3"), true);
  // That move is followed once; the same head reached again later (another window, the Agent) gets its own notice.
  assert.equal(followsTreeMove(move, 7, "cand-3"), false);
  // A head the tree did not move Current onto is someone else's move, followed with its notice and 撤销.
  assert.equal(followsTreeMove(move, null, "cand-4"), false);
  assert.equal(followsTreeMove(null, null, "cand-3"), false);
  assert.equal(followsTreeMove(undefined, null, "cand-3"), false);
  // The tree's Undo is its own move too: back onto the base, confirmed by the same toast.
  assert.equal(followsTreeMove({ runId: "cand-2", id: 8 }, 7, "cand-2"), true);
});
