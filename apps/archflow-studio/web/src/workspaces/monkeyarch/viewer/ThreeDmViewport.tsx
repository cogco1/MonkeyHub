import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
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
  DynamicDrawUsage,
  Float32BufferAttribute,
  GridHelper,
  Group,
  Line,
  LineSegments,
  LineBasicMaterial,
  Material,
  Matrix3,
  Mesh,
  MeshStandardMaterial,
  Object3D,
  PerspectiveCamera,
  Plane,
  Raycaster,
  Scene,
  ShapeUtils,
  Sphere,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
  type Intersection,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Rhino3dmLoader } from "three/examples/jsm/loaders/3DMLoader.js";

import {
  disposeScene,
  inspectScene,
  indexLoadedObjects,
  layerIndexOf,
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
import { fitDistance } from "./fitCamera";
import {
  candidatesOf,
  closestOnEdge,
  nearestCandidate,
  type FeatureEdge,
  type Point3,
} from "./featureEdges";
import { encodeViewportPng } from "./viewportScreenshot";
import type { SketchPlane } from "../../../features/stage/sketch";
import { cancelInteractionFrame, scheduleInteractionFrame, type InteractionSession } from "../interactionSession";
import { Preselection, outlineEdges, raycastCurve, type LocalHit } from "./preselection";

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
  /** Only the local draft layer supplies this id; loaded exports never do. */
  draftElementId?: string;
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
  | { draftElementId: string }
  | null;

export type Vec3 = [number, number, number];

/**
 * A drawing in progress: the closed plan profile in CAD world ``(x, y)`` at
 * ``base``, and how far it is being pulled along +Z. A height of zero shows
 * the outline alone, which is what the first half of the action is.
 */
export interface SketchPreview {
  readonly profile: ReadonlyArray<readonly [number, number]>;
  readonly base: number;
  readonly height: number;
  readonly plane?: SketchPlane;
  readonly closed?: boolean;
}

export interface DraftPreviewObject {
  readonly elementId: string;
  readonly spec: SketchPreview;
}

/** Where a pointer really is on the model, and what that place is. */
export interface ModelSnap {
  readonly point: Vec3;
  readonly kind: "endpoint" | "midpoint" | "edge" | "surface";
  /** The object it belongs to, as the export named it. */
  readonly objectName: string | null;
  readonly edge?: FeatureEdge;
}

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
  /** Parse a possible replacement while the current model remains interactive. */
  readonly background?: boolean;
  /** Keep the current view when replacing a model; the first load still fits. */
  readonly preserveCamera?: boolean;
  /** The caller may leave this project or editing context while parsing is in flight. */
  readonly isCurrent?: () => boolean;
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
  unprojectOnPlane(clientX: number, clientY: number, through: Vec3 | null, vertical?: boolean): Vec3 | null;
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
  /**
   * A client point on the horizontal work plane at ``height``, in world
   * units: where a drawing action puts its next corner. Null when the ray
   * runs parallel to the plane or there is no renderer yet.
   */
  pointOnWorkPlane(clientX: number, clientY: number, height: number): Vec3 | null;
  pointOnSketchPlane(clientX: number, clientY: number, plane: SketchPlane): Vec3 | null;
  pointAlongAxis(clientX: number, clientY: number, origin: Vec3, axis: Vec3): Vec3 | null;
  workPlaneFromSelection(): SketchPlane | null;
  /**
   * The point on the loaded model a pointer is really over: the end or the
   * middle of a visible edge when one is within ``radiusPx`` on screen, a
   * point along that edge when the pointer is on it, and otherwise the place
   * the ray met the surface. Null when the ray meets nothing.
   *
   * The edges are the model's own: a face that was triangulated to draw it
   * offers no diagonal, so nothing snaps to a line nobody can see.
   */
  snapOnModel(clientX: number, clientY: number, radiusPx?: number): ModelSnap | null;
  /**
   * Show a profile, and the solid it would make, while it is being drawn.
   * This is a picture and nothing else: it is not the model, it is never
   * saved, and ``sketchPreview(null)`` leaves the scene exactly as it was.
   */
  sketchPreview(spec: SketchPreview | null): void;
  draftPreview(spec: { objects: readonly DraftPreviewObject[]; hiddenObjectNames: readonly string[] } | null): void;
  fitView(): void;
  frontView(): void;
  standardView(view: "top" | "front" | "right" | "iso"): void;
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
  interaction: RefObject<InteractionSession>;
  hoverEnabled: boolean;
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  onOpenFile?(file: File): void;
  /** Which file the viewport is showing, in the shell's own words. */
  onSource(sourceLabel: string | null): void;
  /** An empty primary click clears the shell's selection as well as its mark. */
  onPick(pick: ViewportPick | null): void;
  /**
   * What an empty viewport should offer, when the shell around it knows which
   * ways into a model are actually available. Without one, the viewer keeps
   * its own plain sentence and its file button.
   */
  idle?: ReactNode;
}

