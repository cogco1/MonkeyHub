/** Disposable, local model edits. Only Sync may submit these commands to Studio. */
import type { DrawnShapeDto } from "../../api/generated/types.gen";
import type { SketchPreview } from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import type { DirectModelAction } from "./ModelEditPanel";
import type { FinishedSketch, PlanPoint, SketchPlane, SketchVector } from "./sketch";
import { preparePushPull } from "./pushPull";

export interface DraftObject {
  readonly elementId: string;
  readonly componentId: string;
  readonly spec: SketchPreview | null;
  /** A null spec without this flag is an existing object with no local shape projection. */
  readonly deleted?: boolean;
  readonly originalObjectNames: readonly string[];
  readonly created: boolean;
  readonly parameterBoundFields?: DrawnShapeDto["parameterBoundFields"];
}
export type DraftCommand =
  | { readonly kind: "sketch"; readonly elementId: string; readonly componentId: string; readonly action: FinishedSketch }
  | { readonly kind: "direct"; readonly elementId: string; readonly action: DirectModelAction; readonly copyElementId?: string }
  | { readonly kind: "delete"; readonly elementId: string };
export interface DraftSnapshot {
  readonly commands: readonly DraftCommand[];
  readonly objects: ReadonlyMap<string, DraftObject>;
}
export interface ModelDraftHistory {
  readonly snapshots: readonly DraftSnapshot[];
  readonly index: number;
}

const swap = ([x, y, z]: SketchVector): [number, number, number] => [x, z, y];
const dot = (a: SketchVector, b: SketchVector) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const vector = (v: SketchVector): [number, number, number] => {
  if (v.length !== 3 || !v.every(Number.isFinite)) throw new Error("Model coordinates must be finite.");
  return [v[0], v[1], v[2]];
};
const unit = (v: SketchVector): [number, number, number] => {
  const length = Math.hypot(...vector(v));
  if (length < 1e-9) throw new Error("Model axes must be nonzero.");
  return [v[0] / length, v[1] / length, v[2] / length];
};
const sameNumber = (a: number, b: number) => Math.abs(a - b) <= 1e-9;
const sameVector = (a: SketchVector, b: SketchVector) => a.every((n, i) => sameNumber(n, b[i]!));
const sameProfile = (a: readonly PlanPoint[], b: readonly PlanPoint[]) => a.length === b.length &&
  a.every((point, i) => point.every((n, j) => sameNumber(n, b[i]![j]!)));
const planeOf = (spec: SketchPreview): SketchPlane => spec.plane ?? {
  origin: [0, 0, spec.base], xAxis: [1, 0, 0], yAxis: [0, 1, 0], normal: [0, 0, 1],
};
const samePlane = (a: SketchPlane, b: SketchPlane) =>
  (["origin", "xAxis", "yAxis", "normal"] as const).every(key => sameVector(a[key], b[key]));
const sameSpec = (a: SketchPreview | null, b: SketchPreview | null) => a === b || (a !== null && b !== null &&
  (a.closed ?? true) === (b.closed ?? true) && sameNumber(a.height, b.height) &&
  sameProfile(a.profile, b.profile) && samePlane(planeOf(a), planeOf(b)));

function copySpec(spec: SketchPreview): SketchPreview {
  const source = planeOf(spec);
  const plane = Object.freeze({ origin: Object.freeze(vector(source.origin)), xAxis: Object.freeze(vector(source.xAxis)),
    yAxis: Object.freeze(vector(source.yAxis)), normal: Object.freeze(vector(source.normal)) });
  if (!Number.isFinite(spec.height) || spec.height < 0 || spec.profile.some(p => p.length !== 2 || !p.every(Number.isFinite))) {
    throw new Error("Model profile and height must be finite, with nonnegative height.");
  }
  if ([plane.xAxis, plane.yAxis, plane.normal].some(v => Math.abs(Math.hypot(...v) - 1) > 1e-6) ||
      Math.abs(dot(plane.xAxis, plane.yAxis)) > 1e-6 || Math.abs(dot(plane.xAxis, plane.normal)) > 1e-6 ||
      Math.abs(dot(plane.yAxis, plane.normal)) > 1e-6) throw new Error("Model work plane must be orthonormal.");
  return Object.freeze({ profile: Object.freeze(spec.profile.map(p => Object.freeze([p[0], p[1]] as const))),
    base: plane.origin[2], height: spec.height, plane, closed: spec.closed ?? true });
}

export function specFromDrawnShape(shape: DrawnShapeDto): SketchPreview {
  const profile = shape.height === 0 && sameProfile([shape.profile[0]!], [shape.profile.at(-1)!])
    ? shape.profile.slice(0, -1) : shape.profile;
  return copySpec({ profile, height: shape.height, base: shape.workPlane.origin[1], closed: true,
    plane: { origin: swap(shape.workPlane.origin), xAxis: swap(shape.workPlane.xAxis),
      yAxis: swap(shape.workPlane.yAxis), normal: swap(shape.workPlane.normal) } });
}

