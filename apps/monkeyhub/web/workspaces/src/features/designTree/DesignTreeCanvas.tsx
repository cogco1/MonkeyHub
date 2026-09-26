/**
 * The growth tree on the project canvas MonkeyBoard uses.
 *
 * View mode: the tree is a projection of retained facts, never an edited
 * scene, and nothing here is saved. The scene is rebuilt only when the tree,
 * the selection, the level of detail or the Hub's colours change. Fitting is
 * the host's own: Excalidraw's fit rounds zoom down to tenths and stops at 10 %.
 * The Stage columns' headers and rules are the page's own words over the
 * canvas (#353): they stay at its top while the tree pans under them, and
 * follow the view through two custom properties rather than a render.
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, FONT_FAMILY } from "@excalidraw/excalidraw";
import type { AppState, ExcalidrawImperativeAPI, ExcalidrawInitialDataState, UIOptions } from "@excalidraw/excalidraw/types";
import { CANVAS_APP_STATE, PROJECT_CANVAS_CLASS, ProjectCanvas, useScenePointer, useWheelZoom, type ZoomRange } from "../canvas/ProjectCanvas";
import { topmostAt } from "../canvas/sceneHit";
import { layoutGrowthTree, type Box } from "./layout";
import { CURRENT, trunkKey, type GrowthTree } from "./model";
import { buildTreeScene, LIGHT_TREE_PALETTE, type SceneHit, type TreePalette, type ZoomLevel } from "./scene";
import type { TreeWords } from "./words";

const TREE_ZOOM: ZoomRange = { min: 0.05, max: 4 };
const TREE_UI: UIOptions = {
  canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false, clearCanvas: false,
    changeViewBackgroundColor: false, toggleTheme: false },
  tools: { image: false },
};
/** Screen pixels around a fitted tree, and the strip at the top that the column headers take. */
const PAD = 48;
const HEADER = 36;

