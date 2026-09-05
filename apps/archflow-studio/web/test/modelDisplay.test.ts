import assert from "node:assert/strict";
import test from "node:test";

import { BoxGeometry, Group, Mesh, MeshBasicMaterial } from "three";

import {
  captureModelAppearance,
  fadeOpacity,
  isDisplayed,
  matchesSemanticCarrier,
  nextModelDisplayMode,
  prepareLoadedModel,
  restoreModelAppearance,
  restoreOpacity,
  savedObjectVisible,
  semanticObjectNames,
} from "../src/viewer/modelDisplay.ts";

/** A mesh as the Rhino3dmLoader leaves it: attributes on userData, ``visible`` taken from its layer only. */
function loadedMesh(name: string, layerIndex: number, saved: { visible?: boolean; layerVisible: boolean }): Mesh {
  const mesh = new Mesh(new BoxGeometry(1, 1, 1), new MeshBasicMaterial());
  mesh.name = name;
  mesh.userData.attributes = { name, layerIndex, ...(saved.visible === undefined ? {} : { visible: saved.visible }) };
  mesh.visible = saved.layerVisible;
  return mesh;
}

test("framework and massing return to the original model without a picked object", () => {
  assert.equal(nextModelDisplayMode("model", "framework"), "framework");
  assert.equal(nextModelDisplayMode("framework", "framework"), "model");
  assert.equal(nextModelDisplayMode("model", "massing"), "massing");
  assert.equal(nextModelDisplayMode("massing", "model"), "model");
  assert.equal(nextModelDisplayMode("framework", "massing"), "massing");
});

test("returning to model restores every original visibility, layer and material", () => {
  const model = new Group();
  model.userData.layers = [{ name: "envelope", visible: true }];
  const carrier = new Group();
  carrier.visible = false;
  const original = new MeshBasicMaterial({ color: 0x315c8f });
  const mesh = new Mesh(new BoxGeometry(1, 1, 1), original);
  carrier.add(mesh);
  model.add(carrier);
  const appearance = captureModelAppearance(model);

  model.visible = false;
  carrier.visible = true;
  mesh.visible = false;
  mesh.material = new MeshBasicMaterial({ color: 0xffffff });
  model.userData.layers[0].visible = false;

  restoreModelAppearance(model, appearance);

  assert.equal(model.visible, true);
  assert.equal(carrier.visible, false);
  assert.equal(mesh.visible, true);
  assert.equal(mesh.material, original);
  assert.equal(model.userData.layers[0].visible, true);
});

test("semantic highlight matches only complete catalog object names", () => {
  const target = { objectNames: ["obj-portico"] } as const;

  assert.equal(matchesSemanticCarrier({ name: "obj-portico" }, target), true);
  assert.equal(matchesSemanticCarrier({ name: "obj-portico-base" }, target), false);
  assert.equal(matchesSemanticCarrier({ name: "obj-portico-01" }, target), false);
  assert.equal(matchesSemanticCarrier({}, target), false);
});

test("an element maps only to its bound catalog objects", () => {
  const objects = [
    {
      name: "obj-column-west",
      componentId: "portico-columns",
      elementId: "column-west",
      status: "bound",
    },
    {
      name: "obj-column-west",
      componentId: "portico-columns",
      elementId: "column-west",
      status: "bound",
    },
    {
      name: "obj-column-west-cap",
      componentId: "portico-columns",
      elementId: "column-west-cap",
      status: "bound",
    },
    {
      name: "obj-column-west-unbound",
      componentId: "portico-columns",
      elementId: "column-west",
      status: "AMBIGUOUS",
    },
    {
      name: "obj-column-west-other-component",
      componentId: "annex-columns",
      elementId: "column-west",
      status: "bound",
    },
  ];

  assert.deepEqual(
    semanticObjectNames(objects, [], "portico-columns", "column-west"),
    ["obj-column-west"],
  );
});

test("a component maps to exact catalog names in its component subtree", () => {
  const components = [
    { componentId: "building", children: ["portico", "rooms"] },
    { componentId: "portico", children: ["portico-columns"] },
    { componentId: "portico-columns", children: [] },
    { componentId: "rooms", children: [] },
    { componentId: "building-annex", children: [] },
  ];
  const objects = [
    {
      name: "obj-building-shell",
      componentId: "building",
      elementId: null,
      status: "MODEL_VISIBLE_CATALOG_MISSING",
    },
    {
      name: "obj-portico-roof",
      componentId: "portico",
      elementId: "portico-roof",
      status: "bound",
    },
    {
      name: "obj-portico-column-west",
      componentId: "portico-columns",
      elementId: "column-west",
      status: "bound",
    },
    {
      name: "obj-room-101",
      componentId: "rooms",
      elementId: "room-101",
      status: "bound",
    },
    {
      name: "obj-building-annex",
      componentId: "building-annex",
      elementId: "annex",
      status: "bound",
    },
    {
      name: "obj-no-component",
      componentId: null,
      elementId: null,
      status: "UNKNOWN_COMPONENT",
    },
  ];

  assert.deepEqual(semanticObjectNames(objects, components, "building", null), [
    "obj-building-shell",
    "obj-portico-column-west",
    "obj-portico-roof",
    "obj-room-101",
  ]);
});

