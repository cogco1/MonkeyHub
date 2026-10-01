/**
 * The display looks: Modeling's pale stand-ins over the file's own materials and
 * thin feature edges beside the model, and Presentation's matte surfaces in the
 * declared colours with a hatch over what declares no material (#562). Switching
 * back must return the exact material references the loader made, with their own
 * state and shadow flags untouched; glass stays see-through, hidden objects keep
 * hidden edges, and a face split into triangles never shows its diagonal.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

import {
  Box3, BoxGeometry, BufferGeometry, Color, DirectionalLight, DoubleSide, Float32BufferAttribute, GridHelper, Group, Line,
  LineBasicMaterial, Mesh, MeshLambertMaterial, MeshPhysicalMaterial, MeshStandardMaterial, OrthographicCamera, PerspectiveCamera,
  PlaneGeometry, Quaternion, ShaderLib, ShadowMaterial, Sphere, SRGBColorSpace, Vector3, type Material, type Object3D,
} from "three";

import {
  DEFAULT_MODEL_DISPLAY_STYLE,
  DISPLAY_STYLE_STORAGE_KEY,
  MODEL_DISPLAY_STYLES,
  PRESENTATION_PALETTE,
  UndeclaredSurfaceMaterial,
  applyDisplayStyle,
  buildFeatureEdgeOverlay,
  captureModelAppearance,
  declaresNoMaterial,
  disposeDisplayMaterials,
  disposeFeatureEdgeOverlay,
  modelingPalette,
  prepareLoadedModel,
  presentationLegend,
  presentationMaterial,
  rememberDisplayStyle,
  rememberedDisplayStyle,
  restoreModelAppearance,
  syncFeatureEdgeOverlay,
} from "../src/workspaces/monkeyarch/viewer/modelDisplay.ts";
import { projectionMode } from "../src/workspaces/monkeyarch/viewer/cameraProjection.ts";
import { toUserStrings, userStringCarrier } from "../src/workspaces/monkeyarch/viewer/sceneInspection.ts";
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
  layerColoured.userData.attributes = { layerIndex: 0, colorSource: { name: "ObjectColorSource_ColorFromLayer" },
    userStrings: [["archflow:material_status", "undeclared"]] };
  // A material from the file's table, its colour built from the saved sRGB channels as the loader builds it.
  const stone = new MeshPhysicalMaterial({ name: "stone", color: new Color(0xba / 255, 0xc2 / 255, 0xb3 / 255), side: DoubleSide });
  stone.userData.id = "native-stone";
  const plinth = new Mesh(new BoxGeometry(4, 4, 0.3), stone);
  plinth.name = "plinth";
  plinth.userData.attributes = { castsShadows: true, receivesShadows: true, userStrings: [["archflow:material", "stone"]] };
  const sharing = new Mesh(new BoxGeometry(1, 1, 1), stone);
  sharing.name = "shares-stone";
  sharing.userData.attributes = { castsShadows: false, receivesShadows: true };
  const glassMaterial = new MeshPhysicalMaterial({ name: "glass", color: new Color(0x96 / 255, 0xc8 / 255, 0xe1 / 255),
    transparent: true, opacity: 0.4, side: DoubleSide });
  glassMaterial.userData.id = "native-glass";
  const glass = new Mesh(new BoxGeometry(2, 0.02, 1), glassMaterial);
  glass.name = "glass";
  const transmissive = new Mesh(new BoxGeometry(1, 0.02, 1), new MeshPhysicalMaterial({ name: "pbr-glass", transmission: 0.9 }));
  transmissive.name = "pbr-glass";
  const curve = new Line(new BufferGeometry().setAttribute("position", new Float32BufferAttribute([0, 0, 0, 3, 0, 0], 3)),
    new LineBasicMaterial({ color: 0x777777 }));
  curve.name = "curve";
  // The loader sets each surface's flags from the file's attributes.
  plinth.castShadow = plinth.receiveShadow = true;
  sharing.receiveShadow = true;
  root.add(layerColoured, plinth, sharing, glass, transmissive, curve);
  prepareLoadedModel(root);
  return { root, layerColoured, plinth, sharing, glass, transmissive, curve };
}

/** What the viewer asks of each object: does the export say its components declare no material? */
const undeclared = (object: Object3D) => declaresNoMaterial(toUserStrings(userStringCarrier(object)?.userData.attributes?.userStrings));

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

