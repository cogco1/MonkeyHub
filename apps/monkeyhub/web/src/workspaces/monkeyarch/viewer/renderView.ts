import type { Scene } from "three";
import { ACESFilmicToneMapping, Matrix4, OrthographicCamera, PerspectiveCamera, SRGBColorSpace, Vector3, WebGLRenderer } from "three";
import type { ViewCamera } from "./cameraProjection";
import type { CameraState, Vec3 } from "./ThreeDmViewport";
import type { ModelSourceDto, RenderCameraDto } from "../../../api/project-runtime/generated";

/** A borrowed display scene, never a second model or persistent project state. */
export interface RenderView {
  readonly scene: Scene;
  readonly camera: ViewCamera;
  readonly target: readonly [number, number, number];
  readonly fov: number;
  readonly aspect: number;
  readonly exposure: number;
  /**
   * Whether the scene's lights cast shadows as Modeling draws it now: Presentation's
   * sun does (#562). A preview and a capture draw them too, so a still is the look on screen.
   */
  readonly shadows?: boolean;
  readonly modelSource?: ModelSourceDto | null;
  readonly sourceStageRef?: string | null;
  readonly sourceIssue?: "unsaved" | "loading" | "unbound" | null;
}

/** A captured view as its source document retains it: the camera, and the pixels it was drawn at. */
export interface SavedView {
  readonly camera: RenderCameraDto;
  readonly screenSize: [number, number];
}

const EPSILON = 1e-9;
/** The loaded CAD model is Z-up. */
const MODEL_UP = new Vector3(0, 0, 1);

async function drawPng(scene: Scene, camera: ViewCamera, screenSize: [number, number], exposure: number, shadows: boolean): Promise<Blob> {
  const renderer = new WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  try {
    renderer.setPixelRatio(1); renderer.setSize(...screenSize, false);
    renderer.outputColorSpace = SRGBColorSpace; renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = exposure;
    // The scene's own lights cast its shadows here as on screen; the map is this renderer's own.
    renderer.shadowMap.enabled = shadows;
    renderer.render(scene, camera);
    return await new Promise<Blob>((resolve, reject) => renderer.domElement.toBlob(
      blob => blob ? resolve(blob) : reject(new Error("Could not capture the modeling view.")), "image/png"));
  } finally { renderer.dispose(); renderer.forceContextLoss(); }
}

/** The camera a capture retains: its matrices and exposure, and the orbit it was turned in. */
export function renderCamera(view: RenderView): RenderCameraDto {
  const { up } = view.camera;
  return { projection: "isOrthographicCamera" in view.camera ? "orthographic" : "perspective",
    worldMatrix: view.camera.matrixWorld.toArray(), projectionMatrix: view.camera.projectionMatrix.toArray(), exposure: view.exposure,
    target: [...view.target], up: [up.x, up.y, up.z] };
}

/** Freeze pixels now; later model/view changes cannot alter the saved input. */
export async function renderViewImage(view: RenderView): Promise<{ png: Blob; screenSize: [number, number]; camera: RenderCameraDto }> {
  const screenSize: [number, number] = view.aspect >= 1 ? [2048, Math.max(1, Math.round(2048 / view.aspect))]
    : [Math.max(1, Math.round(2048 * view.aspect)), 2048];
  return { png: await drawPng(view.scene, view.camera, screenSize, view.exposure, view.shadows === true), screenSize, camera: renderCamera(view) };
}

/**
 * Draw the scene on screen now through a saved camera: its own matrices, pixel
 * size and exposure, so the new picture frames the current model exactly as
 * the saved one framed its model, in the look Modeling shows it in now.
 */
export async function renderSavedViewImage(view: RenderView, saved: SavedView): Promise<Blob> {
  return drawPng(view.scene, savedCamera(saved.camera), saved.screenSize, saved.camera.exposure, view.shadows === true);
}

/** A camera that draws through the saved matrices as they are; nothing recomputes them. */
export function savedCamera(saved: RenderCameraDto): ViewCamera {
  const camera = saved.projection === "orthographic" ? new OrthographicCamera() : new PerspectiveCamera();
  camera.matrixAutoUpdate = false;
  camera.matrix.fromArray(saved.worldMatrix);
  camera.matrix.decompose(camera.position, camera.quaternion, camera.scale);
  if (saved.up) camera.up.fromArray(saved.up);
  camera.updateMatrixWorld(true);
  camera.projectionMatrix.fromArray(saved.projectionMatrix);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  return camera;
}

