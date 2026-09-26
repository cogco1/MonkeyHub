import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { useConnection, useStudio } from "../../api/ProjectRuntimeContext";
import { publicationClient } from "../../api/publication";
import { asStudioApiError } from "../../api/client";
import type { PublicationDto, PublicationElementDto, SourceDocumentDto } from "../../api/generated";
import { pageSource, documentKey } from "../monkeyboard/boardScene";
import { usePreferences } from "../../features/settings/preferences";
import { createPublicationSaveQueue } from "./publicationSaveQueue";
import "./publish.css";
import { MenuCommand, SurfaceMenus } from "../../features/chrome/SurfaceChrome";

// Only failed/in-flight project drafts outlive their surface, in this UI session.
// The saved document remains exclusively in P036; browser close warns on these drafts.
const pendingPublications = new Map<string, ReturnType<typeof createPublicationSaveQueue>>();
const guardPending = (event: BeforeUnloadEvent) => {
  if ([...pendingPublications.values()].some((queue) => queue.pending())) { event.preventDefault(); event.returnValue = ""; }
};

function SourceImage({ item, projectId }: { item: PublicationElementDto; projectId: string }) {
  const studio = useStudio();
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([item.source, item.crop]);
  useEffect(() => {
    let live = true, retainedUrl = "";
    setUrl(""); setError("");
    void (async () => {
      const source = item.source!;
      const blob = await studio.documentFile(source.runId, source.assetSha256, "publication-source", source.revisionRef);
      let image: ImageBitmap;
      if (blob.type === "application/pdf") {
        const renderer = await import("pdfjs-dist");
        renderer.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        const loading = renderer.getDocument({ data: await blob.arrayBuffer() });
        try {
          const pdf = await loading.promise, page = await pdf.getPage(source.pageIndex + 1);
          const original = page.getViewport({ scale: 1 });
          const view = page.getViewport({ scale: Math.min(2, 2048 / Math.max(original.width, original.height)) });
          const canvas = document.createElement("canvas");
          canvas.width = Math.ceil(view.width); canvas.height = Math.ceil(view.height);
          await page.render({ canvas, canvasContext: canvas.getContext("2d", { alpha: true })!, viewport: view, background: "rgba(0,0,0,0)" }).promise;
          image = await createImageBitmap(canvas);
        } finally { await loading.destroy(); }
      } else {
        const original = await createImageBitmap(blob);
        const scale = Math.min(1, 2048 / Math.max(original.width, original.height));
        try { image = await createImageBitmap(original, { resizeWidth: Math.max(1, Math.round(original.width * scale)), resizeHeight: Math.max(1, Math.round(original.height * scale)) }); }
        finally { original.close(); }
      }
      try {
        const [l, t, r, b] = item.crop ?? [0, 0, 0, 0];
        const canvas = document.createElement("canvas");
        const left = Math.round(l * image.width), top = Math.round(t * image.height);
        canvas.width = Math.max(1, Math.round((1 - r) * image.width) - left);
        canvas.height = Math.max(1, Math.round((1 - b) * image.height) - top);
        canvas.getContext("2d")!.drawImage(image, left, top, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
        const cropped = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("Image preview failed"))));
        if (live) { retainedUrl = URL.createObjectURL(cropped); setUrl(retainedUrl); }
      } finally { image.close(); }
    })().catch((cause) => { if (live) setError(asStudioApiError(cause).detail); });
    return () => { live = false; if (retainedUrl) URL.revokeObjectURL(retainedUrl); };
  }, [studio, projectId, key, retry]);
  return url ? <img src={url} alt="" draggable={false} /> : <span className="publish-image-status">{error ? <button onPointerDown={(event) => event.stopPropagation()} onClick={() => setRetry((value) => value + 1)} title={error}>↻</button> : "…"}</span>;
}

