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
import { sha8 } from "../project/TopBar";

/** The chip the viewer wears while a canonical artifact is loaded. */
export function canonicalSourceLabel(artifact: ProjectArtifactDto): string {
  return `CANONICAL · ${artifact.runId} · ${sha8(artifact.sha256)}`;
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
