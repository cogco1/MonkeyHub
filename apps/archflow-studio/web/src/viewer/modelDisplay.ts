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
