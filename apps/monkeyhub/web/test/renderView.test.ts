import assert from "node:assert/strict";
import test from "node:test";
import { Scene, Mesh, BoxGeometry, MeshBasicMaterial, PerspectiveCamera, OrthographicCamera, Vector3 } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { captureRenderView, previewSize, renderCamera, savedCamera, savedView, savedViewCamera } from "../src/workspaces/monkeyarch/viewer/renderView.ts";
import { configureOrthographicAspect, type ProjectionMode, type ViewCamera } from "../src/workspaces/monkeyarch/viewer/cameraProjection.ts";
import type { CameraState } from "../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx";

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

// Saved cameras (#218). Modeling makes its cameras Z-up with an orthographic frustum one unit
// high each way, and turns them with OrbitControls built while they are Z-up.
const ASPECT = 16 / 9;
const POINTS = [new Vector3(), new Vector3(5, 1, 4), new Vector3(-3, 2, 1), new Vector3(9, -4, 6)];

function modelingCamera(projection: ProjectionMode, aspect: number): ViewCamera {
  const camera = projection === "perspective" ? new PerspectiveCamera(38, aspect, 0.01, 10000) : new OrthographicCamera(-1, 1, 1, -1, 0.01, 10000);
  if (camera instanceof OrthographicCamera) configureOrthographicAspect(camera, aspect);
  camera.up.set(0, 0, 1);
  return camera;
}

/** A Modeling view as a person leaves it: orbited about a target, zoomed and drawn. */
function orbitedView(projection: ProjectionMode, position: Vector3, target: Vector3, zoom = 1, up?: Vector3) {
  const camera = modelingCamera(projection, ASPECT);
  const controls = new OrbitControls(camera);
  if (up) camera.up.copy(up);
  camera.position.copy(position); controls.target.copy(target);
  if (camera instanceof OrthographicCamera) camera.zoom = zoom;
  camera.updateProjectionMatrix(); controls.update(); camera.updateMatrixWorld(true);
  return captureRenderView(new Scene(), camera, controls.target, 38, 1.05);
}

/** Retained as a source document's recipe and read back, as the Runtime returns it. */
function retained(camera: object) {
  const saved = savedView(JSON.parse(JSON.stringify({ kind: "model-view", camera, screenSize: [2048, 1152] })));
  assert.ok(saved, "a captured model view is a saved view");
  return saved;
}

/** What ThreeDmViewport.applyCamera does with a camera state, in a Modeling frame of this aspect. */
function standIn(state: CameraState, aspect: number): ViewCamera {
  const camera = modelingCamera(state.projection, aspect);
  const controls = new OrbitControls(camera);
  camera.up.set(...state.up); camera.position.set(...state.position); camera.zoom = state.zoom;
  controls.target.set(...state.target);
  if (camera instanceof PerspectiveCamera) {
    camera.fov = state.fov;
    const distance = camera.position.distanceTo(controls.target);
    camera.near = Math.max(distance / 1000, 0.01); camera.far = Math.max(distance * 100, 1000);
  }
  camera.updateProjectionMatrix(); controls.update(); camera.updateMatrixWorld(true);
  return camera;
}

function close(actual: readonly number[], expected: readonly number[], tolerance: number, label: string) {
  assert.equal(actual.length, expected.length, label);
  actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]!) <= tolerance, `${label}[${index}]: ${value} != ${expected[index]}`));
}

/** Where each point lands on screen: x and y; depth follows each camera's own clipping. */
function screen(camera: ViewCamera, scaleX = 1) {
  return POINTS.flatMap((point) => { const ndc = point.clone().project(camera); return [ndc.x * scaleX, ndc.y]; });
}

