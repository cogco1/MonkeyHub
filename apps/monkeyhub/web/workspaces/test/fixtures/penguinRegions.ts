import type { PhysicalScene, Material, Region, Vec3 } from "../../src/workspaces/render/sceneTypes.ts";

/** Penguin sample only: unverified metre-space masks, never a product default. */
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
