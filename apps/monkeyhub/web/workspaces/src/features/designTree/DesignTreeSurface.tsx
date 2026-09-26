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
import { MenuCommand, MenuSeparator, MenuTabs, StatusLine, SurfaceMenus } from "../chrome/SurfaceChrome";
import { usePreferences } from "../settings/preferences";
import { useT } from "../../i18n/useT";
import DesignTreeCanvas from "./DesignTreeCanvas";
import { DesignTreeDetails } from "./DesignTreeDetails";
import { DesignTreeList } from "./DesignTreeList";
import type { DesignTreeView } from "./DesignTreeBar";
import { CURRENT, type TreeNode } from "./model";
import type { ZoomLevel } from "./scene";
import type { DesignTreeData } from "./useDesignTree";
import { treeWords, type SurfaceName } from "./words";

// #337: the bar's quiet count; the Stage position beside it already counts running work.
const countWords = {
  "zh-CN": (options: number) => `${options} 个方案`,
  en: (options: number) => `${options} ${options === 1 ? "option" : "options"}`,
} as const;

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
  const { language } = usePreferences();
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
    // The node goes with the view: the chip's Continue from here continues exactly it (R2).
    onView({ runId: target.runId, name: words.title(target), back: false, node: target.id });
  };
  const back = t("designTree.back", { surface: t(`designTree.surface.${returnTo}` as MessageKey) });
  const counts = useMemo(() => {
    return countWords[language](tree ? [...tree.nodes.values()].filter((item) => item.kind === "candidate").length : 0);
  }, [tree, language]);

  return <section className="design-tree" aria-label={t("designTree.title")} data-level={level} data-mode={mode} data-status={data.status}>
    {/* #337 L2: the tree's menus, in the project bar. The rail and the pressed chip already name the surface, so the title is for screen readers. */}
    <h2 className="visually-hidden">{t("designTree.title")}</h2>
    <SurfaceMenus label={t("designTree.title")} active={active} end={tree ? counts : null}>
      <MenuCommand onClick={onLeave}><span aria-hidden="true">←</span> {back}</MenuCommand>
      <MenuSeparator />
      <MenuTabs label={t("designTree.mode.label")} value={mode} onChange={(next) => setMode(next)}
        options={[{ value: "canvas" as const, label: t("designTree.mode.canvas") }, { value: "list" as const, label: t("designTree.mode.list") }]} />
      {tree && tree.processedCount > 0 && <><MenuSeparator /><MenuCommand aria-pressed={data.showProcessed}
        onClick={() => data.setShowProcessed(!data.showProcessed)}>{data.showProcessed
          ? t("designTree.processed.hide") : t("designTree.processed.show", { count: tree.processedCount })}</MenuCommand></>}
      {mode === "canvas" && tree && <><MenuSeparator /><MenuCommand onClick={() => setFitRequest((value) => value + 1)}>{t("designTree.fit")}</MenuCommand></>}
    </SurfaceMenus>
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
    <StatusLine end={t("designTree.legend")}>{t("designTree.hint")}</StatusLine>
  </section>;
}
