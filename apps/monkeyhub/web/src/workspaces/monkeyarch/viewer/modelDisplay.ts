import {
  BufferGeometry, Color, Float32BufferAttribute, Group, Line, LineBasicMaterial, LineSegments, Mesh,
  MeshStandardMaterial, SRGBColorSpace, Texture, type Material, type Object3D,
} from "three";
import type { FeatureEdge } from "./featureEdges";

export interface SemanticHighlightTarget {
  /** Exact object names returned by the server's catalog for this model run. */
  readonly objectNames: readonly string[];
}

interface SemanticCarrier {
  readonly name?: string;
}

interface CatalogObjectBinding {
  readonly name: string;
  readonly componentId: string | null;
  readonly elementId: string | null;
  readonly status: string;
}

interface CatalogComponentBinding {
  readonly componentId: string;
  readonly children: readonly string[];
}

/** Resolve a tree choice through the server-owned object catalog only. */
export function semanticObjectNames(
  objects: readonly CatalogObjectBinding[],
  components: readonly CatalogComponentBinding[],
  componentId: string,
  elementId: string | null,
): string[] {
  if (elementId !== null) {
    return [...new Set(
      objects
        .filter(
          (object) =>
            object.status === "bound" &&
            object.componentId === componentId &&
            object.elementId === elementId,
        )
        .map((object) => object.name),
    )].sort();
  }

  const children = new Map(components.map((component) => [component.componentId, component.children]));
  const subtree = new Set<string>();
  const pending = [componentId];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined || subtree.has(current)) continue;
    subtree.add(current);
    pending.push(...(children.get(current) ?? []));
  }
  return [...new Set(
    objects
      .filter((object) => object.componentId !== null && subtree.has(object.componentId))
      .map((object) => object.name),
  )].sort();
}

/** Match only a complete object name already resolved by the server. */
export function matchesSemanticCarrier(
  carrier: SemanticCarrier,
  target: SemanticHighlightTarget,
): boolean {
  return carrier.name !== undefined && target.objectNames.includes(carrier.name);
}

/**
 * Whether the file saved this object as visible.
 *
 * The loader hangs the object's attributes on ``userData.attributes`` and sets
 * ``visible`` from the layer alone; the object's own flag is read here. Only
 * an explicit ``false`` hides: an object without attributes (a group, an
 * instance root) is as visible as its parent.
 */
export function savedObjectVisible(object: Object3D): boolean {
  const attributes = object.userData.attributes as { visible?: unknown } | undefined;
  return attributes?.visible !== false;
}

function savedDisplayColor(object: Object3D): Color | null {
  const attributes = object.userData.attributes;
  if (!attributes) return null;
  // The loader reads Rhino's resolved draw color, including object/layer source.
  let rgb = attributes.drawColor;
  if (rgb === undefined) {
    if (attributes.colorSource?.name === "ObjectColorSource_ColorFromObject") {
      rgb = attributes.objectColor;
    } else if (attributes.colorSource === undefined || attributes.colorSource?.name === "ObjectColorSource_ColorFromLayer") {
      for (let document: Object3D | null = object; document; document = document.parent) {
        if (Array.isArray(document.userData.layers)) {
          rgb = document.userData.layers[attributes.layerIndex]?.color;
          break;
        }
      }
    }
  }
  if (!rgb || ![rgb.r, rgb.g, rgb.b].every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 255)) return null;
  return new Color().setRGB(rgb.r / 255, rgb.g / 255, rgb.b / 255, SRGBColorSpace);
}

/**
 * Give a freshly parsed model the display state its file saved: the layer
 * visibility the loader applied, and each object's own saved visibility on
 * top of it. Every loaded model - the reference, a local file, the second
 * side of a comparison - passes through here before its appearance is
 * captured, so a hidden construction object is remembered as hidden and no
 * restoration brings it back. The loader's unassigned white mesh material
 * uses the file's display color; native materials and textures stay intact.
 */
export function prepareLoadedModel<T extends Object3D>(root: T): T {
  root.traverse((object) => {
    if (object.visible && !savedObjectVisible(object)) object.visible = false;
    if (!(object instanceof Mesh)) return;
    const color = savedDisplayColor(object);
    if (!color) return;
    const prepare = (material: Material): Material => {
      if (!(material instanceof MeshStandardMaterial) || material.name !== "__DEFAULT" || material.userData.id ||
          material.vertexColors || Object.values(material).some((value) => value instanceof Texture) || material.color.equals(color)) return material;
      const copy = material.clone();
      copy.color.copy(color);
      return copy;
    };
    object.material = Array.isArray(object.material) ? object.material.map(prepare) : prepare(object.material);
  });
  return root;
}

