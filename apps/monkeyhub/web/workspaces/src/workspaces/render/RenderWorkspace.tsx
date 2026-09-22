import { useEffect, useRef, useState } from "react";
import { useStudio, useConnection } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { SourceDocumentDto } from "../../api/generated";
import { createRenderJobApiRenderJobsPost, listRenderJobsApiRenderJobsGet, readRenderJobApiRenderJobsJobIdGet,
  type RenderJobDto, type RenderCameraDto } from "../../api/generated";
import { call } from "../../api/error";
import { usePreferences } from "../../features/settings/preferences";
import "./render.css";
import type { RenderSelection } from "./renderCamera";

function Thumbnail({ image }: { image: SourceDocumentDto }) {
  const studio = useStudio();
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let live = true, objectUrl: string | null = null;
    studio.documentFile(image.runId, image.assetSha256, image.fileName, image.revisionRef)
      .then(file => { if (live) { objectUrl = URL.createObjectURL(file); setUrl(objectUrl); } })
      .catch(() => { /* The selected full-size image reports byte-read failures. */ });
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [studio, image.runId, image.assetSha256, image.revisionRef]);
  return url ? <img src={url} alt="" loading="lazy" /> : <span aria-hidden="true">▧</span>;
}

/** Reads retained project images. Source identity is never inferred from a filename. */
export default function RenderWorkspace({ active, refreshKey, selection, onModel }: {
  active: boolean; refreshKey: number; selection: RenderSelection | null; onModel(): void;
}) {
  const studio = useStudio();
  const { language } = usePreferences();
  const connection = useConnection();
  const zh = language === "zh-CN";
  const [images, setImages] = useState<SourceDocumentDto[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [job, setJob] = useState<RenderJobDto | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const requestId = useRef<string | null>(null);
  useEffect(() => { requestId.current = null; }, [selection]);
  const running = job?.status === "queued" || job?.status === "running";
  async function submit() {
    if (!selection || submittingRef.current || running) return;
    submittingRef.current = true; setSubmitting(true); setError(null);
    try {
      let contentBase64: string | undefined;
      if (selection.localFile) {
        if (selection.localFile.size > 32 * 1024 * 1024) throw new Error(zh ? "模型不能超过 32 MiB。" : "Model exceeds 32 MiB.");
        contentBase64 = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(",")[1]);
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(selection.localFile!);
        });
      }
      requestId.current ??= crypto.randomUUID();
      const result = await call("POST /api/render/jobs", createRenderJobApiRenderJobsPost({
        client: connection.client, body: { projectId: selection.projectId, requestId: requestId.current,
          modelSource: selection.modelSource, fileName: selection.localFile?.name, contentBase64,
          camera: selection.camera as RenderCameraDto, resolution: 768, samples: 16 },
      }));
      setJob(result); requestId.current = null;
    } catch (cause) { setError(asStudioApiError(cause).detail); }
    finally { submittingRef.current = false; setSubmitting(false); }
  }
  const key = (row: SourceDocumentDto) => JSON.stringify([row.runId, row.assetSha256, row.revisionRef]);
  const image = images.find(row => key(row) === selected) ?? images[0];
  useEffect(() => {
    if (!active) return;
    let live = true;
    setLoading(true); setError(null);
    studio.documents().then(result => {
      if (live) setImages(result.documents.filter(row => row.mimeType.startsWith("image/") && row.viewRecipe?.kind === "render")
        .sort((a, b) => (b.generatedAt ?? "").localeCompare(a.generatedAt ?? "")));
    }).catch(cause => { if (live) setError(asStudioApiError(cause).detail); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [studio, active, refreshKey, attempt]);
  useEffect(() => {
    if (!active) return;
    let live = true;
    call("GET /api/render/jobs", listRenderJobsApiRenderJobsGet({ client: connection.client }))
      .then(result => { if (live) setJob(previous => previous ?? result.jobs.sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0] ?? null); })
      .catch(cause => { if (live) setError(asStudioApiError(cause).detail); });
    return () => { live = false; };
  }, [connection, active, attempt]);
  useEffect(() => {
    if (!active || !running || !job) return;
    let live = true;
    const timer = window.setTimeout(() => {
      call("GET /api/render/jobs/id", readRenderJobApiRenderJobsJobIdGet({ client: connection.client, path: { job_id: job.jobId } }))
        .then(result => {
          if (!live) return;
          setJob(result);
          if (result.status === "succeeded" && result.document) {
            setSelected(key(result.document)); setAttempt(value => value + 1);
          }
        }).catch(cause => { if (live) { setError(asStudioApiError(cause).detail); setJob(value => value ? { ...value } : value); } });
    }, 1000);
    return () => { live = false; window.clearTimeout(timer); };
  }, [active, running, job, connection]);
  useEffect(() => {
    let live = true, objectUrl: string | null = null;
    setUrl(null); setZoom(1); setImageError(null);
    if (image) studio.documentFile(image.runId, image.assetSha256, image.fileName, image.revisionRef)
      .then(file => { if (live) { objectUrl = URL.createObjectURL(file); setUrl(objectUrl); } })
      .catch(cause => { if (live) setImageError(asStudioApiError(cause).detail); });
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [studio, image?.runId, image?.assetSha256, image?.revisionRef, image?.fileName, attempt]);
  return <section className="render-workspace" aria-label={zh ? "渲染" : "Render"}>
    <header className="render-toolbar">
      <strong>{zh ? "渲染" : "Render"}</strong>
      <button onClick={onModel}>{zh ? "建模" : "Modeling"}</button>
      <button onClick={() => setAttempt(value => value + 1)} disabled={loading}>{zh ? "刷新" : "Refresh"}</button>
      <button onClick={() => setZoom(value => Math.max(.25, value / 1.25))} disabled={!url} aria-label={zh ? "缩小" : "Zoom out"}>−</button>
      <button onClick={() => setZoom(1)} disabled={!url}>{zh ? "适应窗口" : "Fit"}</button>
      <button onClick={() => setZoom(value => Math.min(8, value * 1.25))} disabled={!url} aria-label={zh ? "放大" : "Zoom in"}>+</button>
      {url && image && <a href={url} download={image.fileName}>{zh ? "下载" : "Download"}</a>}
    </header>
    {selection && <div className="render-toolbar">
      <span>{zh ? "当前模型" : "Current model"}: {selection.localFile?.name ?? selection.modelSource?.runId}</span>
      <button onClick={() => void submit()} disabled={submitting || running}>{submitting ? (zh ? "正在提交…" : "Submitting…") : (zh ? "开始渲染" : "Start render")}</button>
      <small>{zh ? "当前机位 · 默认灯光 · 中性材质" : "Current camera · Default lighting · Neutral material"}</small>
    </div>}
    {job && <div className="render-job" role="status">
      {({ queued: zh ? "等待渲染" : "Queued", running: zh ? "Blender 正在渲染…" : "Rendering in Blender…",
        succeeded: zh ? "渲染完成" : "Render completed", failed: zh ? "渲染失败" : "Render failed",
        interrupted: zh ? "渲染已中断" : "Render interrupted" })[job.status]} · {job.fileName}
      {job.error && <p role="alert">{job.error}</p>}
    </div>}
    {(error || imageError) && <div role="alert">{error || imageError} <button onClick={() => setAttempt(value => value + 1)}>{zh ? "重试" : "Retry"}</button></div>}
    {loading && <p role="status">{zh ? "正在读取项目图片…" : "Loading project images…"}</p>}
    {!loading && !images.length && !error && <div className="render-empty">
      <h2>{zh ? "尚无渲染结果" : "No render results yet"}</h2>
      <p>{zh ? "从建模区发送模型，点击开始渲染。完成的图片会保存在当前项目中。" : "Send a model from Modeling and start rendering. Finished images are retained in this project."}</p>
    </div>}
    {!!images.length && <div className="render-content">
      <aside className="render-list" aria-label={zh ? "项目图片" : "Project images"}>
        {images.map(row => <button key={key(row)} aria-pressed={row === image} onClick={() => setSelected(key(row))}>
          <Thumbnail image={row} />
          <span>{row.fileName}</span><small>{row.generatedAt ?? (zh ? "未记录生成时间" : "Creation time not recorded")}</small>
        </button>)}
      </aside>
      <div className="render-detail">
        <div className="render-image" onPointerDown={event => {
          if (event.button === 0) event.currentTarget.setPointerCapture(event.pointerId);
        }} onPointerMove={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.scrollLeft -= event.movementX;
            event.currentTarget.scrollTop -= event.movementY;
          }
        }} onPointerUp={event => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
        }}>
          {url && <img src={url} alt={image?.fileName} draggable={false}
            style={{ width: zoom === 1 ? undefined : `${zoom * 100}%`, maxWidth: zoom === 1 ? "100%" : "none", maxHeight: zoom === 1 ? "100%" : "none" }} />}
        </div>
        {image && <footer><strong>{image.fileName}</strong>
          <p>{image.modelSource ? `${zh ? "来源版本" : "Source version"}: ${image.modelSource.stateDigest.slice(0, 12)} · ${image.modelSource.runId}`
            : zh ? "来源：项目内保留的导入模型" : "Source: imported model retained in this project"}</p>
          <details><summary>{zh ? "渲染记录" : "Render details"}</summary><pre>{JSON.stringify(image.viewRecipe, null, 2)}</pre></details>
        </footer>}
      </div>
    </div>}
  </section>;
}
