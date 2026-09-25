/**
 * The project canvas: MonkeyBoard's Excalidraw setup, shared.
 *
 * MonkeyBoard and the Design Tree mount the same Excalidraw 0.18.1 the same
 * way: Board's paper, ink and drafting defaults, the Hub's language and theme,
 * the Hub's own fonts (`canvasAssets`) and its token mapping (`canvas.css`).
 * Wheel zoom anchored at the pointer, and a capture-phase pointer listener
 * that names the scene point under a click, are hooks on the host element:
 * in view mode Excalidraw treats every press as a pan and never calls its
 * own pointer props, so a click has to be read from the DOM.
 */
import "./canvasAssets";
import { useEffect, useRef, useState, type RefObject } from "react";
import { CaptureUpdateAction, Excalidraw, FONT_FAMILY, viewportCoordsToSceneCoords } from "@excalidraw/excalidraw";
import type { AppState, ExcalidrawImperativeAPI, ExcalidrawProps, UIOptions } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";
import { usePreferences } from "../settings/preferences";
import type { ScenePoint } from "./sceneHit";
import "./canvas.css";

/** The class a canvas's host element carries for the shared theme mapping. */
export const PROJECT_CANVAS_CLASS = "project-canvas";

/** A new scene starts on Board's paper, in its ink, with sharp drafting strokes. */
export const CANVAS_APP_STATE = {
  viewBackgroundColor: "#f4f5f0", currentItemStrokeColor: "#29352d", currentItemBackgroundColor: "transparent",
  currentItemRoughness: 0, currentItemFontFamily: FONT_FAMILY.Helvetica, currentItemStrokeWidth: 1,
  currentItemRoundness: "sharp", gridSize: 20,
} as const;

/** The file menu a project canvas never offers: scenes are the project's, not files. */
export const CANVAS_UI_OPTIONS: UIOptions = {
  canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false },
  tools: { image: false },
};

/** The Hub's theme choice, with "system" resolved the way the browser reports it now. */
export function useCanvasTheme(): "light" | "dark" {
  const { theme } = usePreferences();
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const changed = () => setSystemDark(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  return theme === "system" ? (systemDark ? "dark" : "light") : theme;
}

/** Excalidraw with the project defaults; every prop a caller gives wins. */
export function ProjectCanvas(props: ExcalidrawProps) {
  const { language } = usePreferences();
  const theme = useCanvasTheme();
  return <Excalidraw langCode={language} theme={theme} aiEnabled={false} validateEmbeddable={false}
    handleKeyboardGlobally={false} UIOptions={CANVAS_UI_OPTIONS} {...props} />;
}

export interface ZoomRange {
  readonly min: number;
  readonly max: number;
}

/** MonkeyBoard's wheel range. */
export const BOARD_ZOOM: ZoomRange = { min: 0.1, max: 30 };

/**
 * The plain wheel zooms at the pointer, as MonkeyBoard's canvas always has.
 * A wheel with Ctrl, Cmd or Shift keeps Excalidraw's own meaning.
 */
export function useWheelZoom(root: RefObject<HTMLElement | null>, canvas: RefObject<ExcalidrawImperativeAPI | null>,
  range: ZoomRange = BOARD_ZOOM): void {
  const limits = useRef(range);
  limits.current = range;
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      const api = canvas.current;
      if (!(event.target instanceof HTMLCanvasElement) || !api || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault(); event.stopPropagation();
      const state = api.getAppState();
      const point = viewportCoordsToSceneCoords({ clientX: event.clientX, clientY: event.clientY }, state);
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      const zoom = Math.min(limits.current.max, Math.max(limits.current.min, state.zoom.value * Math.exp(-delta * 0.0015))) as AppState["zoom"]["value"];
      api.updateScene({ appState: { zoom: { value: zoom }, scrollX: (point.x + state.scrollX) * state.zoom.value / zoom - point.x, scrollY: (point.y + state.scrollY) * state.zoom.value / zoom - point.y }, captureUpdate: CaptureUpdateAction.NEVER });
    };
    element.addEventListener("wheel", wheel, { passive: false, capture: true });
    return () => element.removeEventListener("wheel", wheel, true);
  }, [root, canvas]);
}

/** A press that moves further than this is a pan, not a click. */
const CLICK_SLOP = 4;

export interface ScenePointerHandlers {
  /** A primary click on the canvas, in scene coordinates. */
  onClick?(point: ScenePoint, api: ExcalidrawImperativeAPI, event: PointerEvent): void;
  /** A double click on the canvas, in scene coordinates; true keeps it from Excalidraw. */
  onDoubleClick?(point: ScenePoint, api: ExcalidrawImperativeAPI, event: MouseEvent): boolean;
}

/**
 * Clicks on the canvas, read in the capture phase before Excalidraw acts on
 * them and converted to scene coordinates for the caller's own hit test
 * (`sceneHit`). The newest handlers are used without re-subscribing.
 */
export function useScenePointer(root: RefObject<HTMLElement | null>, canvas: RefObject<ExcalidrawImperativeAPI | null>,
  handlers: ScenePointerHandlers, enabled = true): void {
  const current = useRef(handlers);
  current.current = handlers;
  useEffect(() => {
    const element = root.current;
    if (!element || !enabled) return;
    const scenePoint = (event: MouseEvent, api: ExcalidrawImperativeAPI): ScenePoint =>
      viewportCoordsToSceneCoords({ clientX: event.clientX, clientY: event.clientY }, api.getAppState());
    let press: { x: number; y: number; pointerId: number } | null = null;
    const down = (event: PointerEvent) => {
      press = event.button === 0 && event.isPrimary && event.target instanceof HTMLCanvasElement
        ? { x: event.clientX, y: event.clientY, pointerId: event.pointerId } : null;
    };
    const up = (event: PointerEvent) => {
      const start = press;
      press = null;
      const api = canvas.current, onClick = current.current.onClick;
      if (!start || !api || !onClick || event.pointerId !== start.pointerId ||
          Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_SLOP) return;
      onClick(scenePoint(event, api), api, event);
    };
    const double = (event: MouseEvent) => {
      const api = canvas.current, onDoubleClick = current.current.onDoubleClick;
      if (!(event.target instanceof HTMLCanvasElement) || !api || !onDoubleClick) return;
      // The canvas would otherwise start its own double-click gesture here.
      if (onDoubleClick(scenePoint(event, api), api, event)) { event.preventDefault(); event.stopPropagation(); }
    };
    element.addEventListener("pointerdown", down, true);
    window.addEventListener("pointerup", up, true);
    element.addEventListener("dblclick", double, true);
    return () => {
      element.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointerup", up, true);
      element.removeEventListener("dblclick", double, true);
    };
  }, [root, canvas, enabled]);
}
