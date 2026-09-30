/** The viewport's prepared feature edges, shared by every pointer tool. */
import { Box3, Line, Mesh, Object3D, Raycaster, Sphere, Vector2, Vector3, Vector4, type Camera } from "three";
import { candidatesOf, constrainSnapPoint, nearestCandidate, retainSnap, type FeatureEdge, type Point3, type SnapConstraint } from "./featureEdges";
import { isDisplayed } from "./modelDisplay";
import { outlineEdges } from "./preselection";
import type { ModelSnap } from "./ThreeDmViewport";

type Rect = { width: number; height: number; left: number; top: number };
type Feature = { object: Mesh | Line; root: Object3D; objectName: string | null; edges: FeatureEdge[]; bounds: Sphere };
export type SceneSnapTarget = { feature: Object3D; snap: ModelSnap };
export type SnapOptions = { constraint?: SnapConstraint; excludeObjectName?: string; excludeObjects?: readonly Object3D[] };

export class SceneSnapIndex {
  private features: Feature[] = [];

  /** Called on load/completed geometry edits, never in a pointer frame. */
  rebuild(roots: readonly Object3D[], identity: (object: Object3D) => { objectName: string | null } | null): void {
    this.features = [];
    for (const root of roots) {
      root.updateWorldMatrix(true, true);
      root.traverse(object => {
        if (!(object instanceof Mesh || object instanceof Line) || !object.geometry.hasAttribute("position")) return;
        const named = identity(object);
        if (!named) return;
        const edges = outlineEdges(object).map(edge => ({
          a: new Vector3(...edge.a).applyMatrix4(object.matrixWorld).toArray() as Point3,
          b: new Vector3(...edge.b).applyMatrix4(object.matrixWorld).toArray() as Point3,
        }));
        const bounds = new Box3().setFromBufferAttribute(object.geometry.getAttribute("position"))
          .applyMatrix4(object.matrixWorld).getBoundingSphere(new Sphere());
        this.features.push({ object, root, objectName: named.objectName, edges, bounds });
      });
    }
  }

