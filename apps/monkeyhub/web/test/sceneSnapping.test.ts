import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import * as three from "three";
import * as facts from "../src/workspaces/monkeyarch/viewer/featureEdges.ts";

// Execute the production scene query with real Three cameras and raycasts.
// The source extraction only resolves extensionless browser imports in Node.
function implementation() {
  let builds = 0, outlines = 0;
  const isDisplayed = (object: three.Object3D) => {
    for (let p: three.Object3D | null = object; p; p = p.parent) if (!p.visible) return false;
    return true;
  };
  const scope = { ...three, ...facts, isDisplayed, featureEdges: (...args: Parameters<typeof facts.featureEdges>) => {
    builds++; return facts.featureEdges(...args);
  } };
  const pre = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/preselection.ts", import.meta.url), "utf8");
  const helpers = pre.slice(pre.indexOf("function activePositions"), pre.indexOf("/** One neutral outline"));
  const outline = new Function(...Object.keys(scope), stripTypeScriptTypes(helpers.replaceAll("export function", "function"))
    + "; return outlineEdges;")(...Object.values(scope));
  const queryScope = { ...scope, outlineEdges: (o: three.Mesh | three.Line) => { outlines++; return outline(o); } };
  const source = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/sceneSnapping.ts", import.meta.url), "utf8")
    .replace(/^import .*;$/gm, "").replace("export class", "class").replaceAll("export type", "type");
  const Index = new Function(...Object.keys(queryScope), stripTypeScriptTypes(source) + "; return SceneSnapIndex;")(...Object.values(queryScope));
  return { index: new Index(), counts: () => ({ builds, outlines }) };
}
const rect = { left: 50, top: 25, width: 1000, height: 1000 };
function fixture(ortho = true) {
  const h = implementation(), model = new three.Group();
  const camera = ortho ? new three.OrthographicCamera(-5, 5, 5, -5, .01, 1000) : new three.PerspectiveCamera(40, 1, .01, 1000);
  camera.position.set(0, 0, 15); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
  const add = (name: string, x = 0, y = 0, z = 0, width = 2, depth = 2, height = 1) => {
    const object = new three.Mesh(new three.BoxGeometry(width, depth, height), new three.MeshBasicMaterial({ side: three.DoubleSide }));
    object.name = name; object.position.set(x, y, z); model.add(object); return object;
  };
  const rebuild = () => h.index.rebuild([model], (object: three.Object3D) => ({ objectName: object.name }));
  const project = (point: readonly number[]) => {
    const p = new three.Vector3(...point).project(camera);
    return [rect.left + (p.x + 1) * rect.width / 2, rect.top + (1 - p.y) * rect.height / 2] as const;
  };
  const query = (point: readonly number[], offset = [0, 0], previous = null, options = {}) => {
    const screen = project(point);
    return h.index.query(camera, rect, [screen[0] + offset[0], screen[1] + offset[1]], 14, previous, options);
  };
  return { ...h, model, camera, add, rebuild, project, query };
}
function near(actual: number[], expected: number[], tolerance = 1e-6) {
  assert.ok(actual.every((v, i) => Math.abs(v - expected[i]!) < tolerance), `${actual} != ${expected}`);
}

for (const ortho of [false, true]) test(`adjacent objects and empty pixels acquire true features: ${ortho ? "ortho" : "perspective"}`, () => {
  const h = fixture(ortho);
  h.add("A", -2.04); h.add("B"); h.rebuild();
  const corner = h.query([1, 1, .5], [4, -4]);
  assert.equal(corner.snap.kind, "endpoint"); assert.equal(corner.snap.objectName, "B"); near(corner.snap.point, [1, 1, .5]);
  const midpoint = h.query([1, 0, .5], [5, 0]);
  assert.equal(midpoint.snap.kind, "midpoint"); near(midpoint.snap.point, [1, 0, .5]);
  assert.equal(h.query([1, .45, .5], [5, 0]).snap.kind, "edge");
  assert.equal(h.query([.35, .35, .5]).snap.kind, "surface", "the top's triangle diagonal stays a face");
  h.add("floor", 0, 0, -2, 6, 6); h.rebuild();
  const fromA = h.query([1, 1, .5], [4, -4]);
  assert.equal(fromA.snap.objectName, "B", "nearest adjacent target wins even when the pixel lies on A");
});

