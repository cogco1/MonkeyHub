/**
 * The project's position, above whatever surface is open: the Stage chip.
 *
 * "S2 · Layout — Current · 3 new · 2 running". It answers "where am I?" on
 * every surface and opens the Design Tree; pressed again on the tree, it
 * returns to the surface the tree was opened from. While a node is viewed
 * read-only in Modeling it says so, with the way back to Current.
 * It carries no Excalidraw: the canvas loads only when the tree opens.
 */
import { useMemo } from "react";
import { useT } from "../../i18n/useT";
import { CURRENT } from "./model";
import type { DesignTreeData } from "./useDesignTree";
import { treeWords } from "./words";
import "./designTree.css";

/** A node opened read-only in Modeling from the tree. `back` is the return to Current. */
export interface DesignTreeView {
  readonly runId: string;
  readonly name: string;
  readonly back: boolean;
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

export function DesignTreeBar({ data, seen, open, viewing, onToggle, onBackToCurrent }: {
  data: DesignTreeData;
  seen: ReadonlySet<string>;
  /** The tree surface is the one on screen. */
  open: boolean;
  viewing: DesignTreeView | null;
  onToggle(): void;
  onBackToCurrent(): void;
}) {
  const t = useT();
  const tree = data.tree;
  const words = useMemo(() => treeWords(t, tree), [t, tree]);
  const stage = tree?.currentStage ? words.stageName(tree.nodes.get(tree.currentStage)!) : tree ? t("designTree.chip.noStage") : null;
  const fresh = tree ? tree.freshCandidates.filter((id) => !seen.has(tree.nodes.get(id)?.runId ?? "")).length : 0;
  const working = tree ? tree.counts.running + tree.counts.queued : 0;
  const position = stage === null ? t("designTree.title") : t("designTree.chip.position", { stage, position: t("designTree.current") });
  return <div className="design-tree-bar" role="region" aria-label={t("designTree.bar")}>
    <button type="button" className="stage-chip" aria-pressed={open} data-state={data.status} disabled={!tree && data.status === "loading"}
      title={open ? t("designTree.chip.leave") : t("designTree.chip.open")} onClick={onToggle}>
      <TreeIcon />
      <span className="stage-chip__position">{position}</span>
      {fresh > 0 && <span className="stage-chip__attention" data-kind="new"><span aria-hidden="true">·</span> {t("designTree.chip.new", { count: fresh })}</span>}
      {working > 0 && <span className="stage-chip__attention" data-kind="running"><span aria-hidden="true">·</span> {t("designTree.chip.running", { count: working })}</span>}
      {data.status === "failed" && !tree && <span className="stage-chip__warning" aria-hidden="true">!</span>}
    </button>
    {viewing && <span className="stage-chip__viewing" role="status">
      <span>{t("designTree.chip.viewing", { name: viewing.name })}</span>
      <button type="button" className="btn btn--small" disabled={!tree?.nodes.get(CURRENT)} onClick={onBackToCurrent}>{t("designTree.chip.back")}</button>
    </span>}
  </div>;
}
