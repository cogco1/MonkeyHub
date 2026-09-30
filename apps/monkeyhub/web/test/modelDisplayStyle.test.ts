/**
 * The Modeling display look: pale stand-ins over the file's own materials and
 * thin feature edges beside the model. Switching back must return the exact
 * material references the loader made, with their own state untouched; glass
 * stays see-through, hidden objects keep hidden edges, and a face split into
 * triangles never shows its diagonal.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

import {
  BoxGeometry, BufferGeometry, Color, DoubleSide, Float32BufferAttribute, GridHelper, Group, Line, LineBasicMaterial, Mesh,
  MeshPhysicalMaterial, MeshStandardMaterial, ShaderLib, SRGBColorSpace, type Material,
} from "three";

import {
  DEFAULT_MODEL_DISPLAY_STYLE,
  applyDisplayStyle,
  buildFeatureEdgeOverlay,
  captureModelAppearance,
  disposeDisplayMaterials,
  disposeFeatureEdgeOverlay,
  modelingPalette,
  prepareLoadedModel,
  syncFeatureEdgeOverlay,
} from "../src/workspaces/monkeyarch/viewer/modelDisplay.ts";
import { featureEdges } from "../src/workspaces/monkeyarch/viewer/featureEdges.ts";
import { messagesEn } from "../src/i18n/messages.en.ts";
import { messagesZhCN } from "../src/i18n/messages.zh-CN.ts";

const palette = modelingPalette(false);
const edgesOf = (mesh: Mesh) => featureEdges({
  positions: mesh.geometry.getAttribute("position").array,
  index: mesh.geometry.index?.array ?? null,
});

function own(material: Material) {
  const colour = (material as { color?: Color }).color;
  return {
    colour: colour?.getHexString(), opacity: material.opacity, transparent: material.transparent,
    depthWrite: material.depthWrite, side: material.side,
  };
}

/** Surfaces the way the 3DM loader leaves them: a layer-coloured default, a native material, glass and a curve. */
function loadedModel() {
  const root = new Group();
  root.userData.layers = [{ name: "pavilion", visible: true, color: { r: 111, g: 178, b: 96 } }];
  // No render material: the loader's white __DEFAULT, painted with the layer's draw colour.
  const layerColoured = new Mesh(new BoxGeometry(1, 1, 1), new MeshStandardMaterial({ name: "__DEFAULT", color: 0xffffff, metalness: 0.8, side: DoubleSide }));
  layerColoured.name = "no-render-material";
  layerColoured.userData.attributes = { layerIndex: 0, colorSource: { name: "ObjectColorSource_ColorFromLayer" } };
  const stone = new MeshPhysicalMaterial({ name: "stone", color: new Color().setStyle("#bac2b3"), side: DoubleSide });
  stone.userData.id = "native-stone";
  const plinth = new Mesh(new BoxGeometry(4, 4, 0.3), stone);
  plinth.name = "plinth";
  const sharing = new Mesh(new BoxGeometry(1, 1, 1), stone);
  sharing.name = "shares-stone";
  const glassMaterial = new MeshPhysicalMaterial({ name: "glass", color: 0x96c8e1, transparent: true, opacity: 0.4, side: DoubleSide });
  glassMaterial.userData.id = "native-glass";
  const glass = new Mesh(new BoxGeometry(2, 0.02, 1), glassMaterial);
  glass.name = "glass";
  const transmissive = new Mesh(new BoxGeometry(1, 0.02, 1), new MeshPhysicalMaterial({ name: "pbr-glass", transmission: 0.9 }));
  transmissive.name = "pbr-glass";
  const curve = new Line(new BufferGeometry().setAttribute("position", new Float32BufferAttribute([0, 0, 0, 3, 0, 0], 3)),
    new LineBasicMaterial({ color: 0x777777 }));
  curve.name = "curve";
  root.add(layerColoured, plinth, sharing, glass, transmissive, curve);
  prepareLoadedModel(root);
  return { root, layerColoured, plinth, sharing, glass, transmissive, curve };
}

