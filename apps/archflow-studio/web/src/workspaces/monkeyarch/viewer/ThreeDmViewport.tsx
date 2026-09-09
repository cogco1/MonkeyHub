import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  ACESFilmicToneMapping,
  AmbientLight,
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  GridHelper,
  Group,
  LineSegments,
  LineBasicMaterial,
  Material,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  PerspectiveCamera,
  Plane,
  Raycaster,
  Scene,
  ShapeUtils,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Rhino3dmLoader } from "three/examples/jsm/loaders/3DMLoader.js";

import {
  disposeScene,
  documentUserStrings,
  inspectScene,
  layerIndexOf,
  toUserStrings,
  userStringCarrier,
  type SceneInspection,
  type UserStrings,
} from "./sceneInspection";
import {
  captureModelAppearance,
  fadeOpacity,
  isDisplayed,
  matchesSemanticCarrier,
  prepareLoadedModel,
  restoreModelAppearance,
  restoreOpacity,
  savedObjectVisible,
  type MaterialOpacity,
  type ModelAppearance,
  type SemanticHighlightTarget,
} from "./modelDisplay";
import { encodeViewportPng } from "./viewportScreenshot";

export type ViewportStatus = "idle" | "loading" | "ready" | "error";

/** What a file opened from this machine, bound to nothing, is labelled. */
export const LOCAL_SOURCE_LABEL = "LOCAL · UNBOUND";

/**
 * One click, as read off the loaded file.
 *
 * Nothing here is interpreted: these are the strings the file carries and the
 * name the object was given. What they mean is the server's answer, and it is
 * `POST /api/pick/resolve` that gives it.
 */
export interface ViewportPick {
  userStrings: UserStrings;
  documentUserStrings: UserStrings | null;
  objectName: string | null;
  /**
   * The object the ray met, so the shell can ask for it to be marked while
   * the server is deciding what it is. It is the loaded scene's own object:
   * hold no reference to it past the picture it belongs to.
   */
  object: Object3D;
}

/**
 * One element of the record and the exact objects the server catalog bound to
 * it in the model run currently on screen.
 */
export type GhostTarget = SemanticHighlightTarget;

/**
 * A ghost of a proposal: a translucent copy of the target's objects, scaled
 * along world Z by ``factor`` when the changed field is a height (about the
 * copy's own bottom), unscaled otherwise; the affected elements drawn as a
 * fainter cloud. It is a drawing of the proposal's numbers - approximate,
 * never geometry the record certified - and the shell labels it so.
 */
export interface GhostSpec {
  target: GhostTarget;
  factor: number;
  scaleAxis: "z" | null;
  affected: readonly GhostTarget[];
}

/**
 * What a highlight is asked for: exact catalog object names, or the one object
 * a ray met before the server resolved it. Null takes the highlight off.
 */
export type HighlightRequest =
  | SemanticHighlightTarget
  | { object: Object3D }
  | null;

export type Vec3 = [number, number, number];

/** One object under a point of a stroke, read the way a click is read. */
export interface SampleHit {
  objectName: string | null;
  userStrings: UserStrings;
  world: Vec3;
}

/** Where the camera stands: the viewpoint a gesture was drawn in. */
export interface CameraState {
  position: Vec3;
  target: Vec3;
  up: Vec3;
  fov: number;
}

export interface ViewportLoadOptions {
  /** Keep the current view when replacing a model; the first load still fits. */
  readonly preserveCamera?: boolean;
}

export interface ViewportController {
  openFile(file: File, sourceLabel?: string, options?: ViewportLoadOptions): Promise<void>;
  /**
   * Put several exports on the stage as one picture — a whole run rather than
   * one seat of it. All of them or none: a file that will not parse leaves
   * whatever was on screen where it was, and says so. The group is the model
   * from then on, so picking, ghosting, fit and clear treat it as one.
   */
  openFiles(files: readonly File[], sourceLabel?: string, options?: ViewportLoadOptions): Promise<void>;
  /**
   * Mark what was picked. An element lights every object the export tagged
   * with it; a bare object lights only itself; null takes the mark off.
   * Returns how many objects were lit — zero means nothing on screen carries
   * that target.
   */
  highlight(target: HighlightRequest): number;
  /**
   * The object under a client-space point, with the file's strings and the
   * world point where the ray met it; null off the model. Nothing is
   * interpreted here - the server names what it is.
   */
  sampleAt(clientX: number, clientY: number): SampleHit | null;
  /** The camera as it stands, or null before the renderer exists. */
  camera(): CameraState | null;
  /**
   * A client-space point carried into the world on the plane facing the
   * camera through ``through`` (or through the orbit target when null): how
   * a stroke's length and direction become model units.
   */
  unprojectOnPlane(clientX: number, clientY: number, through: Vec3 | null): Vec3 | null;
  /**
   * Load a second file beside the loaded one for a cross-fade: the loaded
   * model is 'before', this one 'after'. Resolves with its mesh count.
   * Dropped by clear(), by a new load and by clearSecondary().
   */
  loadSecondary(file: File): Promise<number>;
  clearSecondary(): void;
  /** 0 shows only the loaded model, 1 only the secondary; in between, both. */
  blend(t: number): void;
  /**
   * Draw a ghost of a proposal over the loaded model, or remove it with null.
   * Returns how many meshes the ghost copied for the target: zero means the
   * loaded file carries no objects of that element, and nothing was drawn.
   */
  ghost(spec: GhostSpec | null): number;
  fitView(): void;
  frontView(): void;
  /** Encode the current rendered canvas for its caller; never writes the project. */
  capturePng(): Promise<Blob | null>;
  /** Remove temporary display projections and restore the loaded file exactly. */
  showOriginal(): void;
  setLayerVisibility(index: number, visible: boolean): void;
  clear(): void;
}

