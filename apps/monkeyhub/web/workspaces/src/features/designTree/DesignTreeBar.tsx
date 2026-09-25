/**
 * The project's position, above whatever surface is open: the Stage chip.
 *
 * "S2 · Layout — Current · 2 running". It answers "where am I?" on every
 * surface and opens the Design Tree; pressed again on the tree, it returns to
 * the surface the tree was opened from. Admitted options this viewer has not
 * opened are its notice, "3 options ready · View", which opens the tree on
 * their Study; a result never opens itself (#302). While a node is viewed
 * read-only in Modeling it says so, with the way back to Current. Beside it,
 * the last act that changed the design confirms itself (FN-5).
 * It carries no Excalidraw: the canvas loads only when the tree opens.
 */
import { useMemo } from "react";
import { useT } from "../../i18n/useT";
import { DesignTreeToast } from "./DesignTreeToast";
import { CURRENT } from "./model";
import type { DesignTreeData } from "./useDesignTree";
import { treeWords } from "./words";
import "./designTree.css";

/** A node opened read-only in Modeling from the tree. `back` is the return to Current. */
export interface DesignTreeView {
  readonly runId: string;
  readonly name: string;
  readonly back: boolean;
  /** The one model of the run to show, when the view names it exactly (a Stage's own model). */
  readonly assetSha256?: string | null;
}

/** The Working Head's own model, read-only in Modeling: the way back from a viewed node. */
export function currentView(data: DesignTreeData): DesignTreeView | null {
  const head = data.tree?.nodes.get(CURRENT)?.runId;
  return head ? { runId: head, name: "", back: true } : null;
}

export function TreeIcon() {
  return <svg className="design-tree-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true"><circle cx="5" cy="12" r="2" /><circle cx="19" cy="12" r="2" /><circle cx="12" cy="5" r="2" />
    <path d="M7 12h10M12 7v5M12 12l-4 6M12 12l4 6" /></svg>;
}

export function DesignTreeBar({ data, seen, open, viewing, onToggle, onBackToCurrent, onShowReady }: {
  data: DesignTreeData;
  seen: ReadonlySet<string>;
  /** The tree surface is the one on screen. */
  open: boolean;
  viewing: DesignTreeView | null;
  onToggle(): void;
  onBackToCurrent(): void;
  /** Open the tree on the ready option's Study, by its node id. */
  onShowReady(node: string): void;
}) {
  const t = useT();
  const tree = data.tree;
  const words = useMemo(() => treeWords(t, tree), [t, tree]);
  const stage = tree?.currentStage ? words.stageName(tree.nodes.get(tree.currentStage)!) : tree ? t("designTree.chip.noStage") : null;
  const fresh = tree ? tree.freshCandidates.filter((id) => !seen.has(tree.nodes.get(id)?.runId ?? "")) : [];
  const working = tree ? tree.counts.running + tree.counts.queued : 0;
  const position = stage === null ? t("designTree.title") : t("designTree.chip.position", { stage, position: t("designTree.current") });
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
    {viewing && <span className="stage-chip__viewing" role="status">
      <span>{t("designTree.chip.viewing", { name: viewing.name })}</span>
      <button type="button" className="btn btn--small" disabled={!tree?.nodes.get(CURRENT)} onClick={onBackToCurrent}>{t("designTree.chip.back")}</button>
    </span>}
    {/* One live region for the toast, present before it speaks. */}
    <div className="design-tree-bar__notice" role="status">
      {data.toast && <DesignTreeToast key={data.toast.id} data={data} toast={data.toast} words={words} />}
    </div>
  </div>;
}
