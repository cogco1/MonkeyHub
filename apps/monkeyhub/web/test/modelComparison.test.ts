import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, OrthographicCamera, PerspectiveCamera, Sphere, Vector3, type Object3D } from "three";

import type { CompareDto, ModelSourceDto, ProjectArtifactDto, RuntimeCandidateDto, RuntimeDto } from "../src/api/project-runtime/generated";
import type { CameraState, ViewBounds } from "../src/workspaces/monkeyarch/viewer/ThreeDmViewport";
import { viewableArtifacts } from "../src/features/artifacts/artifactSelection.ts";
import { fitOrthographicBox, frameBoxKeepingView, projectionMode } from "../src/workspaces/monkeyarch/viewer/cameraProjection.ts";
import { fitDistance } from "../src/workspaces/monkeyarch/viewer/fitCamera.ts";
import {
  commonBounds,
  comparisonFor,
  comparisonPhase,
  createCameraLink,
  createComparisonHost,
  framingPane,
  originalChangedNames,
  resolveComparisonPair,
} from "../src/workspaces/monkeyarch/viewer/comparisonPair.ts";

const PROJECT = "pavilion";
const BASE_STATE = "b".repeat(64);
const RESULT_STATE = "c".repeat(64);
const HEAD_STATE = "e".repeat(64);

let sha = 0;
function model(runId: string, designStateDigest: string, overrides: Partial<ProjectArtifactDto> = {}): ProjectArtifactDto {
  const digest = (++sha).toString(16).padStart(64, "0");
  return {
    artifactId: digest, runId, stageId: "studio-candidate-seat-roof", fileName: `${runId}.preview.3dm`, relativePath: null,
    sha256: digest, sizeBytes: 10, objectCount: 11, status: "succeeded", readbackVerified: true, available: true,
    unavailableReason: null, base: { version: 0, stateSha256: "0".repeat(64) }, branchId: "runner-v1", branchEpoch: 1,
    programRef: null, programDigest: null, designStateDigest, lengthUnit: "meter", upAxis: "Z-up",
    receiptRef: `project://${PROJECT}/runs/${runId}/records/receipt.json`, format: "3dm", representation: "preview",
    ...overrides,
  } as ProjectArtifactDto;
}

function candidateRow(overrides: Partial<RuntimeCandidateDto> = {}): RuntimeCandidateDto {
  return {
    candidateId: "cand-roof", status: "completed", jobId: null, proposalId: null, base: { version: 0, stateSha256: "0".repeat(64) },
    baseRecordDigest: "r".repeat(64), baseStateDigest: BASE_STATE, resultRecordDigest: "s".repeat(64), resultStateDigest: RESULT_STATE,
    receiptRef: null, commitStageRefs: [], error: null, ...overrides,
  };
}

const runtime = (...candidates: RuntimeCandidateDto[]): Pick<RuntimeDto, "projectId" | "candidates"> => ({ projectId: PROJECT, candidates });
const listing = (...rows: ProjectArtifactDto[]) => ({ projectId: PROJECT, viewable: viewableArtifacts(rows) });

const baseModel = model("base-run", BASE_STATE);
const candidateModel = model("cand-roof", RESULT_STATE);
// What the screen or the tree might suggest instead: the Working Head's model, and the candidate's tree parent.
const headModel = model("head-run", HEAD_STATE);
const parentModel = model("tree-parent", "f".repeat(64));

test("the original is the run whose models carry the candidate's retained base state, not the model on screen or the head", () => {
  const result = resolveComparisonPair(PROJECT, "cand-roof", runtime(candidateRow()), listing(headModel, parentModel, candidateModel, baseModel));
  assert.ok("pair" in result);
  assert.equal(result.pair.base.runId, "base-run");
  assert.equal(result.pair.base.stateDigest, BASE_STATE);
  assert.deepEqual(result.pair.base.models.map((row) => row.sha256), [baseModel.sha256]);
  assert.equal(result.pair.candidate.runId, "cand-roof");
  assert.deepEqual(result.pair.candidate.models.map((row) => row.sha256), [candidateModel.sha256]);
});

