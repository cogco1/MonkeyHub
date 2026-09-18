import assert from "node:assert/strict";
import test from "node:test";

import type { ProjectArtifactDto } from "../src/api/generated";
import {
  artifactKindKey,
  isViewable,
  isWorkModel,
  viewableArtifacts,
  workModelOf,
  workModelSourceOf,
} from "../src/features/artifacts/artifactSelection.ts";

function row(overrides: Partial<ProjectArtifactDto>): ProjectArtifactDto {
  return {
    artifactId: "a".repeat(64),
    runId: "studio-cand-1",
    stageId: "studio-candidate-seat-portico",
    fileName: "seat.preview.3dm",
    relativePath: null,
    sha256: "a".repeat(64),
    sizeBytes: null,
    objectCount: 2,
    status: "succeeded",
    readbackVerified: true,
    available: true,
    unavailableReason: null,
    base: { version: 0, stateSha256: "b".repeat(64) },
    branchId: "runner-v1",
    branchEpoch: 1,
    programRef: null,
    programDigest: null,
    designStateDigest: null,
    lengthUnit: "meter",
    upAxis: "Z-up",
    receiptRef: "project://demo/runs/studio-cand-1/records/seat-occt-execution-x.json",
    format: "3dm",
    representation: "preview",
    ...overrides,
  } as ProjectArtifactDto;
}

const preview = row({});
const exactStep = row({
  artifactId: "c".repeat(64),
  sha256: "c".repeat(64),
  fileName: "seat.step",
  format: "step",
  representation: "exact",
});
const rhino = row({
  artifactId: "d".repeat(64),
  sha256: "d".repeat(64),
  fileName: "seat.3dm",
  format: "3dm",
  representation: "exact",
  receiptRef: "project://demo/runs/run-001/records/seat-rhino-execution-y.json",
});

test("only a 3dm is handed to the viewer; the exact STEP is for saving", () => {
  assert.equal(isViewable(preview), true);
  assert.equal(isViewable(rhino), true);
  assert.equal(isViewable(exactStep), false);
});

test("one receipt's two files become one picture: the preview, not the STEP, and never twice", () => {
  const shown = viewableArtifacts([exactStep, preview, rhino]);
  assert.deepEqual(
    shown.map((item) => item.fileName),
    ["seat.preview.3dm", "seat.3dm"],
  );
});

test("an unavailable or digestless preview is not shown", () => {
  assert.deepEqual(viewableArtifacts([row({ available: false, unavailableReason: "file missing" })]), []);
  assert.deepEqual(viewableArtifacts([row({ sha256: null, artifactId: "receipt:x" })]), []);
});

test("what a file is comes off the receipt's words, and a preview is never called exact", () => {
  assert.equal(artifactKindKey(exactStep), "artifact.kind.exactStep");
  assert.equal(artifactKindKey(preview), "artifact.kind.previewMesh");
  assert.equal(artifactKindKey(rhino), "artifact.kind.exact3dm");
  // A preview stays a preview whatever its format string says.
  assert.equal(artifactKindKey(row({ format: "step", representation: "preview" })), "artifact.kind.previewMesh");
});

const workModel = row({
  artifactId: "e".repeat(64),
  sha256: "e".repeat(64),
  fileName: "seat.work.3dm",
  format: "3dm",
  representation: "exact",
  sourceStepSha256: "c".repeat(64),
  receiptRef: "project://demo/runs/studio-cand-1/records/seat-rhino-execution-work.json",
});

test("an editable copy is the same delivery, so the viewer is not handed it twice", () => {
  assert.equal(isWorkModel(workModel), true);
  assert.equal(isWorkModel(rhino), false);
  const shown = viewableArtifacts([exactStep, preview, workModel, rhino]);
  assert.deepEqual(
    shown.map((item) => item.fileName),
    ["seat.preview.3dm", "seat.3dm"],
  );
  assert.equal(artifactKindKey(workModel), "artifact.kind.workModel");
});

test("the source is the exact STEP of the model actually on screen", () => {
  const rows = [exactStep, preview, rhino, workModel];
  const found = workModelSourceOf(rows, { runId: "studio-cand-1", shas: [preview.sha256!] });
  assert.deepEqual("source" in found && found.source.fileName, "seat.step");
  // and the copy already made from it is found by digest, not by name
  assert.equal(workModelOf(rows, exactStep)?.fileName, "seat.work.3dm");
  assert.equal(workModelOf([exactStep, preview], exactStep), null);
});

