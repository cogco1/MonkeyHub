import assert from "node:assert/strict";
import test from "node:test";
import { OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import { captureNormalDrag, signedAxisDistance } from "../src/workspaces/monkeyarch/viewer/normalDrag.ts";

const rect = { left: 137, top: 81, width: 960, height: 720 };
const near = (actual: number | null, expected: number) => {
  assert.notEqual(actual, null);
  assert.ok(Math.abs(actual! - expected) < 1e-8, `${actual} != ${expected}`);
};
function fixture(ortho: boolean, position = [9, -12, 8]) {
  const camera = ortho ? new OrthographicCamera(-8, 8, 6, -6, .01, 1000) : new PerspectiveCamera(38, 4 / 3, .01, 1000);
  camera.position.fromArray(position); camera.up.set(0, 0, 1); camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true); camera.updateProjectionMatrix();
  return camera;
}
for (const ortho of [false, true]) for (const position of [[9, -12, 8], [-9, 12, 8]]) {
  for (const normal of [[0, 0, 1], [0, 0, -1], [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0]] as const) {
    test(`${ortho ? "orthographic" : "perspective"} ${position}: signed face normal ${normal}`, () => {
      const camera = fixture(ortho, position), before = camera.matrixWorld.toArray();
      const drag = captureNormalDrag(camera, rect, [0, 0, 0], normal);
      assert.equal(drag.numericOnly, false);
      for (const distance of [-1.75, -.1, 0, .1, 2.25]) {
        const p = drag.project(distance)!;
        near(drag.distance(p[0] + rect.left, p[1] + rect.top), distance);
      }
      assert.deepEqual(drag.normal, normal.map(v => v + 0));
      assert.deepEqual(camera.matrixWorld.toArray(), before, "capturing/sampling may not move the camera");
      assert.equal(drag.matches(camera, rect), true);
      const { origin, positive, negative } = drag.guide;
      const p = drag.project(.1)!;
      assert.ok((p[0] - origin[0]) * (positive[0] - origin[0]) + (p[1] - origin[1]) * (positive[1] - origin[1]) > 0);
      assert.ok((positive[0] - origin[0]) * (negative[0] - origin[0]) + (positive[1] - origin[1]) * (negative[1] - origin[1]) < 0);
    });
  }
}
test("the frozen pointer mapping survives camera mutation but refuses the new view at commit", () => {
  const camera = fixture(false), drag = captureNormalDrag(camera, rect, [0, 0, 0], [0, 0, 5]);
  const p = drag.project(1)!;
  camera.position.set(-10, 20, 30); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
  near(drag.distance(p[0] + rect.left, p[1] + rect.top), 1);
  assert.equal(drag.matches(camera, rect), false);
  const second = fixture(true), frozen = captureNormalDrag(second, rect, [0, 0, 0], [1, 0, 0]);
  second.zoom = 2; second.updateProjectionMatrix();
  assert.equal(frozen.matches(second, rect), false);
  assert.equal(drag.matches(fixture(false), { ...rect, width: rect.width + 1 }), false);
  assert.equal(drag.matches(fixture(false), { ...rect, left: rect.left + 1 }), false);
});
test("end-on and nearly end-on normals expose numeric-only input, never huge pointer jumps", () => {
  for (const ortho of [false, true]) for (const x of [0, .5]) {
    const camera = fixture(ortho, [x, 0, 20]);
    const drag = captureNormalDrag(camera, rect, [0, 0, 0], [0, 0, 1]);
    assert.equal(drag.numericOnly, true);
    assert.equal(drag.distance(rect.left + 10, rect.top + 10), null);
    assert.ok(drag.project(-1), "an exact signed numeric distance is still projectable");
  }
});
test("invalid normals, zero viewports, behind-camera points and nonfinite samples refuse explicitly", () => {
  const camera = fixture(false);
  for (const normal of [[0, 0, 0], [NaN, 0, 1], [Infinity, 1, 0]] as const)
    assert.throws(() => captureNormalDrag(camera, rect, [0, 0, 0], normal), /finite/);
  assert.throws(() => captureNormalDrag(camera, { ...rect, width: 0 }, [0, 0, 0], [0, 0, 1]));
  const behind = camera.position.clone().add(camera.position.clone());
  assert.throws(() => captureNormalDrag(camera, rect, behind.toArray() as [number, number, number], [1, 0, 0]), /front/);
  const drag = captureNormalDrag(camera, rect, [0, 0, 0], [0, 0, 1]);
  assert.equal(drag.distance(NaN, 1), null); assert.equal(drag.project(Infinity), null);
  assert.equal(signedAxisDistance([0, 0, 0], [0, 0, 1], [0, 0, 10], [0, 0, -1]), null);
  assert.equal(signedAxisDistance([0, 0, 0], [1, 0, 0], [0, 10, 0], [0, 1, 0]), null);
});
