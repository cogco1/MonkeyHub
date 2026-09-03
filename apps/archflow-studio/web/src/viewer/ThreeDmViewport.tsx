import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  ACESFilmicToneMapping,
  AmbientLight,
  Box3,
  Color,
  DirectionalLight,
  GridHelper,
  Object3D,
  PerspectiveCamera,
  Raycaster,
  Scene,
  SRGBColorSpace,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Rhino3dmLoader } from "three/examples/jsm/loaders/3DMLoader.js";

import {
  disposeScene,
  documentUserStrings,
  inspectScene,
  layerIndexOf,
  toUserStrings,
  userStringCarrier,
  type SceneInspection,
  type UserStrings,
} from "./sceneInspection";

export type ViewportStatus = "idle" | "loading" | "ready" | "error";

/** What a file opened from this machine, bound to nothing, is labelled. */
export const LOCAL_SOURCE_LABEL = "LOCAL · UNBOUND";

/**
 * One click, as read off the loaded file.
 *
 * Nothing here is interpreted: these are the strings the file carries and the
 * name the object was given. What they mean is the server's answer, and it is
 * `POST /api/pick/resolve` that gives it.
 */
export interface ViewportPick {
  userStrings: UserStrings;
  documentUserStrings: UserStrings | null;
  objectName: string | null;
}

export interface ViewportController {
  openFile(file: File, sourceLabel?: string): Promise<void>;
  fitView(): void;
  frontView(): void;
  setLayerVisibility(index: number, visible: boolean): void;
  clear(): void;
}

/** A pointer that moved further than this was an orbit, not a click. */
const CLICK_SLOP_PX = 5;

interface ThreeDmViewportProps {
  onInspection(inspection: SceneInspection | null): void;
  onStatus(status: ViewportStatus, message: string): void;
  onRequestFile(): void;
  /** Which file the viewport is showing, in the shell's own words. */
  onSource(sourceLabel: string | null): void;
  onPick(pick: ViewportPick): void;
}

interface ViewportRuntime {
  scene: Scene;
  camera: PerspectiveCamera;
  renderer: WebGLRenderer;
  controls: OrbitControls;
  model: Object3D | null;
  render: () => void;
}

const MAX_FILE_SIZE = 512 * 1024 * 1024;

interface ThemeColours {
  viewport: string;
  gridMajor: string;
  gridMinor: string;
}

/**
 * The canvas belongs to the theme: its background and grid are the same tokens
 * the shell paints with, read off the document rather than fixed here, so a
 * dark shell never frames a bright canvas. Colours only — nothing about what
 * is drawn changes with them.
 */
function themeColours(): ThemeColours {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    viewport: read("--viewport", "#202020"),
    gridMajor: read("--grid-major", "#3d3d3d"),
    gridMinor: read("--grid-minor", "#2e2e2e"),
  };
}

function gridMaterialsOf(grid: GridHelper) {
  return Array.isArray(grid.material) ? grid.material : [grid.material];
}

function buildGrid(colours: ThemeColours): GridHelper {
  const grid = new GridHelper(
    40,
    40,
    new Color(colours.gridMajor),
    new Color(colours.gridMinor),
  );
  grid.rotation.x = Math.PI / 2;
  grid.position.z = -0.002;
  gridMaterialsOf(grid).forEach((material) => {
    material.transparent = true;
    material.opacity = 0.62;
  });
  return grid;
}

function disposeGrid(grid: GridHelper): void {
  grid.geometry.dispose();
  gridMaterialsOf(grid).forEach((material) => material.dispose());
}

function fitRuntime(runtime: ViewportRuntime): void {
  if (!runtime.model) return;
  const box = new Box3().setFromObject(runtime.model);
  if (box.isEmpty()) return;
  const center = box.getCenter(new Vector3());
  const size = box.getSize(new Vector3());
  const maximumDimension = Math.max(size.x, size.y, size.z, 1);
  const fieldOfView = (runtime.camera.fov * Math.PI) / 180;
  const distance = (maximumDimension / (2 * Math.tan(fieldOfView / 2))) * 1.55;
  const direction = new Vector3(1, -1, 0.78).normalize();

  runtime.camera.position.copy(center).addScaledVector(direction, distance);
  runtime.camera.near = Math.max(distance / 1000, 0.01);
  runtime.camera.far = Math.max(distance * 100, 1000);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.target.copy(center);
  runtime.controls.update();
  runtime.render();
}

