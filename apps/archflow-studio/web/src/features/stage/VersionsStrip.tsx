/**
 * The models this project can show, one card per run, in one row along the
 * bottom of the stage: the reference run first, then the candidates this tab
 * launched, and every other run folded behind a count until asked for. A
 * run's exports are the buttons on its card — one per seat — so a version is
 * one card and not two. A card is a receipt's claim beside disk's answer: an
 * unavailable export is a button that says why, never a missing one.
 *
 * With a model on screen, every other run's card offers a comparison against
 * it: Before / After / Why from the inspection records, by run, not by file.
 */

import { useState } from "react";

import type { ProjectArtifactDto } from "../../api/generated";
import { sha8 } from "../../app/format";

export interface VersionExport {
  readonly artifact: ProjectArtifactDto;
  /** The seat this export came from, as the stage id names it. */
  readonly seat: string;
  readonly sourceLabel: string;
}

export interface VersionGroup {
  readonly runId: string;
  readonly label: "Reference" | "Candidate" | "Run";
  /** In the architect's words: the head version, the sentence, or the run. */
  readonly title: string;
  /** The verdict word, or what this tab knows about the run; null when nothing. */
  readonly detail: string | null;
  readonly exports: readonly VersionExport[];
}

/** The seat name an export's stage id ends with, or the file when it has none. */
export function seatOf(artifact: ProjectArtifactDto): string {
  const stage = artifact.stageId;
  if (stage === null) return artifact.fileName;
  const marker = "seat-";
  const at = stage.lastIndexOf(marker);
  return at === -1 ? stage : stage.slice(at + marker.length);
}

export function VersionsStrip({
  groups,
  loadingSha,
  loadedSha,
  loadedRunId,
  onOpen,
  onCompare,
}: {
  groups: readonly VersionGroup[];
  loadingSha: string | null;
  loadedSha: string | null;
  /** The run whose export is on screen; the 'before' of a comparison. */
  loadedRunId: string | null;
  onOpen(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Compare this card's run against the loaded run's exports. */
  onCompare(artifact: ProjectArtifactDto): void;
}) {
  const [showEarlier, setShowEarlier] = useState(false);
  if (groups.length === 0) return null;
  // Reference and this tab's candidates always show; other runs fold. A run
  // whose export is on screen stays visible whatever it is, so the card the
  // source chip points at is never the one that was folded away.
  const earlier = groups.filter(
    (group) => group.label === "Run" && group.runId !== loadedRunId,
  );
  const shown = showEarlier
    ? groups
    : groups.filter((group) => !earlier.includes(group));
  return (
    <div className="versions" role="list">
      {shown.map((group) => {
        const loaded = group.runId === loadedRunId;
        const comparable =
          loadedRunId !== null &&
          !loaded &&
          group.exports.some((item) => item.artifact.available);
        const firstAvailable = group.exports.find((item) => item.artifact.available);
        return (
          <div
            key={group.runId}
            role="listitem"
            className={`vcard${loaded ? " vcard--loaded" : ""}`}
            title={group.runId}
          >
            <div className="vcard__head">
              <span className="label">{group.label}</span>
              <span className="vcard__title">{group.title}</span>
              {group.detail && <span className="vcard__meta">{group.detail}</span>}
            </div>
            <div className="vcard__exports">
              {group.exports.map(({ artifact, seat, sourceLabel }) => {
                const isLoaded =
                  artifact.sha256 !== null && artifact.sha256 === loadedSha;
                const loadingThis =
                  loadingSha !== null && artifact.sha256 === loadingSha;
                return (
                  <button
                    key={artifact.artifactId}
                    type="button"
                    className={`btn btn--small vcard__export${artifact.available ? "" : " vcard__export--unavailable"}`}
                    aria-pressed={isLoaded}
                    disabled={!artifact.available || loadingSha !== null}
                    title={
                      artifact.available
                        ? `${artifact.fileName} · ${sha8(artifact.sha256)}`
                        : (artifact.unavailableReason ??
                          "unavailable, and the server gave no reason")
                    }
                    onClick={() => onOpen(artifact, sourceLabel)}
                  >
                    {loadingThis ? "loading…" : seat}
                    {!artifact.available && " · unavailable"}
                  </button>
                );
              })}
              {comparable && firstAvailable && (
                <button
                  type="button"
                  className="btn btn--small vcard__compare"
                  title="Before / after against the run on screen"
                  onClick={() => onCompare(firstAvailable.artifact)}
                >
                  compare
                </button>
              )}
            </div>
          </div>
        );
      })}
      {earlier.length > 0 && (
        <button
          type="button"
          className="versions__more"
          aria-expanded={showEarlier}
          onClick={() => setShowEarlier((open) => !open)}
        >
          {showEarlier
            ? "fold earlier runs"
            : `earlier runs · ${earlier.length}`}
        </button>
      )}
    </div>
  );
}