test("Modeling paints matte stand-ins in the file's colours and Original returns the file's exact, untouched materials", () => {
  const model = loadedModel();
  const { root, layerColoured, plinth, sharing, glass, transmissive, curve } = model;
  // The layer colour reached the default material through the file, not through the style.
  assert.equal((layerColoured.material as MeshStandardMaterial).color.getHexString(), new Color().setRGB(111 / 255, 178 / 255, 96 / 255, "srgb").getHexString());
  const appearance = captureModelAppearance(root);
  const originals = new Map([layerColoured, plinth, sharing, glass, transmissive, curve].map((object) => [object, object.material as Material]));
  const before = new Map([...originals.values()].map((material) => [material, own(material)]));
  const derived = new Map<Material, Material>();

  applyDisplayStyle(root, appearance, "modeling", derived, palette);

  for (const [object, original] of originals) {
    assert.notEqual(object.material, original, `${object.name} wears a stand-in`);
    assert.equal((object.material as Material).userData.displayStyle, "modeling");
  }
  assert.equal(plinth.material, sharing.material, "objects sharing a native material share its stand-in");
  const stone = originals.get(plinth) as MeshStandardMaterial, worn = plinth.material as MeshStandardMaterial;
  const lightness = (material: MeshStandardMaterial) => material.color.getHSL({ h: 0, s: 0, l: 0 }, SRGBColorSpace).l;
  const chroma = (material: MeshStandardMaterial) => {
    const { r, g, b } = material.color.getRGB(new Color(), SRGBColorSpace);
    return Math.max(r, g, b) - Math.min(r, g, b);
  };
  assert.ok(lightness(worn) >= lightness(stone), "the file's colour is lifted toward the paper, not darkened");
  assert.ok(chroma(worn) <= chroma(stone) && chroma(worn) > chroma(stone) / 2, "most of the file's colour is kept, never made louder");
  assert.equal(worn.toneMapped, false, "the working colour is not compressed by the file look's tone mapping");
  assert.equal(stone.toneMapped, true);
  assert.notEqual((plinth.material as MeshStandardMaterial).color.getHexString(),
    (layerColoured.material as MeshStandardMaterial).color.getHexString(), "two materials still read apart");
  assert.equal((plinth.material as MeshStandardMaterial).metalness, 0);
  assert.equal((plinth.material as MeshStandardMaterial).polygonOffset, false, "faces keep their true depth against the grid");
  const glassStand = glass.material as MeshStandardMaterial;
  assert.deepEqual([glassStand.transparent, glassStand.opacity, glassStand.depthWrite], [true, 0.4, false], "glass stays as see-through as the file said");
  const pbr = transmissive.material as MeshStandardMaterial;
  assert.ok(pbr.transparent && pbr.opacity < 1 && !pbr.depthWrite, "transmission glass stays see-through");
  assert.equal((curve.material as LineBasicMaterial).color.getHexString(), new Color(palette.edge).getHexString());
  for (const [material, state] of before) assert.deepEqual(own(material), state, `${material.name} itself is unchanged`);

  applyDisplayStyle(root, appearance, "original", derived, palette);
  for (const [object, original] of originals) assert.equal(object.material, original, `${object.name} has its own material back`);
  for (const [material, state] of before) assert.deepEqual(own(material), state);

  // Switching again reuses the stand-ins; disposing them never disposes the file's own.
  applyDisplayStyle(root, appearance, "modeling", derived, palette);
  const disposed: Material[] = [];
  for (const material of [...derived.values(), ...originals.values()]) material.addEventListener("dispose", () => disposed.push(material));
  applyDisplayStyle(root, appearance, "original", derived, palette);
  disposeDisplayMaterials(derived);
  assert.equal(derived.size, 0);
  assert.ok(disposed.length > 0 && disposed.every((material) => material.userData.displayStyle === "modeling"));
});

test("a dark shell gets a dark canvas and dimmer pale surfaces", () => {
  const light = modelingPalette(false), dark = modelingPalette(true);
  const l = (value: string) => new Color(value).getHSL({ h: 0, s: 0, l: 0 }, SRGBColorSpace).l;
  assert.ok(l(dark.background) < 0.2 && l(light.background) > 0.85);
  assert.ok(l(dark.paper) < l(light.paper) && l(dark.paper) > 0.7);
  assert.ok(l(dark.edge) < l(dark.paper) / 2 && l(light.edge) < l(light.paper) / 2, "edges stay dark against surfaces in both themes");
});