test("a candidate's model is only the one at its own result state", () => {
  const stale = model("cand-roof", HEAD_STATE);
  const result = resolveComparisonPair(PROJECT, "cand-roof", runtime(candidateRow()), listing(stale, candidateModel, baseModel));
  assert.ok("pair" in result);
  assert.deepEqual(result.pair.candidate.models.map((row) => row.sha256), [candidateModel.sha256]);
});

test("a missing or unmatched retained base is refused by name, never replaced by a neighbour", () => {
  const all = listing(headModel, parentModel, candidateModel, baseModel);
  const refusal = (rows: ReturnType<typeof runtime>, list = all, projectId = PROJECT, id = "cand-roof") => {
    const result = resolveComparisonPair(projectId, id, rows, list);
    return "refusal" in result ? result.refusal : null;
  };
  assert.equal(refusal(runtime(candidateRow({ baseStateDigest: null }))), "no-retained-base", "a legacy result without a retained change");
  assert.equal(refusal(runtime(candidateRow({ baseStateDigest: "9".repeat(64) }))), "base-no-model", "an authored or model-less base");
  assert.equal(refusal(runtime(candidateRow()), listing(candidateModel, model("other-run", BASE_STATE), baseModel)), "base-ambiguous");
  assert.equal(refusal(runtime(candidateRow()), listing(model("cand-roof", BASE_STATE), candidateModel)), "base-ambiguous",
    "the candidate is never its own original");
  assert.equal(refusal(runtime()), "candidate-unknown");
  assert.equal(refusal(runtime(candidateRow({ status: "running" }))), "candidate-unfinished");
  assert.equal(refusal(runtime(candidateRow()), listing(baseModel, headModel)), "candidate-no-model");
  assert.equal(refusal({ projectId: "another", candidates: [candidateRow()] }), "project-changed");
  assert.equal(refusal(runtime(candidateRow()), { projectId: "another", viewable: all.viewable }), "project-changed");
});

test("each side is one complete model, or its seats once each; unservable files and work models are not shown", () => {
  const composed = model("base-run", BASE_STATE, { representation: "composed", stageId: null });
  const seatA = model("base-run", BASE_STATE, { stageId: "seat-a" });
  const seatB = model("base-run", BASE_STATE, { stageId: "seat-b" });
  const gone = model("base-run", BASE_STATE, { stageId: "seat-c", available: false });
  const workModel = model("base-run", BASE_STATE, { stageId: "seat-d", representation: "exact", sourceStepSha256: "5".repeat(64) });
  const pick = (...rows: ProjectArtifactDto[]) => {
    const result = resolveComparisonPair(PROJECT, "cand-roof", runtime(candidateRow()), listing(candidateModel, ...rows));
    return "pair" in result ? result.pair.base.models.map((row) => row.sha256) : result.refusal;
  };
  assert.deepEqual(pick(seatA, composed, seatB), [composed.sha256]);
  assert.deepEqual(pick(seatA, seatB, gone, workModel), [seatA.sha256, seatB.sha256]);
  assert.equal(pick(composed, model("base-run", BASE_STATE, { representation: "composed", stageId: null })), "base-ambiguous");
  assert.equal(pick(seatA, model("base-run", BASE_STATE, { stageId: "seat-a" })), "base-ambiguous", "two pictures of one seat");
});

