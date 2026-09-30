/** Disposable, local model edits. Only Sync may submit these commands to Studio. */
import type { DrawnShapeDto, ElementElevationDto } from "../../api/generated/types.gen";
import type { SketchPreview } from "../../workspaces/monkeyarch/viewer/ThreeDmViewport";
import type { DirectModelAction } from "./ModelEditPanel";
import type { FinishedSketch, PlanPoint, SketchPlane, SketchVector } from "./sketch";
import { preparePushPull } from "./pushPull";

export interface ElevationReference { readonly kind: "level" | "element-top"; readonly id: string; readonly offset: number }
export interface ElevationFacts {
  readonly base: number; readonly top: number; readonly height: number;
  readonly baseReference: ElevationReference | null; readonly topReference: ElevationReference | null;
}
export interface ElevationDatum { readonly levelId: string; readonly name: string; readonly elevation: number }
export const elevationFromProjection = (facts: ElementElevationDto | null | undefined): ElevationFacts | null => facts ? {
  ...facts, baseReference: facts.baseReference && { ...facts.baseReference, offset: facts.baseReference.offset ?? 0 },
  topReference: facts.topReference && { ...facts.topReference, offset: facts.topReference.offset ?? 0 },
} : null;
export type ElevationCommand = { readonly kind: "elevation"; readonly elementId: string;
  readonly action: "set-base" | "set-top" | "set-height" | "bind-base" | "bind-top" | "detach-base" | "detach-top" | "set-datum";
  readonly value?: number; readonly reference?: ElevationReference; readonly levelId?: string; readonly name?: string };

export interface DraftObject {
  readonly elementId: string;
  readonly componentId: string;
  readonly spec: SketchPreview | null;
  /** A null spec without this flag is an existing object with no local shape projection. */
  readonly deleted?: boolean;
  readonly originalObjectNames: readonly string[];
  readonly created: boolean;
  readonly parameterBoundFields?: DrawnShapeDto["parameterBoundFields"];
  readonly elevation?: ElevationFacts | null;
}
export type DraftCommand =
  | ElevationCommand
  | { readonly kind: "sketch"; readonly elementId: string; readonly componentId: string; readonly action: FinishedSketch }
  | { readonly kind: "direct"; readonly elementId: string; readonly action: DirectModelAction; readonly copyElementId?: string }
  | { readonly kind: "delete"; readonly elementId: string };
