/**
 * The edges a person can see, out of a mesh that was triangulated to draw it.
 *
 * A loaded 3DM arrives as triangles. Snapping or measuring against every
 * triangle edge would offer the diagonals that cut across a flat face — lines
 * nobody drew and nobody can see — so an edge counts here only when the two
 * triangles sharing it actually turn, or when nothing shares it at all (a
 * boundary). That is the same rule three.js uses to draw an outline, applied
 * to the numbers rather than to a picture.
 *
 * Everything is in the geometry's own coordinates; the caller places them in
 * the world. Nothing here reads the document, the record or the screen.
 */

export type Point3 = readonly [number, number, number];

export interface FeatureEdge {
  readonly a: Point3;
  readonly b: Point3;
}

/** A triangulated surface, as the loader hands it over. */
export interface TriangleSoup {
  /** Flat xyz triples. */
  readonly positions: ArrayLike<number>;
  /** Triangle corner indices, or null when positions are already in order. */
  readonly index: ArrayLike<number> | null;
}

const KEY = 1e6;

function key(point: Point3): string {
  return `${Math.round(point[0] * KEY)},${Math.round(point[1] * KEY)},${Math.round(point[2] * KEY)}`;
}

function at(positions: ArrayLike<number>, corner: number): Point3 {
  return [positions[corner * 3]!, positions[corner * 3 + 1]!, positions[corner * 3 + 2]!];
}

function subtract(a: Point3, b: Point3): Point3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function cross(a: Point3, b: Point3): Point3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function length(a: Point3): number {
  return Math.hypot(a[0], a[1], a[2]);
}

function normal(a: Point3, b: Point3, c: Point3): Point3 | null {
  const value = cross(subtract(b, a), subtract(c, a));
  const size = length(value);
  // A degenerate triangle has no direction to compare; it is not an edge either.
  return size < 1e-12 ? null : [value[0] / size, value[1] / size, value[2] / size];
}

/**
 * The visible edges of one triangulated surface.
 *
 * `thresholdDegrees` is how far two faces must turn before the line between
 * them counts as an edge; the default matches a drawn outline. An edge with
 * only one face is a boundary and always counts.
 */
export function featureEdges(soup: TriangleSoup, thresholdDegrees = 20): FeatureEdge[] {
  const { positions } = soup;
  const corners = soup.index ?? { length: positions.length / 3, [Symbol.iterator]: undefined } as unknown as ArrayLike<number>;
  const count = soup.index ? soup.index.length : positions.length / 3;
  const cosine = Math.cos((thresholdDegrees * Math.PI) / 180);
  const shared = new Map<string, { a: Point3; b: Point3; normals: Point3[] }>();
  for (let triangle = 0; triangle + 2 < count; triangle += 3) {
    const indices = soup.index
      ? [soup.index[triangle]!, soup.index[triangle + 1]!, soup.index[triangle + 2]!]
      : [triangle, triangle + 1, triangle + 2];
    const points = indices.map((corner) => at(positions, corner)) as [Point3, Point3, Point3];
    const face = normal(points[0], points[1], points[2]);
    if (face === null) continue;
    for (let side = 0; side < 3; side += 1) {
      const a = points[side]!;
      const b = points[(side + 1) % 3]!;
      const keys = [key(a), key(b)].sort();
      if (keys[0] === keys[1]) continue;
      const id = `${keys[0]}|${keys[1]}`;
      const entry = shared.get(id);
      if (entry) entry.normals.push(face);
      else shared.set(id, { a, b, normals: [face] });
    }
  }
  const edges: FeatureEdge[] = [];
  for (const entry of shared.values()) {
    if (entry.normals.length === 1) {
      edges.push({ a: entry.a, b: entry.b });
      continue;
    }
    // Two faces that lie flat against each other are one surface, and the line
    // between them is a triangulation artefact rather than an edge.
    const [first, second] = entry.normals as [Point3, Point3];
    const dot = first[0] * second[0] + first[1] * second[1] + first[2] * second[2];
    if (Math.abs(dot) < cosine) edges.push({ a: entry.a, b: entry.b });
  }
  return edges;
}

export type SnapKind = "endpoint" | "midpoint" | "edge";

export interface SnapCandidate {
  readonly point: Point3;
  readonly kind: SnapKind;
}

/** The points one visible edge offers: its ends, and its middle. */
export function candidatesOf(edge: FeatureEdge): SnapCandidate[] {
  return [
    { point: edge.a, kind: "endpoint" },
    { point: edge.b, kind: "endpoint" },
    { point: [(edge.a[0] + edge.b[0]) / 2, (edge.a[1] + edge.b[1]) / 2, (edge.a[2] + edge.b[2]) / 2], kind: "midpoint" },
  ];
}

/** The nearest point on a segment to a point, for snapping along an edge. */
export function closestOnEdge(edge: FeatureEdge, point: Point3): Point3 {
  const along = subtract(edge.b, edge.a);
  const span = along[0] ** 2 + along[1] ** 2 + along[2] ** 2;
  if (span < 1e-18) return edge.a;
  const toward = subtract(point, edge.a);
  const t = Math.min(1, Math.max(0, (toward[0] * along[0] + toward[1] * along[1] + toward[2] * along[2]) / span));
  return [edge.a[0] + along[0] * t, edge.a[1] + along[1] * t, edge.a[2] + along[2] * t];
}

/**
 * Which candidate a pointer is on, judged where the person is looking: the
 * caller projects each candidate to the screen, and the nearest one inside the
 * radius wins. An endpoint beats a midpoint at the same distance, because that
 * is the point somebody is more likely to have meant.
 */
export function nearestCandidate(
  candidates: readonly SnapCandidate[],
  project: (point: Point3) => readonly [number, number] | null,
  pointer: readonly [number, number],
  radiusPx: number,
): SnapCandidate | null {
  const rank: Record<SnapKind, number> = { endpoint: 0, midpoint: 1, edge: 2 };
  let best: { candidate: SnapCandidate; distance: number } | null = null;
  for (const candidate of candidates) {
    const screen = project(candidate.point);
    if (screen === null) continue;
    const distance = Math.hypot(screen[0] - pointer[0], screen[1] - pointer[1]);
    if (distance > radiusPx) continue;
    if (
      best === null
      || distance < best.distance - 0.5
      || (Math.abs(distance - best.distance) <= 0.5 && rank[candidate.kind] < rank[best.candidate.kind])
    ) {
      best = { candidate, distance };
    }
  }
  return best?.candidate ?? null;
}

/** The distance between two model points, in the model's own units. */
export function distanceBetween(a: Point3, b: Point3): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}