test("a row whose two bindings disagree certifies neither; a row stating one binding keeps it", () => {
  // As the server writes a row: its modelSource is the same run, state and file as the row itself.
  const bound = (row: ProjectArtifactDto, source: Partial<ModelSourceDto> = {}): ProjectArtifactDto =>
    ({ ...row, modelSource: { runId: row.runId, stateDigest: row.designStateDigest!, assetSha256: row.sha256!, ...source } });
  const pick = (...rows: ProjectArtifactDto[]) => {
    const result = resolveComparisonPair(PROJECT, "cand-roof", runtime(candidateRow()), listing(...rows));
    return "pair" in result ? [result.pair.base.models.map((row) => row.sha256), result.pair.candidate.models.map((row) => row.sha256)] : result.refusal;
  };
  const exact = [[baseModel.sha256], [candidateModel.sha256]];
  assert.deepEqual(pick(bound(candidateModel), bound(baseModel)), exact, "both bindings, agreeing");
  assert.deepEqual(pick(candidateModel, baseModel), exact, "an older listing without modelSource");
  assert.deepEqual(pick(candidateModel, { ...bound(baseModel), designStateDigest: null }), exact, "a modelSource alone");

  const otherState = bound(model("base-run", HEAD_STATE), { stateDigest: BASE_STATE });
  assert.equal(pick(candidateModel, otherState), "base-no-model", "its modelSource says the base state, the export another");
  assert.equal(pick(candidateModel, bound(baseModel, { stateDigest: HEAD_STATE })), "base-no-model", "the export says the base state, its modelSource another");
  assert.equal(pick(candidateModel, bound(baseModel, { runId: "other-run" })), "base-no-model", "a modelSource of another run");
  assert.equal(pick(candidateModel, bound(baseModel, { assetSha256: "9".repeat(64) })), "base-no-model", "a modelSource of another file");
  assert.equal(pick(bound(candidateModel, { stateDigest: HEAD_STATE }), baseModel), "candidate-no-model", "the candidate's own row is held to the same");
  assert.deepEqual(pick(candidateModel, baseModel, bound(model("other-run", HEAD_STATE), { stateDigest: BASE_STATE })), exact,
    "a contradictory row names no second original");
});

test("the object comparison is used only when it answers for this exact pair", () => {
  const result = resolveComparisonPair(PROJECT, "cand-roof", runtime(candidateRow()), listing(candidateModel, baseModel));
  assert.ok("pair" in result);
  const compare: CompareDto = { candidateId: "cand-roof", against: "base-run", why: null, whySource: "unavailable", tolerance: 1e-6,
    changed: 2, unchanged: 1, added: 1, removed: 1, components: [], objects: [
      { name: "roof-slab", seatId: "s", componentId: "roof", producerOp: null, status: "changed", before: null, after: null },
      { name: "fascia", seatId: "s", componentId: "roof", producerOp: null, status: "changed", before: null, after: null },
      { name: "post-1", seatId: "s", componentId: "posts", producerOp: null, status: "unchanged", before: null, after: null },
      { name: "gutter", seatId: "s", componentId: "roof", producerOp: null, status: "added", before: null, after: null },
      { name: "old-rail", seatId: "s", componentId: "rail", producerOp: null, status: "removed", before: null, after: null },
    ] };
  assert.equal(comparisonFor(result.pair, compare), compare);
  assert.equal(comparisonFor(result.pair, { ...compare, against: "head-run" }), null, "counted against another run");
  assert.deepEqual(originalChangedNames(compare), ["roof-slab", "fascia", "old-rail"]);
  assert.equal(originalChangedNames(null), null, "without the server's names the outline is the whole original");
});

// ProjectWorkspace hands every change of the Hub's request, its own visibility and the comparison's phase to this host.
const onScreen = { active: true, ready: true };

test("a Hub request opens once; its ids only grow, so a late older one never reopens while a new id does", () => {
  const host = createComparisonHost();
  const first = { candidateRunId: "cand-roof", requestId: 1 };
  assert.equal(host.request(first, { active: true, ready: false }), "wait", "the project is still connecting");
  assert.equal(host.shown(), null);
  assert.equal(host.request(first, onScreen), "open");
  const opened = host.shown();
  assert.deepEqual([opened?.runId, opened?.origin], ["cand-roof", "request"]);
  assert.equal(host.request(first, onScreen), "none", "a re-render with the same request does not reopen it");
  assert.equal(host.request({ ...first }, onScreen), "none", "nor does the same id in a new object");
  assert.equal(host.shown(), opened);
  assert.equal(host.request({ candidateRunId: "cand-2", requestId: 2 }, onScreen), "open", "a newer request supersedes");
  const newer = host.shown();
  assert.equal(host.request(first, onScreen), "none", "1 arriving late after 2 opens nothing");
  assert.equal(host.shown(), newer);
  assert.equal(host.request({ candidateRunId: "cand-2", requestId: 3 }, onScreen), "open", "the person asks for the same candidate again");
  assert.notEqual(host.shown(), newer, "and gets a fresh comparison");
  assert.equal(host.shown()?.runId, "cand-2");

  const hidden = createComparisonHost();
  assert.equal(hidden.request(first, { active: false, ready: true }), "drop");
  assert.equal(hidden.request(first, onScreen), "none", "becoming active later does not install the old request");
  assert.equal(hidden.shown(), null);
  assert.equal(hidden.request({ candidateRunId: "cand-roof", requestId: 2 }, onScreen), "open", "one made while it is on screen opens");
});

