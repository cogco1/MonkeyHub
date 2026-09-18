import { useConnection } from "../../api/ProjectRuntimeContext";
/**
 * The version panel lists working-copy choices first, followed by the other
 * assets in each run. Real file names and export kinds identify every model;
 * view, compare and download still use the existing artifact callbacks.
 */

import { useState } from "react";
import type { DesignHistoryDto, DesignStageDto, ModelSourceDto, ProjectArtifactDto, WorkingCopyDto, WorkingCopyOptionDto } from "../../api/generated";
import { sha8 } from "../../app/format";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../settings/preferences";
import { artifactKindKey, isServable, isViewable, isWorkModel } from "../artifacts/artifactSelection";

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

export interface DesignHistoryControls {
  history: DesignHistoryDto | null;
  acceptedModelSources: readonly ModelSourceDto[];
  currentStageRef: string | null;
  currentModelSource: ModelSourceDto | null;
  candidates: readonly { label: string; modelSource: ModelSourceDto; sourceStageRef: string }[];
  busy: boolean;
  error: string | null;
  onInitialize(): void;
  onStage(stage: DesignStageDto): void;
  onBranch(branchId: string): void;
  onCandidate(source: ModelSourceDto): void;
  onAccept(candidateId: string): void;
  onFork(stage: DesignStageDto, name: string): void;
  onCombine(candidateIds: string[]): void;
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
  design,
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
  design?: DesignHistoryControls;
}) {
  const t = useT();
  const connection = useConnection();
  const { developerMode } = usePreferences();
  const [forkStage, setForkStage] = useState<DesignStageDto | null>(null);
  const [branchName, setBranchName] = useState("");
  const [combineIds, setCombineIds] = useState<string[]>([]);
  if (design) {
    const history = design.history;
    const branch = history?.branches.find((item) => item.branchId === history.branchId);
    const selectedCandidates = design.candidates.filter((candidate) => combineIds.includes(candidate.modelSource.runId));
    const commonSource = selectedCandidates[0]?.sourceStageRef;
    const knownModels = new Set([...design.acceptedModelSources, ...design.candidates.map((candidate) => candidate.modelSource),
      ...workingCopies.flatMap((copy) => copy.options.map((option) => option.modelSource))].map((source) => `${source.runId}:${source.assetSha256}`));
    const legacy = groups.map((group) => ({ ...group, exports: group.exports.filter(({ artifact }) =>
      !knownModels.has(`${artifact.runId}:${artifact.sha256}`)) })).filter((group) => group.exports.length > 0);
    return <div className="versions" role="list" aria-label="Stage 历史">
      <div className="vcard"><div className="vcard__head"><strong>设计历史</strong>
        {history && history.branches.length > 0 && <select aria-label="Branch" value={history.branchId} disabled={design.busy}
          onChange={(event) => design.onBranch(event.target.value)}>{history.branches.map((item) =>
            <option key={item.branchId} value={item.branchId}>{item.branchId}</option>)}</select>}
        {history?.branches.length === 0 && <button className="btn btn--small" disabled={design.busy || !design.currentModelSource}
          onClick={design.onInitialize}>确认当前模型为 S0</button>}
      </div>{design.error && <p role="alert">{design.error}</p>}</div>
      {history?.stages.map((stage) => <div key={stage.stageRef} className="vcard" role="listitem" data-design-stage={stage.label}>
        <div className="vcard__head"><button className="btn btn--small" disabled={design.busy} onClick={() => design.onStage(stage)}
          aria-pressed={stage.stageRef === design.currentStageRef && stage.modelSource.runId === design.currentModelSource?.runId &&
            stage.modelSource.stateDigest === design.currentModelSource.stateDigest && stage.modelSource.assetSha256 === design.currentModelSource.assetSha256}>
          {stage.label}{stage.stageRef === branch?.headStageRef ? " · 当前提交" : ""}</button>
          <button className="btn btn--small" disabled={design.busy} onClick={() => { setForkStage(stage); setBranchName(""); }}>从这里新建分支</button>
        </div>
      </div>)}
      {forkStage && <form className="vcard" onSubmit={(event) => { event.preventDefault(); if (branchName.trim()) design.onFork(forkStage, branchName.trim()); }}>
        <label>{forkStage.label} 的新分支名称 <input value={branchName} onChange={(event) => setBranchName(event.target.value)}
          pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,99}" required placeholder="alternate-layout" /></label>
        <button className="btn btn--small" disabled={design.busy || !branchName.trim()}>创建分支</button>
        <button type="button" className="btn btn--small" onClick={() => setForkStage(null)}>取消</button>
      </form>}
      {workingCopies.map((copy) => <div key={copy.groupId} className="vcard" data-working-copy={copy.groupId}><strong>探索 · {copy.label}</strong>
        <div className="vcard__exports">{copy.options.map((option) => <button key={option.id} className="btn btn--small" disabled={design.busy}
          onClick={() => design.onCandidate(option.modelSource)}>{option.label}{copy.selectedOptionId === option.id ? " · ✓" : ""}</button>)}</div></div>)}
      {design.candidates.length > 1 && <div className="vcard"><button className="btn btn--small"
        disabled={design.busy || selectedCandidates.length < 2 || selectedCandidates.some((candidate) => candidate.sourceStageRef !== commonSource)}
        onClick={() => design.onCombine(selectedCandidates.map((candidate) => candidate.modelSource.runId))}>合并选中候选并预览</button>
        <span className="quiet">选择同一 Stage 下的候选，合并后仍需接受。</span></div>}
      {design.candidates.map(({ label, modelSource, sourceStageRef }) => {
        const selected = modelSource.runId === design.currentModelSource?.runId && modelSource.assetSha256 === design.currentModelSource.assetSha256;
        return <div className="vcard" role="listitem" key={`${modelSource.runId}:${modelSource.assetSha256}`} data-preview-candidate={modelSource.runId}>
          <div className="vcard__head"><label><input type="checkbox" aria-label={`合并 ${label}`} checked={combineIds.includes(modelSource.runId)}
            disabled={design.busy || (commonSource !== undefined && sourceStageRef !== commonSource && !combineIds.includes(modelSource.runId))}
            onChange={(event) => setCombineIds((current) => event.target.checked ? [...current, modelSource.runId] : current.filter((id) => id !== modelSource.runId))} />候选 · 未提交</label><strong>{label}</strong></div>
          <div className="vcard__exports"><button className="btn btn--small" aria-pressed={selected} disabled={design.busy}
            onClick={() => design.onCandidate(modelSource)}>预览并继续修改</button>
            {selected && branch && <button className="btn btn--small" disabled={design.busy || design.currentStageRef !== branch.headStageRef}
              onClick={() => design.onAccept(modelSource.runId)}>接受为下一 Stage</button>}
            {selected && branch && design.currentStageRef !== branch.headStageRef && <span className="quiet">此候选来自历史阶段，请先从该阶段新建分支。</span>}
          </div></div>;
      })}
      {legacy.length > 0 && <details className="vcard"><summary>已有模型与历史运行 · 尚未归入 Stage</summary>
        <VersionsStrip groups={legacy} loadingSha={loadingSha} loadedShas={loadedShas} loadedRunId={loadedRunId}
          onOpen={onOpen} onOpenRun={onOpenRun} onCompare={onCompare} />
      </details>}
    </div>;
  }
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
        // An editable copy stays out of this list for the same reason the
        // viewer is never handed one: it is the delivery already on screen as
        // its preview, and it is reached by saving it, not by opening it.
        const viewable = group.exports.filter(
          (item) => isViewable(item.artifact) && !isWorkModel(item.artifact),
        );
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
              {/* An editable copy is a delivery a person asked for, so its save
                  link is always here; the other per-seat files stay a
                  developer's view of the run. An export that failed its
                  readback can still leave a readable file behind - that file
                  is not the delivery, so only a succeeded one is offered. */}
              {saves.filter(({ artifact }) => isWorkModel(artifact) && artifact.status === "succeeded").map(({ artifact }) => (
                <a
                  key={`work-model-${artifact.artifactId}`}
                  className="vcard__save"
                  data-work-model-save
                  href={connection.url(`/api/artifacts/${artifact.sha256}/bytes`)}
                  download={artifact.fileName}
                  title={t("stage.versions.saveTitle", { fileName: artifact.fileName })}
                >
                  {t("stage.tools.workModelSave")}
                </a>
              ))}
              {developerMode && saves.filter(({ artifact }) => !(isWorkModel(artifact) && artifact.status === "succeeded")).map(({ artifact, seat }) => (
                <a
                  key={`save-${artifact.artifactId}`}
                  className="vcard__save"
                  href={connection.url(`/api/artifacts/${artifact.sha256}/bytes`)}
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
