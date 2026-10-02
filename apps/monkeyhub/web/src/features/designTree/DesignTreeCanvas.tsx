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
 * Close cards show their models' server thumbnails (#406, #367): the store
 * names each model's thumbnail blob, so the cards in view at the close level
 * read theirs with no status request; a model without one is asked for once,
 * and its card keeps its words until the store hears the drawing is done. An
 * image is drawn at about twice its cell (#409), shared with every other
 * thumbnail of the page, and added to Excalidraw's files once, with the first
 * scene that draws it. Images arriving together rebuild the scene once a frame.
 */
import { useEffect, useMemo, useReducer, useRef, useState, type CSSProperties } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, FONT_FAMILY } from "@excalidraw/excalidraw";
import type { AppState, BinaryFileData, ExcalidrawImperativeAPI, ExcalidrawInitialDataState, UIOptions } from "@excalidraw/excalidraw/types";
import { useProjectStore, useProjectStoreInstance, useStudio } from "../../api/project-runtime/ProjectRuntimeContext";
import type { ModelSourceDto } from "../../api/project-runtime/generated";
import { askThumbnail, canvasThumbnail, thumbnailBlobs, thumbnailsMoved, type CanvasThumbnail } from "../artifacts/modelThumbnails";
import { CANVAS_APP_STATE, PROJECT_CANVAS_CLASS, ProjectCanvas, useScenePointer, useWheelZoom, type ZoomRange } from "../canvas/ProjectCanvas";
import { topmostAt } from "../canvas/sceneHit";
import { layoutGrowthTree, type Box } from "./layout";
import { CURRENT, trunkKey, type GrowthTree } from "./model";
import type { DesignTreeSource } from "./contract";
import { createPreviewLoader, nodeModelSource, nodesWantingPreviews, type PreviewLoader } from "./previews";
import { buildTreeScene, LIGHT_TREE_PALETTE, PREVIEW_PIXELS, type SceneHit, type TreePalette, type ZoomLevel } from "./scene";
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

