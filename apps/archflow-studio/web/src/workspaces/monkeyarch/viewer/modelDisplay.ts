import { Mesh, type Material, type Object3D } from "three";

export type ModelDisplayMode = "model" | "framework" | "massing";

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

/** Clicking an open projection returns to the model; every other click selects it. */
export function nextModelDisplayMode(
  current: ModelDisplayMode,
  requested: ModelDisplayMode,
): ModelDisplayMode {
  return requested !== "model" && requested === current ? "model" : requested;
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

/**
 * Give a freshly parsed model the display state its file saved: the layer
 * visibility the loader applied, and each object's own saved visibility on
 * top of it. Every loaded model - the reference, a local file, the second
 * side of a comparison - passes through here before its appearance is
 * captured, so a hidden construction object is remembered as hidden and no
 * restoration brings it back.
 */
export function prepareLoadedModel<T extends Object3D>(root: T): T {
  root.traverse((object) => {
    if (object.visible && !savedObjectVisible(object)) object.visible = false;
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
  readonly materials: WeakMap<Mesh, Material | Material[]>;
  readonly layerVisibility: WeakMap<Object3D, ReadonlyArray<boolean | undefined>>;
}

/** Remember the loaded file's display state before any temporary projection touches it. */
export function captureModelAppearance(root: Object3D): ModelAppearance {
  const visibility = new WeakMap<Object3D, boolean>();
  const materials = new WeakMap<Mesh, Material | Material[]>();
  const layerVisibility = new WeakMap<Object3D, ReadonlyArray<boolean | undefined>>();
  root.traverse((object) => {
    visibility.set(object, object.visible);
    if (object instanceof Mesh) materials.set(object, object.material);
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

/** Restore visibility, layer flags and the exact material references loaded from the file. */
export function restoreModelAppearance(root: Object3D, appearance: ModelAppearance): void {
  root.traverse((object) => {
    const visible = appearance.visibility.get(object);
    if (visible !== undefined) object.visible = visible;
    if (object instanceof Mesh) {
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
