/** The arithmetic a drawing action does before anything is sent. */

import assert from "node:assert/strict";
import test from "node:test";

import {
  IDLE,
  cancelled,
  circleOf,
  enclosesArea,
  finished,
  rectangleOf,
  lockedPoint,
  pointFromPlane,
  pointToPlane,
  sizedRectangle,
  snapPoint,
  typedNumber,
  typedDimensions,
  WORK_PLANES,
  type SketchState,
} from "../src/features/stage/sketch.ts";

test("two corners make a rectangle in order", () => {
  assert.deepEqual(rectangleOf([1, 2], [4, 6]), [[1, 2], [4, 2], [4, 6], [1, 6]]);
  // Drawn the other way, it is the same four corners, still in order.
  assert.deepEqual(rectangleOf([4, 6], [1, 2]), [[4, 6], [1, 6], [1, 2], [4, 2]]);
});

test("circles close into a usable extrusion profile with the requested radius", () => {
  const circle = circleOf([4, -2], 3);
  assert.equal(circle.length, 32);
  assert.equal(enclosesArea(circle), true);
  for (const point of circle) assert.ok(Math.abs(Math.hypot(point[0] - 4, point[1] + 2) - 3) < 1e-9);
  assert.deepEqual(circleOf([0, 0], 0), []);
});

test("typed rectangles support independent dimensions and deliberate local axis locks", () => {
  assert.deepEqual(typedDimensions("4, 2"), [4, 2]);
  assert.deepEqual(typedDimensions("4x2"), [4, 2]);
  assert.deepEqual(typedDimensions("4"), [4, 4]);
  assert.equal(typedDimensions("4,0"), null);
  assert.deepEqual(sizedRectangle([0, 0], [1, 1], 4, 2), [[0, 0], [4, 0], [4, 2], [0, 2]]);
  assert.deepEqual(lockedPoint([4, 3], [1, 2], "x"), [4, 2]);
  assert.deepEqual(lockedPoint([4, 3], [1, 2], "y"), [1, 3]);
});

test("elevation and selected drawing frames retain their actual world positions", () => {
  assert.deepEqual(pointFromPlane([2, 3], WORK_PLANES.xz), [2, 0, 3]);
  assert.deepEqual(pointToPlane([2, 0, 3], WORK_PLANES.xz), [2, 3]);
  assert.deepEqual(pointFromPlane([2, 3], WORK_PLANES.yz), [0, 2, 3]);
  const elevated = { ...WORK_PLANES.xy, origin: [4, 5, 6] as const };
  assert.deepEqual(pointFromPlane([2, 3], elevated), [6, 8, 6]);
  assert.deepEqual(pointToPlane([6, 8, 6], elevated), [2, 3]);
});

test("a closed polygon submits a real face only on explicit confirmation, or a signed extrusion", () => {
  const drawing: SketchState = { ...IDLE, tool: "polygon", phase: "height", plane: WORK_PLANES.xz,
    profile: [[0, 0], [3, 0], [2, 2]], height: 0 };
  assert.equal(finished(drawing), null, "a pointer click at zero height does not submit by accident");
  assert.equal(finished(drawing, true)?.height, 0);
  assert.deepEqual(finished(drawing, true)?.plane, WORK_PLANES.xz);
  assert.equal(finished({ ...drawing, height: -2 })?.height, -2);
  assert.equal(cancelled(drawing).plane, WORK_PLANES.xz, "Esc keeps the selected drawing plane");
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