test("a Hub that withdraws its request closes what it asked for, loading or shown, and is not told of a Back", () => {
  let told = 0;
  const tell = () => { told += 1; };
  const host = createComparisonHost();
  host.request({ candidateRunId: "cand-roof", requestId: 1 }, onScreen);
  const inFlight = host.shown()!.key;
  assert.equal(host.request(null, onScreen), "withdraw", "still finding its pair");
  assert.equal(host.shown(), null);
  host.report(inFlight, "shown");
  assert.equal(host.shown(), null, "what it read afterwards is not heard");
  host.request({ candidateRunId: "cand-roof", requestId: 2 }, onScreen);
  host.report(host.shown()!.key, "shown");
  assert.equal(host.request(null, onScreen), "withdraw", "on screen");
  assert.equal(host.shown(), null);
  assert.equal(host.request({ candidateRunId: "cand-roof", requestId: 2 }, onScreen), "none", "the withdrawn request does not come back");
  host.back(tell);
  assert.equal(told, 0, "a withdrawal is not a Back");

  host.open("cand-roof");
  assert.equal(host.request(null, onScreen), "none", "one the person opened from the tree is theirs");
  assert.equal(host.shown()?.origin, "tree");
  host.back(tell);
  assert.equal(told, 0, "Back from the tree's comparison is this project's own");
  host.request({ candidateRunId: "cand-roof", requestId: 3 }, onScreen);
  host.back(tell);
  assert.equal(told, 1, "Back from a requested one lets the Hub restore its own surface");
  assert.equal(host.shown(), null);
});

test("a comparison whose pair is found but whose models are still opening is abandoned when its project leaves", () => {
  const host = createComparisonHost();
  host.request({ candidateRunId: "cand-roof", requestId: 1 }, onScreen);
  const key = host.shown()!.key;
  const found = comparisonPhase("ready", { before: "loading", after: "idle" });
  assert.equal(found, "resolving", "the pair alone puts nothing on screen");
  host.report(key, found);
  host.leave();
  assert.equal(host.shown(), null);
  host.report(key, comparisonPhase("ready", { before: "ready", after: "ready" }));
  assert.equal(host.request({ candidateRunId: "cand-roof", requestId: 1 }, onScreen), "none", "back on screen");
  assert.equal(host.shown(), null, "the models that finished late never appear");

  host.request({ candidateRunId: "cand-roof", requestId: 2 }, onScreen);
  const settled = comparisonPhase("ready", { before: "ready", after: "error" });
  assert.equal(settled, "shown", "each view holds its model or says it could not open one");
  host.report(host.shown()!.key, settled);
  host.leave();
  assert.equal(host.shown()?.runId, "cand-roof", "a comparison that was on screen stays with its project");
  host.open("cand-roof");
  host.leave();
  assert.equal(host.shown(), null, "the tree's, still resolving, is abandoned too");
  assert.equal(comparisonPhase("refused", { before: "idle", after: "idle" }), "refused");
  assert.equal(comparisonPhase("failed", { before: "idle", after: "idle" }), "refused");
  assert.equal(comparisonPhase("resolving", { before: "ready", after: "ready" }), "resolving");
});

const view = (distance: number): CameraState => ({
  position: [distance, 0, 0], target: [0, 0, 0], up: [0, 0, 1], fov: 38, projection: "perspective", zoom: 1,
});