export default function PublishWorkspace({ projectId, active, refreshKey = 0, boardRequest = null }: { projectId: string; active: boolean; refreshKey?: number; boardRequest?: { revision: string; ids: string[]; requestId: string } | null }) {
  const studio = useStudio(), connection = useConnection();
  const api = useMemo(() => publicationClient(connection), [connection]);
  const cacheKey = JSON.stringify([connection.baseUrl, projectId]);
  const { language } = usePreferences();
  const zh = language !== "en";
  const [draft, setDraft] = useState<PublicationDto | null>(null);
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]);
  const queue = useRef<ReturnType<typeof createPublicationSaveQueue> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pageIndex, setPageIndex] = useState(0), [selected, setSelected] = useState<string | null>(null);
  const [running, setBusy] = useState(false), [importing, setImporting] = useState(false), [error, setError] = useState("");
  const busy = running || importing;
  const [attempt, setAttempt] = useState(0), [asset, setAsset] = useState("");
  const [scale, setScale] = useState(1);
  const handledBoardRequest = useRef<string | null>(null);
  const readEpoch = useRef(0);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; startX: number; startY: number; item: PublicationElementDto; resize: boolean } | null>(null);
  const alive = useRef(true), locked = useRef(false);
  useEffect(() => { alive.current = true; return () => {
    alive.current = false;
    const pending = queue.current;
    pending?.dispose();
    if (!pending?.pending()) return;
    pendingPublications.set(cacheKey, pending);
    window.addEventListener("beforeunload", guardPending);
    void pending.flush().then(() => {
      if (pendingPublications.get(cacheKey) === pending && !pending.pending()) pendingPublications.delete(cacheKey);
      if (!pendingPublications.size) window.removeEventListener("beforeunload", guardPending);
    }).catch(() => {});
  }; }, [cacheKey]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (queue.current?.pending()) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, []);
  useEffect(() => { if (!active) void queue.current?.flush().catch(() => {}); }, [active]);
  useEffect(() => {
    if (!active) return;
    let live = true;
    const notify = (next: PublicationDto, changed: boolean, writing: boolean, cause: unknown) => {
      if (!alive.current) return;
      ++readEpoch.current; setDraft(next); setDirty(changed); setSaving(writing);
      setImporting(queue.current?.importing() ?? false);
      setSaveError(cause ? asStudioApiError(cause).detail : "");
    };
    // Take over the pending writer before a GET can race its final acknowledgement.
    const pending = pendingPublications.get(cacheKey);
    if (!queue.current && pending) {
      queue.current = pending; pending.resume(api.save, notify);
      pendingPublications.delete(cacheKey);
      if (!pendingPublications.size) window.removeEventListener("beforeunload", guardPending);
    }
    const epoch = readEpoch.current;
    void Promise.all([api.read(), studio.documents()]).then(([saved, sources]) => {
      if (!live) return;
      if (saved.projectId !== projectId || sources.projectId !== projectId) throw new Error("The publication response belongs to another project.");
      setDocuments(sources.documents);
      if (epoch !== readEpoch.current) return;
      if (!queue.current) {
        queue.current = createPublicationSaveQueue(saved, api.save, notify); setDraft(saved);
      } else queue.current.accept(saved);
      setError("");
    }).catch((cause) => { if (live) setError(asStudioApiError(cause).detail); });
    return () => { live = false; };
  }, [active, api, studio, projectId, cacheKey, refreshKey, attempt]);
  useEffect(() => {
    const node = viewport.current;
    if (!node || !draft) return;
    const observer = new ResizeObserver(() => setScale(Math.min(1, Math.max(.1, (node.clientWidth - 40) / draft.spec.width!))));
    observer.observe(node); return () => observer.disconnect();
  }, [draft?.spec.width, !!draft]);
  const change = (next: PublicationDto) => { queue.current?.change(next); };
  const page = draft?.pages[pageIndex];
  const item = page?.elements.find((element) => element.id === selected);
  const updateItem = (patch: Partial<PublicationElementDto>) => {
    if (!draft || !item) return;
    change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.map((element) => element.id === item.id ? { ...element, ...patch } : element) } : row) });
  };
  const save = async () => {
    if (!queue.current) throw new Error("Publication is not loaded");
    return queue.current.flush();
  };
  const run = async (action: () => Promise<unknown>) => {
    if (locked.current) return;
    ++readEpoch.current;
    locked.current = true; setBusy(true); setError("");
    try { await action(); } catch (cause) { if (alive.current) setError(asStudioApiError(cause).detail); }
    finally { ++readEpoch.current; locked.current = false; if (alive.current) setBusy(false); }
  };
  useEffect(() => {
    if (!active || !draft || !boardRequest || handledBoardRequest.current === boardRequest.requestId || locked.current) return;
    handledBoardRequest.current = boardRequest.requestId;
    void run(async () => {
      let previousLength = 0;
      const updated = await queue.current!.append((saved) => {
        previousLength = saved.pages.length;
        return api.fromBoard({ projectId, baseRevisionSha256: saved.revisionSha256,
          boardRevisionSha256: boardRequest.revision, elementIds: boardRequest.ids });
      });
      if (alive.current) setPageIndex(Math.max(0, Math.min(previousLength, updated.pages.length - 1)));
    });
  }, [active, boardRequest, !!draft, busy, attempt]);
  const exportFile = async (format: "pdf" | "pptx") => {
    const saved = await save();
    const blob = await api.export({ projectId, revisionSha256: saved.revisionSha256!, format });
    if (!alive.current) return;
    const url = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${saved.title}.${format}`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const addPage = () => {
    if (!draft) return;
    change({ ...draft, pages: [...draft.pages, { id: crypto.randomUUID(), elements: [] }] });
    setPageIndex(draft.pages.length); setSelected(null);
  };
  const addElement = (kind: "text" | "image") => {
    if (!draft || !page) return;
    const document = documents.find((source) => documentKey(source) === asset);
    if (kind === "image" && !document) return;
    const id = crypto.randomUUID();
    const next: PublicationElementDto = { id, kind, x: 48, y: kind === "text" ? 28 : 115, width: draft.spec.width! - 96,
      height: kind === "text" ? 75 : draft.spec.height! - 140, text: kind === "text" ? (zh ? "输入标题" : "Add a title") : "", fontSize: 28,
      source: document && kind === "image" ? pageSource(document, 0) : null, frozen: false, crop: [0, 0, 0, 0] };
    change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: [...row.elements, next] } : row) }); setSelected(id);
  };
  const startDrag = (event: PointerEvent<HTMLDivElement>, element: PublicationElementDto, resize = false) => {
    if (busy || event.button !== 0) return;
    event.preventDefault(); event.stopPropagation(); setSelected(element.id);
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointer: event.pointerId, startX: event.clientX, startY: event.clientY, item: element, resize };
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const action = drag.current;
    if (!action || !draft || action.pointer !== event.pointerId) return;
    const dx = (event.clientX - action.startX) / scale, dy = (event.clientY - action.startY) / scale, initial = action.item;
    const patch = action.resize ? { width: Math.max(20, Math.min(draft.spec.width! - initial.x, initial.width + dx)), height: Math.max(20, Math.min(draft.spec.height! - initial.y, initial.height + dy)) }
      : { x: Math.max(0, Math.min(draft.spec.width! - initial.width, initial.x + dx)), y: Math.max(0, Math.min(draft.spec.height! - initial.height, initial.y + dy)) };
    change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.map((element) => element.id === initial.id ? { ...element, ...patch } : element) } : row) });
  };
  const reload = () => {
    if (queue.current?.pending() && !window.confirm(zh ? "放弃未保存的排版，重新读取？" : "Discard unsaved changes and reload?")) return;
    ++readEpoch.current; queue.current?.discard(); queue.current = null; setDirty(false); setDraft(null);
    setError(""); setSaveError(""); setAttempt((value) => value + 1);
  };
  if (!draft) return <section className="publish-workspace"><p>{error || (zh ? "正在读取排版…" : "Loading publication…")}</p>{error && <button onClick={() => setAttempt((value) => value + 1)}>{zh ? "重试" : "Retry"}</button>}</section>;
  return <section className="publish-workspace" aria-label="Publish">
    {/* #337: Layout's title and exports sit in the project bar after Board | Layout, its save state at the bar's right end. */}
    <SurfaceMenus label="Publish" active={active}
      end={<span className="publish-save-state" role="status">{saveError ? (zh ? "保存失败" : "Not saved") : saving ? (zh ? "正在保存…" : "Saving…") : dirty ? (zh ? "等待保存…" : "Waiting to save…") : (zh ? "已保存" : "Saved")}</span>}>
      <input className="surface-title publish-title" aria-label={zh ? "文件标题" : "Publication title"} value={draft.title} disabled={busy} onChange={(event) => change({ ...draft, title: event.target.value })} />
      <MenuCommand disabled={busy || !draft.pages.length} onClick={() => void run(() => exportFile("pptx"))}>PPTX</MenuCommand>
      <MenuCommand disabled={busy || !draft.pages.length} onClick={() => void run(() => exportFile("pdf"))}>PDF</MenuCommand>
    </SurfaceMenus>
    {saveError && <div role="alert" className="publish-error">{saveError}<button disabled={busy || saving} onClick={() => void queue.current?.retry().then(() => { setError(""); setAttempt((value) => value + 1); }).catch(() => {})}>{zh ? "重试保存" : "Retry save"}</button><button disabled={busy || saving} onClick={reload}>{zh ? "重新读取" : "Reload"}</button></div>}
    {error && <div role="alert" className="publish-error">{error}<button disabled={busy || saving} onClick={reload}>{zh ? "重新读取" : "Reload"}</button></div>}
    <div className="publish-body">
      <aside className="publish-pages" aria-label={zh ? "页面顺序" : "Page order"}>
        <button disabled={busy || draft.pages.length >= 60} onClick={addPage}>{zh ? "+ 添加页" : "+ Page"}</button>
        {draft.pages.map((row, index) => <button key={row.id} aria-pressed={index === pageIndex} onClick={() => { setPageIndex(index); setSelected(null); }}>{index + 1}. {row.elements.find((element) => element.kind === "text")?.text?.slice(0, 22) || (zh ? "空白页" : "Blank page")}</button>)}
        {page && <><button disabled={busy || pageIndex === 0} onClick={() => { const pages = [...draft.pages]; [pages[pageIndex - 1], pages[pageIndex]] = [pages[pageIndex], pages[pageIndex - 1]]; change({ ...draft, pages }); setPageIndex(pageIndex - 1); }}>{zh ? "上移一页" : "Move page up"}</button>
          <button disabled={busy} onClick={() => { change({ ...draft, pages: draft.pages.filter((_, index) => index !== pageIndex) }); setPageIndex(Math.max(0, pageIndex - 1)); setSelected(null); }}>{zh ? "删除此页" : "Delete page"}</button></>}
      </aside>
      <div className="publish-viewport" ref={viewport}>
        {!page ? <p className="publish-empty">{zh ? "添加页面，或在画板选择图纸后点击“放入汇报”。" : "Add a page, or select Board drawings and choose Add to Publish."}</p> : <div style={{ width: draft.spec.width! * scale, height: draft.spec.height! * scale }}>
          <div className="publish-page" style={{ width: draft.spec.width, height: draft.spec.height, transform: `scale(${scale})` }} onPointerDown={() => setSelected(null)}>
            {page.elements.map((element) => <div key={element.id} role="button" tabIndex={0} aria-label={`${element.kind} ${element.text || element.id}`} aria-pressed={selected === element.id}
              className={`publish-element ${selected === element.id ? "selected" : ""}`} style={{ left: element.x, top: element.y, width: element.width, height: element.height, fontSize: element.fontSize, lineHeight: 1.2 }}
              onFocus={() => setSelected(element.id)} onKeyDown={(event) => { if (event.key === "Enter") setSelected(element.id); }}
              onPointerDown={(event) => startDrag(event, element)} onPointerMove={move} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
              {element.kind === "text" ? element.text : <SourceImage item={element} projectId={projectId} />}
              {selected === element.id && <div className="publish-resize" onPointerDown={(event) => startDrag(event, element, true)} />}
            </div>)}
          </div>
        </div>}
      </div>
      <aside className="publish-properties">
        <fieldset disabled={busy || !page}>
          <legend>{zh ? "页面内容" : "Page content"}</legend>
          <button onClick={() => addElement("text")}>{zh ? "添加文字" : "Add text"}</button>
          <label>{zh ? "项目图纸 / 图片" : "Project drawing / image"}<select aria-label={zh ? "项目图纸 / 图片" : "Project drawing / image"} value={asset} onChange={(event) => setAsset(event.target.value)}><option value="">{zh ? "选择来源" : "Choose source"}</option>{documents.map((source) => <option value={documentKey(source)} key={documentKey(source)}>{source.fileName}</option>)}</select></label>
          <button disabled={!asset} onClick={() => addElement("image")}>{zh ? "放入此页" : "Place on page"}</button>
          <button onClick={() => {
            if (!page) return;
            const margin = draft.spec.width! * .05, top = draft.spec.height! * .05, gap = draft.spec.height! * .025;
            const texts = page.elements.filter((element) => element.kind === "text");
            const images = page.elements.filter((element) => element.kind === "image");
            const textHeight = texts.reduce((total, element) => total + Math.max(element.height, (element.fontSize ?? 24) * 1.4) + gap, 0);
            const imageTop = top + textHeight;
            const imageHeight = draft.spec.height! - top - imageTop;
            const imageWidth = (draft.spec.width! - 2 * margin - gap * Math.max(0, images.length - 1)) / Math.max(1, images.length);
            if (imageHeight < (images.length ? draft.spec.height! * .2 : 0) || imageWidth < 20) {
              setError(zh ? "文字过多，无法应用图文布局。请缩短文字、调整文字框或分到其他页面。" : "There is not enough room for this layout. Shorten the text, resize text boxes or split content across pages."); return;
            }
            setError("");
            let textY = top, imageIndex = 0;
            change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.map((element) => {
              if (element.kind === "text") {
                const height = Math.max(element.height, (element.fontSize ?? 24) * 1.4), y = textY; textY += height + gap;
                return { ...element, x: margin, y, width: draft.spec.width! - 2 * margin, height };
              }
              const width = imageWidth;
              return { ...element, x: margin + imageIndex++ * (width + gap), y: imageTop, width, height: imageHeight };
            }) } : row) });
          }}>{zh ? "应用图文布局" : "Apply image + title layout"}</button>
        </fieldset>
        {item && <fieldset disabled={busy}><legend>{zh ? "选中对象" : "Selected object"}</legend>
          {item.kind === "text" && <><label>{zh ? "文字" : "Text"}<textarea aria-label={zh ? "文字" : "Text"} value={item.text} onChange={(event) => updateItem({ text: event.target.value })} /></label><label>{zh ? "字号" : "Font size"}<input type="number" min="8" max="96" value={item.fontSize} onChange={(event) => updateItem({ fontSize: Number(event.target.value) })} /></label></>}
          <div className="publish-coordinates">{(["x", "y", "width", "height"] as const).map((field) => <label key={field}>{field}<input aria-label={field} type="number" value={Math.round(item[field])} onChange={(event) => updateItem({ [field]: Number(event.target.value) })} /></label>)}</div>
          <button onClick={() => updateItem({ x: (draft.spec.width! - item.width) / 2 })}>{zh ? "水平居中" : "Center horizontally"}</button>
          {item.source && <><label>{zh ? "来源页码" : "Source page"}<input type="number" min="1" value={item.source.pageIndex + 1} onChange={(event) => updateItem({ source: { ...item.source!, pageIndex: Number(event.target.value) - 1 } })} /></label><label><input type="checkbox" checked={item.frozen ?? false} onChange={(event) => updateItem({ frozen: event.target.checked })} />{zh ? "固定此来源版本" : "Freeze this source"}</label>
          <span title={draft.sources.find((source) => source.elementId === item.id)?.detail}>{(() => {
            const state = draft.sources.find((source) => source.elementId === item.id)?.status;
            return state ? zh ? { current: "当前来源", frozen: "已固定版本", stale: "来源有变化，需核对", missing: "来源不可用" }[state] : state : (zh ? "保存后核对来源" : "Save to check source");
          })()}</span>
            {draft.sources.find((source) => source.elementId === item.id)?.replacement && <button onClick={() => updateItem({ source: draft.sources.find((source) => source.elementId === item.id)!.replacement!, frozen: false })}>{zh ? "更新此图来源" : "Update this image source"}</button>}
            <div className="publish-coordinates">{(zh ? ["裁左", "裁上", "裁右", "裁下"] : ["Crop left", "Crop top", "Crop right", "Crop bottom"]).map((label, index) => <label key={label}>{label} %<input aria-label={`${label} %`} type="number" min="0" max="95" value={Math.round((item.crop?.[index] ?? 0) * 100)} onChange={(event) => { const crop = [...(item.crop ?? [0, 0, 0, 0])] as [number, number, number, number]; crop[index] = Number(event.target.value) / 100; if (crop[0] + crop[2] < 1 && crop[1] + crop[3] < 1) updateItem({ crop }); }} /></label>)}</div>
          </>}
          <button onClick={() => { change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.filter((element) => element.id !== item.id) } : row) }); setSelected(null); }}>{zh ? "删除对象" : "Delete object"}</button>
        </fieldset>}
      </aside>
    </div>
  </section>;
}
