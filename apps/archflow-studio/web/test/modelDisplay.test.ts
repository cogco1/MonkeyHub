import assert from "node:assert/strict";
import test from "node:test";

import { BoxGeometry, Group, Mesh, MeshBasicMaterial } from "three";

import {
  captureModelAppearance,
  matchesSemanticCarrier,
  nextModelDisplayMode,
  restoreModelAppearance,
  semanticObjectNames,
} from "../src/viewer/modelDisplay.ts";

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
