import assert from "node:assert/strict";
import test from "node:test";

import { EMPTY_MODEL_HISTORY, recordEditingBase, redoTarget, undoTarget, type ModelHistory }
  from "../src/app/modelHistory.ts";

// The shell records the editing base it settled on (the projection's reference
// run) and never the picture. These walks replay what it records for each
// kind of user action.
const opened = recordEditingBase(EMPTY_MODEL_HISTORY, "R0");

test("viewing other runs is not a model step: the unchanged base leaves the history as it was", () => {
  assert.deepEqual(opened, { runs: ["R0"], index: 0 });
  // Open C1, then C2, only looking: the editing base is still R0.
  let history: ModelHistory = opened;
  for (const _viewed of ["C1", "C2"]) history = recordEditingBase(history, "R0");
  assert.equal(history, opened, "An unchanged editing base returns the identical history");
  assert.equal(undoTarget(history), null, "Nothing the architect did can be undone");
  assert.equal(redoTarget(history), null);
  assert.equal(recordEditingBase(history, null), history, "A base with no retained run is not recorded");
});

test("an adopted edit result and an explicit Continue each add one step", () => {
  const adopted = recordEditingBase(opened, "R1");
  assert.deepEqual(adopted, { runs: ["R0", "R1"], index: 1 });
  const continued = recordEditingBase(adopted, "C2");
  assert.deepEqual(continued, { runs: ["R0", "R1", "C2"], index: 2 });
  assert.equal(undoTarget(continued), "R1");
  assert.equal(redoTarget(continued), null);
});

test("Undo and Redo move the position without adding runs", () => {
  const walked = recordEditingBase(recordEditingBase(opened, "R1"), "C2");
  const undone = recordEditingBase(walked, "R1", "R1");
  assert.deepEqual(undone, { runs: ["R0", "R1", "C2"], index: 1 });
  assert.equal(undoTarget(undone), "R0");
  assert.equal(redoTarget(undone), "C2");
  const first = recordEditingBase(undone, "R0", "R0");
  assert.deepEqual(first, { runs: ["R0", "R1", "C2"], index: 0 });
  assert.equal(undoTarget(first), null);
  const redone = recordEditingBase(first, "R1", "R1");
  assert.deepEqual(redone, { runs: ["R0", "R1", "C2"], index: 1 });
  assert.equal(recordEditingBase(redone, "R1", "R1"), redone, "Arriving where the position already is changes nothing");
});

test("a new base after Undo drops what was undone, and a revisited run moves to the end", () => {
  const undone = recordEditingBase(recordEditingBase(recordEditingBase(opened, "R1"), "C2"), "R1", "R1");
  const branched = recordEditingBase(undone, "R3");
  assert.deepEqual(branched, { runs: ["R0", "R1", "R3"], index: 2 });
  assert.equal(redoTarget(branched), null, "The undone C2 is no longer reachable forwards");
  // Continuing from R0 again, rather than undoing to it, is a new step.
  const revisited = recordEditingBase(branched, "R0");
  assert.deepEqual(revisited, { runs: ["R1", "R3", "R0"], index: 2 });
  assert.equal(undoTarget(revisited), "R3");
});

test("a navigation that no longer names a known run is recorded as a new step", () => {
  const stale = recordEditingBase(recordEditingBase(opened, "R1"), "R9", "R9");
  assert.deepEqual(stale, { runs: ["R0", "R1", "R9"], index: 2 });
});
