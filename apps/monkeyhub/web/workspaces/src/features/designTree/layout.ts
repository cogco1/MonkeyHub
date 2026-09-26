/**
 * A planar left-to-right drawing of the growth tree, one column per Stage.
 *
 * The trunk runs along y = 0 through the Working Head's lineage, and each
 * Stage on it opens a column of its own that nothing drawn before it reaches
 * into, so a column's header names everything under it (#353). At each point
 * the trunk passes, the options not taken hang below it as a stack of cards,
 * level with the option that was chosen, whose Study comes first. Each leaves
 * on its own elbow, the farthest leftmost, so the elbows nest. A side branch
 * grows along its own row through whatever was continued there, and its own
 * options hang below that row in turn; the last point of a side row hands the
 * row on to its first option. A row's stacks stand side by side, each clear
 * of everything the row already hangs below itself, so no edge crosses
 * another and no footprint overlaps (a tree has E = V - 1). Positions never
 * depend on zoom; the scene only changes what it draws.
 * Grown from the #284 prototype (`docs/prototypes/candidate-graph/prototype.js`).
 */
import type { GrowthTree, TreeNodeKind } from "./model";

/** On the trunk, or below it. */
export type Side = 0 | 1;
export type NodeRole = "trunk" | "twig" | "option" | "branch";
export type EdgeKind = "twig" | "pending" | "branch" | "muted-twig";