interface ViewportRuntime {
  scene: Scene;
  camera: PerspectiveCamera;
  renderer: WebGLRenderer;
  controls: OrbitControls;
  model: Object3D | null;
  modelIndex: ReturnType<typeof indexLoadedObjects> | null;
  modelBounds: Sphere | null;
  preselection: Preselection | null;
  /** Visibility, layers and material references as the loaded file supplied them. */
  appearance: ModelAppearance | null;
  ghost: Group | null;
  secondary: Object3D | null;
  /** Each faded material's own opacity, as it was before a blend scaled it. */
  restore: Map<Material, MaterialOpacity>;
  /** Where the cross-fade stands, so a highlight can be taken off without losing it. */
  blendT: number | null;
  /** The meshes wearing a highlight clone, in the order they were lit. */
  highlighted: (Mesh | Line)[];
  /** What each of those meshes wore before; the clone is thrown away, this is not. */
  original: WeakMap<Mesh | Line, Material | Material[]>;
  /** The clones themselves, so they are disposed rather than leaked. */
  clones: Material[];
  /** True while the camera still stands where a fit put it, nobody having moved it. */
  fitted: boolean;
  /** The drawing in progress. It is shown and thrown away; nothing saves it. */
  sketch: Group | null;
  draftRoot: Group;
  draftObjects: Map<string, { object: Group; spec: SketchPreview }>;
  draftIds: WeakMap<Object3D, string>;
  draftHidden: Map<Object3D, boolean>;
  draftBounds: Sphere | null;
  render: () => void;
}

function createPreviewGroup(name: string, temporary = true): Group {
  const group = new Group();
  group.name = name;
  const lineColour = temporary ? accentColour() : getComputedStyle(document.documentElement).getPropertyValue("--ink-2").trim() || "#6f727a";
  const base = new LineSegments(new BufferGeometry(), new LineBasicMaterial({
    color: lineColour, depthTest: !temporary, transparent: temporary, opacity: temporary ? 0.95 : 1,
  }));
  const surface = new Mesh(new BufferGeometry(), new MeshStandardMaterial({
    color: temporary ? accentColour() : "#b8b1a5", side: DoubleSide,
    transparent: temporary, opacity: temporary ? 0.18 : 1, depthWrite: !temporary,
  }));
  const edges = new LineSegments(new BufferGeometry(), new LineBasicMaterial({
    color: lineColour, depthTest: !temporary, transparent: temporary, opacity: temporary ? 0.75 : 1,
  }));
  base.renderOrder = edges.renderOrder = temporary ? 3 : 1;
  group.add(base, surface, edges);
  return group;
}

/** The same geometry builder serves the current gesture and completed local objects. */
function updatePreviewGroup(group: Group, spec: SketchPreview): void {
  group.visible = spec.profile.length >= 2;
  if (!group.visible) return;
  const [base, surface, edges] = group.children as [LineSegments, Mesh, LineSegments];
  const closed = (spec.closed ?? true) && spec.profile.length > 2;
  const outline: number[] = [];
  const world = (x: number, y: number, height = 0): Vec3 => spec.plane
    ? spec.plane.origin.map((value, index) => value + x * spec.plane!.xAxis[index]! + y * spec.plane!.yAxis[index]! + height * spec.plane!.normal[index]!) as unknown as Vec3
    : [x, y, spec.base + height];
  spec.profile.forEach(([planX, planY], index) => {
    if (index + 1 === spec.profile.length && !closed) return;
    const [nextX, nextY] = spec.profile[(index + 1) % spec.profile.length]!;
    outline.push(...world(planX, planY), ...world(nextX, nextY));
  });
  const positions: number[] = [];
  if (closed) {
    const points = spec.profile.map(([x, y]) => new Vector2(x, y));
    for (const triangle of ShapeUtils.triangulateShape(points, [])) {
      const [a, b, c] = triangle.map((index) => spec.profile[index]!);
      const normalOrder = (b![0] - a![0]) * (c![1] - a![1]) - (b![1] - a![1]) * (c![0] - a![0]) >= 0 ? triangle : [...triangle].reverse();
      for (const index of spec.height > 0 ? [...normalOrder].reverse() : normalOrder) positions.push(...world(...spec.profile[index]!));
      if (spec.height !== 0) for (const index of spec.height > 0 ? normalOrder : [...normalOrder].reverse()) positions.push(...world(...spec.profile[index]!, spec.height));
    }
    const winding = spec.profile.reduce((area, [x, y], index) => {
      const [nx, ny] = spec.profile[(index + 1) % spec.profile.length]!;
      return area + x * ny - nx * y;
    }, 0);
    if (spec.height !== 0) spec.profile.forEach(([x, y], index) => {
      const [nx, ny] = spec.profile[(index + 1) % spec.profile.length]!;
      const triangles = [[world(x, y), world(nx, ny), world(nx, ny, spec.height)],
        [world(x, y), world(nx, ny, spec.height), world(x, y, spec.height)]];
      for (const triangle of triangles) positions.push(...(winding * spec.height >= 0 ? triangle : triangle.reverse()).flat());
    });
  }
  const raised: number[] = [];
  if (spec.height !== 0 && closed) {
    spec.profile.forEach(([planX, planY], index) => {
      const [nextX, nextY] = spec.profile[(index + 1) % spec.profile.length]!;
      raised.push(...world(planX, planY, spec.height), ...world(nextX, nextY, spec.height));
      raised.push(...world(planX, planY), ...world(planX, planY, spec.height));
    });
  }
  // Reserve both flat and extruded capacity. Only a larger polygon grows
  // attributes; ordinary motion reuses the same GPU buffers and materials.
  const capacity = Math.max(32, 2 ** Math.ceil(Math.log2(spec.profile.length)));
  const first = world(...spec.profile[0]!);
  const update = (object: LineSegments | Mesh, values: number[], vertices: number) => {
    const geometry = object.geometry;
    let attribute = geometry.getAttribute("position") as BufferAttribute | undefined;
    if (!attribute || attribute.count < vertices) {
      // Release old GPU buffers when capacity grows, keeping the geometry.
      if (attribute) geometry.dispose();
      attribute = new Float32BufferAttribute(vertices * 3, 3).setUsage(DynamicDrawUsage);
      geometry.setAttribute("position", attribute);
      if (object === surface) geometry.setAttribute("normal",
        new Float32BufferAttribute(vertices * 3, 3).setUsage(DynamicDrawUsage));
    }
    (attribute.array as Float32Array).set(values);
    // Padding is degenerate at the first vertex, so bounds never include
    // stale coordinates when a polygon shrinks or a pull crosses zero.
    for (let index = values.length / 3; index < attribute.count; index++) attribute.setXYZ(index, ...first);
    attribute.needsUpdate = true;
    geometry.setDrawRange(0, values.length / 3);
    geometry.userData.previewVertexCount = values.length / 3;
    delete geometry.userData.archflowEdges;
    delete geometry.userData["archflowCurveEdges:segments"];
    if (object === surface) geometry.computeVertexNormals();
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
    object.visible = values.length > 0;
  };
  update(base, outline, capacity * 2);
  update(surface, positions, capacity * 12);
  update(edges, raised, capacity * 4);
}

