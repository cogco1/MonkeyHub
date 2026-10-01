import assert from "node:assert/strict";
import test from "node:test";

import type { DesignHistoryDto, DesignStageDto, RepresentationStatusDto, SourceDocumentDto } from "../src/api/project-runtime/generated";
import { pageSourceDetails, pageSourceReason, pageSourceState, pageStageLabel, pageStatusShows, readStageLabels,
  type PageStatusRead } from "../src/workspaces/monkeyboard/boardSourceStatus.ts";

// #288: what the selected Board page says about its source. The Runtime's
// representation status is the answer; these checks hold the Board to showing
// it for exactly the selected page, and naming the page's Stage and time.
const PROJECT = "project-a";
const model = { runId: "model-run", stateDigest: "1".repeat(64), assetSha256: "2".repeat(64) };
function document(overrides: Partial<SourceDocumentDto> = {}): SourceDocumentDto {
  return {
    projectId: PROJECT, runId: "drawing-run", assetSha256: "a".repeat(64), fileName: "Ground floor.pdf",
    mimeType: "application/pdf", sizeBytes: 1200, pageCount: 1, pages: [{ pageIndex: 0, width: 1200, height: 800, rotation: 0 }],
    modelSource: model, sourceStageRef: "stage-1", generatedAt: "2026-09-26T12:00:00Z", ...overrides,
  };
}
const KEY = "drawing-run:aaaa:0";
const answer = (state: RepresentationStatusDto["state"], reason: string | null = null, projectId = PROJECT): PageStatusRead =>
  ({ key: KEY, status: { projectId, state, reason } });

test("a page linked to no model says so whatever was read", () => {
  const drawing = document({ modelSource: null });
  assert.equal(pageSourceState(drawing, KEY, PROJECT, null), "unlinked");
  assert.equal(pageSourceState(drawing, KEY, PROJECT, answer("outdated")), "unlinked");
  assert.equal(pageSourceReason("unlinked", answer("outdated", "Changed.")), null);
});

test("the Runtime's answer is shown for exactly the selected page, in its own words", () => {
  const drawing = document();
  // Nothing read yet, or an answer about another page: still checking, never a guess.
  assert.equal(pageSourceState(drawing, KEY, PROJECT, null), "checking");
  assert.equal(pageSourceState(drawing, KEY, PROJECT, { ...answer("current"), key: "another-page" }), "checking");
  for (const state of ["current", "outdated", "frozen", "unavailable"] as const) {
    assert.equal(pageSourceState(drawing, KEY, PROJECT, answer(state)), state);
  }
  // A failed read, or an answer from another project, cannot say the page matches.
  assert.equal(pageSourceState(drawing, KEY, PROJECT, { key: KEY, status: null }), "unavailable");
  assert.equal(pageSourceState(drawing, KEY, PROJECT, answer("current", null, "project-b")), "unavailable");
  // A page naming another project is never judged against this project's editing base.
  assert.equal(pageSourceState(document({ projectId: "project-b" }), KEY, PROJECT, answer("current")), "unavailable");
});

test("the owner's own reason is kept for the shown answer only", () => {
  const outdated = answer("outdated", "The project model has changed since this was made.");
  assert.equal(pageSourceReason("outdated", outdated), "The project model has changed since this was made.");
  assert.equal(pageSourceReason("checking", outdated), null);
  assert.equal(pageSourceReason("current", answer("current")), null);
  assert.equal(pageSourceReason("unavailable", { key: KEY, status: null }), null);
});

test("the status is read again only when what it is derived from moves", () => {
  for (const id of ["run:drawing-run", "tree", "working", "area:head"]) assert.equal(pageStatusShows(id), true, id);
  // Each Board save and page ink is kept aside; a thumbnail and Modeling's autosave pointer change nothing it reads.
  for (const id of ["aside:studio-board", "projections:page-1", "area:working"]) assert.equal(pageStatusShows(id), false, id);
});

const stage = (stageRef: string, label: string, branchId = "main"): DesignStageDto => ({
  stageRef, label, branchId, parentStageRef: null, candidateId: `${stageRef}-run`, modelSource: model,
  recordDigest: "3".repeat(64), acceptedBy: "architect",
});
function history(branchId: string, stages: DesignStageDto[], projectId = PROJECT): DesignHistoryDto {
  return { projectId, branchId, branches: [{ branchId: "main", parentBranch: null, forkStageRef: "stage-0", headStageRef: "stage-1" },
    { branchId: "fork", parentBranch: "main", forkStageRef: "stage-0", headStageRef: "stage-f" }], stages } as unknown as DesignHistoryDto;
}

test("the page's Stage is named from the main line first, then from the branch that holds it", async () => {
  const reads: string[] = [];
  const histories: Record<string, DesignHistoryDto> = {
    main: history("main", [stage("stage-0", "S0"), stage("stage-1", "Atrium study")]),
    fork: history("fork", [stage("stage-0", "S0"), stage("stage-f", "Courtyard option", "fork")]),
  };
  const read = async (branchId = "main") => { reads.push(branchId); return histories[branchId]; };
  const onMain = await readStageLabels(read, PROJECT, "stage-1");
  assert.equal(onMain.get("stage-1"), "Atrium study");
  assert.deepEqual(reads, ["main"], "A Stage on the main line reads no other branch");
  reads.length = 0;
  const onFork = await readStageLabels(read, PROJECT, "stage-f");
  assert.equal(onFork.get("stage-f"), "Courtyard option");
  assert.deepEqual(reads, ["main", "fork"]);
  // A history answered for another project names nothing here.
  const foreign = await readStageLabels(async () => history("main", [stage("stage-1", "Elsewhere")], "project-b"), PROJECT, "stage-1");
  assert.equal(foreign.size, 0);
});

test("the card names the page's Stage and time, not its raw source", () => {
  const drawing = document();
  const named = pageSourceDetails(drawing, pageStageLabel(drawing, new Map([["stage-1", "Atrium study"]])), "en");
  assert.match(named, /^Stage · Atrium study · Generated Sep 26, 2026, /);
  assert.doesNotMatch(named, /stage-1|model-run|1111/);
  // Looked for and not found: said so. Not yet looked for: only the time.
  assert.match(pageSourceDetails(drawing, pageStageLabel(drawing, new Map([["stage-1", null]])), "en"), /^Stage · Unknown stage · Generated /);
  assert.match(pageSourceDetails(drawing, pageStageLabel(drawing, new Map()), "en"), /^Generated Sep 26, 2026, /);
  assert.match(pageSourceDetails(drawing, "Atrium study", "zh-CN"), /^阶段 · Atrium study · 生成于 2026年9月26日/);
  // A page naming no Stage and no time shows neither; an unreadable time is left out.
  assert.equal(pageSourceDetails(document({ sourceStageRef: null, generatedAt: null }), undefined, "en"), "");
  assert.equal(pageSourceDetails(document({ generatedAt: "not a time" }), "Atrium study", "en"), "Stage · Atrium study");
});