test("Presentation paints matte surfaces in the declared colours, hatches what declares none, and Original gets every material and flag back", () => {
  const { root, layerColoured, plinth, sharing, glass, transmissive, curve } = loadedModel();
  const appearance = captureModelAppearance(root);
  const objects = [layerColoured, plinth, sharing, glass, transmissive, curve];
  const originals = new Map(objects.map((object) => [object, object.material as Material]));
  const before = new Map([...originals.values()].map((material) => [material, own(material)]));
  const flags = (mesh: Mesh) => [mesh.castShadow, mesh.receiveShadow];
  const loadedFlags = new Map(objects.filter((object): object is Mesh => object instanceof Mesh).map((mesh) => [mesh, flags(mesh)]));
  const derived = new Map<Material, Material>();

  applyDisplayStyle(root, appearance, "presentation", derived, palette, undeclared);

  for (const object of objects) assert.equal((object.material as Material).userData.displayStyle, "presentation", object.name);
  const stone = plinth.material as MeshLambertMaterial;
  assert.ok(stone instanceof MeshLambertMaterial && !(stone instanceof UndeclaredSurfaceMaterial), "a declared material is a plain matte surface");
  assert.equal(sharing.material, stone, "objects sharing a material share its stand-in");
  assert.equal(stone.color.getHexString(SRGBColorSpace), "bac2b3", "the declared colour itself, not lifted toward paper");
  assert.equal(stone.toneMapped, false, "lit, never compressed by the file look's filmic curve");
  const bench = layerColoured.material as UndeclaredSurfaceMaterial;
  assert.ok(bench instanceof UndeclaredSurfaceMaterial, "a surface whose components declare no material is hatched");
  assert.equal(bench.color.getHexString(SRGBColorSpace), new Color().setRGB(111 / 255, 178 / 255, 96 / 255, SRGBColorSpace).getHexString(SRGBColorSpace),
    "it keeps its distinction colour; nothing is recoloured by a name");
  const glassStand = glass.material as MeshLambertMaterial;
  assert.equal(glassStand.color.getHexString(SRGBColorSpace), "96c8e1");
  assert.deepEqual([glassStand.transparent, glassStand.opacity, glassStand.depthWrite], [true, 0.4, false], "glass stays as see-through as the file said");
  assert.ok((transmissive.material as Material).transparent, "transmission glass stays see-through");
  assert.equal((curve.material as LineBasicMaterial).color.getHexString(SRGBColorSpace), "777777", "a curve keeps its own colour");
  // The sun's shadow: from what the file lets cast and one cannot see through; every surface the file lets receive it.
  assert.deepEqual(flags(plinth), [true, true]);
  assert.deepEqual(flags(sharing), [false, true], "the file said this one casts no shadow");
  assert.deepEqual(flags(glass), [false, true], "glass casts no shadow");
  assert.deepEqual(flags(transmissive), [false, true]);
  assert.deepEqual(flags(layerColoured), [true, true], "Rhino's own default is to cast and receive");
  for (const [material, state] of before) assert.deepEqual(own(material), state, `${material.name} itself is unchanged`);

  // A selection mark clones what an object wears; the copy of a hatched surface is hatched too.
  const marked = bench.clone();
  assert.ok(marked instanceof UndeclaredSurfaceMaterial && marked.customProgramCacheKey() === "archflow-undeclared-hatch");
  marked.dispose();

  applyDisplayStyle(root, appearance, "original", derived, palette, undeclared);
  for (const [object, original] of originals) assert.equal(object.material, original, `${object.name} has its own material back`);
  for (const [mesh, state] of loadedFlags) assert.deepEqual(flags(mesh), state, `${mesh.name} has its own shadow flags back`);
  for (const [material, state] of before) assert.deepEqual(own(material), state);
  const disposed: Material[] = [];
  for (const material of [...derived.values(), ...originals.values()]) material.addEventListener("dispose", () => disposed.push(material));
  disposeDisplayMaterials(derived);
  assert.equal(derived.size, 0);
  assert.ok(disposed.includes(bench) && disposed.includes(stone), "the hatched and plain stand-ins go with the style");
  assert.ok(disposed.every((material) => material.userData.displayStyle === "presentation"), "the file's own materials are never disposed here");

  // Moved surfaces and switched layers aside, restoring the file's appearance also gives the flags back.
  applyDisplayStyle(root, appearance, "presentation", derived, palette, undeclared);
  restoreModelAppearance(root, appearance);
  for (const [mesh, state] of loadedFlags) assert.deepEqual(flags(mesh), state);
  disposeDisplayMaterials(derived);
});

