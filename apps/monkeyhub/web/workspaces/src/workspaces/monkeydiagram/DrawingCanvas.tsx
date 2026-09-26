import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError, type StudioApiError } from "../../api/client";
import type { DesignStageDto, ModelSourceDto, PlanDimensionChoicesDto, PlanStatusDto, PlanVectorDto, PlanDressingDto, ProjectArtifactDto, SectionLineDto, SourceDocumentDto, WorkingSourceDto } from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import { usePreferences } from "../../features/settings/preferences";
import { DocumentSurface } from "./DocumentCanvas";
import { defaultPlanForm, drawingDocumentKey, keptOnChosenVersion, latestRevisions, liveAction, PAPER_PENS, planFormFromDocument, planRequestFields, type PlanForm } from "./drawingPlan";
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
    placement: "Label offset (paper mm)", remove: "Remove dimension", saving: "Saving appearance…", held: "Appearance changes are not saved yet; fix the marked field or retry.", retry: "Retry saving",
    status: "Source status", current: "Current", outdated: "Outdated", "partially-broken": "Some anchors are broken", unknown: "Source status unknown",
    checking: "Checking source…", sourceHint: "Heights use the source model unit:", unitUnknown: "Waiting for model units",
    broken: "Unresolved anchor", outsideView: "Dimension falls outside the drawing. Adjust its paper offset.", empty: "Choose a model and generate a cut plan.",
    zoomOut: "Zoom out", zoomIn: "Zoom in", fit: "Fit page", sourceOfPage: "This drawing's source",
    download: "Download SVG", downloadHint: "Saved vector drawing, with its scale and entourage, for further editing in Illustrator.",

    settings: "Drawing settings", imported: "Imported models", importModel: "Import 3DM or SKP", importing: "Importing model…",
    converted: "Converted from {source} to {target} for drawing; the original file is retained.",
    section: "Section perspective", sectionHint: "Cut the model with a vertical plane and draw the side you look toward in perspective; the cut is drawn true to scale.",
    sectionPlane: "Cut plane", sectionAcrossX: "Across X (x = position)", sectionAcrossY: "Across Y (y = position)", sectionPosition: "Position",
    sectionToward: "Look toward", eyeHeight: "Eye height above the lowest cut point", fov: "Field of view (degrees)",
    sectionGenerate: "Generate section perspective", sectionView: "Section perspective · true to scale at the cut plane",
    statusError: "Source status could not be read. Refresh to try again.", loading: "Loading drawing…",
    recipePen: "Project recipe", penHint: "A pen left empty follows the project recipe, or 0.35 / 0.18 / 2 mm without one; the drawing then shows the values it was made with.",
    objects: "Objects in this drawing", object: "Projected object", noObject: "Choose an object",
    pickHint: "Point at a line to see which object it draws; click it to select that object.",
    tooMany: "This drawing has {count} lines, so it is shown as an image; choose its objects from the list.",
    hideObject: "Hide this object", hiddenObjects: "Hidden objects", showObject: "Show again", notInModel: "not in this model", noMaterial: "no material",
    roles: { cut: "cut", beyond: "beyond", hidden: "hidden line", other: "line" } },

  "zh-CN": { title: "Drawing · 图纸", intro: "跟随项目当前模型的剖切平面。", source: "出图版本", revision: "图纸", fresh: "新建剖切平面",
    noModel: "项目当前模型还没有可出图的精确几何。", refresh: "刷新来源", generating: "正在生成…", generate: "生成剖切平面",
    rebuild: "基于此版本重建", another: "绘制其他版本", anotherHint: "在这里选择的版本只按一次绘制，不会自动更新。",
    earlier: "较早版本", earlierView: "较早版本 · 不自动更新", openLatest: "打开当前版本",
    live: "跟随项目当前模型", liveCurrent: "与项目当前模型一致", updating: "正在更新到当前模型…",
    stale: "尚未更新", chosenView: "按所选版本绘制 · 不自动更新", followAgain: "改为跟随当前模型", workingVersion: "工作版本（未接受）", representation: "图纸表达", cutHeight: "剖切高度", bottom: "视图底部", scale: "比例分母（1 : n）",
    graphics: "线型与填充", cutLine: "剖切线宽（纸面 mm）", visibleLine: "可见线宽（纸面 mm）", hatch: "填充间距（纸面 mm）",
    dimensions: "已有尺寸标注",
    placement: "标注偏移（纸面 mm）", remove: "移除尺寸", saving: "正在保存表达…", held: "表达修改尚未保存：请修正标出的字段或重试。", retry: "重试保存",
    status: "来源状态", current: "当前有效", outdated: "来源已更新", "partially-broken": "部分锚点断开", unknown: "来源状态未知",
    checking: "正在核对来源…", sourceHint: "高度使用模型单位：", unitUnknown: "正在读取模型单位",
    broken: "锚点未解析", outsideView: "标注超出图框，请调整纸面偏移。", empty: "选择模型并生成剖切平面。",
    zoomOut: "缩小", zoomIn: "放大", fit: "适合页面", sourceOfPage: "此图来源",
    download: "下载 SVG", downloadHint: "下载已保存的矢量图，保留比例和配景，可在 Illustrator 中继续编辑。",

    settings: "图纸设置", imported: "已导入模型", importModel: "导入 3DM 或 SKP", importing: "正在导入模型…",
    converted: "已将 {source} 转换为可出图的 {target}，原文件仍被保留。",
    section: "剖透视", sectionHint: "用竖直剖切面切开模型，以透视绘制所看的一侧；剖切面按比例绘制。",
    sectionPlane: "剖切面", sectionAcrossX: "垂直于 X（x = 位置）", sectionAcrossY: "垂直于 Y（y = 位置）", sectionPosition: "位置",
    sectionToward: "看向", eyeHeight: "视高（自剖切最低点起）", fov: "视角（度）",
    sectionGenerate: "生成剖透视", sectionView: "剖透视 · 剖切面处按比例",
    statusError: "无法读取来源状态，请刷新重试。", loading: "正在读取图纸…",
    recipePen: "项目设定", penHint: "留空的线宽和填充间距按项目表达设定取值，没有设定时为 0.35 / 0.18 / 2 mm；生成后显示图纸实际采用的数值。",
    objects: "图中对象", object: "投影对象", noObject: "选择对象",
    pickHint: "指向线条可查看它来自哪个对象，点击即可选中该对象。",
    tooMany: "此图共有 {count} 条线，以图片显示；请从列表中选择对象。",
    hideObject: "隐藏此对象", hiddenObjects: "已隐藏对象", showObject: "重新显示", notInModel: "不在当前模型中", noMaterial: "无材质",
    roles: { cut: "剖切", beyond: "看线", hidden: "隐藏线", other: "线" } },
} as const;
type Copy = (typeof copy)[keyof typeof copy];

