export type Vec3 = [number, number, number];
export type ImageRef = { runId: string; assetSha256: string; revisionRef: string | null };
export type Material = { id: string; name: string; baseColor: string; roughness: number; metallic: number; texture: ImageRef | null; normalMap: ImageRef | null };
export type Region = { id: string; name: string; materialId: string; mesh: number; shape: "box" | "ellipsoid"; center: Vec3; radius: Vec3; geometryRevision: string };
export type Light = { id: string; type: "area" | "point" | "sun"; position: Vec3; target: Vec3; intensity: number; color: string; size: number };
export type Camera = { position: Vec3; target: Vec3; up: Vec3; projection: "perspective" | "orthographic"; fov: number; orthoScale: number };
export type PhysicalScene = {
  geometryRevision: string; materials: Material[]; assignments: Record<string, string>; regions: Region[]; lights: Light[];
  environment: { color: string; strength: number; background: ImageRef | null; environmentMap: ImageRef | null };
  camera: Camera; exposure: number; settings: { width: number; height: number; samples: number; denoise: boolean };
};
export type Geometry = { source: { geometryRevision: string; kind?: string; warnings?: string[]; runId?: string; assetSha256?: string; preview?: { runId: string; assetSha256: string }; modelSource?: { runId: string; assetSha256: string } }; meshes: { name: string; vertices: Vec3[]; triangles: [number, number, number][]; normals: Vec3[] | null; texcoords: [number, number][] | null }[] };
export type SceneState = { scene: PhysicalScene | null; sceneRevision: string | null; status: string; cyclesAvailable?: boolean };

/** User-selected source mesh bounds; no inferred semantic category or fixed coordinates. */
export function meshRegion(geometry: Geometry, mesh: number, materialId: string, id: string): Region {
  const source = geometry.meshes[mesh];
  if (!source?.vertices.length) throw new Error("Select a non-empty source mesh.");
  const low: Vec3 = [Infinity, Infinity, Infinity], high: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const vertex of source.vertices) for (let axis = 0; axis < 3; axis++) {
    low[axis] = Math.min(low[axis]!, vertex[axis]!);
    high[axis] = Math.max(high[axis]!, vertex[axis]!);
  }
  const extent = Math.max(...high.map((v, axis) => v - low[axis]!));
  const padding = Math.max(extent * 1e-7, 1e-9);
  return { id, name: source.name || `Mesh ${mesh}`, materialId, mesh, shape: "box",
    center: low.map((v, axis) => (v + high[axis]!) / 2) as Vec3,
    radius: low.map((v, axis) => (high[axis]! - v) / 2 + padding) as Vec3,
    geometryRevision: geometry.source.geometryRevision };
}

export function triangleMaterials(mesh: Geometry["meshes"][number], index: number, scene: PhysicalScene): number[] {
  const ids = scene.materials.map(m => m.id), base = ids.indexOf(scene.assignments[String(index)]!);
  return mesh.triangles.map(face => {
    const center = [0, 1, 2].map(axis => face.reduce((sum, vertex) => sum + mesh.vertices[vertex]![axis]!, 0) / 3);
    let selected = base;
    for (const region of scene.regions.filter(r => r.mesh === index)) {
      const q = center.map((v, axis) => (v - region.center[axis]!) / region.radius[axis]!);
      if (region.shape === "box" ? Math.max(...q.map(Math.abs)) <= 1 : q.reduce((sum, v) => sum + v*v, 0) <= 1) selected = ids.indexOf(region.materialId);
    }
    return selected;
  });
}
