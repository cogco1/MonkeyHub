import assert from "node:assert/strict";
import test from "node:test";

import type { DrawnShapeDto } from "../src/api/generated/types.gen.ts";
import { preparePushPull } from "../src/features/stage/pushPull.ts";
import { pointFromPlane } from "../src/features/stage/sketch.ts";

const shape = (change: Partial<DrawnShapeDto> = {}): DrawnShapeDto => ({
  profile: [[0, 0], [3, 0], [3, 2], [0, 2]], height: 2,
  workPlane: { origin: [10, 5, 20], xAxis: [1, 0, 0], yAxis: [0, 0, 1], normal: [0, 1, 0] },
  parameterBoundFields: [], ...change,
});
const flat = (change: Partial<DrawnShapeDto> = {}) => shape({ height: 0,
  profile: [[0, 0], [3, 0], [3, 2], [0, 2], [0, 0]], ...change });
const near = (actual: number, expected: number) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test("upper and lower caps keep the opposite end fixed in CAD world coordinates", () => {
  const top = preparePushPull(shape(), [0, 0, 2]).preview(1)!;
  assert.equal(top.height, 3);
  assert.deepEqual(top.plane!.origin, [10, 20, 5]);
  assert.deepEqual(top.plane!.normal, [0, 0, 1]);
  const bottom = preparePushPull(shape(), [0, 0, -1]);
  const outward = bottom.preview(1)!;
  assert.equal(outward.height, 3);
  assert.deepEqual(outward.plane!.origin, [10, 20, 4]);
  assert.equal(outward.plane!.origin[2] + outward.height, 7);
  const inward = bottom.preview(-0.5)!;
  assert.equal(inward.height, 1.5);
  assert.equal(inward.plane!.origin[2] + inward.height, 7);
});

test("a prism can become a true face while zero motion and invalid distances never form a candidate", () => {
  const pull = preparePushPull(shape(), [0, 0, 1]);
  assert.equal(pull.preview(0), null);
  assert.equal(pull.preview(-0), null);
  assert.equal(pull.preview(-2)!.height, 0);
  assert.equal(pull.preview(-2)!.profile.length, 4, "the viewport closes the resulting flat boundary");
  assert.equal(pull.preview(-2 + 5e-10)!.height, 0);
  assert.equal(pull.preview(1e-9)!.height, 2 + 1e-9);
  for (const distance of [NaN, Infinity, -Infinity, 5e-10]) assert.throws(() => pull.preview(distance), /finite and nonzero/);
  assert.throws(() => pull.preview(-2 - 1e-8), /opposite face/);
  assert.throws(() => preparePushPull(shape({ height: Number.MAX_VALUE }), [0, 0, 1]).preview(Number.MAX_VALUE), /dimensions must be finite/);
  assert.deepEqual(preparePushPull(shape(), [0, 0, -1]).preview(-2)!.plane!.origin, [10, 20, 7]);
});

test("a flat face extrudes in either picked direction without inventing thickness", () => {
  const pull = preparePushPull(flat(), [0, 0, 1]);
  assert.equal(pull.preview(0), null);
  const forward = pull.preview(1.5)!;
  assert.equal(forward.height, 1.5);
  assert.deepEqual(forward.plane!.normal, [0, 0, 1]);
  assert.equal(forward.profile.length, 4);
  const reverse = pull.preview(-1.5)!;
  assert.equal(reverse.height, 1.5);
  assert.equal(reverse.plane!.normal[2], -1);
  const otherSide = preparePushPull(flat(), [0, 0, -1]).preview(-1.5)!;
  assert.equal(otherSide.plane!.normal[2], 1);
});

test("tilted and mirrored work planes retain their placement and selected side", () => {
  const s = Math.sqrt(0.5);
  const tilted = shape({ workPlane: { origin: [10, 4, 20], xAxis: [1, 0, 0], yAxis: [0, s, s], normal: [0, -s, s] } });
  const preview = preparePushPull(tilted, [0, s, -s]).preview(1)!;
  assert.deepEqual(preview.plane!.origin, [10, 20, 4]);
  assert.deepEqual(pointFromPlane([3, 2], preview.plane!), [13, 20 + 2 * s, 4 + 2 * s]);
  assert.equal(preview.height, 3);
  const mirrored = shape({ workPlane: { origin: [0, 0, 0], xAxis: [-1, 0, 0], yAxis: [0, 0, 1], normal: [0, -1, 0] } });
  const side = preparePushPull(mirrored, [-1, 0, 0]).preview(1)!;
  assert.deepEqual(side.profile, [[0, 0], [4, 0], [4, 2], [0, 2]]);
  assert.deepEqual(pointFromPlane(side.profile[2]!, side.plane!), [-4, 2, 0]);
  assert.equal(side.plane!.normal[2], -1);
});

