/**
 * The growth tree on the project canvas MonkeyBoard uses.
 *
 * View mode: the tree is a projection of retained facts, never an edited
 * scene, and nothing here is saved. The scene is rebuilt only when the tree,
 * the selection, the level of detail or the Hub's colours change. Fitting is
 * the host's own: Excalidraw's fit rounds zoom down to tenths and stops at 10 %.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, FONT_FAMILY } from "@excalidraw/excalidraw";
import type { AppState, ExcalidrawImperativeAPI, ExcalidrawInitialDataState, UIOptions } from "@excalidraw/excalidraw/types";
import { CANVAS_APP_STATE, PROJECT_CANVAS_CLASS, ProjectCanvas, useScenePointer, useWheelZoom, type ZoomRange } from "../canvas/ProjectCanvas";
import { topmostAt } from "../canvas/sceneHit";
import { layoutGrowthTree, type Box } from "./layout";
import { CURRENT, trunkKey, type GrowthTree } from "./model";
import { buildTreeScene, LIGHT_TREE_PALETTE, type SceneHit, type SceneWords, type TreePalette, type ZoomLevel } from "./scene";

const TREE_ZOOM: ZoomRange = { min: 0.05, max: 4 };
const TREE_UI: UIOptions = {
  canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false, clearCanvas: false,
    changeViewBackgroundColor: false, toggleTheme: false },
  tools: { image: false },
};

/** The Hub token each scene colour takes (#337). */
const PALETTE_TOKENS: Readonly<Record<keyof TreePalette, string>> = {
  ground: "--ground", ink: "--ink", ink2: "--ink-2", faint: "--faint", accent: "--accent", onAccent: "--accent-ink",
  accentSoft: "--accent-soft", paper: "--panel-2", tile: "--panel", twig: "--rule", muted: "--rule-soft",
  running: "--processing", warn: "--unchecked",
};

/**
 * The palette from the Hub's tokens as they are now. A translucent token is laid
 * on the ground, so a card still hides the lines under it.
 */
function readTreePalette(): TreePalette {
  const probe = document.createElement("canvas").getContext("2d");
  if (!probe) return LIGHT_TREE_PALETTE;
  const rgba = (value: string): readonly [number, number, number, number] | null => {
    if (!value) return null;
    // The browser reads any CSS colour; one it cannot read leaves the marker.
    probe.fillStyle = "#010203";
    probe.fillStyle = value;
    const read = String(probe.fillStyle);
    const hex = /^#([0-9a-f]{6})$/i.exec(read);
    if (hex) return read === "#010203" && value.toLowerCase() !== "#010203" ? null
      : [parseInt(hex[1].slice(0, 2), 16), parseInt(hex[1].slice(2, 4), 16), parseInt(hex[1].slice(4), 16), 1];
    const parts = /^rgba?\(([^)]*)\)$/i.exec(read)?.[1].split(",").map(Number);
    return parts?.length === 4 && parts.every(Number.isFinite) ? [parts[0], parts[1], parts[2], parts[3]] : null;
  };
  const style = getComputedStyle(document.documentElement);
  const token = (name: keyof TreePalette) => rgba(style.getPropertyValue(PALETTE_TOKENS[name]).trim()) ?? rgba(LIGHT_TREE_PALETTE[name])!;
  const ground = token("ground");
  const opaque = (name: keyof TreePalette) => {
    const [r, g, b, a] = token(name);
    return `#${[[r, ground[0]], [g, ground[1]], [b, ground[2]]].map(([top, under]) =>
      Math.round(top * a + under * (1 - a)).toString(16).padStart(2, "0")).join("")}`;
  };
  return Object.fromEntries((Object.keys(PALETTE_TOKENS) as (keyof TreePalette)[]).map((name) => [name, opaque(name)])) as unknown as TreePalette;
}

/** The palette, read again when the theme or the interface style changes. */
function useTreePalette(): TreePalette {
  const [palette, setPalette] = useState(readTreePalette);
  useEffect(() => {
    const update = () => setPalette((previous) => {
      const next = readTreePalette();
      return (Object.keys(next) as (keyof TreePalette)[]).every((name) => next[name] === previous[name]) ? previous : next;
    });
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-ui-style"] });
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", update);
    update();
    return () => { observer.disconnect(); media.removeEventListener("change", update); };
  }, []);
  return palette;
}