function frontRuntime(runtime: ViewportRuntime): void {
  if (!runtime.model) return;
  const box = new Box3().setFromObject(runtime.model);
  if (box.isEmpty()) return;
  const center = box.getCenter(new Vector3());
  const size = box.getSize(new Vector3());
  const direction = runtime.camera.position
    .clone()
    .sub(runtime.controls.target);
  direction.z = 0;
  if (direction.lengthSq() < 1e-8) direction.set(1, -1, 0);
  direction.normalize();
  const screenRight = new Vector3(-direction.y, direction.x, 0);
  const verticalFieldOfView = (runtime.camera.fov * Math.PI) / 180;
  const horizontalFieldOfView =
    2 * Math.atan(Math.tan(verticalFieldOfView / 2) * runtime.camera.aspect);
  const tangentVertical = Math.tan(verticalFieldOfView / 2);
  const tangentHorizontal = Math.tan(horizontalFieldOfView / 2);
  const halfSize = size.multiplyScalar(0.5);
  let distance = 1;

  for (const x of [-halfSize.x, halfSize.x]) {
    for (const y of [-halfSize.y, halfSize.y]) {
      for (const z of [-halfSize.z, halfSize.z]) {
        const offset = new Vector3(x, y, z);
        const depthTowardCamera = offset.dot(direction);
        distance = Math.max(
          distance,
          depthTowardCamera + Math.abs(offset.dot(screenRight)) / tangentHorizontal,
          depthTowardCamera + Math.abs(z) / tangentVertical,
        );
      }
    }
  }
  distance *= 1.1;

  runtime.camera.up.set(0, 0, 1);
  runtime.camera.position.copy(center).addScaledVector(direction, distance);
  runtime.camera.near = Math.max(distance / 1000, 0.01);
  runtime.camera.far = Math.max(distance * 100, 1000);
  runtime.camera.updateProjectionMatrix();
  runtime.controls.target.copy(center);
  runtime.controls.update();
  runtime.render();
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (
    typeof error === "object" &&
    error !== null &&
    "message" in error &&
    typeof error.message === "string"
  ) {
    return error.message;
  }
  return "This 3DM file could not be parsed.";
}

export const ThreeDmViewport = forwardRef<
  ViewportController,
  ThreeDmViewportProps
