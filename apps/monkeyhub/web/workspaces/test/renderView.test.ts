import assert from "node:assert/strict";
import test from "node:test";
import { Scene, Mesh, BoxGeometry, MeshBasicMaterial, PerspectiveCamera, OrthographicCamera, Vector3 } from "three";
import { captureRenderView, previewSize } from "../src/workspaces/monkeyarch/viewer/renderView.ts";

for (const projection of ["perspective", "orthographic"]) {
  test(`${projection}: borrowed model and camera preserve the projected geometry`, () => {
    const scene = new Scene();
    const mesh = new Mesh(new BoxGeometry(), new MeshBasicMaterial());
    scene.add(mesh);
    const camera = projection === "perspective" ? new PerspectiveCamera(51, 16 / 9, 0.2, 900)
      : new OrthographicCamera(-8, 8, 4.5, -4.5, 0.2, 900);
    camera.position.set(12, -19, 7);
    camera.up.set(0, 0, 1);
    camera.zoom = 1.3;
    const target = new Vector3(2, 3, 1);
    camera.lookAt(target); camera.updateProjectionMatrix();
    const view = captureRenderView(scene, camera, target, 51, 1.05);
    assert.equal(view.scene, scene);
    assert.equal(view.scene.children[0], mesh);
    assert.equal(mesh.parent, scene);
    assert.notEqual(view.camera, camera);
    assert.equal(view.camera.type, camera.type);
    assert.deepEqual(view.camera.position, camera.position);
    assert.deepEqual(view.camera.quaternion.toArray(), camera.quaternion.toArray());
    assert.deepEqual(view.camera.up, camera.up);
    assert.deepEqual(view.target, target.toArray());
    assert.equal(view.fov, 51);
    if (view.camera instanceof PerspectiveCamera) assert.equal(view.camera.fov, 51);
    assert.equal(view.aspect, 16 / 9);
    assert.equal(view.camera.near, 0.2); assert.equal(view.camera.far, 900);
    assert.equal(view.camera.zoom, 1.3);
    assert.deepEqual(view.camera.projectionMatrix, camera.projectionMatrix);
    for (const point of [new Vector3(), new Vector3(5, 1, 4), new Vector3(-3, 2, 1)]) {
      assert.deepEqual(point.clone().project(view.camera), point.clone().project(camera));
    }
    const original = camera.position.clone();
    view.camera.position.set(0, 0, 0);
    assert.deepEqual(camera.position, original);
    mesh.geometry.dispose(); (mesh.material as MeshBasicMaterial).dispose();
  });
}

test("preview letterboxes wide and narrow hosts without changing source aspect", () => {
  assert.deepEqual(previewSize(1000, 300, 2), [600, 300]);
  assert.deepEqual(previewSize(200, 300, 2), [200, 100]);
});
