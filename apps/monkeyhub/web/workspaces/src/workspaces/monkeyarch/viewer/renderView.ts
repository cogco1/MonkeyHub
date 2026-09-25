import type { Scene, Vector3 } from "three";
import { ACESFilmicToneMapping, SRGBColorSpace, WebGLRenderer } from "three";
import type { ViewCamera } from "./cameraProjection";
import type { ModelSourceDto, RenderCameraDto } from "../../../api/generated";

/** A borrowed display scene, never a second model or persistent project state. */
export interface RenderView {
  readonly scene: Scene;
  readonly camera: ViewCamera;
  readonly target: readonly number[];
  readonly fov: number;
  readonly aspect: number;
  readonly exposure: number;
  readonly modelSource?: ModelSourceDto | null;
  readonly sourceStageRef?: string | null;
  readonly sourceIssue?: "unsaved" | "loading" | "unbound" | null;
}

/** Freeze pixels now; later model/view changes cannot alter the saved input. */
export async function renderViewImage(view: RenderView): Promise<{ png: Blob; screenSize: [number, number]; camera: RenderCameraDto }> {
  const screenSize: [number, number] = view.aspect >= 1 ? [2048, Math.max(1, Math.round(2048 / view.aspect))]
    : [Math.max(1, Math.round(2048 * view.aspect)), 2048];
  const renderer = new WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  try {
    renderer.setPixelRatio(1); renderer.setSize(...screenSize, false);
    renderer.outputColorSpace = SRGBColorSpace; renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = view.exposure;
    renderer.render(view.scene, view.camera);
    const camera: RenderCameraDto = { projection: "isOrthographicCamera" in view.camera ? "orthographic" : "perspective",
      worldMatrix: view.camera.matrixWorld.toArray(), projectionMatrix: view.camera.projectionMatrix.toArray(), exposure: view.exposure };
    const png = await new Promise<Blob>((resolve, reject) => renderer.domElement.toBlob(
      blob => blob ? resolve(blob) : reject(new Error("Could not capture the modeling view.")), "image/png"));
    return { png, screenSize, camera };
  } finally { renderer.dispose(); renderer.forceContextLoss(); }
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