test("the undeclared hatch is laid into Presentation's matte shader, before its output, under its own program key", () => {
  const hatched = presentationMaterial(new MeshStandardMaterial({ color: 0x6fb260 }), true);
  const plain = presentationMaterial(new MeshStandardMaterial({ color: 0x6fb260 }));
  assert.ok(hatched instanceof UndeclaredSurfaceMaterial && plain instanceof MeshLambertMaterial && !(plain instanceof UndeclaredSurfaceMaterial));
  const shader = { vertexShader: ShaderLib.lambert.vertexShader, fragmentShader: ShaderLib.lambert.fragmentShader, uniforms: {} } as
    Parameters<UndeclaredSurfaceMaterial["onBeforeCompile"]>[0];
  hatched.onBeforeCompile(shader, undefined as never);
  assert.match(shader.fragmentShader, /outgoingLight = [^\n]+\n[\s\S]*archflowUndeclared = 1\.0 - step\( 1\.5, mod\( gl_FragCoord\.x \+ gl_FragCoord\.y, 7\.0 \) \);[\s\S]*outgoingLight \*= 1\.0 - 0\.30 \* archflowUndeclared;[\s\S]*#include <opaque_fragment>/);
  assert.equal(shader.fragmentShader.split("archflowUndeclared =").length, 2, "laid once");
  assert.notEqual(hatched.customProgramCacheKey(), plain.customProgramCacheKey(), "a hatched surface never reuses a plain surface's program");
  hatched.dispose(); plain.dispose();
});

test("Presentation's legend names the materials it paints and counts what declares none, from the file alone", () => {
  const { root, layerColoured } = loadedModel();
  const hidden = layerColoured.clone();
  hidden.material = (layerColoured.material as Material).clone();
  hidden.visible = false;
  root.add(hidden);
  const appearance = captureModelAppearance(root);
  assert.deepEqual(presentationLegend(root, appearance, undeclared), {
    materials: [{ name: "glass", colour: "#96c8e1" }, { name: "stone", colour: "#bac2b3" }],
    undeclared: 1,
  }, "two materials of the file's table, shown once each; an object the file hid is not part of the picture");
  assert.deepEqual(presentationLegend(new Group(), captureModelAppearance(new Group()), undeclared), { materials: [], undeclared: 0 });
});

test("the chosen display style survives a refresh in this browser, and an unreadable or unknown choice opens the default", () => {
  assert.deepEqual(MODEL_DISPLAY_STYLES, ["modeling", "original", "presentation"]);
  const saved = new Map<string, string>();
  const storage = { getItem: (key: string) => saved.get(key) ?? null, setItem: (key: string, value: string) => { saved.set(key, value); } };
  assert.equal(rememberedDisplayStyle(storage), DEFAULT_MODEL_DISPLAY_STYLE);
  rememberDisplayStyle("presentation", storage);
  assert.deepEqual([...saved], [[DISPLAY_STYLE_STORAGE_KEY, "presentation"]]);
  assert.equal(rememberedDisplayStyle(storage), "presentation", "a refreshed page opens in it");
  rememberDisplayStyle("original", storage);
  assert.equal(rememberedDisplayStyle(storage), "original");
  saved.set(DISPLAY_STYLE_STORAGE_KEY, "wireframe");
  assert.equal(rememberedDisplayStyle(storage), DEFAULT_MODEL_DISPLAY_STYLE, "a style this viewer does not know");
  const blocked = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(rememberedDisplayStyle(blocked), DEFAULT_MODEL_DISPLAY_STYLE);
  assert.doesNotThrow(() => rememberDisplayStyle("presentation", blocked), "the choice still holds on the page");
  assert.equal(rememberedDisplayStyle(null), DEFAULT_MODEL_DISPLAY_STYLE);
  assert.equal(rememberedDisplayStyle(), DEFAULT_MODEL_DISPLAY_STYLE, "no browser storage outside a browser");
});

/** Production module-level functions of the viewer, run on their own: the fakes are the runtime's fields only. */
function viewerFunctions(from: string, to: string, scope: Record<string, unknown>, names: readonly string[]) {
  const viewer = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const start = viewer.indexOf(from), end = viewer.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `${from} … ${to}`);
  return new Function(...Object.keys(scope), `${stripTypeScriptTypes(viewer.slice(start, end))}; return { ${names.join(", ")} };`)(...Object.values(scope));
}

