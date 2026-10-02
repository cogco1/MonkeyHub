/**
 * The growth tree as Excalidraw element skeletons, one level of detail at a time.
 *
 * The caller passes the skeletons through `convertToExcalidrawElements`, the
 * way MonkeyBoard places its pages. Semantic zoom only changes what is drawn,
 * never where: far shows the trunk, Stage milestones and counts; middle draws
 * each option as a small card with its letter, short name and status bar;
 * close adds a one-line summary and a status, and a card whose model has a
 * retained preview shows that image in place of the summary (#406). Author,
 * time and runs belong to the inspector, and the Stage columns' headers are the canvas's own (#353).
 * Colours come from a palette the canvas reads from the Hub's tokens (#337), so
 * the tree follows the theme and interface style and is never inverted.
 * Every hit target is produced with the element it stands for, because view
 * mode leaves clicks to the host (`useScenePointer` + `topmostAt`). The two
 * folds (#575), Current's earlier steps and its line's superseded drafts, are
 * controls: their hit opens them rather than selecting a node.
 */
import type { ExcalidrawElementSkeleton } from "@excalidraw/excalidraw/data/transform";
import type { SceneBox } from "../canvas/sceneHit";
import type { Box, Fork, GrowthLayout, LayoutEdge, PlacedNode } from "./layout";
import { checkOf, type GrowthTree, type PendingStatus, type TreeNode } from "./model";

export type ZoomLevel = "far" | "mid" | "close";

/** The words a scene needs, already in the viewer's language. */
export interface SceneWords {
  current: string;
  origin: string;
  /** A card's name beside its letter: an option's label, or a running line's. */
  name(node: TreeNode): string;
  pending(status: PendingStatus): string;
  currentAt: string;
  accept: string;
  acceptBlocked: string;
  status(node: TreeNode): string;
  /** A point's counts, longest first: the far view uses the first that fits. */
  fork(fork: Fork): readonly string[];
}

export interface SceneOptions {
  readonly level: ZoomLevel;
  /** How much larger than 100 % text is drawn, so it stays legible when zoomed out. */
  readonly textScale: number;
  readonly selected: string | null;
  readonly fontFamily: number;
  readonly words: SceneWords;
  /** The light theme's colours when left out. */
  readonly palette?: TreePalette;
  /** Close only: the preview image each node's card shows, by node id, once the canvas has it. */
  readonly previews?: ReadonlyMap<string, ScenePreview>;
}

/** An image the canvas has added to Excalidraw's files, and its size in pixels. */
export interface ScenePreview {
  readonly fileId: string;
  readonly width: number;
  readonly height: number;
}

/** The scene's colours, each opaque: a card has to hide the lines under it. */
export interface TreePalette {
  /** The canvas behind everything. */
  readonly ground: string;
  readonly ink: string;
  readonly ink2: string;
  readonly faint: string;
  readonly accent: string;
  /** Words on an accent fill. */
  readonly onAccent: string;
  readonly accentSoft: string;
  /** A card's face, and words on an ink fill. */
  readonly paper: string;
  /** Option lines and option card edges. */
  readonly twig: string;
  /** Branches left behind, what cannot be done now, and options set aside in review. */
  readonly muted: string;
  /** Work still running. */
  readonly running: string;
  /** Review checks that held when the option was admitted. */
  readonly held: string;
  /** Review checks that did not hold: the violation marker (#294 Q2). */
  readonly violated: string;
  /** Admitted by an earlier record that carried no review verdict. */
  readonly unchecked: string;
}

/** The light theme's colours, for a test or a canvas that cannot read the Hub's tokens. */
export const LIGHT_TREE_PALETTE: TreePalette = {
  ground: "#f4f5f0", ink: "#29352d", ink2: "#41414a", faint: "#6b716a", accent: "#356b9e", onAccent: "#ffffff", accentSoft: "#e8eff6",
  paper: "#ffffff", twig: "#7b837a", muted: "#9ea39c", running: "#56656e", held: "#3d7754", violated: "#a64e47", unchecked: "#8a6123",
};

/** #575: a drafts card holding drafts the project cleaned is selected, so its inspector can offer Restore. */
const holdsCleaned = (node: TreeNode) => node.kind === "drafts" && node.drafts!.cleaned.length > 0;

