/**
 * How the tree's facts read in the viewer's language. One place, so the
 * chip, the canvas, the list and the inspector name a node the same way,
 * and no raw id or hash reaches the main copy.
 */
import { translateMessage, type MessageParameters } from "../../../../../../packages/web-shared/src/i18n.js";
import type { StudioApiError } from "../../api/project-runtime/client";
import type { TFunction } from "../../i18n/useT";
import type { Language } from "../settings/preferences";
import { DESIGN_TREE_UNDO_MOVED, DESIGN_TREE_UNSYNCED } from "./continueUndo";
import type { Fork } from "./layout";
import { candidateNodeId, checkOf, CURRENT, type CleanedDraft, type GrowthTree, type PendingStatus, type TreeNode } from "./model";
import type { SceneWords } from "./scene";

/**
 * The tree's action copy that the catalogs do not carry yet. GH-244 claims the
 * catalogs, so it stays here until it moves into them (review §4 step 1).
 */
interface ActionCopy {
  /** FN-5: the toast after Continue, naming the new Current. */
  readonly continued: string;
  readonly undo: string;
  readonly undoing: string;
  /** FN-5: the toast after Accept as next Stage; acceptance has no Undo. */
  readonly accepted: string;
  readonly undone: string;
  /** An Undo that found Current changed since its Continue: nothing was put back. */
  readonly undoMoved: string;
}

const ACTION_COPY: Readonly<Record<Language, ActionCopy>> = {
  en: {
    continued: "Current is now “{name}”", undo: "Undo", undoing: "Undoing…", accepted: "Accepted as {stage}",
    undone: "Undone · Current is back where it was", undoMoved: "Current has changed since the Continue; nothing was undone.",
  },
  "zh-CN": {
    continued: "当前已改为「{name}」", undo: "撤销", undoing: "正在撤销…", accepted: "已接受为 {stage}",
    undone: "已撤销 · 当前已回到原处", undoMoved: "继续之后当前已有变化，未撤销。",
  },
};

/** One line of the action copy, filled the way the catalogs are. */
export const actionWords = (language: Language) =>
  (key: keyof ActionCopy, parameters?: MessageParameters): string => translateMessage(ACTION_COPY[language], key, parameters);

/** A refused Continue, Accept or Undo, in the words it gets wherever it was asked for. */
export function refusalWords(t: TFunction, language: Language, error: StudioApiError): string {
  return error.code === DESIGN_TREE_UNSYNCED ? t("designTree.outcome.unsynced")
    : error.code === DESIGN_TREE_UNDO_MOVED ? actionWords(language)("undoMoved")
      : error.code === "CANDIDATE_REJECTED" ? t("designTree.outcome.rejected")
        : t("designTree.outcome.refused", { reason: error.detail });
}

/** The actor id an explicit act in the Studio carries when nobody signed in. */
const LOCAL_ACTOR = "studio:explicit-user-action";
/** The origin an admission carries when the Hub Agent closed a chat task on the user's words. */
const HUB_AGENT = "hub-agent";
/** A label that already names its option, such as "A Courtyard gate": it gets no second letter. */
const OWN_LETTER = /^[A-Za-z]\s/;

export type SurfaceName = "arch" | "board" | "drawing" | "render" | "publish";

