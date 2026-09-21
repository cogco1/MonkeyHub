import assert from "node:assert/strict";
import test from "node:test";
import { PerspectiveCamera, OrthographicCamera, Vector3 } from "three";
import { captureRenderCamera } from "../src/workspaces/render/renderCamera.ts";

test("perspective snapshot preserves zoom-adjusted projection and is independent of later camera edits", () => {
  const camera = new PerspectiveCamera(60, 1.5, .2, 1200);
  camera.position.set(2, -5, 3); camera.up.set(0, 0, 1); camera.zoom = 2;
  camera.updateProjectionMatrix();
  const target = new Vector3(0, 0, 2);
  const saved = captureRenderCamera(camera, target, 1.5);
  const restored = new PerspectiveCamera(saved.verticalFov!, saved.aspect, saved.near, saved.far);
  for (let i = 0; i < 16; i++) assert.ok(Math.abs(camera.projectionMatrix.elements[i] - restored.projectionMatrix.elements[i]) < 1e-10);
  camera.position.set(99, 99, 99); target.set(9, 9, 9);
  assert.deepEqual(saved.position, [2, -5, 3]);
  assert.deepEqual(saved.target, [0, 0, 2]);
});

test("orthographic snapshot preserves off-center bounds and zoom without fitting a new view", () => {
  const camera = new OrthographicCamera(-2, 6, 5, -1, .1, 100);
  camera.zoom = 4; camera.updateProjectionMatrix();
  const saved = captureRenderCamera(camera, new Vector3(), 8 / 6);
  const [left, right, top, bottom] = saved.orthographicBounds!;
  const restored = new OrthographicCamera(left, right, top, bottom, saved.near, saved.far);
  for (let i = 0; i < 16; i++) assert.ok(Math.abs(camera.projectionMatrix.elements[i] - restored.projectionMatrix.elements[i]) < 1e-10);
  assert.equal(saved.verticalFov, null);
});