for (const projection of ["perspective", "orthographic"] as const) {
  test(`${projection}: a saved camera draws the same projection again and stands Modeling in it`, () => {
    const view = orbitedView(projection, new Vector3(12, -19, 7), new Vector3(2, 3, 1), 0.11);
    const camera = renderCamera(view);
    assert.deepEqual(camera.target, [2, 3, 1]);
    assert.deepEqual(camera.up, [0, 0, 1]);
    const saved = retained(camera);
    assert.deepEqual(saved.camera, JSON.parse(JSON.stringify(camera)), "a capture taken again keeps the retained camera");
    assert.deepEqual(saved.screenSize, [2048, 1152]);

    // Captured again: the saved matrices as they are.
    const again = savedCamera(saved.camera);
    assert.equal(again.type, view.camera.type);
    close(again.matrixWorld.elements, view.camera.matrixWorld.elements, 0, "world");
    close(again.projectionMatrix.elements, view.camera.projectionMatrix.elements, 0, "projection");
    close(POINTS.flatMap((point) => point.clone().project(again).toArray()),
      POINTS.flatMap((point) => point.clone().project(view.camera).toArray()), 1e-12, "re-captured points");

    // Shown in Modeling: the same eye, target, upright and lens through applyCamera.
    const state = savedViewCamera(saved.camera, [0, 0, 0], 38);
    close(state.position, view.camera.position.toArray(), 1e-9, "position");
    close(state.target, [2, 3, 1], 1e-9, "target");
    assert.deepEqual(state.up, [0, 0, 1]);
    assert.equal(state.projection, projection);
    const shown = standIn(state, ASPECT);
    close(screen(shown), screen(view.camera), 1e-9, "shown points");
    // Another Modeling frame keeps the saved vertical extent; only the width follows the frame.
    close(screen(standIn(state, 4 / 3), (4 / 3) / ASPECT), screen(view.camera), 1e-9, "points in a 4:3 frame");
    const turned = savedViewCamera({ ...saved.camera, up: [1, 0, 0] }, [0, 0, 0], 38);
    assert.deepEqual(turned.up, [0, 0, 1], "an up that would turn the saved picture is not stood in");
    close(screen(standIn(turned, ASPECT)), screen(view.camera), 1e-9, "points with the corrected up");
  });

  test(`${projection}: an older capture without target or up stands in its own view line`, () => {
    const view = orbitedView(projection, new Vector3(-8, -14, 9), new Vector3(1, 2, 0.5), 0.07);
    const { target: _target, up: _up, ...older } = renderCamera(view);
    const saved = retained(older);
    assert.equal("target" in saved.camera || "up" in saved.camera, false, "nothing is invented for the retained camera");
    const focus: [number, number, number] = [4, 0, 2];
    const state = savedViewCamera(saved.camera, focus, 38);
    const eye = new Vector3(...state.position), target = new Vector3(...state.target);
    const forward = view.camera.getWorldDirection(new Vector3());
    assert.ok(target.clone().sub(eye).normalize().dot(forward) > 1 - 1e-12, "the inferred target is ahead on the view line");
    assert.ok(Math.abs(new Vector3(...focus).sub(target).dot(forward)) < 1e-9, "it is the point of that line nearest the model");
    assert.deepEqual(state.up, [0, 0, 1], "a view that is not rolled keeps the model upright");
    close(screen(standIn(state, ASPECT)), screen(view.camera), 1e-9, "shown points");
  });
}

test("a saved top view keeps its saved up, or its own screen up where the model's Z cannot orient it", () => {
  // Modeling's Top view turns its camera's up to the screen's (fitOrthographicBox).
  const view = orbitedView("orthographic", new Vector3(3, 4, 40), new Vector3(3, 4, 0), 0.2, new Vector3(0, 1, 0));
  const camera = renderCamera(view);
  assert.deepEqual(camera.up, [0, 1, 0]);
  const state = savedViewCamera(retained(camera).camera, [0, 0, 0], 38);
  assert.deepEqual(state.up, [0, 1, 0]);
  close(screen(standIn(state, ASPECT)), screen(view.camera), 1e-9, "shown points");
  const { target: _target, up: _up, ...older } = camera;
  const inferred = savedViewCamera(retained(older).camera, [3, 4, 0], 38);
  close(inferred.up, view.camera.matrixWorld.elements.slice(4, 7), 1e-12, "screen up");
  close(screen(standIn(inferred, ASPECT)), screen(view.camera), 1e-9, "older capture's points");
});

test("only a complete captured model view is a saved view", () => {
  const camera = renderCamera(orbitedView("perspective", new Vector3(12, -19, 7), new Vector3(2, 3, 1)));
  const recipe = { kind: "model-view", camera, screenSize: [2048, 1152] };
  assert.equal(savedView(null), null);
  assert.equal(savedView({ ...recipe, kind: "cut-plan" }), null);
  assert.equal(savedView({ ...recipe, screenSize: [2048] }), null);
  assert.equal(savedView({ ...recipe, camera: { ...camera, projection: "fisheye" } }), null);
  assert.equal(savedView({ ...recipe, camera: { ...camera, worldMatrix: camera.worldMatrix.slice(1) } }), null);
  assert.equal(savedView({ ...recipe, camera: { ...camera, exposure: 0 } }), null);
  assert.deepEqual(savedView({ ...recipe, camera: { ...camera, up: undefined } })?.camera,
    { projection: "perspective", worldMatrix: camera.worldMatrix, projectionMatrix: camera.projectionMatrix, exposure: 1.05 },
    "an orbit is kept only whole");
});
