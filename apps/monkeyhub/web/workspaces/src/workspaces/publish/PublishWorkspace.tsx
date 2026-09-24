import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { useConnection, useStudio } from "../../api/ProjectRuntimeContext";
import { publicationClient } from "../../api/publication";
import { asStudioApiError } from "../../api/client";
import type { PublicationDto, PublicationElementDto, SourceDocumentDto } from "../../api/generated";
import { pageSource, documentKey } from "../monkeyboard/boardScene";
import { usePreferences } from "../../features/settings/preferences";
import "./publish.css";

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
      const blob = await studio.exportBoard({ projectId, pages: [item.source!], format: "png", zip: false, maxEdge: 2048 });
      const image = await createImageBitmap(blob);
      try {
        const [l, t, r, b] = item.crop ?? [0, 0, 0, 0];
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.width * (1 - l - r)));
        canvas.height = Math.max(1, Math.round(image.height * (1 - t - b)));
        canvas.getContext("2d")!.drawImage(image, l * image.width, t * image.height, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
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
  const { language } = usePreferences();
  const zh = language !== "en";
  const [draft, setDraft] = useState<PublicationDto | null>(null);
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]);
  const [dirty, setDirty] = useState(false), dirtyRef = useRef(false);
  const [pageIndex, setPageIndex] = useState(0), [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0), [asset, setAsset] = useState("");
  const [scale, setScale] = useState(1);
  const handledBoardRequest = useRef<string | null>(null);
  const readEpoch = useRef(0);
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointer: number; startX: number; startY: number; item: PublicationElementDto; resize: boolean } | null>(null);
  const alive = useRef(true), locked = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!active) return;
    let live = true;
    const epoch = readEpoch.current;
    void Promise.all([api.read(), studio.documents()]).then(([saved, sources]) => {
      if (!live || epoch !== readEpoch.current) return;
      if (saved.projectId !== projectId || sources.projectId !== projectId) throw new Error("The publication response belongs to another project.");
      if (!dirtyRef.current) setDraft(saved);
      setDocuments(sources.documents); setError("");
    }).catch((cause) => { if (live) setError(asStudioApiError(cause).detail); });
    return () => { live = false; };
  }, [active, api, studio, projectId, refreshKey, attempt]);
  useEffect(() => {
    const node = viewport.current;
    if (!node || !draft) return;
    const observer = new ResizeObserver(() => setScale(Math.min(1, Math.max(.1, (node.clientWidth - 40) / draft.spec.width!))));
    observer.observe(node); return () => observer.disconnect();
  }, [draft?.spec.width, !!draft]);
  const change = (next: PublicationDto) => { dirtyRef.current = true; setDirty(true); setDraft(next); };
  const page = draft?.pages[pageIndex];
  const item = page?.elements.find((element) => element.id === selected);
  const updateItem = (patch: Partial<PublicationElementDto>) => {
    if (!draft || !item) return;
    change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.map((element) => element.id === item.id ? { ...element, ...patch } : element) } : row) });
  };
  const save = async () => {
    if (!draft) throw new Error("Publication is not loaded");
    const saved = await api.save({ projectId, baseRevisionSha256: draft.revisionSha256, title: draft.title, spec: draft.spec, pages: draft.pages });
    if (saved.projectId !== projectId) throw new Error("The saved publication belongs to another project.");
    if (alive.current) { setDraft(saved); dirtyRef.current = false; setDirty(false); }
    return saved;
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
      const saved = dirtyRef.current ? await save() : draft;
      const updated = await api.fromBoard({ projectId, baseRevisionSha256: saved.revisionSha256,
        boardRevisionSha256: boardRequest.revision, elementIds: boardRequest.ids });
      if (alive.current) { setDraft(updated); dirtyRef.current = false; setDirty(false); setPageIndex(Math.max(0, Math.min(saved.pages.length, updated.pages.length - 1))); }
    });
  }, [active, boardRequest, !!draft, busy, attempt]);
  const exportFile = async (format: "pdf" | "pptx") => {
    const saved = dirty || !draft?.revisionSha256 ? await save() : draft;
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
  if (!draft) return <section className="publish-workspace"><p>{error || (zh ? "正在读取排版…" : "Loading publication…")}</p>{error && <button onClick={() => setAttempt((value) => value + 1)}>{zh ? "重试" : "Retry"}</button>}</section>;
  return <section className="publish-workspace" aria-label="Publish">
    <header className="publish-toolbar">
      <strong>Publish</strong><input aria-label={zh ? "文件标题" : "Publication title"} value={draft.title} disabled={busy} onChange={(event) => change({ ...draft, title: event.target.value })} />
      <span role="status">{dirty ? (zh ? "未保存" : "Unsaved") : (zh ? "已保存" : "Saved")}</span>
      <button disabled={busy} onClick={() => void run(save)}>{zh ? "保存" : "Save"}</button>
      <button disabled={busy || !draft.pages.length} onClick={() => void run(() => exportFile("pptx"))}>PPTX</button>
      <button disabled={busy || !draft.pages.length} onClick={() => void run(() => exportFile("pdf"))}>PDF</button>
    </header>
    {error && <div role="alert" className="publish-error">{error}<button onClick={() => { if (!dirtyRef.current || window.confirm(zh ? "放弃未保存的排版，重新读取？" : "Discard unsaved changes and reload?")) { ++readEpoch.current; dirtyRef.current = false; setDirty(false); setDraft(null); setError(""); handledBoardRequest.current = null; setAttempt((value) => value + 1); } }}>{zh ? "重新读取" : "Reload"}</button></div>}
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
          <button onClick={() => { if (!page) return; let textIndex = 0, imageIndex = 0; change({ ...draft, pages: draft.pages.map((row, index) => index === pageIndex ? { ...row, elements: row.elements.map((element) => element.kind === "text" ? { ...element, x: 48, y: 28 + textIndex++ * 50, width: draft.spec.width! - 96, height: 50 } : { ...element, x: 48 + imageIndex++ * ((draft.spec.width! - 96) / Math.max(1, row.elements.filter((e) => e.kind === "image").length)), y: 130, width: (draft.spec.width! - 96) / Math.max(1, row.elements.filter((e) => e.kind === "image").length), height: draft.spec.height! - 155 }) } : row) }); }}>{zh ? "应用图文布局" : "Apply image + title layout"}</button>
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
