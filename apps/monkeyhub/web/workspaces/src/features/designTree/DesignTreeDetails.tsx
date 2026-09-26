/**
 * The side card of the selected node: the only place for author, time and
 * progress, and for the actions, kept apart. View never moves Current;
 * Continue does; Accept as next Stage exists on Current only and asks first.
 * A done act confirms itself in the toast beside the chip (FN-5); a refused
 * one says why here.
 */
import { useState } from "react";
import { usePreferences } from "../settings/preferences";
import { useT } from "../../i18n/useT";
import { CURRENT, type GrowthTree, type TreeNode } from "./model";
import { DESIGN_TREE_UNSYNCED, useRecordAndContinue, type DesignTreeData } from "./useDesignTree";
import { refusalWords, whenText, type TreeWords } from "./words";
import { ModelThumbnail } from "../artifacts/ModelThumbnail";

export function DesignTreeDetails({ tree, node, words, data, confirmAccept, onConfirmAccept, onClose, onView, onRecordEdits = null }: {
  tree: GrowthTree;
  node: TreeNode;
  words: TreeWords;
  data: DesignTreeData;
  confirmAccept: boolean;
  onConfirmAccept(open: boolean): void;
  onClose(): void;
  onView(node: TreeNode): void;
  /** Records Modeling's unrecorded edits, so a Continue they refused can go on (#302). */
  onRecordEdits?: (() => Promise<void>) | null;
}) {
  const t = useT();
  const { language, developerMode } = usePreferences();
  const [confirmClosedFor, setConfirmClosedFor] = useState<string | null>(null);
  const { recording, failure, recordAndContinue } = useRecordAndContinue(data, onRecordEdits);
  const title = words.title(node);
  const parent = words.byId(node.parent);
  const study = node.studyId ? tree.studies.get(node.studyId) : undefined;
  const studyName = words.studyName(node.studyId);
  // Off the trunk and not a twig straight off it: part of a future a Continue left behind.
  const earlier = !tree.onTrunk.has(node.id) && !(node.parent !== null && tree.onTrunk.has(node.parent));
  const role = node.kind === "stage" ? t("designTree.role.stage")
    : node.kind === "candidate" ? (earlier ? t("designTree.role.earlier") : studyName ? t("designTree.role.option", { study: studyName }) : t("designTree.role.optionAlone"))
      : node.kind === "pending" ? t("designTree.role.pending")
        : node.kind === "current" ? t("designTree.role.current") : t("designTree.role.origin");
  const facts: [string, string][] = [];
  const add = (label: string, value: string | null | undefined) => { if (value) facts.push([label, value]); };
  if (node.kind === "candidate") {
    const base = parent ? words.title(parent) : null;
    add(t("designTree.fact.study"), study?.label && base ? t("designTree.fact.studyFrom", { study: study.label, base }) : studyName);
    if (!study) add(t("designTree.fact.from"), base);
    add(t("designTree.fact.admittedBy"), words.admitter(node.candidate!));
    add(t("designTree.fact.admittedAt"), whenText(node.candidate!.admittedAt, language));
    add(t("designTree.fact.status"), words.status(node));
  } else if (node.kind === "stage") {
    add(t("designTree.fact.acceptedBy"), words.actor(node.stage!.acceptedBy));
    add(t("designTree.fact.acceptedAt"), whenText(node.stage!.acceptedAt, language));
    add(t("designTree.fact.from"), parent ? words.title(parent) : null);
  } else if (node.kind === "current") {
    add(t("designTree.fact.at"), words.currentAt());
    add(t("designTree.fact.stage"), tree.currentStage ? words.title(tree.nodes.get(tree.currentStage)!) : t("designTree.chip.noStage"));
  } else if (node.kind === "pending") {
    add(t("designTree.fact.study"), studyName);
    add(t("designTree.fact.status"), words.pendingText(node.pending!.status));
    add(t("designTree.fact.progress"), node.pending!.detail);
    add(t("designTree.fact.from"), parent ? words.title(parent) : null);
    add(t("designTree.fact.updated"), whenText(node.pending!.updatedAt, language));
  }
  const busy = data.busy !== null;
  const continuing = data.busy?.kind === "continue" && data.busy.node === node.id;
  const isAnchor = tree.nodes.get(CURRENT)?.parent === node.id && tree.nodes.get(CURRENT)?.current?.editsAfter === 0;
  // A refusal of this node's act (Accept is Current's); a done act answers in the toast instead.
  const refused = data.outcome?.kind === "refused" && data.outcome.node === node.id ? data.outcome.error ?? null : null;
  // Refused only because Modeling holds unrecorded edits: one click records them and continues again.
  const recordable = refused?.code === DESIGN_TREE_UNSYNCED && onRecordEdits !== null;
  const refusal = refused ? refusalWords(t, language, refused) : null;
  const accept = tree.accept;
  const acceptBlocked = accept.block === "already-stage" ? t("designTree.accept.alreadyStage", { stage: words.title(tree.nodes.get(tree.currentStage ?? "") ?? node) })
    : accept.block === "rejected" ? t("designTree.accept.rejected")
    : accept.block === "older-stage" ? t("designTree.accept.olderStage", {
      base: accept.baseStage && tree.nodes.get(accept.baseStage) ? words.title(tree.nodes.get(accept.baseStage)!) : "—",
      head: accept.lineHeadStage && tree.nodes.get(accept.lineHeadStage) ? words.title(tree.nodes.get(accept.lineHeadStage)!) : "—" })
      : accept.block === "no-stage" ? t("designTree.accept.noStage") : accept.block === "no-head" ? t("designTree.accept.noHead") : null;
  const showConfirm = confirmAccept && accept.allowed && confirmClosedFor !== node.id;
  const source = node.kind === "stage" ? data.source?.history.stages.find(stage => stage.stageRef === node.stage?.ref)?.modelSource
    : node.kind === "candidate" ? data.source?.history.candidates?.find(candidate => candidate.candidateId === node.candidate?.candidateId)?.modelSource
      : node.kind === "current" ? data.source?.workingSource.head?.modelSource : null;
  return <aside className="design-tree-card" aria-label={title} data-node={node.id} data-kind={node.kind}>
    <div className="design-tree-card__head">
      {node.kind === "candidate" && <span className="design-tree-card__tile" aria-hidden="true">{node.letter ?? "·"}</span>}
      <div className="design-tree-card__identity"><span>{role}</span><strong>{title}</strong></div>
      <button type="button" className="design-tree-card__close" aria-label={t("designTree.action.close")} onClick={onClose}>×</button>
    </div>
    {(node.kind === "stage" || node.kind === "candidate" || node.kind === "current") && <ModelThumbnail source={source} />}
    {node.kind !== "pending" && node.summary && <p className="design-tree-card__summary">{node.summary}</p>}
    {(node.candidate?.blockedBy.length ?? 0) > 0 && <p className="design-tree-card__warning" role="note">
      <span aria-hidden="true">!</span> {t("designTree.review.note", { count: node.candidate!.blockedBy.length })}</p>}
    {facts.length > 0 && <dl className="design-tree-card__facts">{facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
    {(node.kind === "candidate" || node.kind === "stage") && <>
      <div className="design-tree-card__actions">
        <button type="button" className="btn btn--small" disabled={!node.runId} onClick={() => onView(node)}>{t("designTree.action.view")}</button>
        {node.kind === "candidate" && <button type="button" className="btn btn--small" disabled title={t("designTree.action.compareLater")}
          aria-describedby="design-tree-compare-later">{t("designTree.action.compare")} <small>· {t("designTree.action.later")}</small></button>}
        <button type="button" className="btn btn--small btn--primary" data-action="continue" disabled={busy || !data.canContinue || isAnchor}
          onClick={() => void data.continueFrom(node.id)}>{continuing ? t("designTree.action.continuing") : t("designTree.action.continue")}</button>
      </div>
      {node.kind === "candidate" && <p id="design-tree-compare-later" className="visually-hidden">{t("designTree.action.compareLater")}</p>}
      <p className="design-tree-card__note">{data.canContinue ? t("designTree.action.hint") : t("designTree.outcome.cannotContinue")}</p>
    </>}
    {node.kind === "current" && <div className="design-tree-card__accept">
      <button type="button" className="btn btn--small btn--primary" data-action="accept" disabled={!accept.allowed || busy}
        onClick={() => { setConfirmClosedFor(null); onConfirmAccept(true); }}>
        {accept.allowed && accept.nextLabel ? t("designTree.action.accept", { stage: accept.nextLabel }) : t("designTree.action.acceptNext")}</button>
      {acceptBlocked && <p className="design-tree-card__note">{acceptBlocked}</p>}
      {showConfirm && <div className="design-tree-card__confirm" role="group" aria-label={t("designTree.action.acceptNext")}>
        <p>{t("designTree.action.confirm", { stage: accept.nextLabel ?? "" })}</p>
        <div className="design-tree-card__actions">
          <button type="button" className="btn btn--small btn--primary" data-action="accept-confirm" disabled={busy}
            onClick={() => void data.acceptCurrent().then((done) => { if (done) onConfirmAccept(false); })}>
            {data.busy?.kind === "accept" ? t("designTree.action.accepting") : t("designTree.action.accept", { stage: accept.nextLabel ?? "" })}</button>
          <button type="button" className="btn btn--small" onClick={() => { setConfirmClosedFor(node.id); onConfirmAccept(false); }}>{t("designTree.action.cancel")}</button>
        </div>
      </div>}
    </div>}
    {node.kind === "pending" && <p className="design-tree-card__note">{t("designTree.pendingNote")}</p>}
    {refusal && <p className="design-tree-card__refusal" role="alert">{refusal}</p>}
    {recordable && <div className="design-tree-card__actions">
      <button type="button" className="btn btn--small btn--primary" data-action="record" disabled={recording || busy}
        onClick={() => void recordAndContinue(node.id)}>{t(recording ? "stage.record.busy" : "stage.record.continue")}</button>
    </div>}
    {failure && <p className="design-tree-card__refusal" role="alert">{t("stage.record.failed", { reason: failure.detail })}</p>}
    {developerMode && <details className="design-tree-card__details">
      <summary>{t("designTree.details")}</summary>
      <dl>{technical(node).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    </details>}
  </aside>;
}

function technical(node: TreeNode): [string, string][] {
  const rows: [string, string | null | undefined][] = [
    ["node", node.id], ["run", node.runId], ["stageRef", node.stage?.ref], ["branch", node.stage?.branchId],
    ["candidate", node.candidate?.candidateId], ["legacy", node.candidate?.legacy], ["baseStageRef", node.candidate?.baseStageRef],
    ["blockedBy", node.candidate?.blockedBy.join(", ")],
    ["line", node.pending?.lineId], ["head", node.current?.headRunId],
  ];
  return rows.filter((row): row is [string, string] => typeof row[1] === "string" && row[1].length > 0);
}
