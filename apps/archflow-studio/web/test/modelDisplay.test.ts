import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";

import { Box3, BufferGeometry, Float32BufferAttribute, Line, LineBasicMaterial, LineLoop, LineSegments, BoxGeometry, Color, Group, Matrix3, Matrix4, Mesh, MeshBasicMaterial, MeshStandardMaterial, PerspectiveCamera, Raycaster, Sphere, Texture, Vector2, Vector3 } from "three";

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
} from "../src/workspaces/monkeyarch/viewer/modelDisplay.ts";
import { indexLoadedObjects } from "../src/workspaces/monkeyarch/viewer/sceneInspection.ts";
import { curveEdges, featureEdges } from "../src/workspaces/monkeyarch/viewer/featureEdges.ts";

test("loaded identity groups only explicit file bindings and expires with its model", () => {
  const root = new Group();
  const carrier = (name: string, strings: Record<string, string>) => {
    const object = new Group(); object.name = name;
    object.userData.attributes = { userStrings: strings };
    object.add(new Group()); root.add(object);
    return object;
  };
  const strings = { "archflow:component": "wall", "archflow:object_ref": "cad-object:wall-1" };
  const first = carrier("front", strings), second = carrier("back", { ...strings });
  const sameName = carrier("front", {});
  const otherComponent = carrier("front", { ...strings, "archflow:component": "roof" });
  const index = indexLoadedObjects(root);
  const identity = index.identity(first)!;
  assert.equal(index.identity(first.children[0]!), identity);
  assert.deepEqual(index.siblings(first), [first, second]);
  assert.deepEqual(index.siblings(sameName), [sameName]);
  assert.deepEqual(index.siblings(otherComponent), [otherComponent]);
  assert.ok(Object.isFrozen(identity.userStrings));
  strings["archflow:component"] = "changed after loading";
  assert.equal(identity.userStrings["archflow:component"], "wall");
  assert.equal(indexLoadedObjects(new Group()).identity(first), undefined);
});

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

