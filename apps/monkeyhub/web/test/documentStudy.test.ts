import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createServer } from "vite";
import type { StudyViewDto } from "../src/api/generated/types.gen.ts";

const source = { runId: "drawing", assetSha256: "a".repeat(64), revisionRef: null, pageIndex: 1 };
const evidence = [{ evidenceId: "trace-a", kind: "void", status: "confirmed", origin: "user", confidence: 1,
  geometry: { type: "polygon", points: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]] } }];
const view: StudyViewDto = { projectId: "project", runId: "study-page", studyId: "page", ledgerRef: "retained-ledger",
  previousRef: null, source: { ...source, pageWidth: 200, pageHeight: 100 }, evidence,
  measurements: [], relations: [], compositionGraph: {}, hypotheses: [], counterfactuals: [],
  derivationMethod: "StudyDerivation@1", canonicalStateChanged: false };

async function helpers(t: { after(callback: () => unknown): void }) {
  const vite = await createServer({ root: fileURLToPath(new URL("..", import.meta.url)), configFile: false,
    logLevel: "silent", server: { middlewareMode: true, watch: null } });
  t.after(() => vite.close());
  return vite.ssrLoadModule("/src/workspaces/monkeydiagram/documentStudy.ts");
}

test("a camel-case retained Study reopens only against its exact source page", async t => {
  const { studyDraftFromView, studyMatchesSource, studyDraftChanged } = await helpers(t);
  const draft = studyDraftFromView(view);
  assert.equal(draft.evidence[0].evidenceId, "trace-a");
  assert.deepEqual(draft.evidence[0].geometry.points, evidence[0].geometry.points);
  assert.equal(studyDraftChanged(draft, view), false);
  assert.equal(studyMatchesSource(view, source), true);
  for (const mismatch of [{ pageIndex: 0 }, { runId: "different" }, { assetSha256: "b".repeat(64) }, { revisionRef: "generated-revision" }]) {
    assert.equal(studyMatchesSource(view, { ...source, ...mismatch }), false);
  }
  draft.evidence[0].geometry.points[0][0] = 0.2;
  assert.equal(evidence[0].geometry.points[0][0], 0.1, "editing must not mutate the saved baseline used for actual-result inspection");
});

test("editing a confirmed contour requires confirmation again and keeps its identity and classification", async t => {
  const { studyEvidenceToGestures, studyEvidenceFromGestures } = await helpers(t);
  const gestures = studyEvidenceToGestures(evidence);
  const unchanged = studyEvidenceFromGestures(gestures, evidence);
  assert.equal(unchanged[0].status, "confirmed");
  gestures[0].points[0] = [0.15, 0.1];
  const corrected = studyEvidenceFromGestures(gestures, evidence);
  assert.equal(corrected[0].status, "proposed");
  assert.equal(corrected[0].kind, "void");
  assert.equal(corrected[0].evidenceId, "trace-a");
  assert.equal(corrected[0].origin, "user");
  assert.equal(evidence[0].geometry.points[0][0], 0.1);
  assert.equal(evidence[0].status, "confirmed");
});

test("erasures remain rejected evidence and an open curve never becomes a confirmed polygon", async t => {
  const { studyEvidenceToGestures, studyEvidenceFromGestures, addStudyTrace, createStudyDraft } = await helpers(t);
  assert.equal(studyEvidenceFromGestures([], evidence)[0].status, "rejected");
  const open = { ...studyEvidenceToGestures(evidence)[0], closed: false };
  const prior = studyEvidenceFromGestures([open], evidence);
  assert.equal(prior[0].status, "proposed");
  assert.equal(prior[0].kind, "void", "opening a contour for correction must not discard its evidence identity");
  assert.deepEqual(studyEvidenceFromGestures([{ ...open, id: "new-open" }], []), []);
  const blank = createStudyDraft("new-study");
  assert.equal(addStudyTrace(blank, open), blank);
});

