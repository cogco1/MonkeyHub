import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { Color, Material, Mesh, MeshStandardMaterial, SRGBColorSpace, type Object3D } from "three";

import {
  captureModelAppearance,
  fadeOpacity,
  isDisplayed,
  prepareLoadedModel,
  restoreModelAppearance,
  restoreOpacity,
  savedObjectVisible,
  type MaterialOpacity,
} from "../src/workspaces/monkeyarch/viewer/modelDisplay.ts";
import { decodeWithLoaderWorker, loaderPath, type DecodedFile } from "./rhino3dmLoaderWorker.ts";

/**
 * The preview ``execute_occt_export`` just wrote, opened by the installed
 * Rhino3dmLoader exactly as the viewer opens it - no browser, no manually
 * typed opacity, no copied binary.
 *
 * There is no fixture file. ``tests/integration/test_occt_execution.py`` executes the
 * typed south window of the fixture record into a temporary workspace and
 * hands the preview's path here through ``ARCHFLOW_PREVIEW_3DM``; on its
 * own, this file skips and says so. That preview carries two object
 * materials, ``frame`` opaque and ``glazing`` at openNURBS transparency 0.6,
 * bound MaterialFromObject; the plinth, the cut wall and the aperture on the
 * layer, the aperture saved hidden. The file is decoded by the loader's own
 * worker body, run in this process (rhino3dmLoaderWorker.ts), and handed to the
 * real ``_createGeometry``.
 */

const previewPath = process.env.ARCHFLOW_PREVIEW_3DM;
const skip = previewPath
  ? false
  : "no current preview: tests/integration/test_occt_execution.py exports one and hands its path in ARCHFLOW_PREVIEW_3DM";

function meshesByName(root: Object3D): Map<string, Mesh> {
  const meshes = new Map<string, Mesh>();
  root.traverse((object) => {
    if (object instanceof Mesh) meshes.set(object.name, object);
  });
  return meshes;
}

function opacityOf(material: Material): MaterialOpacity {
  return { transparent: material.transparent, opacity: material.opacity, depthWrite: material.depthWrite };
}

const FRAME = "obj-frame-wall-south-window-south";
const PANE = "obj-glazing-wall-south-window-south";
const APERTURE = "obj-wall-south-aperture-window-south";
const ON_LAYER = ["obj-plinth", "obj-wall-south", APERTURE];

interface LoadedPreview {
  decoded: DecodedFile;
  /** What ``Rhino3dmLoader._createGeometry`` built from the decode, before any preparation. */
  model: Object3D;
}

/** Decode the current preview once; the file is opened exactly as the viewer opens a file. */
let loading: Promise<LoadedPreview> | null = null;
function loadCurrentPreview(): Promise<LoadedPreview> {
  loading ??= (async () => {
    assert.ok(previewPath, "ARCHFLOW_PREVIEW_3DM names the current preview");
    assert.ok(existsSync(previewPath), `the current preview exists at ${previewPath}`);
    const { Rhino3dmLoader } = await import(pathToFileURL(loaderPath).href);
    const decoded = await decodeWithLoaderWorker(readFileSync(previewPath));
    const model: Object3D = new Rhino3dmLoader()._createGeometry(decoded);
    return { decoded, model };
  })();
  return loading;
}