export default function DesignTreeCanvas({ tree, source = null, words, selected, fitRequest, centerOn = null, title, onSelect, onAccept, onLevel,
  onExpand = () => undefined }: {
  tree: GrowthTree;
  /** What the tree was built from: it names each node's model, whose preview a close card shows. */
  source?: DesignTreeSource | null;
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
  /** A fold was clicked (#575): open Current's earlier steps, or the drafts its line superseded. */
  onExpand?(fold: "steps" | "drafts"): void;
}) {
  const canvas = useRef<ExcalidrawImperativeAPI | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const columns = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const [width, setWidth] = useState(0);
  const layout = useMemo(() => layoutGrowthTree(tree), [tree]);
  const [detail, setDetail] = useState<{ level: ZoomLevel; textScale: number }>({ level: "mid", textScale: 1 });
  const level = useRef<ZoomLevel>("mid");
  const palette = useTreePalette();

  // Each node's model, and the blob of its thumbnail: the store's, or a done answer to this canvas's own ask.
  const models = useMemo(() => {
    const byNode = new Map<string, ModelSourceDto>();
    for (const node of tree.nodes.values()) {
      const model = nodeModelSource(source, node);
      if (model) byNode.set(node.id, model);
    }
    return byNode;
  }, [tree, source]);
  const modelsNow = useRef(models);
  modelsNow.current = models;
  const studio = useStudio();
  const store = useProjectStoreInstance();
  const thumbnailsAt = useProjectStore(thumbnailsMoved);
  // A done answer is kept until its blob cannot be read (the server lost it and draws it again).
  const [answered, answer] = useReducer((known: ReadonlyMap<string, string>,
    action: { readonly asset: string; readonly blob: string } | { readonly lost: string }) => {
    if ("lost" in action) {
      if (![...known.values()].includes(action.lost)) return known;
      return new Map([...known].filter(([, blob]) => blob !== action.lost));
    }
    return known.get(action.asset) === action.blob ? known : new Map(known).set(action.asset, action.blob);
  }, new Map<string, string>());
  const blobs = useMemo(() => {
    const held = thumbnailBlobs(store.getSnapshot());
    const byNode = new Map<string, string>();
    for (const [node, model] of models) {
      const blob = held.get(model.assetSha256) ?? answered.get(model.assetSha256);
      if (blob) byNode.set(node, blob);
    }
    return byNode;
  }, [models, store, thumbnailsAt, answered]);
  const blobsNow = useRef(blobs);
  blobsNow.current = blobs;
  // Images arrive one by one: the scene is rebuilt at most once a frame for them, and not at all while no card
  // draws images (an image read at close that lands after zooming out waits for the next close scene).
  const [imagesRead, imageRead] = useReducer((count: number) => count + 1, 0);
  const imageFrame = useRef(0);
  const [loader, setLoader] = useState<PreviewLoader<CanvasThumbnail> | null>(null);
  useEffect(() => {
    const arrived = () => {
      if (level.current !== "close" || imageFrame.current) return;
      imageFrame.current = requestAnimationFrame(() => { imageFrame.current = 0; imageRead(); });
    };
    const created = createPreviewLoader<CanvasThumbnail>((blob) => canvasThumbnail(studio, blob, PREVIEW_PIXELS.width,
      PREVIEW_PIXELS.height).then((image) => { if (!image) answer({ lost: blob }); return image; }),
    { limit: 3, onChange: arrived });
    setLoader(created);
    return () => { created.dispose(); cancelAnimationFrame(imageFrame.current); imageFrame.current = 0; };
  }, [studio]);
  /** Reads the thumbnails of the cards in view at the close level, asks for those not drawn yet, and nothing otherwise. */
  const wantPreviews = () => {
    const state = canvas.current?.getAppState();
    const nodes = state ? nodesWantingPreviews(drawn.current, { scrollX: state.scrollX, scrollY: state.scrollY, zoom: state.zoom.value,
      width: state.width, height: state.height }, level.current) : [];
    const wanted: string[] = [];
    for (const node of nodes) {
      const blob = blobsNow.current.get(node), model = modelsNow.current.get(node);
      if (blob) wanted.push(blob);
      else if (model) void askThumbnail(studio, model).then((done) => { if (done) answer({ asset: model.assetSha256, blob: done }); });
    }
    loader?.want(wanted);
  };

  const scene = useMemo(() => {
    const previews = new Map<string, CanvasThumbnail>();
    if (loader && detail.level === "close") {
      for (const [node, blob] of blobs) {
        const image = loader.get(blob);
        if (image) previews.set(node, image);
      }
    }
    const built = buildTreeScene(tree, layout, { ...detail, selected, fontFamily: FONT_FAMILY.Helvetica, words: words.scene, palette, previews });
    return { elements: convertToExcalidrawElements(built.skeletons, { regenerateIds: false }), hits: built.hits, ground: palette.ground,
      files: [...previews.values()] };
  }, [tree, layout, detail, selected, words, palette, blobs, loader, imagesRead]);
  const hits = useRef<SceneHit[]>(scene.hits);
  const [initialData] = useState<ExcalidrawInitialDataState>(() => ({ elements: scene.elements,
    appState: { ...CANVAS_APP_STATE, viewBackgroundColor: scene.ground } }));
  const filesAdded = useRef(new Set<string>());
  useEffect(() => {
    hits.current = scene.hits;
    const api = canvas.current;
    if (!api) return;
    api.updateScene({ elements: scene.elements, appState: { viewBackgroundColor: scene.ground }, captureUpdate: CaptureUpdateAction.NEVER });
    // A new image's file goes in once the scene draws it: Excalidraw decodes the files its scene's images use.
    const files: BinaryFileData[] = scene.files.filter((image) => !filesAdded.current.has(image.fileId)).map((image) => ({
      id: image.fileId as BinaryFileData["id"], dataURL: image.dataURL as BinaryFileData["dataURL"],
      mimeType: image.mimeType as BinaryFileData["mimeType"], created: Date.now() }));
    if (files.length > 0) {
      api.addFiles(files);
      for (const file of files) filesAdded.current.add(file.id);
    }
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
  // A projection landed: a card whose image could not be read (its blob was being drawn again) reads it again.
  useEffect(() => { loader?.retry(); }, [loader, thumbnailsAt]);
  useEffect(() => { if (ready) wantPreviews(); }, [ready, layout, blobs, loader, width]);
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
      if (hit.action === "expand" && hit.expand) { onExpand(hit.expand); return; }
      onSelect(hit.node);
    },
  });

  return <div className={`design-tree__canvas ${PROJECT_CANVAS_CLASS}`} ref={root} data-level={detail.level} data-text-scale={detail.textScale}>
    <ProjectCanvas initialData={initialData} excalidrawAPI={(api) => { canvas.current = api; setReady(true); }} name={title}
      viewModeEnabled UIOptions={TREE_UI}
      onScrollChange={(scrollX, _y, zoom) => {
        view.current = { scrollX, zoom: zoom.value };
        follow();
        level.current = levelFor(zoom.value, level.current);
        const next = { level: level.current, textScale: textScaleFor(zoom.value, level.current) };
        setDetail((previous) => next.level === previous.level && next.textScale === previous.textScale ? previous : next);
        wantPreviews();
      }} />
    {/* #353: one column per Stage on the trunk, headed at the top of the canvas, then the trailing column of the work
        growing towards the next Stage; the list names the same nodes to assistive technology. The header strip takes
        its own clicks, so nothing under it is picked or deselected through it. */}
    <div className="design-tree__columns" ref={columns} aria-hidden="true">
      <div className="design-tree__strip" />
      {layout.columns.map((column, index) => {
        const head = words.column(column.node, layout.columns[index - 1]?.node ?? null);
        return <div key={column.node ?? "next"} className="design-tree-column" data-node={column.node ?? undefined} data-next={head.next || undefined}
          style={{ "--column-x": column.x, "--column-width": column.width, "--column-inset": column.start - column.x } as CSSProperties}>
          <div className="design-tree-column__head">
            <span className="design-tree-column__title">
              {head.id && <span className="design-tree-column__id">{head.id}</span>}
              {head.name && <span className="design-tree-column__name">{head.name}</span>}
            </span>
            <span className="design-tree-column__line" />
            {column.options > 0 && <span className="design-tree-column__count">{column.options}</span>}
          </div>
        </div>;
      })}
    </div>
  </div>;
}