test("leaving Presentation stands the view exactly where it was: projection, both cameras and the orbit target", () => {
  const { snapshotCamera, restoreCamera } = viewerFunctions("function cameraPose(", "/**\n * Presentation's light",
    { PerspectiveCamera, projectionMode }, ["snapshotCamera", "restoreCamera"]);
  const perspectiveCamera = new PerspectiveCamera(38, 1.6, 0.01, 10000);
  perspectiveCamera.up.set(0, 0, 1);
  perspectiveCamera.position.set(12.3, -8.7, 6.1);
  perspectiveCamera.lookAt(1, 2, 0.5);
  perspectiveCamera.updateMatrixWorld();
  const orthographicCamera = new OrthographicCamera(-1.6, 1.6, 1, -1, 0.01, 10000);
  orthographicCamera.up.set(0, 0, 1);
  const cameraOf = (camera: PerspectiveCamera | OrthographicCamera) => ({ position: camera.position.toArray(), quaternion: camera.quaternion.toArray(),
    up: camera.up.toArray(), zoom: camera.zoom, near: camera.near, far: camera.far, projection: camera.projectionMatrix.toArray() });
  const translations: unknown[] = [];
  const runtime = { camera: perspectiveCamera as PerspectiveCamera | OrthographicCamera, perspectiveCamera, orthographicCamera,
    controls: { target: new Vector3(1, 2, 0.5), object: perspectiveCamera as unknown }, translation: { setCamera: (camera: unknown) => translations.push(camera) } };
  const before = { perspective: cameraOf(perspectiveCamera), orthographic: cameraOf(orthographicCamera), target: runtime.controls.target.toArray() };
  const snapshot = snapshotCamera(runtime);

  // In Presentation: the axonometric preset, then a wheel zoom and an orbit.
  runtime.camera = orthographicCamera;
  runtime.controls.object = orthographicCamera;
  orthographicCamera.position.set(20, -20, 20);
  orthographicCamera.zoom = 0.37;
  orthographicCamera.far = 4321;
  orthographicCamera.quaternion.setFromAxisAngle(new Vector3(1, 1, 0).normalize(), 0.8);
  orthographicCamera.updateProjectionMatrix();
  perspectiveCamera.position.set(3, 3, 3);
  perspectiveCamera.fov = 50;
  perspectiveCamera.updateProjectionMatrix();
  runtime.controls.target.set(9, 9, 9);

  restoreCamera(runtime, snapshot);
  assert.equal(runtime.camera, perspectiveCamera, "the projection that was on");
  assert.equal(runtime.controls.object, perspectiveCamera);
  assert.deepEqual(translations, [perspectiveCamera], "the move handles follow the camera");
  assert.deepEqual({ perspective: cameraOf(perspectiveCamera), orthographic: cameraOf(orthographicCamera), target: runtime.controls.target.toArray() },
    before, "bit for bit: nothing went through the orbit's spherical round trip");
  assert.equal(perspectiveCamera.fov, 38);
  assert.ok(new Quaternion().fromArray(before.perspective.quaternion).equals(perspectiveCamera.quaternion));
});