/** Whether this object is actually on screen: its own flag and every ancestor's. */
export function isDisplayed(object: Object3D): boolean {
  for (let current: Object3D | null = object; current !== null; current = current.parent) {
    if (!current.visible) return false;
  }
  return true;
}

export interface ModelAppearance {
  readonly visibility: WeakMap<Object3D, boolean>;
  readonly materials: WeakMap<Mesh | Line, Material | Material[]>;
  readonly layerVisibility: WeakMap<Object3D, ReadonlyArray<boolean | undefined>>;
}

/** Remember the loaded file's display state before any temporary projection touches it. */
export function captureModelAppearance(root: Object3D): ModelAppearance {
  const visibility = new WeakMap<Object3D, boolean>();
  const materials = new WeakMap<Mesh | Line, Material | Material[]>();
  const layerVisibility = new WeakMap<Object3D, ReadonlyArray<boolean | undefined>>();
  root.traverse((object) => {
    visibility.set(object, object.visible);
    if (object instanceof Mesh || object instanceof Line) materials.set(object, object.material);
    const layers = object.userData.layers;
    if (Array.isArray(layers)) {
      layerVisibility.set(
        object,
        layers.map((layer) =>
          typeof layer === "object" && layer !== null && typeof layer.visible === "boolean"
            ? layer.visible
            : undefined,
        ),
      );
    }
  });
  return { visibility, materials, layerVisibility };
}

/** A material's own opacity state, as the file's loader set it. */
export interface MaterialOpacity {
  readonly transparent: boolean;
  readonly opacity: number;
  readonly depthWrite: boolean;
}

/**
 * Scale each material's own opacity by ``weight`` (0..1) for a cross-fade.
 *
 * A material's own state is remembered in ``own`` the first time it is faded
 * and never overwritten, so a pane the file made translucent stays
 * proportionally translucent at every blend, and an opaque wall is opaque
 * again at weight 1. ``restoreOpacity`` gives every remembered material its
 * own state back.
 */
export function fadeOpacity(
  materials: readonly Material[],
  own: Map<Material, MaterialOpacity>,
  weight: number,
): void {
  const scale = Math.min(1, Math.max(0, weight));
  for (const material of materials) {
    let state = own.get(material);
    if (state === undefined) {
      state = {
        transparent: material.transparent,
        opacity: material.opacity,
        depthWrite: material.depthWrite,
      };
      own.set(material, state);
    }
    const opacity = state.opacity * scale;
    material.transparent = state.transparent || opacity < 1;
    material.opacity = opacity;
    material.depthWrite = state.depthWrite && opacity >= 1;
    material.needsUpdate = true;
  }
}

/** Give every faded material its own opacity state back, and forget them. */
export function restoreOpacity(own: Map<Material, MaterialOpacity>): void {
  for (const [material, state] of own) {
    material.transparent = state.transparent;
    material.opacity = state.opacity;
    material.depthWrite = state.depthWrite;
    material.needsUpdate = true;
  }
  own.clear();
}

/**
 * How the loaded model is painted. ``original`` is the file's own look: its
 * render materials, or its object/layer display colour where it has none.
 * ``modeling`` is a SketchUp-direction working look - matte surfaces in the
 * file's muted colours, thin feature edges, plain light. Neither touches geometry, names, visibility or
 * anything a project records; the file's own materials are never modified.
 */
export type ModelDisplayStyle = "original" | "modeling";

/** The look a viewer opens in; Original stays one switch away. */
export const DEFAULT_MODEL_DISPLAY_STYLE: ModelDisplayStyle = "modeling";

export interface ModelingPalette {
  readonly background: string;
  /** The pale base every surface is lifted toward. */
  readonly paper: string;
  /** How much of the file's own colour survives in a pale surface, 0..1. */
  readonly tint: number;
  readonly edge: string;
  /** Edges of a translucent surface stay visible but lighter than solid ones. */
  readonly translucentEdgeOpacity: number;
}

/**
 * A neutral working palette. Surfaces keep most of the file's own colour, only
 * lifted a little toward the paper, so materials read apart as they were
 * painted; a dark shell gets a dark canvas and slightly dimmer surfaces.
 */
export function modelingPalette(dark: boolean): ModelingPalette {
  return dark
    ? { background: "#1e1f22", paper: "#d9d7d1", tint: 0.8, edge: "#0d0e10", translucentEdgeOpacity: 0.55 }
    : { background: "#f7f7f4", paper: "#f8f7f3", tint: 0.85, edge: "#333333", translucentEdgeOpacity: 0.45 };
}

/** Whether a material lets the model behind it through, by opacity or by transmission. */
function seeThrough(material: Material): boolean {
  const transmission = (material as { transmission?: unknown }).transmission;
  return (material.transparent && material.opacity < 1) || (typeof transmission === "number" && transmission > 0);
}

