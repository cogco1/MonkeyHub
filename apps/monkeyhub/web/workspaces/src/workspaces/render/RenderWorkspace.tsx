import { useEffect, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { SourceDocumentDto } from "../../api/generated";
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
  const zh = language === "zh-CN";
  const [images, setImages] = useState<SourceDocumentDto[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [zoom, setZoom] = useState(1);
  const key = (row: SourceDocumentDto) => JSON.stringify([row.runId, row.assetSha256, row.revisionRef]);
  const image = images.find(row => key(row) === selected) ?? images[0];
  useEffect(() => {
    if (!active) return;
    let live = true;
    setLoading(true); setError(null);
    studio.documents().then(result => {
      if (live) setImages(result.documents.filter(row => row.mimeType.startsWith("image/")));
    }).catch(cause => { if (live) setError(asStudioApiError(cause).detail); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [studio, active, refreshKey, attempt]);
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
      <strong>{zh ? "渲染 · 项目图片" : "Render · Project images"}</strong>
      <button onClick={onModel}>{zh ? "建模" : "Modeling"}</button>
      <button onClick={() => setAttempt(value => value + 1)} disabled={loading}>{zh ? "刷新" : "Refresh"}</button>
      <button onClick={() => setZoom(value => Math.max(.25, value / 1.25))} disabled={!url} aria-label={zh ? "缩小" : "Zoom out"}>−</button>
      <button onClick={() => setZoom(1)} disabled={!url}>{zh ? "适应窗口" : "Fit"}</button>
      <button onClick={() => setZoom(value => Math.min(8, value * 1.25))} disabled={!url} aria-label={zh ? "放大" : "Zoom in"}>+</button>
      {url && image && <a href={url} download={image.fileName}>{zh ? "下载" : "Download"}</a>}
    </header>
    {selection && <div className="render-toolbar">
      <span>{zh ? "当前模型" : "Current model"}: {selection.modelSource.runId} · {selection.modelSource.stateDigest.slice(0, 12)}</span>
      <span>{selection.camera.projection} · {selection.camera.aspect.toFixed(3)}</span>
      <span>{zh ? "视角已接收；渲染任务接口尚未接通。" : "View received; render job integration is not connected yet."}</span>
    </div>}
    {(error || imageError) && <div role="alert">{error || imageError} <button onClick={() => setAttempt(value => value + 1)}>{zh ? "重试" : "Retry"}</button></div>}
    {loading && <p role="status">{zh ? "正在读取项目图片…" : "Loading project images…"}</p>}
    {!loading && !images.length && !error && <div className="render-empty">
      <h2>{zh ? "尚无项目图片" : "No project images yet"}</h2>
      <p>{zh ? "已登记到当前项目的渲染图和参考图片会显示在这里。" : "Registered render results and reference images for this project appear here."}</p>
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
            : zh ? "未关联模型；不推断来源版本。" : "No model association; source version is unknown."}</p>
        </footer>}
      </div>
    </div>}
  </section>;
}