test("linked views stand in one common view, relay only a person's move, and keep both views when unlinked", () => {
  const link = createCameraLink();
  assert.equal(link.moved("before", view(3)), null, "loading fits are not relayed before both models are in");
  assert.deepEqual(link.ready(view(12)), { panes: ["before", "after"], camera: view(12) }, "the common view goes to both panes");
  assert.deepEqual(link.moved("before", view(8)), { panes: ["after"], camera: view(8) });
  assert.deepEqual(link.moved("after", view(7)), { panes: ["before"], camera: view(7) });
  assert.equal(link.setLinked(false), null, "turning the link off moves nothing");
  assert.equal(link.moved("after", view(5)), null);
  assert.equal(link.moved("before", view(4)), null);
  assert.deepEqual(link.setLinked(true), { panes: ["after"], camera: view(4) }, "on again, the other view joins the one moved last");
  assert.equal(link.setLinked(true), null, "already linked: nothing to apply");
  const loose = createCameraLink();
  loose.setLinked(false);
  assert.equal(loose.ready(view(12)), null, "unlinked while loading: each pane keeps its own fit");
  assert.deepEqual(loose.setLinked(true), { panes: ["before", "after"], camera: view(12) }, "linked before anyone moved: both take the common view");
});

// The viewer's own bounds, Fit and framing, run on Three as viewportFit.test.ts runs Fit; only the WebGL surface is a stand-in.
async function viewer() {
  const source = await readFile(new URL("../src/workspaces/monkeyarch/viewer/ThreeDmViewport.tsx", import.meta.url), "utf8");
  const start = source.indexOf("function boundsForRuntime("), end = source.indexOf("function errorMessage(");
  assert.ok(start >= 0 && end > start);
  return new Function("Box3", "Vector3", "Sphere", "fitDistance", "fitOrthographicBox", "frameBoxKeepingView", "projectionMode",
    `${stripTypeScriptTypes(source.slice(start, end))}; return { boundsForRuntime, fitRuntime, framingRuntime };`,
  )(Box3, Vector3, Sphere, fitDistance, fitOrthographicBox, frameBoxKeepingView, projectionMode) as {
    boundsForRuntime(runtime: Pane): Box3;
    fitRuntime(runtime: Pane): void;
    framingRuntime(runtime: Pane, bounds: ViewBounds): CameraState | null;
  };
}

/** One comparison pane as the viewer holds it: its own model, camera and frame. */
function pane(model: Object3D, [width, height]: readonly [number, number], camera: PerspectiveCamera | OrthographicCamera = new PerspectiveCamera(38, width / height, 0.01, 10000)) {
  const target = new Vector3();
  let touched = 0;
  return {
    model, camera, perspectiveCamera: camera instanceof PerspectiveCamera ? camera : new PerspectiveCamera(38, width / height, 0.01, 10000),
    draftRoot: new Group(), draftObjects: new Map(),
    controls: { target, update: () => { touched += 1; camera.lookAt(target); camera.updateMatrixWorld(); } },
    renderer: { domElement: { getBoundingClientRect: () => ({ width, height }) } },
    render: () => { touched += 1; },
    touched: () => touched,
  };
}
type Pane = ReturnType<typeof pane>;

/** The camera as the pane reports it (ThreeDmViewport's camera()). */
const standing = (runtime: Pane): CameraState => ({
  position: runtime.camera.position.toArray(), target: runtime.controls.target.toArray(), up: runtime.camera.up.toArray(),
  fov: runtime.perspectiveCamera.fov, projection: projectionMode(runtime.camera), zoom: runtime.camera.zoom,
});

/** Whether every corner of every box is inside a frame of this aspect, seen from where `state` stands. */
function frames(state: CameraState, aspect: number, boxes: readonly Box3[]): boolean {
  const camera = state.projection === "orthographic" ? new OrthographicCamera(-aspect, aspect, 1, -1, 0.01, 1e6)
    : new PerspectiveCamera(state.fov, aspect, 0.01, 1e6);
  camera.up.set(...state.up);
  camera.position.set(...state.position);
  camera.zoom = state.zoom;
  camera.lookAt(new Vector3(...state.target));
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return boxes.every((box) => [box.min.x, box.max.x].every((x) => [box.min.y, box.max.y].every((y) => [box.min.z, box.max.z].every((z) => {
    const point = new Vector3(x, y, z).project(camera);
    return Math.abs(point.x) <= 1 && Math.abs(point.y) <= 1 && Math.abs(point.z) < 1;
  }))));
}

