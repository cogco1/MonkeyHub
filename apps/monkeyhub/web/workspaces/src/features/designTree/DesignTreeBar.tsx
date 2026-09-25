/**
 * The project's position, above whatever surface is open: the Stage chip.
 *
 * "S2 · Layout — Current · 2 running". It answers "where am I?" on every
 * surface and opens the Design Tree; pressed again on the tree, it returns to
 * the surface the tree was opened from. Admitted options this viewer has not
 * opened are its notice, "3 options ready · View", which opens the tree on
 * their Study; a result never opens itself (#302). While a node is viewed
 * read-only in Modeling it says so, with the way back to Current and Continue
 * from here (R2). Beside it, the last act that changed the design confirms
 * itself (FN-5); a refused one says why in the bar instead.
 * It carries no Excalidraw: the canvas loads only when the tree opens.
 */
import { useMemo, useState } from "react";
import { usePreferences } from "../settings/preferences";
import { useT } from "../../i18n/useT";
import { DESIGN_TREE_UNSYNCED } from "./continueUndo";
import { DesignTreeToast } from "./DesignTreeToast";
import { CURRENT, type GrowthTree, type TreeNode } from "./model";
import { useRecordAndContinue, type DesignTreeData } from "./useDesignTree";
import { refusalWords, treeWords } from "./words";
import "./designTree.css";

/** A node opened read-only in Modeling from the tree. `back` is the return to Current. */
export interface DesignTreeView {
  readonly runId: string;
  readonly name: string;
  readonly back: boolean;
  /** The one model of the run to show, when the view names it exactly (a Stage's own model). */
  readonly assetSha256?: string | null;
  /** The tree node the view was opened from, when it was: the node Continue from here continues. */
  readonly node?: string | null;
}

/** The Working Head's own model, read-only in Modeling: the way back from a viewed node. */
export function currentView(data: DesignTreeData): DesignTreeView | null {
  const head = data.tree?.nodes.get(CURRENT)?.runId;
  return head ? { runId: head, name: "", back: true } : null;
}

/**
 * The node a view stands for, so the chip can continue from it: the node it
 * was opened from, else the one node of its run (a Stage before the option it
 * was accepted from, as the tree draws it). A run the tree has no node for,
 * such as an export, has no Continue here.
 */
export function viewedNode(tree: GrowthTree | null, view: DesignTreeView): TreeNode | null {
  if (!tree) return null;
  const named = view.node ? tree.nodes.get(view.node) : undefined;
  if (named && named.runId === view.runId && (named.kind === "candidate" || named.kind === "stage")) return named;
  let option: TreeNode | null = null;
  for (const node of tree.nodes.values()) {
    if (node.runId !== view.runId) continue;
    if (node.kind === "stage") return node;
    if (node.kind === "candidate") option ??= node;
  }
  return option;
}

export function TreeIcon() {
  return <svg className="design-tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="19" cy="12" r="2" /><circle cx="12" cy="5" r="2" />
    <path d="M7 12h10M12 7v5M12 12l-4 6M12 12l4 6" /></svg>;
}

