import {
  Box3,
  BufferGeometry,
  Line,
  LineSegments,
  Material,
  Mesh,
  Object3D,
  Points,
  Texture,
  Vector3,
} from "three";

export interface LayerInspection {
  index: number;
  name: string;
  visible: boolean;
  objectCount: number;
}

export interface SceneInspection {
  fileName: string;
  fileSize: number;
  loadDurationMs: number;
  objectCount: number;
  meshCount: number;
  curveCount: number;
  pointCount: number;
  vertexCount: number;
  triangleCount: number;
  bounds: {
    min: [number, number, number];
    max: [number, number, number];
    size: [number, number, number];
  } | null;
  layers: LayerInspection[];
  warnings: string[];
}

interface RhinoLayerLike {
  name?: unknown;
  visible?: unknown;
}

function finiteTuple(vector: Vector3): [number, number, number] {
  return [vector.x, vector.y, vector.z].map((value) =>
    Number.isFinite(value) ? value : 0,
  ) as [number, number, number];
}

/** A picked object's user strings, exactly as the file carries them. */
export type UserStrings = Record<string, string>;

function pairOf(entry: unknown): readonly [string, string] | null {
  if (Array.isArray(entry) && entry.length >= 2) {
    const [key, value] = entry as unknown[];
    return typeof key === "string" && typeof value === "string"
      ? [key, value]
      : null;
  }
  if (typeof entry === "object" && entry !== null) {
    const record = entry as Record<string, unknown>;
    const key = record["0"] ?? record.key;
    const value = record["1"] ?? record.value;
    return typeof key === "string" && typeof value === "string"
      ? [key, value]
      : null;
  }
  return null;
}

/**
 * User strings as a record, whatever shape the loader handed them over in.
 *
 * rhino3dm returns `getUserStrings()` as an array of `[key, value]` pairs, and
 * the 3DM loader passes that through its worker where a structured clone can
 * turn an array into an index-keyed object. Both are read here, and so is a
 * plain `key: value` record. Nothing is renamed, filtered or interpreted: what
 * the file says is what goes to the server, which is the only place a pick is
 * resolved.
 */
export function toUserStrings(value: unknown): UserStrings {
  const strings: UserStrings = {};
  if (typeof value !== "object" || value === null) return strings;
  const entries = Array.isArray(value)
    ? (value as unknown[])
    : Object.values(value as Record<string, unknown>);
  let pairs = 0;
  for (const entry of entries) {
    const pair = pairOf(entry);
    if (pair !== null) {
      strings[pair[0]] = pair[1];
      pairs += 1;
    }
  }
  if (pairs > 0) return strings;
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === "string") strings[key] = item;
  }
  return strings;
}

/**
 * The nearest object at or above this one that carries user strings.
 *
 * The loader hangs `userData.attributes` on the object it made from a Rhino
 * object; a click can land on a child mesh of it, so the walk goes upwards. It
 * stops at the first carrier and never merges two objects' strings together.
 */
export function userStringCarrier(object: Object3D): Object3D | null {
  let current: Object3D | null = object;
  while (current !== null) {
    const attributes = current.userData.attributes as
      | { userStrings?: unknown }
      | undefined;
    if (attributes?.userStrings !== undefined) return current;
    current = current.parent;
  }
  return null;
}

/**
 * The document's own user strings, when the loader exposes them.
 *
 * three 0.185.1's `3DMLoader` reads them off the file and then does not attach
 * them to the scene graph, so this is normally `null` and the server answers
 * `sourceState: "unknown"` — which is the truthful answer: the file has not
 * been shown to be current, and unknown is not current. The lookup stays here
 * so that a loader which does expose them is read rather than ignored. The
 * client never substitutes what it thinks the file should say.
 */
export function documentUserStrings(root: Object3D): UserStrings | null {
  const settings = root.userData.settings as
    | { strings?: unknown }
    | undefined;
  const raw = root.userData.strings ?? settings?.strings;
  if (raw === undefined || raw === null) return null;
  const strings = toUserStrings(raw);
  return Object.keys(strings).length > 0 ? strings : null;
}