/** A pointer that moved further than this was an orbit, not a click. */
const CLICK_SLOP_PX = 5;

interface ThreeDmViewportProps {
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  /** Which file the viewport is showing, in the shell's own words. */
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
}

interface ViewportRuntime {
  scene: Scene;
  camera: PerspectiveCamera;
  renderer: WebGLRenderer;
  controls: OrbitControls;
  model: Object3D | null;
  /** Visibility, layers and material references as the loaded file supplied them. */
  appearance: ModelAppearance | null;
  ghost: Group | null;
  secondary: Object3D | null;
  /** Each faded material's own opacity, as it was before a blend scaled it. */
  restore: Map<Material, MaterialOpacity>;
  /** Where the cross-fade stands, so a highlight can be taken off without losing it. */
  blendT: number | null;
  /** The meshes wearing a highlight clone, in the order they were lit. */
  highlighted: Mesh[];
  /** What each of those meshes wore before; the clone is thrown away, this is not. */
  original: WeakMap<Mesh, Material | Material[]>;
  /** The clones themselves, so they are disposed rather than leaked. */
  clones: Material[];
  render: () => void;
}

const MAX_FILE_SIZE = 512 * 1024 * 1024;

interface NurbsFallbackPatch {
  positions: Float32Array;
  indices?: Uint32Array;
  attributes: { name?: string; visible?: boolean; layerIndex?: number; userStrings?: unknown };
}

interface NurbsFallbackFace {
  loops: Array<Array<[number, number, number]>>;
  attributes: NurbsFallbackPatch["attributes"];
}

/**
 * Read-only last resort for exact 3DM geometry saved without render meshes.
 * It is deliberately only called after the installed loader gave us a valid
 * but empty picture, so native meshes always retain their own materials and
 * triangle topology.
 */
async function nurbsFallback(buffer: ArrayBuffer, source: Object3D): Promise<Object3D | null> {
  const worker = new Worker("/nurbsFallback.worker.js");
  try {
    let missingFaces = 0;
    const patches = await new Promise<NurbsFallbackPatch[]>((resolve, reject) => {
      worker.onmessage = (event: MessageEvent<{ paths?: NurbsFallbackPatch[]; faces?: NurbsFallbackFace[]; unsupportedFaces?: number; error?: string }>) => {
        if (event.data.error) reject(new Error(event.data.error));
        else {
          missingFaces = event.data.unsupportedFaces ?? 0;
          const surfaces = (event.data.faces ?? []).flatMap((face) => {
            const triangles = triangulatedFace(face);
            if (triangles.length === 0) missingFaces += 1;
            return triangles;
          });
          resolve([...(event.data.paths ?? []), ...surfaces]);
        }
      };
      worker.onerror = () => reject(new Error("The local NURBS display fallback could not start."));
      worker.postMessage(buffer, [buffer]);
    });
    if (patches.length === 0) return null;
    const model = new Group();
    source.userData.nurbsFallback = true;
    source.userData.nurbsFallbackMissingFaces = missingFaces;
    const lineMaterial = new LineBasicMaterial({ color: "#d7d0c2" });
    const surfaceMaterial = new MeshStandardMaterial({ color: "#b8b1a5", roughness: 0.72, metalness: 0, side: DoubleSide });
    for (const patch of patches) {
      const geometry = new BufferGeometry();
      geometry.setAttribute("position", new Float32BufferAttribute(patch.positions, 3));
      const object = patch.indices === undefined
        ? new LineSegments(geometry, lineMaterial.clone())
        : new Mesh(geometry.setIndex(new BufferAttribute(patch.indices, 1)), surfaceMaterial.clone());
      if (patch.indices !== undefined) geometry.computeVertexNormals();
      object.name = patch.attributes.name ?? "";
      const layer = source.userData.layers?.[patch.attributes.layerIndex ?? -1];
      object.visible = patch.attributes.visible !== false && layer?.visible !== false;
      object.userData.attributes = patch.attributes;
      model.add(object);
    }
    // Keep the loader's layer table, settings, curves and source attributes.
    // The fallback supplies only the faces and edges its loader could not draw.
    source.add(model);
    const warning = nurbsFallbackWarning(source);
    if (warning !== null) source.userData.warnings = [...(source.userData.warnings ?? []), warning];
    return source;
  } finally {
    worker.terminate();
  }
}

function nurbsFallbackWarning(root: Object3D): string | null {
  let missingFaces = 0;
  root.traverse((object) => { missingFaces += object.userData.nurbsFallbackMissingFaces ?? 0; });
  return missingFaces > 0
    ? `${missingFaces} ${missingFaces === 1 ? "face is" : "faces are"} shown as sampled edges only; no saved render mesh is available.`
    : null;
}

export function triangulatedFace(face: NurbsFallbackFace): NurbsFallbackPatch[] {
  const samePoint = (left: [number, number, number], right: [number, number, number]) =>
    left.every((value, index) => Math.abs(value - right[index]) < 1e-8);
  const loops = face.loops.map((loop) =>
    loop.length > 1 && samePoint(loop[0], loop[loop.length - 1]) ? loop.slice(0, -1) : loop,
  ).filter((loop) => loop.length >= 3);
  const outer = loops[0];
  if (outer === undefined || outer.length < 3) return [];
  let normal = new Vector3();
  for (let index = 0; index < outer.length; index += 1) normal.add(new Vector3().fromArray(outer[index]).cross(new Vector3().fromArray(outer[(index + 1) % outer.length])));
  const axis = Math.abs(normal.x) > Math.abs(normal.y) && Math.abs(normal.x) > Math.abs(normal.z) ? 0 : Math.abs(normal.y) > Math.abs(normal.z) ? 1 : 2;
  const project = ([x, y, z]: [number, number, number]) => axis === 0 ? new Vector2(y, z) : axis === 1 ? new Vector2(x, z) : new Vector2(x, y);
  const points = loops.flat();
  try {
    const triangles = ShapeUtils.triangulateShape(outer.map(project), loops.slice(1).map((loop) => loop.map(project)));
    const positions: number[] = [], indices: number[] = [];
    for (const point of points) positions.push(...point);
    for (const triangle of triangles) indices.push(...triangle);
    if (indices.length === 0) return [];
    return [{ positions: new Float32Array(positions), indices: new Uint32Array(indices), attributes: face.attributes } as any];
  } catch { return []; }
}

