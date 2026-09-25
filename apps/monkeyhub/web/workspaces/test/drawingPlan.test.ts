import assert from "node:assert/strict";
import test from "node:test";
import { defaultPlanForm, drawingDocumentKey, keptOnChosenVersion, latestRevisions, liveAction, planFormFromDocument } from "../src/workspaces/monkeydiagram/drawingPlan.ts";
import type { SourceDocumentDto } from "../src/api/generated";

test("a retained cut-plan restores its actual frame and keeps dimension identity, placement and hidden intent", () => {
  const dimension = { id: "door-dimension", entityRef: "entity:wall", openingId: "door", placement: { offsetMm: -12 } };
  const document = { runId: "model-A", assetSha256: "a".repeat(64), revisionRef: "old-revision", viewRecipe: {
    kind: "cut-plan", frame: { origin: [0, 0, 1500], far_depth: 1300, scale: "1:50", crop_uv: [-2, -3, 12, 8] },
    graphics: { cutLineMm: .5, visibleLineMm: .2, hatchSpacingMm: 3 }, dimensions: [dimension], hiddenObjectIds: ["hidden-object"],
  } } as SourceDocumentDto;
  const form = planFormFromDocument(document, "millimeter");
  assert.deepEqual(form, { cutHeight: 1500, bottom: 200, scaleDenominator: 50,
    cutLineMm: .5, visibleLineMm: .2, hatchSpacingMm: 3, dimensions: [dimension], dressing: [], cropUv: [-2, -3, 12, 8], hiddenObjectIds: ["hidden-object"] });
  form.dimensions[0].placement!.offsetMm = 20;
  assert.equal(dimension.placement.offsetMm, -12, "editing the form never mutates its saved source recipe");
  assert.notEqual(drawingDocumentKey(document), drawingDocumentKey({ ...document, revisionRef: "new-revision" }),
    "identical document bytes do not collapse revision identities");
});

test("the initial cut is expressed in the exact source length unit", () => {
  assert.equal(defaultPlanForm("meter").cutHeight, 1.2);
  assert.equal(defaultPlanForm("millimeter").cutHeight, 1200);
  assert.equal(defaultPlanForm("foot").cutHeight * .3048, 1.2);
  assert.equal(defaultPlanForm("inch").cutHeight * .0254, 1.2);
});

test("the newest revision of each drawing opens by default and older revisions stay history", () => {
  const revision = (drawingId: string | null, fileName: string, generatedAt: string) =>
    ({ runId: "r", assetSha256: generatedAt, revisionRef: generatedAt, drawingId, fileName, generatedAt }) as SourceDocumentDto;
  const plans = [revision("floor", "floor.png", "2026-09-24T01:00:00Z"), revision("floor", "floor.png", "2026-09-24T03:00:00Z"),
    revision("roof", "roof.png", "2026-09-24T02:00:00Z"), revision(null, "legacy.png", "2026-09-23T00:00:00Z")];
  assert.deepEqual(latestRevisions(plans).map((row) => row.generatedAt),
    ["2026-09-24T03:00:00Z", "2026-09-24T02:00:00Z", "2026-09-23T00:00:00Z"]);
});

test("a LIVE drawing rebinds once to the Working Head and never loops on its own broken anchors", () => {
  const head = { runId: "cand-2", stateDigest: "d".repeat(64), assetSha256: "e".repeat(64) };
  const moved = { status: "outdated" as const, bindingChanged: true, targetModelSource: head };
  assert.equal(liveAction({ live: true, dirty: false, attempted: false, status: moved }), "rebuild");
  assert.equal(liveAction({ live: true, dirty: false, attempted: true, status: moved }), "none", "one attempt per head");
  assert.equal(liveAction({ live: true, dirty: true, attempted: false, status: moved }), "none", "unsaved appearance edits are kept");
  assert.equal(liveAction({ live: false, dirty: false, attempted: false, status: moved }), "none", "an earlier revision is a frozen view");
  assert.equal(liveAction({ live: true, dirty: false, attempted: false,
    status: { status: "current", bindingChanged: true, targetModelSource: head } }), "rebuild", "unchanged inputs still rebind to the head");
  assert.equal(liveAction({ live: true, dirty: false, attempted: false,
    status: { status: "partially-broken", bindingChanged: false, targetModelSource: head } }), "none");
  assert.equal(liveAction({ live: true, dirty: false, attempted: false,
    status: { status: "outdated", bindingChanged: false, targetModelSource: null } }), "blocked", "a head without an exact STEP is stated");
  assert.equal(liveAction({ live: true, dirty: false, attempted: false,
    status: { status: "unknown", bindingChanged: true, targetModelSource: head } }), "none");
  assert.equal(liveAction({ live: true, dirty: false, attempted: false, status: null }), "none");
});

test("only a drawing made from a chosen version is kept off the Working Head", () => {
  assert.equal(keptOnChosenVersion(null), false);
  assert.equal(keptOnChosenVersion({ viewRecipe: null }), false);
  assert.equal(keptOnChosenVersion({ viewRecipe: { kind: "cut-plan" } }), false, "retained drawings without a choice follow");
  assert.equal(keptOnChosenVersion({ viewRecipe: { kind: "cut-plan", follow: "live" } }), false);
  assert.equal(keptOnChosenVersion({ viewRecipe: { kind: "cut-plan", follow: "frozen" } }), true);
});