/**
 * The matte working material standing in for one of the file's materials.
 *
 * It keeps what the file said about seeing through - opacity, transparency,
 * depth writing, the side drawn - and most of its colour, so glass stays
 * glass and two materials read apart. Textures and vertex colours are
 * the file's look and stay with the original. Surfaces keep their true depth,
 * so the ground grid and near-coincident geometry resolve exactly as in the
 * file's own look; the edges carry the depth bias instead. Stand-ins are not
 * tone mapped: the renderer's filmic curve stays for the file's own look, and
 * a working colour is shown as lit rather than compressed toward grey. A
 * material this style has no stand-in for (points) is returned unchanged.
 */
export function modelingMaterial(material: Material, palette: ModelingPalette): Material {
  if (material instanceof LineBasicMaterial) {
    const line = new LineBasicMaterial({
      color: palette.edge, transparent: material.transparent, opacity: material.opacity, depthWrite: material.depthWrite,
      toneMapped: false,
    });
    line.name = material.name;
    line.userData.displayStyle = "modeling";
    return line;
  }
  if (material.type === "PointsMaterial") return material;
  const own = (material as { color?: unknown }).color;
  const colour = new Color(palette.paper);
  if (own instanceof Color) colour.lerp(own, palette.tint);
  const transmission = (material as { transmission?: unknown }).transmission;
  // Transmission is glass the loader expressed physically; the working look shows it as opacity.
  const opacity = material.transparent ? material.opacity
    : typeof transmission === "number" && transmission > 0 ? Math.max(0.25, 1 - transmission) : 1;
  const surface = new MeshStandardMaterial({
    color: colour,
    roughness: 0.9,
    metalness: 0,
    toneMapped: false,
    side: material.side,
    transparent: material.transparent || opacity < 1,
    opacity,
    depthWrite: material.depthWrite && opacity >= 1,
  });
  surface.name = material.name;
  surface.userData.displayStyle = "modeling";
  return surface;
}

/**
 * Paint every captured primitive under ``root`` in ``style``.
 *
 * The file's own materials come from ``appearance``, never from what an
 * object wears now, so switching back returns the exact references the loader
 * made. ``derived`` holds one stand-in per original material, shared the way
 * the originals are shared. A primitive the appearance never saw is left alone.
 * The caller takes any selection mark off first and puts it back after.
 */
export function applyDisplayStyle(
  root: Object3D,
  appearance: ModelAppearance,
  style: ModelDisplayStyle,
  derived: Map<Material, Material>,
  palette: ModelingPalette,
): void {
  const paint = (material: Material): Material => {
    let stand = derived.get(material);
    if (stand === undefined) {
      stand = modelingMaterial(material, palette);
      derived.set(material, stand);
    }
    return stand;
  };
  root.traverse((object) => {
    if (!(object instanceof Mesh || object instanceof Line)) return;
    const own = appearance.materials.get(object);
    if (own === undefined) return;
    if (style === "original") object.material = own;
    else object.material = Array.isArray(own) ? own.map(paint) : paint(own);
  });
}

/** Dispose the stand-ins a style made; the file's own materials are never disposed here. */
export function disposeDisplayMaterials(derived: Map<Material, Material>): void {
  for (const [own, stand] of derived) if (stand !== own) stand.dispose();
  derived.clear();
}

/**
 * Clip-space depth pulled toward the camera for edge lines: about 17 steps of
 * a 24-bit depth buffer, enough that an edge wins against the face it bounds
 * without its surface being pushed back behind anything else.
 */
export const EDGE_DEPTH_BIAS = 2e-6;

/** A line material drawn a few depth steps in front of where its vertices lie. */
function edgeMaterial(parameters: ConstructorParameters<typeof LineBasicMaterial>[0]): LineBasicMaterial {
  // Drawn only in the Modeling look, so not tone mapped, like its surfaces.
  const material = new LineBasicMaterial({ ...parameters, toneMapped: false });
  material.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader.replace("#include <project_vertex>",
      `#include <project_vertex>\n\tgl_Position.z -= ${EDGE_DEPTH_BIAS.toExponential()} * gl_Position.w;`);
  };
  material.customProgramCacheKey = () => "archflow-feature-edge";
  return material;
}

export interface FeatureEdgeLine {
  /** The file's surface these edges belong to. */
  readonly source: Mesh;
  readonly line: LineSegments;
  readonly translucent: boolean;
}

export interface FeatureEdgeOverlay {
  readonly root: Object3D;
  /** Kept beside the model, never under it: picking, snapping and bounds do not see it. */
  readonly group: Group;
  readonly lines: readonly FeatureEdgeLine[];
  readonly materials: { readonly solid: LineBasicMaterial; readonly translucent: LineBasicMaterial; readonly selected: LineBasicMaterial };
  /** Each edge material's own opacity, before a comparison fades it. */
  readonly opacity: { readonly solid: number; readonly translucent: number; readonly selected: number };
  readonly geometries: readonly BufferGeometry[];
}