test("a cross-fade scales each material's own opacity and gives it back exactly", () => {
  // As the Rhino3dmLoader builds them from the saved file: an opaque frame and a
  // pane with openNURBS transparency 0.6, i.e. opacity 0.4, already transparent.
  const frame = new MeshBasicMaterial({ color: 0x6b5266 });
  const glass = new MeshBasicMaterial({ color: 0x96c8e1, transparent: true, opacity: 0.4 });
  glass.depthWrite = false;
  const own = new Map();

  fadeOpacity([frame, glass], own, 0.5);
  assert.equal(frame.opacity, 0.5);
  assert.equal(frame.transparent, true);
  assert.equal(frame.depthWrite, false);
  assert.equal(glass.opacity, 0.2);
  assert.equal(glass.transparent, true);
  assert.equal(glass.depthWrite, false);

  // weight 1 is the file's own appearance, not "everything opaque"
  fadeOpacity([frame, glass], own, 1);
  assert.equal(frame.opacity, 1);
  assert.equal(frame.transparent, false);
  assert.equal(frame.depthWrite, true);
  assert.equal(glass.opacity, 0.4);
  assert.equal(glass.transparent, true);
  assert.equal(glass.depthWrite, false);

  // the remembered state is the first one seen, never a faded one
  fadeOpacity([frame, glass], own, 0);
  assert.equal(frame.opacity, 0);
  assert.equal(glass.opacity, 0);
  fadeOpacity([frame, glass], own, 0.25);
  assert.equal(frame.opacity, 0.25);
  assert.equal(glass.opacity, 0.1);

  restoreOpacity(own);
  assert.equal(own.size, 0);
  assert.deepEqual(
    [frame, glass].map((m) => [m.opacity, m.transparent, m.depthWrite]),
    [[1, false, true], [0.4, true, false]],
  );
});

test("a prepared model hides what the file saved hidden, on top of what its layer hides", () => {
  const model = new Group();
  model.userData.layers = [{ name: "building", visible: true }, { name: "setting-out", visible: false }];
  const frame = loadedMesh("obj-frame", 0, { visible: true, layerVisible: true });
  const aperture = loadedMesh("obj-aperture", 0, { visible: false, layerVisible: true });
  const axis = loadedMesh("obj-axis", 1, { visible: true, layerVisible: false });
  const untyped = new Group(); // an instance root: no attributes of its own
  untyped.add(loadedMesh("obj-leaf", 0, { layerVisible: true }));
  model.add(frame, aperture, axis, untyped);

  assert.equal(savedObjectVisible(frame), true);
  assert.equal(savedObjectVisible(aperture), false);
  assert.equal(savedObjectVisible(untyped), true);
  assert.equal(aperture.visible, true, "the loader read the layer only");

  assert.equal(prepareLoadedModel(model), model);
  assert.deepEqual(
    [frame, aperture, axis, untyped, untyped.children[0]].map((object) => object.visible),
    [true, false, false, true, true],
  );

  // the layer switched on shows the axis; switched on, it does not show the aperture
  for (const object of [frame, aperture, axis]) object.visible = true && savedObjectVisible(object);
  assert.deepEqual([frame.visible, aperture.visible, axis.visible], [true, false, true]);
});

test("the appearance captured after preparation restores without reviving a hidden object", () => {
  const model = new Group();
  const aperture = loadedMesh("obj-aperture", 0, { visible: false, layerVisible: true });
  const frame = loadedMesh("obj-frame", 0, { visible: true, layerVisible: true });
  model.add(aperture, frame);
  const appearance = captureModelAppearance(prepareLoadedModel(model));

  // a blend at 1 hides the root; a projection hides the frame
  model.visible = false;
  frame.visible = false;
  restoreModelAppearance(model, appearance);

  assert.equal(model.visible, true);
  assert.equal(frame.visible, true);
  assert.equal(aperture.visible, false);

  // a fade and its restoration touch materials, never the flags
  const own = new Map();
  fadeOpacity([frame.material as MeshBasicMaterial, aperture.material as MeshBasicMaterial], own, 0.3);
  restoreOpacity(own);
  assert.equal(aperture.visible, false);
});

test("only an object on screen through every ancestor is displayed", () => {
  const model = new Group();
  const carrier = new Group();
  const mesh = loadedMesh("obj-leaf", 0, { visible: true, layerVisible: true });
  carrier.add(mesh);
  model.add(carrier);

  assert.equal(isDisplayed(mesh), true);
  carrier.visible = false;
  assert.equal(isDisplayed(mesh), false, "a hidden ancestor hides the pick");
  carrier.visible = true;
  mesh.visible = false;
  assert.equal(isDisplayed(mesh), false, "its own flag hides the pick");
  mesh.visible = true;
  model.visible = false;
  assert.equal(isDisplayed(mesh), false, "the hidden root hides everything");
});
