import {
  Box3,
  OrthographicCamera,
  PerspectiveCamera,
  Vector3,
} from "three";

export type ViewCamera = PerspectiveCamera | OrthographicCamera;
export type ProjectionMode = "perspective" | "orthographic";
export type StandardView = "top" | "front" | "right" | "iso" | "perspective";

const EPS = 1e-9;

export function projectionMode(camera: ViewCamera): ProjectionMode {
  return camera instanceof OrthographicCamera ? "orthographic" : "perspective";
}

/**
 * Keep one normalized orthographic frustum. OrbitControls then owns zoom,
 * exactly as upstream Three does: resize changes the horizontal extent,
 * while wheel zoom changes camera.zoom rather than camera distance.
 */
export function configureOrthographicAspect(camera: OrthographicCamera, aspect: number): void {
  if (!Number.isFinite(aspect) || aspect <= 0) throw new Error("Viewport aspect must be positive.");
  camera.left = -aspect;
  camera.right = aspect;
  camera.top = 1;
  camera.bottom = -1;
  camera.updateProjectionMatrix();
}

export function visibleHalfHeight(camera: ViewCamera, target: Vector3): number {
  if (camera instanceof OrthographicCamera) {
    return (camera.top - camera.bottom) / (2 * Math.max(camera.zoom, EPS));
  }
  const distance = Math.max(camera.position.distanceTo(target), EPS);
  return distance * Math.tan(camera.fov * Math.PI / 360);
}

/** Preserve target, orientation and apparent vertical scale when projection changes. */
export function transferProjectionPose(
  from: ViewCamera,
  to: ViewCamera,
  target: Vector3,
  aspect: number,
): void {
  if (from === to) return;
  const halfHeight = visibleHalfHeight(from, target);
  const direction = from.position.clone().sub(target);
  if (direction.lengthSq() < EPS) direction.set(1, -1, 0.78);
  direction.normalize();

  to.up.copy(from.up);
  to.quaternion.copy(from.quaternion);
  to.near = from.near;
  to.far = from.far;

  if (to instanceof OrthographicCamera) {
    configureOrthographicAspect(to, aspect);
    to.position.copy(from.position);
    to.zoom = 1 / Math.max(halfHeight, EPS);
    to.updateProjectionMatrix();
  } else {
    const distance = halfHeight / Math.max(Math.tan(to.fov * Math.PI / 360), EPS);
    to.position.copy(target).addScaledVector(direction, distance);
    to.updateProjectionMatrix();
  }
  to.updateMatrixWorld();
}

export function standardViewFrame(view: Exclude<StandardView, "iso" | "perspective">): {
  direction: Vector3;
  up: Vector3;
} {
  if (view === "top") return { direction: new Vector3(0, 0, 1), up: new Vector3(0, 1, 0) };
  if (view === "front") return { direction: new Vector3(0, -1, 0), up: new Vector3(0, 0, 1) };
  return { direction: new Vector3(1, 0, 0), up: new Vector3(0, 0, 1) };
}

/** Fit a box in screen X/Y for a known view direction; depth only controls clipping. */
export function fitOrthographicBox(
  camera: OrthographicCamera,
  box: Box3,
  aspect: number,
  direction: Vector3,
  up: Vector3,
  margin = 1.1,
): Vector3 {
  if (box.isEmpty()) throw new Error("Cannot fit an empty box.");
  configureOrthographicAspect(camera, aspect);
  const center = box.getCenter(new Vector3());
  const viewOut = direction.clone();
  if (viewOut.lengthSq() < EPS) viewOut.set(1, -1, 0.78);
  viewOut.normalize();
  const viewIn = viewOut.clone().negate();
  let screenUp = up.clone();
  if (screenUp.lengthSq() < EPS || Math.abs(screenUp.normalize().dot(viewIn)) > 1 - 1e-6) {
    screenUp = Math.abs(viewIn.z) < 0.9 ? new Vector3(0, 0, 1) : new Vector3(0, 1, 0);
  }
  let right = viewIn.clone().cross(screenUp);
  if (right.lengthSq() < EPS) right = new Vector3(1, 0, 0);
  right.normalize();
  screenUp = right.clone().cross(viewIn).normalize();

  const corners: Vector3[] = [];
  for (const x of [box.min.x, box.max.x]) for (const y of [box.min.y, box.max.y]) for (const z of [box.min.z, box.max.z]) {
    corners.push(new Vector3(x, y, z).sub(center));
  }
  const halfWidth = Math.max(EPS, ...corners.map(point => Math.abs(point.dot(right))));
  const halfHeight = Math.max(EPS, ...corners.map(point => Math.abs(point.dot(screenUp))));
  const halfDepth = Math.max(EPS, ...corners.map(point => Math.abs(point.dot(viewOut))));
  const visibleHalfHeight = Math.max(halfHeight, halfWidth / aspect) * margin;
  camera.zoom = 1 / Math.max(visibleHalfHeight, EPS);
  const distance = Math.max(box.getSize(new Vector3()).length(), 1) + halfDepth;
  camera.position.copy(center).addScaledVector(viewOut, distance);
  camera.up.copy(screenUp);
  camera.near = 0.01;
  camera.far = Math.max(1000, distance + halfDepth * 4 + 10);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return center;
}

/**
 * Frame caller-supplied bounds while preserving the current view direction.
 * The caller decides what is selected; this projection owner only knows Box3.
 */
export function frameBoxKeepingView(
  camera: ViewCamera,
  target: Vector3,
  box: Box3,
  aspect: number,
): Vector3 {
  if (box.isEmpty()) throw new Error("Cannot frame empty bounds.");
  if (!Number.isFinite(aspect) || aspect <= 0) throw new Error("Viewport aspect must be positive.");
  const direction = camera.position.clone().sub(target);
  if (direction.lengthSq() < EPS) direction.copy(camera.getWorldDirection(new Vector3())).negate();
  if (direction.lengthSq() < EPS) direction.set(1, -1, 0.78);
  direction.normalize();

  if (camera instanceof OrthographicCamera) {
    return fitOrthographicBox(camera, box, aspect, direction, camera.up);
  }

  const center = box.getCenter(new Vector3());
  const radius = box.getSize(new Vector3()).length() / 2;
  const vertical = camera.fov * Math.PI / 180;
  const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * aspect);
  const distance = 1.15 * Math.max(
    radius / Math.max(Math.tan(vertical / 2), EPS),
    radius / Math.max(Math.tan(horizontal / 2), EPS),
    1,
  );
  camera.position.copy(center).addScaledVector(direction, distance);
  camera.near = Math.max(distance / 1000, 0.01);
  camera.far = Math.max(distance * 100, 1000);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return center;
}