type SourceAsset = { runId: string; assetSha256: string };
// The drawings this workspace opens. A section perspective is generated and read here; its plan-only
// appearance, status and vector controls belong to cut plans.
const DRAWING_KINDS = new Set(["cut-plan", "section-perspective"]);
const isCutPlan = (document: SourceDocumentDto | null) => document?.viewRecipe?.kind === "cut-plan";
const UNIT_METRES: Record<string, number> = { meter: 1, millimeter: 0.001, inch: 0.0254, foot: 0.3048 };
type SectionForm = { axis: "x" | "y"; position: number | null; toward: "+" | "-"; eyeHeight: number | null; fovDeg: number };
/** A vertical cut across X or Y at the position, keeping the side the eye looks toward. */
function sectionLine(axis: "x" | "y", position: number, toward: "+" | "-"): SectionLineDto {
  return axis === "x" ? { line: [[position, 0], [position, 1]], keep: toward === "+" ? "right" : "left" }
    : { line: [[0, position], [1, position]], keep: toward === "+" ? "left" : "right" };
}
type PlanTarget = { modelSource: ModelSourceDto; stageRef: string | null } | { sourceAsset: SourceAsset };
const assetKey = (asset: SourceAsset) => `asset:${asset.runId}:${asset.assetSha256}`;
function documentAsset(document: SourceDocumentDto | null): SourceAsset | null {
  const value = document?.viewRecipe?.sourceAsset;
  if (!value || typeof value !== "object") return null;
  const source = value as Record<string, unknown>;
  return typeof source.runId === "string" && typeof source.assetSha256 === "string"
    ? { runId: source.runId, assetSha256: source.assetSha256 } : null;
}
const targetSource = (target: PlanTarget) => "modelSource" in target
  ? { modelSource: target.modelSource, sourceStageRef: target.stageRef }
  : { sourceAsset: target.sourceAsset };

// An edited appearance is saved as a new revision once the edits pause, as Board saves itself.
const APPEARANCE_PAUSE_MS = 800;

const SVG_NS = "http://www.w3.org/2000/svg";
/** Past this many lines a plan is shown as an image; its objects can still be chosen from the list (D-244-3). */
const INLINE_LINE_LIMIT = 20_000;
/** What a projected line is in a cut plan, by the group it is drawn in: the cut (outline, hatch or poché), beyond the cut, or hidden. */
type LineRole = "cut" | "beyond" | "hidden" | "other";
const GROUP_ROLES: Record<string, LineRole> = { section: "cut", "section-hatch": "cut", visible: "beyond", hidden: "hidden" };
const ROLE_ORDER: readonly LineRole[] = ["cut", "beyond", "hidden", "other"];
/** One projected polyline or poché of a source object, as the retained SVG draws it. */
type PlanMark = { index: number; object: string; role: LineRole; polygon: boolean; points: string };
/** A source object as the plan names it: its id, and its component and material when the model has them. */
type PlanObject = { id: string; component: string | null; material: string | null; roles: LineRole[] };
type PlanShape = { tag: "polyline" | "polygon" | "text"; attributes: Record<string, string>; text: string };
type PlanPicture = {
  viewBox: string; font: string | null; groups: { name: string; attributes: Record<string, string>; shapes: PlanShape[] }[];
  marks: PlanMark[]; byObject: Map<string, PlanMark[]>; objects: Map<string, PlanObject>; lines: number;
  /** The SVG without entourage, for the image a plan past the line limit is shown as. */
  image: string | null;
};
// The attributes the retained plan draws and names its sources with, as React spells them; nothing else reaches the page.
const DRAWN: Record<string, string> = { points: "points", fill: "fill", "fill-rule": "fillRule", stroke: "stroke", "stroke-width": "strokeWidth",
  "stroke-dasharray": "strokeDasharray", "stroke-linecap": "strokeLinecap", "stroke-linejoin": "strokeLinejoin",
  x: "x", y: "y", "font-family": "fontFamily", "font-size": "fontSize", "text-anchor": "textAnchor",
  "data-object": "data-object", "data-component": "data-component", "data-material": "data-material", "data-dimension": "data-dimension" };
// The one style the projection writes: the dimension text's embedded font.
const DIMENSION_FONT = /^@font-face\{font-family:DrawingDimension;src:url\(data:font\/ttf;base64,[A-Za-z0-9+/=]*\) format\('truetype'\);\}$/;

