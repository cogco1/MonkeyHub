/**
 * The models this project can show, one card each, along the bottom of the
 * stage: the reference run's exports, the candidates this tab launched, and
 * any other run's. A card is a receipt's claim beside disk's answer — an
 * unavailable export is a card that says why, never a missing one.
 *
 * With a model on screen, every other run's card offers a comparison against
 * it: Before / After / Why from the inspection records, by run, not by file.
 */

import type { ProjectArtifactDto } from "../../api/generated";

export interface VersionCard {
  readonly artifact: ProjectArtifactDto;
  readonly label: "Reference" | "Candidate" | "Run";
  readonly title: string;
  readonly meta: string;
  /** The verdict word when a verdict was read for the run; null otherwise. */
  readonly verdict: string | null;
  readonly sourceLabel: string;
}

export function VersionsStrip({
  versions,
  loadingSha,
  loadedSha,
  loadedRunId,
  onOpen,
  onCompare,
}: {
  versions: readonly VersionCard[];
  loadingSha: string | null;
  loadedSha: string | null;
  /** The run whose export is on screen; the 'before' of a comparison. */
  loadedRunId: string | null;
  onOpen(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Compare this card's run against the loaded run's exports. */
  onCompare(artifact: ProjectArtifactDto): void;
}) {
  if (versions.length === 0) return null;
  return (
    <div className="versions" role="list">
      {versions.map((version) => {
        const { artifact } = version;
        const loaded = artifact.sha256 !== null && artifact.sha256 === loadedSha;
        const loadingThis =
          loadingSha !== null && artifact.sha256 === loadingSha;
        const comparable =
          loadedRunId !== null && artifact.runId !== loadedRunId && artifact.available;
        return (
          <div
            key={artifact.artifactId}
            role="listitem"
            className={`vcard${artifact.available ? "" : " vcard--unavailable"}${loaded ? " vcard--loaded" : ""}`}
          >
            <button
              type="button"
              className="vcard__open"
              aria-pressed={loaded}
              disabled={!artifact.available || loadingSha !== null}
              onClick={() => onOpen(artifact, version.sourceLabel)}
            >
              <span className="label">{version.label}</span>
              <span className="vcard__title">{version.title}</span>
              <span className="vcard__meta mono">
                {loadingThis
                  ? "loading bytes…"
                  : artifact.available
                    ? version.meta
                    : (artifact.unavailableReason ??
                      "unavailable, and the server gave no reason")}
              </span>
              {version.verdict && (
                <span className="vcard__meta">{version.verdict}</span>
              )}
            </button>
            {comparable && (
              <button
                type="button"
                className="btn btn--small vcard__compare"
                title="Before / after against the run on screen"
                onClick={() => onCompare(artifact)}
              >
                compare with what is on screen
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