test("Presentation's sun frames the model's bounding sphere at any scale, and its ground lies just under the model", () => {
  const scope = { PRESENTATION_SUN: new Vector3(-1, -1.5, 3.2).normalize(), Box3, Sphere, declaresNoMaterial };
  const { placeSun } = viewerFunctions("function undeclaredObject(", "/** The sun, the ground and the legend", scope, ["placeSun"]);
  for (const [scale, origin] of [[1, [0, 0, 0]], [1000, [5000, -2000, 300]], [0.02, [1, 1, 1]]] as const) {
    const model = new Mesh(new BoxGeometry(8 * scale, 5 * scale, 6 * scale), new MeshLambertMaterial());
    model.position.set(origin[0], origin[1], origin[2] + 3 * scale);
    model.updateMatrixWorld();
    const sun = new DirectionalLight(0xffffff, 1);
    sun.shadow.mapSize.set(2048, 2048);
    const shadowGround = new Mesh(new PlaneGeometry(1, 1), new ShadowMaterial());
    const runtime = { style: "presentation", model, sun, shadowGround };
    placeSun(runtime);
    const box = new Box3().setFromObject(model), sphere = box.getBoundingSphere(new Sphere());
    const frame = sun.shadow.camera;
    sun.shadow.updateMatrices(sun);
    // Every corner of the model is inside the sun's view: across, up and in depth.
    for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
      const seen = new Vector3(x, y, z).applyMatrix4(frame.matrixWorldInverse), edge = frame.right * (1 + 1e-9);
      assert.ok(Math.abs(seen.x) <= edge && Math.abs(seen.y) <= edge && -seen.z >= frame.near && -seen.z <= frame.far, `scale ${scale}: ${x},${y},${z}`);
    }
    assert.ok(frame.right <= sphere.radius * 1.0001, "no wider than the model needs: the whole map is spent on it");
    assert.ok(sun.position.clone().sub(sphere.center).normalize().dot(scope.PRESENTATION_SUN) > 0.9999, "the sun stands where the look lit from");
    assert.ok(Math.abs(sun.shadow.normalBias - 1.5 * 2 * sphere.radius / 2048) < 1e-12, "the self-shadow bias follows a texel");
    assert.equal(shadowGround.visible, true);
    assert.ok(shadowGround.position.z < box.min.z && box.min.z - shadowGround.position.z <= sphere.radius * 1e-3, "just under the lowest point");
    assert.ok(shadowGround.scale.x >= 6 * sphere.radius, "wide enough for the shadow the sun casts");
    runtime.style = "modeling";
    placeSun(runtime);
    assert.equal(shadowGround.visible, false, "outside Presentation there is no ground");
    runtime.style = "presentation";
    Object.assign(runtime, { model: null });
    placeSun(runtime);
    assert.equal(shadowGround.visible, false, "nor without a model");
  }
});

test("a selection mark lifts Presentation's matte surfaces like Modeling's, and keeps the hatch", () => {
  const { highlightMaterial } = viewerFunctions("function highlightMaterial(", "/** Give every highlighted mesh back",
    { MeshStandardMaterial, MeshLambertMaterial, LineBasicMaterial, Color }, ["highlightMaterial"]);
  const accent = new Color("#2f80ed");
  for (const material of [presentationMaterial(new MeshStandardMaterial({ color: 0xbac2b3 })), presentationMaterial(new MeshStandardMaterial(), true)]) {
    const lit = highlightMaterial(material, accent);
    assert.equal(lit.emissive.getHexString(), accent.getHexString(), material.type);
    assert.ok(lit.emissiveIntensity > 0);
    assert.equal(lit instanceof UndeclaredSurfaceMaterial, material instanceof UndeclaredSurfaceMaterial);
    lit.dispose(); material.dispose();
  }
});

