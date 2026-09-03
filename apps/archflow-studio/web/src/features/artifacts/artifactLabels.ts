/**
 * What the viewer wears while an artifact is loaded, and what the loaded file
 * can say about itself — both taken off the receipt that certified the bytes.
 */

import type { ProjectArtifactDto } from "../../api/generated";
import { sha8 } from "../../app/format";

/** The chip the viewer wears while a canonical artifact is loaded. */
export function canonicalSourceLabel(artifact: ProjectArtifactDto): string {
  return `CANONICAL · ${artifact.runId} · ${sha8(artifact.sha256)}`;
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