/**
 * Thin lines along the edges a person can see on every surface of ``root``.
 *
 * ``edgesOf`` supplies each surface's feature edges - boundaries and real
 * folds - so a flat face triangulated to draw it shows no diagonal; the
 * triangulation itself is never drawn. Surfaces sharing one geometry share one
 * line geometry. Whether a surface is translucent is read from the file's own
 * material in ``appearance``, not from a highlight or a cross-fade.
 */
export function buildFeatureEdgeOverlay(
  root: Object3D,
  appearance: ModelAppearance,
  edgesOf: (mesh: Mesh) => readonly FeatureEdge[],
  palette: ModelingPalette,
  selectedColour: string,
): FeatureEdgeOverlay {
  const group = new Group();
  group.name = "archflow-feature-edges";
  const materials = {
    solid: edgeMaterial({ color: palette.edge }),
    translucent: edgeMaterial({ color: palette.edge, transparent: true, opacity: palette.translucentEdgeOpacity, depthWrite: false }),
    selected: edgeMaterial({ color: selectedColour }),
  };
  const shared = new Map<BufferGeometry, BufferGeometry>();
  const lines: FeatureEdgeLine[] = [];
  root.traverse((object) => {
    if (!(object instanceof Mesh) || !object.geometry?.hasAttribute("position")) return;
    let geometry = shared.get(object.geometry);
    if (geometry === undefined) {
      const positions: number[] = [];
      for (const edge of edgesOf(object)) positions.push(...edge.a, ...edge.b);
      geometry = new BufferGeometry();
      geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
      shared.set(object.geometry, geometry);
    }
    if (geometry.getAttribute("position").count === 0) return;
    const own = appearance.materials.get(object) ?? object.material;
    const translucent = (Array.isArray(own) ? own : [own]).some(seeThrough);
    const line = new LineSegments(geometry, translucent ? materials.translucent : materials.solid);
    line.name = `${object.name}:edges`;
    line.matrixAutoUpdate = false;
    group.add(line);
    lines.push({ source: object, line, translucent });
  });
  const opacity = { solid: 1, translucent: palette.translucentEdgeOpacity, selected: 1 };
  return { root, group, lines, materials, opacity, geometries: [...shared.values()] };
}

/**
 * Follow the model before a frame: an edge shows only while its surface is
 * actually displayed (file, layer, draft hiding or a cross-fade), sits where
 * its surface sits, and takes the selection colour while its surface is
 * marked. ``weight`` fades the edges with their model during a comparison.
 */
export function syncFeatureEdgeOverlay(
  overlay: FeatureEdgeOverlay,
  selected: ReadonlySet<Object3D>,
  weight = 1,
): void {
  overlay.root.updateMatrixWorld();
  const scale = Math.min(1, Math.max(0, weight));
  for (const { source, line, translucent } of overlay.lines) {
    line.visible = scale > 0 && isDisplayed(source);
    if (!line.visible) continue;
    line.matrix.copy(source.matrixWorld);
    line.matrixWorldNeedsUpdate = true;
    line.material = selected.has(source) ? overlay.materials.selected
      : translucent ? overlay.materials.translucent : overlay.materials.solid;
  }
  for (const [kind, material] of Object.entries(overlay.materials) as [keyof FeatureEdgeOverlay["opacity"], LineBasicMaterial][]) {
    const opacity = overlay.opacity[kind] * scale;
    if (material.opacity === opacity) continue;
    material.opacity = opacity;
    material.transparent = opacity < 1;
    material.needsUpdate = true;
  }
}

export function disposeFeatureEdgeOverlay(overlay: FeatureEdgeOverlay): void {
  overlay.group.removeFromParent();
  for (const geometry of overlay.geometries) geometry.dispose();
  for (const material of Object.values(overlay.materials)) material.dispose();
}

/** Restore visibility, layer flags and the exact material references loaded from the file. */
export function restoreModelAppearance(root: Object3D, appearance: ModelAppearance): void {
  root.traverse((object) => {
    const visible = appearance.visibility.get(object);
    if (visible !== undefined) object.visible = visible;
    if (object instanceof Mesh || object instanceof Line) {
      const material = appearance.materials.get(object);
      if (material !== undefined) object.material = material;
    }
    const savedLayers = appearance.layerVisibility.get(object);
    const layers = object.userData.layers;
    if (savedLayers && Array.isArray(layers)) {
      savedLayers.forEach((saved, index) => {
        const layer = layers[index];
        if (saved !== undefined && typeof layer === "object" && layer !== null) {
          layer.visible = saved;
        }
      });
    }
  });
}