function meshCount(root: Object3D): number {
  let count = 0;
  root.traverse((object) => {
    if (object instanceof Mesh) count += 1;
  });
  return count;
}

interface ThemeColours {
  viewport: string;
  gridMajor: string;
  gridMinor: string;
}

/**
 * The canvas belongs to the theme: its background and grid are the same tokens
 * the shell paints with, read off the document rather than fixed here, so a
 * dark shell never frames a bright canvas. Colours only — nothing about what
 * is drawn changes with them.
 */
function themeColours(): ThemeColours {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    viewport: read("--viewport", "#202020"),
    gridMajor: read("--grid-major", "#3d3d3d"),
    gridMinor: read("--grid-minor", "#2e2e2e"),
  };
}

function gridMaterialsOf(grid: GridHelper) {
  return Array.isArray(grid.material) ? grid.material : [grid.material];
}

function buildGrid(colours: ThemeColours): GridHelper {
  const grid = new GridHelper(
    40,
    40,
    new Color(colours.gridMajor),
    new Color(colours.gridMinor),
  );
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.002;
  gridMaterialsOf(grid).forEach((material) => {
    material.transparent = true;
    material.opacity = 0.62;
  });
  return grid;
}

function disposeGrid(grid: GridHelper): void {
  grid.geometry.dispose();
  gridMaterialsOf(grid).forEach((material) => material.dispose());
}

function accentColour(): string {
  return (
    getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() ||
    "#2f80ed"
  );
}

/**
 * Whether a loaded object is one the server catalog explicitly returned.
 */
function belongsTo(object: Object3D, target: SemanticHighlightTarget): boolean {
  return matchesSemanticCarrier({ name: object.name }, target);
}

function carriersOf(model: Object3D, target: SemanticHighlightTarget): Object3D[] {
  const found: Object3D[] = [];
  model.traverse((object) => {
    if (belongsTo(object, target)) found.push(object);
  });
  return found;
}

/** Whether this object is still part of the picture on screen. */
function isUnder(object: Object3D, root: Object3D): boolean {
  let current: Object3D | null = object;
  while (current !== null) {
    if (current === root) return true;
    current = current.parent;
  }
  return false;
}

/**
 * The material a picked object wears: its own, cloned once, with a restrained
 * drafting-blue lift and a little less transparency. No post-processing glow
 * and no second pass — the same forward render, one material deep. The lift
 * starts from the material's own opacity (``own``), not from a cross-fade's
 * scaled one, so the fade applied over the mark scales it exactly once.
 */
function highlightMaterial(material: Material, accent: Color, own?: MaterialOpacity): Material {
  const copy = material.clone();
  if (copy instanceof MeshStandardMaterial) {
    copy.emissive = new Color(accent);
    copy.emissiveIntensity = 0.22;
  }
  copy.opacity = Math.min(1, (own?.opacity ?? material.opacity) + 0.2);
  copy.transparent = copy.opacity < 1;
  copy.depthWrite = copy.opacity >= 1;
  copy.needsUpdate = true;
  return copy;
}

/** Give every highlighted mesh back the material it had, and drop the clones. */
function restoreHighlight(runtime: ViewportRuntime): void {
  for (const mesh of runtime.highlighted) {
    const original = runtime.original.get(mesh);
    if (original !== undefined) mesh.material = original;
    runtime.original.delete(mesh);
  }
  runtime.highlighted = [];
  for (const material of runtime.clones) {
    // A clone faded by a cross-fade was remembered like any other material;
    // a disposed clone has nothing to be given back.
    runtime.restore.delete(material);
    material.dispose();
  }
  runtime.clones = [];
}

function applyHighlight(runtime: ViewportRuntime, objects: readonly Object3D[]): void {
  if (objects.length === 0) return;
  const accent = new Color(accentColour());
  const cloned = new Map<Material, Material>();
  const clone = (material: Material): Material => {
    const existing = cloned.get(material);
    if (existing) return existing;
    const copy = highlightMaterial(material, accent, runtime.restore.get(material));
    cloned.set(material, copy);
    runtime.clones.push(copy);
    return copy;
  };
  for (const object of objects) {
    object.traverse((child) => {
      if (!(child instanceof Mesh) || runtime.original.has(child)) return;
      const original = child.material as Material | Material[];
      runtime.original.set(child, original);
      child.material = Array.isArray(original) ? original.map(clone) : clone(original);
      runtime.highlighted.push(child);
    });
  }
}

/** A translucent copy of every mesh under these carriers, in world space. */
function ghostCopy(carriers: readonly Object3D[], material: MeshStandardMaterial): Group {
  const group = new Group();
  for (const carrier of carriers) {
    carrier.updateWorldMatrix(true, true);
    carrier.traverse((object) => {
      if (!(object instanceof Mesh)) return;
      const copy = new Mesh(object.geometry, material);
      copy.matrixAutoUpdate = false;
      copy.matrix.copy(object.matrixWorld);
      group.add(copy);
    });
  }
  return group;
}

/** Every material under an object, once each. */
function materialsUnder(root: Object3D): Material[] {
  const seen = new Set<Material>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const material = object.material;
    for (const item of Array.isArray(material) ? material : [material]) seen.add(item);
  });
  return [...seen];
}

