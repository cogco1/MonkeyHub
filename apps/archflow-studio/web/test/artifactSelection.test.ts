import assert from "node:assert/strict";
import test from "node:test";

import type { ProjectArtifactDto } from "../src/api/generated";
import {
  artifactKindKey,
  isViewable,
  viewableArtifacts,
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
