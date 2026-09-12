import assert from "node:assert/strict";
import test from "node:test";

import { fitDistance } from "../src/workspaces/monkeyarch/viewer/fitCamera.ts";

/** Half of what the frame shows at that distance, across and down. */
function visible(distance: number, fovDegrees: number, aspect: number) {
  const halfHeight = distance * Math.tan((fovDegrees * Math.PI) / 360);
  return { halfHeight, halfWidth: halfHeight * aspect };
}

// A portico is wide and low; the Hub tool panel is tall and narrow.
const radius = 4.6;
const fov = 45;

test("the whole model is inside the frame at every panel shape", () => {
  for (const aspect of [2.2, 1.6, 1, 620 / 950, 0.5, 0.3]) {
    const distance = fitDistance({ radius, fovDegrees: fov, aspect });
    const frame = visible(distance, fov, aspect);
    assert.ok(frame.halfWidth >= radius, `cut off across at aspect ${aspect}: ${frame.halfWidth} < ${radius}`);
    assert.ok(frame.halfHeight >= radius, `cut off down at aspect ${aspect}: ${frame.halfHeight} < ${radius}`);
  }
});

test("a narrower panel stands the camera further back, a wider one no further than needed", () => {
  const narrow = fitDistance({ radius, fovDegrees: fov, aspect: 620 / 950 });
  const wide = fitDistance({ radius, fovDegrees: fov, aspect: 1.6 });
  assert.ok(narrow > wide, "a narrow frame needs more distance than a wide one");
  // The vertical-only fit this replaced: it is what left the sides clipped.
  const verticalOnly = (2 * radius / (2 * Math.tan((fov * Math.PI) / 360))) * 1.55;
  assert.ok(narrow > verticalOnly, "the narrow fit must exceed the vertical-only distance");
  assert.ok(wide <= verticalOnly * 1.05, "a wide frame does not push the model further away than before");
});

test("an empty or tiny model still gets a usable distance", () => {
  assert.ok(fitDistance({ radius: 0, fovDegrees: fov, aspect: 1 }) > 0);
  assert.ok(Number.isFinite(fitDistance({ radius: 3, fovDegrees: fov, aspect: 0 })));
});
