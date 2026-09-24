import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, PerspectiveCamera, Sphere, Vector3 } from "three";

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


// Exercise the production callbacks, not a second implementation of resize.
// The WebGL surface is the only fake; camera, bounds and projection are Three.
function viewportFixture() {
  const source = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const fitBody = source.slice(source.indexOf("function fitRuntime("), source.indexOf("function frontRuntime("));
  assert.ok(fitBody.startsWith("function fitRuntime("));
  const fit = new Function("Box3", "Vector3", "Sphere", "fitDistance",
    stripTypeScriptTypes(fitBody) + "; return fitRuntime;")(Box3, Vector3, Sphere, fitDistance);
  const marker = "    const resize = () => {";
  const start = source.indexOf(marker);
  assert.ok(start >= 0 && start === source.lastIndexOf(marker));
  const end = source.indexOf("    const observer = new ResizeObserver(resize);", start);
  assert.ok(end > start);
  const model = new Mesh(new BoxGeometry(8, 3, 4), new MeshBasicMaterial());
  model.position.set(12, -3, 7);
  const camera = new PerspectiveCamera(38, 1.5, 0.01, 10000);
  const target = new Vector3();
  const controls = { target, update: () => { camera.lookAt(target); camera.updateMatrixWorld(); } };
  const sizes: number[][] = [];
  let renders = 0;
  const render = () => { renders++; };
  const renderer = { setSize: (w: number, h: number) => { sizes.push([w, h]); } };
  const runtime = { model, camera, controls, renderer, draftRoot: new Group(), draftObjects: new Map(), render };
  const host = { clientWidth: 900, clientHeight: 600 };
  const resize = new Function("host", "renderer", "camera", "runtime", "clearHover", "render", "fitRuntime",
    source.slice(start, end) + "; return resize;")(host, renderer, camera, runtime, () => {}, render, fit);
  const pose = () => ({ position: camera.position.toArray(), target: target.toArray(), up: camera.up.toArray(),
    quaternion: camera.quaternion.toArray(), fov: camera.fov, near: camera.near, far: camera.far, zoom: camera.zoom });
  return { runtime, camera, controls, host, resize, fit: () => fit(runtime), pose, sizes,
    renderCount: () => renders, dispose: () => { model.geometry.dispose(); model.material.dispose(); } };
}

test("selection-panel resize never moves a camera, even immediately after Fit", () => {
  const h = viewportFixture();
  try {
    h.fit();
    const before = h.pose();
    for (const [width, height] of [[900, 600], [380, 850], [900, 600], [600, 1000], [1, 1], [900, 600]]) {
      h.host.clientWidth = width; h.host.clientHeight = height;
      h.resize();
      assert.deepEqual(h.pose(), before, `layout ${width}x${height} must not refit the selected view`);
      assert.equal(h.camera.aspect, width / height, "only the frame's aspect follows its size");
      assert.deepEqual(h.sizes.at(-1), [width, height]);
      assert.ok(h.camera.projectionMatrix.elements.every(Number.isFinite));
    }
    assert.equal(h.renderCount(), 7, "fit once, then render each resized frame without refitting");
  } finally { h.dispose(); }
});

test("a hand-placed view keeps target, up and lens through workspace resizing", () => {
  const h = viewportFixture();
  try {
    h.fit();
    h.camera.position.set(5, -8, 12); h.controls.target.set(1, 2, 3);
    h.camera.up.set(0, 1, 0); h.camera.fov = 52; h.camera.zoom = 1.25;
    h.controls.update(); h.camera.updateProjectionMatrix();
    const before = h.pose();
    h.host.clientWidth = 320; h.host.clientHeight = 900; h.resize();
    assert.deepEqual(h.pose(), before);
  } finally { h.dispose(); }
});

test("initial and explicitly requested Fit still frame the model after a layout change", () => {
  const h = viewportFixture();
  try {
    h.fit();
    assert.deepEqual(h.controls.target.toArray(), [12, -3, 7]);
    const first = h.camera.position.distanceTo(h.controls.target);
    h.host.clientWidth = 300; h.host.clientHeight = 1000; h.resize();
    assert.equal(h.camera.position.distanceTo(h.controls.target), first);
    h.fit();
    assert.ok(h.camera.position.distanceTo(h.controls.target) > first, "only explicit Fit moves back for a narrower frame");
    assert.deepEqual(h.controls.target.toArray(), [12, -3, 7]);
    assert.deepEqual(h.camera.up.toArray(), [0, 0, 1]);
  } finally { h.dispose(); }
});

test("hiding Modeling for Render preserves the last visible aspect and projection", () => {
  const h = viewportFixture();
  try {
    h.fit(); h.resize();
    const pose = h.pose(), matrix = h.camera.projectionMatrix.clone(), aspect = h.camera.aspect;
    const count = h.renderCount();
    h.host.clientWidth = 0; h.host.clientHeight = 0; h.resize();
    assert.deepEqual(h.pose(), pose);
    assert.equal(h.camera.aspect, aspect);
    assert.deepEqual(h.camera.projectionMatrix, matrix);
    assert.equal(h.renderCount(), count);
    h.host.clientWidth = 900; h.host.clientHeight = 600; h.resize();
    assert.deepEqual(h.pose(), pose);
  } finally { h.dispose(); }
});
