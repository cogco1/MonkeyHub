/**
 * #562: the colours a 3DM saves reach the screen as saved. Rhino keeps colours as sRGB
 * channels; three's Rhino3dmLoader built them as if they were linear, so the renderer's
 * output encoding lifted every declared colour (Original showed them pale). The viewer's
 * preparation decodes them once. A small file written through rhino3dm the way MonkeyCAD's
 * preview writes declared materials (#560) is decoded here by the loader's own worker body
 * and built by its own ``_createGeometry``: nothing of the loader is re-implemented.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { pathToFileURL } from "node:url";

import rhino3dm from "rhino3dm";
import { Line, Mesh, MeshStandardMaterial, SRGBColorSpace, type Object3D } from "three";

import { prepareLoadedModel } from "../src/workspaces/monkeyarch/viewer/modelDisplay.ts";
import { decodeWithLoaderWorker, loaderPath } from "./rhino3dmLoaderWorker.ts";

function meshesByName(root: Object3D): Map<string, Mesh> {
  const meshes = new Map<string, Mesh>();
  root.traverse((object) => {
    if (object instanceof Mesh) meshes.set(object.name, object);
  });
  return meshes;
}

/** A small file written the way MonkeyCAD's preview writes one (#560), through rhino3dm in this process. */
async function declaredMaterialsFile(): Promise<Buffer> {
  const rhino = await rhino3dm();
  const model = new rhino.File3dm();
  const layer = new rhino.Layer();
  layer.name = "parts";
  layer.color = { r: 111, g: 178, b: 96, a: 255 };
  const layerIndex = model.layers().add(layer);
  const material = (name: string, diffuse: [number, number, number], transparency = 0, emission: [number, number, number] = [0, 0, 0]) => {
    const native = new rhino.Material();
    native.name = name;
    native.diffuseColor = { r: diffuse[0], g: diffuse[1], b: diffuse[2], a: 255 };
    native.emissionColor = { r: emission[0], g: emission[1], b: emission[2], a: 255 };
    native.transparency = transparency;
    return model.materials().add(native);
  };
  const metal = material("metal", [104, 112, 115]), glow = material("glow", [200, 120, 40], 0, [40, 20, 10]);
  const square = (name: string, z: number, strings: Record<string, string>, materialIndex: number | null) => {
    const mesh = new rhino.Mesh();
    for (const [x, y] of [[0, 0], [1, 0], [1, 1], [0, 1]]) mesh.vertices().add(x, y, z);
    mesh.faces().addQuadFace(0, 1, 2, 3);
    mesh.normals().computeNormals();
    const attributes = new rhino.ObjectAttributes();
    attributes.name = name;
    attributes.layerIndex = layerIndex;
    if (materialIndex !== null) {
      attributes.materialSource = rhino.ObjectMaterialSource.MaterialFromObject;
      attributes.materialIndex = materialIndex;
    }
    for (const [key, value] of Object.entries(strings)) attributes.setUserString(key, value);
    model.objects().add(mesh, attributes);
  };
  square("roof-a", 0, { "archflow:material": "metal" }, metal);
  square("roof-b", 1, { "archflow:material": "metal" }, metal);
  square("lamp", 2, { "archflow:material": "glow" }, glow);
  square("bench", 3, { "archflow:material_status": "undeclared" }, null);
  const curve = new rhino.PolylineCurve([[0, 0, 4], [1, 0, 4]]);
  const drawn = new rhino.ObjectAttributes();
  drawn.name = "axis";
  drawn.colorSource = rhino.ObjectColorSource.ColorFromObject;
  drawn.objectColor = { r: 104, g: 112, b: 115, a: 255 };
  model.objects().add(curve, drawn);
  return Buffer.from(model.toByteArray());
}

test("a declared material's sRGB colour is decoded once, so the viewer shows the colour the file saved (#562)", async () => {
  const { Rhino3dmLoader } = await import(pathToFileURL(loaderPath).href);
  const decoded = await decodeWithLoaderWorker(await declaredMaterialsFile());
  assert.deepEqual(decoded.materials.map((row) => [row.name, [row.diffuseColor.r, row.diffuseColor.g, row.diffuseColor.b]]),
    [["metal", [104, 112, 115]], ["glow", [200, 120, 40]]], "the worker reads the material table as written");
  const model: Object3D = new Rhino3dmLoader()._createGeometry(decoded);
  const meshes = meshesByName(model);
  const metal = meshes.get("roof-a")!.material as MeshStandardMaterial;
  const glow = meshes.get("lamp")!.material as MeshStandardMaterial;
  let axis: Line | undefined;
  model.traverse((object) => { if (object instanceof Line && object.name === "axis") axis = object; });
  assert.ok(axis, "the curve is drawn");
  const axisColour = (axis.material as MeshStandardMaterial).color;

  // The root cause: the installed loader builds a 0..1 colour from the sRGB channels and three takes it as
  // linear, so the output encoding lifts it. #687073 left the renderer as #abb1b3, the emission at 255 times its value.
  assert.equal(meshes.get("roof-b")!.material, metal, "objects of one material share it");
  assert.equal(metal.color.getHexString(SRGBColorSpace), "abb1b3");
  assert.equal(axisColour.getHexString(SRGBColorSpace), "abb1b3");
  assert.deepEqual(glow.emissive.toArray(), [40, 20, 10]);

  prepareLoadedModel(model);
  assert.equal(metal.color.getHexString(SRGBColorSpace), "687073", "the declared colour, as the file saved it");
  assert.deepEqual(metal.color.toArray().map((value) => Number(value.toFixed(4))), [0.1384, 0.162, 0.1714], "held in the renderer's linear working space");
  assert.equal(glow.color.getHexString(SRGBColorSpace), "c87828");
  assert.equal(glow.emissive.getHexString(SRGBColorSpace), "28140a", "the emission is a colour too, not 255 times one");
  assert.equal(axisColour.getHexString(SRGBColorSpace), "687073", "a curve's draw colour is decoded the same way");
  const bench = meshes.get("bench")!.material as MeshStandardMaterial;
  assert.equal(bench.color.getHexString(SRGBColorSpace), "6fb260", "an undeclared object keeps its distinction colour");

  // Preparing again - another view of the same file, a clone sharing these materials - never decodes twice.
  prepareLoadedModel(model.clone(true));
  prepareLoadedModel(model);
  assert.equal(metal.color.getHexString(SRGBColorSpace), "687073");
  assert.equal(glow.emissive.getHexString(SRGBColorSpace), "28140a");
  assert.equal(axisColour.getHexString(SRGBColorSpace), "687073");
});
