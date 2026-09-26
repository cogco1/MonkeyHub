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
export type Geometry = { source: { geometryRevision: string }; meshes: { name: string; vertices: Vec3[]; triangles: [number, number, number][]; normals: Vec3[] | null; texcoords: [number, number][] | null }[] };
export type SceneState = { scene: PhysicalScene | null; sceneRevision: string | null; status: string; cyclesAvailable?: boolean };

/** Suggestions are a draft until the user explicitly applies them; all masks name this mesh revision. */
export function penguinSuggestions(scene: PhysicalScene): Pick<PhysicalScene, "materials" | "regions" | "assignments"> {
  const materials: Material[] = [
    ["body", "Dark back / head", "#252b33", .65], ["belly", "White belly", "#ece4ce", .78],
    ["beak", "Beak", "#de922c", .38], ["eye", "Eye white", "#fcf8ee", .22],
    ["pupil", "Pupil", "#11151a", .18], ["feet", "Feet", "#403b35", .55],
  ].map(([id, name, baseColor, roughness]) => ({ id: String(id), name: String(name), baseColor: String(baseColor), roughness: Number(roughness), metallic: 0, texture: null, normalMap: null }));
  const region = (id: string, materialId: string, shape: Region["shape"], center: Vec3, radius: Vec3): Region => ({ id, name: id, materialId, shape, center, radius, mesh: 0, geometryRevision: scene.geometryRevision });
  return { materials, assignments: Object.fromEntries(Object.keys(scene.assignments).map(id => [id, "body"])), regions: [
    region("belly", "belly", "ellipsoid", [0, -.19, .39], [.215, .21, .36]),
    region("beak", "beak", "box", [0, -.29, .873], [.15, .14, .04]),
    region("feet", "feet", "box", [0, 0, .025], [.4, .4, .04]),
    region("eye-right", "eye", "ellipsoid", [.116, .001, .875], [.032, .044, .038]),
    region("eye-left", "eye", "ellipsoid", [-.116, -.001, .879], [.032, .044, .038]),
    region("pupil-right", "pupil", "ellipsoid", [.136, -.014, .886], [.024, .017, .019]),
    region("pupil-left", "pupil", "ellipsoid", [-.136, -.016, .890], [.024, .017, .019]),
  ] };
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
