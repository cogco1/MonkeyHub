import {
  BufferGeometry, Color, Float32BufferAttribute, Group, Line, LineBasicMaterial, LineSegments, Mesh, MeshLambertMaterial,
  MeshStandardMaterial, Points, SRGBColorSpace, Texture, type Material, type Object3D, type WebGLProgramParametersWithUniforms,
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

/** Set on a loader material once its file colours are decoded, so a material many objects share is decoded once. */
const DECODED_FILE_COLOURS = "fileColoursDecoded";

/** The objects three's 3DM loader draws in a colour it read off the file: its curves and points. */
const DRAWN_IN_FILE_COLOUR = new Set(["Curve", "Point", "PointSet"]);

/**
 * Decode the colours three's Rhino3dmLoader read off the file into the renderer's working colour space.
 *
 * Rhino saves every colour as sRGB channels, and the renderer encodes its working (linear)
 * colours to sRGB on output. The loader builds a material's diffuse colour as
 * ``new Color(r / 255, g / 255, b / 255)`` - a PBR base colour and a curve's or point's draw
 * colour the same way - which three takes as already linear, so the output encoding lifted
 * every one of them: a declared #687073 left the renderer as #abb1b3 before any light, and
 * Original showed declared colours far paler than the file says. The emission colour it passes
 * as raw 0-255 channels. Each is decoded here, in place: the loader shares one material among
 * the objects that wear it (and among the files of one batch), so the material is marked and
 * decoded once. Only what the loader made from the file is touched - a material from the file's
 * material table (it carries the table's ``userData.id``), or the material of a curve or point
 * the loader drew; layer and object display colours already arrive decoded (savedDisplayColor),
 * and anything this viewer drew itself is left alone.
 */
function decodeFileColours(object: Object3D): void {
  const drawn = (object instanceof Line || object instanceof Points) && DRAWN_IN_FILE_COLOUR.has(object.userData.objectType);
  if (!drawn && !(object instanceof Mesh)) return;
  const worn = (object as Mesh | Line | Points).material;
  for (const material of Array.isArray(worn) ? worn : [worn]) {
    if (material.userData[DECODED_FILE_COLOURS] === true || (!drawn && material.userData.id === undefined)) continue;
    const { color, emissive } = material as { color?: unknown; emissive?: unknown };
    if (color instanceof Color) color.setRGB(color.r, color.g, color.b, SRGBColorSpace);
    if (emissive instanceof Color && !drawn) emissive.setRGB(emissive.r / 255, emissive.g / 255, emissive.b / 255, SRGBColorSpace);
    material.userData[DECODED_FILE_COLOURS] = true;
  }
}

/**
 * Give a freshly parsed model the display state its file saved: the layer
 * visibility the loader applied, and each object's own saved visibility on
 * top of it. Every loaded model - the reference, a local file, the second
 * side of a comparison - passes through here before its appearance is
 * captured, so a hidden construction object is remembered as hidden and no
 * restoration brings it back. The colours the loader read off the file are
 * decoded from sRGB (decodeFileColours), and the loader's unassigned white mesh
 * material uses the file's display color; textures stay intact.
 */
export function prepareLoadedModel<T extends Object3D>(root: T): T {
  root.traverse((object) => {
    if (object.visible && !savedObjectVisible(object)) object.visible = false;
    decodeFileColours(object);
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
  /** Each surface's own cast and receive shadow flags; only Presentation's sun draws shadows. */
  readonly shadows: WeakMap<Mesh, readonly [cast: boolean, receive: boolean]>;
}

/** Remember the loaded file's display state before any temporary projection touches it. */
export function captureModelAppearance(root: Object3D): ModelAppearance {
  const visibility = new WeakMap<Object3D, boolean>();
  const materials = new WeakMap<Mesh | Line, Material | Material[]>();
  const layerVisibility = new WeakMap<Object3D, ReadonlyArray<boolean | undefined>>();
  const shadows = new WeakMap<Mesh, readonly [boolean, boolean]>();
  root.traverse((object) => {
    visibility.set(object, object.visible);
    if (object instanceof Mesh || object instanceof Line) materials.set(object, object.material);
    if (object instanceof Mesh) shadows.set(object, [object.castShadow, object.receiveShadow]);
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
  return { visibility, materials, layerVisibility, shadows };
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
 * file's muted colours, thin feature edges, plain light. ``presentation`` (展示,
 * #562) previews a restrained offline look in real time: matte surfaces in the
 * colours the file's materials declare, one sun with soft shadows over a sky
 * fill, faint edges, on paper; a surface whose component declares no material
 * keeps its distinction colour under a fine hatch. None of them touches geometry,
 * names, visibility or anything a project records; the file's own materials are
 * never modified.
 */
export type ModelDisplayStyle = "original" | "modeling" | "presentation";

/** Every style, in the order the display selector lists them. */
export const MODEL_DISPLAY_STYLES: readonly ModelDisplayStyle[] = ["modeling", "original", "presentation"];

/** The look a viewer opens in; Original stays one switch away. */
export const DEFAULT_MODEL_DISPLAY_STYLE: ModelDisplayStyle = "modeling";

/** Where this browser keeps the style last chosen, so a refreshed page opens in it. */
export const DISPLAY_STYLE_STORAGE_KEY = "monkeyhub.model-display-style.v1";

function browserStorage(): Storage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; }
}

/** The style this browser chose last: the default when it chose none, or none it still knows, or cannot say. */
export function rememberedDisplayStyle(storage: Pick<Storage, "getItem"> | null = browserStorage()): ModelDisplayStyle {
  try {
    const saved = storage?.getItem(DISPLAY_STYLE_STORAGE_KEY);
    return MODEL_DISPLAY_STYLES.find((style) => style === saved) ?? DEFAULT_MODEL_DISPLAY_STYLE;
  } catch {
    return DEFAULT_MODEL_DISPLAY_STYLE;
  }
}

/** Keep the chosen style for the next page; a browser that cannot keep it still shows it on this one. */
export function rememberDisplayStyle(style: ModelDisplayStyle, storage: Pick<Storage, "setItem"> | null = browserStorage()): void {
  try { storage?.setItem(DISPLAY_STYLE_STORAGE_KEY, style); } catch { /* The choice still holds on this page. */ }
}

/** How feature edges are drawn: their colour, how strongly, and how faintly over a see-through surface. */
export interface EdgePalette {
  readonly edge: string;
  /** Solid edges' opacity; fully opaque when not given. */
  readonly edgeOpacity?: number;
  /** Edges of a translucent surface stay visible but lighter than solid ones. */
  readonly translucentEdgeOpacity: number;
}

export interface ModelingPalette extends EdgePalette {
  readonly background: string;
  /** The pale base every surface is lifted toward. */
  readonly paper: string;
  /** How much of the file's own colour survives in a pale surface, 0..1. */
  readonly tint: number;
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

/**
 * Presentation's paper and lines, the same in a light or a dark shell: a still made
 * from it does not depend on the theme it was made in. The paper is the offline
 * look's warm white; an edge is a faint darkening of the face it bounds, as that
 * look drew each boundary in a darker shade of its own material.
 */
export const PRESENTATION_PALETTE: EdgePalette & { readonly background: string } = {
  background: "#f7f6f2", edge: "#1c1a17", edgeOpacity: 0.26, translucentEdgeOpacity: 0.12,
};

/** The user string an export writes on an object whose components declare no material (#560), and its value. */
export const MATERIAL_STATUS_KEY = "archflow:material_status";
export const UNDECLARED_MATERIAL = "undeclared";

/** Whether an object's own strings say its components declare no material; nothing else is read into it. */
export function declaresNoMaterial(userStrings: Readonly<Record<string, string>> | null | undefined): boolean {
  return userStrings?.[MATERIAL_STATUS_KEY] === UNDECLARED_MATERIAL;
}

/** Whether a material lets the model behind it through, by opacity or by transmission. */
function seeThrough(material: Material): boolean {
  const transmission = (material as { transmission?: unknown }).transmission;
  return (material.transparent && material.opacity < 1) || (typeof transmission === "number" && transmission > 0);
}

/** What a stand-in keeps of a material's see-through state; glass the loader made physical (transmission) shows as opacity. */
function seeThroughState(material: Material): { transparent: boolean; opacity: number; depthWrite: boolean } {
  const transmission = (material as { transmission?: unknown }).transmission;
  const opacity = material.transparent ? material.opacity
    : typeof transmission === "number" && transmission > 0 ? Math.max(0.25, 1 - transmission) : 1;
  return { transparent: material.transparent || opacity < 1, opacity, depthWrite: material.depthWrite && opacity >= 1 };
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
  const surface = new MeshStandardMaterial({
    color: colour,
    roughness: 0.9,
    metalness: 0,
    toneMapped: false,
    side: material.side,
    ...seeThroughState(material),
  });
  surface.name = material.name;
  surface.userData.displayStyle = "modeling";
  return surface;
}

/** How far apart, how wide (device pixels) and how dark the lines of the undeclared hatch are. */
export const UNDECLARED_HATCH = { spacing: 7, width: 1.5, depth: 0.3 } as const;

/**
 * A fine 45-degree hatch over the lit colour: a thin line every few pixels of the
 * picture darkens the distinction colour beneath it. It is laid in screen pixels, so
 * it reads the same on a small part and a large one, and in a still as on screen.
 */
const UNDECLARED_HATCH_GLSL = `
	{
		float archflowUndeclared = 1.0 - step( ${UNDECLARED_HATCH.width.toFixed(1)}, mod( gl_FragCoord.x + gl_FragCoord.y, ${UNDECLARED_HATCH.spacing.toFixed(1)} ) );
		outgoingLight *= 1.0 - ${UNDECLARED_HATCH.depth.toFixed(2)} * archflowUndeclared;
	}
`;

/**
 * Presentation's surface for an object whose components declare no material: its own
 * (distinction) colour, matte, under the undeclared hatch. The hatch belongs to the
 * class, not to one instance, so a copy - a selection mark clones what an object
 * wears - is hatched too.
 */
export class UndeclaredSurfaceMaterial extends MeshLambertMaterial {
  readonly isUndeclaredSurface = true;

  onBeforeCompile(shader: WebGLProgramParametersWithUniforms): void {
    shader.fragmentShader = shader.fragmentShader.replace("#include <opaque_fragment>", `${UNDECLARED_HATCH_GLSL}#include <opaque_fragment>`);
  }

  customProgramCacheKey(): string {
    return "archflow-undeclared-hatch";
  }
}

/**
 * Presentation's stand-in for one of the file's materials (#562): a matte (Lambert)
 * surface in the material's own colour - the colour its material declares, decoded
 * from the file's sRGB by prepareLoadedModel - lit by Presentation's sun and sky and
 * not tone mapped, so a face turned square to the sun shows that colour itself and
 * the rest of the model the same colour in shade. It keeps what the file says about
 * seeing through, so glass stays glass. ``undeclared`` gives a surface whose
 * components declare no material the hatched surface: its colour is still its own,
 * the distinction colour the export gave it, never one picked from a name. Curves
 * keep their own colour; points keep their own material.
 *
 * Textures stay with Original. No texture can be declared yet (#560 declares a
 * material's name and colour), and none is guessed from a material's name or taken
 * from a file's bitmap. A texture a material's appearance declares would be applied
 * here, as the stand-in's ``map``: this is the one place Presentation's surfaces are made.
 */
export function presentationMaterial(material: Material, undeclared = false): Material {
  if (material instanceof LineBasicMaterial) {
    const line = new LineBasicMaterial({
      color: material.color, transparent: material.transparent, opacity: material.opacity, depthWrite: material.depthWrite,
      toneMapped: false,
    });
    line.name = material.name;
    line.userData.displayStyle = "presentation";
    return line;
  }
  if (material.type === "PointsMaterial") return material;
  const own = (material as { color?: unknown }).color;
  const parameters = { color: own instanceof Color ? own : new Color(1, 1, 1), side: material.side, toneMapped: false, ...seeThroughState(material) };
  const surface = undeclared ? new UndeclaredSurfaceMaterial(parameters) : new MeshLambertMaterial(parameters);
  surface.name = material.name;
  surface.userData.displayStyle = "presentation";
  return surface;
}

/**
 * Paint every captured primitive under ``root`` in ``style``.
 *
 * The file's own materials come from ``appearance``, never from what an
 * object wears now, so switching back returns the exact references the loader
 * made, and each surface its own shadow flags. ``derived`` holds one style's
 * stand-ins: one per original material, shared the way the originals are
 * shared, and in Presentation one hatched copy of a stand-in for the surfaces
 * ``undeclared`` names, kept under the stand-in it copies. In Presentation a
 * surface casts the sun's shadow where the file lets it (Rhino's own default
 * is yes) unless one sees through it, and receives it where the file lets it.
 * A primitive the appearance never saw is left alone. The caller takes any
 * selection mark off first and puts it back after.
 */
export function applyDisplayStyle(
  root: Object3D,
  appearance: ModelAppearance,
  style: ModelDisplayStyle,
  derived: Map<Material, Material>,
  palette: ModelingPalette,
  undeclared: (object: Object3D) => boolean = () => false,
): void {
  const standIn = (material: Material): Material => {
    let stand = derived.get(material);
    if (stand === undefined) {
      stand = style === "presentation" ? presentationMaterial(material) : modelingMaterial(material, palette);
      derived.set(material, stand);
    }
    return stand;
  };
  const hatched = (material: Material): Material => {
    const plain = standIn(material);
    let marked = derived.get(plain);
    if (marked === undefined) {
      marked = presentationMaterial(material, true);
      derived.set(plain, marked);
    }
    return marked;
  };
  root.traverse((object) => {
    if (!(object instanceof Mesh || object instanceof Line)) return;
    const own = appearance.materials.get(object);
    if (own === undefined) return;
    const shadows = object instanceof Mesh ? appearance.shadows.get(object) : undefined;
    if (object instanceof Mesh && shadows !== undefined) [object.castShadow, object.receiveShadow] = shadows;
    if (style === "original") {
      object.material = own;
      return;
    }
    const paint = style === "presentation" && object instanceof Mesh && undeclared(object) ? hatched : standIn;
    const worn = Array.isArray(own) ? own.map(paint) : paint(own);
    object.material = worn;
    if (style === "presentation" && object instanceof Mesh) {
      const attributes = object.userData.attributes as { castsShadows?: unknown; receivesShadows?: unknown } | undefined;
      object.castShadow = attributes?.castsShadows !== false && !(Array.isArray(worn) ? worn : [worn]).some(seeThrough);
      object.receiveShadow = attributes?.receivesShadows !== false;
    }
  });
}

/** What Presentation tells a person beside the model: the materials it paints, and how many objects declare none. */
export interface PresentationLegend {
  /** Each material from the file's table a shown object wears, by its name, in its colour (#rrggbb). */
  readonly materials: ReadonlyArray<{ readonly name: string; readonly colour: string }>;
  /** Shown objects whose components declare no material: the hatched ones. */
  readonly undeclared: number;
}

/**
 * The legend of a model in Presentation, read off the file's own materials as the
 * appearance holds them and the objects ``undeclared`` names; objects the file
 * hid are not part of the picture and not counted.
 */
export function presentationLegend(
  root: Object3D,
  appearance: ModelAppearance,
  undeclared: (object: Object3D) => boolean,
): PresentationLegend {
  const materials = new Map<string, { name: string; colour: string }>();
  let count = 0;
  root.traverse((object) => {
    if (!(object instanceof Mesh) || appearance.visibility.get(object) === false) return;
    const own = appearance.materials.get(object);
    if (own === undefined) return;
    if (undeclared(object)) {
      count += 1;
      return;
    }
    for (const material of Array.isArray(own) ? own : [own]) {
      const colour = (material as { color?: unknown }).color;
      if (material.userData.id === undefined || !(colour instanceof Color)) continue;
      const entry = { name: material.name, colour: `#${colour.getHexString(SRGBColorSpace)}` };
      materials.set(`${entry.name}\u0000${entry.colour}`, entry);
    }
  });
  return {
    materials: [...materials.values()].sort((a, b) => a.name.localeCompare(b.name) || a.colour.localeCompare(b.colour)),
    undeclared: count,
  };
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
  palette: EdgePalette,
  selectedColour: string,
): FeatureEdgeOverlay {
  const group = new Group();
  group.name = "archflow-feature-edges";
  const solidOpacity = palette.edgeOpacity ?? 1;
  const materials = {
    solid: edgeMaterial(solidOpacity < 1
      ? { color: palette.edge, transparent: true, opacity: solidOpacity, depthWrite: false } : { color: palette.edge }),
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
  const opacity = { solid: solidOpacity, translucent: palette.translucentEdgeOpacity, selected: 1 };
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

/** Restore visibility, layer flags, shadow flags and the exact material references loaded from the file. */
export function restoreModelAppearance(root: Object3D, appearance: ModelAppearance): void {
  root.traverse((object) => {
    const visible = appearance.visibility.get(object);
    if (visible !== undefined) object.visible = visible;
    if (object instanceof Mesh || object instanceof Line) {
      const material = appearance.materials.get(object);
      if (material !== undefined) object.material = material;
    }
    const shadows = object instanceof Mesh ? appearance.shadows.get(object) : undefined;
    if (object instanceof Mesh && shadows !== undefined) [object.castShadow, object.receiveShadow] = shadows;
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