>(function ThreeDmViewport(
  { onInspection, onStatus, onRequestFile, onSource, onPick },
  forwardedRef,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<ViewportRuntime | null>(null);
  const loadGenerationRef = useRef(0);
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const callbacksRef = useRef({ onInspection, onStatus, onSource, onPick });
  const [dragActive, setDragActive] = useState(false);
  const [visualStatus, setVisualStatus] = useState<ViewportStatus>("idle");
  const [visualMessage, setVisualMessage] = useState("Drop a .3dm here, or open one from this machine");

  callbacksRef.current = { onInspection, onStatus, onSource, onPick };

  const reportStatus = useCallback((status: ViewportStatus, message: string) => {
    setVisualStatus(status);
    setVisualMessage(message);
    callbacksRef.current.onStatus(status, message);
  }, []);

  const clear = useCallback(() => {
    const runtime = runtimeRef.current;
    loadGenerationRef.current += 1;
    callbacksRef.current.onSource(null);
    if (!runtime?.model) {
      callbacksRef.current.onInspection(null);
      reportStatus("idle", "Drop a .3dm here, or open one from this machine");
      return;
    }
    runtime.scene.remove(runtime.model);
    disposeScene(runtime.model);
    runtime.model = null;
    runtime.render();
    callbacksRef.current.onInspection(null);
    reportStatus("idle", "Drop a .3dm here, or open one from this machine");
  }, [reportStatus]);

  const openFile = useCallback(
    async (file: File, sourceLabel: string = LOCAL_SOURCE_LABEL) => {
      const runtime = runtimeRef.current;
      if (!runtime) {
        throw new Error(
          "The 3D viewport is not ready yet; try again in a moment.",
        );
      }
      // A refused file leaves whatever was already loaded on screen, so the
      // fact line under the canvas has to go: it names the *previous* file, and
      // beside an error status it would read as that file having failed to
      // load. Nothing was loaded and nothing failed — a file was declined
      // before it was read, and the refusal below says which.
      if (!file.name.toLowerCase().endsWith(".3dm")) {
        callbacksRef.current.onInspection(null);
        reportStatus(
          "error",
          "Only Rhino .3dm files are supported. The file was not opened.",
        );
        return;
      }
      if (file.size <= 0 || file.size > MAX_FILE_SIZE) {
        callbacksRef.current.onInspection(null);
        reportStatus(
          "error",
          "The file is empty or over the 512 MB local parse limit.",
        );
        return;
      }

      const generation = loadGenerationRef.current + 1;
      loadGenerationRef.current = generation;
      reportStatus("loading", `Parsing ${file.name} locally`);
      const started = performance.now();
      let buffer: ArrayBuffer;
      try {
        buffer = await file.arrayBuffer();
      } catch (error) {
        // Bytes that could not be read are a file that never arrived, and the
        // fact line still names the one before it. Same reason as a refusal.
        callbacksRef.current.onInspection(null);
        reportStatus("error", errorMessage(error));
        return;
      }

      const loader = new Rhino3dmLoader();
      loader.setLibraryPath("/rhino3dm/");
      loader.setWorkerLimit(
        Math.max(1, Math.min(4, navigator.hardwareConcurrency || 2)),
      );

      await new Promise<void>((resolve) => {
        loader.parse(
          buffer,
          (model) => {
            loader.dispose();
            if (generation !== loadGenerationRef.current) {
              disposeScene(model);
              resolve();
              return;
            }
            if (runtime.model) {
              runtime.scene.remove(runtime.model);
              disposeScene(runtime.model);
            }
            runtime.model = model;
            runtime.scene.add(model);
            const inspection = inspectScene(
              model,
              file,
              Math.round(performance.now() - started),
            );
            callbacksRef.current.onInspection(inspection);
            callbacksRef.current.onSource(sourceLabel);
            fitRuntime(runtime);
            reportStatus(
              "ready",
              inspection.meshCount > 0
                ? `${file.name} · ${inspection.meshCount.toLocaleString()} meshes`
                : `${file.name} opened, but it holds no displayable mesh`,
            );
            resolve();
          },
          (error) => {
            loader.dispose();
            if (generation === loadGenerationRef.current) {
              callbacksRef.current.onInspection(null);
              callbacksRef.current.onSource(null);
              reportStatus("error", errorMessage(error));
            }
            resolve();
          },
        );
      });
    },
    [reportStatus],
  );

  /**
   * Read the object under the cursor and hand what it carries to the shell.
   *
   * The raycast is how a pixel becomes an object; it computes nothing about the
   * design. The walk upwards finds the object the loader hung Rhino's
   * attributes on, and the strings go out untouched — identity is resolved by
   * the server against the State Record, never here.
   */
  const pickAt = useCallback((clientX: number, clientY: number) => {
    const runtime = runtimeRef.current;
    if (!runtime?.model) return;
    const rect = runtime.renderer.domElement.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const raycaster = new Raycaster();
    raycaster.setFromCamera(
      new Vector2(
        ((clientX - rect.left) / rect.width) * 2 - 1,
        -((clientY - rect.top) / rect.height) * 2 + 1,
      ),
      runtime.camera,
    );
    const hit = raycaster
      .intersectObject(runtime.model, true)
      .find((intersection) => intersection.object.visible);
    if (!hit) return;
    const carrier = userStringCarrier(hit.object);
    const attributes = carrier?.userData.attributes as
      | { userStrings?: unknown }
      | undefined;
    callbacksRef.current.onPick({
      userStrings: toUserStrings(attributes?.userStrings),
      documentUserStrings: documentUserStrings(runtime.model),
      objectName: (carrier ?? hit.object).name || null,
    });
  }, []);

  useImperativeHandle(
    forwardedRef,
    () => ({
      openFile,
      fitView: () => {
        const runtime = runtimeRef.current;
        if (runtime) fitRuntime(runtime);
      },
      frontView: () => {
        const runtime = runtimeRef.current;
        if (runtime) frontRuntime(runtime);
      },
      setLayerVisibility: (index, visible) => {
        const runtime = runtimeRef.current;
        if (!runtime?.model) return;
        runtime.model.traverse((object) => {
          if (layerIndexOf(object) === index) object.visible = visible;
        });
        const layers = runtime.model.userData.layers;
        if (Array.isArray(layers) && layers[index]) layers[index].visible = visible;
        runtime.render();
      },
      clear,
    }),
    [clear, openFile],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;

    const scene = new Scene();
    const background = new Color(themeColours().viewport);
    scene.background = background;
    const camera = new PerspectiveCamera(38, 1, 0.01, 10000);
    camera.up.set(0, 0, 1);
    camera.position.set(8, -8, 6);

    const renderer = new WebGLRenderer({
      antialias: true,
      powerPreference: "high-performance",
    });
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.className = "viewport-canvas";
    renderer.domElement.setAttribute("aria-label", "3DM model viewport");
    host.prepend(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.screenSpacePanning = true;
    controls.target.set(0, 0, 0);

    const ambient = new AmbientLight(0xffffff, 1.45);
    const key = new DirectionalLight(0xffffff, 2.2);
    key.position.set(10, -8, 16);
    const fill = new DirectionalLight(0xd9e4e2, 1.1);
    fill.position.set(-10, 6, 8);
    scene.add(ambient, key, fill);

    let grid = buildGrid(themeColours());
    scene.add(grid);

    const render = () => renderer.render(scene, camera);

    // The tokens can change under a running canvas — a theme toggle, or the OS
    // switching at dusk — and the grid's colours are baked into its vertices,
    // so it is rebuilt rather than recoloured.
    const applyTheme = () => {
      const colours = themeColours();
      background.set(colours.viewport);
      scene.remove(grid);
      disposeGrid(grid);
      grid = buildGrid(colours);
      scene.add(grid);
      render();
    };
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", applyTheme);
    const themeObserver = new MutationObserver(applyTheme);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    const runtime: ViewportRuntime = {
      scene,
      camera,
      renderer,
      controls,
      model: null,
      render,
    };
    runtimeRef.current = runtime;
    controls.addEventListener("change", render);

    const resize = () => {
      const width = Math.max(host.clientWidth, 1);
      const height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      render();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    return () => {
      loadGenerationRef.current += 1;
      observer.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      if (runtime.model) disposeScene(runtime.model);
      media.removeEventListener("change", applyTheme);
      themeObserver.disconnect();
      disposeGrid(grid);
      renderer.dispose();
      renderer.domElement.remove();
      runtimeRef.current = null;
    };
  }, []);

  return (
    <div
      ref={hostRef}
      className={`viewport-host${dragActive ? " is-dragging" : ""}`}
      onDragEnter={(event) => {
        event.preventDefault();
        setDragActive(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragActive(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragActive(false);
        const file = event.dataTransfer.files.item(0);
        // Dropped from this machine: bound to nothing, and labelled so.
        if (file) void openFile(file, LOCAL_SOURCE_LABEL);
      }}
      onPointerDown={(event) => {
        pointerDownRef.current = { x: event.clientX, y: event.clientY };
      }}
      onPointerUp={(event) => {
        const down = pointerDownRef.current;
        pointerDownRef.current = null;
        if (!down) return;
        const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
        if (moved > CLICK_SLOP_PX) return;
        pickAt(event.clientX, event.clientY);
      }}
    >
      {visualStatus !== "ready" && (
        <div className={`viewport-state viewport-state--${visualStatus}`}>
          {visualStatus === "loading" && <span className="spinner" aria-hidden="true" />}
          <p aria-live="polite">{visualMessage}</p>
          {(visualStatus === "idle" || visualStatus === "error") && (
            <button className="button button--primary" type="button" onClick={onRequestFile}>
              Open .3dm
            </button>
          )}
        </div>
      )}
      {dragActive && <div className="drop-target">Release to open locally</div>}
    </div>
  );
});
