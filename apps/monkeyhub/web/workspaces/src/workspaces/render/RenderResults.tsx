/**
 * Adapted from YNNAP-HelloWorld (Panny), PR #235,
 * 6f39e67116a2716c2dac70bb4ee3cf1b369afd9d, RenderResults.tsx.
 * Retains authenticated document bytes, history, fit/zoom/pan and download;
 * the Runtime now supplies task identity, source freshness and exact outputs.
 */
import { useEffect, useState } from "react";
import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError } from "../../api/client";
import type { RenderJobDto, SourceDocumentDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import { findSource, pageSource, type PageSource } from "../monkeyboard/boardScene";

export function useDocumentImage(image: SourceDocumentDto | undefined, active: boolean, attempt = 0) {
  const studio = useStudio();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!active) return;
    let live = true, objectUrl: string | null = null;
    setUrl(null); setError(null);
    if (image) studio.documentFile(image.runId, image.assetSha256, image.fileName, image.revisionRef)
      .then((file) => { if (live) { objectUrl = URL.createObjectURL(file); setUrl(objectUrl); } })
      .catch((cause) => { if (live) setError(asStudioApiError(cause).detail); });
    return () => { live = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [studio, image?.runId, image?.assetSha256, image?.revisionRef, image?.fileName, active, attempt]);
  return { url, error };
}

export function ImageThumbnail({ image, active }: { image: SourceDocumentDto; active: boolean }) {
  const { url } = useDocumentImage(image, active);
  return url ? <img src={url} alt="" loading="lazy" /> : <span className="render-thumbnail-empty" aria-hidden="true" />;
}

export function renderStatus(status: RenderJobDto["status"], zh: boolean): string {
  return (zh ? { queued: "排队中", running: "生成中", succeeded: "已完成", failed: "失败", unknown: "结果未知" }
    : { queued: "Queued", running: "Generating", succeeded: "Complete", failed: "Failed", unknown: "Unknown outcome" })[status];
}

