import { OrthographicCamera, type Vector3 } from "three";
import type { ViewCamera } from "../monkeyarch/viewer/cameraProjection";
import type { ModelSourceDto } from "../../api/generated";

export interface RenderSelection {
  projectId: string;
  modelSource?: ModelSourceDto;
  localFile?: File;
  camera: RenderCamera;
}

/** CAD viewer world coordinates are Z-up and use the loaded model's units. */
export interface RenderCamera {
  position: number[];
  target: number[];
  up: number[];
  projection: "perspective" | "orthographic";
  near: number;
  far: number;
  aspect: number;
  verticalFov: number | null;
  orthographicBounds: [number, number, number, number] | null;
}

export function captureRenderCamera(camera: ViewCamera, target: Vector3, aspect: number): RenderCamera {
  if (!Number.isFinite(aspect) || aspect <= 0) throw new Error("Invalid render aspect ratio");
  const orthographic = camera instanceof OrthographicCamera;
  return {
    position: camera.position.toArray(), target: target.toArray(), up: camera.up.toArray(),
    projection: orthographic ? "orthographic" : "perspective",
    near: camera.near, far: camera.far, aspect,
    verticalFov: orthographic ? null : camera.getEffectiveFOV(),
    orthographicBounds: orthographic ? [
      (camera.left + camera.right) / 2 - (camera.right - camera.left) / (2 * camera.zoom),
      (camera.left + camera.right) / 2 + (camera.right - camera.left) / (2 * camera.zoom),
      (camera.top + camera.bottom) / 2 + (camera.top - camera.bottom) / (2 * camera.zoom),
      (camera.top + camera.bottom) / 2 - (camera.top - camera.bottom) / (2 * camera.zoom),
    ] : null,
  };
}
