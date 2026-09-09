/**
 * The version panel lists working-copy choices first, followed by the other
 * assets in each run. Real file names and export kinds identify every model;
 * view, compare and download still use the existing artifact callbacks.
 */

import type { ProjectArtifactDto, WorkingCopyDto, WorkingCopyOptionDto } from "../../api/generated";
import { sha8 } from "../../app/format";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
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
  workingCopies = [],
  onOpenWorkingOption,
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
  workingCopies?: readonly WorkingCopyDto[];
  onOpenWorkingOption?(option: WorkingCopyOptionDto): void;
}) {
  const t = useT();
  const { developerMode } = usePreferences();
  if (groups.length === 0 && workingCopies.length === 0) return null;
  // A grouped option represents one exact asset, not every export in its run.
  // Keep other composed models and native exports from that run discoverable.
  const groupedAssets = new Set(workingCopies.flatMap((copy) => copy.options.map((option) =>
    `${option.modelSource.runId}:${option.modelSource.assetSha256}`)));
  const shown = groups.map((group) => ({ ...group,
    exports: group.exports.filter(({ artifact }) => !groupedAssets.has(`${group.runId}:${artifact.sha256}`)),
  })).filter((group) => group.exports.length > 0);
  return (
    <div className="versions" role="list" aria-label={t("stage.versions.ariaLabel")}>
      {workingCopies.map((copy) => (
        <div key={copy.groupId} className="vcard" role="listitem" data-working-copy={copy.groupId}>
          <div className="vcard__head"><span className="vcard__title">{copy.label}</span></div>
          <div className="vcard__exports">
            {copy.options.map((option) => {
              const source = option.modelSource;
              const loaded = source.runId === loadedRunId && loadedShas.includes(source.assetSha256);
              return <button type="button" key={option.id} className="btn btn--small vcard__export"
                aria-pressed={loaded} disabled={loadingSha !== null} onClick={() => onOpenWorkingOption?.(option)}
                title={groups.find((group) => group.runId === source.runId)?.exports.find(({ artifact }) => artifact.sha256 === source.assetSha256)?.artifact.fileName}>
                {t("stage.workingCopy.view", { label: option.label })}
                {copy.selectedOptionId === option.id && <span aria-label={t("stage.workingCopy.selected")}> · ✓</span>}
              </button>;
            })}
          </div>
        </div>
      ))}
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
        const namedArtifact = group.exports.find(({ artifact }) => artifact.representation === "composed")?.artifact ?? viewable[0]?.artifact ?? group.exports[0]?.artifact;
        const displayTitle = group.label === "Candidate" ? group.title : namedArtifact?.fileName ?? group.title;
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
            if (!developerMode) return t("stage.view.needsAttention");
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
            title={developerMode ? group.runId : undefined}
          >
            <div className="vcard__head">
              <span className="label">{displayedLabel}</span>
              <span className="vcard__title" title={displayTitle}>{displayTitle}</span>
              {developerMode && referenceIssue !== undefined && <span className="vcard__meta">{t("stage.versions.referenceBasedOn", { version: referenceIssue })}</span>}
              {developerMode && <span className="vcard__meta">{group.runId}</span>}
              {displayedDetail && (developerMode || group.detail !== "not launched from this tab") && <span className="vcard__meta">{displayedDetail}</span>}
            </div>
            <div className="vcard__exports">
              {servable.length > 1 && (
                <button
                  type="button"
                  className="btn btn--small vcard__run"
                  aria-pressed={loaded && loadedShas.length > 1}
                  disabled={loadingSha !== null}
                  title={developerMode ? t("stage.versions.showRunTitle", {
                    seats: servable.map((item) => item.seat).join(" + "),
                  }) : undefined}
                  onClick={() => onOpenRun(groups.find((original) => original.runId === group.runId) ?? group)}
                >
                  {t("stage.versions.showAllExports")}
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
                    title={artifact.available
                      ? developerMode ? t("stage.versions.showSeatTitle", { fileName: artifact.fileName, sha: sha8(artifact.sha256) }) : artifact.fileName
                      : (artifact.unavailableReason ?? t("stage.versions.unavailableNoReason"))}
                    onClick={() => onOpen(artifact, sourceLabel)}
                  >
                    <span className="vcard__asset-kind">{loadingThis ? t("stage.versions.loading") : t(artifact.representation === "composed" ? "stage.versions.completeModel" : "stage.versions.nativeExport")}</span>
                    <span className="vcard__filename">{artifact.fileName}</span>
                    {(developerMode || artifact.representation === "preview") && <span className="quiet">{developerMode ? `${seat} · ` : ""}{t(artifactKindKey(artifact))}</span>}
                    {!artifact.available && ` · ${t("stage.versions.unavailable")}`}
                  </button>
                );
              })}
              {developerMode && saves.map(({ artifact, seat }) => (
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
              {developerMode && group.exports
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
    </div>
  );
}