/** Far below 50 %, close from 125 %; a small band keeps the level from flickering at the edge. */
export function levelFor(zoom: number, previous: ZoomLevel): ZoomLevel {
  const far = previous === "far" ? zoom <= 0.52 : zoom < 0.48;
  if (far) return "far";
  const close = previous === "close" ? zoom >= 1.22 : zoom > 1.28;
  return close ? "close" : "mid";
}

/** Text grows as the view shrinks, in steps, so labels keep about the same size on screen. */
export function textScaleFor(zoom: number, level: ZoomLevel): number {
  if (level === "close") return 1;
  if (level === "mid") return Math.min(2, Math.max(1, Math.round(4 / Math.max(zoom, 0.01)) / 4));
  return Math.min(24, 2 ** (Math.round(2 * Math.log2(1 / Math.max(zoom, 0.01))) / 2));
}

export default function DesignTreeCanvas({ tree, words, selected, fitRequest, centerOn = null, title, onSelect, onAccept, onLevel }: {
  tree: GrowthTree;
  words: SceneWords;
  selected: string | null;
  /** Changes when the viewer asks to see the whole tree again. */
  fitRequest: number;
  /** A node to bring into view, once per request, clear of the side card. */
  centerOn?: { node: string; request: number } | null;
  title: string;
  onSelect(node: string | null): void;
  onAccept(): void;
  onLevel(level: ZoomLevel): void;
}) {
  const canvas = useRef<ExcalidrawImperativeAPI | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const layout = useMemo(() => layoutGrowthTree(tree), [tree]);
  const [detail, setDetail] = useState<{ level: ZoomLevel; textScale: number }>({ level: "mid", textScale: 1 });
  const palette = useTreePalette();
  const scene = useMemo(() => {
    const built = buildTreeScene(tree, layout, { ...detail, selected, fontFamily: FONT_FAMILY.Helvetica, words, palette });
    return { elements: convertToExcalidrawElements(built.skeletons, { regenerateIds: false }), hits: built.hits, ground: palette.ground };
  }, [tree, layout, detail, selected, words, palette]);
  const hits = useRef<SceneHit[]>(scene.hits);
  const [initialData] = useState<ExcalidrawInitialDataState>(() => ({ elements: scene.elements,
    appState: { ...CANVAS_APP_STATE, viewBackgroundColor: scene.ground } }));
  useEffect(() => {
    hits.current = scene.hits;
    canvas.current?.updateScene({ elements: scene.elements, appState: { viewBackgroundColor: scene.ground }, captureUpdate: CaptureUpdateAction.NEVER });
  }, [scene, ready]);

  const drawn = useRef(layout);
  drawn.current = layout;
  const PAD = 48;
  const zoomFor = (box: Box, width: number, height: number, maxZoom: number) => Math.max(TREE_ZOOM.min,
    Math.min(maxZoom, (width - PAD * 2) / Math.max(1, box.width), (height - PAD * 2) / Math.max(1, box.height)));
  const fitTo = (box: Box, maxZoom: number): boolean => {
    const api = canvas.current;
    const state = api?.getAppState();
    if (!api || !state || state.width < 40 || state.height < 40) return false;
    const zoom = zoomFor(box, state.width, state.height, maxZoom) as AppState["zoom"]["value"];
    api.updateScene({ appState: { zoom: { value: zoom }, scrollX: state.width / (2 * zoom) - (box.x + box.width / 2),
      scrollY: state.height / (2 * zoom) - (box.y + box.height / 2) }, captureUpdate: CaptureUpdateAction.NEVER });
    return true;
  };
  /**
   * The whole tree while it still reads at the middle level. When it does not,
   * the view opens where the tree is growing: the trunk at mid-height and
   * Current at the right edge, with the older history to the left.
   */
  const fitView = (whole: boolean): boolean => {
    const api = canvas.current;
    const state = api?.getAppState();
    if (!api || !state || state.width < 40 || state.height < 40) return false;
    const { bounds, nodes } = drawn.current;
    if (whole || zoomFor(bounds, state.width, state.height, 1.1) >= 0.52) return fitTo(bounds, 1.1);
    const tip = nodes.get(CURRENT) ?? [...nodes.values()].at(-1)!;
    const zoom = Math.min(0.85, Math.max(0.6, (state.height - PAD * 2) / Math.max(1, bounds.height))) as AppState["zoom"]["value"];
    api.updateScene({ appState: { zoom: { value: zoom }, scrollX: (state.width - PAD / 2) / zoom - (tip.footprint.x + tip.footprint.width),
      scrollY: state.height / (2 * zoom) - tip.y }, captureUpdate: CaptureUpdateAction.NEVER });
    return true;
  };
  // Fit when the tree first shows, when its trunk changes (Continue, Accept) and on request.
  const fitWanted = useRef<boolean | null>(false);
  const fitFrame = useRef(0);
  const requestFit = (whole: boolean) => {
    fitWanted.current = whole;
    cancelAnimationFrame(fitFrame.current);
    let tries = 0;
    const attempt = () => {
      if (fitWanted.current === null) return;
      if (fitView(fitWanted.current)) { fitWanted.current = null; return; }
      if (++tries < 120) fitFrame.current = requestAnimationFrame(attempt);
    };
    fitFrame.current = requestAnimationFrame(attempt);
  };
  const key = trunkKey(tree);
  useEffect(() => { if (ready) requestFit(false); }, [ready, key]);
  useEffect(() => { if (ready && fitRequest > 0) requestFit(true); }, [fitRequest]);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    // A hidden surface has no size; the first time it shows, the pending fit runs.
    const observer = new ResizeObserver(() => { if (fitWanted.current !== null) requestFit(fitWanted.current); });
    observer.observe(element);
    return () => { observer.disconnect(); cancelAnimationFrame(fitFrame.current); };
  }, []);
  useEffect(() => { onLevel(detail.level); }, [detail.level, onLevel]);
  // A focused node replaces the pending fit: at a readable zoom, centred in the part of the
  // canvas the side card leaves free.
  useEffect(() => {
    if (!ready || !centerOn) return;
    fitWanted.current = null;
    cancelAnimationFrame(fitFrame.current);
    let tries = 0, frame = 0;
    const attempt = () => {
      const api = canvas.current, state = api?.getAppState(), placed = drawn.current.nodes.get(centerOn.node);
      if (api && state && placed && state.width >= 40 && state.height >= 40) {
        const zoom = Math.max(state.zoom.value, 0.8) as AppState["zoom"]["value"];
        const free = state.width > 700 ? state.width - 344 : state.width, box = placed.footprint;
        api.updateScene({ appState: { zoom: { value: zoom }, scrollX: free / (2 * zoom) - (box.x + box.width / 2),
          scrollY: state.height / (2 * zoom) - (box.y + box.height / 2) }, captureUpdate: CaptureUpdateAction.NEVER });
        return;
      }
      if (++tries < 120) frame = requestAnimationFrame(attempt);
    };
    frame = requestAnimationFrame(attempt);
    return () => cancelAnimationFrame(frame);
  }, [ready, centerOn?.node, centerOn?.request]);

  useWheelZoom(root, canvas, TREE_ZOOM);
  useScenePointer(root, canvas, {
    onClick: (point) => {
      const hit = topmostAt(hits.current, point);
      if (!hit) { onSelect(null); return; }
      if (hit.action === "zoom" && hit.zoomTo) { fitTo(hit.zoomTo, 0.9); return; }
      if (hit.action === "accept") { onAccept(); return; }
      onSelect(hit.node);
    },
  });

  return <div className={`design-tree__canvas ${PROJECT_CANVAS_CLASS}`} ref={root} data-level={detail.level} data-text-scale={detail.textScale}>
    <ProjectCanvas initialData={initialData} excalidrawAPI={(api) => { canvas.current = api; setReady(true); }} name={title}
      viewModeEnabled UIOptions={TREE_UI}
      onScrollChange={(_x, _y, zoom) => setDetail((previous) => {
        const level = levelFor(zoom.value, previous.level);
        const textScale = textScaleFor(zoom.value, level);
        return level === previous.level && textScale === previous.textScale ? previous : { level, textScale };
      })} />
  </div>;
}