test("the current preview's native materials reach the viewer through the installed loader", { skip }, async () => {
  const { decoded, model } = await loadCurrentPreview();

  // the worker read the file's material table and each object's binding as saved
  assert.deepEqual(
    decoded.materials.map((m) => [m.name, m.transparency, [m.diffuseColor.r, m.diffuseColor.g, m.diffuseColor.b]]),
    [["frame", 0, [107, 82, 102]], ["glazing", 0.6, [150, 200, 225]]],
  );
  const bindings = new Map(
    decoded.objects.map((o) => [o.attributes.name, [o.attributes.materialSource.name, o.attributes.materialIndex]]),
  );
  assert.deepEqual(bindings.get(FRAME), ["ObjectMaterialSource_MaterialFromObject", 0]);
  assert.deepEqual(bindings.get(PANE), ["ObjectMaterialSource_MaterialFromObject", 1]);
  for (const name of ON_LAYER) {
    assert.deepEqual(bindings.get(name), ["ObjectMaterialSource_MaterialFromLayer", -1], name);
  }

  // the loader builds the materials the viewer actually renders
  const meshes = meshesByName(model);
  assert.deepEqual([...meshes.keys()].sort(), [FRAME, PANE, ...ON_LAYER].sort());
  const frame = meshes.get(FRAME)!.material as Material;
  const glass = meshes.get(PANE)!.material as Material;
  const plinth = meshes.get("obj-plinth")!.material as Material;
  assert.equal(frame.type, "MeshPhysicalMaterial");
  assert.equal(frame.name, "frame");
  assert.deepEqual(opacityOf(frame), { transparent: false, opacity: 1, depthWrite: true });
  assert.equal(glass.type, "MeshPhysicalMaterial");
  assert.equal(glass.name, "glazing");
  assert.equal(glass.transparent, true);
  assert.ok(Math.abs(glass.opacity - 0.4) < 1e-9, `glazing opacity ${glass.opacity} is 1 - transparency 0.6`);
  assert.equal(plinth.name, "__DEFAULT");
  assert.deepEqual(opacityOf(plinth), { transparent: false, opacity: 1, depthWrite: true });

  // a cross-fade scales what the loader made and gives exactly that back
  const loaded = new Map([frame, glass, plinth].map((m) => [m, opacityOf(m)]));
  const own = new Map<Material, MaterialOpacity>();
  fadeOpacity([frame, glass, plinth], own, 0.5);
  assert.equal(frame.opacity, 0.5);
  assert.ok(Math.abs(glass.opacity - 0.2) < 1e-9, `faded glazing ${glass.opacity}`);
  assert.equal(glass.transparent, true);
  assert.equal(glass.depthWrite, false);
  fadeOpacity([frame, glass, plinth], own, 1);
  assert.equal(frame.transparent, false);
  assert.ok(Math.abs(glass.opacity - 0.4) < 1e-9, `glazing at weight 1 ${glass.opacity}`);
  assert.equal(glass.transparent, true);
  restoreOpacity(own);
  assert.equal(own.size, 0);
  for (const [material, state] of loaded) assert.deepEqual(opacityOf(material), state, material.name);

  // Layer display colors are saved in the native file, but the loader leaves
  // unassigned meshes white. Preparation restores that display color, and
  // decodes the native material's saved sRGB colour in place (#562).
  const savedPlinth = decoded.objects.find((object) => object.attributes.name === "obj-plinth")!.attributes;
  const rgb = savedPlinth.drawColor;
  assert.deepEqual(rgb, decoded.layers[savedPlinth.layerIndex].color);
  const expectedColor = new Color().setRGB(rgb.r / 255, rgb.g / 255, rgb.b / 255, SRGBColorSpace);
  const displayed = meshesByName(prepareLoadedModel(model.clone(true)));
  const displayedPlinth = displayed.get("obj-plinth")!.material as MeshStandardMaterial;
  assert.notEqual(displayedPlinth, plinth);
  assert.ok(displayedPlinth.color.equals(expectedColor));
  assert.equal(displayed.get(FRAME)!.material, frame);
  assert.equal(displayed.get(PANE)!.material, glass);
  assert.equal((frame as MeshStandardMaterial).color.getHexString(SRGBColorSpace), "6b5266", "the frame shows the colour the file saved");
  assert.deepEqual(opacityOf(displayedPlinth), loaded.get(plinth));
});

test("the aperture the export saved hidden is not displayed, and nothing brings it back", { skip }, async () => {
  const { decoded, model } = await loadCurrentPreview();

  // the file: every layer on, the aperture's own flag off, everything else on
  assert.ok(decoded.layers.length > 0 && decoded.layers.every((layer) => layer.visible), "the preview's layers are all visible");
  const saved = new Map(decoded.objects.map((o) => [o.attributes.name, o.attributes.visible]));
  assert.equal(saved.get(APERTURE), false);
  for (const name of [FRAME, PANE, "obj-plinth", "obj-wall-south"]) assert.equal(saved.get(name), true, name);

  // the installed loader set visibility from the layer alone: the hidden aperture came out visible
  const meshes = meshesByName(model);
  const aperture = meshes.get(APERTURE)!;
  assert.equal(aperture.visible, true);
  assert.equal(savedObjectVisible(aperture), false);

  // the viewer's preparation reads the object's own saved flag; the rest stays as the layer said
  assert.equal(prepareLoadedModel(model), model);
  assert.equal(aperture.visible, false);
  assert.equal(isDisplayed(aperture), false);
  for (const name of [FRAME, PANE, "obj-plinth", "obj-wall-south"]) {
    assert.equal(meshes.get(name)!.visible, true, name);
    assert.equal(isDisplayed(meshes.get(name)!), true, name);
  }

  // the appearance captured after preparation is the file's: hiding the root
  // for a blend and restoring the original brings the model back without the aperture
  const appearance = captureModelAppearance(model);
  model.visible = false;
  assert.equal(isDisplayed(meshes.get(FRAME)!), false);
  restoreModelAppearance(model, appearance);
  assert.equal(model.visible, true);
  assert.equal(aperture.visible, false);
  assert.equal(isDisplayed(aperture), false);
  assert.equal(isDisplayed(meshes.get(FRAME)!), true);

  // a cross-fade touches materials only; the hidden object stays hidden through it
  const own = new Map<Material, MaterialOpacity>();
  const materials = [...meshes.values()].map((mesh) => mesh.material as Material);
  fadeOpacity(materials, own, 0.5);
  fadeOpacity(materials, own, 1);
  restoreOpacity(own);
  assert.equal(aperture.visible, false);

  // and a layer switched on shows its objects, not the one the file hid
  const layerOn = true;
  assert.equal(layerOn && savedObjectVisible(aperture), false);
  assert.equal(layerOn && savedObjectVisible(meshes.get("obj-plinth")!), true);
});