/**
 * Give the secondary model its own materials, tinted towards the accent so
 * 'after' reads apart from 'before' at any blend; the loader's materials
 * are left as they were.
 */
function tintSecondary(root: Object3D, accent: Color): void {
  const cloned = new Map<Material, Material>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const original = object.material as Material | Material[];
    const clone = (material: Material): Material => {
      const existing = cloned.get(material);
      if (existing) return existing;
      const copy = material.clone();
      if (copy instanceof MeshStandardMaterial) copy.color.lerp(accent, 0.45);
      cloned.set(material, copy);
      return copy;
    };
    object.material = Array.isArray(original) ? original.map(clone) : clone(original);
  });
}

function disposeSecondary(root: Object3D): void {
  for (const material of materialsUnder(root)) material.dispose();
  disposeScene(root);
}

function disposeGhost(ghost: Group): void {
  const materials = new Set<MeshStandardMaterial>();
  ghost.traverse((object) => {
    if (object instanceof Mesh && object.material instanceof MeshStandardMaterial) {
      materials.add(object.material);
    }
  });
  materials.forEach((material) => material.dispose());
}

/**
 * Several parsed exports as one model.
 *
 * The group carries the union of its documents' layers so the layer switch and
 * the inspection still have names to work with; the indices are each
 * document's own, and where two documents disagree the first name seen stands.
 * It carries no document user strings of its own: those are one file's claim
 * about one file, and the receipt the bytes came with is what answers for a
 * run — `receiptDocumentStrings` in the shell, never a guess made here.
 */
function groupOf(models: readonly Object3D[]): Group {
  const group = new Group();
  group.name = "archflow-run";
  const layers: unknown[] = [];
  const warnings: unknown[] = [];
  for (const model of models) {
    const own = model.userData.layers;
    if (Array.isArray(own)) {
      for (let index = 0; index < own.length; index += 1) {
        if (layers[index] === undefined) layers[index] = own[index] ?? {};
      }
    }
    const said = model.userData.warnings;
    if (Array.isArray(said)) warnings.push(...said);
    group.add(model);
  }
  if (layers.length > 0) {
    group.userData.layers = [...layers].map((layer) => layer ?? {});
  }
  if (warnings.length > 0) group.userData.warnings = warnings;
  return group;
}

function fitRuntime(runtime: ViewportRuntime): void {
  if (!runtime.model) return;
  const box = new Box3().setFromObject(runtime.model);
  if (box.isEmpty()) return;
  const center = box.getCenter(new Vector3());
  const size = box.getSize(new Vector3());
  const maximumDimension = Math.max(size.x, size.y, size.z, 1);
  const fieldOfView = (runtime.camera.fov * Math.PI) / 180;
  const distance = (maximumDimension / (2 * Math.tan(fieldOfView / 2))) * 1.55;
  const direction = new Vector3(1, -1, 0.78).normalize();

  runtime.camera.position.copy(center).addScaledVector(direction, distance);
  runtime.camera.near = Math.max(distance / 1000, 0.01);
  runtime.camera.far = Math.max(distance * 100, 1000);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.target.copy(center);
  runtime.controls.update();
  runtime.render();
}

function frontRuntime(runtime: ViewportRuntime): void {
  if (!runtime.model) return;
  const box = new Box3().setFromObject(runtime.model);
  if (box.isEmpty()) return;
  const center = box.getCenter(new Vector3());
  const size = box.getSize(new Vector3());
  const direction = runtime.camera.position
    .clone()
    .sub(runtime.controls.target);
  direction.z = 0;
  if (direction.lengthSq() < 1e-8) direction.set(1, -1, 0);
  direction.normalize();
  const screenRight = new Vector3(-direction.y, direction.x, 0);
  const verticalFieldOfView = (runtime.camera.fov * Math.PI) / 180;
  const horizontalFieldOfView =
    2 * Math.atan(Math.tan(verticalFieldOfView / 2) * runtime.camera.aspect);
  const tangentVertical = Math.tan(verticalFieldOfView / 2);
  const tangentHorizontal = Math.tan(horizontalFieldOfView / 2);
  const halfSize = size.multiplyScalar(0.5);
  let distance = 1;

  for (const x of [-halfSize.x, halfSize.x]) {
    for (const y of [-halfSize.y, halfSize.y]) {
      for (const z of [-halfSize.z, halfSize.z]) {
        const offset = new Vector3(x, y, z);
        const depthTowardCamera = offset.dot(direction);
        distance = Math.max(
          distance,
          depthTowardCamera + Math.abs(offset.dot(screenRight)) / tangentHorizontal,
          depthTowardCamera + Math.abs(z) / tangentVertical,
        );
      }
    }
  }
  distance *= 1.1;

  runtime.camera.up.set(0, 0, 1);
  runtime.camera.position.copy(center).addScaledVector(direction, distance);
  runtime.camera.near = Math.max(distance / 1000, 0.01);
  runtime.camera.far = Math.max(distance * 100, 1000);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.target.copy(center);
  runtime.controls.update();
  runtime.render();
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof error.message === "string"
  ) {
    return error.message;
  }
  return "This 3DM file could not be parsed.";
}

export const ThreeDmViewport = forwardRef<
  ViewportController,
  ThreeDmViewportProps