test("unassigned loader meshes use saved display colors without changing native materials or shared defaults", () => {
  const model = new Group();
  model.userData.layers = [{ color: { r: 12, g: 34, b: 56 } }];
  const defaultMaterial = new MeshStandardMaterial({ name: "__DEFAULT", transparent: true, opacity: 0.4, depthWrite: false });
  const add = (attributes: object, material = defaultMaterial) => {
    const mesh = new Mesh(new BoxGeometry(1, 1, 1), material);
    mesh.userData.attributes = attributes;
    model.add(mesh);
    return mesh;
  };
  const layer = add({ layerIndex: 0, colorSource: { name: "ObjectColorSource_ColorFromLayer" } });
  const object = add({ layerIndex: 0, colorSource: { name: "ObjectColorSource_ColorFromObject" }, objectColor: { r: 180, g: 90, b: 30 } });
  const resolved = add({ drawColor: { r: 0, g: 0, b: 0 }, objectColor: { r: 255, g: 255, b: 255 } });
  const invalid = add({ drawColor: { r: 400, g: 20, b: 30 } });
  const nativeMaterial = defaultMaterial.clone();
  nativeMaterial.userData.id = "native-material-id";
  const native = add({ drawColor: { r: 180, g: 90, b: 30 } }, nativeMaterial);
  const texturedMaterial = defaultMaterial.clone();
  texturedMaterial.map = new Texture();
  const textured = add({ drawColor: { r: 180, g: 90, b: 30 } }, texturedMaterial);
  prepareLoadedModel(model);
  assert.equal(layer.material.color.getHex(), 0x0c2238);
  assert.equal(object.material.color.getHex(), 0xb45a1e);
  assert.equal(resolved.material.color.getHex(), 0x000000);
  assert.notEqual(layer.material, object.material);
  assert.equal(defaultMaterial.color.getHex(), 0xffffff);
  assert.deepEqual([layer.material.transparent, layer.material.opacity, layer.material.depthWrite], [true, 0.4, false]);
  assert.equal(invalid.material, defaultMaterial);
  assert.equal(native.material, nativeMaterial);
  assert.equal(textured.material, texturedMaterial);
  const prepared = layer.material;
  prepareLoadedModel(model);
  assert.equal(layer.material, prepared);
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

// Execute the production callbacks with deferred I/O; no browser, project,
// renderer or replacement implementation of their async transitions is needed.
function productionCallback(file: string, name: string, scope: Record<string, unknown>) {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const declaration = `const ${name} = useCallback(`;
  const declarationAt = source.indexOf(declaration);
  assert.ok(declarationAt >= 0, `Missing production callback ${name}`);
  const start = declarationAt + declaration.length;
  const end = /\r?\n {2,4}},\s*\[/.exec(source.slice(start));
  assert.ok(end, `Missing callback dependency list for ${name}`);
  const callback = source.slice(start, start + end.index + end[0].indexOf("}") + 1);
  return new Function(...Object.keys(scope), `return ${stripTypeScriptTypes(`(${callback})`)}`)(...Object.values(scope));
}

function curvePickingViewport(scale: number[], rotation: number[]) {
  // Execute the real curve helper beside the real hitAt callback, with Three's
  // actual mesh raycaster. The test changes instance transforms, not algorithms.
  const source = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/preselection.ts", import.meta.url), "utf8");
  const helpers = source.slice(source.indexOf("function activePositions"), source.indexOf("/** One neutral outline"));
  const scope = { Line, LineLoop, LineSegments, Vector3, curveEdges, featureEdges };
  const raycastCurve = new Function(...Object.keys(scope),
    `${stripTypeScriptTypes(helpers.replaceAll("export function", "function"))}; return raycastCurve;`)(...Object.values(scope));
  const model = new Group(), parent = new Group();
  parent.scale.fromArray(scale); parent.rotation.set(rotation[0], rotation[1], rotation[2]);
  parent.position.set(1, -0.5, -2); model.add(parent); model.updateMatrixWorld(true);
  const inverse = new Matrix4().copy(parent.matrixWorld).invert();
  const line = new Line(new BufferGeometry().setFromPoints([
    new Vector3(-2, 0, 0).applyMatrix4(inverse), new Vector3(2, 0, 0).applyMatrix4(inverse),
  ]), new LineBasicMaterial());
  line.name = "curve"; parent.add(line);
  const mesh = new Mesh(new BoxGeometry(4, 4, 0.5), new MeshBasicMaterial());
  mesh.name = "body"; mesh.position.z = -2; mesh.visible = false; model.add(mesh);
  model.updateMatrixWorld(true);
  const camera = new PerspectiveCamera(38, 1, 0.01, 1000);
  camera.position.set(0, 0, 10); camera.lookAt(0, 0, 0); camera.updateMatrixWorld();
  const rect = { left: 0, top: 0, width: 800, height: 800 };
  const runtime = { model, camera, modelIndex: indexLoadedObjects(model),
    draftRoot: new Group(), draftObjects: new Map(), draftIds: new WeakMap(), draftBounds: null,
    modelBounds: new Box3().setFromObject(model).getBoundingSphere(new Sphere()),
    renderer: { domElement: { getBoundingClientRect: () => rect } } };
  const rayAt = (x: number, y: number) => {
    const raycaster = new Raycaster();
    raycaster.setFromCamera(new Vector2(x / 400 - 1, 1 - y / 400), camera);
    return raycaster;
  };
  const viewportSource = readFileSync(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const draftIdFor = new Function(stripTypeScriptTypes(viewportSource.slice(
    viewportSource.indexOf("function draftIdFor"), viewportSource.indexOf("function clearDraftPreview"),
  )) + "; return draftIdFor;")();
  const hitAt = productionCallback("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", "hitAt",
    { runtimeRef: { current: runtime }, rayAt, raycastCurve, Line, Matrix3, isDisplayed, draftIdFor });
  return { model, parent, line, mesh, rayAt, hitAt };
}

test("curve picking keeps its eight-pixel reach under scaled and rotated instance parents", () => {
  for (const scale of [[1, 1, 1], [0.1, 0.1, 0.1], [0.01, 0.01, 0.01], [0.01, 0.2, 1.5], [-0.1, 0.3, 0.01]]) {
    const h = curvePickingViewport(scale, [0.24, -0.35, 0.63]);
    const hit = h.hitAt(400, 404);
    assert.equal(hit?.mesh, h.line, `four pixels must hit parent scale ${scale}`);
    assert.ok(Math.abs(hit.point.y) < 1e-5 && Math.abs(hit.point.z) < 1e-5, "pick point retains world coordinates");
    assert.equal(hit.normal, null, "a curve does not pretend to be a face");
    assert.equal(h.hitAt(400, 409), null, `nine pixels must miss parent scale ${scale}`);
    h.parent.visible = false;
    assert.equal(h.hitAt(400, 400), null, "hidden instance parents cannot supply picks");
    h.line.geometry.dispose(); h.line.material.dispose(); h.mesh.geometry.dispose(); h.mesh.material.dispose();
  }
});

test("curves share front-to-back occlusion with unchanged native mesh picking", () => {
  const h = curvePickingViewport([0.01, 0.2, 1.5], [0.24, -0.35, 0.63]);
  h.mesh.visible = true;
  assert.equal(h.hitAt(400, 404)?.mesh, h.line, "a nearer curve is picked before the rear solid");
  h.mesh.position.z = 2; h.model.updateMatrixWorld(true);
  const hit = h.hitAt(400, 404);
  const native = h.rayAt(400, 404).intersectObject(h.mesh, false)[0]!;
  assert.equal(hit?.mesh, h.mesh, "a front solid occludes the curve");
  assert.equal(hit.faceIndex, native.faceIndex);
  assert.ok(hit.point.distanceTo(native.point) < 1e-12, "mesh point remains Three's native intersection");
  assert.ok(hit.normal.distanceTo(native.face!.normal) < 1e-12, "mesh normal is preserved");
  h.mesh.visible = false;
  assert.equal(h.hitAt(400, 404)?.mesh, h.line, "hiding the solid reveals the curve again");
  h.line.geometry.dispose(); h.line.material.dispose(); h.mesh.geometry.dispose(); h.mesh.material.dispose();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const flush = () => new Promise<void>((resolve) => setImmediate(resolve));

test("comparison downloads cannot outlive a model switch, a clear or a newer comparison", async () => {
  for (const action of ["switch", "clear", "newer"] as const) {
    const first = deferred<string>(), second = deferred<string>();
    const displayed: string[] = [];
    const before = { runId: "before", stageId: "seat", sha256: "before-sha" };
    let blend: { candidateId: string } | null = null;
    const scope = {
      modelLoading: false, loadedArtifact: before, loadedArtifactsRef: { current: [before] },
      comparisonRequest: { current: 0 }, modelLoadRequest: { current: 0 },
      artifacts: { status: "ready", value: { artifacts: ["first", "second"].map((id) =>
        ({ runId: id, stageId: "seat", sha256: id, fileName: `${id}.3dm` })) } },
      viewableArtifacts: (rows: unknown) => rows,
      studio: { artifactFile: (sha: string) => sha === "first" ? first.promise : second.promise },
      viewportRef: { current: { clearSecondary() {}, loadSecondary: async (file: string) => { displayed.push(file); return 1; } } },
      setBlendState: (value: typeof blend) => { blend = value; },
      append: (entry: unknown) => assert.fail(JSON.stringify(entry)), asStudioApiError: (value: unknown) => value,
    };
    const clear = productionCallback("../src/app/App.tsx", "clearComparison", scope);
    const compare = productionCallback("../src/app/App.tsx", "compareInModel", { ...scope, clearComparison: clear });
    const old = compare({ candidateId: "first", against: "before" });
    if (action === "switch") {
      scope.modelLoadRequest.current += 1;
      scope.loadedArtifactsRef.current = [{ ...before, runId: "another-model" }];
    } else if (action === "clear") clear();
    else {
      const latest = compare({ candidateId: "second", against: "before" });
      second.resolve("second.3dm");
      await latest;
    }
    first.resolve("first.3dm");
    await old;
    assert.deepEqual(displayed, action === "newer" ? ["second.3dm"] : [], action);
    assert.equal(blend?.candidateId ?? null, action === "newer" ? "second" : null, action);
  }
});

function secondaryViewport() {
  const parsed = new Map<number, (model: Group) => void>();
  const disposed: string[] = [];
  const runtime = { model: new Group(), secondary: null as Group | null,
    scene: new Group(), restore: new Map(), blendT: null as number | null, render() {} };
  const scope = {
    runtimeRef: { current: runtime }, loadGenerationRef: { current: 0 }, secondaryLoadRequest: { current: 0 },
    Rhino3dmLoader: class {
      setLibraryPath() {} setWorkerLimit() {} dispose() {}
      parse(bytes: ArrayBuffer, complete: (model: Group) => void) { parsed.set(new Uint8Array(bytes)[0], complete); }
    },
    navigator: { hardwareConcurrency: 1 }, Color, accentColour: () => "#2277dd",
    meshCount: () => 1, prepareLoadedModel, tintSecondary() {}, reportStatus() {},
    nurbsFallbackWarning: () => null, restoreOpacity,
    disposeScene: (model: Group) => disposed.push(model.name),
    disposeSecondary: (model: Group) => disposed.push(model.name),
    blend: (value: number) => { runtime.blendT = value; },
  };
  const clear = productionCallback("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", "clearSecondary", scope);
  const load = productionCallback("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", "loadSecondary", { ...scope, clearSecondary: clear });
  const file = (id: number) => ({ arrayBuffer: async () => Uint8Array.of(id).buffer });
  const complete = (id: number) => {
    const model = new Group(); model.name = `comparison-${id}`;
    assert.ok(parsed.has(id)); parsed.get(id)!(model);
  };
  return { ...scope, runtime, parsed, disposed, load, clear, file, complete };
}

test("secondary file reading stays bound to the primary model present before the await", async () => {
  const h = secondaryViewport();
  const bytes = deferred<ArrayBuffer>();
  const loading = h.load({ arrayBuffer: () => bytes.promise });
  h.loadGenerationRef.current += 1;
  h.runtime.model = new Group();
  bytes.resolve(Uint8Array.of(1).buffer);
  assert.equal(await loading, 0);
  assert.equal(h.parsed.size, 0, "an obsolete file is not handed to the parser");
  assert.equal(h.runtime.secondary, null);
});

test("clearing a comparison prevents a late parse from restoring it", async () => {
  const h = secondaryViewport();
  const loading = h.load(h.file(1));
  await flush();
  h.clear();
  h.complete(1);
  assert.equal(await loading, 0);
  assert.equal(h.runtime.secondary, null);
  assert.equal(h.runtime.blendT, null);
  assert.deepEqual(h.disposed, ["comparison-1"]);
});

test("the last secondary request wins when its parse finishes before the earlier request", async () => {
  const h = secondaryViewport();
  const first = h.load(h.file(1));
  await flush();
  const second = h.load(h.file(2));
  await flush();
  h.complete(2);
  assert.equal(await second, 1);
  h.complete(1);
  assert.equal(await first, 0);
  assert.equal(h.runtime.secondary?.name, "comparison-2");
  assert.equal(h.runtime.blendT, 0.5);
  assert.deepEqual(h.disposed, ["comparison-1"]);
});


test("saved model curves restore their original visibility and material after a temporary selection", () => {
  const geometry = new BufferGeometry().setAttribute("position", new Float32BufferAttribute([0, 0, 0, 3, 4, 0], 3));
  const material = new LineBasicMaterial({ color: "#777777" });
  const line = new Line(geometry, material);
  const root = new Group(); root.add(line);
  const appearance = captureModelAppearance(root);
  const selected = material.clone(); line.material = selected; line.visible = false;
  restoreModelAppearance(root, appearance);
  assert.equal(line.visible, true);
  assert.equal(line.material, material);
  selected.dispose(); material.dispose(); geometry.dispose();
});