  query(camera: Camera, rect: Rect, pointer: readonly [number, number], radius: number,
    previous: SceneSnapTarget | null, options: SnapOptions = {}): SceneSnapTarget | null {
    if (rect.width <= 0 || rect.height <= 0) return null;
    const project = (point: Point3): readonly [number, number] | null => {
      const p = new Vector3(...point).project(camera);
      return [p.x, p.y, p.z].every(Number.isFinite) && p.z >= -1 && p.z <= 1
        ? [(p.x + 1) * rect.width / 2 + rect.left, (1 - p.y) * rect.height / 2 + rect.top] : null;
    };
    const distance = (point: Point3) => {
      const p = project(point);
      return p ? Math.hypot(p[0] - pointer[0], p[1] - pointer[1]) : Infinity;
    };
    const ray = new Raycaster();
    const setRay = (screen: readonly [number, number]) => {
      const ndc = new Vector2((screen[0] - rect.left) / rect.width * 2 - 1, 1 - (screen[1] - rect.top) / rect.height * 2);
      ray.setFromCamera(ndc, camera);
      const depth = (z: number) => new Vector3(ndc.x, ndc.y, z).unproject(camera).sub(ray.ray.origin).dot(ray.ray.direction);
      ray.near = Math.max(0, depth(-1)); ray.far = depth(1);
    };
    setRay(pointer);
    const pointerRay = ray.ray.clone();
    const visible = this.features.filter(f => {
      if (!isDisplayed(f.object)) return false;
      for (let node: Object3D | null = f.object; node; node = node.parent) if (node === f.root) return true;
      return false;
    });
    const occluders = visible.filter(f => f.object instanceof Mesh);
    const allowed = (f: Feature) => {
      if (options.excludeObjectName !== undefined && f.objectName === options.excludeObjectName) return false;
      for (let node: Object3D | null = f.object; node; node = node.parent) if (options.excludeObjects?.includes(node)) return false;
      return true;
    };
    const along = (edge: FeatureEdge): Point3 | null => {
      const clip = (p: Point3) => new Vector4(...p, 1).applyMatrix4(camera.matrixWorldInverse).applyMatrix4(camera.projectionMatrix);
      const a = clip(edge.a), b = clip(edge.b);
      let low = 0, high = 1;
      // Clip in homogeneous coordinates before dividing by w. This keeps an
      // edge crossing the near plane usable without inventing a vertex there.
      for (const [da, db] of [[a.z + a.w, b.z + b.w], [a.w - a.z, b.w - b.z]]) {
        if (da < 0 && db < 0) return null;
        if (da < 0) low = Math.max(low, da / (da - db));
        if (db < 0) high = Math.min(high, da / (da - db));
      }
      if (low > high) return null;
      const start = a.clone().lerp(b, low), end = a.clone().lerp(b, high);
      if (start.w <= 0 || end.w <= 0) return null;
      const x = (start.x / start.w + 1) * rect.width / 2 + rect.left;
      const y = (1 - start.y / start.w) * rect.height / 2 + rect.top;
      const dx = (end.x / end.w - start.x / start.w) * rect.width / 2;
      const dy = (start.y / start.w - end.y / end.w) * rect.height / 2;
      const span = dx * dx + dy * dy;
      const s = span < 1e-12 ? 0 : Math.max(0, Math.min(1, ((pointer[0] - x) * dx + (pointer[1] - y) * dy) / span));
      // Screen interpolation is not world interpolation in perspective.
      const t = s * start.w / ((1 - s) * end.w + s * start.w);
      return new Vector3(...edge.a).lerp(new Vector3(...edge.b), low + (high - low) * t).toArray() as Point3;
    };
    const exposed = (point: Point3): boolean => {
      const screen = project(point);
      if (!screen) return false;
      setRay(screen);
      const target = new Vector3(...point), depth = target.clone().sub(ray.ray.origin).dot(ray.ray.direction);
      const epsilon = Math.max(1e-6, Math.abs(depth) * 1e-7);
      ray.far = Math.min(ray.far, depth - epsilon);
      // Test at the candidate's pixel, not at the cursor: a nearer face under
      // the cursor need not occlude the neighbouring silhouette being offered.
      return !occluders.some(f => ray.ray.intersectsSphere(f.bounds) && ray.intersectObject(f.object, false).length > 0);
    };
    const constrain = (snap: ModelSnap): ModelSnap => {
      const sourcePoint = snap.sourcePoint ?? snap.point;
      const point = constrainSnapPoint(sourcePoint, options.constraint);
      const projected = Math.hypot(...point.map((v, i) => v - sourcePoint[i]!)) > 1e-7;
      return { ...snap, point: [...point], sourcePoint: [...sourcePoint], projected };
    };
    const candidates: SceneSnapTarget[] = [];
    // Project only nearby objects' prepared edges. A screen-space expansion
    // of each bound works with both cameras and their actual zoom matrices.
    for (const f of visible) {
      if (!allowed(f)) continue;
      const center = f.bounds.center.clone().project(camera);
      const offset = center.clone(); offset.x += radius * 3 / rect.width;
      const reach = offset.unproject(camera).distanceTo(f.bounds.center);
      if (Number.isFinite(reach) && !pointerRay.intersectsSphere(new Sphere(f.bounds.center, f.bounds.radius + reach))) continue;
      for (const edge of f.edges) {
        const edgePoint = along(edge);
        for (const candidate of [...candidatesOf(edge), ...(edgePoint ? [{ kind: "edge" as const, point: edgePoint }] : [])]) {
          if (distance(candidate.point) > radius) continue;
          const snap = constrain({ point: [...candidate.point], kind: candidate.kind, objectName: f.objectName, edge });
          if (distance(snap.point) <= radius) candidates.push({ feature: f.object, snap });
        }
      }
    }
    const choose = (targets: SceneSnapTarget[]) => {
      const best = nearestCandidate(targets.map(t => t.snap), project, pointer, radius, snap => exposed(snap.sourcePoint!));
      return targets.find(t => t.snap === best) ?? null;
    };
    const next = choose(candidates.filter(t => t.snap.kind !== "edge")) ?? choose(candidates.filter(t => t.snap.kind === "edge"));
    const priorFeature = previous && visible.find(f => f.object === previous.feature && allowed(f));
    let held: SceneSnapTarget | null = null;
    if (previous && priorFeature && previous.snap.edge && priorFeature.edges.includes(previous.snap.edge)) {
      const old = previous.snap;
      const edgePoint = old.kind === "edge" ? along(old.edge!) : null;
      const snap = constrain(old.kind === "edge" && old.edge
        ? { ...old, point: [...(edgePoint ?? old.point)], sourcePoint: undefined } : old);
      if ((old.kind !== "edge" || edgePoint) && distance(snap.point) <= radius * 1.5 && distance(snap.sourcePoint!) <= radius * 1.5 && exposed(snap.sourcePoint!)) {
        held = { feature: previous.feature, snap };
      }
    }
    const retained = retainSnap(held?.snap ?? null, next?.snap ?? null, project, pointer, radius);
    if (retained) return retained === held?.snap ? held : next;

    // A face is the native nearest surface hit. Curves offer their real edges
    // above; a triangulation diagonal never becomes an inference candidate.
    setRay(pointer);
    const hits = occluders.filter(f => pointerRay.intersectsSphere(f.bounds))
      .flatMap(f => ray.intersectObject(f.object, false).map(hit => ({ f, hit })))
      .sort((a, b) => a.hit.distance - b.hit.distance);
    const first = hits[0];
    if (!first || !allowed(first.f)) return null;
    const snap = constrain({ point: first.hit.point.toArray() as [number, number, number], kind: "surface", objectName: first.f.objectName });
    return distance(snap.point) <= radius ? { feature: first.f.object, snap } : null;
  }
}
