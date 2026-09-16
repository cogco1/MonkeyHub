/** A disposable, camera-bound pointer constraint; never a model or a history. */
import { OrthographicCamera, PerspectiveCamera, Raycaster, Vector2, Vector3 } from "three";

type Vec3 = readonly [number, number, number];
type Point = readonly [number, number];
export type ViewRect = { left: number; top: number; width: number; height: number };
export interface NormalDrag {
  readonly normal: [number, number, number];
  readonly numericOnly: boolean;
  readonly guide: { origin: Point; positive: Point; negative: Point };
  distance(clientX: number, clientY: number): number | null;
  project(distance: number): Point | null;
  matches(camera: PerspectiveCamera | OrthographicCamera, rect: ViewRect): boolean;
}
export type NormalDragController = Omit<NormalDrag, "matches"> & { isCurrent(): boolean };

/** Signed closest point on an axis. End-on rays have no usable pointer distance. */
export function signedAxisDistance(origin: Vec3, normal: Vec3, rayOrigin: Vec3, rayDirection: Vec3,
  minimumSineSquared = 0.0025): number | null {
  if (![...origin, ...normal, ...rayOrigin, ...rayDirection].every(Number.isFinite)) return null;
  const nLength = Math.hypot(...normal), rLength = Math.hypot(...rayDirection);
  if (nLength < 1e-9 || rLength < 1e-9) return null;
  const n = normal.map(v => v / nLength), r = rayDirection.map(v => v / rLength);
  const offset = origin.map((v, i) => v - rayOrigin[i]!);
  const dot = (a: readonly number[], b: readonly number[]) => a.reduce((sum, v, i) => sum + v * b[i]!, 0);
  const b = dot(n, r), denominator = 1 - b * b;
  if (denominator <= minimumSineSquared) return null;
  const distance = (b * dot(r, offset) - dot(n, offset)) / denominator;
  const alongRay = dot(offset, r) + distance * b;
  return Number.isFinite(distance) && alongRay >= 0 ? distance : null;
}

/** Freeze camera, viewport and the picked normal once, at gesture start. */
export function captureNormalDrag(live: PerspectiveCamera | OrthographicCamera, box: ViewRect,
  pickedOrigin: Vec3, pickedNormal: Vec3): NormalDrag {
  if (![...pickedOrigin, ...pickedNormal, box.left, box.top, box.width, box.height].every(Number.isFinite)
      || box.width <= 0 || box.height <= 0 || Math.hypot(...pickedNormal) < 1e-9) {
    throw new Error("A finite face normal and a visible viewport are required.");
  }
  const rect = { ...box }, camera = live.clone();
  camera.updateMatrixWorld(true);
  const world = camera.matrixWorld.toArray(), projection = camera.projectionMatrix.toArray();
  const origin = new Vector3(...pickedOrigin), normal = new Vector3(...pickedNormal).normalize();
  const raycaster = new Raycaster();
  const clip = (point: Vector3): Point | null => {
    if (point.clone().applyMatrix4(camera.matrixWorldInverse).z >= -camera.near) return null;
    const p = point.clone().project(camera);
    return [p.x, p.y, p.z].every(Number.isFinite)
      ? [(p.x + 1) * rect.width / 2, (1 - p.y) * rect.height / 2] : null;
  };
  const start = clip(origin);
  if (!start) throw new Error("The picked face is not in front of the camera.");
  const depth = -origin.clone().applyMatrix4(camera.matrixWorldInverse).z;
  const unitsPerPixel = camera instanceof OrthographicCamera
    ? (camera.top - camera.bottom) / camera.zoom / rect.height
    : 2 * depth * Math.tan(camera.fov * Math.PI / 360) / camera.zoom / rect.height;
  const rayThroughFace = camera instanceof OrthographicCamera
    ? camera.getWorldDirection(new Vector3()) : origin.clone().sub(camera.position).normalize();
  const numericOnly = 1 - normal.dot(rayThroughFace) ** 2 <= 0.0025;
  const nearPoint = clip(origin.clone().addScaledVector(normal, unitsPerPixel));
  const dx = (nearPoint?.[0] ?? start[0]) - start[0], dy = (nearPoint?.[1] ?? start[1]) - start[1];
  const length = Math.hypot(dx, dy);
  const screenDirection: Point = length > 1e-9 ? [dx / length, dy / length] : [0, 0];
  const frozenOrigin = origin.toArray() as [number, number, number], frozenNormal = normal.toArray() as [number, number, number];
  return {
    normal: [...frozenNormal], numericOnly,
    guide: { origin: start,
      positive: [start[0] + screenDirection[0] * 64, start[1] + screenDirection[1] * 64],
      negative: [start[0] - screenDirection[0] * 48, start[1] - screenDirection[1] * 48] },
    distance(clientX, clientY) {
      if (numericOnly || !Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
      raycaster.setFromCamera(new Vector2((clientX - rect.left) / rect.width * 2 - 1,
        1 - (clientY - rect.top) / rect.height * 2), camera);
      return signedAxisDistance(frozenOrigin, frozenNormal,
        raycaster.ray.origin.toArray() as [number, number, number], raycaster.ray.direction.toArray() as [number, number, number]);
    },
    project(distance) { return Number.isFinite(distance) ? clip(origin.clone().addScaledVector(normal, distance)) : null; },
    matches(next, nextRect) {
      return ["left", "top", "width", "height"].every(key => rect[key as keyof ViewRect] === nextRect[key as keyof ViewRect])
        && world.every((v, i) => Math.abs(v - next.matrixWorld.elements[i]!) <= 1e-9)
        && projection.every((v, i) => Math.abs(v - next.projectionMatrix.elements[i]!) <= 1e-9);
    },
  };
}
