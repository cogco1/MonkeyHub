import {
  AlwaysStencilFunc, BackSide, BufferGeometry, Color, DecrementWrapStencilOp, DoubleSide, FrontSide, Group,
  IncrementWrapStencilOp, Mesh, MeshBasicMaterial, NotEqualStencilFunc, PlaneGeometry, Quaternion, ReplaceStencilOp,
  Vector3, type Material, type Object3D, type Plane,
} from "three";

/**
 * The fill a live section shows where it passes through a closed member (G5, step 2).
 *
 * For each closed mesh, in turn: its kept back faces add one to the stencil and
 * its kept front faces take one away, without drawing; then one quad on the
 * section plane is drawn where the stencil is not zero, in the member's own
 * colour darkened, and puts the stencil back to zero for the next member. An
 * open surface is cut and never filled, so a sheet does not look solid.
 */

/** Drawn after every ordinary object: three steps per member, from here on. */
const CAP_ORDER = 1000;
/** Beyond this many closed members a cut shows no fills rather than slowing every frame. */
export const MAX_CAPPED_MEMBERS = 4000;
const CAP_DARKEN = 0.6;
const closedGeometries = new WeakMap<BufferGeometry, boolean>();

/** Whether an object and every parent above it are visible, as modelDisplay's isDisplayed reads it. */
function shownInScene(object: Object3D): boolean {
  for (let current: Object3D | null = object; current !== null; current = current.parent) {
    if (!current.visible) return false;
  }
  return true;
}

/** Whether a mesh's triangles close up: every edge, its corners welded by position, borders exactly two triangles. */
export function closedGeometry(geometry: BufferGeometry): boolean {
  const known = closedGeometries.get(geometry);
  if (known !== undefined) return known;
  const position = geometry.getAttribute("position");
  let closed = false;
  if (position && position.count >= 3) {
    geometry.computeBoundingBox();
    const box = geometry.boundingBox!;
    const step = Math.max(box.max.x - box.min.x, box.max.y - box.min.y, box.max.z - box.min.z, 1e-9) * 1e-6;
    const welded = new Map<string, number>();
    const vertex = (at: number) => {
      const key = `${Math.round(position.getX(at) / step)},${Math.round(position.getY(at) / step)},${Math.round(position.getZ(at) / step)}`;
      let id = welded.get(key);
      if (id === undefined) { id = welded.size; welded.set(key, id); }
      return id;
    };
    const index = geometry.getIndex();
    const count = index ? index.count : position.count;
    const corner = (n: number) => vertex(index ? index.getX(n) : n);
    const edges = new Map<number, number>();
    let triangles = 0;
    for (let n = 0; n + 2 < count; n += 3) {
      const a = corner(n), b = corner(n + 1), c = corner(n + 2);
      if (a === b || b === c || a === c) continue;
      triangles += 1;
      for (const [p, q] of [[a, b], [b, c], [c, a]]) {
        const key = Math.min(p, q) * 0x200000 + Math.max(p, q);
        edges.set(key, (edges.get(key) ?? 0) + 1);
      }
    }
    closed = triangles > 0 && [...edges.values()].every((uses) => uses === 2);
  }
  closedGeometries.set(geometry, closed);
  return closed;
}

/** The fill colour of a cut member: its material's own colour, darkened. */
export function capColour(material: Material | Material[]): Color {
  const own = Array.isArray(material) ? material[0] : material;
  const colour = own && "color" in own && (own as { color: unknown }).color instanceof Color
    ? (own as { color: Color }).color : new Color(0x9a948a);
  return colour.clone().multiplyScalar(CAP_DARKEN);
}

export interface SectionCaps {
  readonly group: Group;
  /** Each capped member, the two passes over its faces and the fill laid on the plane. */
  readonly members: readonly { readonly source: Mesh; readonly passes: readonly Mesh[]; readonly fill: Mesh }[];
  readonly materials: readonly Material[];
  readonly quad: PlaneGeometry;
}

/** The passes and fills for every displayed closed mesh of ``model``; null when none is closed or too many are. */
export function buildSectionCaps(model: Object3D, planes: Plane[], extent: number): SectionCaps | null {
  const meshes: Mesh[] = [];
  model.traverse((object) => {
    if (object instanceof Mesh && object.geometry instanceof BufferGeometry && shownInScene(object)
        && closedGeometry(object.geometry)) meshes.push(object);
  });
  if (!meshes.length || meshes.length > MAX_CAPPED_MEMBERS) return null;
  model.updateMatrixWorld(true);
  const group = new Group();
  group.name = "archflow-section-caps";
  const quad = new PlaneGeometry(extent, extent);
  const materials: Material[] = [];
  const counting = (side: typeof BackSide | typeof FrontSide, op: typeof IncrementWrapStencilOp | typeof DecrementWrapStencilOp) =>
    new MeshBasicMaterial({ side, clippingPlanes: planes, colorWrite: false, depthWrite: false, depthTest: false,
      stencilWrite: true, stencilFunc: AlwaysStencilFunc, stencilFail: op, stencilZFail: op, stencilZPass: op });
  const members = meshes.map((source, index) => {
    const back = counting(BackSide, IncrementWrapStencilOp);
    const front = counting(FrontSide, DecrementWrapStencilOp);
    const paint = new MeshBasicMaterial({ color: capColour(source.material), side: DoubleSide, stencilWrite: true,
      stencilRef: 0, stencilFunc: NotEqualStencilFunc, stencilFail: ReplaceStencilOp, stencilZFail: ReplaceStencilOp,
      stencilZPass: ReplaceStencilOp });
    materials.push(back, front, paint);
    const passes = [back, front].map((material, order) => {
      const pass = new Mesh(source.geometry, material);
      pass.matrixAutoUpdate = false;
      pass.matrix.copy(source.matrixWorld);
      pass.matrixWorldNeedsUpdate = true;
      pass.renderOrder = CAP_ORDER + index * 3 + order;
      return pass;
    });
    const fill = new Mesh(quad, paint);
    fill.renderOrder = CAP_ORDER + index * 3 + 2;
    group.add(...passes, fill);
    return { source, passes, fill };
  });
  return { group, members, materials, quad };
}

/** Lay every fill on the section plane, centred near the model, and follow each member's own visibility. */
export function syncSectionCaps(caps: SectionCaps, plane: Plane, centre: Vector3): void {
  const point = plane.projectPoint(centre, new Vector3());
  const turn = new Quaternion().setFromUnitVectors(new Vector3(0, 0, 1), plane.normal);
  for (const member of caps.members) {
    const shown = shownInScene(member.source);
    for (const pass of member.passes) pass.visible = shown;
    member.fill.visible = shown;
    member.fill.position.copy(point);
    member.fill.quaternion.copy(turn);
  }
}

/** Take the fills off the scene; the members' own geometry is the model's and stays. */
export function disposeSectionCaps(caps: SectionCaps): void {
  caps.group.removeFromParent();
  for (const material of caps.materials) material.dispose();
  caps.quad.dispose();
}
