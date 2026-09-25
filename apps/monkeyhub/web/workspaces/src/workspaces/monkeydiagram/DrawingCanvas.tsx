import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError, type StudioApiError } from "../../api/client";
import type { DesignStageDto, ModelSourceDto, PlanDimensionChoicesDto, PlanStatusDto, PlanVectorDto, PlanDressingDto, SourceDocumentDto, WorkingSourceDto } from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import { usePreferences } from "../../features/settings/preferences";
import { DocumentSurface } from "./DocumentCanvas";
import { defaultPlanForm, drawingDocumentKey, keptOnChosenVersion, latestRevisions, liveAction, planFormFromDocument, type PlanForm } from "./drawingPlan";
import { DressingControls, DressingOverlay } from "./DrawingDressing";
import "./DrawingCanvas.css";

const copy = {
  en: { title: "Drawing", intro: "Cut plans that follow the current project model.", source: "Version to draw", revision: "Drawing", fresh: "New cut plan",
    noModel: "The current project model has no exact geometry to draw yet.", refresh: "Refresh sources", generating: "Generating…", generate: "Generate cut plan",
    rebuild: "Rebuild on this version", another: "Draw another version", anotherHint: "A version chosen here is drawn once and is not updated automatically.",
    earlier: "Earlier revisions", earlierView: "Earlier revision · not updated automatically", openLatest: "Open the current revision",
    live: "Follows the current project model", liveCurrent: "Current with the project model", updating: "Updating to the current model…",
    stale: "Not updated", chosenView: "Drawn from a chosen version · not updated automatically", followAgain: "Follow the current model again", workingVersion: "Working version (not accepted)", representation: "Drawing appearance", cutHeight: "Cut height", bottom: "View bottom", scale: "Scale denominator (1 : n)",
    graphics: "Linework and hatch", cutLine: "Cut line (paper mm)", visibleLine: "Visible line (paper mm)", hatch: "Hatch spacing (paper mm)",
    dimensions: "Saved dimensions",
    placement: "Label offset (paper mm)", remove: "Remove dimension", apply: "Save appearance as a revision", dirty: "Save appearance changes before downloading SVG.",
    status: "Source status", current: "Current", outdated: "Outdated", "partially-broken": "Some anchors are broken", unknown: "Source status unknown",
    checking: "Checking source…", sourceHint: "Heights use the source model unit:", unitUnknown: "Waiting for model units",
    broken: "Unresolved anchor", outsideView: "Dimension falls outside the drawing. Adjust its paper offset and save appearance.", empty: "Choose a model and generate a cut plan.",
    zoomOut: "Zoom out", zoomIn: "Zoom in", fit: "Fit page", sourceOfPage: "This drawing's source",
    download: "Download SVG", downloadHint: "Saved vector drawing, with its scale and entourage, for further editing in Illustrator.",
    settings: "Drawing settings",
    statusError: "Source status could not be read. Refresh to try again.", loading: "Loading drawing…" },
  "zh-CN": { title: "Drawing · 图纸", intro: "跟随项目当前模型的剖切平面。", source: "出图版本", revision: "图纸", fresh: "新建剖切平面",
    noModel: "项目当前模型还没有可出图的精确几何。", refresh: "刷新来源", generating: "正在生成…", generate: "生成剖切平面",
    rebuild: "基于此版本重建", another: "绘制其他版本", anotherHint: "在这里选择的版本只按一次绘制，不会自动更新。",
    earlier: "较早版本", earlierView: "较早版本 · 不自动更新", openLatest: "打开当前版本",
    live: "跟随项目当前模型", liveCurrent: "与项目当前模型一致", updating: "正在更新到当前模型…",
    stale: "尚未更新", chosenView: "按所选版本绘制 · 不自动更新", followAgain: "改为跟随当前模型", workingVersion: "工作版本（未接受）", representation: "图纸表达", cutHeight: "剖切高度", bottom: "视图底部", scale: "比例分母（1 : n）",
    graphics: "线型与填充", cutLine: "剖切线宽（纸面 mm）", visibleLine: "可见线宽（纸面 mm）", hatch: "填充间距（纸面 mm）",
    dimensions: "已有尺寸标注",
    placement: "标注偏移（纸面 mm）", remove: "移除尺寸", apply: "保存表达新版本", dirty: "表达修改尚未保存，保存后可下载 SVG。",
    status: "来源状态", current: "当前有效", outdated: "来源已更新", "partially-broken": "部分锚点断开", unknown: "来源状态未知",
    checking: "正在核对来源…", sourceHint: "高度使用模型单位：", unitUnknown: "正在读取模型单位",
    broken: "锚点未解析", outsideView: "标注超出图框，请调整纸面偏移后保存表达。", empty: "选择模型并生成剖切平面。",
    zoomOut: "缩小", zoomIn: "放大", fit: "适合页面", sourceOfPage: "此图来源",
    download: "下载 SVG", downloadHint: "下载已保存的矢量图，保留比例和配景，可在 Illustrator 中继续编辑。",
    settings: "图纸设置",
    statusError: "无法读取来源状态，请刷新重试。", loading: "正在读取图纸…" },
} as const;

