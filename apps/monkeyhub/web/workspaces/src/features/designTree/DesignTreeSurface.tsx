/**
 * The Design Tree surface (状态树): the project's growth tree beside
 * Modeling and Board. The canvas is the default view; the list shows the
 * same nodes to the keyboard and screen readers. Selecting a node opens its
 * side card; leaving returns to the surface the tree was opened from.
 * Loaded on demand with the canvas it draws on.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MessageKey } from "../../../../src/i18n/messages.en";
import { ErrorPanel } from "../../app/ErrorPanel";
import { useT } from "../../i18n/useT";
import DesignTreeCanvas from "./DesignTreeCanvas";
import { DesignTreeDetails } from "./DesignTreeDetails";
import { DesignTreeList } from "./DesignTreeList";
import { TreeIcon, type DesignTreeView } from "./DesignTreeBar";
import { CURRENT, type TreeNode } from "./model";
import type { ZoomLevel } from "./scene";
import type { DesignTreeData } from "./useDesignTree";
import { treeWords, type SurfaceName } from "./words";

export default function DesignTreeSurface({ data, markSeen, active, returnTo, onLeave, onView, onRecordEdits = null, focus = null }: {
  data: DesignTreeData;
  markSeen(candidateId: string): void;
  active: boolean;
  /** The surface the tree was opened from, and returns to. */
  returnTo: SurfaceName;
  onLeave(): void;
  onView(view: DesignTreeView): void;
  /** Modeling's Record edits and continue, when Modeling is open to record them (#302). */
  onRecordEdits?: (() => Promise<void>) | null;
  /** A node to open with its side card and bring into view, such as the ready options' Study (#302). */
  focus?: { node: string; request: number } | null;
}) {
  const t = useT();
  const [mode, setMode] = useState<"canvas" | "list">("canvas");
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmAccept, setConfirmAccept] = useState(false);
  const [fitRequest, setFitRequest] = useState(0);
  const [level, setLevel] = useState<ZoomLevel>("mid");
  const tree = data.tree;
  const words = useMemo(() => treeWords(t, tree), [t, tree]);
  const node = selected && tree ? tree.nodes.get(selected) ?? null : null;
  useEffect(() => { if (selected && tree && !tree.nodes.has(selected)) setSelected(null); }, [tree, selected]);
  const select = useCallback((id: string | null) => {
    setSelected(id);
    setConfirmAccept(false);
    const chosen = id ? tree?.nodes.get(id) : undefined;
    if (chosen?.kind === "candidate" && chosen.runId) markSeen(chosen.runId);
  }, [tree, markSeen]);
  const acceptFromCanvas = useCallback(() => { setSelected(CURRENT); setConfirmAccept(Boolean(tree?.accept.allowed)); }, [tree]);
  // Focus once per request, when the tree has the node: its side card opens and the canvas centres it.
  const [center, setCenter] = useState<{ node: string; request: number } | null>(null);
  const focused = useRef(0);
  useEffect(() => {
    if (!focus || focused.current === focus.request || !tree?.nodes.has(focus.node)) return;
    focused.current = focus.request;
    select(focus.node);
    setCenter(focus);
  }, [focus, tree, select]);
  useEffect(() => {
    if (!active) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setSelected(null); };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [active]);
  const view = (target: TreeNode) => {
    if (!target.runId) return;
    if (target.kind === "candidate") markSeen(target.runId);
    onView({ runId: target.runId, name: words.title(target), back: false });
  };
  const back = t("designTree.back", { surface: t(`designTree.surface.${returnTo}` as MessageKey) });

  return <section className="design-tree" aria-label={t("designTree.title")} data-level={level} data-mode={mode} data-status={data.status}>
    <header className="design-tree__head">
      <button type="button" className="btn btn--small design-tree__back" onClick={onLeave}><span aria-hidden="true">←</span> {back}</button>
      <div className="design-tree__title"><TreeIcon /><h2>{t("designTree.title")}</h2><span>{t("designTree.subtitle")}</span></div>
      <div className="design-tree__modes" role="group" aria-label={t("designTree.mode.label")}>
        <button type="button" aria-pressed={mode === "canvas"} onClick={() => setMode("canvas")}>{t("designTree.mode.canvas")}</button>
        <button type="button" aria-pressed={mode === "list"} onClick={() => setMode("list")}>{t("designTree.mode.list")}</button>
      </div>
      {mode === "canvas" && tree && <button type="button" className="btn btn--small" onClick={() => setFitRequest((value) => value + 1)}>{t("designTree.fit")}</button>}
    </header>
    {data.source && !data.admissions && <p className="design-tree__notice">{t("designTree.noAdmissions")}</p>}
    {data.source && data.admissions && (data.source.history.warnings?.length ?? 0) > 0 &&
      <p className="design-tree__notice">{t("designTree.admissionWarnings")}</p>}
    {tree && data.error && <p className="design-tree__notice" role="status">{t("designTree.failed")}
      <button type="button" className="btn btn--small" onClick={data.reload}>{t("designTree.retry")}</button></p>}
    <div className="design-tree__body">
      {!tree ? <div className="design-tree__state" role="status">
        {!data.available ? t("designTree.unavailable") : data.status === "failed" && data.error ? <><ErrorPanel error={data.error} what="GET /api/design-history" />
          <button type="button" className="btn btn--small" onClick={data.reload}>{t("designTree.retry")}</button></> : t("designTree.loading")}
      </div> : mode === "canvas" ? <>
        <DesignTreeCanvas tree={tree} words={words.scene} selected={selected} fitRequest={fitRequest} centerOn={center} title={t("designTree.title")}
          onSelect={select} onAccept={acceptFromCanvas} onLevel={setLevel} />
        <p className="visually-hidden">{t("designTree.canvasNote")}</p>
      </> : <DesignTreeList tree={tree} words={words} selected={selected} onSelect={select} />}
      {tree && node && <DesignTreeDetails tree={tree} node={node} words={words} data={data} confirmAccept={confirmAccept}
        onConfirmAccept={setConfirmAccept} onClose={() => select(null)} onView={view} onRecordEdits={onRecordEdits} />}
    </div>
    <footer className="design-tree__foot"><span>{t("designTree.hint")}</span><span>{t("designTree.legend")}</span></footer>
  </section>;
}
