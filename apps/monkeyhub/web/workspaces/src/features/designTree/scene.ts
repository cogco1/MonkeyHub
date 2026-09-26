/**
 * The growth tree as Excalidraw element skeletons, one level of detail at a time.
 *
 * The caller passes the skeletons through `convertToExcalidrawElements`, the
 * way MonkeyBoard places its pages. Semantic zoom only changes what is drawn,
 * never where: far shows the trunk, Stage milestones and counts; middle adds
 * option cards with their letter and short name; close adds a one-line
 * summary and a status. Author, time and runs belong to the side card.
 * Colours come from a palette the canvas reads from the Hub's tokens (#337), so
 * the tree follows the theme and interface style and is never inverted.
 * Every hit target is produced with the element it stands for, because view
 * mode leaves clicks to the host (`useScenePointer` + `topmostAt`).
 */
import type { ExcalidrawElementSkeleton } from "@excalidraw/excalidraw/data/transform";
import type { SceneBox } from "../canvas/sceneHit";
import type { Box, Fork, GrowthLayout, LayoutEdge, PlacedNode } from "./layout";
import { CURRENT, type GrowthTree, type PendingStatus, type TreeNode } from "./model";

export type ZoomLevel = "far" | "mid" | "close";

/** The words a scene needs, already in the viewer's language. */
export interface SceneWords {
  current: string;
  origin: string;
  stage(node: TreeNode): string;
  option(node: TreeNode): string;
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
  /** The inset behind an option's letter. */
  readonly tile: string;
  /** Option lines and option card edges. */
  readonly twig: string;
  /** Branches left behind, and what cannot be done now. */
  readonly muted: string;
  /** Work still running. */
  readonly running: string;
  /** Review checks still open. */
  readonly warn: string;
}

/** The light theme's colours, for a test or a canvas that cannot read the Hub's tokens. */
export const LIGHT_TREE_PALETTE: TreePalette = {
  ground: "#f4f5f0", ink: "#29352d", ink2: "#41414a", faint: "#6b716a", accent: "#356b9e", onAccent: "#ffffff", accentSoft: "#e8eff6",
  paper: "#ffffff", tile: "#eceee8", twig: "#7b837a", muted: "#9ea39c", running: "#8d948c", warn: "#a86a12",
};

export interface SceneHit extends SceneBox {
  readonly node: string;
  readonly action: "select" | "accept" | "zoom";
  readonly zoomTo?: Box;
}

export interface TreeScene {
  readonly skeletons: ExcalidrawElementSkeleton[];
  readonly hits: SceneHit[];
}

/** What a test or a debugger can read back from an element: never shown. */
export interface TreeElementData {
  readonly role: "trunk" | "edge" | "card" | "ring" | "letter" | "name" | "summary" | "status" | "text" | "accept" | "dot" | "fork";
  readonly node?: string;
  readonly edge?: LayoutEdge["kind"];
  readonly level: ZoomLevel;
}

const LINE_HEIGHT = 1.2;

const WIDE = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/u;
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

const TOKEN = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]|[^\sᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]+|\s+/gu;

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

