import { BufferAttribute, BufferGeometry, DoubleSide, Float32BufferAttribute, Group, LineBasicMaterial,
  LineSegments, Mesh, MeshBasicMaterial, Object3D, Points, PointsMaterial, Vector3 } from "three";
import { indexConnectedFaces, featureEdges, type FeatureEdge } from "./featureEdges";
import type { LoadedObjectIdentity } from "./sceneInspection";
import type { ModelSnap } from "./ThreeDmViewport";
import { isDisplayed } from "./modelDisplay";

/** A hit in the current display, never a semantic resolution or edit grant. */
export interface LocalHit extends LoadedObjectIdentity {
  readonly mesh: Object3D;
  readonly faceIndex: number | null;
  readonly point: Vector3;
  readonly normal: Vector3 | null;
}

function positions(geometry: BufferGeometry, values: number[]): void {
  let attribute = geometry.getAttribute("position") as BufferAttribute | undefined;
  if (!attribute || attribute.array.length < values.length) {
    if (attribute) geometry.dispose();
    attribute = new Float32BufferAttribute(Math.max(values.length, 3), 3);
    geometry.setAttribute("position", attribute);
  }
  (attribute.array as Float32Array).set(values);
  attribute.needsUpdate = true;
  geometry.setDrawRange(0, values.length / 3);
}

function edgesOf(mesh: Mesh): FeatureEdge[] {
  const geometry = mesh.geometry;
  return geometry.userData.archflowEdges ??= featureEdges({
    positions: geometry.getAttribute("position").array,
    index: geometry.index?.array ?? null,
  });
}

/** One neutral outline/face layer, separate from the selected material lift. */
export class Preselection {
  readonly group = new Group();
  private readonly outline = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ transparent: true, opacity: 0.8 }));
  private readonly face = new Mesh(new BufferGeometry(), new MeshBasicMaterial({
    transparent: true, opacity: 0.12, side: DoubleSide, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  }));
  private readonly edge = new LineSegments(new BufferGeometry(), new LineBasicMaterial({ depthTest: false }));
  private readonly point = new Points(new BufferGeometry(), new PointsMaterial({ size: 8, sizeAttenuation: false, depthTest: false }));
  private object: Object3D | null = null;
  private mesh: Object3D | null = null;
  private faceTriangles: readonly number[] | null = null;
  private readonly faces = new WeakMap<BufferGeometry, {
    lookup: ReturnType<typeof indexConnectedFaces>; cached: Map<number, number[]>;
  }>();

  constructor(model: Object3D, colour: string) {
    this.group.name = "archflow-preselection";
    this.group.add(this.outline, this.face, this.edge, this.point);
    for (const object of this.group.children) { object.frustumCulled = false; object.renderOrder = 4; }
    this.colour(colour);
    this.clear();
    // Prepare immutable mesh topology with the model, outside pointer frames.
    // Instances sharing a geometry reuse one index for this loaded picture.
    model.traverse((object) => {
      if (!(object instanceof Mesh) || this.faces.has(object.geometry)) return;
      const geometry = object.geometry;
      this.faces.set(geometry, { lookup: indexConnectedFaces({
        positions: geometry.getAttribute("position").array, index: geometry.index?.array ?? null,
      }), cached: new Map() });
    });
  }

  colour(value: string): void {
    for (const object of [this.outline, this.face, this.edge, this.point]) object.material.color.set(value);
  }

  clear(): boolean {
    const visible = this.group.visible;
    this.group.visible = false;
    this.object = this.mesh = null;
    this.faceTriangles = null;
    return visible;
  }

  update(hit: LocalHit, snap: ModelSnap | null): void {
    if (this.object !== hit.object) {
      const outline: number[] = [];
      hit.object.traverse((object) => {
        if (!(object instanceof Mesh) || !isDisplayed(object)) return;
        object.updateWorldMatrix(true, false);
        for (const edge of edgesOf(object)) for (const point of [edge.a, edge.b]) {
          outline.push(...new Vector3(...point).applyMatrix4(object.matrixWorld).toArray());
        }
      });
      positions(this.outline.geometry, outline);
      this.object = hit.object;
    }
    let triangles: number[] | null = null;
    if (hit.mesh instanceof Mesh && hit.faceIndex !== null) {
      const geometry = hit.mesh.geometry;
      const { lookup, cached } = this.faces.get(geometry)!;
      triangles = cached.get(hit.faceIndex) ?? lookup(hit.faceIndex);
      for (const index of triangles) cached.set(index, triangles);
      if (this.mesh !== hit.mesh || this.faceTriangles !== triangles) {
        const values: number[] = [];
        hit.mesh.updateWorldMatrix(true, false);
        const attribute = geometry.getAttribute("position");
        for (const triangle of triangles) for (let corner = 0; corner < 3; corner++) {
          const index = geometry.index?.getX(triangle * 3 + corner) ?? triangle * 3 + corner;
          values.push(...new Vector3().fromBufferAttribute(attribute, index).applyMatrix4(hit.mesh.matrixWorld).toArray());
        }
        positions(this.face.geometry, values);
      }
    }
    this.mesh = hit.mesh; this.faceTriangles = triangles;
    this.face.visible = triangles !== null && triangles.length > 0;
    this.edge.visible = !!snap?.edge;
    this.point.visible = !!snap && snap.kind !== "surface";
    if (snap?.edge) positions(this.edge.geometry, [...snap.edge.a, ...snap.edge.b]);
    if (this.point.visible) positions(this.point.geometry, [...snap!.point]);
    this.group.visible = true;
  }

  dispose(): void {
    this.group.removeFromParent();
    for (const object of [this.outline, this.face, this.edge, this.point]) {
      object.geometry.dispose(); object.material.dispose();
    }
    this.clear();
  }
}
