import assert from "node:assert/strict";
import test from "node:test";

import { followStep, headOf, pinDisposition, viewerFollows, type HeadRef } from "../src/app/workingHead.ts";

const head: HeadRef = { runId: "cand-3", stateDigest: "d3", lineage: ["cand-3", "cand-2", "stage-run"] };

test("headOf reads only the resolved head", () => {
  assert.equal(headOf(null), null);
  assert.equal(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: false, head: null, warnings: [] }), null);
  assert.deepEqual(headOf({ projectId: "p", workspace: "modeling", policy: "live", compatible: true, warnings: [],
    head: { runId: "cand-3", stateDigest: "d3", recordDigest: "r3", accepted: false, origin: "working-position", lineage: head.lineage } }), head);
});

test("a delivery pin on the head or an ancestor follows the head; another line stays a comparison", () => {
  assert.equal(pinDisposition(null, head), "follow");
  assert.equal(pinDisposition(undefined, head), "follow");
  assert.equal(pinDisposition("cand-3", head), "follow");
  assert.equal(pinDisposition("cand-2", head), "follow", "a stale pin never overrides the current head");
  assert.equal(pinDisposition("stage-run", head), "follow");
  assert.equal(pinDisposition("cand-other", head), "view");
  assert.equal(pinDisposition("cand-3", null), "view", "without a known head a named pin is only a view");
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