export function drawnShapeFromSpec(spec: SketchPreview,
  parameterBoundFields: DrawnShapeDto["parameterBoundFields"] = []): DrawnShapeDto {
  if (spec.closed === false) throw new Error("Open model curves do not support direct face/prism edits.");
  const plane = planeOf(spec);
  const profile: [number, number][] = spec.profile.map(p => [p[0], p[1]]);
  if (spec.height === 0 && profile.length && !sameProfile([profile[0]!], [profile.at(-1)!])) profile.push([...profile[0]!]);
  return { profile, height: spec.height, parameterBoundFields: [...parameterBoundFields],
    workPlane: { origin: swap(plane.origin), xAxis: swap(plane.xAxis), yAxis: swap(plane.yAxis), normal: swap(plane.normal) } };
}

function specFromSketch(action: FinishedSketch): SketchPreview {
  if (action.closed === false && action.height !== 0) throw new Error("An open curve has no extrusion height.");
  const plane = planeOf(action);
  return copySpec({ ...action, height: Math.abs(action.height), plane: action.height < 0
    ? { ...plane, normal: plane.normal.map(n => -n) as [number, number, number] } : plane });
}

/** Mirrors edit_drawn_element arithmetic in CAD coordinates; it is not a geometry validation result. */
function transformSpec(object: DraftObject, action: DirectModelAction): SketchPreview {
  const before = object.spec!;
  if (before.closed === false) throw new Error("Open model curves do not support direct face/prism edits.");
  const plane = planeOf(before), bound = object.parameterBoundFields ?? [];
  if (action.kind === "pushPull") {
    const result = preparePushPull(drawnShapeFromSpec(before, bound), action.normal ?? plane.normal).preview(action.distance);
    if (!result) throw new Error("Pull distance must be nonzero.");
    return copySpec(result);
  }
  const corners = before.profile.map(([x, y]) => plane.origin.map((c, i) => c + x * plane.xAxis[i]! + y * plane.yAxis[i]!) as [number, number, number]);
  if (before.height) corners.push(...corners.map(point => point.map((c, i) => c + before.height * plane.normal[i]!) as [number, number, number]));
  const pivot = [0, 1, 2].map(i => (Math.min(...corners.map(p => p[i]!)) + Math.max(...corners.map(p => p[i]!))) / 2) as [number, number, number];
  const offset = action.kind === "move" || action.kind === "copy" ? vector(action.translation) : [0, 0, 0];
  const factors = action.kind === "scale" ? vector(action.scale) : [1, 1, 1];
  if (factors.some(n => Math.abs(n) < 1e-9)) throw new Error("Scale factors must be nonzero.");
  const axis = action.kind === "rotate" ? unit(action.axis) : [0, 0, 1] as const;
  const angle = action.kind === "rotate" ? action.angleDegrees * Math.PI / 180 : 0;
  if (!Number.isFinite(angle)) throw new Error("Rotation angle must be finite.");
  const direction = (v: SketchVector): [number, number, number] => {
    if (action.kind === "scale") return [v[0] * factors[0]!, v[1] * factors[1]!, v[2] * factors[2]!];
    if (action.kind !== "rotate") return vector(v);
    const cross = [axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0]];
    return v.map((n, i) => n * Math.cos(angle) + cross[i]! * Math.sin(angle) + axis[i]! * dot(axis, v) * (1 - Math.cos(angle))) as [number, number, number];
  };
  const moved = direction(plane.origin.map((c, i) => c - pivot[i]!) as [number, number, number]);
  const origin = moved.map((c, i) => c + pivot[i]! + offset[i]!) as [number, number, number];
  const u = direction(plane.xAxis), v = direction(plane.yAxis), n = direction(plane.normal);
  let profile = before.profile, height = before.height;
  let xAxis = unit(u), yAxis = unit(v), normal = unit(n);
  if (action.kind === "scale") {
    const along = dot(v, xAxis), across = v.map((c, i) => c - along * xAxis[i]!) as [number, number, number];
    yAxis = unit(across);
    if (height && (Math.abs(dot(normal, xAxis)) > 1e-6 || Math.abs(dot(normal, yAxis)) > 1e-6)) {
      throw new Error("Non-uniform scale would make the prism extrusion oblique to its profile.");
    }
    if (!height) {
      normal = [xAxis[1] * yAxis[2] - xAxis[2] * yAxis[1], xAxis[2] * yAxis[0] - xAxis[0] * yAxis[2], xAxis[0] * yAxis[1] - xAxis[1] * yAxis[0]];
      if (dot(normal, n) < 0) normal = normal.map(c => -c) as [number, number, number];
    }
    profile = profile.map(([x, y]) => [x * Math.hypot(...u) + y * along, y * Math.hypot(...across)] as const);
    height *= Math.hypot(...n);
  }
  const result = copySpec({ profile, height, base: origin[2], plane: { origin, xAxis, yAxis, normal }, closed: true });
  const changed = { profile: before.profile.length !== result.profile.length || before.profile.some((p, i) => p.some((n, j) => n !== result.profile[i]![j])),
    height: before.height !== result.height,
    work_plane: (["origin", "xAxis", "yAxis", "normal"] as const).some(key => plane[key].some((n, i) => n !== result.plane![key][i])) };
  for (const field of bound) if (changed[field]) throw new Error(`${field} is parameter-bound; edit its existing control instead of detaching it.`);
  return result;
}

