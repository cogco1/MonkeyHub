/**
 * What the viewer wears while an artifact is loaded, and what the loaded file
 * can say about itself — both taken off the receipt that certified the bytes.
 */

import type { ProjectArtifactDto } from "../../api/generated";
import { sha8 } from "../../app/format";

/** The seat name an export's stage id ends with, or the file when it has none. */
export function seatOf(artifact: ProjectArtifactDto): string {
  const stage = artifact.stageId;
  if (stage === null) return artifact.fileName;
  const marker = "seat-";
  const at = stage.lastIndexOf(marker);
  return at === -1 ? stage : stage.slice(at + marker.length);
}

/**
 * The chip the viewer wears while one canonical export is loaded: the run, the
 * seat that export came from, and the digest of the bytes on screen. The seat
 * is there so one seat of a run never reads as the whole of it.
 */
export function canonicalSourceLabel(artifact: ProjectArtifactDto): string {
  return `CANONICAL · ${artifact.runId} · ${seatOf(artifact)} · ${sha8(artifact.sha256)}`;
}

/**
 * The chip the viewer wears while a whole run is loaded: the run and every
 * seat on screen, named. No digest — the picture is several files, and one
 * sha would be a claim about one of them.
 */
export function canonicalRunSourceLabel(
  runId: string,
  seats: readonly string[],
): string {
  return `CANONICAL · ${runId} · ${seats.join(" + ")}`;
}

/** The chip the viewer wears while a candidate's own export is loaded. */
export function candidateSourceLabel(candidateId: string): string {
  return `CANDIDATE · ${candidateId}`;
}

/**
 * What the loaded file says about itself, taken off the receipt that certified
 * its bytes.
 *
 * The three keys are the document-level ones `POST /api/pick/resolve` reads,
 * and each value is the receipt's own claim about the run that produced this
 * artifact — never a guess and never the projection's numbers copied across. A
 * receipt that claims no digest contributes no key, and the server answers
 * `sourceState: "unknown"` for it, which is still the truth about that file.
 *
 * This exists because three's `3DMLoader` parses the document's user strings
 * and then does not attach them to the scene, so the bytes the viewer holds
 * cannot answer for themselves. The API served both the bytes and the receipt
 * under the same sha256, so the answer is the server's either way.
 */
export function receiptDocumentStrings(
  artifact: ProjectArtifactDto | null,
): Record<string, string> | null {
  if (artifact === null) return null;
  const strings: Record<string, string> = {};
  if (artifact.designStateDigest) {
    strings["archflow:design_state_digest"] = artifact.designStateDigest;
  }
  if (artifact.runId) strings["archflow:run_id"] = artifact.runId;
  if (artifact.programDigest) {
    strings["archflow:program_digest"] = artifact.programDigest;
  }
  return Object.keys(strings).length > 0 ? strings : null;
}