/** The retained plan SVG as the page draws it, and each source object its lines name. Entourage is left to its own overlay. */
function planPicture(svg: string): PlanPicture | null {
  const parsed = new DOMParser().parseFromString(svg, "image/svg+xml"), root = parsed.documentElement;
  if (root.namespaceURI !== SVG_NS || root.localName !== "svg" || parsed.getElementsByTagName("parsererror").length > 0) return null;
  root.querySelector('[id="dressing"]')?.remove();
  const drawn = (element: Element) => Object.fromEntries([...element.attributes].flatMap(({ name, value }) => DRAWN[name] ? [[DRAWN[name], value]] : []));
  const marks: PlanMark[] = [], byObject = new Map<string, PlanMark[]>(), objects = new Map<string, PlanObject>(), groups: PlanPicture["groups"] = [];
  let font: string | null = null, lines = 0;
  const shape = (element: Element, role: LineRole): PlanShape | null => {
    const tag = element.localName;
    if (element.namespaceURI !== SVG_NS || (tag !== "polyline" && tag !== "polygon" && tag !== "text")) return null;
    const id = tag === "text" ? null : element.getAttribute("data-object");
    if (tag !== "text") lines += 1;
    if (id) {
      const mark = { index: marks.length, object: id, role, polygon: tag === "polygon", points: element.getAttribute("points") ?? "" };
      marks.push(mark);
      if (byObject.has(id)) byObject.get(id)!.push(mark); else byObject.set(id, [mark]);
      const object = objects.get(id) ?? { id, component: element.getAttribute("data-component"), material: element.getAttribute("data-material"), roles: [] };
      object.roles = ROLE_ORDER.filter(item => item === role || object.roles.includes(item));
      objects.set(id, object);
    }
    return { tag, attributes: drawn(element), text: tag === "text" ? element.textContent ?? "" : "" };
  };
  for (const child of [...root.children]) {
    if (child.namespaceURI !== SVG_NS) continue;
    if (child.localName === "style") { if (DIMENSION_FONT.test(child.textContent ?? "")) font = child.textContent; continue; }
    const group = child.localName === "g", name = group ? child.getAttribute("id") ?? "" : "";
    const shapes = (group ? [...child.children] : [child]).flatMap(element => shape(element, GROUP_ROLES[name] ?? "other") ?? []);
    if (shapes.length > 0) groups.push({ name, attributes: group ? drawn(child) : {}, shapes });
  }
  return { viewBox: root.getAttribute("viewBox") ?? "", font, groups, marks, byObject, objects, lines,
    image: lines > INLINE_LINE_LIMIT ? new XMLSerializer().serializeToString(root) : null };
}

/** `component · material · role`; an object without a component is named by its id. */
const objectLabel = (text: Copy, object: PlanObject, roles: readonly LineRole[] = object.roles) =>
  [object.component ?? object.id, object.material ?? text.noMaterial, roles.map(role => text.roles[role]).join(", ")].join(" · ");