/**
 * The saved camera of a captured Modeling view, or null when the recipe is not
 * one. The camera is the one retained, so capturing it again draws the same view.
 */
export function savedView(recipe: { readonly [key: string]: unknown } | null | undefined): SavedView | null {
  if (recipe?.kind !== "model-view") return null;
  const camera = recipe.camera, size = recipe.screenSize;
  const numbers = (value: unknown, length: number): value is number[] =>
    Array.isArray(value) && value.length === length && value.every((item) => typeof item === "number" && Number.isFinite(item));
  if (typeof camera !== "object" || camera === null || !numbers(size, 2) || !size.every((value) => Number.isInteger(value) && value > 0)) return null;
  const { projection, worldMatrix, projectionMatrix, exposure, target, up } = camera as Record<string, unknown>;
  if ((projection !== "perspective" && projection !== "orthographic") || !numbers(worldMatrix, 16) || !numbers(projectionMatrix, 16)
    || !(projectionMatrix[5] > 0) || typeof exposure !== "number" || !(exposure > 0)) return null;
  const orbit = numbers(target, 3) && numbers(up, 3) ? { target: target as Vec3, up: up as Vec3 } : {};
  return { camera: { projection, worldMatrix: worldMatrix as RenderCameraDto["worldMatrix"],
    projectionMatrix: projectionMatrix as RenderCameraDto["projectionMatrix"], exposure, ...orbit }, screenSize: [size[0], size[1]] };
}

/**
 * Where Modeling's orbit stands to show a saved view: the same eye, view line,
 * upright and lens, for ThreeDmViewport.applyCamera. The target is the saved
 * one, kept on the view line; a capture retained without one orbits about the
 * point of its view line nearest ``focus``. The up is the saved one when it
 * keeps the saved orientation, else the model's Z when that does, else the
 * view's own screen up. The orthographic zoom is the saved vertical scale,
 * because Modeling's frustum is one unit high each way (configureOrthographicAspect);
 * ``lens`` keeps Modeling's perspective lens while an orthographic view is shown.
 */
export function savedViewCamera(saved: RenderCameraDto, focus: Vec3, lens: number): CameraState {
  const world = new Matrix4().fromArray(saved.worldMatrix);
  const position = new Vector3().setFromMatrixPosition(world);
  const right = new Vector3(), screenUp = new Vector3(), back = new Vector3();
  world.extractBasis(right, screenUp, back);
  right.normalize(); screenUp.normalize(); back.normalize();
  const forward = back.clone().negate();
  const along = (point: readonly number[]) => new Vector3().fromArray(point).sub(position).dot(forward);
  let distance = saved.target ? along(saved.target) : Number.NaN;
  if (!(distance > EPSILON)) distance = along(focus);
  if (!(distance > EPSILON)) distance = Math.max(position.distanceTo(new Vector3().fromArray(focus)), 1);
  const target = position.clone().addScaledVector(forward, distance);
  // lookAt turns a camera so its right is up × back: an up that gives the saved right keeps the picture.
  const keeps = (up: Vector3) => {
    const turned = up.clone().cross(back);
    return turned.lengthSq() > EPSILON && turned.normalize().dot(right) > 1 - 1e-6;
  };
  const up = [saved.up ? new Vector3().fromArray(saved.up) : null, MODEL_UP].find((candidate) => candidate && keeps(candidate)) ?? screenUp;
  const scale = saved.projectionMatrix[5];
  const perspective = saved.projection === "perspective";
  return {
    position: [position.x, position.y, position.z],
    target: [target.x, target.y, target.z],
    up: [up.x, up.y, up.z],
    fov: perspective ? 2 * Math.atan(1 / scale) * 180 / Math.PI : lens,
    projection: saved.projection,
    zoom: perspective ? 1 : scale,
  };
}

export function captureRenderView(scene: Scene, camera: ViewCamera, target: Vector3, fov: number, exposure: number, shadows = false): RenderView {
  camera.updateMatrixWorld(true);
  const copy = camera.clone() as ViewCamera;
  const aspect = "aspect" in copy ? copy.aspect : (copy.right - copy.left) / (copy.top - copy.bottom);
  return { scene, camera: copy, target: [target.x, target.y, target.z], fov, aspect, exposure, shadows };
}

/** Preserve the source projection by fitting the canvas, not changing its lens. */
export function previewSize(width: number, height: number, aspect: number): [number, number] {
  const w = Math.max(1, Math.min(width, height * aspect));
  return [w, w / aspect];
}
