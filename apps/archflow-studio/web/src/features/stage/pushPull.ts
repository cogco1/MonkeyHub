/** Local previews of the existing drawn-element push/pull action. */

import type { DrawnShapeDto } from "../../api/generated/types.gen";
import type { SketchPreview } from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import type { PlanPoint, SketchPlane, SketchVector } from "./sketch";

export interface PreparedPushPull {
  preview(distance: number): SketchPreview | null;
}

const dot = (a: SketchVector, b: SketchVector) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cad = ([x, y, z]: SketchVector): SketchVector => [x, z, y];
const sameVector = (a: SketchVector, b: SketchVector) => a.every((value, index) => value === b[index]);
const sameProfile = (a: readonly PlanPoint[], b: readonly PlanPoint[]) => a.length === b.length
  && a.every((point, index) => point[0] === b[index]![0] && point[1] === b[index]![1]);

/**
 * Capture one recorded shape and picked CAD normal. All subsequent motion is
 * arithmetic; only the caller's finished typed action may reach the server.
 * Classification and tolerances follow edit_drawn_element in element_producers.
 */
export function preparePushPull(shape: DrawnShapeDto, normalCAD: SketchVector): PreparedPushPull {
  const magnitude = Math.sqrt(dot(normalCAD, normalCAD));
  if (!Number.isFinite(magnitude) || magnitude < 1e-9) throw new Error("Face normal must be finite and nonzero.");
  const faceNormal = normalCAD.map((value) => value / magnitude) as [number, number, number];
  const plane: SketchPlane = {
    origin: cad(shape.workPlane.origin), xAxis: cad(shape.workPlane.xAxis),
    yAxis: cad(shape.workPlane.yAxis), normal: cad(shape.workPlane.normal),
  };
  const profile: PlanPoint[] = shape.profile.map(([x, y]) => [x, y]);
  const height = shape.height;
  if (!Number.isFinite(height) || height < 0 || profile.some((point) => point.some((value) => !Number.isFinite(value)))) {
    throw new Error("Drawing dimensions must be finite and height cannot be negative.");
  }
  const flat = height === 0;
  if (profile.length < (flat ? 4 : 3)
      || (flat && !sameProfile([profile[0]!], [profile[profile.length - 1]!]))) {
    throw new Error("A drawn face needs a closed profile; a prism needs at least three vertices.");
  }
  const boundFields = new Set(shape.parameterBoundFields);
  const alignment = dot(faceNormal, plane.normal);
  const cap = Math.abs(Math.abs(alignment) - 1) <= 1e-6;
  let sideProfile: ((distance: number) => PlanPoint[]) | null = null;
  if (!cap) {
    if (flat || Math.abs(alignment) > 1e-6) throw new Error("The picked normal is not a profile end or side face.");
    const count = profile.length;
    const area = profile.reduce((sum, a, index) => {
      const b = profile[(index + 1) % count]!;
      return sum + (a[0] * b[1] - b[0] * a[1]);
    }, 0);
    if (Math.abs(area) < 1e-9) throw new Error("Side push/pull requires a nondegenerate profile.");
    const orientation = area > 0 ? 1 : -1;
    const convex = (points: readonly PlanPoint[]) => points.every((b, index) => {
      const a = points[(index + count - 1) % count]!;
      const c = points[(index + 1) % count]!;
      return ((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) * orientation > 1e-9;
    });
    if (!convex(profile)) throw new Error("Side push/pull currently supports convex profiles without collinear vertices.");
    const normals: PlanPoint[] = profile.map((a, index) => {
      const b = profile[(index + 1) % count]!;
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const length = Math.hypot(dx, dy);
      return [orientation * dy / length, -orientation * dx / length];
    });
    const constants = normals.map((normal, index) => normal[0] * profile[index]![0] + normal[1] * profile[index]![1]);
    const localNormal = [dot(faceNormal, plane.xAxis), dot(faceNormal, plane.yAxis)];
    const matching = normals.flatMap((normal, index) => normal[0] * localNormal[0]! + normal[1] * localNormal[1]! > 1 - 1e-6 ? [index] : []);
    if (matching.length !== 1) throw new Error("Pick one unambiguous planar side face before push/pull.");
    const selected = matching[0]!;
    sideProfile = (distance) => {
      const moved = constants.map((value, index) => value + (index === selected ? distance : 0));
      const result = [...profile];
      for (const index of [selected, (selected + 1) % count]) {
        const left = (index + count - 1) % count, right = index;
        const a = normals[left]!, b = normals[right]!;
        const determinant = a[0] * b[1] - a[1] * b[0];
        if (Math.abs(determinant) < 1e-9) throw new Error("Side push/pull cannot intersect parallel adjoining edges.");
        result[index] = [
          (moved[left]! * b[1] - a[1] * moved[right]!) / determinant,
          (a[0] * moved[right]! - moved[left]! * b[0]) / determinant,
        ];
      }
      // Convexity alone also accepts a triangle inverted past its opposite
      // vertex; mirror the producer's moved half-plane constraints.
      const inside = result.every(([x, y]) => normals.every(([nx, ny], index) => nx * x + ny * y <= moved[index]! + 1e-9));
      if (!convex(result) || !inside) throw new Error("This side pull would collapse or cross another profile edge.");
      return result;
    };
  }

  return {
    preview(distance) {
      if (distance === 0) return null;
      if (!Number.isFinite(distance) || Math.abs(distance) < 1e-9) throw new Error("Pull distance must be finite and nonzero.");
      let nextProfile = profile;
      let nextPlane = plane;
      let nextHeight = height;
      if (sideProfile) nextProfile = sideProfile(distance);
      else if (flat) {
        nextProfile = profile.slice(0, -1);
        nextHeight = Math.abs(distance);
        nextPlane = { ...plane, normal: faceNormal.map((value) => value * (distance > 0 ? 1 : -1)) as [number, number, number] };
      } else {
        nextHeight = height + distance;
        if (nextHeight < -1e-9) throw new Error("Push/pull cannot cross the opposite face; pull to zero first.");
        if (alignment < 0) {
          nextPlane = { ...plane, origin: plane.origin.map((value, index) => value + distance * faceNormal[index]!) as [number, number, number] };
        }
        if (Math.abs(nextHeight) < 1e-9) {
          nextHeight = 0;
          nextProfile = [...profile, profile[0]!];
        } else if (nextHeight < 0) throw new Error("Prism height must be positive.");
      }
      if (!Number.isFinite(nextHeight) || nextPlane.origin.some((value) => !Number.isFinite(value))) {
        throw new Error("The resulting drawing dimensions must be finite.");
      }
      const changed = {
        height: nextHeight !== height,
        profile: !sameProfile(nextProfile, profile),
        work_plane: !sameVector(nextPlane.origin, plane.origin) || !sameVector(nextPlane.normal, plane.normal),
      };
      for (const field of boundFields) {
        if (changed[field]) throw new Error(`${field} is parameter-bound; edit its existing control instead of detaching it.`);
      }
      // The viewer closes its own boundary; closing/removing a producer's
      // stored endpoint above still counts when checking parameter bindings.
      const closed = sameProfile([nextProfile[0]!], [nextProfile[nextProfile.length - 1]!]);
      return { profile: closed ? nextProfile.slice(0, -1) : nextProfile, plane: nextPlane,
        base: nextPlane.origin[2], height: nextHeight };
    },
  };
}
