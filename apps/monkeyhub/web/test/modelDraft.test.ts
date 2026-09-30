import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import type { DrawnShapeDto } from "../src/api/generated/types.gen.ts";
import type { DraftCommand, DraftObject, ModelDraftHistory } from "../src/features/stage/modelDraft.ts";
import type { SketchPreview } from "../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx";

// Resolve the production module's one runtime import for Node's native TS runner.
const source = readFileSync(new URL("../src/features/stage/modelDraft.ts", import.meta.url), "utf8");
const module = stripTypeScriptTypes(source).replace('"./pushPull"', JSON.stringify(new URL("../src/features/stage/pushPull.ts", import.meta.url).href));
const { createModelDraft, currentDraft, applyDraftCommand, undoDraft, redoDraft, isDraftDirty,
  specFromDrawnShape, drawnShapeFromSpec, snapshotsEquivalent } = await import(`data:text/javascript,${encodeURIComponent(module)}`) as typeof import("../src/features/stage/modelDraft.ts");

const shape = (change: Partial<DrawnShapeDto> = {}): DrawnShapeDto => ({
  profile: [[0, 0], [3, 0], [3, 2], [0, 2]], height: 2,
  workPlane: { origin: [10, 5, 20], xAxis: [1, 0, 0], yAxis: [0, 0, 1], normal: [0, 1, 0] },
  parameterBoundFields: [], ...change,
});
const sourceObject = (change: Partial<DrawnShapeDto> = {}): DraftObject => {
  const recorded = shape(change);
  return { elementId: "existing", componentId: "part", created: false, originalObjectNames: ["obj-existing"],
    spec: specFromDrawnShape(recorded), parameterBoundFields: recorded.parameterBoundFields };
};
const spec = (history: ModelDraftHistory, id = "existing") => currentDraft(history).objects.get(id)!.spec!;
const near = (actual: number, expected: number) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const nearVector = (actual: readonly number[], expected: readonly number[]) => actual.forEach((n, i) => near(n, expected[i]!));
const corners = (spec: SketchPreview) => {
  const p = spec.plane!;
  return [0, spec.height].flatMap(height => spec.profile.map(([x, y]) =>
    p.origin.map((n, i) => n + x * p.xAxis[i]! + y * p.yAxis[i]! + height * p.normal[i]!)));
};
const draw = (id: string, height = 2): DraftCommand => ({ kind: "sketch", elementId: id, componentId: "part",
  action: { profile: [[0, 0], [3, 0], [3, 2], [0, 2]], base: 7, height } });
const direct = (history: ModelDraftHistory, action: Extract<DraftCommand, { kind: "direct" }>["action"], elementId = "existing", copyElementId?: string) =>
  applyDraftCommand(history, { kind: "direct", elementId, action, copyElementId });

test("draft conversion preserves recorded tilted placement and a flat producer's closing endpoint", () => {
  const s = Math.SQRT1_2;
  const recorded = shape({ height: 0, profile: [[0, 0], [3, 0], [3, 2], [0, 2], [0, 0]],
    workPlane: { origin: [12, 8, 23], xAxis: [1, 0, 0], yAxis: [0, s, s], normal: [0, -s, s] },
    parameterBoundFields: ["height"] });
  const display = specFromDrawnShape(recorded);
  assert.deepEqual(display.plane!.origin, [12, 23, 8]);
  assert.equal(display.profile.length, 4);
  assert.deepEqual(drawnShapeFromSpec(display, recorded.parameterBoundFields), recorded);
  nearVector(corners(display)[2]!, [15, 23 + 2 * s, 8 + 2 * s]);
});

test("two local pulls accumulate and undo/redo restores immutable source-bound snapshots", () => {
  const original = createModelDraft([sourceObject()]);
  const first = direct(original, { kind: "pushPull", distance: 1, normal: [0, 0, 1] });
  const captured = currentDraft(first);
  const second = direct(first, { kind: "pushPull", distance: 0.5, normal: [0, 0, 1] });
  assert.equal(spec(original).height, 2);
  assert.equal(spec(first).height, 3);
  assert.equal(spec(second).height, 3.5);
  assert.equal(currentDraft(undoDraft(second)), captured, "Sync can retain a prior snapshot while editing continues");
  assert.equal(currentDraft(redoDraft(undoDraft(second))), currentDraft(second));
  assert.deepEqual(currentDraft(second).objects.get("existing")!.originalObjectNames, ["obj-existing"]);
  assert.equal(currentDraft(second).commands.length, 2);
  assert.equal(isDraftDirty(undoDraft(first)), false);
  const branch = direct(undoDraft(second), { kind: "move", translation: [1, 0, 0] });
  assert.equal(redoDraft(branch), branch, "a new gesture retires only its abandoned redo branch");
  assert.equal(spec(second).height, 3.5, "an old Sync snapshot is not rewritten");
});