export function DesignTreeBar({ data, seen, open, viewing, onToggle, onBackToCurrent, onShowReady, onRecordEdits = null }: {
  data: DesignTreeData;
  seen: ReadonlySet<string>;
  /** The tree surface is the one on screen. */
  open: boolean;
  viewing: DesignTreeView | null;
  onToggle(): void;
  onBackToCurrent(): void;
  /** Open the tree on the ready option's Study, by its node id. */
  onShowReady(node: string): void;
  /** Modeling's Record edits and continue, for a Continue from here its unrecorded edits refused (#302). */
  onRecordEdits?: (() => Promise<void>) | null;
}) {
  const t = useT();
  const { language } = usePreferences();
  const tree = data.tree;
  const words = useMemo(() => treeWords(t, tree), [t, tree]);
  const stage = tree?.currentStage ? words.stageName(tree.nodes.get(tree.currentStage)!) : tree ? t("designTree.chip.noStage") : null;
  const fresh = tree ? tree.freshCandidates.filter((id) => !seen.has(tree.nodes.get(id)?.runId ?? "")) : [];
  const working = tree ? tree.counts.running + tree.counts.queued : 0;
  const position = stage === null ? t("designTree.title") : t("designTree.chip.position", { stage, position: t("designTree.current") });
  // A viewed run that is the Working Head is Current, not another model: it needs neither way back nor Continue.
  const head = tree?.nodes.get(CURRENT)?.runId ?? null;
  const shown = viewing && viewing.runId !== head ? viewing : null;
  const node = shown && data.canContinue ? viewedNode(tree, shown) : null;
  // What the chip itself asked to continue, from this view: its refusal is read here; the side card reads its own.
  const [asked, setAsked] = useState<{ view: DesignTreeView; node: string } | null>(null);
  const mine = node !== null && asked !== null && asked.view === shown && asked.node === node.id;
  const refused = mine && data.outcome?.kind === "refused" && data.outcome.node === node.id && data.outcome.error ? data.outcome.error : null;
  const { recording, failure, recordAndContinue } = useRecordAndContinue(data, onRecordEdits);
  const recordable = refused?.code === DESIGN_TREE_UNSYNCED && onRecordEdits !== null;
  const continueViewed = () => {
    if (!node || !shown) return;
    setAsked({ view: shown, node: node.id });
    void data.continueFrom(node.id);
  };
  return <div className="design-tree-bar" role="region" aria-label={t("designTree.bar")}>
    <button type="button" className="stage-chip" aria-pressed={open} data-state={data.status} disabled={!tree && data.status === "loading"}
      title={open ? t("designTree.chip.leave") : t("designTree.chip.open")} onClick={onToggle}>
      <TreeIcon />
      <span className="stage-chip__position">{position}</span>
      {working > 0 && <span className="stage-chip__attention" data-kind="running"><span aria-hidden="true">·</span> {t("designTree.chip.running", { count: working })}</span>}
      {data.status === "failed" && !tree && <span className="stage-chip__warning" aria-hidden="true">!</span>}
    </button>
    {fresh.length > 0 && <button type="button" className="stage-chip__ready" onClick={() => onShowReady(fresh[0]!)}>
      <span>{fresh.length === 1 ? t("designTree.chip.readyOne") : t("designTree.chip.ready", { count: fresh.length })}</span>
      <span aria-hidden="true">·</span> <strong>{t("designTree.action.view")}</strong>
    </button>}
    {shown && <span className="stage-chip__viewing" role="status">
      <span>{t("designTree.chip.viewing", { name: shown.name })}</span>
      <button type="button" className="btn btn--small" disabled={!tree?.nodes.get(CURRENT)} onClick={onBackToCurrent}>{t("designTree.chip.back")}</button>
      {node && <button type="button" className="btn btn--small btn--primary" data-action="continue" disabled={data.busy !== null || recording}
        onClick={continueViewed}>{data.busy?.kind === "continue" && data.busy.node === node.id ? t("designTree.action.continuing") : t("designTree.action.continue")}</button>}
    </span>}
    {/* One live region for the toast, present before it speaks. */}
    <div className="design-tree-bar__notice" role="status">
      {data.toast && <DesignTreeToast key={data.toast.id} data={data} toast={data.toast} words={words} />}
    </div>
    {mine && (refused || failure) && <p className="design-tree-bar__refusal" role="alert">
      {refused && <span>{refusalWords(t, language, refused)}</span>}
      {recordable && <button type="button" className="btn btn--small btn--primary" data-action="record" disabled={recording || data.busy !== null}
        onClick={() => void recordAndContinue(node.id)}>{t(recording ? "stage.record.busy" : "stage.record.continue")}</button>}
      {failure && <span>{t("stage.record.failed", { reason: failure.detail })}</span>}
    </p>}
  </div>;
}