const MAX_FILE_SIZE = 512 * 1024 * 1024;

function samePreview(a: SketchPreview, b: SketchPreview): boolean {
  return a === b || (a.base === b.base && a.height === b.height && (a.closed ?? true) === (b.closed ?? true) &&
    a.profile.length === b.profile.length && a.profile.every((point, index) => point[0] === b.profile[index]![0] && point[1] === b.profile[index]![1]) &&
    JSON.stringify(a.plane ?? null) === JSON.stringify(b.plane ?? null));
}

function draftIdFor(runtime: ViewportRuntime, object: Object3D): string | undefined {
  for (let current: Object3D | null = object; current && current !== runtime.draftRoot; current = current.parent) {
    const id = runtime.draftIds.get(current);
    if (id !== undefined) return id;
  }
  return undefined;
}

function clearDraftPreview(runtime: ViewportRuntime): void {
  if (runtime.highlighted.some((object) => isUnder(object, runtime.draftRoot))) restoreHighlight(runtime);
  for (const [object, visible] of runtime.draftHidden) object.visible = visible;
  runtime.draftHidden.clear();
  for (const { object } of runtime.draftObjects.values()) { object.removeFromParent(); disposeScene(object); }
  runtime.draftObjects.clear();
  runtime.draftRoot.removeFromParent();
  runtime.draftBounds = null;
}

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
  if (copy instanceof LineBasicMaterial) copy.color.copy(accent);
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
      if (!(child instanceof Mesh || child instanceof Line) || runtime.original.has(child)) return;
      const original = child.material as Material | Material[];
      runtime.original.set(child, original);
      child.material = Array.isArray(original) ? original.map(clone) : clone(original);
      runtime.highlighted.push(child);
    });
  }
}

/** A translucent copy of every surface and curve, in world space. */
function ghostCopy(carriers: readonly Object3D[], material: MeshStandardMaterial): Group {
  const group = new Group();
  let hasMesh = false;
  for (const carrier of carriers) {
    carrier.updateWorldMatrix(true, true);
    carrier.traverse((object) => {
      if (!(object instanceof Mesh || object instanceof Line)) return;
      const lineMaterial = object instanceof Line ? new LineBasicMaterial({
        color: material.color, transparent: true, opacity: material.opacity, depthWrite: false,
      }) : null;
      const copy = object instanceof LineSegments ? new LineSegments(object.geometry, lineMaterial!)
        : object instanceof Line ? new Line(object.geometry, lineMaterial!) : new Mesh(object.geometry, material);
      if (object instanceof Mesh) hasMesh = true;
      copy.matrixAutoUpdate = false;
      copy.matrix.copy(object.matrixWorld);
      group.add(copy);
    });
  }
  if (!hasMesh) material.dispose();
  return group;
}

