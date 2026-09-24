import type { Scene, Vector3 } from "three";
import type { ViewCamera } from "./cameraProjection";

/** A borrowed display scene, never a second model or persistent project state. */
export interface RenderView {
  readonly scene: Scene;
  readonly camera: ViewCamera;
  readonly target: readonly number[];
  readonly fov: number;
  readonly aspect: number;
  readonly exposure: number;
}

export function captureRenderView(scene: Scene, camera: ViewCamera, target: Vector3, fov: number, exposure: number): RenderView {
  camera.updateMatrixWorld(true);
  const copy = camera.clone() as ViewCamera;
  const aspect = "aspect" in copy ? copy.aspect : (copy.right - copy.left) / (copy.top - copy.bottom);
  return { scene, camera: copy, target: target.toArray(), fov, aspect, exposure };
}

/** Preserve the source projection by fitting the canvas, not changing its lens. */
export function previewSize(width: number, height: number, aspect: number): [number, number] {
  const w = Math.max(1, Math.min(width, height * aspect));
  return [w, w / aspect];
}
