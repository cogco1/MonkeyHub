/**
 * A planar left-to-right drawing of the growth tree.
 *
 * The trunk runs along y = 0 through the Working Head's lineage. At each point
 * it passes, the options not taken leave on short elbows, alternating above
 * and below. A side branch keeps growing along its own row through whatever
 * was continued there, and its own options leave further outwards. Every side
 * subtree is given its own x-range on its side before the next one starts, so
 * no edge crosses another and no footprint overlaps (a tree has E = V - 1).
 * Positions never depend on zoom; the scene only changes what it draws.
 * Ported from the #284 prototype (`docs/prototypes/candidate-graph/prototype.js`).
 */
import type { GrowthTree, TreeNodeKind } from "./model";

export type Side = -1 | 0 | 1;
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
  /** Where its name, summary and status go; null for Stages and Current. */
  readonly labels: Box | null;
  /** Card plus labels: what no other node may overlap. */
  readonly footprint: Box;
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

export interface GrowthLayout {
  readonly nodes: ReadonlyMap<string, PlacedNode>;
  readonly edges: readonly LayoutEdge[];
  /** The trunk as one polyline through its nodes' centres, left to right. */
  readonly trunk: readonly (readonly [number, number])[];
  readonly forks: readonly Fork[];
  readonly bounds: Box;
}

/** Scene units at 100 %. */
export const GEOMETRY = {
  card: { width: 96, height: 64 },
  labels: 74,
  stage: { width: 176, height: 40 },
  current: { width: 250, height: 112 },
  origin: { width: 150, height: 40 },
  slot: { card: 140, stage: 196, current: 270, origin: 170 },
  gap: 36,
  elbow: 16,
  lane: 150,
  subLane: 170,
  labelGap: 20,
} as const;

const visual = (kind: TreeNodeKind) => kind === "stage" ? GEOMETRY.stage : kind === "current" ? GEOMETRY.current
  : kind === "origin" ? GEOMETRY.origin : GEOMETRY.card;
const slotOf = (kind: TreeNodeKind) => kind === "stage" ? GEOMETRY.slot.stage : kind === "current" ? GEOMETRY.slot.current
  : kind === "origin" ? GEOMETRY.slot.origin : GEOMETRY.slot.card;

