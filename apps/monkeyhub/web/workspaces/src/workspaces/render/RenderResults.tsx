import {useEffect,useState} from 'react';
import {useStudio,useConnection} from '../../api/ProjectRuntimeContext';
import {asStudioApiError} from '../../api/client';
import type {SourceDocumentDto} from '../../api/generated';
import {usePreferences} from '../../features/settings/preferences';
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

export default function RenderResults({active,refreshKey,onPreview}:{active:boolean;refreshKey:number;onPreview():void}) {
  const studio=useStudio(),connection=useConnection(); const {language}=usePreferences(); const zh=language==='zh-CN';
  const [images,setImages]=useState<SourceDocumentDto[]>([]),[selected,setSelected]=useState<string|null>(null);
  const [url,setUrl]=useState<string|null>(null),[error,setError]=useState<string|null>(null),[imageError,setImageError]=useState<string|null>(null);
  const [loading,setLoading]=useState(false),[attempt,setAttempt]=useState(0),[zoom,setZoom]=useState(1);
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
    let live = true, objectUrl: string | null = null;
    setUrl(null); setZoom(1); setImageError(null);
    if (image) studio.documentFile(image.runId, image.assetSha256, image.fileName, image.revisionRef)
      .then(file => { if (live) { objectUrl = URL.createObjectURL(file); setUrl(objectUrl); } })
      .catch(cause => { if (live) setImageError(asStudioApiError(cause).detail); });
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [studio, image?.runId, image?.assetSha256, image?.revisionRef, image?.fileName, attempt]);
  const downloadUrl=image?connection.url(`/api/documents/${image.assetSha256}/bytes?${new URLSearchParams({runId:image.runId,download:'true',...(image.revisionRef?{revisionRef:image.revisionRef}:{})})}`):null;
  return <section className="render-workspace" aria-label={zh ? "渲染" : "Render"}>
    <header className="render-toolbar">
      <strong>{zh ? "渲染" : "Render"}</strong>
      <button onClick={onPreview}>{zh ? "返回实时预览" : "Live preview"}</button>
      <button onClick={() => setAttempt(value => value + 1)} disabled={loading}>{zh ? "刷新" : "Refresh"}</button>
      <button onClick={() => setZoom(value => Math.max(.25, value / 1.25))} disabled={!url} aria-label={zh ? "缩小" : "Zoom out"}>−</button>
      <button onClick={() => setZoom(1)} disabled={!url}>{zh ? "适应窗口" : "Fit"}</button>
      <button onClick={() => setZoom(value => Math.min(8, value * 1.25))} disabled={!url} aria-label={zh ? "放大" : "Zoom in"}>+</button>
      {url && image && <a href={connection.authenticated?url:downloadUrl!} download={image.fileName}>{zh ? "下载" : "Download"}</a>}
    </header>
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