test("Presentation's faint edges are drawn over its surfaces, fainter still over glass", () => {
  const { root, plinth, glass } = loadedModel();
  const overlay = buildFeatureEdgeOverlay(root, captureModelAppearance(root), edgesOf, PRESENTATION_PALETTE, "#356b9e");
  const solid = overlay.lines.find((entry) => entry.source === plinth)!, translucent = overlay.lines.find((entry) => entry.source === glass)!;
  syncFeatureEdgeOverlay(overlay, new Set());
  assert.equal(solid.line.material, overlay.materials.solid);
  assert.deepEqual([overlay.materials.solid.opacity, overlay.materials.solid.transparent], [PRESENTATION_PALETTE.edgeOpacity, true]);
  assert.equal(translucent.line.material, overlay.materials.translucent);
  assert.ok(overlay.materials.translucent.opacity < overlay.materials.solid.opacity);
  syncFeatureEdgeOverlay(overlay, new Set([plinth]));
  assert.equal(solid.line.material, overlay.materials.selected, "a marked surface's edges are the selection's, fully drawn");
  assert.equal(overlay.materials.selected.opacity, 1);
  syncFeatureEdgeOverlay(overlay, new Set(), 0.5);
  assert.ok(Math.abs(overlay.materials.solid.opacity - PRESENTATION_PALETTE.edgeOpacity! / 2) < 1e-12, "a comparison fades them with their model");
  disposeFeatureEdgeOverlay(overlay);
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

test("Modeling is the default, and every look is named and explained in both languages", () => {
  assert.equal(DEFAULT_MODEL_DISPLAY_STYLE, "modeling");
  for (const [catalog, words] of [[messagesEn, ["Display", "Modeling", "Original materials", "Presentation"]],
    [messagesZhCN, ["显示方式", "建模显示", "原始材质", "展示"]]] as const) {
    assert.deepEqual([catalog["stage.display.label"], catalog["stage.display.modeling"], catalog["stage.display.original"],
      catalog["stage.display.presentation"]], words);
    assert.ok(catalog["stage.display.modelingTitle"] && catalog["stage.display.originalTitle"] && catalog["stage.display.presentationTitle"]);
    for (const view of ["front", "side", "axonometric"] as const) {
      assert.ok(catalog[`stage.presentation.${view}`] && catalog[`stage.presentation.${view}Title`], view);
    }
  }
  assert.match(messagesZhCN["stage.display.presentationTitle"], /声明.*颜色.*阴影.*正面、侧面、轴测.*未声明材料.*斜线/s);
  assert.match(messagesEn["stage.display.presentationTitle"], /declare.*shadows.*front, side and axonometric.*no declared material.*hatched/s);
});

test("each look has its own canvas and lights; only Presentation's sun casts a shadow, and leaving it puts the others back", () => {
  const viewer = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const body = viewer.slice(viewer.indexOf("function paintStage("), viewer.indexOf("/**", viewer.indexOf("function paintStage(")));
  assert.ok(body.startsWith("function paintStage("));
  const paintStage = new Function("currentPalette", "themeColours", "PRESENTATION_PALETTE", stripTypeScriptTypes(body) + "; return paintStage;")(
    () => modelingPalette(false), () => ({ viewport: "#2b2b2b" }), PRESENTATION_PALETTE);
  const runtime = { style: "modeling", background: new Color(), grid: new GridHelper(4, 4), nativeLights: new Group(), modelingLights: new Group(),
    presentationStage: new Group(), renderer: { shadowMap: { enabled: false } } };
  const stage = () => [runtime.grid.visible, runtime.nativeLights.visible, runtime.modelingLights.visible, runtime.presentationStage.visible,
    runtime.renderer.shadowMap.enabled, runtime.background.getHexString()];
  paintStage(runtime);
  const modeling = [false, false, true, false, false, new Color(palette.background).getHexString()];
  assert.deepEqual(stage(), modeling);
  runtime.style = "presentation";
  paintStage(runtime);
  assert.deepEqual(stage(), [false, false, false, true, true, new Color(PRESENTATION_PALETTE.background).getHexString()], "paper, the sun and its shadow");
  runtime.style = "original";
  paintStage(runtime);
  assert.deepEqual(stage(), [true, true, false, false, false, new Color("#2b2b2b").getHexString()]);
  runtime.style = "presentation";
  paintStage(runtime);
  runtime.style = "modeling";
  paintStage(runtime);
  assert.deepEqual(stage(), modeling, "Modeling's own canvas and lights, exactly");
  runtime.grid.dispose();
});