type PlanTarget = { modelSource: ModelSourceDto; stageRef: string | null };

function PlanPreview({ source, file, vector, objects, selected, onSelect, onChange, disabled }: {
  source: SourceDocumentDto; file: File; vector: PlanVectorDto | null; objects: PlanDressingDto[];
  selected: string; onSelect(id: string): void; onChange(objects: PlanDressingDto[]): void; disabled: boolean;
}) {
  const { language } = usePreferences(), text = copy[language];
  const viewport = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState({ width: 600, height: 500 });
  const [zoom, setZoom] = useState(1), [ready, setReady] = useState(false);
  const onReady = useCallback((value: boolean) => setReady(value), []);
  const [baseImage, setBaseImage] = useState<string | null>(null);
  useEffect(() => {
    if (!vector) { setBaseImage(null); return; }
    const svg = new DOMParser().parseFromString(vector.svg, "image/svg+xml");
    svg.querySelector('[id="dressing"]')?.remove();
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], { type: "image/svg+xml" }));
    setReady(false); setBaseImage(url); return () => URL.revokeObjectURL(url);
  }, [vector]);
  const recipeFrame = source.viewRecipe?.frame as { crop_uv?: number[] } | undefined;
  const crop = recipeFrame?.crop_uv;
  useEffect(() => {
    const node = viewport.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setBounds({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(node); return () => observer.disconnect();
  }, []);
  const page = source.pages[0];
  if (!page) return null;
  const scale = Math.max(0.02, Math.min((bounds.width - 32) / page.width, (bounds.height - 32) / page.height)) * zoom;
  return <section className="drawing-preview" aria-label={source.fileName}>
    <div className="drawing-preview__tools">
      <button type="button" aria-label={text.zoomOut} disabled={zoom <= 0.25} onClick={() => setZoom(value => Math.max(0.25, value / 1.25))}>−</button>
      <button type="button" onClick={() => setZoom(1)}>{text.fit}</button>
      <button type="button" aria-label={text.zoomIn} disabled={zoom >= 8} onClick={() => setZoom(value => Math.min(8, value * 1.25))}>+</button>
      <span>{source.fileName}</span>
    </div>
    <div className="drawing-preview__viewport" ref={viewport} tabIndex={0} data-ready={ready}>
      <div className="drawing-preview__paper" style={{ width: page.width * scale, height: page.height * scale }}>
        {baseImage ? <img className="drawing-vector-base" src={baseImage} alt={source.fileName} onLoad={() => setReady(true)} />
          : <DocumentSurface file={file} page={page} scale={scale} onReady={onReady} />}
        {baseImage && vector && crop && <DressingOverlay objects={objects} vector={vector} crop={crop} selected={selected}
          onSelect={onSelect} onChange={onChange} language={language} disabled={disabled} />}
      </div>
    </div>
  </section>;
}

export default function DrawingCanvas({ projectId, active = true, refreshKey = 0 }: {
  projectId: string; active?: boolean; refreshKey?: number;
}) {
  const studio = useStudio(), { language } = usePreferences(), text = copy[language];
  const controls = useRef<HTMLFormElement>(null);
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]), [stages, setStages] = useState<DesignStageDto[]>([]);
  const [selected, setSelected] = useState(""), [target, setTarget] = useState("");
  const [explicitTarget, setExplicitTarget] = useState(false), [defaultTarget, setDefaultTarget] = useState("");
  const targetWasChosen = useRef(false); targetWasChosen.current = explicitTarget;
  const [automaticTarget, setAutomaticTarget] = useState<{ modelSource: ModelSourceDto; stageRef: string | null } | null>(null);
  const [form, setForm] = useState<PlanForm>(() => defaultPlanForm("meter")), [dirty, setDirty] = useState(false);
  const [choices, setChoices] = useState<PlanDimensionChoicesDto | null>(null);
  const [status, setStatus] = useState<PlanStatusDto | null>(null), [statusLoading, setStatusLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null), [loading, setLoading] = useState(true);
  const [vector, setVector] = useState<PlanVectorDto | null>(null), [selectedDressing, setSelectedDressing] = useState("");
  const [error, setError] = useState<StudioApiError | null>(null), [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  // #271: the Working Head this workspace follows, as the Project Runtime resolves it.
  const [live, setLive] = useState<WorkingSourceDto | null>(null), [liveBusy, setLiveBusy] = useState(false);
  const liveRevision = useRef<string | null | undefined>(undefined);
  const attemptedLive = useRef(new Set<string>());
  // The drawing a status was read for; a status never answers for another revision.
  const statusFor = useRef<string | null>(null);
  const opened = useRef(false);
  // Revisions this view wrote; a list read that began before one was written never drops it.
  const madeHere = useRef(new Set<string>());
  const source = documents.find(document => drawingDocumentKey(document) === selected) ?? null;
  const latest = useMemo(() => latestRevisions(documents), [documents]);
  const latestKeys = useMemo(() => new Set(latest.map(drawingDocumentKey)), [latest]);
  const earlier = documents.filter(document => !latestKeys.has(drawingDocumentKey(document)));
  const historical = source !== null && !latestKeys.has(drawingDocumentKey(source));
  // A drawing made from a chosen version keeps it until a person rebuilds it on the current model.
  const kept = keptOnChosenVersion(source);
  const liveMode = !explicitTarget && !historical && !kept;
  const chosenStage = stages.find(item => item.stageRef === target) ?? null;
  const liveTarget = live?.compatible && live.source ? { modelSource: live.source, stageRef: live.stageRef ?? null } : null;
  const stage: PlanTarget | null = explicitTarget ? (chosenStage ? { modelSource: chosenStage.modelSource, stageRef: chosenStage.stageRef } : null)
    : source ? automaticTarget : liveTarget;
  const selectedTargetValue = explicitTarget ? target : "";
  const lengthUnit = choices?.lengthUnit ?? status?.lengthUnit ?? "";
  const scopeKey = JSON.stringify([projectId, selected, selectedTargetValue, explicitTarget, active]);
  const scope = useRef({ key: scopeKey });
  if (scope.current.key !== scopeKey) scope.current = { key: scopeKey };
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true); setError(null);
    void Promise.all([studio.documents(), studio.designHistory(), studio.workingSource("drawing")]).then(([list, history, working]) => {
      if (cancelled) return;
      if (list.projectId !== projectId || history.projectId !== projectId || working.projectId !== projectId) throw new Error("The drawing workspace belongs to another project.");
      const plans = list.documents.filter(item => item.viewRecipe?.kind === "cut-plan");
      const listed = new Set(plans.map(drawingDocumentKey));
      setDocuments(current => [...plans, ...current.filter(item =>
        madeHere.current.has(drawingDocumentKey(item)) && !listed.has(drawingDocumentKey(item)))]);
      setStages(history.stages); setLive(working);
      liveRevision.current = working.revisionSha256 ?? null;
      const head = history.branches.find(branch => branch.branchId === history.branchId)?.headStageRef;
      const defaultStage = head ?? history.stages.at(-1)?.stageRef ?? "";
      setDefaultTarget(defaultStage);
      setTarget(current => targetWasChosen.current && history.stages.some(item => item.stageRef === current) ? current : defaultStage);
      // Open the newest drawing instead of asking which revision is current.
      const newest = latestRevisions(plans)[0];
      if (!opened.current && newest) { opened.current = true; openDocument(newest); }
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [studio, projectId, active, refreshKey, refresh]);
  useEffect(() => {
    // A visible drawing notices when the Working Head moves; the position's
    // revision is cheap to read and changes with every retained working result.
    if (!active) return;
    const timer = window.setInterval(() => {
      void studio.workingRevision().then(position => {
        if (liveRevision.current !== undefined && (position.revisionSha256 ?? null) !== liveRevision.current) setRefresh(value => value + 1);
      }).catch(() => { /* The next tick or an explicit refresh reads it again. */ });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [studio, active]);
  useEffect(() => {
    setChoices(null);
    if (!stage || !active) return;
    let cancelled = false;
    void studio.drawingPlanDimensions(stage.modelSource, stage.stageRef ?? undefined).then(value => {
      if (cancelled) return;
      setChoices(value);
      if (!source && !dirty) setForm(defaultPlanForm(value.lengthUnit));
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    return () => { cancelled = true; };
  }, [studio, projectId, stage?.modelSource.runId, stage?.modelSource.assetSha256, stage?.stageRef, active, refresh]);
  useEffect(() => {
    setFile(null); setVector(null); setSelectedDressing(""); setStatus(null); setAutomaticTarget(null);
    if (!source) { setStatusLoading(false); return; }
    let cancelled = false;
    if (source.revisionRef) void studio.drawingPlanVector({ runId: source.runId, assetSha256: source.assetSha256, revisionRef: source.revisionRef })
      .then(value => { if (!cancelled) setVector(value); }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    void studio.documentFile(source.runId, source.assetSha256, source.fileName, source.revisionRef).then(value => {
      if (!cancelled) setFile(value);
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    return () => { cancelled = true; };
  }, [studio, projectId, selected]);
  useEffect(() => {
    setStatus(null);
    if (!source || !source.revisionRef || !active) { setStatusLoading(false); return; }
    let cancelled = false;
    setStatusLoading(true);
    void studio.drawingPlanStatus({ runId: source.runId, assetSha256: source.assetSha256, revisionRef: source.revisionRef,
      ...(explicitTarget && stage ? { targetModelSource: stage.modelSource, targetStageRef: stage.stageRef } : {}) }).then(value => {
      if (!cancelled) {
        statusFor.current = drawingDocumentKey(source);
        setStatus(value);
        if (!explicitTarget) setAutomaticTarget(value.targetModelSource
          ? { modelSource: value.targetModelSource, stageRef: value.targetStageRef ?? null } : null);
      }
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); }).finally(() => { if (!cancelled) setStatusLoading(false); });
    return () => { cancelled = true; };
  }, [studio, projectId, selected, explicitTarget, target, active, refreshKey, refresh]);

  function openDocument(document: SourceDocumentDto | null) {
    setSelected(document ? drawingDocumentKey(document) : ""); setVector(null); setDirty(false); setError(null);
    setExplicitTarget(false); setAutomaticTarget(null); setStatus(null);
    if (!document) setTarget(defaultTarget);
    setForm(document ? planFormFromDocument(document, lengthUnit) : defaultPlanForm(lengthUnit));
  }
  function chooseDocument(key: string) {
    opened.current = true;
    openDocument(documents.find(item => drawingDocumentKey(item) === key) ?? null);
  }
  function update(patch: Partial<PlanForm>) { setForm(current => ({ ...current, ...patch })); setDirty(true); }
  const dimensions = form.dimensions ?? [];
  /** Write a revision on a target, or on the drawing's own source; `follow` records a person's choice. */
  const generate = async (target: PlanTarget | null, options: { following?: boolean; follow?: "live" | "frozen" } = {}) => {
    const modelSource = target ? target.modelSource : source?.modelSource;
    const stageRef = target ? target.stageRef : source?.sourceStageRef;
    if (!modelSource || busy || !active || !controls.current?.reportValidity()) return;
    const origin = scope.current;
    setBusy(true); setLiveBusy(Boolean(options.following)); setError(null);
    try {
      const result = await studio.drawingPlan({ projectId, modelSource, sourceStageRef: stageRef, ...form,
        ...(source?.drawingId ? { drawingId: source.drawingId } : {}), ...(source?.revisionRef ? { previousRevisionRef: source.revisionRef } : {}),
        ...(options.follow ? { follow: options.follow } : {}) });
      if (!mounted.current || scope.current !== origin) return;
      madeHere.current.add(drawingDocumentKey(result));
      setDocuments(current => [...current.filter(item => drawingDocumentKey(item) !== drawingDocumentKey(result)), result]);
      if (drawingDocumentKey(result) !== selected) setVector(null);
      setSelected(drawingDocumentKey(result)); setForm(planFormFromDocument(result, lengthUnit)); setDirty(false);
    } catch (cause) { if (mounted.current && scope.current === origin) setError(asStudioApiError(cause)); }
    finally { if (mounted.current) { setBusy(false); setLiveBusy(false); } }
  };
  useEffect(() => {
    // A LIVE drawing rebinds once to each new Working Head; it never loops.
    if (!source || !status || busy || statusLoading || !active || statusFor.current !== drawingDocumentKey(source)) return;
    const key = [drawingDocumentKey(source), status.targetModelSource?.runId, status.targetModelSource?.stateDigest].join("|");
    if (!stage || liveAction({ live: liveMode, dirty, attempted: attemptedLive.current.has(key), status }) !== "rebuild") return;
    attemptedLive.current.add(key);
    void generate(stage, { following: true, follow: "live" });
  }, [source, status, stage, busy, statusLoading, active, liveMode, dirty]);
  const sourceName = (document: SourceDocumentDto) => stages.find(item => item.stageRef === document.sourceStageRef)?.label ??
    (live?.head && document.modelSource?.runId === live.head.runId && live.head.label ? live.head.label : text.workingVersion);
  const action = source ? liveAction({ live: liveMode, dirty, attempted: false, status }) : "none";
  const statusLine = statusLoading ? text.checking : liveBusy ? text.updating
    : historical ? text.earlierView : explicitTarget || kept ? text.chosenView
    : action === "blocked" ? text.stale
    : status?.status === "current" && !status.bindingChanged ? text.liveCurrent : text[status?.status ?? "unknown"];
  const downloadSvg = () => {
    if (!source || !vector || busy || dirty || !active) return;
    const url = URL.createObjectURL(new Blob([vector.svg], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = source.fileName.replace(/\.[^.]+$/, "") + ".svg"; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const numeric = (key: "cutHeight" | "bottom" | "scaleDenominator" | "cutLineMm" | "visibleLineMm" | "hatchSpacingMm", label: string, min?: number, step = "any") =>
    <label className="drawing-field">{label}<input type="number" min={min} step={step} required value={Number.isFinite(form[key]) ? form[key] : ""} onChange={event => update({ [key]: event.currentTarget.valueAsNumber })} /></label>;

  return <div className="drawing-workspace" aria-label={text.title}>
    <header className="drawing-header"><div><h1>{text.title}</h1><p>{text.intro}</p></div>
      <button type="button" disabled={busy || loading} onClick={() => setRefresh(value => value + 1)}>{text.refresh}</button></header>
    <div className="drawing-body">
      <div className="drawing-main">
        <div className="drawing-context">
        <div className="drawing-context__fields">
        <label className="drawing-field">{text.revision}<select value={selected} disabled={busy} onChange={event => chooseDocument(event.target.value)}>
          <option value="">{text.fresh}</option>{latest.map(item => <option key={drawingDocumentKey(item)} value={drawingDocumentKey(item)}>
            {item.fileName}</option>)}
          {earlier.length > 0 && <optgroup label={text.earlier}>{earlier.map(item => <option key={drawingDocumentKey(item)} value={drawingDocumentKey(item)}>
            {item.fileName} · {item.generatedAt ?? item.revisionRef?.split("/").at(-1)?.slice(0, 8)}</option>)}</optgroup>}</select></label>
        <div className="drawing-context__target">
          <p className="drawing-field__hint" role="note">{live && !live.compatible && live.reason && !source ? live.reason : text.live}</p>
          <details className="drawing-another" open={explicitTarget || undefined}><summary>{text.another}</summary>
            <label className="drawing-field">{text.source}<select value={selectedTargetValue} disabled={busy || loading || statusLoading}
              onChange={event => { setTarget(event.target.value); setExplicitTarget(event.target.value !== ""); setError(null); }}>
              <option value="">{text.live}</option>
              {stages.map(item => <option key={item.stageRef} value={item.stageRef}>{item.label} · {item.branchId}</option>)}</select></label>
            <p className="drawing-field__hint">{text.anotherHint}</p>
            {explicitTarget && source && <button type="button" disabled={!stage || busy || statusLoading} onClick={() => void generate(stage, { follow: "frozen" })}>{text.rebuild}</button>}
          </details></div>
        </div>
        {source && <section className="drawing-status" aria-label={text.status}
          data-follow={historical || explicitTarget || kept ? "frozen" : "live"}
          data-status={liveBusy ? "updating" : action === "blocked" ? "outdated" : status?.status ?? "unknown"}>
          <div className="drawing-status__summary">
            <span className="drawing-source">{text.sourceOfPage}: <b>{sourceName(source)}</b></span>
            <strong role="status">{statusLine}</strong>
            <p>{status?.detail ?? (!statusLoading ? text.statusError : "")}</p>
          </div>
          {historical && latest.some(item => item.drawingId === source.drawingId) &&
            <button type="button" disabled={busy} onClick={() => chooseDocument(drawingDocumentKey(latest.find(item => item.drawingId === source.drawingId)!))}>{text.openLatest}</button>}
          {kept && !historical && !explicitTarget &&
            <button type="button" disabled={busy || !liveTarget} onClick={() => void generate(liveTarget, { follow: "live" })}>{text.followAgain}</button>}
        </section>}
        </div>
        <div className="drawing-canvas">{source && file ? <PlanPreview key={selected} source={source} file={file} vector={vector} objects={form.dressing} selected={selectedDressing}
          onSelect={setSelectedDressing} onChange={dressing => update({ dressing })} disabled={busy || !active} />
          : <div className="drawing-empty" role="status">{loading || source ? text.loading : stage ? text.empty : live?.reason ?? text.noModel}</div>}</div>
      </div>
      <form ref={controls} className="drawing-controls" aria-label={text.settings} onSubmit={event => { event.preventDefault(); void generate(source ? null : stage, !source && explicitTarget ? { follow: "frozen" } : {}); }}>
        <div className="drawing-controls__fields">
        <p className="drawing-unit">{lengthUnit ? `${text.sourceHint} ${lengthUnit}` : text.unitUnknown}</p>
        <fieldset disabled={busy || !active || !lengthUnit}><legend>{text.representation}</legend>
          {numeric("cutHeight", `${text.cutHeight} (${lengthUnit || "…"})`)}
          {numeric("bottom", `${text.bottom} (${lengthUnit || "…"})`)}
          {numeric("scaleDenominator", text.scale, 1, "1")}
          <details><summary>{text.graphics}</summary>{numeric("cutLineMm", text.cutLine, 0.01)}{numeric("visibleLineMm", text.visibleLine, 0.01)}{numeric("hatchSpacingMm", text.hatch, 0.1)}</details>
        </fieldset>
        {source && vector && form.cropUv && <DressingControls objects={form.dressing} vector={vector} crop={form.cropUv}
          selected={selectedDressing} onSelect={setSelectedDressing} onChange={dressing => update({ dressing })}
          disabled={busy || !active} language={language} unit={lengthUnit} status={status} />}
        {dimensions.length > 0 && <fieldset disabled={busy || !active}><legend>{text.dimensions}</legend>
          {dimensions.map(dimension => {
            const resolved = status?.dimensions?.find(item => item.id === dimension.id);
            const label = choices?.dimensions.find(item => item.entityRef === dimension.entityRef && item.openingId === dimension.openingId)?.label ?? dimension.openingId;
            return <div className="drawing-dimension" key={dimension.id}>
              <strong>{label}</strong>{resolved?.label && <span>{resolved.label}</span>}
              {resolved && resolved.status !== "resolved" && <p className="drawing-anchor-warning">{resolved.status === "outside-view" ? text.outsideView : resolved.detail ?? text.broken}</p>}
              <label className="drawing-field">{text.placement}<input type="number" min="-100" max="100" step="1" required value={Number.isFinite(dimension.placement?.offsetMm ?? 8) ? dimension.placement?.offsetMm ?? 8 : ""}
                onChange={event => update({ dimensions: dimensions.map(item => item.id === dimension.id ? { ...item, placement: { ...item.placement, offsetMm: event.currentTarget.valueAsNumber } } : item) })} /></label>
              <button type="button" onClick={() => update({ dimensions: dimensions.filter(item => item.id !== dimension.id) })}>{text.remove}</button>
            </div>;
          })}
        </fieldset>}
        </div>
        <div className="drawing-actions">
        {dirty && <p role="status">{text.dirty}</p>}
        <button className="btn btn--accent" type="submit" disabled={busy || loading || !lengthUnit || (!source && !stage) || Boolean(source && !source.modelSource)}>
          {busy ? text.generating : source ? text.apply : text.generate}</button>
        {source && <><button className="drawing-download" type="button" disabled={busy || dirty || !active || !vector} onClick={downloadSvg}>{text.download}</button>
          <p>{text.downloadHint}</p></>}
        {error && <ErrorPanel error={error} what={text.title} />}
        </div>
      </form>
    </div>
  </div>;
}
