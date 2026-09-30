import assert from "node:assert/strict";
import test from "node:test";
import { Box3, BoxGeometry, Mesh, MeshBasicMaterial, OrthographicCamera, PerspectiveCamera, Scene, Vector3, type Camera } from "three";
import { constrainedTranslation, TRANSLATION_CONSTRAINTS, TranslationGizmo, type TranslationConstraint } from "../src/workspaces/monkeyarch/viewer/translationGizmo.ts";

const pointer = (point: readonly number[], camera: Camera) => {
  const p = new Vector3(...point).project(camera);
  return { x: p.x, y: p.y, button: 0 };
};
function fixture(orthographic = false, position = [8, -10, 9]) {
  const scene = new Scene();
  const camera = orthographic ? new OrthographicCamera(-8, 8, 6, -6, 0.01, 1000) : new PerspectiveCamera(38, 4 / 3, 0.01, 1000);
  camera.position.fromArray(position); camera.up.set(0, 0, 1); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  const object = new Mesh(new BoxGeometry(2, 2, 2), new MeshBasicMaterial());
  object.position.set(20, 30, 40); scene.add(object);
  const handles = new TranslationGizmo(scene, camera);
  handles.set({ origin: [0, 0, 0], translation: [0, 0, 0], constraint: null });
  return { scene, camera, object, handles, dispose: () => { handles.dispose(); object.geometry.dispose(); object.material.dispose(); } };
}
function handlePoint(h: ReturnType<typeof fixture>, axis: TranslationConstraint) {
  const candidates: number[][] = [];
  // Find a point actually covered by a visible named handle. This reads the
  // helper's public scene graph, never the vendor's private picker state.
  h.handles.controls.getHelper().traverse(object => {
    if (object instanceof Mesh && object.name === axis && object.visible && object.parent?.visible && object.layers.mask) {
      const center = new Box3().setFromObject(object).getCenter(new Vector3());
      const onConstraint = constrainedTranslation(center.toArray(), axis);
      const p = pointer(onConstraint, h.camera);
      if (h.handles.hover(p) === axis) candidates.push(onConstraint);
    }
  });
  assert.ok(candidates.length, `visible ${axis} handle must be pickable`);
  return candidates[0]!;
}
function close(actual: readonly number[], expected: readonly number[]) {
  actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]!) < 1e-8, `${actual} != ${expected}`));
}

test("one numeric constraint leaves all other World coordinates exactly zero", () => {
  const values = [1.25, -2.5, 3.75];
  for (const axis of TRANSLATION_CONSTRAINTS) {
    const result = constrainedTranslation(values, axis);
    result.forEach((value, i) => assert.equal(value, axis.includes("XYZ"[i]!) ? values[i] : 0));
  }
  assert.deepEqual(values, [1.25, -2.5, 3.75], "input is not mutated");
  for (const bad of [[NaN, 0, 0], [0, Infinity, 0], [0, 0], [0, 0, 0, 0]])
    assert.throws(() => constrainedTranslation(bad, "X"));
});

for (const orthographic of [false, true]) for (const position of [[8,-10,9], [-8,10,9]]) {
  test(`real handle picking and signed axis/plane drag: ${orthographic ? "orthographic" : "perspective"} ${position}`, () => {
    for (const axis of TRANSLATION_CONSTRAINTS) {
      const h = fixture(orthographic, position);
      try {
        const camera = h.camera.matrixWorld.toArray(), original = h.object.position.toArray();
        const from = handlePoint(h, axis), start = h.handles.start(pointer(from, h.camera));
        assert.equal(start?.constraint, axis);
        const delta = constrainedTranslation([-0.75, 1.25, -0.5], axis);
        const moved = h.handles.move(pointer(from.map((n, i) => n + delta[i]!), h.camera));
        assert.equal(moved?.constraint, axis); close(moved!.translation, delta);
        h.handles.end();
        assert.equal(h.handles.move(pointer([99, 99, 99], h.camera)), null, "release does not keep dragging");
        assert.deepEqual(h.object.position.toArray(), original, "a gizmo never transforms the design object");
        assert.deepEqual(h.camera.matrixWorld.toArray(), camera, "a gizmo never moves the camera");
      } finally { h.dispose(); }
    }
  });
}

test("numeric override updates only the projection and changing constraint clears other axes", () => {
  const h = fixture();
  try {
    h.handles.set({ origin: [10,20,30], translation: [-1.2345,9,8], constraint: "X" });
    close(h.handles.proxy.position.toArray(), [8.7655,20,30]);
    h.handles.set({ origin: [10,20,30], translation: [3,4,5], constraint: "YZ" });
    close(h.handles.proxy.position.toArray(), [10,24,35]);
    assert.deepEqual(h.object.position.toArray(), [20,30,40]);
  } finally { h.dispose(); }
  assert.equal(h.scene.children.length, 1, "all disposable handles and proxy are removed");
});

test("end-on axes and edge-on planes have no active drag handle; numbers still work", () => {
  const h = fixture(true, [0,0,20]);
  try {
    const available = new Set<string>();
    h.handles.controls.getHelper().traverse(object => {
      if (object instanceof Mesh && object.visible && object.parent?.visible && object.layers.mask) available.add(object.name);
      if (object.name === "XYZ") assert.equal(object.layers.mask, 0, "free handle cannot be drawn or picked");
    });
    assert.ok(!available.has("Z")); assert.ok(!available.has("XZ")); assert.ok(!available.has("YZ"));
    h.handles.set({ origin: [0,0,0], translation: [0,0,-3], constraint: "Z" });
    assert.deepEqual(h.handles.proxy.position.toArray(), [0,0,-3]);
  } finally { h.dispose(); }
});