export interface SceneHit extends SceneBox {
  readonly node: string;
  /** `expand` opens a fold (#575): Current's earlier steps, or the drafts its line superseded. */
  readonly action: "select" | "accept" | "zoom" | "expand";
  readonly zoomTo?: Box;
  readonly expand?: "steps" | "drafts";
}

export interface TreeScene {
  readonly skeletons: ExcalidrawElementSkeleton[];
  readonly hits: SceneHit[];
}

/** What a test or a debugger can read back from an element: never shown. */
export interface TreeElementData {
  readonly role: "trunk" | "edge" | "card" | "ring" | "letter" | "name" | "summary" | "status" | "text" | "accept" | "dot" | "fork" | "preview";
  readonly node?: string;
  readonly edge?: LayoutEdge["kind"];
  readonly level: ZoomLevel;
}

const LINE_HEIGHT = 1.2;

const WIDE = /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6]/u;
/** A generous estimate of a line's width: a clipped name must never make Excalidraw wrap. */
export function textWidth(value: string, size: number): number {
  let width = 0;
  for (const char of value) width += WIDE.test(char) ? size : /[A-Z0-9@#%&MWmw]/.test(char) ? size * 0.68 : char === " " ? size * 0.32 : size * 0.56;
  return width;
}

/** One line that fits, ending in an ellipsis when it had to be cut. */
export function clip(value: string, size: number, width: number): string {
  const text = value.trim();
  if (textWidth(text, size) <= width) return text;
  let out = "";
  for (const char of text) {
    if (textWidth(`${out}${char}…`, size) > width) break;
    out += char;
  }
  return `${out.trimEnd()}…`;
}

const TOKEN = /[\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6]|[^\s\u1100-\u115F\u2E80-\uA4CF\uAC00-\uD7A3\uF900-\uFAFF\uFE30-\uFE4F\uFF00-\uFF60\uFFE0-\uFFE6]+|\s+/gu;

/** Up to `lines` lines: words for Latin text, characters for CJK; what does not fit ends in an ellipsis. */
export function wrap(value: string, size: number, width: number, lines: number): string[] {
  const tokens = value.trim().replace(/\s+/g, " ").match(TOKEN) ?? [];
  const out: string[] = [];
  let line = "", index = 0;
  for (; index < tokens.length; index += 1) {
    const next = line + tokens[index];
    if (!line.trim() || textWidth(next.trimEnd(), size) <= width) { line = next; continue; }
    out.push(line.trimEnd());
    if (out.length === lines) break;
    line = tokens[index].trim() ? tokens[index] : "";
  }
  if (index >= tokens.length) {
    if (line.trim()) out.push(line.trimEnd());
    return out.map((row) => clip(row, size, width));
  }
  // More text than lines: the last line says so.
  let last = out[out.length - 1];
  while (last && textWidth(`${last}…`, size) > width) last = [...last].slice(0, -1).join("");
  out[out.length - 1] = `${last.trimEnd()}…`;
  return out;
}

type Skeleton = ExcalidrawElementSkeleton;
type Style = Partial<{ strokeColor: string; backgroundColor: string; strokeWidth: number; strokeStyle: "solid" | "dashed" | "dotted"; opacity: number }>;

/** Inside a card: the status bar at its left edge, then the words. */
const BAR = { inset: 6, width: 3.5 };
const PAD = { left: 16, right: 10, top: 6 };
/** The review mark in an option card's top-right corner. */
const MARK = { inset: 14, top: 5, size: 14 };
/** Where a close card shows its preview: beside an option's bar, and at Current's top right. */
const PICTURE = { option: 80, current: 88, gap: 8 };
/** The pixels a card's image is drawn at: about twice its largest slot (#409), so it stays sharp up close and small in memory. */
export const PREVIEW_PIXELS = { width: 2 * PICTURE.current, height: 2 * 62 };

export function buildTreeScene(tree: GrowthTree, layout: GrowthLayout, options: SceneOptions): TreeScene {
  const { level, words, fontFamily } = options;
  const { ground: GROUND, ink: INK, ink2: INK_2, faint: FAINT, accent: ACCENT, onAccent: ON_ACCENT, accentSoft: ACCENT_SOFT, paper: PAPER,
    twig: TWIG, muted: MUTED, running: RUNNING, held: HELD, violated: VIOLATED, unchecked: UNCHECKED } = options.palette ?? LIGHT_TREE_PALETTE;
  const k = options.textScale;
  const skeletons: Skeleton[] = [];
  const hits: SceneHit[] = [];
  const data = (role: TreeElementData["role"], extra: Omit<TreeElementData, "role" | "level"> = {}): { tree: TreeElementData } =>
    ({ tree: { role, level, ...extra } });

  const rect = (id: string, box: Box, style: Style, custom: { tree: TreeElementData }, rounded = true) => skeletons.push({
    type: "rectangle", id, x: box.x, y: box.y, width: box.width, height: box.height, roughness: 0, fillStyle: "solid",
    strokeColor: INK, backgroundColor: "transparent", strokeWidth: 1, strokeStyle: "solid", opacity: 100,
    ...(rounded ? { roundness: { type: 3 } } : {}), ...style, customData: custom,
  } as unknown as Skeleton);
  const ellipse = (id: string, box: Box, style: Style, custom: { tree: TreeElementData }) => skeletons.push({
    type: "ellipse", id, x: box.x, y: box.y, width: box.width, height: box.height, roughness: 0, fillStyle: "solid",
    strokeColor: INK, backgroundColor: "transparent", strokeWidth: 1, strokeStyle: "solid", opacity: 100, ...style, customData: custom,
  } as unknown as Skeleton);
  const polyline = (id: string, points: readonly (readonly [number, number])[], style: Style, custom: { tree: TreeElementData }) => {
    const [x0, y0] = points[0];
    const xs = points.map(([x]) => x), ys = points.map(([, y]) => y);
    skeletons.push({
      type: "line", id, x: x0, y: y0, width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys),
      points: points.map(([x, y]) => [x - x0, y - y0]), roughness: 0, strokeColor: INK, strokeWidth: 1, strokeStyle: "solid",
      opacity: 100, ...style, customData: custom,
    } as unknown as Skeleton);
  };
  const text = (id: string, x: number, y: number, value: string, size: number, color: string, custom: { tree: TreeElementData }, opacity = 100) => {
    if (!value) return;
    skeletons.push({ type: "text", id, x, y, text: value, fontSize: size, fontFamily, strokeColor: color, textAlign: "left",
      verticalAlign: "top", opacity, customData: custom } as unknown as Skeleton);
  };
  /** The image, as large as it fits in the slot and centred in it; a slot never stretches it. */
  const picture = (id: string, slot: Box, preview: ScenePreview, opacity: number) => {
    const scale = Math.min(slot.width / Math.max(1, preview.width), slot.height / Math.max(1, preview.height));
    const width = preview.width * scale, height = preview.height * scale;
    skeletons.push({ type: "image", id: `${id}:preview`, fileId: preview.fileId, status: "saved", x: slot.x + (slot.width - width) / 2,
      y: slot.y + (slot.height - height) / 2, width, height, opacity, customData: data("preview", { node: id }) } as unknown as Skeleton);
  };
  /** A small label on a picture's corner, so the image does not hide the words it replaces. */
  const tag = (id: string, x: number, y: number, value: string, size: number, fill: string, color: string, role: TreeElementData["role"], opacity: number) => {
    rect(`${id}:tag`, { x, y, width: textWidth(value, size) + 8, height: size * LINE_HEIGHT + 2 }, { backgroundColor: fill, strokeColor: "transparent", opacity },
      data("preview", { node: id }), false);
    text(`${id}:${role}`, x + 4, y + 1, value, size, color, data(role, { node: id }), opacity);
  };
  const ring = (id: string, box: Box, pad: number) => rect(`${id}:ring`, { x: box.x - pad, y: box.y - pad, width: box.width + pad * 2, height: box.height + pad * 2 },
    { strokeColor: ACCENT, strokeWidth: 2.5 }, data("ring", { node: id }));
  /** The status bar's colour: review checks as retained, work still running, or set aside in review. */
  const barColour = (node: TreeNode): string | null => {
    const disposition = node.candidate?.review?.disposition;
    if (disposition === "rejected" || disposition === "archived") return MUTED;
    const check = checkOf(node);
    return check === "held" ? HELD : check === "violated" ? VIOLATED : check === "unchecked" ? UNCHECKED : check === "running" ? RUNNING : null;
  };

  // Edges first, so every card sits on its lines.
  const far = level === "far";
  const weight = far ? Math.min(k, 8) : 1;
  if (layout.trunk.length > 1) polyline("trunk", layout.trunk, { strokeColor: ACCENT, strokeWidth: 4 * weight }, data("trunk"));
  layout.edges.forEach((edge, index) => {
    // An option's elbow is a thin solid line: a stack's elbows nest into one bracket beside it.
    const style: Style = edge.kind === "twig" ? { strokeColor: TWIG, strokeWidth: 1.25 * weight, strokeStyle: "solid" }
      : edge.kind === "pending" ? { strokeColor: RUNNING, strokeWidth: 1.5 * weight, strokeStyle: "dotted" }
        : edge.kind === "branch" ? { strokeColor: MUTED, strokeWidth: 1.5 * weight, strokeStyle: "dashed", opacity: 60 }
          : { strokeColor: MUTED, strokeWidth: 1.25 * weight, strokeStyle: "dashed", opacity: 45 };
    polyline(`edge:${index}:${edge.to}`, edge.points, style, data("edge", { node: edge.to, edge: edge.kind }));
  });

  for (const placed of layout.nodes.values()) {
    const node = tree.nodes.get(placed.id)!;
    if (far) drawFar(node, placed);
    else drawNear(node, placed);
  }
  if (far) {
    // Counts sit above the trunk, where nothing else is drawn (every option hangs below it), clear of
    // Current's pill and the Stage marks, each up to the next point that has its own.
    const trunkForks = layout.forks.filter((fork) => !fork.muted).sort((a, b) => a.x - b.x);
    const lift = (Math.min(13 * k, 72) * LINE_HEIGHT + 12 * k) / 2 + 4 * k;
    trunkForks.forEach((fork, index) => {
      const size = Math.min(11 * k, 64);
      const stop = trunkForks[index + 1]?.x ?? fork.x + fork.region.width + 600 * k;
      const limit = stop - fork.x - 12 * k;
      const variants = words.fork(fork);
      const label = variants.find((variant) => textWidth(variant, size) <= limit) ?? clip(variants.at(-1) ?? "", size, limit);
      if (!label) return;
      const y = fork.y - lift - size * LINE_HEIGHT;
      text(`fork:${fork.node}`, fork.x + 6 * k, y, label, size, INK_2, data("fork", { node: fork.node }));
      hits.push({ x: fork.x, y, width: textWidth(label, size) + 12 * k, height: size * LINE_HEIGHT, node: fork.node, action: "zoom", zoomTo: fork.region });
    });
  }
  return { skeletons, hits };

  function drawFar(node: TreeNode, placed: PlacedNode) {
    const cx = placed.card.x + placed.card.width / 2, cy = placed.y;
    const opacity = placed.muted ? 45 : 100;
    if (node.kind === "stage" || node.kind === "origin") {
      const mark = Math.min(18 * k, 72);
      const box = { x: cx - mark / 2, y: cy - mark / 2, width: mark, height: mark };
      rect(`${node.id}:card`, box, { backgroundColor: INK, strokeColor: placed.role === "trunk" ? ACCENT : INK, strokeWidth: 2 * Math.min(k, 6), opacity },
        data("card", { node: node.id }));
      // Under the mark, where nothing hangs: a column's header names its Stage in full.
      const size = Math.min(13 * k, 72);
      const label = node.kind === "stage" ? `S${node.stage!.number}` : clip(words.origin, size, placed.card.width);
      const width = textWidth(label, size), y = cy + mark / 2 + 6 * k;
      text(`${node.id}:name`, cx - width / 2, y, label, size, INK, data("name", { node: node.id }), opacity);
      if (options.selected === node.id) ring(node.id, box, 4 * k);
      hits.push({ x: Math.min(cx - width / 2, box.x), y: box.y, width: Math.max(width, mark), height: y + size * LINE_HEIGHT - box.y, node: node.id, action: "select" });
      return;
    }
    if (node.kind === "current") {
      const size = Math.min(13 * k, 72);
      const width = textWidth(words.current, size) + 24 * k, height = size * LINE_HEIGHT + 12 * k;
      const box = { x: cx - width / 2, y: cy - height / 2, width, height };
      rect(`${node.id}:card`, box, { backgroundColor: ACCENT, strokeColor: ACCENT, strokeWidth: 2 * Math.min(k, 6) }, data("card", { node: node.id }));
      text(`${node.id}:name`, box.x + 12 * k, box.y + 6 * k, words.current, size, ON_ACCENT, data("name", { node: node.id }));
      if (options.selected === node.id) ring(node.id, box, 4 * k);
      hits.push({ ...box, node: node.id, action: "select" });
      return;
    }
    const diameter = Math.min(16 * k, 72);
    const box = { x: cx - diameter / 2, y: cy - diameter / 2, width: diameter, height: diameter };
    // #575: a fold is an open ring on the trunk, or a quiet one where drafts were folded; a click opens it. A drafts
    // fold that holds cleaned drafts opens its inspector instead, which lists them with Restore.
    const expand = node.kind === "fold" ? "steps" as const : node.kind === "drafts" ? "drafts" as const : null;
    const inspect = holdsCleaned(node);
    const style: Style = node.kind === "pending"
      ? { strokeColor: RUNNING, strokeStyle: "dashed", strokeWidth: 2 * Math.min(k, 6), opacity }
      : expand ? { backgroundColor: PAPER, strokeColor: expand === "steps" ? ACCENT : MUTED, strokeStyle: "dashed", strokeWidth: 2 * Math.min(k, 6), opacity }
        : { backgroundColor: placed.role === "trunk" ? ACCENT : INK_2, strokeColor: placed.role === "trunk" ? ACCENT : INK_2, opacity };
    ellipse(`${node.id}:dot`, box, style, data("dot", { node: node.id }));
    if (options.selected === node.id) ring(node.id, box, 4 * k);
    const pad = Math.max(diameter, 24 * k);
    hits.push({ x: cx - pad / 2, y: cy - pad / 2, width: pad, height: pad, node: node.id,
      ...(expand && !inspect ? { action: "expand", expand } : { action: "select" }) });
  }

  function drawNear(node: TreeNode, placed: PlacedNode) {
    const card = placed.card;
    const opacity = placed.muted ? 45 : 100;
    const selected = options.selected === node.id;
    const trunk = placed.role === "trunk";
    const s = Math.min(k, 1.5);
    const close = level === "close";
    const preview = close ? options.previews?.get(node.id) : undefined;
    if (node.kind === "stage" || node.kind === "origin") {
      const stage = node.kind === "stage";
      rect(`${node.id}:card`, card, { backgroundColor: stage ? INK : PAPER, strokeColor: trunk ? ACCENT : INK, strokeWidth: trunk ? 2.5 : 1.5, opacity },
        data("card", { node: node.id }));
      if (stage && preview) {
        // The accepted model fills its milestone; its number stays on the corner.
        picture(node.id, { x: card.x + 3, y: card.y + 3, width: card.width - 6, height: card.height - 6 }, preview, opacity);
        tag(node.id, card.x + 3, card.y + 3, `S${node.stage!.number}`, 11, INK, PAPER, "name", opacity);
        if (selected) ring(node.id, card, 6);
        hits.push({ ...card, node: node.id, action: "select" });
        return;
      }
      // A Stage is a milestone on its line; the header of the column it opens names it in full.
      const size = 14 * s;
      const label = clip(stage ? `S${node.stage!.number}` : words.origin, size, card.width - 20);
      text(`${node.id}:name`, stage ? card.x + (card.width - textWidth(label, size)) / 2 : card.x + 12, placed.y - (size * LINE_HEIGHT) / 2, label, size,
        stage ? PAPER : INK, data("name", { node: node.id }), opacity);
      if (selected) ring(node.id, card, 6);
      hits.push({ ...card, node: node.id, action: "select" });
      return;
    }
    if (node.kind === "current") {
      rect(`${node.id}:card`, card, { backgroundColor: ACCENT_SOFT, strokeColor: ACCENT, strokeWidth: 2.5 }, data("card", { node: node.id }));
      const title = 15 * Math.min(k, 1.6), sub = 12 * s, button = 12 * Math.min(k, 1.4);
      text(`${node.id}:name`, card.x + 14, card.y + 9, words.current, title, ACCENT, data("name", { node: node.id }));
      const box = { x: card.x + 12, y: card.y + card.height - 11 - Math.max(30, button * LINE_HEIGHT + 12), width: card.width - 24, height: Math.max(30, button * LINE_HEIGHT + 12) };
      const aside = preview ? PICTURE.current + PICTURE.gap : 0;
      if (preview) {
        const slot = { x: card.x + card.width - 12 - PICTURE.current, y: card.y + 9, width: PICTURE.current, height: box.y - 6 - (card.y + 9) };
        picture(node.id, slot, preview, 100);
      }
      // Where Current stands takes a second line when the card has room above its button: a long version name keeps its end.
      const summaryTop = card.y + 12 + title * LINE_HEIGHT;
      const lines = Math.max(1, Math.min(2, Math.floor((box.y - 4 - summaryTop) / (sub * LINE_HEIGHT))));
      text(`${node.id}:summary`, card.x + 14, summaryTop, wrap(words.currentAt, sub, card.width - 28 - aside, lines).join("\n"), sub, INK,
        data("summary", { node: node.id }));
      const allowed = tree.accept.allowed;
      rect(`${node.id}:accept`, box, allowed ? { backgroundColor: ACCENT, strokeColor: ACCENT, strokeWidth: 1.5 } : { strokeColor: MUTED, strokeStyle: "dashed", strokeWidth: 1.5 },
        data("accept", { node: node.id }));
      const label = clip(allowed ? words.accept : words.acceptBlocked, button, box.width - 20);
      text(`${node.id}:accept-label`, box.x + 10, box.y + (box.height - button * LINE_HEIGHT) / 2, label, button, allowed ? ON_ACCENT : FAINT, data("accept", { node: node.id }));
      if (selected) ring(node.id, card, 6);
      hits.push({ ...card, node: node.id, action: "select" });
      hits.push({ ...box, node: node.id, action: "accept" });
      return;
    }
    if (node.kind === "fold" || node.kind === "drafts") {
      // #575: a fold is a control, not a run: a dashed card naming what it holds, which a click opens.
      const steps = node.kind === "fold";
      rect(`${node.id}:card`, card, { backgroundColor: PAPER, strokeColor: steps ? ACCENT : TWIG, strokeStyle: "dashed", strokeWidth: steps ? 2 : 1.5,
        opacity }, data("card", { node: node.id }));
      const nameSize = Math.min(13 * s, 16), small = Math.min(11 * s, 13), width = card.width - 24, note = words.status(node);
      const rows: [string, number, string, "name" | "status"][] = [[clip(words.name(node), nameSize, width), nameSize, steps ? ACCENT : INK_2, "name"]];
      if (note && (nameSize + small) * LINE_HEIGHT <= card.height - 12) rows.push([clip(note, small, width), small, FAINT, "status"]);
      let y = placed.y - rows.reduce((sum, [, size]) => sum + size * LINE_HEIGHT, 0) / 2;
      for (const [value, size, color, role] of rows) {
        text(`${node.id}:${role}`, card.x + 12, y, value, size, color, data(role, { node: node.id }), opacity);
        y += size * LINE_HEIGHT;
      }
      if (selected) ring(node.id, card, 5);
      hits.push({ ...card, node: node.id, ...(holdsCleaned(node) ? { action: "select" as const }
        : { action: "expand" as const, expand: steps ? "steps" as const : "drafts" as const }) });
      return;
    }
    // An option, a step of Current's line, a superseded draft or a running line: a small card with its status bar, letter and name.
    const pending = node.kind === "pending";
    rect(`${node.id}:card`, card, pending
      ? { strokeColor: RUNNING, strokeStyle: "dotted", strokeWidth: 1.5, opacity }
      : { backgroundColor: PAPER, strokeColor: trunk ? ACCENT : TWIG, strokeWidth: trunk ? 2.5 : 1.5, strokeStyle: placed.muted ? "dashed" : "solid", opacity },
      data("card", { node: node.id }));
    const bar = barColour(node);
    if (bar) rect(`${node.id}:bar`, { x: card.x + BAR.inset, y: card.y + BAR.inset + 2, width: BAR.width, height: card.height - (BAR.inset + 2) * 2 },
      { backgroundColor: bar, strokeColor: "transparent", opacity }, data("status", { node: node.id }), false);
    // Admitted for comparison although review checks did not pass (#294 Q2): a mark beside the bar's colour,
    // explained in the inspector, in the card's top-right corner.
    const marked = Boolean(node.candidate?.blockedBy.length);
    if (marked) text(`${node.id}:review`, card.x + card.width - MARK.inset, card.y + MARK.top, "!", MARK.size, VIOLATED, data("status", { node: node.id }), opacity);
    const nameSize = close ? 13 : 12 * k, small = close ? 11.5 : 11 * s;
    // With a preview the image stands beside the bar and carries the letter on its corner; the words take the rest.
    const slot = preview && !pending ? { x: card.x + BAR.inset + BAR.width + 5, y: card.y + 6, width: PICTURE.option, height: card.height - 12 } : null;
    if (slot && preview) {
      rect(`${node.id}:slot`, slot, { backgroundColor: GROUND, strokeColor: "transparent", opacity }, data("preview", { node: node.id }), false);
      picture(node.id, slot, preview, opacity);
      if (node.letter) tag(node.id, slot.x, slot.y, node.letter, 11, PAPER, trunk ? ACCENT : INK_2, "letter", opacity);
    }
    // The letter keeps a column of its own; a lone option has none and its name takes the width.
    const letter = pending || slot ? null : node.letter;
    const letterSize = nameSize * 1.2;
    const left = slot ? slot.x + slot.width + PICTURE.gap : card.x + PAD.left + (letter ? letterSize * 0.72 + 6 : 0);
    const width = card.x + card.width - PAD.right - left;
    const rows: { role: "letter" | "name" | "summary" | "status"; value: string; size: number; color: string }[] = [];
    if (pending) rows.push({ role: "letter", value: clip(words.pending(node.pending!.status), small, width), size: small, color: FAINT });
    // A name takes two lines where the card has the room for them. Only its first line can reach the review
    // mark's corner; one long enough to is wrapped short of it.
    const room = card.height - PAD.top * 2 - rows.reduce((sum, row) => sum + row.size * LINE_HEIGHT, 0);
    const lines = (!close || slot) && 2 * nameSize * LINE_HEIGHT <= room - (slot ? small * LINE_HEIGHT : 0) ? 2 : 1;
    const clearOfMark = card.x + card.width - MARK.inset - 4 - left;
    let names = wrap(words.name(node), nameSize, width, lines);
    if (marked && names.length > 0 && textWidth(names[0], nameSize) > clearOfMark) names = wrap(words.name(node), nameSize, clearOfMark, lines);
    for (const value of names) rows.push({ role: "name", value, size: nameSize, color: INK });
    if (close) {
      // A running line's summary is its progress detail.
      if (node.summary && !slot) rows.push({ role: "summary", value: clip(node.summary, small, width), size: small, color: INK_2 });
      // A step no words name is called by its place on the line, which its status says too: it is said once.
      if (!pending && words.status(node) !== words.name(node)) rows.push({ role: "status", value: clip(words.status(node), small, width), size: small, color: FAINT });
    }
    const height = rows.reduce((sum, row) => sum + row.size * LINE_HEIGHT, 0);
    let y = placed.y - height / 2;
    rows.forEach((row, index) => {
      text(`${node.id}:${row.role}:${index}`, left, y, row.value, row.size, row.color, data(row.role, { node: node.id }), opacity);
      y += row.size * LINE_HEIGHT;
    });
    if (letter) {
      text(`${node.id}:letter`, card.x + PAD.left, placed.y - (letterSize * LINE_HEIGHT) / 2, letter, letterSize, trunk ? ACCENT : INK_2,
        data("letter", { node: node.id }), opacity);
    }
    if (selected) ring(node.id, card, 5);
    hits.push({ ...placed.footprint, node: node.id, action: "select" });
  }
}