test("candidate visibility is tested at its own pixel; held targets cannot survive occlusion, hiding or deletion", () => {
  const h = fixture(); const back = h.add("back"), cover = h.add("cover", .5, .5, 2, 3, 3);
  cover.visible = false; h.rebuild();
  const held = h.query([1, 1, .5], [3, -3]); assert.equal(held.snap.kind, "endpoint");
  cover.visible = true;
  assert.equal(h.query([1, 1, .5], [3, -3], held).snap.objectName, "cover");
  cover.visible = false; back.visible = false;
  assert.equal(h.query([1, 1, .5], [3, -3], held), null);
  back.visible = true; back.removeFromParent();
  assert.equal(h.query([1, 1, .5], [3, -3], held), null);
});

test("a hidden ancestor and changed geometry invalidate candidates without pointer-time topology builds", () => {
  const h = fixture(); const mesh = h.add("body"); h.rebuild();
  const held = h.query([1, 1, .5], [3, -3]);
  h.model.visible = false; assert.equal(h.query([1, 1, .5], [3, -3], held), null);
  h.model.visible = true;
  mesh.geometry = new three.BoxGeometry(1, 1, 1); h.rebuild();
  assert.equal(h.query([1, 1, .5], [3, -3], held), null);
  assert.equal(h.query([.5, .5, .5], [3, -3]).snap.kind, "endpoint");
  const before = h.counts(); for (let i = 0; i < 100; i++) h.query([.5, .5, .5], [3, -3]);
  assert.deepEqual(h.counts(), before);
});

test("hysteresis holds small noise, releases outside 21 pixels and rechecks the current constraint", () => {
  const h = fixture(); h.add("body"); h.rebuild();
  const held = h.query([1, 1, .5], [4, -4]);
  assert.deepEqual(h.query([1, 1, .5], [15, -1], held).snap.point, held.snap.point);
  assert.equal(h.query([1, 1, .5], [22, -1], held), null);
  assert.equal(h.query([1, 1, .5], [4, -4], held, { constraint: { kind: "axis", origin: [0, 0, 0], direction: [1, 0, 0] } }), null);
});

test("plane and normal projections expose the actual result and decline distant incompatible points", () => {
  const h = fixture(); h.add("body"); h.rebuild();
  const plane = { kind: "plane", origin: [0, 0, 0], direction: [0, 0, 1] };
  const projected = h.query([1, 1, .5], [3, -3], null, { constraint: plane }).snap;
  near(projected.point, [1, 1, 0]); near(projected.sourcePoint, [1, 1, .5]); assert.equal(projected.projected, true);
  const axis = { kind: "axis", origin: [0, .95, 0], direction: [1, 0, 0] };
  near(h.query([1, 1, .5], [3, -3], null, { constraint: axis }).snap.point, [1, .95, 0]);
  assert.equal(h.query([1, 1, .5], [3, -3], null, { excludeObjectName: "body" }), null);
});

for (const ortho of [false, true]) test(`screen tolerance survives zoom: ${ortho ? "ortho" : "perspective"}`, () => {
  const h = fixture(ortho); h.add("body"); h.rebuild();
  for (const zoom of [.3, 1, 2]) {
    h.camera.zoom = zoom; h.camera.updateProjectionMatrix();
    assert.equal(h.query([1, 1, .5], [10, -1]).snap.kind, "endpoint");
    assert.equal(h.query([1, 1, .5], [15, -1]), null);
  }
});