test("nothing on screen, a registered model, or several seats: no source is invented", () => {
  const rows = [exactStep, preview, rhino];
  assert.deepEqual(workModelSourceOf(rows, { runId: null, shas: [] }), { refusal: "nothing-loaded" });
  assert.deepEqual(
    workModelSourceOf(rows, { runId: "studio-cand-1", shas: ["f".repeat(64)] }),
    { refusal: "not-an-export" },
  );
  // Two seats of one run on screen: which delivery would be exported is not
  // for this code to decide.
  const second = row({
    artifactId: "9".repeat(64), sha256: "9".repeat(64), fileName: "seat-two.preview.3dm",
    receiptRef: "project://demo/runs/studio-cand-1/records/seat-occt-execution-two.json",
  });
  assert.deepEqual(
    workModelSourceOf([...rows, second], { runId: "studio-cand-1", shas: [preview.sha256!, second.sha256!] }),
    { refusal: "several-seats" },
  );
  // Two seats are on screen while the listing still holds only one of them:
  // the picture is what decides, so no single delivery is offered.
  assert.deepEqual(
    workModelSourceOf([exactStep, preview], { runId: "studio-cand-1", shas: [preview.sha256!, "9".repeat(64)] }),
    { refusal: "several-seats" },
  );
  // The same digest listed twice is still one model on screen.
  assert.deepEqual(
    "source" in workModelSourceOf(rows, { runId: "studio-cand-1", shas: [preview.sha256!, preview.sha256!] }),
    true,
  );
  // A composed model has no exact STEP under its own receipt.
  const composed = row({
    artifactId: "8".repeat(64), sha256: "8".repeat(64), fileName: "composed.3dm",
    representation: "composed", receiptRef: "project://demo/runs/studio-cand-1/records/studio-model-asset-z.json",
  });
  assert.deepEqual(
    workModelSourceOf([composed], { runId: "studio-cand-1", shas: [composed.sha256!] }),
    { refusal: "not-an-export" },
  );
});

test("a copy that failed, or belongs to another stage or program, is not this model's", () => {
  const failed = row({
    artifactId: "receipt:failed", sha256: "1".repeat(64), fileName: "seat.work.3dm",
    format: "3dm", representation: "exact", status: "failed", readbackVerified: false,
    sourceStepSha256: exactStep.sha256,
  });
  // Same source digest, same run, but the export did not finish: its file is
  // not the editable model of this delivery.
  assert.equal(workModelOf([exactStep, failed], exactStep), null);

  // Same source digest and a file that is there, but made against another
  // stage, program or state - a different delivery wearing the same bytes.
  for (const elsewhere of [
    { stageId: "studio-candidate-seat-other" },
    { programRef: "project://demo/runs/studio-cand-1/branches/runner-v1/records/other-program.json" },
    { programDigest: "7".repeat(64) },
    { designStateDigest: "6".repeat(64) },
  ]) {
    const stray = row({
      artifactId: "2".repeat(64), sha256: "2".repeat(64), fileName: "seat.work.3dm",
      format: "3dm", representation: "exact", sourceStepSha256: exactStep.sha256,
      programRef: "project://demo/runs/studio-cand-1/branches/runner-v1/records/program.json",
      programDigest: "3".repeat(64), designStateDigest: "4".repeat(64),
      ...elsewhere,
    });
    const source = row({
      ...exactStep,
      programRef: "project://demo/runs/studio-cand-1/branches/runner-v1/records/program.json",
      programDigest: "3".repeat(64), designStateDigest: "4".repeat(64),
    });
    assert.equal(workModelOf([source, stray], source), null, JSON.stringify(elsewhere));
  }

  // The one that does match on every count is found.
  const source = row({
    ...exactStep,
    programRef: "project://demo/runs/studio-cand-1/branches/runner-v1/records/program.json",
    programDigest: "3".repeat(64), designStateDigest: "4".repeat(64),
  });
  const mine = row({
    artifactId: "5".repeat(64), sha256: "5".repeat(64), fileName: "seat.work.3dm",
    format: "3dm", representation: "exact", sourceStepSha256: source.sha256,
    programRef: source.programRef, programDigest: source.programDigest,
    designStateDigest: source.designStateDigest,
  });
  assert.equal(workModelOf([source, mine], source)?.sha256, mine.sha256);
});
