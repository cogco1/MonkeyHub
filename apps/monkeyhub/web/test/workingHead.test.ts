import assert from "node:assert/strict";
import test from "node:test";

import { followStep, headOf, pinStep, viewerFollows, type HeadRef } from "../src/app/workingHead.ts";

const head: HeadRef = { runId: "cand-3", stateDigest: "d3", lineage: ["cand-3", "cand-2", "stage-run"] };

test("headOf reads only the resolved head", () => {
  assert.equal(headOf(null), null);
  assert.equal(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: false, head: null, warnings: [] }), null);
  assert.deepEqual(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: true, warnings: [],
    head: { runId: "cand-3", stateDigest: "d3", recordDigest: "r3", accepted: false, origin: "working-position", lineage: head.lineage } }), head);
});

test("a delivered pin follows the head through the same gate; an explicit open stays a comparison", () => {
  const idle = { baseRunId: "cand-2", head, busy: false, localEdits: false };
  assert.equal(pinStep(true, idle), "follow");
  assert.equal(pinStep(true, { ...idle, baseRunId: "cand-3" }), "stay");
  assert.equal(pinStep(true, { ...idle, localEdits: true }), "defer", "unsynced local edits keep their base and view");
  assert.equal(pinStep(true, { ...idle, busy: true }), "defer");
  assert.equal(pinStep(false, idle), "view", "opening an earlier turn shows that turn, even on the head's own line");
});

test("the base follows the head unless this tab is working or holds unsynced edits", () => {
  const idle = { baseRunId: "cand-2", head, busy: false, localEdits: false };
  assert.equal(followStep(idle), "follow");
  assert.equal(followStep({ ...idle, baseRunId: "cand-3" }), "stay");
  assert.equal(followStep({ ...idle, head: null }), "stay");
  assert.equal(followStep({ ...idle, busy: true }), "defer");
  assert.equal(followStep({ ...idle, localEdits: true }), "defer");
  assert.equal(followStep({ ...idle, baseRunId: null }), "follow");
});

test("the viewer follows only when it was showing the base", () => {
  assert.equal(viewerFollows(null, "cand-2"), true);
  assert.equal(viewerFollows("cand-2", "cand-2"), true);
  assert.equal(viewerFollows("history-run", "cand-2"), false, "an explicitly opened history view is kept");
});
