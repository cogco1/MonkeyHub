import assert from "node:assert/strict";
import test from "node:test";
import { Box3, OrthographicCamera, PerspectiveCamera, Vector3 } from "three";
import {
  configureOrthographicAspect,
  fitOrthographicBox,
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