test("local drawing and deletion have no net Sync commands, while undo restores the drawing", () => {
  const empty = createModelDraft();
  const added = applyDraftCommand(empty, draw("new"));
  const moved = direct(added, { kind: "move", translation: [2, 3, 4] }, "new");
  const removed = applyDraftCommand(moved, { kind: "delete", elementId: "new" });
  assert.equal(isDraftDirty(removed), false);
  assert.equal(currentDraft(removed).commands.length, 0);
  assert.equal(currentDraft(removed).objects.size, 0);
  assert.equal(currentDraft(undoDraft(removed)), currentDraft(moved));
  assert.equal(spec(undoDraft(removed), "new").height, 2);
  const existing = createModelDraft([sourceObject()]);
  const deletion = applyDraftCommand(existing, { kind: "delete", elementId: "existing" });
  assert.equal(currentDraft(deletion).objects.get("existing")!.spec, null);
  assert.equal(isDraftDirty(deletion), true);
  assert.deepEqual(currentDraft(deletion).commands, [{ kind: "delete", elementId: "existing" }]);
});

test("copies retain their stable IDs and deleted local sources stay in replay only while needed", () => {
  let history = applyDraftCommand(createModelDraft(), draw("source"));
  history = direct(history, { kind: "copy", translation: [5, 0, 0] }, "source", "copy-a");
  const copied = currentDraft(history);
  assert.equal(currentDraft(redoDraft(undoDraft(history))), copied);
  assert.deepEqual(currentDraft(history).objects.get("copy-a")!.originalObjectNames, []);
  assert.equal(spec(history, "copy-a").plane!.origin[0], 5);
  history = applyDraftCommand(history, { kind: "delete", elementId: "source" });
  assert.equal(currentDraft(history).commands.length, 3, "copy replay still requires its deleted source");
  assert.equal(isDraftDirty(history), true);
  history = applyDraftCommand(history, { kind: "delete", elementId: "copy-a" });
  assert.equal(currentDraft(history).commands.length, 0, "removing the final copy also releases its source");
  assert.equal(isDraftDirty(history), false);
  const source = createModelDraft([sourceObject()]);
  assert.throws(() => direct(source, { kind: "copy", translation: [1, 0, 0] }), /stable element id/);
  assert.throws(() => direct(source, { kind: "copy", translation: [1, 0, 0] }, "existing", "existing"), /stable element id/);
});

test("translation, positive CAD rotation and mirrored nonuniform scaling keep the actual object centre", () => {
  const initial = createModelDraft([sourceObject()]);
  const moved = direct(initial, { kind: "move", translation: [1, 2, 3] });
  nearVector(spec(moved).plane!.origin, [11, 22, 8]);
  assert.deepEqual(spec(moved).profile, spec(initial).profile);
  const turned = direct(initial, { kind: "rotate", angleDegrees: 90, axis: [0, 0, 1] });
  nearVector(corners(spec(turned))[0]!, [12.5, 19.5, 5]);
  nearVector(spec(turned).plane!.xAxis, [0, 1, 0]);
  const scaled = direct(initial, { kind: "scale", scale: [-2, 3, 0.5] });
  const points = corners(spec(scaled));
  nearVector([0, 1, 2].map(i => Math.min(...points.map(p => p[i]!))), [8.5, 18, 5.5]);
  nearVector([0, 1, 2].map(i => Math.max(...points.map(p => p[i]!))), [14.5, 24, 6.5]);
  near(spec(scaled).height, 1);
  const restored = direct(moved, { kind: "move", translation: [-1, -2, -3] });
  assert.equal(isDraftDirty(restored), false);
  assert.equal(currentDraft(restored).commands.length, 0);
});

test("a downward drawing and bottom pulls preserve their opposite end", () => {
  const down = applyDraftCommand(createModelDraft(), draw("down", -2));
  assert.equal(spec(down, "down").height, 2);
  nearVector(spec(down, "down").plane!.normal, [0, 0, -1]);
  const points = corners(spec(down, "down"));
  near(Math.min(...points.map(p => p[2]!)), 5);
  near(Math.max(...points.map(p => p[2]!)), 7);
  const lower = direct(createModelDraft([sourceObject()]), { kind: "pushPull", distance: 1, normal: [0, 0, -1] });
  near(spec(lower).plane!.origin[2], 4);
  near(spec(lower).height, 3);
  const face = direct(lower, { kind: "pushPull", distance: -3, normal: [0, 0, 1] });
  assert.equal(spec(face).height, 0);
  assert.equal(drawnShapeFromSpec(spec(face)).profile.length, 5);
});

