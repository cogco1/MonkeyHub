/**
 * The growth tree on the project canvas MonkeyBoard uses.
 *
 * View mode: the tree is a projection of retained facts, never an edited
 * scene, and nothing here is saved. The scene is rebuilt only when the tree,
 * the selection or the level of detail changes. Fitting is the host's own:
 * Excalidraw's fit rounds zoom down to tenths and stops at 10 %.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, FONT_FAMILY } from "@excalidraw/excalidraw";
import type { AppState, ExcalidrawImperativeAPI, ExcalidrawInitialDataState, UIOptions } from "@excalidraw/excalidraw/types";
import { CANVAS_APP_STATE, PROJECT_CANVAS_CLASS, ProjectCanvas, useScenePointer, useWheelZoom, type ZoomRange } from "../canvas/ProjectCanvas";
import { topmostAt } from "../canvas/sceneHit";
import { layoutGrowthTree, type Box } from "./layout";
import { CURRENT, trunkKey, type GrowthTree } from "./model";
import { buildTreeScene, type SceneHit, type SceneWords, type ZoomLevel } from "./scene";

const TREE_ZOOM: ZoomRange = { min: 0.05, max: 4 };
const TREE_UI: UIOptions = {
  canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false, clearCanvas: false,
    changeViewBackgroundColor: false, toggleTheme: false },
  tools: { image: false },
};

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

export default function DesignTreeCanvas({ tree, words, selected, fitRequest, title, onSelect, onAccept, onLevel }: {
  tree: GrowthTree;
  words: SceneWords;
  selected: string | null;
  /** Changes when the viewer asks to see the whole tree again. */
  fitRequest: number;
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
  const scene = useMemo(() => {
    const built = buildTreeScene(tree, layout, { ...detail, selected, fontFamily: FONT_FAMILY.Helvetica, words });
    return { elements: convertToExcalidrawElements(built.skeletons, { regenerateIds: false }), hits: built.hits };
  }, [tree, layout, detail, selected, words]);
  const hits = useRef<SceneHit[]>(scene.hits);
  const [initialData] = useState<ExcalidrawInitialDataState>(() => ({ elements: scene.elements, appState: { ...CANVAS_APP_STATE } }));
  useEffect(() => {
    hits.current = scene.hits;
    canvas.current?.updateScene({ elements: scene.elements, captureUpdate: CaptureUpdateAction.NEVER });
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