function PlanPreview({ source, file, vector, picture, hidden, picked, onPick, objects, selected, onSelect, onChange, disabled }: {
  source: SourceDocumentDto; file: File; vector: PlanVectorDto | null; picture: PlanPicture | null;
  hidden: readonly string[]; picked: string | null; onPick(object: string | null): void; objects: PlanDressingDto[];
  selected: string; onSelect(id: string): void; onChange(objects: PlanDressingDto[]): void; disabled: boolean;
}) {
  const { language } = usePreferences(), text = copy[language];
  const viewport = useRef<HTMLDivElement>(null), paper = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState({ width: 600, height: 500 });
  const [zoom, setZoom] = useState(1), [ready, setReady] = useState(false);
  const onReady = useCallback((value: boolean) => setReady(value), []);
  const [hover, setHover] = useState<{ mark: PlanMark; x: number; y: number } | null>(null);
  // The plan is drawn inline so each projected line can say what it is (#244); a very large one is an image.
  const inline = picture !== null && picture.image === null;
  const [image, setImage] = useState<string | null>(null);
  useEffect(() => {
    if (!picture?.image) { setImage(null); return; }
    const url = URL.createObjectURL(new Blob([picture.image], { type: "image/svg+xml" }));
    setReady(false); setImage(url); return () => URL.revokeObjectURL(url);
  }, [picture]);
  // A hidden object leaves the page at once; the saved revision then draws without it.
  const hiddenKey = JSON.stringify(hidden), gone = useMemo(() => new Set(hidden), [hiddenKey]);
  useEffect(() => setHover(null), [picture, gone]);
  const lines = useMemo(() => picture && inline && <g className="drawing-plan__lines">
    {picture.groups.map((group, index) => <g key={index} data-group={group.name || undefined} {...group.attributes}>
      {group.shapes.map((shape, key) => shape.tag === "text" ? <text key={key} {...shape.attributes}>{shape.text}</text>
        : gone.has(shape.attributes["data-object"]) ? null
        : shape.tag === "polygon" ? <polygon key={key} {...shape.attributes} /> : <polyline key={key} {...shape.attributes} />)}
    </g>)}
  </g>, [picture, inline, gone]);
  // Wide invisible strokes over each object's lines, so a thin pen is still easy to point at.
  const targets = useMemo(() => picture && inline && <g className="drawing-plan__targets">
    {picture.marks.filter(mark => !gone.has(mark.object)).map(mark => mark.polygon
      ? <polygon key={mark.index} data-mark={mark.index} points={mark.points} />
      : <polyline key={mark.index} data-mark={mark.index} points={mark.points} />)}
  </g>, [picture, inline, gone]);
  const markAt = (target: EventTarget | null) => {
    const index = target instanceof Element ? target.closest("[data-mark]")?.getAttribute("data-mark") : null;
    return index == null ? null : picture?.marks[Number(index)] ?? null;
  };
  const lit = (object: string | null, className: string) => object ? (picture?.byObject.get(object) ?? []).map(mark => mark.polygon
    ? <polygon key={mark.index} className={className} points={mark.points} /> : <polyline key={mark.index} className={className} points={mark.points} />) : null;
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
  const hovered = hover && picture?.objects.get(hover.mark.object);
  return <section className="drawing-preview" aria-label={source.fileName}>
    <div className="drawing-preview__tools">
      <button type="button" aria-label={text.zoomOut} disabled={zoom <= 0.25} onClick={() => setZoom(value => Math.max(0.25, value / 1.25))}>−</button>
      <button type="button" onClick={() => setZoom(1)}>{text.fit}</button>
      <button type="button" aria-label={text.zoomIn} disabled={zoom >= 8} onClick={() => setZoom(value => Math.min(8, value * 1.25))}>+</button>
      <span>{source.fileName}</span>
    </div>
    <div className="drawing-preview__viewport" ref={viewport} tabIndex={0} data-ready={inline || ready}
      onKeyDown={event => { if (event.key === "Escape" && picked) { event.preventDefault(); onPick(null); } }}>
      <div className="drawing-preview__paper" ref={paper} style={{ width: page.width * scale, height: page.height * scale }}>
        {picture && inline ? <svg className="drawing-vector-base" viewBox={picture.viewBox} aria-label={source.fileName}
          onPointerMove={event => {
            const mark = markAt(event.target), box = paper.current?.getBoundingClientRect();
            setHover(mark && box ? { mark, x: event.clientX - box.left, y: event.clientY - box.top } : null);
          }}
          onPointerLeave={() => setHover(null)} onClick={event => onPick(markAt(event.target)?.object ?? null)}>
          {picture.font && <style>{picture.font}</style>}
          {lines}
          <g className="drawing-plan__highlight">{lit(hover && hover.mark.object !== picked ? hover.mark.object : null, "is-hovered")}{lit(picked, "is-picked")}</g>
          {targets}
        </svg>
          : image ? <img className="drawing-vector-base" src={image} alt={source.fileName} onLoad={() => setReady(true)} />
          : <DocumentSurface file={file} page={page} scale={scale} onReady={onReady} />}
        {(inline || image) && vector && crop && <DressingOverlay objects={objects} vector={vector} crop={crop} selected={selected}
          onSelect={onSelect} onChange={onChange} language={language} disabled={disabled} />}
        {hover && hovered && <div className="drawing-object-tip" role="tooltip" style={{ left: hover.x, top: hover.y,
          transform: `translate(${hover.x > page.width * scale / 2 ? "calc(-100% - 12px)" : "12px"}, ${hover.y > page.height * scale / 2 ? "calc(-100% - 12px)" : "16px"})` }}>
          {objectLabel(text, hovered, [hover.mark.role])}</div>}
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
  const [assets, setAssets] = useState<ProjectArtifactDto[]>([]), [importing, setImporting] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);
  const [selected, setSelected] = useState(""), [target, setTarget] = useState("");
  const [explicitTarget, setExplicitTarget] = useState(false), [defaultTarget, setDefaultTarget] = useState("");
  const targetWasChosen = useRef(false); targetWasChosen.current = explicitTarget;
  const [automaticTarget, setAutomaticTarget] = useState<{ modelSource: ModelSourceDto; stageRef: string | null } | null>(null);
  const [form, setForm] = useState<PlanForm>(() => defaultPlanForm("meter")), [dirty, setDirty] = useState(false);
  const [choices, setChoices] = useState<PlanDimensionChoicesDto | null>(null);
  const [status, setStatus] = useState<PlanStatusDto | null>(null), [statusLoading, setStatusLoading] = useState(false);
  const [file, setFile] = useState<File | null>(null), [loading, setLoading] = useState(true);
  const [vector, setVector] = useState<PlanVectorDto | null>(null), [selectedDressing, setSelectedDressing] = useState("");
  // The retained plan as the page draws it, and the projected object a person picked on it, by source object id.
  const picture = useMemo(() => vector ? planPicture(vector.svg) : null, [vector]);
  const [picked, setPicked] = useState<string | null>(null);
  // What each object was, so a hidden one keeps its name after it leaves the drawing.
  const seen = useRef(new Map<string, PlanObject>());
  useEffect(() => { picture?.objects.forEach((object, id) => seen.current.set(id, object)); }, [picture]);
  const [error, setError] = useState<StudioApiError | null>(null), [busy, setBusy] = useState(false);
  // Why edited appearance is not being saved: the pause ended on an invalid field, or the
  // save was refused. The next edit clears it; a refusal can also be retried, never in a loop.
  const [appearanceHeld, setAppearanceHeld] = useState<"invalid" | "refused" | null>(null);
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
  // #307: a section perspective has its own form, so its fields never hold a cut plan's autosave.
  const sectionFormId = useId(), sectionForm = useRef<HTMLFormElement>(null);
  const [section, setSection] = useState<SectionForm>({ axis: "y", position: null, toward: "+", eyeHeight: null, fovDeg: 55 });
  const source = documents.find(document => drawingDocumentKey(document) === selected) ?? null;
  const perspectiveOpen = source?.viewRecipe?.kind === "section-perspective";
  const latest = useMemo(() => latestRevisions(documents), [documents]);
  const latestKeys = useMemo(() => new Set(latest.map(drawingDocumentKey)), [latest]);
  const earlier = documents.filter(document => !latestKeys.has(drawingDocumentKey(document)));
  const historical = source !== null && !latestKeys.has(drawingDocumentKey(source));
  const drawableAssets = assets.filter(item => item.available && (item.representation === "external" || item.representation === "composed"));
  // A drawing made from a chosen version keeps it until a person rebuilds it on the current model.
  const kept = keptOnChosenVersion(source);
  const liveMode = !explicitTarget && !historical && !kept;
  const chosenStage = stages.find(item => item.stageRef === target) ?? null;
  const chosenAsset = drawableAssets.find(item => item.sha256 && assetKey({ runId: item.runId, assetSha256: item.sha256 }) === target) ?? null;
  const sourceAsset = documentAsset(source);
  const savedArtifact = sourceAsset ? assets.find(item => item.runId === sourceAsset.runId && item.sha256 === sourceAsset.assetSha256) ?? null : null;
  const liveTarget = live?.compatible && live.source ? { modelSource: live.source, stageRef: live.stageRef ?? null } : null;
  const stage: PlanTarget | null = explicitTarget ? (chosenStage ? { modelSource: chosenStage.modelSource, stageRef: chosenStage.stageRef }
    : chosenAsset?.sha256 ? chosenAsset.modelSource ? { modelSource: chosenAsset.modelSource, stageRef: chosenAsset.sourceStageRef ?? null }
      : { sourceAsset: { runId: chosenAsset.runId, assetSha256: chosenAsset.sha256 } } : null)
    : source ? (documentAsset(source) ? { sourceAsset: documentAsset(source)! } : automaticTarget) : liveTarget;
  const selectedTargetValue = explicitTarget ? target : "";
  // A section perspective has no plan status; keep a chosen imported source when present.
  const unitTarget = stage ?? (perspectiveOpen ? liveTarget : null);
  const sectionTarget = stage ?? liveTarget;
  const lengthUnit = choices?.lengthUnit ?? status?.lengthUnit ?? "";
  const planCrop = isCutPlan(source) ? (source?.viewRecipe?.frame as { crop_uv?: number[] } | undefined)?.crop_uv : undefined;
  const sectionPosition = section.position ?? (planCrop ? (section.axis === "x" ? planCrop[0] + planCrop[2] : planCrop[1] + planCrop[3]) / 2 : 0);
  const sectionEyeHeight = section.eyeHeight ?? (lengthUnit ? 1.6 / (UNIT_METRES[lengthUnit] ?? 1) : NaN);
  const scopeKey = JSON.stringify([projectId, selected, selectedTargetValue, explicitTarget, active]);
  const scope = useRef({ key: scopeKey });
  if (scope.current.key !== scopeKey) scope.current = { key: scopeKey };
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true); setError(null);
    void Promise.all([studio.documents(), studio.designHistory(), studio.workingSource("drawing"), studio.artifacts()]).then(([list, history, working, available]) => {
      if (cancelled) return;

      if (list.projectId !== projectId || history.projectId !== projectId || working.projectId !== projectId || available.projectId !== projectId) throw new Error("The drawing workspace belongs to another project.");
      const plans = list.documents.filter(item => DRAWING_KINDS.has(String(item.viewRecipe?.kind)));

      const listed = new Set(plans.map(drawingDocumentKey));
      setDocuments(current => [...plans, ...current.filter(item =>
        madeHere.current.has(drawingDocumentKey(item)) && !listed.has(drawingDocumentKey(item)))]);
      setStages(history.stages); setLive(working);
      setAssets(available.artifacts.filter(item => item.format === "3dm" && item.sha256 !== null));
      liveRevision.current = working.revisionSha256 ?? null;
      const head = history.branches.find(branch => branch.branchId === history.branchId)?.headStageRef;
      const defaultStage = head ?? history.stages.at(-1)?.stageRef ?? "";
      setDefaultTarget(defaultStage);
      setTarget(current => targetWasChosen.current && (history.stages.some(item => item.stageRef === current) ||
        available.artifacts.some(item => item.available && (item.representation === "external" || item.representation === "composed")
          && item.sha256 && assetKey({ runId: item.runId, assetSha256: item.sha256 }) === current)) ? current : defaultStage);
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
    if (!unitTarget || !active) return;
    let cancelled = false;

    void studio.drawingPlanDimensions(unitTarget).then(value => {
      if (cancelled) return;
      setChoices(value);
      if (!source && !dirty) setForm(defaultPlanForm(value.lengthUnit));
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    return () => { cancelled = true; };
  }, [studio, projectId, JSON.stringify(unitTarget), active, refresh]);

  useEffect(() => {
    // A revision this view just wrote replaces the one on screen when it has
    // loaded; the picture and the selected object stay while it saves.
    if (!madeHere.current.has(selected)) { setFile(null); setVector(null); setSelectedDressing(""); }
    setStatus(null); setAutomaticTarget(null);
    if (!source) { setStatusLoading(false); return; }
    let cancelled = false;
    if (source.revisionRef && isCutPlan(source)) void studio.drawingPlanVector({ runId: source.runId, assetSha256: source.assetSha256, revisionRef: source.revisionRef })
      .then(value => { if (!cancelled) setVector(value); }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    void studio.documentFile(source.runId, source.assetSha256, source.fileName, source.revisionRef).then(value => {
      if (!cancelled) setFile(value);
    }).catch(cause => { if (!cancelled) setError(asStudioApiError(cause)); });
    return () => { cancelled = true; };
  }, [studio, projectId, selected]);
  useEffect(() => {
    setStatus(null);
    if (!source || !source.revisionRef || !active || !isCutPlan(source)) { setStatusLoading(false); return; }
    let cancelled = false;
    setStatusLoading(true);
    void studio.drawingPlanStatus({ runId: source.runId, assetSha256: source.assetSha256, revisionRef: source.revisionRef,
      ...(explicitTarget && stage && "modelSource" in stage ? { targetModelSource: stage.modelSource, targetStageRef: stage.stageRef } : {}) }).then(value => {
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
    setSelected(document ? drawingDocumentKey(document) : ""); setVector(null); setDirty(false); setAppearanceHeld(null); setError(null);
    setExplicitTarget(false); setAutomaticTarget(null); setStatus(null); setPicked(null);
    if (!document) setTarget(defaultTarget);
    setForm(document && isCutPlan(document) ? planFormFromDocument(document, lengthUnit) : defaultPlanForm(lengthUnit));
  }
  async function chooseDocument(key: string) {
    // Appearance edits still waiting for their pause are saved before another revision opens.
    if (dirty && source) {
      if (!controls.current?.reportValidity()) return;
      if (!await generate(null)) return;
    }
    opened.current = true;
    openDocument(documents.find(item => drawingDocumentKey(item) === key) ?? null);
  }
  async function importModel(file: File) {
    if (busy || importing || !active) return;
    const origin = scope.current;
    setImporting(true); setError(null);
    try {
      const artifact = await studio.uploadModel(projectId, file);
      if (artifact.sha256 === null || !artifact.available || artifact.format !== "3dm") throw new Error("The imported model has no viewable retained 3DM source.");
      if (!mounted.current || scope.current !== origin) return;
      setAssets(current => [artifact, ...current.filter(item => item.runId !== artifact.runId || item.sha256 !== artifact.sha256)]);
      setTarget(assetKey({ runId: artifact.runId, assetSha256: artifact.sha256 }));
      setExplicitTarget(true);
    } catch (cause) { if (mounted.current && scope.current === origin) setError(asStudioApiError(cause)); }
    finally { if (mounted.current) setImporting(false); }
  }
  function update(patch: Partial<PlanForm>) { setForm(current => ({ ...current, ...patch })); setDirty(true); setAppearanceHeld(null); }
  const dimensions = form.dimensions ?? [];
  // Hiding is this drawing's appearance: it saves as a revision and never touches the design.
  const hiddenIds = form.hiddenObjectIds ?? [], hiddenSet = new Set(hiddenIds);
  const hideObject = (id: string) => { setPicked(null); update({ hiddenObjectIds: [...new Set([...hiddenIds, id])].sort() }); };
  const showObject = (id: string) => update({ hiddenObjectIds: hiddenIds.filter(item => item !== id) });
  const pickedObject = picked && !hiddenSet.has(picked) ? picture?.objects.get(picked) ?? null : null;
  const listedObjects = picture ? [...picture.objects.values()].filter(object => !hiddenSet.has(object.id))
    .map(object => ({ object, label: objectLabel(text, object) })).sort((a, b) => a.label.localeCompare(b.label)) : [];
  const hiddenName = (id: string) => {
    const object = picture?.objects.get(id) ?? seen.current.get(id);
    return object ? `${object.component ?? object.id} · ${object.material ?? text.noMaterial}` : id;
  };
  /**
   * The revision request itself: on a target, or on the drawing's own source. A new drawing names only the
   * pens a person set. It is a person's own edit (human), which a project recipe offer may count.
   */
  const requestRevision = (drawn: PlanTarget, follow?: "live" | "frozen") =>
    studio.drawingPlan({ projectId, ...targetSource(drawn), ...planRequestFields(form), sourceKind: "human",
      ...(source?.drawingId ? { drawingId: source.drawingId } : {}), ...(source?.revisionRef ? { previousRevisionRef: source.revisionRef } : {}),
      ...(follow ? { follow } : {}) });
  /** Write a revision on a target, or on the drawing's own source; `follow` records a person's choice. True once it is open. */
  const generate = async (target: PlanTarget | null, options: { following?: boolean; follow?: "live" | "frozen" } = {}) => {
    const drawn: PlanTarget | null = target ?? (source?.modelSource
      ? { modelSource: source.modelSource, stageRef: source.sourceStageRef ?? null }
      : documentAsset(source) ? { sourceAsset: documentAsset(source)! } : null);
    if (!drawn || busy || !active || !controls.current?.reportValidity()) return false;
    const origin = scope.current;
    setBusy(true); setLiveBusy(Boolean(options.following)); setError(null);
    try {
      const result = await requestRevision(drawn, "sourceAsset" in drawn ? "frozen" : options.follow);
      if (!mounted.current || scope.current !== origin) return false;
      madeHere.current.add(drawingDocumentKey(result));
      setDocuments(current => [...current.filter(item => drawingDocumentKey(item) !== drawingDocumentKey(result)), result]);
      if (drawingDocumentKey(result) !== selected) setVector(null);
      setSelected(drawingDocumentKey(result)); setForm(planFormFromDocument(result, lengthUnit)); setDirty(false); setAppearanceHeld(null);
      return true;
    } catch (cause) {
      if (mounted.current && scope.current === origin) { setError(asStudioApiError(cause)); if (!target) setAppearanceHeld("refused"); }
      return false;
    }
    finally { if (mounted.current) { setBusy(false); setLiveBusy(false); } }
  };
  /** Generate a section perspective of the drawing's target and open it; nothing else changes. */
  const generateSection = async () => {
    const drawn = sectionTarget;
    if (!drawn || busy || !active || !sectionForm.current?.reportValidity()) return;
    const origin = scope.current;
    setBusy(true); setError(null);
    try {
      const result = await studio.drawingSectionPerspective({ projectId, ...targetSource(drawn),
        section: sectionLine(section.axis, sectionPosition, section.toward), camera: { eyeHeight: sectionEyeHeight, fovDeg: section.fovDeg } });
      if (!mounted.current || scope.current !== origin) return;
      madeHere.current.add(drawingDocumentKey(result)); opened.current = true;
      setDocuments(current => [...current.filter(item => drawingDocumentKey(item) !== drawingDocumentKey(result)), result]);
      openDocument(result);
    } catch (cause) {
      if (mounted.current && scope.current === origin) setError(asStudioApiError(cause));
    } finally { if (mounted.current) setBusy(false); }
  };
  // Appearance edits save themselves as a new revision after a pause. An
  // invalid field stays marked and unsaved; a refused save waits for the next
  // edit or Retry. Download waits for the saved revision.
  const appearanceSavable = Boolean(dirty && (source?.modelSource || documentAsset(source)) && active && appearanceHeld === null);
  useEffect(() => {
    if (!appearanceSavable || busy) return;
    const timer = window.setTimeout(() => {
      if (controls.current?.checkValidity()) void generate(null); else setAppearanceHeld("invalid");
    }, APPEARANCE_PAUSE_MS);
    return () => window.clearTimeout(timer);
  }, [appearanceSavable, busy, form]);
  // Closing the drawing with edits still inside their pause saves them, without waiting for the answer.
  const pendingAppearance = useRef<(() => void) | null>(null);
  useEffect(() => {
    const drawn: PlanTarget | null = source?.modelSource ? { modelSource: source.modelSource, stageRef: source.sourceStageRef ?? null }
      : documentAsset(source) ? { sourceAsset: documentAsset(source)! } : null;
    pendingAppearance.current = appearanceSavable && !busy && drawn && controls.current?.checkValidity()
      ? () => { void requestRevision(drawn, "sourceAsset" in drawn ? "frozen" : undefined).catch(() => { /* Reopening shows the last saved revision. */ }); } : null;
  });
  useEffect(() => () => pendingAppearance.current?.(), []);
  useEffect(() => {
    // A LIVE drawing rebinds once to each new Working Head; it never loops.
    if (!source || !status || busy || statusLoading || !active || statusFor.current !== drawingDocumentKey(source)) return;
    const key = [drawingDocumentKey(source), status.targetModelSource?.runId, status.targetModelSource?.stateDigest].join("|");
    if (!stage || liveAction({ live: liveMode, dirty, attempted: attemptedLive.current.has(key), status }) !== "rebuild") return;
    attemptedLive.current.add(key);
    void generate(stage, { following: true, follow: "live" });
  }, [source, status, stage, busy, statusLoading, active, liveMode, dirty]);
  const sourceName = (document: SourceDocumentDto) => {
    const imported = documentAsset(document);
    if (imported) {
      const artifact = assets.find(item => item.runId === imported.runId && item.sha256 === imported.assetSha256);
      return artifact?.sourceImport?.sourceFileName ?? artifact?.fileName ?? `${text.imported} · ${imported.assetSha256.slice(0, 8)}`;
    }
    return stages.find(item => item.stageRef === document.sourceStageRef)?.label ??
      (live?.head && document.modelSource?.runId === live.head.runId && live.head.label ? live.head.label : text.workingVersion);
  };
  const action = source ? liveAction({ live: liveMode, dirty, attempted: false, status }) : "none";
  const statusLine = statusLoading ? text.checking : liveBusy ? text.updating
    : historical ? text.earlierView : explicitTarget || kept ? text.chosenView
    : action === "blocked" ? text.stale
    : status?.status === "current" && !status.bindingChanged ? text.liveCurrent : text[status?.status ?? "unknown"];
  const importNotice = (artifact: ProjectArtifactDto) => artifact.sourceImport && <div className="drawing-import-note" role="note">
    <span>{text.converted.replace("{source}", artifact.sourceImport.conversion.sourceFormat.toUpperCase())
      .replace("{target}", artifact.sourceImport.conversion.targetFormat.toUpperCase())}</span>
    {artifact.sourceImport.conversion.warnings.length > 0 && <ul>{artifact.sourceImport.conversion.warnings.map((warning, index) =>
      <li key={`${index}:${warning}`}>{warning}</li>)}</ul>}
  </div>;
  const downloadSvg = () => {
    if (!source || !vector || busy || dirty || !active) return;
    const url = URL.createObjectURL(new Blob([vector.svg], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url; link.download = source.fileName.replace(/\.[^.]+$/, "") + ".svg"; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const numeric = (key: "cutHeight" | "bottom" | "scaleDenominator" | "cutLineMm" | "visibleLineMm" | "hatchSpacingMm", label: string, min?: number, step = "any") => {
    // A new drawing's empty pen is left to the project recipe; a drawn revision always names its own.
    const open = !source && (PAPER_PENS as readonly string[]).includes(key);
    return <label className="drawing-field">{label}<input type="number" min={min} step={step} required={!open} placeholder={open ? text.recipePen : undefined}
      value={Number.isFinite(form[key]) ? form[key] : ""} onChange={event => update({ [key]: event.currentTarget.valueAsNumber })} /></label>;
  };

  return <div className="drawing-workspace" aria-label={text.title}>
    <header className="drawing-header"><div><h1>{text.title}</h1><p>{text.intro}</p></div>
      <button type="button" disabled={busy || loading} onClick={() => setRefresh(value => value + 1)}>{text.refresh}</button></header>
    <div className="drawing-body">
      <div className="drawing-main">
        <div className="drawing-context">
        <div className="drawing-context__fields">
        <label className="drawing-field">{text.revision}<select value={selected} disabled={busy} onChange={event => { void chooseDocument(event.target.value); }}>
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
              {stages.map(item => <option key={item.stageRef} value={item.stageRef}>{item.label} · {item.branchId}</option>)}
              {drawableAssets.length > 0 && <optgroup label={text.imported}>{drawableAssets.map(item => item.sha256 && <option key={`${item.runId}:${item.sha256}`}
                value={assetKey({ runId: item.runId, assetSha256: item.sha256 })}>{item.sourceImport?.sourceFileName ?? item.fileName}</option>)}</optgroup>}</select></label>
            {chosenAsset?.sourceImport && chosenAsset !== savedArtifact && importNotice(chosenAsset)}
            <input ref={importInput} className="visually-hidden" type="file" accept=".3dm,.skp" onChange={event => {
              const file = event.currentTarget.files?.[0]; if (file) void importModel(file); event.currentTarget.value = "";
            }} />
            <button type="button" disabled={busy || importing || !active} onClick={() => importInput.current?.click()}>{importing ? text.importing : text.importModel}</button>
            <p className="drawing-field__hint">{text.anotherHint}</p>
            {explicitTarget && source && !perspectiveOpen && <button type="button" disabled={!stage || busy || statusLoading} onClick={() => void generate(stage, { follow: "frozen" })}>{text.rebuild}</button>}
          </details></div>
        </div>
        {source && perspectiveOpen && <section className="drawing-status" aria-label={text.status} data-kind="section-perspective">
          <div className="drawing-status__summary">
            <span className="drawing-source">{text.sourceOfPage}: <b>{sourceName(source)}</b></span>
            <strong role="status">{text.sectionView}</strong>
            {savedArtifact?.sourceImport && importNotice(savedArtifact)}
          </div>
        </section>}
        {source && !perspectiveOpen && <section className="drawing-status" aria-label={text.status}
          data-follow={historical || explicitTarget || kept ? "frozen" : "live"}
          data-status={liveBusy ? "updating" : action === "blocked" ? "outdated" : status?.status ?? "unknown"}>
          <div className="drawing-status__summary">
            <span className="drawing-source">{text.sourceOfPage}: <b>{sourceName(source)}</b></span>
            <strong role="status">{statusLine}</strong>
            <p>{status?.detail ?? (!statusLoading ? text.statusError : "")}</p>
            {savedArtifact?.sourceImport && importNotice(savedArtifact)}
          </div>
          {historical && latest.some(item => item.drawingId === source.drawingId) &&
            <button type="button" disabled={busy} onClick={() => { void chooseDocument(drawingDocumentKey(latest.find(item => item.drawingId === source.drawingId)!)); }}>{text.openLatest}</button>}
          {kept && !historical && !explicitTarget &&
            <button type="button" disabled={busy || !liveTarget} onClick={() => void generate(liveTarget, { follow: "live" })}>{text.followAgain}</button>}
        </section>}
        </div>
        <div className="drawing-canvas">{source && file ? <PlanPreview key={source.drawingId ?? selected} source={source} file={file} vector={vector}
          picture={picture} hidden={hiddenIds} picked={pickedObject?.id ?? null} onPick={setPicked} objects={form.dressing} selected={selectedDressing}
          onSelect={setSelectedDressing} onChange={dressing => update({ dressing })} disabled={busy || !active} />
          : <div className="drawing-empty" role="status">{loading || source ? text.loading : stage ? text.empty : live?.reason ?? text.noModel}</div>}</div>
      </div>
      <form ref={controls} className="drawing-controls" aria-label={text.settings} onSubmit={event => { event.preventDefault(); void generate(source ? null : stage, !source && explicitTarget ? { follow: "frozen" } : {}); }}>
        <div className="drawing-controls__fields">
        <p className="drawing-unit">{lengthUnit ? `${text.sourceHint} ${lengthUnit}` : text.unitUnknown}</p>
        {!perspectiveOpen && <fieldset disabled={busy || !active || !lengthUnit}><legend>{text.representation}</legend>
          {numeric("cutHeight", `${text.cutHeight} (${lengthUnit || "…"})`)}
          {numeric("bottom", `${text.bottom} (${lengthUnit || "…"})`)}
          {numeric("scaleDenominator", text.scale, 1, "1")}
          <details><summary>{text.graphics}</summary>{numeric("cutLineMm", text.cutLine, 0.01)}{numeric("visibleLineMm", text.visibleLine, 0.01)}{numeric("hatchSpacingMm", text.hatch, 0.1)}
            {!source && <p className="drawing-field__hint">{text.penHint}</p>}</details>
        </fieldset>}
        {source && !perspectiveOpen && picture && <fieldset disabled={busy || !active}><legend>{text.objects}</legend>
          <label className="drawing-field">{text.object}<select value={pickedObject?.id ?? ""} onChange={event => setPicked(event.target.value || null)}>
            <option value="">{text.noObject}</option>
            {listedObjects.map(({ object, label }) => <option key={object.id} value={object.id}>{label}</option>)}</select></label>
          {pickedObject ? <div className="drawing-object-picked">
            <strong>{objectLabel(text, pickedObject)}</strong><small>{pickedObject.id}</small>
            <button type="button" onClick={() => hideObject(pickedObject.id)}>{text.hideObject}</button>
          </div> : <p>{picture.image ? text.tooMany.replace("{count}", String(picture.lines)) : text.pickHint}</p>}
          {hiddenIds.length > 0 && <div className="drawing-object-hidden"><strong>{text.hiddenObjects}</strong><ul>{hiddenIds.map(id => {
            const name = hiddenName(id);
            return <li key={id}><span>{name}{status?.unresolvedObjectIds?.includes(id) ? ` · ${text.notInModel}` : ""}</span>
              <button type="button" aria-label={`${text.showObject}: ${name}`} onClick={() => showObject(id)}>{text.showObject}</button></li>;
          })}</ul></div>}
        </fieldset>}
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
        {/* Its controls belong to the section form below, so a cut plan's autosave never waits on them. */}
        <details className="drawing-section" open={perspectiveOpen || undefined}><summary>{text.section}</summary>
          <fieldset form={sectionFormId} disabled={busy || !active || !lengthUnit || !sectionTarget}>
            <p className="drawing-field__hint">{text.sectionHint}</p>
            <label className="drawing-field">{text.sectionPlane}<select form={sectionFormId} value={section.axis}
              onChange={event => { const axis = event.currentTarget.value === "x" ? "x" : "y"; setSection(current => ({ ...current, axis, position: null })); }}>
              <option value="x">{text.sectionAcrossX}</option><option value="y">{text.sectionAcrossY}</option></select></label>
            <label className="drawing-field">{`${text.sectionPosition} (${lengthUnit || "…"})`}<input form={sectionFormId} type="number" step="any" required
              value={Number.isFinite(sectionPosition) ? sectionPosition : ""} onChange={event => { const position = event.currentTarget.valueAsNumber; setSection(current => ({ ...current, position })); }} /></label>
            <label className="drawing-field">{text.sectionToward}<select form={sectionFormId} value={section.toward}
              onChange={event => { const toward = event.currentTarget.value === "-" ? "-" : "+"; setSection(current => ({ ...current, toward })); }}>
              <option value="+">+{section.axis.toUpperCase()}</option><option value="-">−{section.axis.toUpperCase()}</option></select></label>
            <label className="drawing-field">{`${text.eyeHeight} (${lengthUnit || "…"})`}<input form={sectionFormId} type="number" step="any" required
              value={Number.isFinite(sectionEyeHeight) ? sectionEyeHeight : ""} onChange={event => { const eyeHeight = event.currentTarget.valueAsNumber; setSection(current => ({ ...current, eyeHeight })); }} /></label>
            <label className="drawing-field">{text.fov}<input form={sectionFormId} type="number" min="1" max="170" step="any" required
              value={Number.isFinite(section.fovDeg) ? section.fovDeg : ""} onChange={event => { const fovDeg = event.currentTarget.valueAsNumber; setSection(current => ({ ...current, fovDeg })); }} /></label>
            <button className="btn btn--accent" type="submit" form={sectionFormId}>{busy && perspectiveOpen ? text.generating : text.sectionGenerate}</button>
          </fieldset>
        </details>
        </div>
        <div className="drawing-actions">
        {source ? <p className="drawing-save-state" role="status">{perspectiveOpen ? busy ? text.generating : ""
          : busy || (dirty && appearanceHeld === null) ? text.saving : dirty ? text.held : ""}</p>
          : <button className="btn btn--accent" type="submit" disabled={busy || loading || !lengthUnit || !stage}>
            {busy ? text.generating : text.generate}</button>}

        {source && appearanceHeld === "refused" && <button className="btn btn--accent" type="submit" disabled={busy || !(source.modelSource || documentAsset(source))}>{text.retry}</button>}
        {source && !perspectiveOpen && <><button className="drawing-download" type="button" disabled={busy || dirty || !active || !vector} onClick={downloadSvg}>{text.download}</button>

          <p>{text.downloadHint}</p></>}
        {error && <ErrorPanel error={error} what={text.title} />}
        </div>
      </form>
      <form id={sectionFormId} ref={sectionForm} hidden aria-label={text.section}
        onSubmit={event => { event.preventDefault(); void generateSection(); }} />
    </div>
  </div>;
}