export default function RenderResults({ active, jobs, documents, selectedId, onSelect, onBoard, onReuse, onUpdateSource }: {
  active: boolean; jobs: RenderJobDto[]; documents: SourceDocumentDto[]; selectedId: string | null;
  onSelect(id: string): void; onBoard(source: PageSource): void; onReuse(job: RenderJobDto): void;
  onUpdateSource(job: RenderJobDto): void;
}) {
  const { language } = usePreferences(), zh = language === "zh-CN";
  const [attempt, setAttempt] = useState(0), [zoom, setZoom] = useState(1);
  const [view, setView] = useState<"result" | "source" | "compare">("result");
  const job = jobs.find((row) => row.jobId === selectedId) ?? jobs.find((row) => row.document) ?? jobs[0];
  const image = job?.resultAvailable ? job.document ?? undefined : undefined;
  const source = job?.request ? findSource(documents, { ...job.request.source, revisionRef: job.request.source.revisionRef ?? null }) : undefined;
  const resultImage = useDocumentImage(image, active, attempt);
  const sourceImage = useDocumentImage(source, active && view !== "result", attempt);
  useEffect(() => { setZoom(1); }, [job?.jobId]);
  const error = resultImage.error || (view !== "result" && sourceImage.error);
  const imagePane = (url: string | null, name: string, caption: string) => <figure className="render-compare-pane">
    <figcaption>{caption}</figcaption>
    <div className="render-image" tabIndex={0} aria-label={caption} onPointerDown={(event) => {
      if (event.button === 0) event.currentTarget.setPointerCapture(event.pointerId);
    }} onPointerMove={(event) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.scrollLeft -= event.movementX;
        event.currentTarget.scrollTop -= event.movementY;
      }
    }} onPointerUp={(event) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }}>
      {url ? <img src={url} alt={name} draggable={false}
        style={{ width: zoom === 1 ? undefined : `${zoom * 100}%`, maxWidth: zoom === 1 ? "100%" : "none", maxHeight: zoom === 1 ? "100%" : "none" }} />
        : <p>{zh ? "图像暂不可用" : "Image unavailable"}</p>}
    </div>
  </figure>;
  return <section className="render-results" aria-label={zh ? "渲染历史" : "Render history"}>
    <header className="render-toolbar">
      <strong>{zh ? "结果与历史" : "Results & history"}</strong>
      {(["result", "source", "compare"] as const).map((mode) => <button key={mode} type="button" disabled={!job}
        aria-pressed={view === mode} onClick={() => setView(mode)}>{(zh ? { result: "结果", source: "来源", compare: "对比" }
          : { result: "Result", source: "Source", compare: "Compare" })[mode]}</button>)}
      <button type="button" onClick={() => setZoom((value) => Math.max(.25, value / 1.25))} disabled={!image && !source} aria-label={zh ? "缩小" : "Zoom out"}>−</button>
      <button type="button" onClick={() => setZoom(1)} disabled={!image && !source}>{zh ? "适应窗口" : "Fit"}</button>
      <button type="button" onClick={() => setZoom((value) => Math.min(8, value * 1.25))} disabled={!image && !source} aria-label={zh ? "放大" : "Zoom in"}>+</button>
      {resultImage.url && image && <a href={resultImage.url} download={image.fileName}>{zh ? "下载" : "Download"}</a>}
      <button type="button" disabled={!image} onClick={() => { if (image) onBoard(pageSource(image, 0)); }}>{zh ? "送到画板" : "Send to Board"}</button>
    </header>
    {error && <div className="render-error" role="alert">{error} <button type="button" onClick={() => setAttempt((value) => value + 1)}>{zh ? "重读图像" : "Reload image"}</button></div>}
    {!jobs.length ? <div className="render-empty"><h2>{zh ? "从一张已有设计图开始" : "Start with an existing design image"}</h2>
      <p>{zh ? "选择底图、参考图和视觉方向。每次生成的来源与结果独立保留。" : "Choose a source image, references and visual direction. Each generation retains its own source and result."}</p></div>
      : <div className="render-content">
        <aside className="render-list" aria-label={zh ? "生成记录" : "Generations"}>
          {jobs.map((row) => <button type="button" key={row.jobId} aria-pressed={row.jobId === job?.jobId} onClick={() => onSelect(row.jobId)}>
            {row.document && row.resultAvailable && <ImageThumbnail image={row.document} active={active} />}
            <span>{row.request?.direction ?? row.document?.fileName ?? "Native Render"}</span><small>{renderStatus(row.status, zh)}</small>
            <time dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString(language)}</time>
            {row.sourceState !== "current" && <small>{row.sourceState === "outdated" ? (zh ? "来源已更新" : "Source outdated") : (zh ? "来源不可用" : "Source unavailable")}</small>}
          </button>)}
        </aside>
        <div className="render-detail">
          {job && <div className="render-job" role="status">
            {renderStatus(job.status, zh)} · {job.model ?? job.providerId}
            {job.sourceState !== "current" && <span className="render-source-state"> · {job.sourceState === "outdated" ? (zh ? "来源已更新，此结果保留原来源" : "Source outdated; this result keeps its original source") : (zh ? "来源不可用" : "Source unavailable")}</span>}
            {job.error && <p>{job.errorCode ? `${job.errorCode}: ` : ""}{job.error}</p>}
            {job.status === "unknown" && <p>{zh ? "调用结果未确认，可能已产生费用。刷新只查询状态；不会自动重发。" : "The call outcome is unconfirmed and may have incurred a charge. Refresh only reads its status; it never resends."}</p>}
          </div>}
          <div className="render-comparison" data-view={view}>
            {view !== "result" && imagePane(sourceImage.url, source?.fileName ?? "", zh ? "本次生成的来源" : "Source for this generation")}
            {view !== "source" && imagePane(resultImage.url, image?.fileName ?? "", zh ? "渲染结果" : "Render result")}
          </div>
          {job && <footer>
            <p>{job.request?.direction ?? (zh ? "保留的 Native Render 结果" : "Retained Native Render result")}</p>
            <button type="button" disabled={!job.request} onClick={() => onReuse(job)}>{zh ? "载入这次输入" : "Use these inputs"}</button>
            {job.sourceState === "outdated" && job.request && <button type="button" onClick={() => onUpdateSource(job)}>{zh ? "使用更新来源" : "Use updated source"}</button>}
            <span className="render-cost">{zh ? "费用：" : "Cost: "}{job.costUsd == null ? (zh ? "未知" : "Unknown") : `USD ${job.costUsd.toFixed(4)}`}</span>
            <details><summary>{zh ? "来源与生成记录" : "Source & generation details"}</summary>
              <pre>{JSON.stringify({ source: job.request?.source ?? null, references: job.request?.references ?? [], modelSource: image?.modelSource ?? null,
                sourceState: job.sourceState, sourceStateReason: job.sourceStateReason, requestId: job.requestId, jobId: job.jobId,
                providerId: job.providerId, model: job.model, output: job.request?.output ?? image?.viewRecipe, providerRequestId: job.providerRequestId }, null, 2)}</pre>
            </details>
          </footer>}
        </div>
      </div>}
  </section>;
}