/** The Hub token each scene colour takes (#337). */
const PALETTE_TOKENS: Readonly<Record<keyof TreePalette, string>> = {
  ground: "--ground", ink: "--ink", ink2: "--ink-2", faint: "--faint", accent: "--accent", onAccent: "--accent-ink",
  accentSoft: "--accent-soft", paper: "--panel-2", twig: "--rule", muted: "--rule-soft",
  running: "--processing", held: "--held", violated: "--violated", unchecked: "--unchecked",
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
  words: TreeWords;
  selected: string | null;
  /** Changes when the viewer asks to see the whole tree again. */
  fitRequest: number;
  /** A node to bring into view, once per request. */
  centerOn?: { node: string; request: number } | null;
  title: string;
  onSelect(node: string | null): void;
  onAccept(): void;
  onLevel(level: ZoomLevel): void;
}) {
  const canvas = useRef<ExcalidrawImperativeAPI | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const columns = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const [width, setWidth] = useState(0);
  const layout = useMemo(() => layoutGrowthTree(tree), [tree]);
  const [detail, setDetail] = useState<{ level: ZoomLevel; textScale: number }>({ level: "mid", textScale: 1 });
  const palette = useTreePalette();
  const scene = useMemo(() => {
    const built = buildTreeScene(tree, layout, { ...detail, selected, fontFamily: FONT_FAMILY.Helvetica, words: words.scene, palette });
    return { elements: convertToExcalidrawElements(built.skeletons, { regenerateIds: false }), hits: built.hits, ground: palette.ground };
  }, [tree, layout, detail, selected, words, palette]);
  const hits = useRef<SceneHit[]>(scene.hits);
  const [initialData] = useState<ExcalidrawInitialDataState>(() => ({ elements: scene.elements,
    appState: { ...CANVAS_APP_STATE, viewBackgroundColor: scene.ground } }));
  useEffect(() => {
    hits.current = scene.hits;
    canvas.current?.updateScene({ elements: scene.elements, appState: { viewBackgroundColor: scene.ground }, captureUpdate: CaptureUpdateAction.NEVER });
  }, [scene, ready]);

  // The headers follow the view: scene units to screen pixels, (x + scrollX) × zoom.
  const view = useRef({ scrollX: 0, zoom: 1 });
  const follow = () => {
    columns.current?.style.setProperty("--tree-scroll-x", String(view.current.scrollX));
    columns.current?.style.setProperty("--tree-zoom", String(view.current.zoom));
  };
  useEffect(follow, [layout]);

  const drawn = useRef(layout);
  drawn.current = layout;
  const zoomFor = (box: Box, width: number, height: number, maxZoom: number) => Math.max(TREE_ZOOM.min,
    Math.min(maxZoom, (width - PAD * 2) / Math.max(1, box.width), (height - PAD * 2 - HEADER) / Math.max(1, box.height)));
  /** The box in view, centred; or, for the whole tree, hung from the headers as it grows downwards. */
  const fitTo = (box: Box, maxZoom: number, top = false): boolean => {
    const api = canvas.current;
    const state = api?.getAppState();
    if (!api || !state || state.width < 40 || state.height < 40) return false;
    const zoom = zoomFor(box, state.width, state.height, maxZoom) as AppState["zoom"]["value"];
    api.updateScene({ appState: { zoom: { value: zoom }, scrollX: state.width / (2 * zoom) - (box.x + box.width / 2),
      scrollY: top ? (HEADER + PAD) / zoom - box.y : (state.height + HEADER) / (2 * zoom) - (box.y + box.height / 2) },
    captureUpdate: CaptureUpdateAction.NEVER });
    return true;
  };
  /**
   * The whole tree while it still reads at the middle level. When it does not,
   * the view opens where the tree is growing: the trunk under the headers and
   * Current at the right edge, with the older history to the left.
   */
  const fitView = (whole: boolean): boolean => {
    const api = canvas.current;
    const state = api?.getAppState();
    if (!api || !state || state.width < 40 || state.height < 40) return false;
    const { bounds, nodes } = drawn.current;
    if (whole || zoomFor(bounds, state.width, state.height, 1.1) >= 0.52) return fitTo(bounds, 1.1, true);
    const tip = nodes.get(CURRENT) ?? [...nodes.values()].at(-1)!;
    const zoom = Math.min(0.85, Math.max(0.6, (state.height - PAD * 2 - HEADER) / Math.max(1, bounds.height))) as AppState["zoom"]["value"];
    api.updateScene({ appState: { zoom: { value: zoom }, scrollX: (state.width - PAD / 2) / zoom - (tip.footprint.x + tip.footprint.width),
      scrollY: (HEADER + PAD) / zoom - bounds.y }, captureUpdate: CaptureUpdateAction.NEVER });
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
    const observer = new ResizeObserver(() => {
      setWidth(element.clientWidth);
      if (fitWanted.current !== null) requestFit(fitWanted.current);
    });
    observer.observe(element);
    return () => { observer.disconnect(); cancelAnimationFrame(fitFrame.current); };
  }, []);
  useEffect(() => { onLevel(detail.level); }, [detail.level, onLevel]);
  // A focused node replaces the pending fit: at a readable zoom, centred on the canvas.
  useEffect(() => {
    if (!ready || !centerOn) return;
    fitWanted.current = null;
    cancelAnimationFrame(fitFrame.current);
    let tries = 0, frame = 0;
    const attempt = () => {
      const api = canvas.current, state = api?.getAppState(), placed = drawn.current.nodes.get(centerOn.node);
      const free = root.current?.clientWidth ?? 0;
      if (api && state && placed && free >= 40 && state.height >= 40) {
        const zoom = Math.max(state.zoom.value, 0.8) as AppState["zoom"]["value"];
        const box = placed.footprint;
        api.updateScene({ appState: { zoom: { value: zoom }, scrollX: free / (2 * zoom) - (box.x + box.width / 2),
          scrollY: (state.height + HEADER) / (2 * zoom) - (box.y + box.height / 2) }, captureUpdate: CaptureUpdateAction.NEVER });
        return;
      }
      if (++tries < 120) frame = requestAnimationFrame(attempt);
    };
    frame = requestAnimationFrame(attempt);
    return () => cancelAnimationFrame(frame);
  }, [ready, centerOn?.node, centerOn?.request]);
  // The inspector docks beside the canvas and takes its right side: a selected node it
  // would hide moves just far enough to stay in sight.
  useEffect(() => {
    const api = canvas.current, state = api?.getAppState(), placed = selected ? drawn.current.nodes.get(selected) : undefined;
    if (!ready || !api || !state || !placed || width < 40) return;
    const zoom = state.zoom.value, box = placed.footprint, margin = 24;
    const left = (box.x + state.scrollX) * zoom, right = (box.x + box.width + state.scrollX) * zoom;
    let shift = Math.max(0, right - (width - margin));
    if (left - shift < margin) shift = left - margin;
    if (Math.abs(shift) > 0.5) api.updateScene({ appState: { scrollX: state.scrollX - shift / zoom }, captureUpdate: CaptureUpdateAction.NEVER });
  }, [ready, selected, width]);

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
      onScrollChange={(scrollX, _y, zoom) => {
        view.current = { scrollX, zoom: zoom.value };
        follow();
        setDetail((previous) => {
          const level = levelFor(zoom.value, previous.level);
          const textScale = textScaleFor(zoom.value, level);
          return level === previous.level && textScale === previous.textScale ? previous : { level, textScale };
        });
      }} />
    {/* #353: one column per Stage on the trunk, its header at the top of the canvas; the list names the same nodes to assistive technology. */}
    <div className="design-tree__columns" ref={columns} aria-hidden="true">
      {layout.columns.map((column) => {
        const head = words.column(tree.nodes.get(column.node)!);
        return <div key={column.node} className="design-tree-column" data-node={column.node}
          style={{ "--column-x": column.x, "--column-width": column.width, "--column-inset": column.start - column.x } as CSSProperties}>
          <div className="design-tree-column__head">
            {head.id && <span className="design-tree-column__id">{head.id}</span>}
            {head.name && <span className="design-tree-column__name">{head.name}</span>}
            <span className="design-tree-column__line" />
            {column.options > 0 && <span className="design-tree-column__count">{column.options}</span>}
          </div>
        </div>;
      })}
    </div>
  </div>;
}
