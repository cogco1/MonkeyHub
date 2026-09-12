/** The arithmetic a drawing action does before anything is sent. */

import assert from "node:assert/strict";
import test from "node:test";

import {
  IDLE,
  cancelled,
  enclosesArea,
  finished,
  rectangleOf,
  sizedRectangle,
  snapPoint,
  typedNumber,
  type SketchState,
} from "../src/features/stage/sketch.ts";

test("two corners make a rectangle in order", () => {
  assert.deepEqual(rectangleOf([1, 2], [4, 6]), [[1, 2], [4, 2], [4, 6], [1, 6]]);
  // Drawn the other way, it is the same four corners, still in order.
  assert.deepEqual(rectangleOf([4, 6], [1, 2]), [[4, 6], [1, 6], [1, 2], [4, 2]]);
});

test("a profile that encloses nothing is not a face", () => {
  assert.equal(enclosesArea(rectangleOf([0, 0], [3, 2])), true);
  assert.equal(enclosesArea(rectangleOf([0, 0], [0, 2])), false, "a line has no area");
  assert.equal(enclosesArea([[0, 0], [1, 1]]), false, "two points are not a profile");
});

test("snapping moves the point to something real, and says which", () => {
  const endpoints = [[0, 0], [4, 0], [4, 2], [0, 2]] as const;
  const onEnd = snapPoint([3.95, 0.04], { endpoints, radius: 0.2 });
  assert.deepEqual(onEnd.point, [4, 0]);
  assert.equal(onEnd.snapped?.kind, "endpoint");

  const onMid = snapPoint([2.02, -0.01], { endpoints, radius: 0.2 });
  assert.deepEqual(onMid.point, [2, 0]);
  assert.equal(onMid.snapped?.kind, "midpoint");

  const free = snapPoint([1.5, 1.5], { endpoints, radius: 0.2 });
  assert.deepEqual(free.point, [1.5, 1.5]);
  assert.equal(free.snapped, null, "nothing near means nothing moves");
});

test("an axis lock straightens one coordinate against the action's anchor", () => {
  const locked = snapPoint([2.05, 5], { anchor: [2, 0], radius: 0.2 });
  assert.deepEqual(locked.point, [2, 5]);
  assert.equal(locked.snapped?.kind, "axis");
  const diagonal = snapPoint([5, 5], { anchor: [2, 0], radius: 0.2 });
  assert.equal(diagonal.snapped, null);
});

test("a number typed during the action sizes it exactly", () => {
  assert.equal(typedNumber(" 2.5 "), 2.5);
  assert.equal(typedNumber(""), null);
  assert.equal(typedNumber("2.5m"), null, "half a number is not a number yet");
  assert.deepEqual(sizedRectangle([1, 1], [3, 4], 2), [[1, 1], [3, 1], [3, 3], [1, 3]]);
  assert.deepEqual(sizedRectangle([1, 1], [-3, -4], 2), [[1, 1], [-1, 1], [-1, -1], [1, -1]]);
});

test("only a finished action has anything to submit, and Esc leaves nothing", () => {
  const drawing: SketchState = {
    ...IDLE, tool: "rectangle", phase: "profile", anchor: [0, 0],
    profile: rectangleOf([0, 0], [3, 2]),
  };
  assert.equal(finished(drawing), null, "an outline with no height is not an action");
  const pulled: SketchState = { ...drawing, phase: "height", height: 2.4 };
  assert.deepEqual(finished(pulled), { profile: rectangleOf([0, 0], [3, 2]), height: 2.4, base: 0 });
  assert.equal(finished({ ...pulled, height: 0 }), null);
  // Cancelling keeps the chosen tool and drops everything the action held.
  assert.deepEqual(cancelled(pulled), { ...IDLE, tool: "rectangle" });
  assert.equal(finished(cancelled(pulled)), null);
});