export function buildTreeScene(tree: GrowthTree, layout: GrowthLayout, options: SceneOptions): TreeScene {
  const { level, words, fontFamily } = options;
  const { ink: INK, ink2: INK_2, faint: FAINT, accent: ACCENT, onAccent: ON_ACCENT, accentSoft: ACCENT_SOFT, paper: PAPER, tile: TILE,
    twig: TWIG, muted: MUTED, running: RUNNING, warn: WARN } = options.palette ?? LIGHT_TREE_PALETTE;
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
  const ring = (id: string, box: Box, pad: number) => rect(`${id}:ring`, { x: box.x - pad, y: box.y - pad, width: box.width + pad * 2, height: box.height + pad * 2 },
    { strokeColor: ACCENT, strokeWidth: 2.5 }, data("ring", { node: id }));

  // Edges first, so every card sits on its lines.
  const far = level === "far";
  const weight = far ? Math.min(k, 8) : 1;
  if (layout.trunk.length > 1) polyline("trunk", layout.trunk, { strokeColor: ACCENT, strokeWidth: 4 * weight }, data("trunk"));
  layout.edges.forEach((edge, index) => {
    const style: Style = edge.kind === "twig" ? { strokeColor: TWIG, strokeWidth: 1.5 * weight, strokeStyle: "dashed" }
      : edge.kind === "pending" ? { strokeColor: RUNNING, strokeWidth: 1.5 * weight, strokeStyle: "dotted" }
        : edge.kind === "branch" ? { strokeColor: MUTED, strokeWidth: 1.5 * weight, strokeStyle: "dashed", opacity: 60 }
          : { strokeColor: MUTED, strokeWidth: 1.25 * weight, strokeStyle: "dashed", opacity: 45 };
    polyline(`edge:${index}:${edge.to}`, edge.points, style, data("edge", { node: edge.to, edge: edge.kind }));
  });

  const trunkIndex = new Map(tree.trunk.map((id, index) => [id, index]));
  const nextTrunkX = (placed: PlacedNode) => {
    const index = trunkIndex.get(placed.id);
    const next = index === undefined ? undefined : tree.trunk[index + 1];
    return next ? layout.nodes.get(next)!.x : placed.x + placed.footprint.width * 3;
  };

  for (const placed of layout.nodes.values()) {
    const node = tree.nodes.get(placed.id)!;
    if (far) drawFar(node, placed);
    else drawNear(node, placed);
  }
  if (far) {
    // Counts sit under the trunk, each up to the next point that has its own
    // and never under Current's pill.
    const trunkForks = layout.forks.filter((fork) => !fork.muted).sort((a, b) => a.x - b.x);
    const tip = layout.nodes.get(CURRENT);
    const tipLeft = tip ? tip.card.x + tip.card.width / 2 - (textWidth(words.current, Math.min(13 * k, 72)) + 24 * k) / 2 : Infinity;
    trunkForks.forEach((fork, index) => {
      const size = Math.min(11 * k, 64);
      const stop = Math.min(trunkForks[index + 1]?.x ?? Infinity, tipLeft > fork.x ? tipLeft : Infinity);
      const limit = (Number.isFinite(stop) ? stop : fork.x + fork.region.width + 600 * k) - fork.x - 12 * k;
      const variants = words.fork(fork);
      const label = variants.find((variant) => textWidth(variant, size) <= limit) ?? clip(variants.at(-1) ?? "", size, limit);
      if (!label) return;
      const y = fork.y + Math.min(12 * k, 30);
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
      rect(`${node.id}:card`, { x: cx - mark / 2, y: cy - mark / 2, width: mark, height: mark }, { backgroundColor: INK, strokeColor: placed.role === "trunk" ? ACCENT : INK, strokeWidth: 2 * Math.min(k, 6), opacity }, data("card", { node: node.id }));
      const size = Math.min((placed.muted ? 10 : 13) * k, 72);
      const limit = placed.side === 0 ? Math.max(size * 3, nextTrunkX(placed) - placed.x - 8 * k) : GEOMETRY_FALLBACK * k;
      const label = clip(node.kind === "stage" ? words.stage(node) : words.origin, size, limit);
      const y = cy - mark / 2 - size * LINE_HEIGHT - 6 * k;
      text(`${node.id}:name`, placed.x, y, label, size, INK, data("name", { node: node.id }), opacity);
      if (options.selected === node.id) ring(node.id, { x: cx - mark / 2, y: cy - mark / 2, width: mark, height: mark }, 4 * k);
      hits.push({ x: Math.min(placed.x, cx - mark / 2), y, width: Math.max(textWidth(label, size), mark), height: cy + mark / 2 - y, node: node.id, action: "select" });
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
    const style: Style = node.kind === "pending"
      ? { strokeColor: RUNNING, strokeStyle: "dashed", strokeWidth: 2 * Math.min(k, 6), opacity }
      : { backgroundColor: placed.role === "trunk" ? ACCENT : INK_2, strokeColor: placed.role === "trunk" ? ACCENT : INK_2, opacity };
    ellipse(`${node.id}:dot`, box, style, data("dot", { node: node.id }));
    if (options.selected === node.id) ring(node.id, box, 4 * k);
    const pad = Math.max(diameter, 24 * k);
    hits.push({ x: cx - pad / 2, y: cy - pad / 2, width: pad, height: pad, node: node.id, action: "select" });
  }

  function drawNear(node: TreeNode, placed: PlacedNode) {
    const card = placed.card;
    const opacity = placed.muted ? 45 : 100;
    const selected = options.selected === node.id;
    const trunk = placed.role === "trunk";
    const s = Math.min(k, 1.5);
    if (node.kind === "stage" || node.kind === "origin") {
      const stage = node.kind === "stage";
      rect(`${node.id}:card`, card, { backgroundColor: stage ? INK : PAPER, strokeColor: trunk ? ACCENT : INK, strokeWidth: trunk ? 2.5 : 1.5, opacity },
        data("card", { node: node.id }));
      const size = 14 * s;
      const label = clip(stage ? words.stage(node) : words.origin, size, card.width - 24);
      text(`${node.id}:name`, card.x + 12, placed.y - (size * LINE_HEIGHT) / 2, label, size, stage ? PAPER : INK, data("name", { node: node.id }), opacity);
      if (selected) ring(node.id, card, 6);
      hits.push({ ...card, node: node.id, action: "select" });
      return;
    }
    if (node.kind === "current") {
      rect(`${node.id}:card`, card, { backgroundColor: ACCENT_SOFT, strokeColor: ACCENT, strokeWidth: 2.5 }, data("card", { node: node.id }));
      const title = 15 * Math.min(k, 1.6), sub = 12 * s, button = 12 * Math.min(k, 1.4);
      text(`${node.id}:name`, card.x + 14, card.y + 10, words.current, title, ACCENT, data("name", { node: node.id }));
      text(`${node.id}:summary`, card.x + 14, card.y + 14 + title * LINE_HEIGHT, clip(words.currentAt, sub, card.width - 28), sub, INK, data("summary", { node: node.id }));
      const box = { x: card.x + 12, y: card.y + card.height - 12 - Math.max(30, button * LINE_HEIGHT + 12), width: card.width - 24, height: Math.max(30, button * LINE_HEIGHT + 12) };
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
    const pending = node.kind === "pending";
    const onLine = trunk || tree.continued.has(node.id);
    rect(`${node.id}:card`, card, pending
      ? { strokeColor: RUNNING, strokeStyle: "dotted", strokeWidth: 1.5, opacity }
      : { backgroundColor: PAPER, strokeColor: trunk ? ACCENT : TWIG, strokeWidth: trunk ? 2.5 : 1.5, strokeStyle: placed.muted ? "dashed" : "solid", opacity },
      data("card", { node: node.id }));
    if (pending) {
      const size = 11 * s;
      const rows = wrap(words.pending(node.pending!.status), size, card.width - 14, 2);
      text(`${node.id}:letter`, card.x + 7, placed.y - (rows.length * size * LINE_HEIGHT) / 2, rows.join("\n"), size, FAINT, data("letter", { node: node.id }), opacity);
    } else {
      rect(`${node.id}:tile`, { x: card.x + 6, y: card.y + 6, width: card.width - 12, height: card.height - 12 }, { backgroundColor: TILE, strokeColor: "transparent", opacity },
        data("card", { node: node.id }), false);
      const letter = node.letter ?? "·", size = 26;
      text(`${node.id}:letter`, card.x + card.width / 2 - textWidth(letter, size) / 2, placed.y - (size * LINE_HEIGHT) / 2, letter, size, trunk ? ACCENT : FAINT,
        data("letter", { node: node.id }), opacity);
      ellipse(`${node.id}:state`, { x: card.x + card.width - 15, y: card.y + 5, width: 10, height: 10 },
        onLine ? { backgroundColor: trunk ? ACCENT : PAPER, strokeColor: trunk ? ACCENT : TWIG, strokeWidth: 1.5, opacity } : { backgroundColor: MUTED, strokeColor: MUTED, opacity },
        data("status", { node: node.id }));
      // Admitted for comparison with review checks still open (#294 Q2): a small mark, explained in the side card.
      if (node.candidate?.blockedBy.length) {
        text(`${node.id}:review`, card.x + 10, card.y + 6, "!", 14, WARN, data("status", { node: node.id }), opacity);
      }
    }
    const labels = placed.labels!;
    const close = level === "close";
    const nameSize = close ? 13 : 12 * k;
    const summarySize = 11.5;
    const nameRows = wrap(words.option(node), nameSize, labels.width, close ? 1 : 2);
    const rows: { role: "name" | "summary" | "status"; value: string; size: number; color: string }[] =
      nameRows.map((value) => ({ role: "name", value, size: nameSize, color: INK }));
    if (close) {
      // A running line's summary is its progress detail.
      if (node.summary) rows.push({ role: "summary", value: clip(node.summary, summarySize, labels.width), size: summarySize, color: INK_2 });
      rows.push({ role: "status", value: clip(words.status(node), summarySize, labels.width), size: summarySize, color: FAINT });
    }
    const height = rows.reduce((sum, row) => sum + row.size * LINE_HEIGHT, 0);
    let y = placed.side < 0 ? labels.y + labels.height - height - 5 : labels.y + 5;
    rows.forEach((row, index) => {
      text(`${node.id}:${row.role}:${index}`, labels.x, y, row.value, row.size, row.color, data(row.role, { node: node.id }), opacity);
      y += row.size * LINE_HEIGHT;
    });
    if (selected) ring(node.id, card, 5);
    hits.push({ ...placed.footprint, node: node.id, action: "select" });
  }
}

/** Width allowed for a branch Stage's far label, in units of the text scale. */
const GEOMETRY_FALLBACK = 180;