test("feature edges show folds and boundaries only, and follow visibility, placement and selection", () => {
  const model = loadedModel();
  const { root, plinth, sharing, glass, curve } = model;
  const appearance = captureModelAppearance(root);
  const overlay = buildFeatureEdgeOverlay(root, appearance, edgesOf, palette, "#356b9e");

  // A box is 12 triangles and 18 triangle sides; only its 12 real edges are drawn.
  const plinthLine = overlay.lines.find((entry) => entry.source === plinth)!;
  assert.equal(plinthLine.line.geometry.getAttribute("position").count, 24);
  assert.equal(overlay.lines.find((entry) => entry.source === sharing)!.line.geometry.getAttribute("position").count, 24);
  assert.ok(!overlay.lines.some((entry) => entry.source === (curve as unknown as Mesh)), "authored curves draw themselves");
  assert.equal(overlay.group.parent, null, "edges are not part of the model");
  assert.equal(root.getObjectByName("plinth:edges"), undefined, "picking and snapping never meet an edge line");
  const glassLine = overlay.lines.find((entry) => entry.source === glass)!;
  assert.equal(glassLine.translucent, true);
  // Edges, not faces, carry the depth bias: the real three.js line shader gets pulled toward the camera.
  for (const material of Object.values(overlay.materials)) {
    const shader = { vertexShader: ShaderLib.basic.vertexShader, fragmentShader: "", uniforms: {} } as Parameters<typeof material.onBeforeCompile>[0];
    material.onBeforeCompile(shader, undefined as never);
    assert.match(shader.vertexShader, /#include <project_vertex>\n\tgl_Position\.z -= 2e-6 \* gl_Position\.w;/);
    assert.equal(material.customProgramCacheKey(), "archflow-feature-edge");
  }

  syncFeatureEdgeOverlay(overlay, new Set());
  assert.equal(plinthLine.line.visible, true);
  assert.equal(plinthLine.line.material, overlay.materials.solid);
  assert.equal(glassLine.line.material, overlay.materials.translucent);
  assert.ok(overlay.materials.translucent.opacity < 1 && overlay.materials.translucent.opacity > 0);

  plinth.visible = false;
  syncFeatureEdgeOverlay(overlay, new Set());
  assert.equal(plinthLine.line.visible, false, "a hidden object shows no edges");
  plinth.visible = true;
  root.visible = false;
  syncFeatureEdgeOverlay(overlay, new Set());
  assert.equal(plinthLine.line.visible, false, "nor does one under a hidden parent");
  root.visible = true;

  plinth.position.set(5, 0, 0);
  syncFeatureEdgeOverlay(overlay, new Set([plinth]));
  assert.equal(plinthLine.line.visible, true);
  assert.equal(plinthLine.line.matrix.elements[12], 5, "edges sit where their surface sits");
  assert.equal(plinthLine.line.material, overlay.materials.selected, "a marked surface marks its edges");

  syncFeatureEdgeOverlay(overlay, new Set(), 0.5);
  assert.equal(overlay.materials.solid.opacity, 0.5);
  assert.equal(overlay.materials.solid.transparent, true);
  syncFeatureEdgeOverlay(overlay, new Set(), 1);
  assert.equal(overlay.materials.solid.opacity, 1);
  assert.equal(overlay.materials.solid.transparent, false);
  assert.equal(overlay.materials.translucent.opacity, palette.translucentEdgeOpacity);

  const disposed: string[] = [];
  for (const material of Object.values(overlay.materials)) material.addEventListener("dispose", () => disposed.push("material"));
  root.add(overlay.group);
  disposeFeatureEdgeOverlay(overlay);
  assert.equal(overlay.group.parent, null);
  assert.equal(disposed.length, 3);
});

test("a flat face split into triangles keeps its outline but loses its diagonal", () => {
  const quad = new Mesh(new BufferGeometry().setAttribute("position", new Float32BufferAttribute([
    0, 0, 0, 6, 0, 0, 6, 0, 4,
    0, 0, 0, 6, 0, 4, 0, 0, 4,
  ], 3)), new MeshStandardMaterial());
  const root = new Group(); root.add(quad);
  const overlay = buildFeatureEdgeOverlay(root, captureModelAppearance(root), edgesOf, palette, "#356b9e");
  const positions = overlay.lines[0]!.line.geometry.getAttribute("position");
  assert.equal(positions.count, 8, "four boundary edges, no diagonal");
  for (let index = 0; index < positions.count; index += 2) {
    const a = [positions.getX(index), positions.getZ(index)], b = [positions.getX(index + 1), positions.getZ(index + 1)];
    assert.ok(a[0] === b[0] || a[1] === b[1], `edge ${a} -> ${b} is a side, not the diagonal`);
  }
  disposeFeatureEdgeOverlay(overlay);
});

test("Modeling is the default, and both looks are named in both languages", () => {
  assert.equal(DEFAULT_MODEL_DISPLAY_STYLE, "modeling");
  for (const [catalog, words] of [[messagesEn, ["Display", "Modeling", "Original materials"]], [messagesZhCN, ["显示方式", "建模显示", "原始材质"]]] as const) {
    assert.deepEqual([catalog["stage.display.label"], catalog["stage.display.modeling"], catalog["stage.display.original"]], words);
    assert.ok(catalog["stage.display.modelingTitle"] && catalog["stage.display.originalTitle"]);
  }
});

test("the neutral Modeling canvas hides the ground grid; Original keeps it", () => {
  const viewer = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const body = viewer.slice(viewer.indexOf("function paintStage("), viewer.indexOf("/**", viewer.indexOf("function paintStage(")));
  assert.ok(body.startsWith("function paintStage("));
  const paintStage = new Function("currentPalette", "themeColours", stripTypeScriptTypes(body) + "; return paintStage;")(
    () => modelingPalette(false), () => ({ viewport: "#2b2b2b" }));
  const runtime = { style: "modeling", background: new Color(), grid: new GridHelper(4, 4), nativeLights: new Group(), modelingLights: new Group() };
  paintStage(runtime);
  assert.deepEqual([runtime.grid.visible, runtime.nativeLights.visible, runtime.modelingLights.visible], [false, false, true]);
  assert.equal(runtime.background.getHexString(), new Color(palette.background).getHexString());
  runtime.style = "original";
  paintStage(runtime);
  assert.deepEqual([runtime.grid.visible, runtime.nativeLights.visible, runtime.modelingLights.visible], [true, true, false]);
  assert.equal(runtime.background.getHexString(), new Color("#2b2b2b").getHexString());
  runtime.grid.dispose();
});