test("research save strips computed outcomes and preserves unresolved human preference", async t => {
  const { createStudyDraft, studyResearchInput } = await helpers(t);
  const research = createStudyDraft("page").research;
  research.hypotheses = [
    { hypothesisId: "hypothesis-a", statement: "An explanation", evidenceIds: ["trace-a"], counterEvidenceIds: ["trace-b"],
      historicalSourceIds: [], assumptions: [], falsification: "A disconfirming result", competesWith: ["hypothesis-b"], status: "open" },
    { hypothesisId: "hypothesis-b", statement: "A retained older explanation", evidenceIds: ["trace-b"],
      historicalSourceIds: [], assumptions: [], falsification: "Another result", competesWith: ["hypothesis-a"], status: "open" },
  ];
  research.counterfactuals = [{ counterfactualId: "change-a", hypothesisIds: ["hypothesis-a"], targetEvidenceId: "trace-a",
    operation: "translate", parameters: { dx: 0.08, dy: 0, scale: 4 }, conditions: ["same page", ""], prediction: "Distance will change.", execute: true,
    actual: { status: "computed", evidence: [], relationSignatureSimilarity: 1 } }];
  research.designPrior = { priorId: "prior-a", patternId: "pattern-a", statement: "A provisional prior", hypothesisIds: ["hypothesis-a"],
    conditions: ["same programme"], preference: "", preferenceStatus: "unresolved", changedContext: null };
  research.completion = { ready: true, missing: [] };
  research.observations = { measurements: [{ area: 500 }] };
  const input = studyResearchInput(research);
  assert.equal("completion" in input, false);
  assert.equal("observations" in input, false);
  assert.equal("actual" in input.counterfactuals[0], false);
  assert.deepEqual(input.counterfactuals[0].parameters, { dx: 0.08, dy: 0 });
  assert.deepEqual(input.counterfactuals[0].conditions, ["same page"]);
  assert.equal(input.designPrior.preferenceStatus, "unresolved");
  assert.equal(input.designPrior.preference, "");
  assert.deepEqual(input.hypotheses[0].counterEvidenceIds, ["trace-b"]);
  assert.deepEqual(input.hypotheses[1].counterEvidenceIds, [], "old hypotheses without opposing refs remain editable");
  input.hypotheses[0].counterEvidenceIds.push("trace-c");
  assert.deepEqual(research.hypotheses[0].counterEvidenceIds, ["trace-b"], "preparing a save must not mutate retained opposing evidence");
  assert.ok(research.counterfactuals[0].actual, "saved actual evidence remains inspectable after preparing another request");
});

test("returning a saved research revision to editing does not silently rerun or endorse it", async t => {
  const { createStudyDraft, studyDraftFromView, studyDraftChanged } = await helpers(t);
  const research = createStudyDraft("page").research;
  const reopened = { ...view, research: { ...research, method: "retained-method", completion: { ready: false, missing: ["preferences"] } } };
  const draft = studyDraftFromView(reopened);
  assert.deepEqual(draft.research, research);
  assert.equal(studyDraftChanged(draft, reopened), false);
  assert.equal(draft.research.designPrior, null);
});

test("archived comparisons reopen their exact inputs while computed results never return as save inputs", async t => {
  const { createStudyDraft, studyDraftFromView, studyResearchInput, studyResearchResult, studyComparisonDefinitionKey } = await helpers(t);
  const research = createStudyDraft("page").research;
  const first = { studies: [{ studyId: "page", ledgerRef: "page-revision-1" }, { studyId: "other", ledgerRef: "other-revision-2" }] };
  const second = { studies: [{ studyId: "page", ledgerRef: "page-revision-3" }, { studyId: "other", ledgerRef: "other-revision-2" }] };
  research.comparisons = [first, second];
  const results = [{ method: "retained-method-1", pairwise: [] }, { method: "retained-method-2", pairwise: [] }];
  const saved = { ...view, research: { ...research, comparisonResults: results } };
  const draft = studyDraftFromView(saved);
  assert.deepEqual(draft.research.comparisons, [first, second]);
  assert.equal("comparisonResults" in draft.research, false);
  assert.deepEqual(studyResearchResult(saved).comparisonResults, results);
  draft.research.comparisons.shift();
  const remainingKey = studyComparisonDefinitionKey(draft.research.comparisons[0]);
  const retainedIndex = research.comparisons.findIndex(row => studyComparisonDefinitionKey(row) === remainingKey);
  assert.equal(studyResearchResult(saved).comparisonResults[retainedIndex].method, "retained-method-2",
    "removing an earlier comparison must not attach its result to the remaining definition");
  const input = studyResearchInput(draft.research);
  assert.deepEqual(input.comparisons, [second]);
  assert.equal("comparisonResults" in input, false);
  input.comparisons[0].studies[0].ledgerRef = "changed-draft-ref";
  assert.equal(saved.research.comparisons[1].studies[0].ledgerRef, "page-revision-3");
  const oldResearch = { ...research };
  delete oldResearch.comparisons;
  assert.deepEqual(studyResearchInput(oldResearch).comparisons, [], "pre-comparison research remains editable");
});
