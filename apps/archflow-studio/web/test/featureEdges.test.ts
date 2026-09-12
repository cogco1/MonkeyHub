/**
 * The edges a person can see, and the points they can snap to, out of real
 * triangulated geometry — including the case that matters: a flat face split
 * into two triangles must not offer its diagonal.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  candidatesOf,
  closestOnEdge,
  distanceBetween,
  featureEdges,
  nearestCandidate,
  type FeatureEdge,
  type Point3,
} from "../src/workspaces/monkeyarch/viewer/featureEdges.ts";

/** A 6 by 4 rectangle in the XZ plane, split into two triangles. */
const FLAT = {
  positions: [
    0, 0, 0, 6, 0, 0, 6, 0, 4,
    0, 0, 0, 6, 0, 4, 0, 0, 4,
  ],
  index: null,
};

/** A 6 x 3.2 x 4 box as twelve triangles, the way a loader hands one over. */
function box(width: number, height: number, depth: number) {
  const [x, y, z] = [width, height, depth];
  const corners: Point3[] = [
    [0, 0, 0], [x, 0, 0], [x, 0, z], [0, 0, z],
    [0, y, 0], [x, y, 0], [x, y, z], [0, y, z],
  ];
  const faces = [
    [0, 1, 2], [0, 2, 3], // bottom
    [4, 6, 5], [4, 7, 6], // top
    [0, 5, 1], [0, 4, 5], // front
    [1, 6, 2], [1, 5, 6], // right
    [2, 7, 3], [2, 6, 7], // back
    [3, 4, 0], [3, 7, 4], // left
  ];
  const positions: number[] = [];
  for (const face of faces) {
    for (const corner of face) positions.push(...corners[corner]!);
  }
  return { positions, index: null };
}

const near = (value: number, want: number, tolerance = 1e-9) => Math.abs(value - want) <= tolerance;

test("a flat face split into triangles offers no diagonal", () => {
  const edges = featureEdges(FLAT);
  assert.equal(edges.length, 4, `a rectangle has four edges, got ${edges.length}`);
  const lengths = edges.map((edge) => distanceBetween(edge.a, edge.b)).sort((a, b) => a - b);
  assert.deepEqual(lengths.map((value) => Math.round(value * 1000) / 1000), [4, 4, 6, 6]);
  // The diagonal is 7.211…; it must not be there.
  assert.ok(!lengths.some((value) => near(value, Math.hypot(6, 4), 1e-6)), "the triangulation diagonal is not an edge");
});

test("a box offers its twelve real edges and nothing across a face", () => {
  const edges = featureEdges(box(6, 3.2, 4));
  assert.equal(edges.length, 12, `a box has twelve edges, got ${edges.length}`);
  const lengths = edges.map((edge) => Math.round(distanceBetween(edge.a, edge.b) * 1000) / 1000);
  assert.deepEqual(
    [6, 3.2, 4].map((value) => lengths.filter((row) => near(row, value, 1e-6)).length),
    [4, 4, 4],
    "four edges of each dimension",
  );
});

test("an edge offers its ends and its middle, in model coordinates", () => {
  const edge: FeatureEdge = { a: [0, 0, 0], b: [6, 0, 0] };
  const candidates = candidatesOf(edge);
  assert.deepEqual(candidates.map((row) => row.kind), ["endpoint", "endpoint", "midpoint"]);
  assert.deepEqual(candidates[2]!.point, [3, 0, 0]);
  assert.deepEqual(closestOnEdge(edge, [4.2, 9, -3]), [4.2, 0, 0]);
  assert.deepEqual(closestOnEdge(edge, [-5, 0, 0]), [0, 0, 0], "a point before the start snaps to the start");
});

test("the pointer takes the nearest candidate, preferring an end over a middle", () => {
  const flat = (point: Point3) => [point[0] * 10, point[2] * 10] as const;
  const candidates = candidatesOf({ a: [0, 0, 0], b: [6, 0, 0] });
  assert.equal(nearestCandidate(candidates, flat, [1, 0], 12)?.kind, "endpoint");
  assert.deepEqual(nearestCandidate(candidates, flat, [31, 2], 12)?.point, [3, 0, 0]);
  assert.equal(nearestCandidate(candidates, flat, [200, 200], 12), null, "nothing near means nothing snaps");
  // At the same distance the end wins, because that is what was meant.
  const tie = [{ point: [1, 0, 0] as Point3, kind: "midpoint" as const },
               { point: [1, 0, 0] as Point3, kind: "endpoint" as const }];
  assert.equal(nearestCandidate(tie, flat, [10, 0], 12)?.kind, "endpoint");
});

test("measuring two model points gives the model's own distance", () => {
  assert.equal(distanceBetween([0, 0, 0], [6, 0, 0]), 6);
  assert.equal(Math.round(distanceBetween([0, 0, 0], [6, 3.2, 4]) * 1000) / 1000, 7.889);
});
