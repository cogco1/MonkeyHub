import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import rhino3dm from "rhino3dm";

const rhino = await rhino3dm();
const workerSource = await readFile(new URL("../public/nurbsFallback.worker.js", import.meta.url), "utf8");

// Run the unchanged worker entry with the installed Rhino runtime. Every 3DM
// below is created and re-read as bytes in memory; no source project is used.
async function fallback(document) {
  const bytes = document.toByteArray();
  let result;
  const self = { postMessage(value) { result = value; } };
  new Function("importScripts", "rhino3dm", "self", workerSource)(
    (url) => assert.equal(url, "/rhino3dm/rhino3dm.js"), async () => rhino, self,
  );
  await self.onmessage({ data: bytes });
  assert.ok(result);
  assert.equal(result.error, undefined);
  for (const path of result.paths) assert.ok([...path.positions].every(Number.isFinite));
  return result;
}
function layer(document, name, visible = true, parentIndex = null) {
  const row = new rhino.Layer(); row.name = name; row.visible = visible;
  if (parentIndex !== null) {
    const parent = document.layers().get(parentIndex);
    row.parentLayerId = parent.id; parent.delete();
  }
  const index = document.layers().add(row); row.delete(); return index;
}
function attributes(name, layerIndex, visible = true) {
  const value = new rhino.ObjectAttributes(); value.name = name; value.layerIndex = layerIndex; value.visible = visible; return value;
}
function box(x = 0) {
  const bounds = new rhino.BoundingBox([x, 0, 0], [x + 1, 1, 1]);
  const value = rhino.Brep.createFromBoundingBox(bounds); bounds.delete(); return value;
}
function add(document, geometry, attrs) {
  document.objects().add(geometry, attrs); geometry.delete(); attrs.delete();
}
function definition(document, name, geometry, attrs) {
  const index = document.instanceDefinitions().add(name, "in-memory fixture", "", "", [0, 0, 0], geometry, attrs);
  assert.ok(index >= 0);
  const saved = document.instanceDefinitions().get(index), id = saved.id;
  // Adding a definition resets its members to visible. Save their requested
  // visibility after insertion, as an editor would, so the bytes carry it.
  [...saved.getObjectIds()].forEach((objectId, position) => {
    const object = document.objects().findId(objectId), member = object.attributes();
    member.visible = attrs[position].visible;
    assert.equal(member.isInstanceDefinitionObject, true);
    member.delete(); object.delete();
  });
  for (const value of [...geometry, ...attrs]) value.delete();
  saved.delete(); return id;
}
function instance(id, x) {
  const transform = rhino.Transform.translationXYZ(x, 0, 0);
  const value = new rhino.InstanceReference(id, transform); transform.delete(); return value;
}
function names(result) { return [...new Set([...result.paths, ...result.faces].map((patch) => patch.attributes.name))].sort(); }

test("fallback omits hidden objects and layers, including visible children of hidden ancestors", async () => {
  const document = new rhino.File3dm();
  try {
    const shown = layer(document, "shown"), off = layer(document, "off", false);
    const childOfOff = layer(document, "child-of-off", true, off);
    const grandchildOfOff = layer(document, "grandchild-of-off", true, childOfOff);
    const shownChild = layer(document, "shown-child", true, shown);
    const offChild = layer(document, "off-child", false, shown);
    const shownGrandchild = layer(document, "shown-grandchild", true, shownChild);
    add(document, box(0), attributes("shown", shown));
    add(document, box(3), attributes("shown-child", shownChild));
    add(document, box(6), attributes("shown-grandchild", shownGrandchild));
    add(document, box(1000), attributes("hidden-object", shown, false));
    add(document, box(2000), attributes("hidden-layer", off));
    add(document, box(3000), attributes("hidden-parent-layer", childOfOff));
    add(document, box(4000), attributes("hidden-grandparent-layer", grandchildOfOff));
    add(document, box(5000), attributes("hidden-child-layer", offChild));
    const savedChild = document.layers().get(childOfOff);
    assert.equal(savedChild.visible, true, "The child itself must be visible for this to exercise parent inheritance"); savedChild.delete();
    const result = await fallback(document);
    assert.deepEqual(names(result), ["shown", "shown-child", "shown-grandchild"]);
    assert.equal(result.faces.length, 18); assert.equal(result.unsupportedFaces, 0);
    assert.ok(result.paths.every((path) => [...path.positions].filter((_, index) => index % 3 === 0).every((x) => x >= 0 && x <= 7)),
      "Hidden far-away geometry must not enter the fallback's display bounds");
  } finally { document.delete(); }
});

test("fallback inherits root and nested instance visibility while preserving visible transforms", async () => {
  const document = new rhino.File3dm();
  try {
    const shown = layer(document, "shown"), off = layer(document, "off", false);
    const childOfOff = layer(document, "child-of-off", true, off);
    const inner = definition(document, "inner", [box(), box(100)],
      [attributes("unit", shown), attributes("hidden-definition-leaf", shown, false)]);
    const outer = definition(document, "outer", [instance(inner, 2), instance(inner, 50), instance(inner, 80)],
      [attributes("visible-nested-reference", shown), attributes("hidden-nested-reference", shown, false), attributes("nested-reference-on-hidden-parent-layer", childOfOff)]);
    add(document, instance(inner, 10), attributes("visible-root", shown));
    add(document, instance(inner, 1000), attributes("hidden-root", shown, false));
    add(document, instance(outer, 20), attributes("visible-outer", shown));
    add(document, instance(outer, 2000), attributes("hidden-outer", shown, false));
    add(document, instance(outer, 3000), attributes("outer-on-hidden-layer", off));
    add(document, instance(outer, 4000), attributes("outer-on-hidden-parent-layer", childOfOff));
    const result = await fallback(document);
    assert.deepEqual(names(result), ["unit"]);
    assert.equal(result.faces.length, 12); assert.equal(result.paths.length, 24); assert.equal(result.unsupportedFaces, 0);
    const xs = result.paths.flatMap((path) => [...path.positions].filter((_, index) => index % 3 === 0));
    assert.ok(xs.every((x) => (x >= 10 && x <= 11) || (x >= 22 && x <= 23)));
    assert.equal(Math.min(...xs), 10); assert.equal(Math.max(...xs), 23);
  } finally { document.delete(); }
});

test("visible planar Breps still produce face loops and non-planar Breps remain edge-only", async () => {
  const document = new rhino.File3dm();
  try {
    const shown = layer(document, "shown"), off = layer(document, "off", false);
    add(document, box(), attributes("planar-box", shown));
    const sphere = new rhino.Sphere([3, 0, 0], 1);
    add(document, rhino.Brep.createFromSphere(sphere), attributes("curved-sphere", shown)); sphere.delete();
    const hiddenSphere = new rhino.Sphere([1000, 0, 0], 5);
    add(document, rhino.Brep.createFromSphere(hiddenSphere), attributes("hidden-curved-sphere", off)); hiddenSphere.delete();
    const result = await fallback(document);
    assert.equal(result.faces.length, 6);
    assert.ok(result.faces.every((face) => face.attributes.name === "planar-box" && face.loops.length === 1 && face.loops[0].length >= 4));
    assert.ok(result.paths.some((path) => path.attributes.name === "curved-sphere"));
    assert.equal(result.faces.some((face) => face.attributes.name === "curved-sphere"), false);
    assert.equal(result.unsupportedFaces, 1, "Only the visible curved face contributes an edge-only warning");
    assert.equal(names(result).includes("hidden-curved-sphere"), false);
  } finally { document.delete(); }
});
