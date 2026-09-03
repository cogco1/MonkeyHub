/**
 * The exported models the run receipts certify.
 *
 * Every row keeps the receipt's own claims beside the disk's answer:
 * `available` and `unavailableReason` are shown, never filtered out. An
 * artifact whose bytes are gone is a row that says so — dropping it would turn
 * a missing file into a project that never exported one.
 *
 * A `.3dm` in this list is a file that was written. It is not a claim that
 * anything about it passed.
 */

import type { ArtifactListDto, ProjectArtifactDto } from "../../api/generated";
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

export function ArtifactList({
  listing,
  loadingSha,
  onLoad,
}: {
  listing: ArtifactListDto;
  loadingSha: string | null;
  onLoad(artifact: ProjectArtifactDto): void;
}) {
  return (
    <div className="artifacts">
      {listing.skippedRuns.length > 0 && (
        <p className="panel__note panel__note--refused">
          runs whose records could not be listed:{" "}
          <span className="mono">{listing.skippedRuns.join(", ")}</span>
        </p>
      )}
      {listing.artifacts.length === 0 ? (
        <p className="panel__note">
          the receipts in this project certify no exported models.
        </p>
      ) : (
        <ul className="rows">
          {listing.artifacts.map((artifact) => (
            <li key={artifact.artifactId}>
              <button
                type="button"
                className={`row${artifact.available ? "" : " is-unavailable"}`}
                disabled={!artifact.available || loadingSha !== null}
                onClick={() => onLoad(artifact)}
              >
                <span className="row__id">{artifact.fileName}</span>
                <span className="row__meta">
                  {artifact.runId}
                  {artifact.stageId ? ` · ${artifact.stageId}` : ""}
                </span>
                <span className="row__meta">
                  {artifact.status ?? "no status"} · readback{" "}
                  {String(artifact.readbackVerified)} · sha{" "}
                  {sha8(artifact.sha256)}
                </span>
                {artifact.objectCount !== null && (
                  <span className="row__meta">
                    {artifact.objectCount} objects
                  </span>
                )}
                {!artifact.available && (
                  <span className="row__fields">
                    unavailable:{" "}
                    {artifact.unavailableReason ??
                      "the server gave no reason, which is the answer it gave"}
                  </span>
                )}
                {loadingSha !== null && loadingSha === artifact.sha256 && (
                  <span className="row__fields">loading bytes…</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