test("all four rectangle sides move their own boundary without changing height or the opposite side", () => {
  const cases = [
    { normal: [1, 0, 0] as const, expected: [[0, 0], [4, 0], [4, 2], [0, 2]] },
    { normal: [-1, 0, 0] as const, expected: [[-1, 0], [3, 0], [3, 2], [-1, 2]] },
    { normal: [0, 1, 0] as const, expected: [[0, 0], [3, 0], [3, 3], [0, 3]] },
    { normal: [0, -1, 0] as const, expected: [[0, -1], [3, -1], [3, 2], [0, 2]] },
  ];
  for (const { normal, expected } of cases) {
    const preview = preparePushPull(shape(), normal).preview(1)!;
    assert.deepEqual(preview.profile.map((point) => point.map((value) => value + 0)), expected);
    assert.equal(preview.height, 2);
    assert.deepEqual(preview.plane!.origin, [10, 20, 5]);
  }
  const inward = preparePushPull(shape(), [1, 0, 0]).preview(-1)!;
  assert.deepEqual(inward.profile, [[0, 0], [2, 0], [2, 2], [0, 2]]);
});

test("a triangular side re-intersects its adjacent edges but cannot invert beyond its opposite vertex", () => {
  const vertices = [[0, 0], [4, 0], [0, 3]] as [number, number][];
  for (const profile of [vertices, [...vertices].reverse()]) {
    const pull = preparePushPull(shape({ profile }), [0.6, 0.8, 0]);
    for (const distance of [-0.5, 1]) {
      const preview = pull.preview(distance)!;
      const scale = (2.4 + distance) / 2.4;
      preview.profile.forEach((point, index) => point.forEach((value, axis) => near(value, profile[index]![axis]! * scale)));
      assert.equal(preview.height, 2);
    }
    for (const distance of [-2.4, -3]) assert.throws(() => pull.preview(distance), /collapse or cross/);
  }
});

test("unavailable normals and unsupported side profiles fail before they can show a misleading preview", () => {
  for (const normal of [[0, 0, 0], [NaN, 0, 1], [Infinity, 0, 0]] as const) {
    assert.throws(() => preparePushPull(shape(), normal), /normal/);
  }
  assert.throws(() => preparePushPull(flat(), [1, 0, 0]), /end or side face/);
  assert.throws(() => preparePushPull(shape(), [1, 0, 1]), /end or side face/);
  assert.throws(() => preparePushPull(shape(), [1, 1, 0]), /unambiguous/);
  assert.throws(() => preparePushPull(shape({ profile: [[0, 0], [3, 0], [3, 2], [1, 1], [0, 2]] }), [1, 0, 0]), /convex/);
  assert.throws(() => preparePushPull(shape({ profile: [[0, 0], [1, 0], [3, 0], [3, 2], [0, 2]] }), [1, 0, 0]), /collinear/);
  const side = preparePushPull(shape(), [1, 0, 0]);
  assert.throws(() => side.preview(-3), /collapse or cross/);
  assert.throws(() => side.preview(-4), /collapse or cross/);
});

test("parameter bindings reject only the producer fields changed by this preview", () => {
  const heightBound = shape({ parameterBoundFields: ["height"] });
  assert.equal(preparePushPull(heightBound, [1, 0, 0]).preview(1)!.height, 2);
  assert.throws(() => preparePushPull(heightBound, [0, 0, 1]).preview(1), /height is parameter-bound/);
  const profileBound = shape({ parameterBoundFields: ["profile"] });
  assert.equal(preparePushPull(profileBound, [0, 0, 1]).preview(1)!.height, 3);
  assert.throws(() => preparePushPull(profileBound, [1, 0, 0]).preview(1), /profile is parameter-bound/);
  assert.throws(() => preparePushPull(profileBound, [0, 0, 1]).preview(-2), /profile is parameter-bound/);
  assert.throws(() => preparePushPull(flat({ parameterBoundFields: ["profile"] }), [0, 0, 1]).preview(1), /profile is parameter-bound/);
  const planeBound = shape({ parameterBoundFields: ["work_plane"] });
  assert.equal(preparePushPull(planeBound, [0, 0, 1]).preview(1)!.height, 3);
  assert.equal(preparePushPull(planeBound, [1, 0, 0]).preview(1)!.height, 2);
  assert.throws(() => preparePushPull(planeBound, [0, 0, -1]).preview(1), /work_plane is parameter-bound/);
  const flatPlaneBound = preparePushPull(flat({ parameterBoundFields: ["work_plane"] }), [0, 0, 1]);
  assert.equal(flatPlaneBound.preview(1)!.height, 1);
  assert.throws(() => flatPlaneBound.preview(-1), /work_plane is parameter-bound/);
});

test("repeated distances use the captured source and leave both its DTO and earlier results unchanged", () => {
  const source = shape();
  const before = structuredClone(source);
  const pull = preparePushPull(source, [1, 0, 0]);
  const first = pull.preview(1)!;
  assert.deepEqual(pull.preview(2)!.profile, [[0, 0], [5, 0], [5, 2], [0, 2]]);
  assert.deepEqual(first.profile, [[0, 0], [4, 0], [4, 2], [0, 2]]);
  assert.deepEqual(pull.preview(1), first);
  assert.deepEqual(source, before);
});
