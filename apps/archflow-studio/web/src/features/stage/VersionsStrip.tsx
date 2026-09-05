/**
 * The models this project can show, one card per run, in one row along the
 * bottom of the stage: the reference run first, then the candidates this tab
 * launched, and every other run folded behind a count until asked for. A
 * run's exports are the buttons on its card — `show run` for all of them at
 * once, then one per seat — so a version is one card and not two. A card is a
 * receipt's claim beside disk's answer: an unavailable export is a button that
 * says why, never a missing one.
 *
 * With a model on screen, every other run's card offers a comparison against
 * it: Before / After / Why from the inspection records, by run, not by file.
 */

import { useState } from "react";

import type { ProjectArtifactDto } from "../../api/generated";
import { sha8 } from "../../app/format";
import { useT } from "../../i18n/useT";
import { artifactKindKey, isServable, isViewable } from "../artifacts/artifactSelection";

export interface VersionExport {
  readonly artifact: ProjectArtifactDto;
  /** The seat this export came from, as the stage id names it. */
  readonly seat: string;
  readonly sourceLabel: string;
}

export interface VersionGroup {
  readonly runId: string;
  readonly label: "Reference" | "Candidate" | "Run";
  /** In the architect's words: the issue, the sentence, or the run. */
  readonly title: string;
  /** Review readiness, or what this tab knows about the run; null when nothing. */
  readonly detail: string | null;
  readonly exports: readonly VersionExport[];
}

export function VersionsStrip({
  groups,
  loadingSha,
  loadedShas,
  loadedRunId,
  onOpen,
  onOpenRun,
  onCompare,
}: {
  groups: readonly VersionGroup[];
  loadingSha: string | null;
  /** The digests of the exports on screen: one seat, or every seat of a run. */
  loadedShas: readonly string[];
  /** The run whose export is on screen; the 'before' of a comparison. */
  loadedRunId: string | null;
  onOpen(artifact: ProjectArtifactDto, sourceLabel: string): void;
  /** Put every available export of this run on the stage at once. */
  onOpenRun(group: VersionGroup): void;
  /** Compare this card's run against the loaded run's exports. */
  onCompare(artifact: ProjectArtifactDto): void;
}) {
  const t = useT();
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
    <div className="versions" role="list" aria-label={t("stage.versions.ariaLabel")}>
      {shown.map((group) => {
        const loaded = group.runId === loadedRunId;
        const comparable =
          loadedRunId !== null &&
          !loaded &&
          group.exports.some((item) => item.artifact.available);
        const firstAvailable = group.exports.find((item) => item.artifact.available);
        // What the stage can show: the 3dm rows (a Rhino export, or the mesh
        // preview beside an exact STEP). The STEP is saved, never parsed, so
        // an in-process receipt's two files are one seat button and two saves.
        const viewable = group.exports.filter((item) => isViewable(item.artifact));
        const servable = viewable.filter((item) => isServable(item.artifact));
        const saves = group.exports.filter((item) => isServable(item.artifact));
        const referenceIssue =
          group.label === "Reference"
            ? /^based on issue (.+)$/.exec(group.title)?.[1]
            : undefined;
        const displayedLabel =
          group.label === "Reference"
            ? t("stage.versions.reference")
            : group.label === "Candidate"
              ? t("stage.versions.candidate")
              : t("stage.versions.run");
        const displayedDetail = (() => {
          if (group.detail === null) return null;
          if (group.detail === "ready for review") return t("stage.view.reviewReady");
          if (group.detail === "verdict not read yet") return t("stage.view.verdictUnread");
          if (group.detail === "not launched from this tab") {
            return t("stage.versions.notLaunchedHere");
          }
          if (group.detail.startsWith("blocked: ")) {
            return (
              <>
                {t("stage.view.blocked")}: {group.detail.slice("blocked: ".length)}
              </>
            );
          }
          return <span lang="en" translate="no">{group.detail}</span>;
        })();
        return (
          <div
            key={group.runId}
            role="listitem"
            className={`vcard${loaded ? " vcard--loaded" : ""}`}
            title={group.runId}
          >
            <div className="vcard__head">
              <span className="label">{displayedLabel}</span>
              <span className="vcard__title">
                {referenceIssue !== undefined ? (
                  t("stage.versions.referenceBasedOn", { version: referenceIssue })
                ) : (
                  group.title
                )}
              </span>
              {displayedDetail && <span className="vcard__meta">{displayedDetail}</span>}
            </div>
            <div className="vcard__exports">
              {servable.length > 0 && (
                <button
                  type="button"
                  className="btn btn--small vcard__run"
                  aria-pressed={loaded && loadedShas.length > 1}
                  disabled={loadingSha !== null}
                  title={t("stage.versions.showRunTitle", {
                    seats: servable.map((item) => item.seat).join(" + "),
                  })}
                  onClick={() => onOpenRun(group)}
                >
                  {t("stage.versions.showRun")}
                </button>
              )}
              {viewable.map(({ artifact, seat, sourceLabel }) => {
                const isLoaded =
                  artifact.sha256 !== null && loadedShas.includes(artifact.sha256);
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
                        ? t("stage.versions.showSeatTitle", {
                            fileName: artifact.fileName,
                            sha: sha8(artifact.sha256),
                          })
                        : (artifact.unavailableReason ??
                          t("stage.versions.unavailableNoReason"))
                    }
                    onClick={() => onOpen(artifact, sourceLabel)}
                  >
                    {loadingThis ? t("stage.versions.loading") : seat}
                    {!artifact.available && ` · ${t("stage.versions.unavailable")}`}
                  </button>
                );
              })}
              {saves.map(({ artifact, seat }) => (
                <a
                  key={`save-${artifact.artifactId}`}
                  className="vcard__save"
                  href={`/api/artifacts/${artifact.sha256}/bytes`}
                  download={artifact.fileName}
                  title={t("stage.versions.saveTitle", {
                    fileName: artifact.fileName,
                  })}
                >
                  {t("stage.versions.saveSeat", { seat })} · {t(artifactKindKey(artifact))}
                </a>
              ))}
              {group.exports
                .filter(({ artifact }) => !artifact.available && !isViewable(artifact))
                .map(({ artifact, seat }) => (
                  <span
                    key={`unavailable-${artifact.artifactId}`}
                    className="vcard__export vcard__export--unavailable"
                    title={artifact.unavailableReason ?? t("stage.versions.unavailableNoReason")}
                  >
                    {seat} · {t(artifactKindKey(artifact))} · {t("stage.versions.unavailable")}
                  </span>
                ))}
              {comparable && firstAvailable && (
                <button
                  type="button"
                  className="btn btn--small vcard__compare"
                  title={t("stage.versions.compareTitle")}
                  onClick={() => onCompare(firstAvailable.artifact)}
                >
                  {t("stage.versions.compare")}
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
            ? t("stage.versions.foldEarlier")
            : t("stage.versions.earlierRuns", { count: earlier.length })}
        </button>
      )}
    </div>
  );
}