test("the common view frames the original and a candidate moved far from it together, at one scale in both panes", async () => {
  const { boundsForRuntime, fitRuntime, framingRuntime } = await viewer();
  const block = (x: number, y: number) => {
    const mesh = new Mesh(new BoxGeometry(12, 8, 4), new MeshBasicMaterial());
    mesh.position.set(x, y, 2);
    return mesh;
  };
  // The candidate moved the whole building 45 m east and 20 m north of where its original stands.
  const original = block(6, 4), moved = block(51, 24);
  const boxes = [new Box3().setFromObject(original), new Box3().setFromObject(moved)];
  const toBounds = (box: Box3): ViewBounds => ({ min: box.min.toArray(), max: box.max.toArray() });
  // Side by side, side by side with a tall narrow candidate pane, and stacked in a 612 px panel.
  const sizes: readonly (readonly [readonly [number, number], readonly [number, number]])[] = [[[480, 320], [480, 320]], [[520, 300], [260, 420]], [[590, 280], [590, 280]]];
  for (const [beforeSize, afterSize] of sizes) {
    const before = pane(original, beforeSize), after = pane(moved, afterSize);
    fitRuntime(before);
    fitRuntime(after);
    const aspects = [beforeSize[0] / beforeSize[1], afterSize[0] / afterSize[1]];
    // Either pane's own fit, borrowed by the other, leaves one of the two models out: the view taken from one side.
    assert.equal(frames(standing(after), aspects[0]!, boxes), false);
    assert.equal(frames(standing(before), aspects[1]!, boxes), false);

    const bounds = commonBounds([toBounds(boundsForRuntime(before)), toBounds(boundsForRuntime(after))])!;
    assert.deepEqual(bounds, { min: [0, 0, 0], max: [57, 28, 4] });
    const narrower = aspects[1]! < aspects[0]! ? after : before, wider = narrower === after ? before : after;
    const framer = framingPane({ before: beforeSize, after: afterSize }) === "before" ? before : after;
    assert.equal(framer, narrower, "framed in the narrower pane");
    if (aspects[0] !== aspects[1]) {
      assert.equal(frames(framingRuntime(wider, bounds)!, Math.min(...aspects), boxes), false, "framed in the wider pane, the narrower one would cut them");
    }
    const pose = framer.camera.matrixWorld.clone(), touched = framer.touched();
    const shared = framingRuntime(framer, bounds)!;
    assert.deepEqual(framer.camera.matrixWorld, pose, "working out the view moves no camera");
    assert.equal(framer.touched(), touched, "and reports nothing");
    assert.equal(shared.projection, "perspective");
    assert.equal(shared.fov, 38);
    assert.deepEqual(shared.target, [28.5, 14, 2]);
    const direction = new Vector3(...shared.position).sub(new Vector3(...shared.target)).normalize();
    assert.ok(direction.distanceTo(new Vector3(1, -1, 0.78).normalize()) < 1e-9, "turned as Fit turned the panes");
    for (const aspect of aspects) assert.ok(frames(shared, aspect, boxes), `both models whole in a ${aspect.toFixed(2)} frame`);
  }

  // Without perspective the same view holds through zoom, and stays orthographic.
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0.01, 10000);
  const top = pane(original, [480, 320], camera);
  top.controls.target.copy(fitOrthographicBox(camera, boxes[0]!, 1.5, new Vector3(0, 0, 1), new Vector3(0, 1, 0)));
  assert.equal(frames(standing(top), 1.5, boxes), false);
  const plan = framingRuntime(top, commonBounds(boxes.map(toBounds))!)!;
  assert.equal(plan.projection, "orthographic");
  assert.ok(frames(plan, 1.5, boxes));
});

test("the comparison reads the project and never writes it", async () => {
  const source = await readFile(new URL("../src/workspaces/monkeyarch/viewer/ModelComparison.tsx", import.meta.url), "utf8");
  const calls = new Set([...source.matchAll(/\bstudio\.(\w+)\(/g)].map((match) => match[1]));
  assert.deepEqual([...calls].sort(), ["artifactFile", "artifacts", "compare", "runtime"]);
});