export function layoutGrowthTree(tree: GrowthTree): GrowthLayout {
  const placed = new Map<string, PlacedNode>();
  const order: string[] = [];
  const edges: LayoutEdge[] = [];
  const trunk: [number, number][] = [];
  const forks: (Omit<Fork, "region"> & { from: number; to: number })[] = [];
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
  // Study comes last, next to the trunk it continued.
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
      groups.push(index >= 0 ? groups.splice(index, 1)[0] : { study: nextStudy, members: [] });
    }
    return groups;
  };

  function place(id: string, x: number, y: number, side: Side, role: NodeRole, muted: boolean) {
    const kind = kindOf(id);
    const size = visual(kind);
    const card = { x, y: y - size.height / 2, width: size.width, height: size.height };
    const labelled = kind === "candidate" || kind === "pending";
    const labels = labelled ? {
      x, y: side < 0 ? card.y - GEOMETRY.labels : card.y + card.height, width: slotOf(kind) - 4, height: GEOMETRY.labels,
    } : null;
    const top = labels && side < 0 ? labels.y : card.y;
    const bottom = labels && side >= 0 ? labels.y + labels.height : card.y + card.height;
    const width = Math.max(card.width, labels?.width ?? 0);
    placed.set(id, { id, kind, x, y, side, role, muted, card, labels, footprint: { x, y: top, width, height: bottom - top } });
    order.push(id);
  }

  function lay(path: readonly string[], x0: number, y: number, side: Side, fromTrunk: boolean): { right: number } {
    const cursor: Record<-1 | 1, number> = { [-1]: x0, [1]: x0 };
    let x = x0, previous: string | null = null, right = x0;
    path.forEach((id, index) => {
      const kind = kindOf(id);
      const role: NodeRole = side === 0 ? "trunk" : index === 0 ? (fromTrunk ? "twig" : "option") : "branch";
      place(id, x, y, side, role, side !== 0 && !(fromTrunk && index === 0));
      if (side === 0) trunk.push([x + visual(kind).width / 2, y]);
      else if (previous !== null) {
        const from = placed.get(previous)!;
        edges.push({ kind: "branch", from: previous, to: id, points: [[from.card.x + from.card.width, y], [x, y]] });
      }
      const next = path[index + 1];
      let decision = x + slotOf(kind) + GEOMETRY.labelGap, lastStem: number | null = null, parity = 0;
      for (const group of sideGroups(id, next)) {
        let first: number | null = null;
        const start = order.length;
        for (const member of group.members) {
          const sigma: -1 | 1 = side === 0 ? (parity++ % 2 === 0 ? -1 : 1) : side;
          const column = Math.max(decision, cursor[sigma]);
          const childY = y + sigma * (side === 0 ? GEOMETRY.lane : GEOMETRY.subLane);
          const sub = lay(chainFrom(member), column + GEOMETRY.elbow, childY, sigma, side === 0);
          edges.push({ kind: side !== 0 ? "muted-twig" : kindOf(member) === "pending" ? "pending" : "twig", from: id, to: member,
            points: [[column, y], [column, childY], [column + GEOMETRY.elbow, childY]] });
          cursor[sigma] = sub.right + GEOMETRY.gap;
          lastStem = lastStem === null ? column : Math.max(lastStem, column);
          if (first === null) first = column;
          right = Math.max(right, sub.right);
        }
        // A Study counts all its options that grew here, the continued one included.
        const options = kids(id).filter((child) => kindOf(child) === "candidate" && (group.study
          ? tree.nodes.get(child)!.studyId === group.study : group.members.includes(child)));
        forks.push({ node: id, x: first ?? decision, y, side, muted: side !== 0, studies: group.study ? [group.study] : [],
          options: options.length, continued: options.filter((child) => !group.members.includes(child)).length,
          pending: group.members.filter((member) => kindOf(member) === "pending").length, from: start, to: order.length });
        if (lastStem !== null) decision = Math.max(decision, lastStem);
      }
      right = Math.max(right, x + slotOf(kind));
      previous = id;
      x = Math.max(x + slotOf(kind) + GEOMETRY.gap, lastStem === null ? 0 : lastStem + GEOMETRY.elbow + GEOMETRY.gap);
    });
    return { right };
  }

  lay(tree.trunk, 0, 0, 0, false);

  const boxOf = (ids: readonly string[]): Box => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const id of ids) {
      const box = placed.get(id)!.footprint;
      minX = Math.min(minX, box.x); minY = Math.min(minY, box.y);
      maxX = Math.max(maxX, box.x + box.width); maxY = Math.max(maxY, box.y + box.height);
    }
    return Number.isFinite(minX) ? { x: minX, y: minY, width: maxX - minX, height: maxY - minY } : { x: 0, y: 0, width: 0, height: 0 };
  };
  // One count per point: its Studies and its running work together.
  const merged = new Map<string, Omit<Fork, "region"> & { from: number; to: number }>();
  for (const fork of forks) {
    if (fork.options === 0 && fork.pending === 0) continue;
    const known = merged.get(fork.node);
    if (!known) { merged.set(fork.node, { ...fork }); continue; }
    merged.set(fork.node, { ...known, x: Math.min(known.x, fork.x), studies: [...known.studies, ...fork.studies],
      options: known.options + fork.options, continued: known.continued + fork.continued, pending: known.pending + fork.pending,
      from: Math.min(known.from, fork.from), to: Math.max(known.to, fork.to) });
  }
  return {
    nodes: placed, edges, trunk,
    forks: [...merged.values()].map(({ from, to, ...fork }) => ({ ...fork, region: boxOf([fork.node, ...order.slice(from, to)]) })),
    bounds: boxOf(order),
  };
}