export interface Box {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface PlacedNode {
  readonly id: string;
  readonly kind: TreeNodeKind;
  /** Left edge of the node's card; its centre line is y. */
  readonly x: number;
  readonly y: number;
  readonly side: Side;
  readonly role: NodeRole;
  /** On a line that is no longer the trunk: kept, but quiet. */
  readonly muted: boolean;
  readonly card: Box;
  /** What no other node may overlap: the card, which carries all of the node's words. */
  readonly footprint: Box;
  /** The Stage column it is drawn in, as an index into the layout's columns. */
  readonly column: number;
}

export interface LayoutEdge {
  readonly kind: EdgeKind;
  readonly from: string;
  readonly to: string;
  readonly points: readonly (readonly [number, number])[];
}

/** One point of the tree where options leave: what the far level counts. */
export interface Fork {
  readonly node: string;
  /** Where the first elbow leaves the row. */
  readonly x: number;
  readonly y: number;
  readonly side: Side;
  readonly muted: boolean;
  readonly studies: readonly string[];
  readonly options: number;
  readonly continued: number;
  readonly pending: number;
  /** Everything that grows from this point, for zooming to it. */
  readonly region: Box;
}

/** One Stage on the trunk and everything drawn for it: the band its header names, rule to rule. */
export interface StageColumn {
  /** The Stage that opens it, or the project start. */
  readonly node: string;
  readonly x: number;
  readonly width: number;
  /** Where its first card starts; the header lines up with it. */
  readonly start: number;
  readonly options: number;
  readonly pending: number;
}

export interface GrowthLayout {
  readonly nodes: ReadonlyMap<string, PlacedNode>;
  readonly edges: readonly LayoutEdge[];
  /** The trunk as one polyline through its nodes' centres, left to right. */
  readonly trunk: readonly (readonly [number, number])[];
  readonly forks: readonly Fork[];
  /** Left to right, one per Stage on the trunk; the first is the root's. */
  readonly columns: readonly StageColumn[];
  readonly bounds: Box;
}

/**
 * Scene units at 100 %. A Stage is a small milestone on its line: the header of
 * the column it opens names it in full.
 */
export const GEOMETRY = {
  card: { width: 192, height: 68 },
  stage: { width: 72, height: 44 },
  current: { width: 240, height: 108 },
  origin: { width: 160, height: 44 },
  /** Between cards that follow each other along a row. */
  gap: 32,
  /** From a card to the first elbow that leaves after it. */
  stemMargin: 12,
  /** Between neighbouring elbows of one stack. */
  stemStep: 6,
  /** The last straight run of an elbow into its card. */
  elbow: 14,
  /** Between a row's cards and the first card hung below them, and between stacked cards. */
  rowGap: 20,
  /** Between the last card of one Stage's column and the next Stage. */
  columnGap: 56,
} as const;

const visual = (kind: TreeNodeKind) => kind === "stage" ? GEOMETRY.stage : kind === "current" ? GEOMETRY.current
  : kind === "origin" ? GEOMETRY.origin : GEOMETRY.card;

export function layoutGrowthTree(tree: GrowthTree): GrowthLayout {
  const placed = new Map<string, PlacedNode>();
  const order: string[] = [];
  const edges: LayoutEdge[] = [];
  const trunk: [number, number][] = [];
  const forks: Fork[] = [];
  const opened: { node: string; start: number }[] = [];
  let column = 0, far = -Infinity;
  const kids = (id: string) => tree.children.get(id) ?? [];
  const kindOf = (id: string) => tree.nodes.get(id)!.kind;
  const weights = new Map<string, number>();
  const weightOf = (id: string): number => {
    const known = weights.get(id);
    if (known !== undefined) return known;
    weights.set(id, 0);
    const value = 1 + (kindOf(id) === "stage" ? 1000 : 0) + kids(id).reduce((sum, child) => sum + weightOf(child), 0);
    weights.set(id, value);
    return value;
  };
  // A side branch grows on through Stages and through options that were continued.
  const grows = (id: string) => kindOf(id) === "stage" || (kindOf(id) === "candidate" && kids(id).length > 0);
  const chainFrom = (id: string): string[] => {
    const path = [id], seen = new Set([id]);
    for (let cursor = id; ;) {
      const next = kids(cursor).filter((child) => !seen.has(child) && !tree.onTrunk.has(child) && grows(child));
      if (!next.length) break;
      next.sort((a, b) => weightOf(b) - weightOf(a));
      cursor = next[0];
      seen.add(cursor);
      path.push(cursor);
    }
    return path;
  };
  // The options leaving one point, one group per Study; the chosen option's
  // Study comes first, nearest the row it continued.
  const sideGroups = (id: string, next: string | undefined) => {
    const groups: { study: string | null; members: string[] }[] = [];
    const byStudy = new Map<string, { study: string | null; members: string[] }>();
    for (const child of kids(id)) {
      if (child === next || tree.onTrunk.has(child)) continue;
      const study = tree.nodes.get(child)!.studyId;
      if (study) {
        let group = byStudy.get(study);
        if (!group) { group = { study, members: [] }; byStudy.set(study, group); groups.push(group); }
        group.members.push(child);
      } else groups.push({ study: null, members: [child] });
    }
    const nextStudy = next ? tree.nodes.get(next)?.studyId ?? null : null;
    if (nextStudy) {
      const index = groups.findIndex((group) => group.study === nextStudy);
      groups.unshift(index >= 0 ? groups.splice(index, 1)[0] : { study: nextStudy, members: [] });
    }
    return groups;
  };
  const boxOf = (ids: readonly string[]): Box => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of ids) {
      const box = placed.get(id)!.footprint;
      minX = Math.min(minX, box.x); minY = Math.min(minY, box.y);
      maxX = Math.max(maxX, box.x + box.width); maxY = Math.max(maxY, box.y + box.height);
    }
    return Number.isFinite(minX) ? { x: minX, y: minY, width: maxX - minX, height: maxY - minY } : { x: 0, y: 0, width: 0, height: 0 };
  };

  function place(id: string, x: number, y: number, side: Side, role: NodeRole, muted: boolean): Box {
    const size = visual(kindOf(id));
    const card = { x, y: y - size.height / 2, width: size.width, height: size.height };
    placed.set(id, { id, kind: kindOf(id), x, y, side, role, muted, card, footprint: card, column });
    order.push(id);
    far = Math.max(far, x + size.width);
    return card;
  }

  /**
   * One row: `path` along y from x0, each point's options hung below it. `clear` is the
   * right edge of whatever already hangs below the row, which the next elbows stay right
   * of. Returns the right edge of everything the row placed.
   */
  function lay(path: readonly string[], x0: number, y: number, side: Side, fromTrunk: boolean, clear = -Infinity): number {
    // A stack hangs below the tallest card its row carries from there to the end of the column, so no
    // later card on the row reaches into it: Current's column hangs lower than the others.
    const halfFrom = (index: number) => {
      if (side !== 0) return GEOMETRY.card.height / 2;
      let half = 0;
      for (let at = index; at < path.length && !(at > index && kindOf(path[at]) === "stage"); at += 1) half = Math.max(half, visual(kindOf(path[at])).height / 2);
      return half;
    };
    let x = x0, previous: Box | null = null, right = x0;
    path.forEach((id, index) => {
      if (side === 0 && index > 0 && kindOf(id) === "stage") {
        // A Stage on the trunk opens the next column, clear of everything drawn before it.
        x = Math.max(x, far + GEOMETRY.columnGap);
        column += 1;
        opened.push({ node: id, start: x });
      }
      const role: NodeRole = side === 0 ? "trunk" : index === 0 ? (fromTrunk ? "twig" : "option") : "branch";
      const card = place(id, x, y, side, role, side !== 0 && !(fromTrunk && index === 0));
      if (side === 0) trunk.push([x + card.width / 2, y]);
      else if (previous) edges.push({ kind: "branch", from: path[index - 1], to: id, points: [[previous.x + previous.width, y], [x, y]] });
      right = Math.max(right, card.x + card.width);
      const next = path[index + 1];
      const groups = sideGroups(id, next);
      const members = groups.flatMap((group) => group.members);
      // The last point of a side row hands the row on to its first option; the rest hang below.
      const level = side !== 0 && next === undefined ? members[0] ?? null : null;
      const hung = members.filter((member) => member !== level);
      const start = order.length;
      let stack: number | null = null, forkX = card.x + card.width;
      if (members.length) {
        const firstStem = Math.max(card.x + card.width, clear) + GEOMETRY.stemMargin;
        stack = Math.max(firstStem + GEOMETRY.elbow + Math.max(0, hung.length - 1) * GEOMETRY.stemStep,
          level ? card.x + card.width + GEOMETRY.gap : -Infinity);
        if (hung.length) forkX = firstStem;
        let top = y + halfFrom(index) + GEOMETRY.rowGap;
        hung.forEach((member, rank) => {
          // Nearest first, on the rightmost elbow: the elbows nest and never cross.
          const memberY = top + visual(kindOf(member)).height / 2;
          const stem = stack! - GEOMETRY.elbow - rank * GEOMETRY.stemStep;
          edges.push({ kind: side !== 0 ? "muted-twig" : kindOf(member) === "pending" ? "pending" : "twig", from: id, to: member,
            points: [[stem, y], [stem, memberY], [stack!, memberY]] });
          const from = order.length;
          const reach = lay(chainFrom(member), stack!, memberY, 1, side === 0);
          right = Math.max(right, reach);
          clear = Math.max(clear, reach);
          const grown = boxOf(order.slice(from));
          top = grown.y + grown.height + GEOMETRY.rowGap;
        });
        if (level) {
          edges.push({ kind: "muted-twig", from: id, to: level, points: [[card.x + card.width, y], [stack, y]] });
          const reach = lay(chainFrom(level), stack, y, 1, false, clear);
          right = Math.max(right, reach);
          clear = Math.max(clear, reach);
        }
      }
      // One count per point: its Studies and its running work together.
      let options = 0, continued = 0, pending = 0;
      for (const group of groups) {
        const counted = kids(id).filter((child) => kindOf(child) === "candidate" && (group.study
          ? tree.nodes.get(child)!.studyId === group.study : group.members.includes(child)));
        options += counted.length;
        continued += counted.filter((child) => !group.members.includes(child)).length;
        pending += group.members.filter((member) => kindOf(member) === "pending").length;
      }
      if (options > 0 || pending > 0) {
        forks.push({ node: id, x: forkX, y, side, muted: side !== 0, studies: groups.flatMap((group) => group.study ? [group.study] : []),
          options, continued, pending, region: boxOf([id, ...order.slice(start)]) });
      }
      previous = card;
      // The next point stands level with the options it was chosen from.
      x = Math.max(x + card.width + GEOMETRY.gap, stack ?? -Infinity);
    });
    return right;
  }

  if (tree.trunk.length) opened.push({ node: tree.trunk[0], start: 0 });
  lay(tree.trunk, 0, 0, 0, false);

  // Each column reaches halfway across the gap to its neighbours; the rule between them stands there.
  const bands = opened.map((open, index) => {
    const ids = order.filter((id) => placed.get(id)!.column === index);
    const box = boxOf(ids);
    return { ...open, ids, left: box.x, right: box.x + box.width };
  });
  const columns: StageColumn[] = bands.map((band, index) => {
    const left = index === 0 ? band.left - GEOMETRY.columnGap / 2 : (bands[index - 1].right + band.left) / 2;
    const right = index === bands.length - 1 ? band.right + GEOMETRY.columnGap / 2 : (band.right + bands[index + 1].left) / 2;
    return { node: band.node, x: left, width: right - left, start: band.start,
      options: band.ids.filter((id) => kindOf(id) === "candidate").length, pending: band.ids.filter((id) => kindOf(id) === "pending").length };
  });
  return { nodes: placed, edges, trunk, forks, columns, bounds: boxOf(order) };
}
