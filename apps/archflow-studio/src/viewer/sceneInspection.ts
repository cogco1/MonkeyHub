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

export function inspectScene(
  root: Object3D,
  file: File,
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