export function layerIndexOf(object: Object3D): number | null {
  const attributes = object.userData.attributes as
    | { layerIndex?: unknown }
    | undefined;
  const candidate = attributes?.layerIndex ?? object.userData.layerIndex;
  return typeof candidate === "number" && Number.isInteger(candidate)
    ? candidate
    : null;
}

function warningMessages(root: Object3D): string[] {
  const warnings = root.userData.warnings;
  if (!Array.isArray(warnings)) return [];
  const messages = warnings
    .map((warning) => {
      if (typeof warning === "string") return warning;
      if (
        typeof warning === "object" &&
        warning !== null &&
        "message" in warning &&
        typeof warning.message === "string"
      ) {
        return warning.message;
      }
      return null;
    })
    .filter((message): message is string => message !== null);
  return [...new Set(messages)];
}

/**
 * What the loaded picture is made of.
 *
 * ``file`` is only asked for a name and a size, so one `File` and a whole
 * run's worth of them (named together, sized together) are inspected the same
 * way: the counts come from the scene graph, which is one model or a group of
 * them.
 */
export function inspectScene(
  root: Object3D,
  file: { name: string; size: number },
  loadDurationMs: number,
): SceneInspection {
  let objectCount = 0;
  let meshCount = 0;
  let curveCount = 0;
  let pointCount = 0;
  let vertexCount = 0;
  let triangleCount = 0;
  const layerCounts = new Map<number, number>();

  root.traverse((object) => {
    if (object !== root) objectCount += 1;
    const layerIndex = layerIndexOf(object);
    if (layerIndex !== null) {
      layerCounts.set(layerIndex, (layerCounts.get(layerIndex) ?? 0) + 1);
    }

    if (object instanceof Mesh) {
      meshCount += 1;
      const geometry = object.geometry as BufferGeometry;
      const positions = geometry.getAttribute("position");
      const vertices = positions?.count ?? 0;
      vertexCount += vertices;
      triangleCount += geometry.index
        ? Math.floor(geometry.index.count / 3)
        : Math.floor(vertices / 3);
    } else if (object instanceof Line || object instanceof LineSegments) {
      curveCount += 1;
    } else if (object instanceof Points) {
      pointCount += 1;
    }
  });

  const rawLayers = Array.isArray(root.userData.layers)
    ? (root.userData.layers as RhinoLayerLike[])
    : [];
  const knownIndexes = new Set<number>([
    ...rawLayers.map((_, index) => index),
    ...layerCounts.keys(),
  ]);
  const layers = [...knownIndexes]
    .sort((left, right) => left - right)
    .map((index) => {
      const layer = rawLayers[index];
      return {
        index,
        name:
          typeof layer?.name === "string" && layer.name.trim()
            ? layer.name
            : `Layer ${index}`,
        visible: typeof layer?.visible === "boolean" ? layer.visible : true,
        objectCount: layerCounts.get(index) ?? 0,
      };
    });

  const box = new Box3().setFromObject(root);
  const bounds = box.isEmpty()
    ? null
    : {
        min: finiteTuple(box.min),
        max: finiteTuple(box.max),
        size: finiteTuple(box.getSize(new Vector3())),
      };

  return {
    fileName: file.name,
    fileSize: file.size,
    loadDurationMs,
    objectCount,
    meshCount,
    curveCount,
    pointCount,
    vertexCount,
    triangleCount,
    bounds,
    layers,
    warnings: warningMessages(root),
  };
}

function disposeMaterial(material: Material): void {
  const values = Object.values(material) as unknown[];
  for (const value of values) {
    if (value instanceof Texture) value.dispose();
  }
  material.dispose();
}

export function disposeScene(root: Object3D): void {
  root.traverse((object) => {
    if (!(object instanceof Mesh || object instanceof Line || object instanceof Points)) {
      return;
    }
    const geometry = object.geometry as BufferGeometry | undefined;
    geometry?.dispose();
    const material = object.material as Material | Material[] | undefined;
    if (Array.isArray(material)) material.forEach(disposeMaterial);
    else if (material) disposeMaterial(material);
  });
}