>(function ThreeDmViewport(
  { onInspection, onStatus, onRequestFile, onSource, onPick },
  forwardedRef,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<ViewportRuntime | null>(null);
  const loadGenerationRef = useRef(0);
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const callbacksRef = useRef({ onInspection, onStatus, onSource, onPick });
  const [dragActive, setDragActive] = useState(false);
  const [visualStatus, setVisualStatus] = useState<ViewportStatus>("idle");
  const [visualMessage, setVisualMessage] = useState("No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here");

  callbacksRef.current = { onInspection, onStatus, onSource, onPick };

  const reportStatus = useCallback((status: ViewportStatus, message: string) => {
    setVisualStatus(status);
    setVisualMessage(message);
    callbacksRef.current.onStatus(status, message);
  }, []);

  const clear = useCallback(() => {
    const runtime = runtimeRef.current;
    loadGenerationRef.current += 1;
    callbacksRef.current.onSource(null);
    if (runtime) {
      // The mark on a picked object belongs to the picture; it comes off
      // first so the meshes are disposed wearing their own materials.
      restoreHighlight(runtime);
      runtime.blendT = null;
    }
    if (runtime?.ghost) {
      runtime.scene.remove(runtime.ghost);
      disposeGhost(runtime.ghost);
      runtime.ghost = null;
    }
    if (runtime?.secondary) {
      runtime.scene.remove(runtime.secondary);
      disposeSecondary(runtime.secondary);
      runtime.secondary = null;
      runtime.restore.clear();
    }
    if (!runtime?.model) {
      callbacksRef.current.onInspection(null);
      reportStatus("idle", "No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here");
      return;
    }
    runtime.scene.remove(runtime.model);
    disposeScene(runtime.model);
    runtime.model = null;
    runtime.appearance = null;
    runtime.render();
    callbacksRef.current.onInspection(null);
    reportStatus("idle", "No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here");
  }, [reportStatus]);

  const removeGhost = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime?.ghost) return;
    runtime.scene.remove(runtime.ghost);
    disposeGhost(runtime.ghost);
    runtime.ghost = null;
    runtime.render();
  }, []);

  const ghost = useCallback(
    (spec: GhostSpec | null) => {
      const runtime = runtimeRef.current;
      if (!runtime) return 0;
      removeGhost();
      if (spec === null || !runtime.model) return 0;
      const accent = new Color(accentColour());
      const targetMaterial = new MeshStandardMaterial({
        color: accent,
        transparent: true,
        opacity: 0.38,
        depthWrite: false,
      });
      const cloudMaterial = new MeshStandardMaterial({
        color: accent,
        transparent: true,
        opacity: 0.14,
        depthWrite: false,
      });
      const group = new Group();
      group.name = "archflow-ghost";
      const targetCopy = ghostCopy(carriersOf(runtime.model, spec.target), targetMaterial);
      const copied = targetCopy.children.length;
      if (copied === 0) {
        // Nothing of this element is in the loaded file: draw nothing rather
        // than a cloud with no subject, and let the caller say so.
        targetMaterial.dispose();
        cloudMaterial.dispose();
        return 0;
      }
      if (spec.scaleAxis === "z" && spec.factor > 0 && spec.factor !== 1) {
        // Scale about the copy's own bottom: a taller thing grows upward from
        // where it stands. Approximate by construction - a field called
        // height is stretched, nothing else is inferred.
        const box = new Box3().setFromObject(targetCopy);
        if (!box.isEmpty()) {
          const pivot = new Group();
          pivot.position.set(0, 0, box.min.z);
          targetCopy.position.z -= box.min.z;
          pivot.scale.set(1, 1, spec.factor);
          pivot.add(targetCopy);
          group.add(pivot);
        } else {
          group.add(targetCopy);
        }
      } else {
        group.add(targetCopy);
      }
      for (const affected of spec.affected) {
        group.add(ghostCopy(carriersOf(runtime.model, affected), cloudMaterial));
      }
      runtime.ghost = group;
      runtime.scene.add(group);
      runtime.render();
      return copied;
    },
    [removeGhost],
  );

  const clearSecondary = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    // The loaded model gets its materials back exactly as they were - a pane
    // the file made translucent as translucent as the file said - before the
    // comparison's own materials are dropped.
    restoreOpacity(runtime.restore);
    if (runtime.secondary) {
      runtime.scene.remove(runtime.secondary);
      disposeSecondary(runtime.secondary);
      runtime.secondary = null;
    }
    runtime.blendT = null;
    // blend(1) hides the primary root. Removing the comparison must make the
    // original model visible again as well as restoring its material opacity.
    if (runtime.model) runtime.model.visible = true;
    runtime.render();
  }, []);

  const showOriginal = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime?.model) return;
    restoreHighlight(runtime);
    removeGhost();
    clearSecondary();
    if (runtime.appearance) restoreModelAppearance(runtime.model, runtime.appearance);
    runtime.render();
  }, [clearSecondary, removeGhost]);

  const blend = useCallback((t: number) => {
    const runtime = runtimeRef.current;
    if (!runtime?.model || !runtime.secondary) return;
    const mix = Math.min(1, Math.max(0, t));
    runtime.blendT = mix;
    // Each material keeps its own opacity, scaled by its side's weight: the
    // file's glass stays glass at every blend, and is itself again at 0 / 1.
    fadeOpacity(materialsUnder(runtime.model), runtime.restore, 1 - mix);
    fadeOpacity(materialsUnder(runtime.secondary), runtime.restore, mix);
    runtime.model.visible = mix < 1;
    runtime.secondary.visible = mix > 0;
    runtime.render();
  }, []);

  /**
   * Mark what was picked, so the click has an answer on the model and not only
   * in the transcript.
   *
   * The mark is the object's own material, cloned and lit — no outline pass and
   * no second render — and the originals come back the moment the mark moves,
   * the model changes, or the stage is cleared. A stale object (the file it
   * came from is gone) lights nothing rather than resurrecting itself.
   */
  const highlight = useCallback(
    (target: HighlightRequest): number => {
      const runtime = runtimeRef.current;
      if (!runtime) return 0;
      restoreHighlight(runtime);
      const model = runtime.model;
      if (target === null || model === null) {
        runtime.render();
        return 0;
      }
      const objects =
        "object" in target
          ? isUnder(target.object, model)
            ? [target.object]
            : []
          : carriersOf(model, target);
      applyHighlight(runtime, objects);
      // A cross-fade set the opacities; the clones start from what they were
      // before it, so the fade is applied again over the mark.
      if (runtime.secondary && runtime.blendT !== null) blend(runtime.blendT);
      else runtime.render();
      return objects.length;
    },
    [blend],
  );

  const loadSecondary = useCallback(
    async (file: File): Promise<number> => {
      const runtime = runtimeRef.current;
      if (!runtime?.model) throw new Error("Load a model first; the second one is compared against it.");
      const buffer = await file.arrayBuffer();
      const fallbackBuffer = buffer.slice(0);
      const loader = new Rhino3dmLoader();
      loader.setLibraryPath("/rhino3dm/");
      loader.setWorkerLimit(Math.max(1, Math.min(4, navigator.hardwareConcurrency || 2)));
      const generation = loadGenerationRef.current;
      return new Promise<number>((resolve, reject) => {
        loader.parse(
          buffer,
          (model) => {
            let display = model;
            void (async () => {
              try {
                if (meshCount(model) === 0) {
                  const fallback = await nurbsFallback(fallbackBuffer, model);
                  if (fallback !== null) {
                    display = fallback;
                  }
                }
              } catch {
                // A display fallback may fail, but a valid 3DM parse remains a
                // valid (if empty) comparison rather than a failed file load.
              }
              loader.dispose();
              if (generation !== loadGenerationRef.current || !runtimeRef.current?.model) {
                // The loaded model changed while this parsed: nothing to compare against any more.
                disposeScene(display);
                resolve(0);
                return;
              }
              clearSecondary();
              // The same preparation as the loaded model: what the file hid
              // stays hidden on the 'after' side too.
              prepareLoadedModel(display);
              tintSecondary(display, new Color(accentColour()));
              runtime.secondary = display;
              runtime.scene.add(display);
              const meshes = meshCount(display);
              blend(0.5);
              const warning = nurbsFallbackWarning(display);
              if (warning !== null) reportStatus("ready", `Comparison: ${warning}`);
              resolve(meshes);
            })().catch((error) => {
              loader.dispose();
              disposeScene(display);
              reject(error instanceof Error ? error : new Error(String(error)));
            });
          },
          (error) => {
            loader.dispose();
            reject(error instanceof Error ? error : new Error(String(error)));
          },
        );
      });
    },
    [blend, clearSecondary, reportStatus],
  );

  const openFiles = useCallback(
    async (files: readonly File[], sourceLabel: string = LOCAL_SOURCE_LABEL, options?: ViewportLoadOptions) => {
      const runtime = runtimeRef.current;
      if (!runtime) {
        throw new Error(
          "The 3D viewport is not ready yet; try again in a moment.",
        );
      }
      // A refused file leaves whatever was already loaded on screen, so the
      // fact line under the canvas has to go: it names the *previous* file, and
      // beside an error status it would read as that file having failed to
      // load. Nothing was loaded and nothing failed — a file was declined
      // before it was read, and the refusal below says which.
      const decline = (message: string) => {
        callbacksRef.current.onInspection(null);
        reportStatus("error", message);
      };
      if (files.length === 0) {
        decline("No file was given to open, so nothing was opened.");
        return;
      }
      for (const file of files) {
        if (!file.name.toLowerCase().endsWith(".3dm")) {
          decline(
            `Only Rhino .3dm files are supported. ${file.name} was not opened.`,
          );
          return;
        }
        if (file.size <= 0 || file.size > MAX_FILE_SIZE) {
          decline(`${file.name} is empty or over the 512 MB local parse limit.`);
          return;
        }
      }

      const names = files.map((file) => file.name).join(" + ");
      const totalSize = files.reduce((sum, file) => sum + file.size, 0);
      const generation = loadGenerationRef.current + 1;
      loadGenerationRef.current = generation;
      reportStatus(
        "loading",
        files.length === 1
          ? `Parsing ${names} locally`
          : `Parsing ${files.length} exports locally · ${names}`,
      );
      const started = performance.now();
      let buffers: ArrayBuffer[];
      try {
        buffers = await Promise.all(files.map((file) => file.arrayBuffer()));
      } catch (error) {
        // Bytes that could not be read are a file that never arrived, and the
        // fact line still names the one before it. Same reason as a refusal.
        decline(errorMessage(error));
        return;
      }
      // Rhino3dmLoader transfers each buffer to its own worker. Preserve a
      // separate local copy only for the rare valid-but-meshless fallback.
      const fallbackBuffers = buffers.map((buffer) => buffer.slice(0));

      const loader = new Rhino3dmLoader();
      loader.setLibraryPath("/rhino3dm/");
      loader.setWorkerLimit(
        Math.max(1, Math.min(4, navigator.hardwareConcurrency || 2)),
      );
      const parsed = await Promise.allSettled(
        buffers.map(
          (buffer) =>
            new Promise<Object3D>((resolve, reject) => {
              loader.parse(buffer, resolve, (error) =>
                reject(
                  error instanceof Error ? error : new Error(String(error)),
                ),
              );
            }),
        ),
      );
      loader.dispose();
      let models = parsed
        .filter(
          (result): result is PromiseFulfilledResult<Object3D> =>
            result.status === "fulfilled",
        )
        .map((result) => result.value);
      const failure = parsed.find(
        (result): result is PromiseRejectedResult =>
          result.status === "rejected",
      );
      if (generation !== loadGenerationRef.current) {
        // Something else was asked for while these parsed; they are nobody's.
        for (const model of models) disposeScene(model);
        return;
      }
      if (failure !== undefined) {
        // All of them or none: one file that will not parse is not most of a
        // run, and a picture quietly missing a seat is worse than the picture
        // that was already there.
        for (const model of models) disposeScene(model);
        // Parsing is all-or-none. The current model, its source label and any
        // active comparison remain the picture on screen when the replacement
        // cannot be opened.
        reportStatus("error", errorMessage(failure.reason));
        return;
      }

      models = await Promise.all(models.map(async (model, index) => {
        if (meshCount(model) > 0) return model;
        try {
          const fallback = await nurbsFallback(fallbackBuffers[index], model);
          if (fallback === null) return model;
          return fallback;
        } catch {
          // The normal loader did successfully read this file; retain its
          // empty result if its local display approximation cannot be made.
          return model;
        }
      }));
      // A fallback can take longer than the loader's own parse. It remains
      // part of this request only while this generation still owns the stage.
      if (generation !== loadGenerationRef.current || !runtimeRef.current) {
        for (const model of models) disposeScene(model);
        return;
      }

      // The file's own display state, whether one export or a whole run of
      // them, before the appearance below is remembered as the original.
      const model = prepareLoadedModel(models.length === 1 ? models[0] : groupOf(models));
      const preserveCamera = options?.preserveCamera === true && runtime.model !== null;
      // The mark on a picked object belongs to the picture going away.
      restoreHighlight(runtime);
      if (runtime.model) {
        runtime.scene.remove(runtime.model);
        disposeScene(runtime.model);
      }
      if (runtime.ghost) {
        // A ghost belongs to the model it was drawn over; a new model
        // starts without one.
        runtime.scene.remove(runtime.ghost);
        disposeGhost(runtime.ghost);
        runtime.ghost = null;
      }
      if (runtime.secondary) {
        // So does a comparison: it was against the model going away.
        runtime.scene.remove(runtime.secondary);
        disposeSecondary(runtime.secondary);
        runtime.secondary = null;
        runtime.restore.clear();
        runtime.blendT = null;
      }
      runtime.model = model;
      runtime.appearance = captureModelAppearance(model);
      runtime.scene.add(model);
      const inspection = inspectScene(
        model,
        { name: names, size: totalSize },
        Math.round(performance.now() - started),
      );
      callbacksRef.current.onInspection(inspection);
      callbacksRef.current.onSource(sourceLabel);
      // Keep the live camera rather than restoring an earlier snapshot: the
      // architect may have orbited while the replacement was being parsed.
      if (preserveCamera) runtime.render();
      else fitRuntime(runtime);
      const fallbackWarning = nurbsFallbackWarning(model);
      reportStatus(
        "ready",
        fallbackWarning !== null
          ? `${names} · ${inspection.meshCount.toLocaleString()} meshes · ${fallbackWarning}`
          : inspection.meshCount > 0
          ? `${names} · ${inspection.meshCount.toLocaleString()} meshes`
          : model.userData.nurbsFallback === true
            ? `${names} · sampled Brep edges (no saved render mesh)`
          : `${names} opened, but it holds no displayable mesh`,
      );
    },
    [reportStatus],
  );

  /** One file is one export: the same road, with a list of one. */
  const openFile = useCallback(
    (file: File, sourceLabel: string = LOCAL_SOURCE_LABEL, options?: ViewportLoadOptions) =>
      openFiles([file], sourceLabel, options),
    [openFiles],
  );

  /**
   * Read the object under the cursor and hand what it carries to the shell.
   *
   * The raycast is how a pixel becomes an object; it computes nothing about the
   * design. The walk upwards finds the object the loader hung Rhino's
   * attributes on, and the strings go out untouched — identity is resolved by
   * the server against the State Record, never here.
   */
  const rayAt = useCallback((clientX: number, clientY: number): Raycaster | null => {
    const runtime = runtimeRef.current;
    if (!runtime) return null;
    const rect = runtime.renderer.domElement.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    const raycaster = new Raycaster();
    raycaster.setFromCamera(
      new Vector2(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1,
      ),
      runtime.camera,
    );
    return raycaster;
  }, []);

  const hitAt = useCallback(
    (clientX: number, clientY: number) => {
      const runtime = runtimeRef.current;
      if (!runtime?.model) return null;
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      // The raycaster meets hidden meshes too; only what is on screen - its
      // own flag and its ancestors' - can be picked.
      const hit = raycaster
        .intersectObject(runtime.model, true)
        .find((intersection) => isDisplayed(intersection.object));
      if (!hit) return null;
      const carrier = userStringCarrier(hit.object) ?? hit.object;
      const attributes = carrier.userData.attributes as
        | { userStrings?: unknown }
        | undefined;
      return {
        objectName: carrier.name || null,
        userStrings: toUserStrings(attributes?.userStrings),
        point: hit.point,
        object: carrier,
      };
    },
    [rayAt],
  );

  const pickAt = useCallback(
    (clientX: number, clientY: number) => {
      const runtime = runtimeRef.current;
      if (!runtime?.model) return;
      const hit = hitAt(clientX, clientY);
      if (!hit) return;
      callbacksRef.current.onPick({
        userStrings: hit.userStrings,
        documentUserStrings: documentUserStrings(runtime.model),
        objectName: hit.objectName,
        object: hit.object,
      });
    },
    [hitAt],
  );

  const sampleAt = useCallback(
    (clientX: number, clientY: number): SampleHit | null => {
      const hit = hitAt(clientX, clientY);
      if (!hit) return null;
      return {
        objectName: hit.objectName,
        userStrings: hit.userStrings,
        world: [hit.point.x, hit.point.y, hit.point.z],
      };
    },
    [hitAt],
  );

  const cameraState = useCallback((): CameraState | null => {
    const runtime = runtimeRef.current;
    if (!runtime) return null;
    const { camera, controls } = runtime;
    return {
      position: [camera.position.x, camera.position.y, camera.position.z],
      target: [controls.target.x, controls.target.y, controls.target.z],
      up: [camera.up.x, camera.up.y, camera.up.z],
      fov: camera.fov,
    };
  }, []);

  const unprojectOnPlane = useCallback(
    (clientX: number, clientY: number, through: Vec3 | null): Vec3 | null => {
      const runtime = runtimeRef.current;
      if (!runtime) return null;
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      const normal = runtime.camera.getWorldDirection(new Vector3());
      const anchor = through
        ? new Vector3(through[0], through[1], through[2])
        : runtime.controls.target.clone();
      const plane = new Plane().setFromNormalAndCoplanarPoint(normal, anchor);
      const point = raycaster.ray.intersectPlane(plane, new Vector3());
      return point ? [point.x, point.y, point.z] : null;
    },
    [rayAt],
  );

  useImperativeHandle(
    forwardedRef,
    () => ({
      openFile,
      openFiles,
      highlight,
      ghost,
      sampleAt,
      camera: cameraState,
      unprojectOnPlane,
      loadSecondary,
      clearSecondary,
      blend,
      fitView: () => {
        const runtime = runtimeRef.current;
        if (runtime) fitRuntime(runtime);
      },
      frontView: () => {
        const runtime = runtimeRef.current;
        if (runtime) frontRuntime(runtime);
      },
      capturePng: () => {
        const runtime = runtimeRef.current;
        if (!runtime?.model) return Promise.resolve(null);
        return encodeViewportPng(runtime.renderer.domElement, runtime.render);
      },
      showOriginal,
      setLayerVisibility: (index, visible) => {
        const runtime = runtimeRef.current;
        if (!runtime?.model) return;
        runtime.model.traverse((object) => {
          // A layer switched on shows its objects, not the ones the file hid.
          if (layerIndexOf(object) === index) object.visible = visible && savedObjectVisible(object);
        });
        const layers = runtime.model.userData.layers;
        if (Array.isArray(layers) && layers[index]) layers[index].visible = visible;
        runtime.render();
      },
      clear,
    }),
    [
      blend,
      cameraState,
      clear,
      clearSecondary,
      ghost,
      highlight,
      loadSecondary,
      openFile,
      openFiles,
      sampleAt,
      showOriginal,
      unprojectOnPlane,
    ],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const scene = new Scene();
    const background = new Color(themeColours().viewport);
    scene.background = background;
    const camera = new PerspectiveCamera(38, 1, 0.01, 10000);
    camera.up.set(0, 0, 1);
    camera.position.set(8, -8, 6);

    const renderer = new WebGLRenderer({
      antialias: true,
      powerPreference: "high-performance",
      // The screenshot encoder runs asynchronously; retain the last explicit
      // render so the browser cannot serialize a cleared WebGL buffer.
      preserveDrawingBuffer: true,
    });
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.className = "viewport-canvas";
    renderer.domElement.setAttribute("aria-label", "3DM model viewport");
    host.prepend(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.screenSpacePanning = true;
    controls.target.set(0, 0, 0);

    const ambient = new AmbientLight(0xffffff, 1.45);
    const key = new DirectionalLight(0xffffff, 2.2);
    key.position.set(10, -8, 16);
    const fill = new DirectionalLight(0xd9e4e2, 1.1);
    fill.position.set(-10, 6, 8);
    scene.add(ambient, key, fill);

    let grid = buildGrid(themeColours());
    scene.add(grid);

    const render = () => renderer.render(scene, camera);

    // The tokens can change under a running canvas — a theme toggle, or the OS
    // switching at dusk — and the grid's colours are baked into its vertices,
    // so it is rebuilt rather than recoloured.
    const applyTheme = () => {
      const colours = themeColours();
      background.set(colours.viewport);
      scene.remove(grid);
      disposeGrid(grid);
      grid = buildGrid(colours);
      scene.add(grid);
      render();
    };
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", applyTheme);
    const themeObserver = new MutationObserver(applyTheme);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const runtime: ViewportRuntime = {
      scene,
      camera,
      renderer,
      controls,
      model: null,
      appearance: null,
      ghost: null,
      secondary: null,
      restore: new Map(),
      blendT: null,
      highlighted: [],
      original: new WeakMap(),
      clones: [],
      render,
    };
    runtimeRef.current = runtime;
    controls.addEventListener("change", render);

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    return () => {
      loadGenerationRef.current += 1;
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      // Give the highlighted meshes their own materials back, so what is
      // disposed below is the file's and the clones go with the mark.
      restoreHighlight(runtime);
      if (runtime.model) disposeScene(runtime.model);
      if (runtime.ghost) disposeGhost(runtime.ghost);
      if (runtime.secondary) disposeSecondary(runtime.secondary);
      media.removeEventListener("change", applyTheme);
      themeObserver.disconnect();
      disposeGrid(grid);
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className={`viewport-host${dragActive ? " is-dragging" : ""}`}
      onDragEnter={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragActive(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        const file = event.dataTransfer.files.item(0);
        // Dropped from this machine: bound to nothing, and labelled so.
        if (file) void openFile(file, LOCAL_SOURCE_LABEL);
      }}
      onPointerDown={(event) => {
        pointerDownRef.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerUp={(event) => {
        const down = pointerDownRef.current;
        pointerDownRef.current = null;
        if (!down) return;
        const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
        if (moved > CLICK_SLOP_PX) return;
        pickAt(event.clientX, event.clientY);
      }}
    >
      {visualStatus !== "ready" && (
        <div className={`viewport-state viewport-state--${visualStatus}`}>
          {visualStatus === "loading" && (
            <span className="activity-rail activity-rail--compact" aria-hidden="true" />
          )}
          <p aria-live="polite">{visualMessage}</p>
          {(visualStatus === "idle" || visualStatus === "error") && (
            <button className="button" type="button" onClick={onRequestFile}>
              Open a local .3dm
            </button>
          )}
        </div>
      )}
      {dragActive && <div className="drop-target">Release to open locally</div>}
    </div>
  );
});