test("400 shared boxes: one geometry build, no rebuilds during 600 scene queries", () => {
  const h = fixture(); const geometry = new three.BoxGeometry(1, 1, 1);
  for (let i = 0; i < 400; i++) { const mesh = h.add(`box-${i}`, i % 20 * 2, Math.floor(i / 20) * 2); mesh.geometry = geometry; }
  const c = h.camera as three.OrthographicCamera;
  c.left = -20; c.right = 20; c.top = 20; c.bottom = -20; c.position.set(19, 19, 100); c.lookAt(19, 19, 0); c.updateMatrixWorld(true); c.updateProjectionMatrix();
  const start = performance.now(); h.rebuild(); const buildMs = performance.now() - start, counts = h.counts();
  assert.deepEqual(counts, { builds: 1, outlines: 400 });
  const timings = [];
  for (let i = 0; i < 600; i++) {
    const start = performance.now(); const value = h.query([.5, .5, .5], [-2 + (i % 3) * .2, 2]);
    if (i >= 100) timings.push(performance.now() - start);
    assert.equal(value.snap.objectName, "box-0");
  }
  assert.deepEqual(h.counts(), counts); timings.sort((a, b) => a - b);
  console.log(JSON.stringify({ fixture: "400 boxes / Top / 1000px", buildMs, ...counts, queries: 600,
    queryMs: { median: timings[250], p95: timings[475] } }));
});

test("a receding perspective edge inside 14 screen pixels is not lost to ray-distance minimization", () => {
  const h = fixture(false);
  const c = h.camera as three.PerspectiveCamera;
  c.fov = 2 * Math.atan(.5) * 180 / Math.PI; // 1000 px focal length
  c.position.set(0, 0, 0); c.lookAt(0, 0, -1); c.updateMatrixWorld(); c.updateProjectionMatrix();
  const a = new three.Vector3(-.02, 0, -1), b = new three.Vector3(2, 0, -100);
  const line = new three.Line(new three.BufferGeometry().setFromPoints([a, b]), new three.LineBasicMaterial());
  line.name = "receding"; h.model.add(line); h.rebuild();
  const target = h.index.query(c, rect, [rect.left + 500, rect.top + 513], 14, null);
  assert.equal(target.snap.kind, "edge");
  const screen = h.project(target.snap.point);
  near([...screen], [rect.left + 500, rect.top + 500]);
  const point = new three.Vector3(...target.snap.point);
  assert.ok(point.distanceTo(new three.Vector3().copy(a).lerp(b, .02 / 2.02)) < 1e-6, "screen parameter uses perspective-correct world interpolation");
  // Replacing one end with a point behind the camera keeps the visible segment.
  line.geometry = new three.BufferGeometry().setFromPoints([new three.Vector3(-2, 0, 1), b]); h.rebuild();
  const clipped = h.index.query(c, rect, [rect.left + 480, rect.top + 510], 14, null);
  assert.equal(clipped.snap.kind, "edge"); assert.ok(clipped.snap.point[2] < -c.near);
});

test("near/far clipping governs source faces and occluders before constraint projection", () => {
  const h = fixture(false), camera = h.camera as three.PerspectiveCamera;
  camera.far = 10; camera.updateProjectionMatrix();
  h.add("outside-far", 0, 0, -10); h.rebuild();
  assert.equal(h.query([0,0,10], [0,0], null, { constraint: { kind:"plane", origin:[0,0,10], direction:[0,0,1] } }), null);
  h.add("visible", 0, 0, 10);
  h.add("before-near", 0, 0, 14.999, 4, 4, .001); h.rebuild();
  assert.equal(h.query([1,1,10.5], [3,-3]).snap.objectName, "visible", "a mesh clipped by near cannot occlude a visible target");
});

test("an object's bound centred on the eye plane defers to exact edge clipping", () => {
  const h=fixture(false), c=h.camera;
  c.position.set(0,0,0); c.lookAt(0,0,-1); c.updateMatrixWorld();
  const line=new three.Line(new three.BufferGeometry().setFromPoints([new three.Vector3(0,.1,1),new three.Vector3(0,.1,-1)]),new three.LineBasicMaterial());
  line.name="crossing-eye"; h.model.add(line); h.rebuild();
  const result=h.query([0,.1,-.5]);
  assert.equal(result.snap.kind,"edge"); near(result.snap.point,[0,.1,-.5]);
});