/** Every material under an object, once each. */
function materialsUnder(root: Object3D): Material[] {
  const seen = new Set<Material>();
  root.traverse((object) => {
    if (!(object instanceof Mesh || object instanceof Line)) return;
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
    if (!(object instanceof Mesh || object instanceof Line)) return;
    const original = object.material as Material | Material[];
    const clone = (material: Material): Material => {
      const existing = cloned.get(material);
      if (existing) return existing;
      const copy = material.clone();
      if (copy instanceof MeshStandardMaterial || copy instanceof LineBasicMaterial) copy.color.lerp(accent, 0.45);
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
  const materials = new Set<Material>();
  ghost.traverse((object) => {
    if ((object instanceof Mesh || object instanceof Line) && !Array.isArray(object.material)) {
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
  const box = runtime.model ? new Box3().setFromObject(runtime.model) : new Box3();
  if (runtime.draftObjects.size) box.union(new Box3().setFromObject(runtime.draftRoot));
  if (box.isEmpty()) return;
  const center = box.getCenter(new Vector3());
  // The whole model, whichever way it is turned: the bounding sphere is the
  // one radius that holds under orbit, and both frame angles decide how far
  // back the camera has to stand for it.
  const radius = box.getBoundingSphere(new Sphere()).radius;
  const distance = fitDistance({
    radius, fovDegrees: runtime.camera.fov, aspect: runtime.camera.aspect,
  });
  const direction = new Vector3(1, -1, 0.78).normalize();

  runtime.camera.up.set(0, 0, 1);
  runtime.camera.position.copy(center).addScaledVector(direction, distance);
  runtime.camera.near = Math.max(distance / 1000, 0.01);
  runtime.camera.far = Math.max(distance * 100, 1000);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.target.copy(center);
  runtime.controls.update();
  // The frame now holds the model; a later resize may keep it that way until
  // somebody moves the camera themselves.
  runtime.fitted = true;
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
  // An explicitly chosen elevation is not the fit view; a resize keeps it.
  runtime.fitted = false;
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
  { onInspection, onStatus, onRequestFile, onOpenFile, onSource, onPick, idle, interaction, hoverEnabled },
  forwardedRef,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<ViewportRuntime | null>(null);
  const loadGenerationRef = useRef(0);
  const secondaryLoadRequest = useRef(0);
  const hoverEnabledRef = useRef(hoverEnabled);
  hoverEnabledRef.current = hoverEnabled;
  const pickedPlaneRef = useRef<{ object: Object3D; plane: SketchPlane } | null>(null);
  const callbacksRef = useRef({ onInspection, onStatus, onSource, onPick });
  const [dragActive, setDragActive] = useState(false);
  const [visualStatus, setVisualStatus] = useState<ViewportStatus>("idle");
  const [hasDraft, setHasDraft] = useState(false);
  const [visualMessage, setVisualMessage] = useState("No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here");

  callbacksRef.current = { onInspection, onStatus, onSource, onPick };

  const clearHover = useCallback((render = true) => {
    cancelInteractionFrame(interaction.current, "hover");
    interaction.current.hover = null;
    interaction.current.pointer = null;
    const runtime = runtimeRef.current;
    if (!runtime) return;
    runtime.renderer.domElement.style.cursor = "";
    if (runtime.preselection?.clear() && render) runtime.render();
  }, [interaction]);

  useEffect(() => { if (!hoverEnabled) clearHover(); }, [clearHover, hoverEnabled]);

  const reportStatus = useCallback((status: ViewportStatus, message: string) => {
    setVisualStatus(status);
    setVisualMessage(message);
    callbacksRef.current.onStatus(status, message);
  }, []);

  const clear = useCallback(() => {
    const runtime = runtimeRef.current;
    loadGenerationRef.current += 1;
    clearHover(false);
    interaction.current.press = null;
    if (runtime) { runtime.preselection?.dispose(); runtime.preselection = null; runtime.modelIndex = null; }
    callbacksRef.current.onSource(null);
    setHasDraft(false);
    if (runtime) {
      // The mark on a picked object belongs to the picture; it comes off
      // first so the meshes are disposed wearing their own materials.
      restoreHighlight(runtime);
      clearDraftPreview(runtime);
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
    runtime.modelBounds = null;
    runtime.appearance = null;
    runtime.render();
    callbacksRef.current.onInspection(null);
    reportStatus("idle", "No model on screen · reference brings the reference run back, or choose a version below, or drop a .3dm from this machine here");
  }, [clearHover, interaction, reportStatus]);

  const removeGhost = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime?.ghost) return;
    runtime.scene.remove(runtime.ghost);
    disposeGhost(runtime.ghost);
    runtime.ghost = null;
    runtime.render();
  }, []);

  const removeSketch = useCallback(() => {
    const runtime = runtimeRef.current;
    if (!runtime?.sketch) return;
    runtime.scene.remove(runtime.sketch);
    disposeScene(runtime.sketch);
    runtime.sketch = null;
    runtime.render();
  }, []);

  const sketchPreview = useCallback(
    (spec: SketchPreview | null) => {
      const runtime = runtimeRef.current;
      if (!runtime) return;
      if (spec === null) { removeSketch(); return; }
      if (spec.profile.length < 2) {
        if (runtime.sketch) { runtime.sketch.visible = false; runtime.render(); }
        return;
      }
      const group = runtime.sketch ?? createPreviewGroup("archflow-sketch-preview");
      updatePreviewGroup(group, spec);
      if (!runtime.sketch) runtime.scene.add(group);
      runtime.sketch = group;
      runtime.render();
    },
    [removeSketch],
  );

  const draftPreview = useCallback((spec: { objects: readonly DraftPreviewObject[]; hiddenObjectNames: readonly string[] } | null) => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const wanted = new Map(spec?.objects.map((entry) => [entry.elementId, entry]) ?? []);
    const hidden = new Set(spec?.hiddenObjectNames ?? []);
    let changed = false;
    const invalidate = () => { if (!changed) clearHover(false); changed = true; };
    for (const [object, visible] of runtime.draftHidden) if (!hidden.has(object.name)) {
      invalidate(); object.visible = visible; runtime.draftHidden.delete(object);
    }
    runtime.model?.traverse((object) => {
      if (!hidden.has(object.name) || runtime.draftHidden.has(object)) return;
      invalidate(); runtime.draftHidden.set(object, object.visible); object.visible = false;
    });
    for (const [id, entry] of runtime.draftObjects) if (!wanted.has(id)) {
      invalidate();
      if (runtime.highlighted.some((object) => isUnder(object, entry.object))) restoreHighlight(runtime);
      if (pickedPlaneRef.current && isUnder(pickedPlaneRef.current.object, entry.object)) pickedPlaneRef.current = null;
      entry.object.removeFromParent(); disposeScene(entry.object); runtime.draftObjects.delete(id);
    }
    for (const [id, entry] of wanted) {
      const current = runtime.draftObjects.get(id);
      if (current && samePreview(current.spec, entry.spec)) continue;
      invalidate();
      const object = current?.object ?? createPreviewGroup(`draft:${id}`, false);
      updatePreviewGroup(object, entry.spec);
      if (pickedPlaneRef.current && isUnder(pickedPlaneRef.current.object, object)) pickedPlaneRef.current = null;
      if (!current) { runtime.draftRoot.add(object); runtime.draftIds.set(object, id); }
      runtime.draftObjects.set(id, { object, spec: entry.spec });
      if (!runtime.preselection) {
        runtime.preselection = new Preselection(runtime.model ?? runtime.draftRoot, getComputedStyle(document.documentElement).getPropertyValue("--muted").trim() || "#aeafb8");
        runtime.scene.add(runtime.preselection.group);
      }
      runtime.preselection.prepare(object, true);
    }
    if (!changed) return;
    if (runtime.draftObjects.size) {
      if (!runtime.draftRoot.parent) runtime.scene.add(runtime.draftRoot);
      runtime.draftBounds = new Box3().setFromObject(runtime.draftRoot).getBoundingSphere(new Sphere());
    } else { runtime.draftRoot.removeFromParent(); runtime.draftBounds = null; }
    setHasDraft(runtime.draftObjects.size > 0);
    runtime.render();
  }, [clearHover]);

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
      clearHover(false);
      runtime.scene.add(group);
      runtime.render();
      return copied;
    },
    [clearHover, removeGhost],
  );

  const clearSecondary = useCallback(() => {
    secondaryLoadRequest.current += 1;
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
    clearHover(false);
    restoreHighlight(runtime);
    removeGhost();
    clearSecondary();
    if (runtime.appearance) restoreModelAppearance(runtime.model, runtime.appearance);
    for (const object of runtime.draftHidden.keys()) { runtime.draftHidden.set(object, object.visible); object.visible = false; }
    runtime.render();
  }, [clearHover, clearSecondary, removeGhost]);

  const blend = useCallback((t: number) => {
    const runtime = runtimeRef.current;
    if (!runtime?.model || !runtime.secondary) return;
    clearHover(false);
    const mix = Math.min(1, Math.max(0, t));
    runtime.blendT = mix;
    // Each material keeps its own opacity, scaled by its side's weight: the
    // file's glass stays glass at every blend, and is itself again at 0 / 1.
    fadeOpacity(materialsUnder(runtime.model), runtime.restore, 1 - mix);
    fadeOpacity(materialsUnder(runtime.secondary), runtime.restore, mix);
    runtime.model.visible = mix < 1;
    runtime.secondary.visible = mix > 0;
    runtime.render();
  }, [clearHover]);

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
      const model = runtime.model;
      if (target === null) {
        clearHover(false);
        restoreHighlight(runtime);
        pickedPlaneRef.current = null;
        runtime.render();
        return 0;
      }
      const objects =
        "draftElementId" in target ? [runtime.draftObjects.get(target.draftElementId)?.object].filter((object): object is Group => object !== undefined)
        : "object" in target
          ? isUnder(target.object, runtime.draftRoot) ? [runtime.draftObjects.get(draftIdFor(runtime, target.object) ?? "")?.object].filter((object): object is Group => object !== undefined)
          : model && isUnder(target.object, model)
            ? runtime.modelIndex?.siblings(target.object) ?? [target.object]
            : []
          : model ? carriersOf(model, target) : [];
      const primitives: (Mesh | Line)[] = [];
      for (const object of objects) object.traverse((child) => { if (child instanceof Mesh || child instanceof Line) primitives.push(child); });
      if (primitives.length === runtime.highlighted.length && primitives.every((primitive) => runtime.original.has(primitive))) {
        runtime.render();
        return objects.length;
      }
      restoreHighlight(runtime);
      applyHighlight(runtime, objects);
      // A cross-fade set the opacities; the clones start from what they were
      // before it, so the fade is applied again over the mark.
      if (runtime.secondary && runtime.blendT !== null) blend(runtime.blendT);
      else runtime.render();
      return objects.length;
    },
    [blend, clearHover],
  );

  const loadSecondary = useCallback(
    async (file: File): Promise<number> => {
      const runtime = runtimeRef.current;
      if (!runtime?.model) throw new Error("Load a model first; the second one is compared against it.");
      const generation = loadGenerationRef.current;
      const request = ++secondaryLoadRequest.current;
      const primary = runtime.model;
      const isCurrent = () => request === secondaryLoadRequest.current && generation === loadGenerationRef.current &&
        runtimeRef.current === runtime && runtime.model === primary;
      const buffer = await file.arrayBuffer();
      if (!isCurrent()) return 0;
      const fallbackBuffer = buffer.slice(0);
      const loader = new Rhino3dmLoader();
      loader.setLibraryPath("/rhino3dm/");
      loader.setWorkerLimit(Math.max(1, Math.min(4, navigator.hardwareConcurrency || 2)));
      return new Promise<number>((resolve, reject) => {
        loader.parse(
          buffer,
          (model) => {
            if (!isCurrent()) {
              loader.dispose();
              disposeScene(model);
              resolve(0);
              return;
            }
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
              if (!isCurrent()) {
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
      if (options?.isCurrent?.() === false) return;
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
        if (options?.background) throw new Error(message);
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
      const isCurrent = () => generation === loadGenerationRef.current && (options?.isCurrent?.() ?? true);
      const cancelled = () => {
        if (!options?.background && generation === loadGenerationRef.current) reportStatus(runtime.model ? "ready" : "idle",
          runtime.model ? "Kept the current model on screen" : "No model on screen");
      };
      if (!options?.background) reportStatus(
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
        if (isCurrent()) decline(errorMessage(error)); else cancelled();
        return;
      }
      if (!isCurrent()) { cancelled(); return; }
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
      if (!isCurrent()) {
        // Something else was asked for while these parsed; they are nobody's.
        for (const model of models) disposeScene(model);
        cancelled();
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
        if (options?.background) throw new Error(errorMessage(failure.reason));
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
      if (!isCurrent() || !runtimeRef.current) {
        for (const model of models) disposeScene(model);
        cancelled();
        return;
      }

      // The file's own display state, whether one export or a whole run of
      // them, before the appearance below is remembered as the original.
      const model = prepareLoadedModel(models.length === 1 ? models[0] : groupOf(models));
      const preserveCamera = options?.preserveCamera === true && runtime.model !== null;
      // The mark on a picked object belongs to the picture going away.
      restoreHighlight(runtime);
      clearHover(false);
      clearDraftPreview(runtime);
      setHasDraft(false);
      interaction.current.press = null;
      runtime.preselection?.dispose(); runtime.preselection = null;
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
      runtime.modelBounds = new Box3().setFromObject(model).getBoundingSphere(new Sphere());
      runtime.modelIndex = indexLoadedObjects(model);
      runtime.appearance = captureModelAppearance(model);
      runtime.scene.add(model);
      runtime.preselection = new Preselection(model, getComputedStyle(document.documentElement).getPropertyValue("--muted").trim() || "#aeafb8");
      runtime.scene.add(runtime.preselection.group);
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
    [clearHover, interaction, reportStatus],
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
    (clientX: number, clientY: number): LocalHit | null => {
      const runtime = runtimeRef.current;
      if (!runtime || (!runtime.model && !runtime.draftObjects.size)) return null;
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      const rect = runtime.renderer.domElement.getBoundingClientRect();
      // Broad phase at the farthest model depth; the final line hit must be
      // within eight screen pixels so zoom never turns a wire into a wide band.
      const farDepth = Math.max(1, ...[runtime.modelBounds, runtime.draftBounds].filter((bounds): bounds is Sphere => bounds !== null)
        .map((bounds) => runtime.camera.position.distanceTo(bounds.center) + bounds.radius));
      raycaster.params.Line.threshold = 2 * farDepth * Math.tan(runtime.camera.fov * Math.PI / 360) * 8 / rect.height;
      const intersections: Intersection[] = [];
      for (const root of [runtime.model, runtime.draftRoot]) root?.traverse((object) => {
        if (!isDisplayed(object)) return;
        // Three's Line.raycast only compensates for the line's own scale.
        // Native mesh picking stays unchanged; curves use their full world matrix.
        intersections.push(...(object instanceof Line ? raycastCurve(object, raycaster)
          : raycaster.intersectObject(object, false)));
      });
      const hit = intersections.sort((a, b) => a.distance - b.distance).find((intersection) => {
        if (!isDisplayed(intersection.object)) return false;
        if (!(intersection.object instanceof Line)) return true;
        const screen = intersection.point.clone().project(runtime.camera);
        return screen.z >= -1 && screen.z <= 1 && Math.hypot(
          (screen.x + 1) * rect.width / 2 + rect.left - clientX,
          (1 - screen.y) * rect.height / 2 + rect.top - clientY,
        ) <= 8;
      });
      if (!hit) return null;
      const draftElementId = draftIdFor(runtime, hit.object);
      const identity = draftElementId !== undefined ? {
        object: runtime.draftObjects.get(draftElementId)!.object, objectName: `draft:${draftElementId}`, userStrings: Object.freeze({}),
      } : runtime.modelIndex?.identity(hit.object);
      if (!identity) return null;
      return {
        ...identity,
        ...(draftElementId !== undefined ? { draftElementId } : {}),
        mesh: hit.object,
        faceIndex: hit.faceIndex ?? null,
        point: hit.point,
        normal: hit.face?.normal.clone().applyMatrix3(new Matrix3().getNormalMatrix(hit.object.matrixWorld)).normalize() ?? null,
      };
    },
    [rayAt],
  );

  const pickAt = useCallback(
    (clientX: number, clientY: number) => {
      const runtime = runtimeRef.current;
      if (!runtime || (!runtime.model && !runtime.draftObjects.size)) return;
      const hit = hitAt(clientX, clientY);
      clearHover(false);
      if (!hit) {
        highlight(null);
        callbacksRef.current.onPick(null);
        return;
      }
      if (hit.normal) {
        const normal = hit.normal;
        const guide = Math.abs(normal.z) < 0.9 ? new Vector3(0, 0, 1) : new Vector3(0, 1, 0);
        const xAxis = guide.cross(normal).normalize();
        const yAxis = normal.clone().cross(xAxis).normalize();
        pickedPlaneRef.current = { object: hit.object, plane: {
          origin: hit.point.toArray() as Vec3,
          xAxis: xAxis.toArray() as Vec3,
          yAxis: yAxis.toArray() as Vec3,
          normal: normal.toArray() as Vec3,
        } };
      } else pickedPlaneRef.current = null;
      highlight({ object: hit.object });
      callbacksRef.current.onPick({
        userStrings: hit.userStrings,
        documentUserStrings: hit.draftElementId !== undefined ? null : runtime.modelIndex?.documentUserStrings ?? null,
        ...(hit.draftElementId !== undefined ? { draftElementId: hit.draftElementId } : {}),
        objectName: hit.objectName,
        object: hit.object,
      });
    },
    [clearHover, highlight, hitAt],
  );

  const snapAtHit = useCallback(
    (hit: LocalHit | null, clientX: number, clientY: number, radiusPx = 14): ModelSnap | null => {
      const runtime = runtimeRef.current;
      if (!runtime || !hit) return null;
      const rect = runtime.renderer.domElement.getBoundingClientRect();
      const pointer = [clientX - rect.left, clientY - rect.top] as const;
      const project = (point: Point3): readonly [number, number] | null => {
        const carried = new Vector3(point[0], point[1], point[2]).project(runtime.camera);
        if (carried.z > 1) return null;
        return [(carried.x + 1) / 2 * rect.width, (1 - carried.y) / 2 * rect.height];
      };
      // The visible edges of the object the ray met, in world coordinates,
      // computed once per geometry and kept with it.
      const edges: FeatureEdge[] = [];
      hit.object.traverse((node) => {
        if (!(node instanceof Mesh || node instanceof Line) || !isDisplayed(node)) return;
        const mesh = node;
        const own = outlineEdges(mesh);
        mesh.updateWorldMatrix(true, false);
        for (const edge of own) {
          const a = new Vector3(...edge.a).applyMatrix4(mesh.matrixWorld);
          const b = new Vector3(...edge.b).applyMatrix4(mesh.matrixWorld);
          edges.push({ a: [a.x, a.y, a.z], b: [b.x, b.y, b.z] });
        }
      });
      const candidates = edges.flatMap(candidatesOf);
      const chosen = nearestCandidate(candidates, project, pointer, radiusPx);
      if (chosen) {
        const edge = edges.find((edge) => {
          const point = closestOnEdge(edge, chosen.point);
          return Math.hypot(...point.map((value, index) => value - chosen.point[index]!)) < 1e-6;
        });
        return { point: [chosen.point[0], chosen.point[1], chosen.point[2]], kind: chosen.kind, objectName: hit.objectName, edge };
      }
      // Not on a corner or a middle: the nearest visible edge, if the pointer
      // is over one, else the surface itself.
      const where: Point3 = [hit.point.x, hit.point.y, hit.point.z];
      let onEdge: { point: Point3; distance: number; edge: FeatureEdge } | null = null;
      for (const edge of edges) {
        const point = closestOnEdge(edge, where);
        const screen = project(point);
        if (screen === null) continue;
        const distance = Math.hypot(screen[0] - pointer[0], screen[1] - pointer[1]);
        if (distance <= radiusPx && (onEdge === null || distance < onEdge.distance)) onEdge = { point, distance, edge };
      }
      if (onEdge) return { point: [onEdge.point[0], onEdge.point[1], onEdge.point[2]], kind: "edge", objectName: hit.objectName, edge: onEdge.edge };
      return { point: [where[0], where[1], where[2]], kind: "surface", objectName: hit.objectName };
    },
    [],
  );

  const snapOnModel = useCallback((x: number, y: number, radiusPx = 14) => snapAtHit(hitAt(x, y), x, y, radiusPx), [hitAt, snapAtHit]);

  const paintHover = useCallback(() => {
    const session = interaction.current;
    const runtime = runtimeRef.current;
    if (!hoverEnabledRef.current || !runtime || (!runtime.model && !runtime.draftObjects.size) || !runtime.preselection || runtime.secondary || runtime.ghost ||
      !["inactive", "hovering"].includes(session.phase) || !session.pointer) { clearHover(); return; }
    const { x, y } = session.pointer;
    const hit = hitAt(x, y);
    if (!hit) { clearHover(); return; }
    session.hover = hit;
    runtime.preselection.update(hit, snapAtHit(hit, x, y));
    runtime.renderer.domElement.style.cursor = "pointer";
    runtime.render();
  }, [clearHover, hitAt, interaction, snapAtHit]);

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

  const pointOnWorkPlane = useCallback(
    (clientX: number, clientY: number, height: number): Vec3 | null => {
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      // The loaded CAD model is Z-up; its horizontal work plane is XY.
      const plane = new Plane(new Vector3(0, 0, 1), -height);
      const point = raycaster.ray.intersectPlane(plane, new Vector3());
      return point ? [point.x, point.y, point.z] : null;
    },
    [rayAt],
  );

  const pointOnSketchPlane = useCallback(
    (clientX: number, clientY: number, frame: SketchPlane): Vec3 | null => {
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      const plane = new Plane().setFromNormalAndCoplanarPoint(new Vector3(...frame.normal), new Vector3(...frame.origin));
      const point = raycaster.ray.intersectPlane(plane, new Vector3());
      return point ? [point.x, point.y, point.z] : null;
    }, [rayAt],
  );

  const pointAlongAxis = useCallback(
    (clientX: number, clientY: number, origin: Vec3, direction: Vec3): Vec3 | null => {
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      const axis = new Vector3(...direction).normalize();
      const offset = new Vector3(...origin).sub(raycaster.ray.origin);
      const dot = raycaster.ray.direction.dot(axis);
      const denominator = 1 - dot * dot;
      if (denominator < 1e-8) return null;
      const distance = (dot * raycaster.ray.direction.dot(offset) - axis.dot(offset)) / denominator;
      const point = new Vector3(...origin).addScaledVector(axis, distance);
      return [point.x, point.y, point.z];
    }, [rayAt],
  );

  const unprojectOnPlane = useCallback(
    (clientX: number, clientY: number, through: Vec3 | null, vertical = false): Vec3 | null => {
      const runtime = runtimeRef.current;
      if (!runtime) return null;
      const raycaster = rayAt(clientX, clientY);
      if (!raycaster) return null;
      const normal = runtime.camera.getWorldDirection(new Vector3());
      if (vertical) {
        normal.z = 0;
        if (normal.lengthSq() < 1e-12) return null;
        normal.normalize();
      }
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
      snapOnModel,
      pointOnWorkPlane,
      pointOnSketchPlane,
      pointAlongAxis,
      workPlaneFromSelection: () => {
        const picked = pickedPlaneRef.current;
        const runtime = runtimeRef.current;
        return picked && runtime && ((runtime.model && isUnder(picked.object, runtime.model)) || isUnder(picked.object, runtime.draftRoot)) &&
          runtime.highlighted.some((mesh) => isUnder(mesh, picked.object))
          ? picked.plane : null;
      },
      sketchPreview,
      draftPreview,
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
      standardView: (view) => {
        const runtime = runtimeRef.current;
        if (!runtime?.model) return;
        runtime.camera.up.set(0, 0, 1);
        fitRuntime(runtime);
        if (view === "iso") return;
        const distance = runtime.camera.position.distanceTo(runtime.controls.target);
        const direction = view === "top" ? new Vector3(0, 0, 1) : view === "front" ? new Vector3(0, -1, 0) : new Vector3(1, 0, 0);
        if (view === "top") runtime.camera.up.set(0, 1, 0);
        runtime.camera.position.copy(runtime.controls.target).addScaledVector(direction, distance);
        runtime.controls.update();
        runtime.fitted = false;
        runtime.render();
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
        clearHover(false);
        runtime.model.traverse((object) => {
          // A layer switched on shows its objects, not the ones the file hid.
          if (layerIndexOf(object) === index) {
            const next = visible && savedObjectVisible(object);
            if (runtime.draftHidden.has(object)) { runtime.draftHidden.set(object, next); object.visible = false; }
            else object.visible = next;
          }
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
      draftPreview,
      clearSecondary,
      ghost,
      highlight,
      loadSecondary,
      openFile,
      openFiles,
      pointOnSketchPlane,
      pointAlongAxis,
      sampleAt,
      sketchPreview,
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
      const sketch = runtimeRef.current?.sketch;
      if (sketch) {
        const accent = accentColour();
        for (const child of sketch.children as (LineSegments | Mesh)[]) {
          (child.material as LineBasicMaterial | MeshStandardMaterial).color.set(accent);
        }
      }
      const runtime = runtimeRef.current;
      if (runtime) for (const { object } of runtime.draftObjects.values()) {
        for (const child of object.children as (LineSegments | Mesh)[]) {
          const material = runtime.original.get(child) ?? child.material;
          for (const own of Array.isArray(material) ? material : [material]) {
            (own as LineBasicMaterial | MeshStandardMaterial).color.set(child instanceof Line
              ? getComputedStyle(document.documentElement).getPropertyValue("--ink-2").trim() || "#6f727a" : "#b8b1a5");
          }
        }
      }
      runtimeRef.current?.preselection?.colour(getComputedStyle(document.documentElement).getPropertyValue("--muted").trim() || "#aeafb8");
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
      modelIndex: null,
      modelBounds: null,
      preselection: null,
      appearance: null,
      ghost: null,
      secondary: null,
      restore: new Map(),
      blendT: null,
      highlighted: [],
      original: new WeakMap(),
      clones: [],
      fitted: false,
      sketch: null,
      draftRoot: new Group(),
      draftObjects: new Map(),
      draftIds: new WeakMap(),
      draftHidden: new Map(),
      draftBounds: null,
      render,
    };
    runtimeRef.current = runtime;
    const changedCamera = () => { clearHover(false); render(); };
    controls.addEventListener("change", changedCamera);
    // Orbiting, panning or zooming is a chosen view; from then on a resize
    // reframes nothing. OrbitControls raises this for real input only.
    const userTookTheCamera = () => { runtime.fitted = false; clearHover(); };
    controls.addEventListener("start", userTookTheCamera);

    const resize = () => {
      clearHover(false);
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      // A panel that just became narrower sees less across than it did. The
      // fit view is recomputed for the frame it is now in; a hand-placed
      // camera is left alone.
      if (runtime.fitted && runtime.model) fitRuntime(runtime);
      else render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    return () => {
      loadGenerationRef.current += 1;
      observer.disconnect();
      clearHover(false);
      interaction.current.press = null;
      runtime.preselection?.dispose();
      controls.removeEventListener("change", changedCamera);
      controls.removeEventListener("start", userTookTheCamera);
      controls.dispose();
      // Give the highlighted meshes their own materials back, so what is
      // disposed below is the file's and the clones go with the mark.
      restoreHighlight(runtime);
      clearDraftPreview(runtime);
      if (runtime.model) disposeScene(runtime.model);
      if (runtime.ghost) disposeGhost(runtime.ghost);
      if (runtime.sketch) disposeScene(runtime.sketch);
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
        if (file) { if (onOpenFile) onOpenFile(file); else void openFile(file, LOCAL_SOURCE_LABEL); }
      }}
      onPointerDown={(event) => {
        clearHover();
        interaction.current.press = event.button === 0 && event.target === runtimeRef.current?.renderer.domElement
          ? { x: event.clientX, y: event.clientY, dragging: false }
          : null;
      }}
      onPointerUp={(event) => {
        const down = interaction.current.press;
        interaction.current.press = null;
        if (!down) return;
        const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
        if (moved > CLICK_SLOP_PX) return;
        pickAt(event.clientX, event.clientY);
      }}
      onPointerMove={(event) => {
        const session = interaction.current;
        if (session.press) session.press.dragging ||= Math.hypot(event.clientX - session.press.x, event.clientY - session.press.y) > CLICK_SLOP_PX;
        if (!hoverEnabled || event.buttons !== 0 || event.target !== runtimeRef.current?.renderer.domElement) { clearHover(); return; }
        session.pointer = { x: event.clientX, y: event.clientY };
        scheduleInteractionFrame(session, "hover", paintHover);
      }}
      onPointerLeave={() => { clearHover(); }}
      onPointerCancel={() => { interaction.current.press = null; clearHover(); }}
    >
      {visualStatus !== "ready" && !(visualStatus === "idle" && hasDraft) && (
        <div className={`viewport-state viewport-state--${visualStatus}`}>
          {visualStatus === "loading" && (
            <span className="activity-rail activity-rail--compact" aria-hidden="true" />
          )}
          {/* An empty viewport is a place to start from when the shell
              handed one in; otherwise it states what it holds. */}
          {visualStatus === "idle" && idle ? idle : <>
            <p aria-live="polite">{visualMessage}</p>
            {(visualStatus === "idle" || visualStatus === "error") && (
              <button className="button" type="button" onClick={onRequestFile}>
                Open a local .3dm
              </button>
            )}
          </>}
        </div>
      )}
      {dragActive && <div className="drop-target">Release to open locally</div>}
    </div>
  );
});