export interface DraftSnapshot {
  readonly commands: readonly DraftCommand[];
  readonly objects: ReadonlyMap<string, DraftObject>;
  readonly levels?: readonly ElevationDatum[];
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

/** Existing direct transforms use the world bounding-box centre, including extrusion. */
export function draftTransformCenter(spec: SketchPreview): [number, number, number] {
  const plane = planeOf(spec);
  const corners = spec.profile.map(([x, y]) => plane.origin.map((c, i) => c + x * plane.xAxis[i]! + y * plane.yAxis[i]!) as [number, number, number]);
  if (spec.height) corners.push(...corners.map(point => point.map((c, i) => c + spec.height * plane.normal[i]!) as [number, number, number]));
  return [0, 1, 2].map(i => (Math.min(...corners.map(p => p[i]!)) + Math.max(...corners.map(p => p[i]!))) / 2) as [number, number, number];
}

/** Mirrors edit_drawn_element arithmetic in CAD coordinates; it is not a geometry validation result. */
export function previewDirectModel(object: Pick<DraftObject, "spec" | "parameterBoundFields">, action: DirectModelAction): SketchPreview {
  const before = object.spec!;
  if (before.closed === false) throw new Error("Open model curves do not support direct face/prism edits.");
  const plane = planeOf(before), bound = object.parameterBoundFields ?? [];
  if (action.kind === "pushPull") {
    const result = preparePushPull(drawnShapeFromSpec(before, bound), action.normal ?? plane.normal).preview(action.distance);
    if (!result) throw new Error("Pull distance must be nonzero.");
    return copySpec(result);
  }
  const pivot = draftTransformCenter(before);
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

export function createModelDraft(objects: readonly DraftObject[] = [], levels: readonly ElevationDatum[] = []): ModelDraftHistory {
  const captured = new Map(objects.map(object => [object.elementId, Object.freeze({ ...object,
    spec: object.spec && copySpec(object.spec), originalObjectNames: Object.freeze([...object.originalObjectNames]),
    elevation: object.elevation && structuredClone(object.elevation),
    parameterBoundFields: object.parameterBoundFields && [...object.parameterBoundFields] })]));
  return { snapshots: [{ commands: [], objects: captured, levels: structuredClone(levels) }], index: 0 };
}
export const currentDraft = (history: ModelDraftHistory): DraftSnapshot => history.snapshots[history.index]!;
function differs(objects: ReadonlyMap<string, DraftObject>, initial: ReadonlyMap<string, DraftObject>): boolean {
  for (const id of new Set([...objects.keys(), ...initial.keys()])) {
    const left = objects.get(id), right = initial.get(id);
    const leftExists = left !== undefined && left.deleted !== true;
    const rightExists = right !== undefined && right.deleted !== true;
    if (leftExists !== rightExists) return true;
    if (leftExists && rightExists && (left!.componentId !== right!.componentId || !sameSpec(left!.spec, right!.spec) ||
        JSON.stringify([left!.elevation?.baseReference ?? null, left!.elevation?.topReference ?? null]) !==
        JSON.stringify([right!.elevation?.baseReference ?? null, right!.elevation?.topReference ?? null]))) return true;
  }
  return false;
}
export const snapshotsEquivalent = (left: DraftSnapshot, right: DraftSnapshot): boolean => !differs(left.objects, right.objects) &&
  JSON.stringify(left.levels ?? []) === JSON.stringify(right.levels ?? []);
export const isDraftDirty = (history: ModelDraftHistory): boolean => !snapshotsEquivalent(currentDraft(history), history.snapshots[0]!);
export const undoDraft = (history: ModelDraftHistory): ModelDraftHistory => history.index > 0 ? { ...history, index: history.index - 1 } : history;
export const redoDraft = (history: ModelDraftHistory): ModelDraftHistory => history.index + 1 < history.snapshots.length ? { ...history, index: history.index + 1 } : history;

export function applyDraftCommand(history: ModelDraftHistory, command: DraftCommand): ModelDraftHistory {
  const prior = currentDraft(history), objects = new Map(prior.objects);
  let levels = prior.levels ?? [];
  const stored = structuredClone(command);
  if (stored.kind === "elevation") {
    if (stored.action === "set-datum") {
      if (!stored.levelId || !Number.isFinite(stored.value)) throw new Error("Name a datum and enter a finite elevation.");
      const current = levels.find(level => level.levelId === stored.levelId);
      const level = { levelId: stored.levelId, name: stored.name?.trim() || current?.name || stored.levelId, elevation: stored.value! };
      levels = current ? levels.map(row => row === current ? level : row) : [...levels, level];
    } else {
      const object = objects.get(stored.elementId), before = object && elevationOf(object);
      if (!object || !before || !object.spec || object.deleted) throw new Error("Select an upright mass to edit its elevation.");
      if (object.parameterBoundFields?.some(field => field === "height" || field === "work_plane"))
        throw new Error("This mass has parameter controls; edit those controls to preserve its design relationships.");
      let { base, top, height, baseReference, topReference } = before;
      const value = stored.value;
      if (stored.action.startsWith("set-") && !Number.isFinite(value)) throw new Error("Enter a finite elevation or height.");
      if (stored.action === "set-base") {
        base = value!;
        if (baseReference) baseReference = { ...baseReference, offset: baseReference.offset + base - before.base };
        if (!topReference) top = base + height;
      } else if (stored.action === "set-top" || stored.action === "set-height") {
        top = stored.action === "set-top" ? value! : base + value!;
        if (topReference) topReference = { ...topReference, offset: topReference.offset + top - before.top };
      } else if (stored.action === "bind-base" || stored.action === "bind-top") {
        if (!stored.reference || !Number.isFinite(stored.reference.offset)) throw new Error("Choose a valid elevation reference.");
        if (stored.action === "bind-base") baseReference = stored.reference;
        else topReference = stored.reference;
      } else if (stored.action === "detach-base") baseReference = null;
      else if (stored.action === "detach-top") topReference = null;
      height = top - base;
      objects.set(object.elementId, { ...object, elevation: { base, top, height, baseReference, topReference } });
    }
  } else if (stored.kind === "sketch") {
    if (objects.has(stored.elementId)) throw new Error("Drawing element id already exists.");
    objects.set(stored.elementId, { elementId: stored.elementId, componentId: stored.componentId,
      spec: specFromSketch(stored.action), originalObjectNames: [], created: true });
  } else {
    const object = objects.get(stored.elementId);
    if (!object || object.deleted) throw new Error("Pick an existing, undeleted model object.");
    if (stored.kind === "delete") objects.set(stored.elementId, { ...object, spec: null, deleted: true });
    else {
      if (!object.spec) throw new Error("This model object has no local face/prism projection for direct edits.");
      if (object.elevation?.topReference) throw new Error("Use Base Z, Top Z or Height, or detach the top reference before a direct transform.");
      if (stored.action.kind !== "pushPull" && stored.action.kind !== "copy" && [...objects.values()].some(other =>
          !other.deleted && [other.elevation?.baseReference, other.elevation?.topReference].some(reference =>
            reference?.kind === "element-top" && reference.id === object.elementId)))
        throw new Error("Other masses follow this top. Use its elevation controls or detach those references before transforming it.");
      const spec = previewDirectModel(object, stored.action);
      const elevation = object.elevation;
      if (elevation?.baseReference && !sameVector(planeOf(spec).normal, [0, 0, 1]))
        throw new Error("Detach the base reference before tilting or reversing this mass.");
      const updated = elevation ? { ...elevation, base: spec.base, height: spec.height, top: spec.base + spec.height,
        baseReference: elevation.baseReference && { ...elevation.baseReference, offset: elevation.baseReference.offset + spec.base - elevation.base },
      } : elevation;
      if (stored.action.kind === "copy") {
        if (!stored.copyElementId || objects.has(stored.copyElementId)) throw new Error("Copy needs an unused stable element id.");
        objects.set(stored.copyElementId, { ...object, elementId: stored.copyElementId, spec, elevation: updated, created: true, originalObjectNames: [] });
      } else objects.set(stored.elementId, { ...object, spec, elevation: updated });
    }
  }
  resolveDraftElevations(objects, levels);
  let commands = [...prior.commands, stored];
  // A deleted local object can disappear from replay once no surviving copy
  // requires its creation. Iterate because removing a copy can release its source.
  let removed: boolean;
  do {
    removed = false;
    for (const [id, object] of objects) {
      if (!object.created || !object.deleted || commands.some(c =>
        (c.kind === "direct" && c.action.kind === "copy" && c.elementId === id) ||
        (c.kind === "elevation" && c.reference?.kind === "element-top" && c.reference.id === id))) continue;
      commands = commands.filter(c => (c.kind === "elevation" && c.action === "set-datum") ||
        (c.kind === "direct" && c.action.kind === "copy" ? c.copyElementId : c.elementId) !== id);
      objects.delete(id); removed = true;
    }
  } while (removed);
  if (snapshotsEquivalent({ commands, objects, levels }, history.snapshots[0]!)) commands = [];
  const snapshot: DraftSnapshot = { commands, objects, levels };
  return { snapshots: [...history.snapshots.slice(0, history.index + 1), snapshot], index: history.index + 1 };
}

/** Read only the upright prism subset supported by the server elevation editor. */
export function elevationOf(object: DraftObject): ElevationFacts | null {
  if (object.deleted || !object.spec || object.elevation === null) return null;
  const spec = object.spec, plane = planeOf(spec);
  if (spec.closed === false || spec.height <= 0 || !sameVector(plane.normal, [0, 0, 1]) ||
      Math.abs(plane.xAxis[2]) > 1e-9 || Math.abs(plane.yAxis[2]) > 1e-9) return null;
  if (object.elevation) return object.elevation;
  return { base: plane.origin[2], top: plane.origin[2] + spec.height, height: spec.height, baseReference: null, topReference: null };
}

/** Local projection of explicit retained references; Sync rechecks the same intent on the server. */
function resolveDraftElevations(objects: Map<string, DraftObject>, levels: readonly ElevationDatum[]): void {
  const resolved = new Set<string>(), visiting = new Set<string>();
  const factsOf = (object: DraftObject) => elevationOf(object) ?? (!object.deleted && object.elevation &&
    object.spec?.height === 0 && object.elevation.height === 0 && sameVector(planeOf(object.spec).normal, [0, 0, 1])
    ? object.elevation : null);
  const resolve = (id: string): ElevationFacts => {
    const object = objects.get(id), facts = object && factsOf(object);
    if (!object || !facts) throw new Error("The referenced mass is missing or cannot supply an upright top.");
    if (resolved.has(id)) return facts;
    if (visiting.has(id)) throw new Error("Elevation references cannot form a cycle.");
    visiting.add(id);
    const referenceValue = (reference: ElevationReference) => {
      const host = reference.kind === "element-top" ? resolve(reference.id) : null;
      if (host && host.height <= 0) throw new Error("A flat face cannot supply a mass top reference.");
      const value = host ? host.top : levels.find(level => level.levelId === reference.id)?.elevation;
      if (value === undefined) throw new Error("The selected elevation datum no longer exists.");
      return value + reference.offset;
    };
    const base = facts.baseReference ? referenceValue(facts.baseReference) : facts.base;
    const top = facts.topReference ? referenceValue(facts.topReference) : base + facts.height;
    const height = top - base;
    if (![base, top, height].every(Number.isFinite) || (height <= 1e-9 && !(height === 0 && object.spec!.height === 0)))
      throw new Error("Top Z must be above Base Z.");
    const next = { ...facts, base, top, height };
    const plane = planeOf(object.spec!);
    const spec = copySpec({ ...object.spec!, base, height, plane: { ...plane, origin: [plane.origin[0], plane.origin[1], base] } });
    if (!sameSpec(object.spec, spec) || (object.elevation && JSON.stringify(facts) !== JSON.stringify(next)))
      objects.set(id, { ...object, spec, elevation: next });
    visiting.delete(id); resolved.add(id);
    return next;
  };
  for (const [id, object] of objects) if (factsOf(object)) resolve(id);
}
