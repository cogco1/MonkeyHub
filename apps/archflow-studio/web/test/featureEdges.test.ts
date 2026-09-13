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
  connectedFace,
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

test("face preselection crosses a flat diagonal and stops at each box fold", () => {
  assert.deepEqual(connectedFace(FLAT, 0), [0, 1]);
  assert.deepEqual(connectedFace(FLAT, 1), [0, 1], "duplicated corners weld across the diagonal");
  const mesh = box(6, 3.2, 4);
  for (let triangle = 0; triangle < 12; triangle += 1) {
    const first = Math.floor(triangle / 2) * 2;
    assert.deepEqual(connectedFace(mesh, triangle), [first, first + 1]);
  }
});

test("indexed face preselection accepts reversed winding but requires a full shared edge", () => {
  const mesh = {
    positions: [
      0, 0, 0, 6, 0, 0, 6, 0, 4, 0, 0, 4,
      10, 0, 0, 12, 0, 0, 12, 0, 4,
      -2, 0, 0, 0, 0, -2,
    ],
    index: [0, 1, 2, 0, 3, 2, 4, 5, 6, 0, 7, 8],
  };
  assert.deepEqual(connectedFace(mesh, 1), [0, 1]);
  assert.deepEqual(connectedFace(mesh, 2), [2], "a disconnected coplanar region stays separate");
  assert.deepEqual(connectedFace(mesh, 3), [3], "one shared corner does not connect faces");
});

test("translated, sloped Float32 planes stay connected after coordinate rounding", () => {
  const positions = [
    123.603, 345.799, 457.007,
    129.3491502826, 348.1274666478, 457.007,
    128.9278604089, 349.1671186132, 461.571168997,
    123.1817101263, 346.8386519654, 461.571168997,
  ];
  const index = [0, 1, 2, 0, 2, 3];
  assert.deepEqual(connectedFace({ positions, index }, 0), [0, 1]);
  for (const translation of [0, 10000]) {
    const stored = new Float32Array(positions.map((coordinate) => coordinate + translation));
    for (const triangle of [0, 1]) {
      assert.deepEqual(connectedFace({ positions: stored, index }, triangle), [0, 1]);
    }
    const unindexed = new Float32Array(index.flatMap((corner) => Array.from(stored.slice(corner * 3, corner * 3 + 3))));
    assert.deepEqual(connectedFace({ positions: unindexed, index: null }, 0), [0, 1]);
    const folded = new Float32Array([...stored, stored[0]!, stored[1]! + 2, stored[2]!]);
    const withFold = { positions: folded, index: [...index, 0, 1, 4] };
    assert.deepEqual(connectedFace(withFold, 0), [0, 1], "storage tolerance still stops at a sharp fold");
    assert.deepEqual(connectedFace(withFold, 2), [2]);
  }
});

test("thin translated Float32 triangles cannot use large uncertainty to cross real folds", () => {
  const index = [0, 1, 2, 0, 3, 1];
  const ninetyDegrees = new Float32Array([
    0, 0, 10000, 1, 0, 10000, 0, .001, 10000, 0, 0, 10001,
  ]);
  const shallowFold = new Float32Array([
    0, 0, 10000, 1, 0, 10000, 0, .001, 10000,
    0, -1, 10000 + Math.tan(0.1 * Math.PI / 180),
  ]);
  for (const positions of [ninetyDegrees, shallowFold]) {
    assert.deepEqual(connectedFace({ positions, index }, 0), [0]);
    assert.deepEqual(connectedFace({ positions, index }, 1), [1]);
  }
});

test("preselection rejects offset planes, folded and degenerate triangles", () => {
  const mesh = {
    positions: [
      ...FLAT.positions,
      0, 0, 0, 6, 0, 0, 3, 2, 0, // a fold along a flat face boundary
      0, 0, 0, 6, 0, 0, 3, 0, 0, // a collinear triangle along that boundary
      0, 2e-7, 0, 6, 2e-7, 0, 6, 2e-7, 4, // same weld keys, different plane
    ],
    index: null,
  };
  for (const positions of [mesh.positions, new Float32Array(mesh.positions)]) {
    assert.deepEqual(connectedFace({ positions, index: null }, 0), [0, 1]);
    assert.deepEqual(connectedFace({ positions, index: null }, 2), [2]);
    assert.deepEqual(connectedFace({ positions, index: null }, 3), []);
    assert.deepEqual(connectedFace({ positions, index: null }, 4), [4]);
  }
  for (const invalid of [-1, 5, 0.5, NaN]) assert.deepEqual(connectedFace(mesh, invalid), []);
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
