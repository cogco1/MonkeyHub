/**
 * The models this project can show, one card each, along the bottom of the
 * stage: the reference run's exports, the candidates this tab launched, and
 * any other run's. A card is a receipt's claim beside disk's answer — an
 * unavailable export is a card that says why, never a missing one.
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
  onOpen,
}: {
  versions: readonly VersionCard[];
  loadingSha: string | null;
  loadedSha: string | null;
  onOpen(artifact: ProjectArtifactDto, sourceLabel: string): void;
}) {
  if (versions.length === 0) return null;
  return (
    <div className="versions" role="list">
      {versions.map((version) => {
        const { artifact } = version;
        const loaded = artifact.sha256 !== null && artifact.sha256 === loadedSha;
        const loadingThis =
          loadingSha !== null && artifact.sha256 === loadingSha;
        return (
          <button
            key={artifact.artifactId}
            type="button"
            role="listitem"
            className={`vcard${artifact.available ? "" : " vcard--unavailable"}`}
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
        );
      })}
    </div>
  );
}