test("invalid direct operations leave history unchanged and preserve parameter bindings", () => {
  const initial = createModelDraft([sourceObject({ parameterBoundFields: ["height"] })]);
  const snapshot = currentDraft(initial);
  assert.throws(() => direct(initial, { kind: "pushPull", distance: 1, normal: [0, 0, 1] }), /parameter-bound/);
  assert.throws(() => direct(initial, { kind: "scale", scale: [1, 1, 2] }), /parameter-bound/);
  assert.throws(() => direct(initial, { kind: "scale", scale: [1, 0, 1] }), /nonzero/);
  assert.throws(() => direct(initial, { kind: "move", translation: [NaN, 0, 0] }), /finite/);
  assert.equal(currentDraft(initial), snapshot);
  assert.equal(isDraftDirty(initial), false);
  assert.doesNotThrow(() => direct(initial, { kind: "move", translation: [1, 0, 0] }), "bound height allows translation");
  const locked = createModelDraft([sourceObject({ parameterBoundFields: ["work_plane"] })]);
  assert.throws(() => direct(locked, { kind: "rotate", angleDegrees: 90, axis: [0, 0, 1] }), /parameter-bound/);
  const s = Math.SQRT1_2;
  const plane = { origin: [0, 0, 0], xAxis: [1, 0, 0], yAxis: [0, s, s], normal: [0, -s, s] } as DrawnShapeDto["workPlane"];
  const sloped = createModelDraft([sourceObject({ workPlane: plane })]);
  assert.throws(() => direct(sloped, { kind: "scale", scale: [1, 2, 1] }), /oblique/);
  const flat = createModelDraft([sourceObject({ workPlane: plane, height: 0,
    profile: [[0, 0], [3, 0], [3, 2], [0, 2], [0, 0]] })]);
  assert.doesNotThrow(() => direct(flat, { kind: "scale", scale: [1, 2, 1] }), "a flat sloping face can reparameterize without an oblique extrusion");
});

test("open curves remain unfilled local objects and refuse unsupported direct edits", () => {
  const action = { profile: [[0, 0], [3, 2], [4, 2]] as [number, number][], base: 9, height: 0, closed: false };
  const history = applyDraftCommand(createModelDraft(), { kind: "sketch", elementId: "curve", componentId: "part", action });
  action.profile[0]![0] = 99;
  assert.deepEqual(spec(history, "curve").profile[0], [0, 0], "caller changes cannot rewrite a completed action");
  assert.equal(spec(history, "curve").closed, false);
  assert.equal(spec(history, "curve").base, 9);
  assert.throws(() => direct(history, { kind: "move", translation: [1, 0, 0] }, "curve"), /Open model curves/);
  assert.throws(() => drawnShapeFromSpec(spec(history, "curve")), /Open model curves/);
  assert.equal(isDraftDirty(applyDraftCommand(history, { kind: "delete", elementId: "curve" })), false);
});

test("unprojected original objects can be deleted and restored without inventing a shape", () => {
  const initial = createModelDraft([{ elementId: "wall", componentId: "envelope", spec: null,
    created: false, originalObjectNames: ["wall-solid", "wall-frame"] }]);
  assert.equal(isDraftDirty(initial), false);
  assert.throws(() => direct(initial, { kind: "move", translation: [1, 0, 0] }, "wall"), /no local face\/prism projection/);
  const deleted = applyDraftCommand(initial, { kind: "delete", elementId: "wall" });
  assert.equal(isDraftDirty(deleted), true);
  assert.equal(currentDraft(deleted).objects.get("wall")!.deleted, true);
  assert.deepEqual(currentDraft(deleted).objects.get("wall")!.originalObjectNames, ["wall-solid", "wall-frame"]);
  assert.equal(currentDraft(deleted).objects.get("wall")!.spec, null);
  assert.equal(currentDraft(undoDraft(deleted)), currentDraft(initial));
  assert.throws(() => applyDraftCommand(deleted, { kind: "delete", elementId: "wall" }), /undeleted/);
});