export function createModelDraft(objects: readonly DraftObject[] = []): ModelDraftHistory {
  const captured = new Map(objects.map(object => [object.elementId, Object.freeze({ ...object,
    spec: object.spec && copySpec(object.spec), originalObjectNames: Object.freeze([...object.originalObjectNames]),
    parameterBoundFields: object.parameterBoundFields && [...object.parameterBoundFields] })]));
  return { snapshots: [{ commands: [], objects: captured }], index: 0 };
}
export const currentDraft = (history: ModelDraftHistory): DraftSnapshot => history.snapshots[history.index]!;
function differs(objects: ReadonlyMap<string, DraftObject>, initial: ReadonlyMap<string, DraftObject>): boolean {
  for (const id of new Set([...objects.keys(), ...initial.keys()])) {
    const left = objects.get(id), right = initial.get(id);
    const leftExists = left !== undefined && left.deleted !== true;
    const rightExists = right !== undefined && right.deleted !== true;
    if (leftExists !== rightExists) return true;
    if (leftExists && rightExists && (left!.componentId !== right!.componentId || !sameSpec(left!.spec, right!.spec))) return true;
  }
  return false;
}
export const snapshotsEquivalent = (left: DraftSnapshot, right: DraftSnapshot): boolean => !differs(left.objects, right.objects);
export const isDraftDirty = (history: ModelDraftHistory): boolean => !snapshotsEquivalent(currentDraft(history), history.snapshots[0]!);
export const undoDraft = (history: ModelDraftHistory): ModelDraftHistory => history.index > 0 ? { ...history, index: history.index - 1 } : history;
export const redoDraft = (history: ModelDraftHistory): ModelDraftHistory => history.index + 1 < history.snapshots.length ? { ...history, index: history.index + 1 } : history;

export function applyDraftCommand(history: ModelDraftHistory, command: DraftCommand): ModelDraftHistory {
  const prior = currentDraft(history), objects = new Map(prior.objects);
  const stored = structuredClone(command);
  if (stored.kind === "sketch") {
    if (objects.has(stored.elementId)) throw new Error("Drawing element id already exists.");
    objects.set(stored.elementId, { elementId: stored.elementId, componentId: stored.componentId,
      spec: specFromSketch(stored.action), originalObjectNames: [], created: true });
  } else {
    const object = objects.get(stored.elementId);
    if (!object || object.deleted) throw new Error("Pick an existing, undeleted model object.");
    if (stored.kind === "delete") objects.set(stored.elementId, { ...object, spec: null, deleted: true });
    else {
      if (!object.spec) throw new Error("This model object has no local face/prism projection for direct edits.");
      const spec = transformSpec(object, stored.action);
      if (stored.action.kind === "copy") {
        if (!stored.copyElementId || objects.has(stored.copyElementId)) throw new Error("Copy needs an unused stable element id.");
        objects.set(stored.copyElementId, { ...object, elementId: stored.copyElementId, spec, created: true, originalObjectNames: [] });
      } else objects.set(stored.elementId, { ...object, spec });
    }
  }
  let commands = [...prior.commands, stored];
  // A deleted local object can disappear from replay once no surviving copy
  // requires its creation. Iterate because removing a copy can release its source.
  let removed: boolean;
  do {
    removed = false;
    for (const [id, object] of objects) {
      if (!object.created || !object.deleted || commands.some(c => c.kind === "direct" && c.action.kind === "copy" && c.elementId === id)) continue;
      commands = commands.filter(c => (c.kind === "direct" && c.action.kind === "copy" ? c.copyElementId : c.elementId) !== id);
      objects.delete(id); removed = true;
    }
  } while (removed);
  if (!differs(objects, history.snapshots[0]!.objects)) commands = [];
  const snapshot: DraftSnapshot = { commands, objects };
  return { snapshots: [...history.snapshots.slice(0, history.index + 1), snapshot], index: history.index + 1 };
}