export function treeWords(t: TFunction, tree: GrowthTree | null) {
  const stageName = (node: TreeNode) => `S${node.stage!.number}${node.stage!.name ? ` · ${node.stage!.name}` : ""}`;
  const optionName = (node: TreeNode) => node.label
    ? `${node.letter && !OWN_LETTER.test(node.label) ? `${node.letter} · ` : ""}${node.label}`
    : t("designTree.optionUnnamed", { letter: node.letter ?? "" }).trim();
  // #575: a step of Current's line is called what it was named, else what asked for it, else its place on the line.
  const stepName = (node: TreeNode) => node.label ?? node.step!.request ?? t("designTree.step.numbered", { number: node.step!.number });
  // #575: a drafts card names the kept drafts it folds; one that holds only drafts the project cleaned says so.
  const draftsName = (node: TreeNode) => node.drafts!.runs.length || !node.drafts!.cleaned.length
    ? t("designTree.drafts.group", { count: node.drafts!.runs.length })
    : t("designTree.drafts.cleanedGroup", { count: node.drafts!.cleaned.length });
  // The folds and the line's steps and drafts are named the same on a card and everywhere else.
  const lineName = (node: TreeNode): string | null => node.kind === "step" ? stepName(node)
    : node.kind === "fold" ? t("designTree.fold.steps", { count: node.fold!.steps.length })
      : node.kind === "drafts" ? draftsName(node)
        : node.kind === "draft" ? node.label ?? t("designTree.draft.unnamed") : null;
  // #353: a card's name beside its letter, which the card already shows; a label's own letter is not said twice.
  const cardName = (node: TreeNode): string => node.kind === "pending" ? node.label ?? t("designTree.pending.unnamed")
    : lineName(node) ?? (!node.label ? t("designTree.optionUnnamed", { letter: "" }).trim()
      : node.letter && OWN_LETTER.test(node.label) && node.label[0].toUpperCase() === node.letter ? node.label.slice(2).trim() : node.label);
  const pendingText = (status: PendingStatus) => t(status === "running" ? "designTree.pending.running"
    : status === "queued" ? "designTree.pending.queued" : "designTree.pending.interrupted");
  const title = (node: TreeNode): string => node.kind === "stage" ? stageName(node)
    : node.kind === "candidate" ? optionName(node)
      : node.kind === "pending" ? node.label ?? t("designTree.pending.unnamed")
        : node.kind === "current" ? t("designTree.current") : lineName(node) ?? t("designTree.origin");
  const byId = (id: string | null) => (id && tree?.nodes.get(id)) || null;
  /** A run of Current's line by its name, a folded step's included. */
  const lineRun = (runId: string | null): string | null => {
    const node = runId ? tree?.lineNodes.get(runId) : undefined;
    return node ? title(node) : null;
  };
  // A Study a closed loop admitted has no name of its own: it is named after where it started.
  const studyName = (id: string | null): string | null => {
    const study = id ? tree?.studies.get(id) : undefined;
    if (!study) return null;
    if (study.label) return study.label;
    const base = byId(byId(study.members[0] ?? null)?.parent ?? null);
    return base ? t("designTree.study.from", { base: title(base) }) : t("designTree.study.unnamed");
  };
  const actor = (value: string | null) => !value ? null : value === LOCAL_ACTOR ? t("designTree.actor.you") : value;
  // An admission recorded afterwards, when a person reviewed earlier work, says so; one the Hub Agent
  // made on the user's words in chat is the Agent's act, not "you" (#404 F6). The retained actor is unchanged.
  const admitter = (candidate: NonNullable<TreeNode["candidate"]>) => candidate.admittedOrigin === "retroactive"
    ? t("designTree.actor.retroactive") : candidate.admittedOrigin === HUB_AGENT ? t("designTree.actor.hubAgent")
      : actor(candidate.admittedBy);
  const currentAt = (): string => {
    const current = byId(CURRENT);
    const anchor = byId(current?.parent ?? null);
    if (!current?.current || !anchor) return "";
    // Behind a fold, Current follows its last folded step.
    const name = anchor.kind === "fold" && anchor.fold!.steps.length ? title(anchor.fold!.steps.at(-1)!) : title(anchor);
    const edits = current.current.editsAfter;
    return edits === 0 ? t("designTree.currentAt", { node: name })
      : t(edits === 1 ? "designTree.currentAfterOne" : "designTree.currentAfter", { node: name, count: edits });
  };
  const status = (node: TreeNode): string => {
    if (!tree) return "";
    if (node.kind === "pending") return pendingText(node.pending!.status);
    if (node.kind === "step") return t("designTree.step.numbered", { number: node.step!.number });
    if (node.kind === "draft") return t("designTree.status.superseded", { node: lineRun(node.draft!.supersededBy) ?? "—" });
    if (node.kind === "fold") return node.fold!.drafts ? t("designTree.fold.drafts", { count: node.fold!.drafts }) : t("designTree.fold.open");
    if (node.kind === "drafts") {
      // #575: what the project cleaned, quietly, under the kept drafts' name; nothing more where it cleaned none.
      const { runs, cleaned, retentionDays: days } = node.drafts!;
      return !cleaned.length ? t("designTree.drafts.open") : runs.length
        ? t("designTree.drafts.cleanedShort", { count: cleaned.length, days }) : t("designTree.drafts.restorable", { days });
    }
    if (node.kind !== "candidate") return "";
    const accepted = byId(node.candidate!.acceptedStage);
    // The runtime also names a Stage on the nearest admitted option its accepted run
    // grew from; only the option that is that run was accepted as it.
    const own = accepted?.stage?.candidateId === node.candidate!.candidateId;
    const stage = accepted ? stageName(accepted) : "";
    if (tree.onTrunk.has(node.id)) {
      return byId(CURRENT)?.parent === node.id ? t("designTree.status.current") : !accepted ? t("designTree.status.line")
        : t(own ? "designTree.status.accepted" : "designTree.status.grew", { stage });
    }
    if (tree.continued.has(node.id)) return !accepted ? t("designTree.status.earlier")
      : t(own ? "designTree.status.acceptedEarlier" : "designTree.status.grewEarlier", { stage });
    return t("designTree.status.ready");
  };
  // Longest first; the far view shortens a count rather than letting it collide.
  const fork = (value: Fork): string[] => {
    const options = value.options ? t(value.options === 1 ? "designTree.fork.option" : "designTree.fork.options", { count: value.options }) : "";
    const continued = value.continued ? t("designTree.fork.continued", { count: value.continued }) : "";
    const running = value.pending ? t("designTree.fork.running", { count: value.pending }) : "";
    const variants = [[options, continued, running], [options, running], [options || running]]
      .map((parts) => parts.filter(Boolean).join(" · ")).concat(String(value.options + value.pending));
    return [...new Set(variants.filter(Boolean))];
  };
  // #353: what an option's status bar says, in words: the inspector's Checks and the legend.
  const check = (node: TreeNode): string | null => {
    const value = checkOf(node);
    return value === "held" ? t("designTree.check.held")
      : value === "violated" ? t("designTree.check.violatedCount", { count: node.candidate!.blockedBy.length })
        : value === "unchecked" ? t("designTree.check.unchecked") : value === "running" ? t("designTree.check.running") : null;
  };
  /**
   * A column's header. A Stage's column: its S-number and accepted name. The trailing column holds the work
   * growing towards the next Stage; it gets that Stage's number only when it follows the line's newest Stage,
   * the one a Stage can be accepted after.
   */
  const column = (stage: string | null, after: string | null): { readonly id: string; readonly name: string; readonly next: boolean } => {
    const node = byId(stage);
    if (node?.stage) return { id: `S${node.stage.number}`, name: node.stage.name ?? "", next: false };
    const accept = tree?.accept;
    return { id: after !== null && after === accept?.lineHeadStage && accept.nextLabel ? accept.nextLabel : "", name: t("designTree.column.next"), next: true };
  };
  /** #575: a drafts card's whole account of what the project cleaned, for the list and the inspector. */
  const cleaned = (node: TreeNode): string | null => node.kind === "drafts" && node.drafts!.cleaned.length
    ? t("designTree.drafts.cleaned", { count: node.drafts!.cleaned.length, days: node.drafts!.retentionDays }) : null;
  /** #575: what superseded a cleaned draft: the line's step by its name, else a later result, else an attempt that never finished. */
  const cleanedBy = (draft: CleanedDraft): string => {
    if (!draft.supersededBy) return t("designTree.cleaned.unfinished");
    const option = byId(candidateNodeId(draft.supersededBy));
    const name = lineRun(draft.supersededBy) ?? (option ? title(option) : null);
    return name ? t("designTree.status.superseded", { node: name }) : t("designTree.cleaned.later");
  };
  const accept = tree?.accept;
  const scene: SceneWords = {
    current: t("designTree.current"), origin: t("designTree.origin"), name: cardName, pending: pendingText, currentAt: currentAt(),
    accept: accept?.nextLabel ? t("designTree.action.accept", { stage: accept.nextLabel }) : t("designTree.action.acceptNext"),
    acceptBlocked: t("designTree.action.acceptNext"), status, fork,
  };
  return { stageName, optionName, stepName, studyName, title, actor, admitter, currentAt, status, fork, pendingText, check, column, scene, byId, lineRun,
    cleaned, cleanedBy };
}

export type TreeWords = ReturnType<typeof treeWords>;

/** "3 Sep · 14:26", in the viewer's language; the raw timestamp stays in the details. */
export function whenText(value: string | null, language: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