test("Sync comparison follows net object state, independently of snapshot identity or undo route", () => {
  const initial = createModelDraft([sourceObject()]);
  const first = direct(initial, { kind: "move", translation: [1, 0, 0] });
  const synced = currentDraft(first);
  const second = direct(first, { kind: "move", translation: [2, 0, 0] });
  const back = direct(second, { kind: "move", translation: [-2, 0, 0] });
  assert.notEqual(currentDraft(back), synced);
  assert.equal(snapshotsEquivalent(currentDraft(back), synced), true);
  assert.equal(snapshotsEquivalent(currentDraft(second), synced), false);
  assert.equal(snapshotsEquivalent(currentDraft(undoDraft(second)), synced), true);
  const anotherId = createModelDraft([{ ...sourceObject(), elementId: "another" }]);
  assert.equal(snapshotsEquivalent(currentDraft(initial), currentDraft(anotherId)), false, "geometrically identical objects retain distinct IDs");
});

const elevationEdit = (history: ModelDraftHistory, elementId: string, action: any, options = {}) =>
  applyDraftCommand(history, { kind: "elevation", elementId, action, ...options });
const mass = (elementId: string, base = 0, height = 3): DraftObject => ({ elementId, componentId: "part", created: true,
  originalObjectNames: [], spec: { profile: [[0, 0], [3, 0], [3, 2], [0, 2]], base, height } });

test("explicit stacked masses propagate while free neighbors, immutable undo and redo stay independent", () => {
  const initial = createModelDraft([mass("lower"), mass("upper", 8, 2), mass("free", 12)]);
  const bound = elevationEdit(initial, "upper", "bind-base", { reference: { kind: "element-top", id: "lower", offset: 0 } });
  near(spec(bound, "upper").base, 3);
  const changed = elevationEdit(bound, "lower", "set-height", { value: 5 });
  near(spec(changed, "upper").base, 5); near(spec(changed, "upper").height, 2);
  near(spec(changed, "free").base, 12); near(spec(initial, "upper").base, 8);
  assert.equal(currentDraft(undoDraft(changed)), currentDraft(bound));
  assert.equal(currentDraft(redoDraft(undoDraft(changed))), currentDraft(changed));
  const detached = elevationEdit(changed, "upper", "detach-base");
  const moved = elevationEdit(detached, "lower", "set-base", { value: -2 });
  near(spec(moved, "upper").base, 5); near(spec(moved, "lower").base, -2);
});

test("datum edits and numeric overrides preserve offsets and height/top algebra", () => {
  let history = createModelDraft([mass("mass", 1, 3)], [{ levelId: "roof", name: "Roof", elevation: 10 }]);
  history = elevationEdit(history, "mass", "bind-top", { reference: { kind: "level", id: "roof", offset: 0 } });
  near(spec(history, "mass").height, 9);
  history = elevationEdit(history, "mass", "set-top", { value: 11 });
  assert.equal(currentDraft(history).objects.get("mass")!.elevation!.topReference!.offset, 1);
  history = elevationEdit(history, "mass", "set-base", { value: 2 });
  near(spec(history, "mass").height, 9);
  history = elevationEdit(history, "mass", "set-datum", { levelId: "roof", name: "Roof", value: 12 });
  near(spec(history, "mass").height, 11);
  history = elevationEdit(history, "mass", "set-height", { value: 8 });
  near(spec(history, "mass").height, 8);
  assert.equal(currentDraft(history).objects.get("mass")!.elevation!.topReference!.offset, -2);
  const invalid = history;
  assert.throws(() => elevationEdit(history, "mass", "set-base", { value: 11 }), /Top Z/);
  assert.equal(history, invalid);
  assert.equal(currentDraft(undoDraft(history)).levels![0]!.elevation, 12);
});

test("binding alone is dirty, cycles and dependency deletion are atomic, and datum creation is undoable", () => {
  const initial = createModelDraft([mass("lower"), mass("upper", 3)]);
  const bound = elevationEdit(initial, "upper", "bind-base", { reference: { kind: "element-top", id: "lower", offset: 0 } });
  assert.equal(isDraftDirty(bound), true);
  assert.throws(() => elevationEdit(bound, "lower", "bind-base", { reference: { kind: "element-top", id: "upper", offset: 0 } }), /cycle/);
  assert.throws(() => applyDraftCommand(bound, { kind: "delete", elementId: "lower" }), /missing/);
  near(spec(bound, "upper").base, 3);
  const datum = elevationEdit(bound, "lower", "set-datum", { levelId: "site", name: "Site low", value: -1.2 });
  assert.equal(currentDraft(datum).levels![0]!.elevation, -1.2);
  assert.deepEqual(currentDraft(undoDraft(datum)).levels, []);
  assert.equal(currentDraft(redoDraft(undoDraft(datum))), currentDraft(datum));
  const free = elevationEdit(bound, "upper", "detach-base");
  assert.equal(isDraftDirty(free), false, "bind and detach without moving returns to the original geometry and relationships");
});

