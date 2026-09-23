import ModelPreview from "./ModelPreview";
import type { RenderView } from "../monkeyarch/viewer/renderView";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { RenderCapabilityDto, RenderJobDto, SourceDocumentDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import { documentKey, documentMime, findSource, pageKey, pageReplacements, pageSource, type PageSource } from "../monkeyboard/boardScene";
import RenderResults, { ImageThumbnail, renderStatus } from "./RenderResults";
import "./render.css";

/** One mounted draft. Entering another workspace only suspends reads. */
export default function RenderWorkspace({ projectId, active, refreshKey, onBoard, readModelView, onModeling }: {
  readModelView?: () => RenderView | null; onModeling?: () => void;
  projectId: string; active: boolean; refreshKey: number; onBoard(source: PageSource): void;
}) {
  const studio = useStudio(), { language } = usePreferences(), zh = language === "zh-CN";
  const [mode, setMode] = useState<"ai" | "physical">("ai");
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]);
  const [providers, setProviders] = useState<RenderCapabilityDto[]>([]);
  const [jobs, setJobs] = useState<RenderJobDto[]>([]);
  const [source, setSource] = useState<PageSource | null>(null), [references, setReferences] = useState<PageSource[]>([]);
  const [direction, setDirection] = useState(""), [providerId, setProviderId] = useState("");
  const [size, setSize] = useState(""), [aspectRatio, setAspectRatio] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false), [sending, setSending] = useState(false), [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null), [submitError, setSubmitError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState<string | null>(null);
  const reading = useRef(false), submitting = useRef(false), uploadingRef = useRef(false), readEpoch = useRef(0);
  const alive = useRef(true), uncertainRef = useRef<string | null>(null);
  const submittedRequest = useRef<string | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const provider = providers.find((item) => item.providerId === providerId) ?? providers.find((item) => item.available) ?? providers[0];
  const actualSize = provider?.sizes.includes(size) ? size : provider?.sizes[0] ?? "";
  const actualAspect = provider?.aspectRatios.includes(aspectRatio) ? aspectRatio : provider?.aspectRatios[0] ?? "";
  const images = documents.filter((item) => item.mimeType === "image/png" || item.mimeType === "image/jpeg");
  const sourceDocument = source ? findSource(images, source) : undefined;
  const hasPending = jobs.some((job) => job.status === "queued" || job.status === "running");

  const refresh = useCallback(async () => {
    if (reading.current) return;
    reading.current = true;
    const epoch = readEpoch.current;
    setLoading(true);
    try {
      const [capabilities, list, history] = await Promise.all([studio.renderCapabilities(), studio.documents(), studio.renderJobs()]);
      if (list.projectId !== projectId || history.projectId !== projectId || history.jobs.some((job) => job.projectId !== projectId)) {
        throw new Error("The render response belongs to another project.");
      }
      if (!alive.current || epoch !== readEpoch.current) return;
      setProviders(capabilities.providers.filter((item) => item.execution === "server-image"));
      setDocuments(list.documents); setJobs(history.jobs); setError(null);
      const completed = history.jobs.find((job) => job.requestId === submittedRequest.current && job.status === "succeeded");
      if (completed) { setSelectedId(completed.jobId); submittedRequest.current = null; }
      if (uncertainRef.current && history.jobs.some((job) => job.requestId === uncertainRef.current)) {
        uncertainRef.current = null; setUncertain(null); setSubmitError(null);
      }
    } catch (cause) { if (alive.current) setError(asStudioApiError(cause).detail); }
    finally { reading.current = false; if (alive.current) setLoading(false); }
  }, [studio, projectId]);
  useEffect(() => {
    if (!active) return;
    void refresh();
  }, [active, refreshKey, refresh]);
  useEffect(() => {
    if (!active || (!hasPending && !uncertain)) return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [active, hasPending, uncertain, refresh]);

  const upload = async (files: FileList | null, target: "source" | "reference") => {
    if (!files?.length || uploadingRef.current) return;
    uploadingRef.current = true; setUploading(true); setSubmitError(null);
    try {
      const incoming = target === "source" ? [files[0]] : [...files];
      if (target === "reference" && references.length + incoming.length > (provider?.maxReferences ?? 8)) {
        throw new Error(zh ? "参考图数量超出当前引擎支持范围。" : "Too many references for this engine.");
      }
      for (const file of incoming) {
        const mimeType = documentMime(file);
        if (mimeType !== "image/png" && mimeType !== "image/jpeg") throw new Error(zh ? "请选择 PNG 或 JPEG 图片。" : "Choose a PNG or JPEG image.");
        if (file.size > 32 * 1024 * 1024) throw new Error(zh ? "单张图片不能超过 32 MiB。" : "Each image must be at most 32 MiB.");
      }
      ++readEpoch.current;
      for (const file of incoming) {
        const typedFile = file.type === documentMime(file) ? file : new File([file], file.name, { type: documentMime(file)! });
        const retained = await studio.uploadDocument(projectId, null, typedFile);
        if (retained.projectId !== projectId) throw new Error("The uploaded image belongs to another project.");
        if (!alive.current) return;
        setDocuments((rows) => [...rows.filter((row) => documentKey(row) !== documentKey(retained)), retained]);
        if (target === "source") setSource(pageSource(retained, 0));
        else setReferences((rows) => [...rows, pageSource(retained, 0)]);
      }
    } catch (cause) { if (alive.current) setSubmitError(asStudioApiError(cause).detail); }
    finally { uploadingRef.current = false; if (alive.current) setUploading(false); }
  };

  const generate = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current || hasPending || uploadingRef.current || !source || !sourceDocument || !provider?.available || !direction.trim()
      || references.length > provider.maxReferences || !actualSize || !actualAspect) return;
    submitting.current = true; setSending(true); setSubmitError(null);
    const requestId = crypto.randomUUID();
    submittedRequest.current = requestId;
    ++readEpoch.current;
    try {
      const job = await studio.createRender({ projectId, requestId, providerId: provider.providerId,
        source, references, direction: direction.trim(), output: { size: actualSize, aspectRatio: actualAspect } });
      if (job.projectId !== projectId || job.requestId !== requestId) throw new Error("The render response does not match this request.");
      if (!alive.current) return;
      setJobs((rows) => [job, ...rows.filter((row) => row.jobId !== job.jobId)]);
      // Keep the previous result visible while a new task is running or fails.
      if (job.status === "succeeded") setSelectedId(job.jobId);
      uncertainRef.current = null; setUncertain(null);
    } catch (cause) {
      if (!alive.current) return;
      const failure = asStudioApiError(cause);
      setSubmitError(failure.detail);
      if (failure.status === 0 || failure.status >= 500) {
        uncertainRef.current = requestId; setUncertain(requestId);
      }
    } finally { submitting.current = false; if (alive.current) setSending(false); }
  };
  const reuse = (job: RenderJobDto) => {
    if (!job.request) return;
    setSource({ ...job.request.source, revisionRef: job.request.source.revisionRef ?? null });
    setReferences((job.request.references ?? []).map((ref) => ({ ...ref, revisionRef: ref.revisionRef ?? null })));
    setDirection(job.request.direction); setProviderId(job.providerId);
    setSize(job.request.output?.size ?? ""); setAspectRatio(job.request.output?.aspectRatio ?? "");
    setSubmitError(null); setMode("ai");
  };
  const selectedKey = sourceDocument ? documentKey(sourceDocument) : "";
  const updateSource = (job: RenderJobDto) => {
    if (!job.request) return;
    reuse(job);
    try {
      const replacements = pageReplacements(documents);
      const original = { ...job.request.source, revisionRef: job.request.source.revisionRef ?? null };
      const next = replacements.get(pageKey(original));
      const originalReferences = (job.request.references ?? []).map((ref) => ({ ...ref, revisionRef: ref.revisionRef ?? null }));
      const nextReferences = originalReferences.map((source) => replacements.get(pageKey(source)) ?? source);
      const hasReplacement = !!next || nextReferences.some((source, index) => pageKey(source) !== pageKey(originalReferences[index]));
      setSource(hasReplacement ? next ?? original : null);
      setReferences(nextReferences);
      if (!hasReplacement) setSubmitError(zh ? "请从项目图片选择或上传更新的视图；项目中尚无此来源的替代图片。" : "Choose or upload an updated view. No replacement image for this source is registered in the project.");
    } catch (cause) { setSubmitError(asStudioApiError(cause).detail); }
  };
  const addReference = (key: string) => {
    const image = images.find((item) => documentKey(item) === key);
    if (image) setReferences((rows) => [...rows, pageSource(image, 0)]);
  };
  const sortedJobs = [...jobs].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const latest = sortedJobs[0];
  const referenceLimit = provider?.maxReferences ?? 8;

  return <section className="render-workspace" aria-label="Render">
    <header className="render-toolbar render-mode-toolbar">
      <strong>Render</strong>
      <div role="group" aria-label={zh ? "工作模式" : "Render mode"}>
        <button type="button" aria-pressed={mode === "ai"} onClick={() => setMode("ai")}>AI</button>
        <button type="button" aria-pressed={mode === "physical"} onClick={() => setMode("physical")}>Physical</button>
      </div>
      <button type="button" onClick={() => void refresh()} disabled={loading}>{loading ? (zh ? "读取中…" : "Reading…") : (zh ? "刷新状态" : "Refresh status")}</button>
    </header>
    <ModelPreview active={active} readView={readModelView} onModeling={onModeling} zh={zh} />
    {error && <div className="render-error" role="alert">{error}</div>}
    {mode === "physical" && <div className="render-physical" role="status">
      <h2>Physical Render</h2>
      <p>{zh ? "Native WebGL2、D5 与 Blender 尚未接通此工作区的执行器。" : "Native WebGL2, D5 and Blender executors are not connected to this workspace yet."}</p>
      <p>{zh ? "此模式将使用模型、相机、材质与灯光。已有 AI 结果仍可在下方浏览与交接。" : "This mode will use the model, camera, materials and lighting. Existing AI results remain available below."}</p>
    </div>}
    <div className="render-ai-body">
      <form className="render-inputs" onSubmit={(event) => void generate(event)} hidden={mode !== "ai"}>
        <fieldset disabled={sending || uploading}>
          <legend>{zh ? "输入与视觉方向" : "Inputs & visual direction"}</legend>
          <label>{zh ? "AI 引擎" : "AI engine"}<select value={provider?.providerId ?? ""} onChange={(event) => setProviderId(event.target.value)}>
            {!providers.length && <option value="">{zh ? "没有可用引擎" : "No engine available"}</option>}
            {providers.map((item) => <option key={item.providerId} value={item.providerId}>{item.label}{item.model ? ` · ${item.model}` : ""}</option>)}
          </select></label>
          {provider && !provider.available && <p className="render-note" role="status">{provider.unavailableReason ?? (zh ? "尚未配置引擎" : "Engine not configured")}</p>}
          <label>{zh ? "底图" : "Source image"}<select value={selectedKey} onChange={(event) => {
            const image = images.find((item) => documentKey(item) === event.target.value); setSource(image ? pageSource(image, 0) : null);
          }}><option value="">{zh ? "选择项目图片" : "Choose a project image"}</option>
            {images.map((image, index) => <option key={documentKey(image)} value={documentKey(image)}>{index + 1}. {image.fileName}</option>)}
          </select></label>
          {source && !sourceDocument && <p role="alert">{zh ? "已选来源不可用。请选择仍在项目中的图片。" : "The selected source is unavailable. Choose an image retained in this project."}</p>}
          {sourceDocument && <div className="render-source-preview"><ImageThumbnail image={sourceDocument} active={active && mode === "ai"} />
            <small>{sourceDocument.modelSource ? (zh ? "保留原模型来源" : "Original model source retained") : (zh ? "独立图片来源" : "Independent image source")}</small></div>}
          <label className="render-upload">{zh ? "上传底图" : "Upload source"}<input type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" onChange={(event) => {
            void upload(event.target.files, "source"); event.target.value = "";
          }} /></label>
          <label>{zh ? "添加参考图" : "Add reference"}<select value="" disabled={references.length >= referenceLimit} onChange={(event) => addReference(event.target.value)}>
            <option value="">{zh ? "选择项目图片" : "Choose a project image"}</option>
            {images.map((image, index) => <option key={documentKey(image)} value={documentKey(image)}>{index + 1}. {image.fileName}</option>)}
          </select></label>
          <label className="render-upload">{zh ? "上传参考图" : "Upload references"}<input type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" multiple disabled={references.length >= referenceLimit} onChange={(event) => {
            void upload(event.target.files, "reference"); event.target.value = "";
          }} /></label>
          <ol className="render-references" aria-label={zh ? "参考图顺序" : "Reference order"}>
            {references.map((reference, index) => {
              const image = findSource(images, reference);
              const move = (step: number) => setReferences((rows) => {
                const next = [...rows]; [next[index], next[index + step]] = [next[index + step], next[index]]; return next;
              });
              return <li key={`${index}:${JSON.stringify(reference)}`}>
                {image && <ImageThumbnail image={image} active={active && mode === "ai"} />}
                <span>{index + 1}. {image?.fileName ?? (zh ? "参考图不可用" : "Reference unavailable")}</span>
                <div><button type="button" disabled={index === 0} onClick={() => move(-1)} aria-label={`${zh ? "上移参考图" : "Move reference up"} ${index + 1}`}>↑</button>
                  <button type="button" disabled={index === references.length - 1} onClick={() => move(1)} aria-label={`${zh ? "下移参考图" : "Move reference down"} ${index + 1}`}>↓</button>
                  <button type="button" onClick={() => setReferences((rows) => rows.filter((_, i) => i !== index))} aria-label={`${zh ? "移除参考图" : "Remove reference"} ${index + 1}`}>×</button></div>
              </li>;
            })}
          </ol>
          {!references.length && <p className="render-note">{zh ? "参考图可选；添加多张后可调整顺序。" : "References are optional; multiple images can be reordered."}</p>}
          <label>{zh ? "视觉方向" : "Visual direction"}<textarea value={direction} maxLength={16000} rows={5} onChange={(event) => setDirection(event.target.value)}
            placeholder={zh ? "说明材质、光线、氛围，以及希望保留的设计特征。" : "Describe materials, light, atmosphere and design features to preserve."} /></label>
          <div className="render-output-options">
            <label>{zh ? "尺寸" : "Size"}<select value={actualSize} onChange={(event) => setSize(event.target.value)}>{provider?.sizes.map((value) => <option key={value}>{value}</option>)}</select></label>
            <label>{zh ? "比例" : "Aspect ratio"}<select value={actualAspect} onChange={(event) => setAspectRatio(event.target.value)}>{provider?.aspectRatios.map((value) => <option key={value} value={value}>{value === "source" ? (zh ? "跟随底图" : "Source") : value}</option>)}</select></label>
          </div>
        </fieldset>
        {submitError && <div className="render-error" role="alert">{submitError}</div>}
        {uncertain && <p className="render-note" role="alert">{zh ? "提交结果未知，可能已计费。先刷新状态；再次生成会创建新请求。" : "Submission outcome is unknown and may be charged. Refresh status first; generating again creates a new request."}<small>{uncertain}</small></p>}
        <button type="submit" className="render-generate" disabled={sending || hasPending || uploading || !sourceDocument || !provider?.available || !direction.trim()
          || references.length > referenceLimit || references.some((ref) => !findSource(images, ref)) || !actualSize || !actualAspect}>
          {sending ? (zh ? "正在提交…" : "Submitting…") : uncertain ? (zh ? "新建一次生成" : "Generate a new attempt") : (zh ? "生成" : "Generate")}
        </button>
        {uploading && <p role="status">{zh ? "正在保存项目图片…" : "Saving project images…"}</p>}
        {latest && <p className="render-note" role="status">{zh ? "最近任务：" : "Latest: "}{renderStatus(latest.status, zh)}</p>}
      </form>
      <RenderResults active={active} jobs={sortedJobs} documents={documents} selectedId={selectedId} onSelect={setSelectedId} onBoard={onBoard} onReuse={reuse} onUpdateSource={updateSource} />
    </div>
  </section>;
}
