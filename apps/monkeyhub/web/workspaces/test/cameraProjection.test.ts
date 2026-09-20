import assert from "node:assert/strict";
import test from "node:test";
import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import {
  configureOrthographicAspect,
  fitOrthographicBox,
  frameBoxKeepingView,
  standardViewFrame,
  transferProjectionPose,
  visibleHalfHeight,
} from "../src/workspaces/monkeyarch/viewer/cameraProjection.ts";

const close = (actual: number, expected: number, epsilon = 1e-9) =>
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} != ${expected}`);

test("projection transfer preserves apparent vertical scale", () => {
  const target = new Vector3(0, 0, 0);
  const perspective = new PerspectiveCamera(38, 1.6, 0.01, 10000);
  perspective.position.set(8, -8, 6);
  perspective.lookAt(target);
  const before = visibleHalfHeight(perspective, target);
  const orthographic = new OrthographicCamera(-1, 1, 1, -1, 0.01, 10000);
  transferProjectionPose(perspective, orthographic, target, 1.6);
  close(visibleHalfHeight(orthographic, target), before);
  const back = new PerspectiveCamera(38, 1.6, 0.01, 10000);
  transferProjectionPose(orthographic, back, target, 1.6);
  close(visibleHalfHeight(back, target), before);
});

test("orthographic resize changes width but not vertical world scale", () => {
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.01, 1000);
  camera.zoom = 0.25;
  const target = new Vector3();
  const before = visibleHalfHeight(camera, target);
  configureOrthographicAspect(camera, 2.4);
  close(visibleHalfHeight(camera, target), before);
  close(camera.right - camera.left, 4.8);
});

test("top fit keeps equal plan lengths equal at different depths", () => {
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.01, 1000);
  const box = new Box3(new Vector3(-5, -2, 0), new Vector3(5, 2, 8));
  const frame = standardViewFrame("top");
  fitOrthographicBox(camera, box, 1.5, frame.direction, frame.up);
  const project = (p: Vector3) => p.clone().project(camera);
  const a0 = project(new Vector3(-2, 0, 0)), a1 = project(new Vector3(2, 0, 0));
  const b0 = project(new Vector3(-2, 0, 8)), b1 = project(new Vector3(2, 0, 8));
  close(a0.distanceTo(a1), b0.distanceTo(b1));
  close(camera.position.x, 0);
  close(camera.position.y, 0);
  assert.ok(camera.position.z > box.max.z);
});

test("standard view frames follow the Z-up model coordinates", () => {
  assert.deepEqual(standardViewFrame("top").direction.toArray(), [0, 0, 1]);
  assert.deepEqual(standardViewFrame("front").direction.toArray(), [0, -1, 0]);
  assert.deepEqual(standardViewFrame("right").direction.toArray(), [1, 0, 0]);
});

test("isometric fit has equal axis scale and no depth-dependent perspective", () => {
  const frame = standardViewFrame("iso");
  assert.deepEqual(frame.direction.toArray(), [1, -1, 1]);
  assert.deepEqual(frame.up.toArray(), [0, 0, 1]);
  const box = new Box3(new Vector3(-5, -2, 0), new Vector3(5, 2, 8));
  for (const aspect of [0.5, 1, 2]) {
    const camera = new OrthographicCamera(-1, 1, 1, -1, 0.01, 1000);
    const center = fitOrthographicBox(camera, box, aspect, frame.direction, frame.up);
    const screen = (point: Vector3) => {
      const projected = point.clone().project(camera);
      return [projected.x * aspect, projected.y];
    };
    const length = (start: Vector3, end: Vector3) => {
      const a = screen(start), b = screen(end);
      return Math.hypot(b[0]! - a[0]!, b[1]! - a[1]!);
    };
    const x = new Vector3(1, 0, 0), y = new Vector3(0, 1, 0), z = new Vector3(0, 0, 1);
    const scale = length(center, center.clone().add(x));
    close(length(center, center.clone().add(y)), scale);
    close(length(center, center.clone().add(z)), scale);
    const near = center.clone().add(frame.direction), far = center.clone().sub(frame.direction);
    close(length(near, near.clone().add(x)), length(far, far.clone().add(x)));
    for (const bx of [box.min.x, box.max.x]) for (const by of [box.min.y, box.max.y]) for (const bz of [box.min.z, box.max.z]) {
      const projected = new Vector3(bx, by, bz).project(camera);
      assert.ok(Math.abs(projected.x) < 1 && Math.abs(projected.y) < 1 && Math.abs(projected.z) < 1,
        `isometric bounds must fit at aspect ${aspect}`);
    }
  }
});

test("framing selected bounds preserves perspective view direction", () => {
  const camera = new PerspectiveCamera(38, 1.5, 0.01, 10000);
  const target = new Vector3(1, 2, 3);
  camera.position.set(8, -7, 11);
  camera.up.set(0, 0, 1);
  camera.lookAt(target);
  const before = camera.position.clone().sub(target).normalize();
  const box = new Box3(new Vector3(18, 4, 1), new Vector3(22, 10, 7));
  const center = frameBoxKeepingView(camera, target, box, 1.5);
  assert.deepEqual(center.toArray(), [20, 7, 4]);
  const after = camera.position.clone().sub(center).normalize();
  after.toArray().forEach((value, index) => close(value, before.toArray()[index]!));
  assert.deepEqual(camera.up.toArray(), [0, 0, 1]);
});

test("framing selected bounds reuses orthographic zoom framing", () => {
  const camera = new OrthographicCamera(-1.5, 1.5, 1, -1, 0.01, 10000);
  const target = new Vector3(0, 0, 0);
  camera.position.set(0, 0, 20);
  camera.up.set(0, 1, 0);
  camera.lookAt(target);
  const box = new Box3(new Vector3(8, -2, 3), new Vector3(12, 2, 9));
  const center = frameBoxKeepingView(camera, target, box, 1.5);
  assert.deepEqual(center.toArray(), [10, 0, 6]);
  close(camera.position.x, 10);
  close(camera.position.y, 0);
  assert.ok(camera.position.z > box.max.z);
  assert.ok(camera.zoom > 0);
});