test("retained free copies keep vertical displacement and base-bound direct edits preserve offsets", () => {
  const original = mass("mass", 2, 3);
  const free = createModelDraft([{ ...original, elevation: { base: 2, top: 5, height: 3, baseReference: null, topReference: null } }]);
  const copied = direct(free, { kind: "copy", translation: [1, 0, 4] }, "mass", "copy");
  near(spec(copied, "copy").base, 6); near(spec(copied, "mass").base, 2);
  const bound = elevationEdit(createModelDraft([original], [{ levelId: "ground", name: "Ground", elevation: 0 }]),
    "mass", "bind-base", { reference: { kind: "level", id: "ground", offset: 2 } });
  const moved = direct(bound, { kind: "move", translation: [1, 0, 4] }, "mass");
  near(spec(moved, "mass").base, 6);
  assert.equal(currentDraft(moved).objects.get("mass")!.elevation!.baseReference!.offset, 6);
  const pulled = direct(moved, { kind: "pushPull", normal: [0, 0, 1], distance: 2 }, "mass");
  near(spec(pulled, "mass").height, 5);
  const flat = direct(pulled, { kind: "pushPull", normal: [0, 0, 1], distance: -5 }, "mass");
  near(spec(flat, "mass").height, 0);
  const raised = direct(flat, { kind: "pushPull", normal: [0, 0, 1], distance: 2 }, "mass");
  near(spec(raised, "mass").height, 2);
  assert.equal(currentDraft(raised).objects.get("mass")!.elevation!.baseReference!.id, "ground");
});

test("a deleted local source survives replay when earlier elevation bindings need it", () => {
  let history = applyDraftCommand(createModelDraft(), draw("lower"));
  history = applyDraftCommand(history, draw("upper"));
  history = elevationEdit(history, "upper", "bind-base", { reference: { kind: "element-top", id: "lower", offset: 0 } });
  history = elevationEdit(history, "upper", "detach-base");
  history = applyDraftCommand(history, { kind: "delete", elementId: "lower" });
  assert.equal(currentDraft(history).commands.length, 5);
  assert.equal(currentDraft(history).commands[0]!.kind, "sketch");
  assert.equal(currentDraft(history).objects.get("lower")!.deleted, true);
  near(spec(history, "upper").base, 9);
});

test("top-constrained and supporting-mass transforms refuse locally just as the producer does", () => {
  const original = createModelDraft([mass("mass"), mass("upper", 4)], [{ levelId: "roof", name: "Roof", elevation: 8 }]);
  const bound = elevationEdit(original, "mass", "bind-top", { reference: { kind: "level", id: "roof", offset: 0 } });
  assert.throws(() => direct(bound, { kind: "move", translation: [1, 0, 0] }, "mass"), /top reference/);
  near(spec(bound, "mass").height, 8);
  const stack = elevationEdit(original, "upper", "bind-base", { reference: { kind: "element-top", id: "mass", offset: 0 } });
  assert.throws(() => direct(stack, { kind: "move", translation: [1, 0, 0] }, "mass"), /Other masses/);
  const taller = direct(stack, { kind: "pushPull", normal: [0, 0, 1], distance: 1 }, "mass");
  near(spec(taller, "upper").base, 4);
});

test("a flattened bound face follows datum changes but cannot supply a mass top", () => {
  const original = createModelDraft([mass("mass")], [{ levelId: "ground", name: "Ground", elevation: 0 }]);
  const bound = elevationEdit(original, "mass", "bind-base", { reference: { kind: "level", id: "ground", offset: 0 } });
  const flat = direct(bound, { kind: "pushPull", normal: [0, 0, 1], distance: -3 }, "mass");
  const moved = elevationEdit(flat, "mass", "set-datum", { levelId: "ground", value: 2 });
  near(spec(moved, "mass").base, 2); near(spec(moved, "mass").height, 0);
  const solid = direct(moved, { kind: "pushPull", normal: [0, 0, 1], distance: 4 }, "mass");
  near(spec(solid, "mass").base, 2); near(spec(solid, "mass").height, 4);
});
