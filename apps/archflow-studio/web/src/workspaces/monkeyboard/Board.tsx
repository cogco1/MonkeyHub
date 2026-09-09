import { useCallback, useEffect, useRef, useState, type ClipboardEvent as ReactClipboardEvent, type DragEvent } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, Excalidraw, FONT_FAMILY, MainMenu, viewportCoordsToSceneCoords, WelcomeScreen } from "@excalidraw/excalidraw";
import type { ExcalidrawElement, FileId } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles, DataURL, ExcalidrawImperativeAPI, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

import { studio } from "../../api/client";
import type { BoardDto, SourceDocumentDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import { renderDocumentVisual } from "../monkeydiagram/documentVisualInput";
import { createBoardSaveQueue, type BoardSaveState } from "./boardSaveQueue";
import { documentKey, documentMime, documentUrl, findSource, imageSource, nextDocumentPosition, pageKey, pageSource, type BoardDraft, type PageSource } from "./boardScene";
import "./board.css";

const copy = {
  en: { loading: "Opening board…", loadFailed: "The board could not be opened.", retry: "Retry", sources: "Project documents", upload: "Upload PDF / image", title: "Board title", saved: "Saved", saving: "Saving…", dirty: "Unsaved changes", saveError: "Changes have not been saved.", conflict: "Another saved version exists. Your current canvas is preserved; open the saved board separately to compare.", compare: "Open saved board", save: "Save now", add: "Add page", open: "Open in MonkeyDiagram", fit: "Fit board", busy: "Receiving document…", welcome: "Bring the project together", welcomeBody: "Arrange drawings, connect ideas and mark up the discussion. New MonkeyDiagram documents arrive here automatically.", empty: "Upload a PDF, PNG or JPEG to begin. New project drawings will appear here.", hint: "Wheel to zoom · Space or middle mouse to pan · Shift to select several", auto: "New documents arrive automatically", previewError: "Some page previews could not be loaded. The saved layout is retained.", unsupported: "Use PDF, PNG or JPEG files.", unbound: "This image has no registered project source. Upload its original file first.", page: "Page", pages: "pages", received: "Received", pending: "Pending", dismiss: "Dismiss", sourceError: "Project documents could not be refreshed.", select: "Select a drawing to open its original.", refresh: "Retry previews / receive", unknown: "Unknown error" },
  "zh-CN": { loading: "正在打开画布…", loadFailed: "画布暂时无法打开。", retry: "重试", sources: "项目资料", upload: "上传 PDF / 图片", title: "画布标题", saved: "已保存", saving: "正在保存…", dirty: "有未保存的修改", saveError: "修改尚未保存。", conflict: "已有另一份保存版本。当前画布已保留，请另开已保存画布进行比较。", compare: "另开已保存画布", save: "立即保存", add: "添加此页", open: "在 MonkeyDiagram 中打开", fit: "查看全部", busy: "正在接收资料…", welcome: "把项目放在一起讨论", welcomeBody: "摆放图纸、连接想法、标记讨论。MonkeyDiagram 的新资料会自动来到这里。", empty: "上传 PDF、PNG 或 JPEG 开始。项目的新图纸也会自动出现在这里。", hint: "滚轮缩放 · 空格或鼠标中键平移 · Shift 多选", auto: "自动接收新资料", previewError: "部分页面预览未能载入，已保留原有布局。", unsupported: "请使用 PDF、PNG 或 JPEG 文件。", unbound: "这张图片没有项目来源，请先上传原始文件。", page: "第", pages: "页", received: "已接收", pending: "待接收", dismiss: "关闭提示", sourceError: "项目资料暂时无法刷新。", select: "选中图纸可打开原始页面。", refresh: "重试预览 / 接收", unknown: "未知错误" },
};
type Copy = typeof copy.en;
type Preview = { dataURL: DataURL; width: number; height: number };

function errorText(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) return String(error.detail);
  return error instanceof Error ? error.message : String(error);
}
function records(elements: readonly ExcalidrawElement[]): BoardDto["elements"] {
  // Match Excalidraw's restore representation so opening a board does not create a revision.
  return elements.map((element) => element.boundElements === null
    ? { ...element, boundElements: [] } : element) as unknown as BoardDto["elements"];
}
function createPreviewLoader() {
  const originals = new Map<string, Promise<File>>();
  const previews = new Map<string, Promise<Preview>>();
  return (document: SourceDocumentDto, pageIndex: number) => {
    const key = pageKey(pageSource(document, pageIndex));
    const cached = previews.get(key);
    if (cached) return cached;
    const task = (async () => {
      const page = document.pages.find((item) => item.pageIndex === pageIndex);
      if (!page) throw new Error("The original document does not contain this page.");
      const key = documentKey(document);
      let original = originals.get(key);
      if (!original) {
        original = studio.documentFile(document.runId, document.assetSha256, document.fileName, document.revisionRef)
          .then((file) => file.type === document.mimeType ? file : new File([file], file.name, { type: document.mimeType }))
          .catch((error) => { originals.delete(key); throw error; });
        originals.set(key, original);
      }
      const visual = await renderDocumentVisual(await original, page, []);
      return { dataURL: `data:image/png;base64,${visual.pagePngBase64}` as DataURL, width: visual.width, height: visual.height };
    })().catch((error) => { previews.delete(key); throw error; });
    previews.set(key, task);
    return task;
  };
}
type PreviewLoader = ReturnType<typeof createPreviewLoader>;
async function sceneFiles(board: BoardDto, documents: SourceDocumentDto[], preview: PreviewLoader) {
  const files: BinaryFiles = {};
  const failures: string[] = [];
  // Render pages in sequence to avoid opening every PDF at once on a large board.
  for (const element of board.elements) {
    if (element.type !== "image" || typeof element.fileId !== "string" || files[element.fileId]) continue;
    const source = imageSource(element);
    const document = source && findSource(documents, source);
    if (!source || !document) { failures.push(String(element.id)); continue; }
    try {
      const rendered = await preview(document, source.pageIndex);
      files[element.fileId] = { id: element.fileId as FileId, dataURL: rendered.dataURL, mimeType: "image/png", created: 0 };
    } catch { failures.push(document.fileName); }
  }
  return { files, failures };
}

export default function MonkeyBoard() {
  const { language } = usePreferences();
  const text = copy[language];
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ board: BoardDto; documents: SourceDocumentDto[]; files: BinaryFiles; failures: string[]; preview: PreviewLoader } | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    setError(null);
    const preview = createPreviewLoader();
    void Promise.all([studio.board(), studio.documents()]).then(async ([board, list]) => {
      if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
      const visual = await sceneFiles(board, list.documents, preview);
      if (alive) setLoaded({ board, documents: list.documents, ...visual, preview });
    }).catch((cause) => { if (alive) setError(cause); });
    return () => { alive = false; };
  }, [attempt]);
  if (loaded) return <BoardCanvas {...loaded} />;
  return <section className="monkeyboard monkeyboard-loading" aria-live="polite">
    <strong>MonkeyBoard</strong>
    <p>{error === null ? text.loading : text.loadFailed}</p>
    {error !== null && <><p className="monkeyboard-error-detail">{errorText(error)}</p><button onClick={() => setAttempt((value) => value + 1)}>{text.retry}</button></>}
  </section>;
}

function BoardCanvas({ board, documents: initialDocuments, files, failures, preview }: {
  board: BoardDto; documents: SourceDocumentDto[]; files: BinaryFiles; failures: string[]; preview: PreviewLoader;
}) {
  const { language, theme } = usePreferences();
  const text = copy[language];
  const textRef = useRef<Copy>(text); textRef.current = text;
  const alive = useRef(true);
  const canvas = useRef<ExcalidrawImperativeAPI | null>(null);
  const root = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const [title, setTitle] = useState(board.title);
  const titleRef = useRef(title);
  const [documents, setDocuments] = useState(initialDocuments);
  const documentsRef = useRef(documents);
  const seen = useRef(new Set(board.seenDocuments));
  const skipped = useRef(new Set<string>());
  const initialized = useRef(false);
  const [ready, setReady] = useState(false);
  const [selected, setSelected] = useState<PageSource | null>(null);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  const [notice, setNotice] = useState("");
  const [previewFailed, setPreviewFailed] = useState(failures.length > 0);
  const [sourceError, setSourceError] = useState("");
  const [saveState, setSaveState] = useState<BoardSaveState>({ dirty: false, saving: false, error: null, conflict: false });
  const [queue] = useState(() => createBoardSaveQueue(board, studio.saveBoard, (state) => {
    if (alive.current) setSaveState(state);
  }));
  const work = useRef(Promise.resolve());
  const [initialData] = useState<ExcalidrawInitialDataState>(() => ({
    elements: board.elements as unknown as ExcalidrawElement[], files, scrollToContent: true,
    appState: { viewBackgroundColor: "#f4f5f0", currentItemStrokeColor: "#29352d", currentItemBackgroundColor: "transparent", currentItemRoughness: 0, currentItemFontFamily: FONT_FAMILY.Helvetica, currentItemStrokeWidth: 1, gridSize: 20 },
  }));
  const capture = useCallback((elements: readonly ExcalidrawElement[]) => {
    if (!initialized.current) return;
    const invalid = records(elements).some((element) => element.type === "image" && (!imageSource(element) || !findSource(documentsRef.current, imageSource(element)!)));
    if (invalid) { setNotice(textRef.current.unbound); return; }
    const draft: BoardDraft = { projectId: board.projectId, title: titleRef.current.trim() || "MonkeyBoard", elements: records(elements), seenDocuments: [...seen.current] };
    queue.change(draft);
  }, [board.projectId, queue]);
  const serial = useCallback((action: () => Promise<void>, visible = true) => {
    work.current = work.current.then(async () => {
      if (!alive.current) return;
      busyRef.current = true; if (visible) setBusy(true);
      try { await action(); } catch (error) { if (alive.current) setNotice(errorText(error)); }
      finally { busyRef.current = false; if (visible && alive.current) setBusy(false); }
    });
    return work.current;
  }, []);
  const addPage = useCallback(async (document: SourceDocumentDto, pageIndex: number, automatic = false) => {
    if (automatic && seen.current.has(documentKey(document))) return;
    const rendered = await preview(document, pageIndex);
    const api = canvas.current;
    if (!alive.current || !api || !initialized.current || queue.getState().conflict) return;
    if (automatic && seen.current.has(documentKey(document))) return;
    const position = nextDocumentPosition(records(api.getSceneElements()));
    const scale = Math.min(1, 1000 / Math.max(rendered.width, rendered.height));
    const imageId = crypto.randomUUID();
    const fileId = crypto.randomUUID() as FileId;
    const additions = convertToExcalidrawElements([
      { type: "image", id: imageId, fileId, ...position, width: rendered.width * scale, height: rendered.height * scale, status: "saved", customData: { sourceDocument: pageSource(document, pageIndex) } },
      { type: "frame", children: [imageId], name: `${document.fileName} · ${pageIndex + 1}/${document.pageCount}` },
    ], { regenerateIds: false });
    api.addFiles([{ id: fileId, dataURL: rendered.dataURL, mimeType: "image/png", created: Date.now() }]);
    seen.current.add(documentKey(document));
    const elements = [...api.getSceneElementsIncludingDeleted(), ...additions];
    api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    capture(elements);
    if (!automatic || elements.length === additions.length) api.scrollToContent(additions, { fitToContent: true, animate: false });
  }, [capture, preview, queue]);
  const acceptDocuments = useCallback((next: SourceDocumentDto[]) => {
    documentsRef.current = next;
    setDocuments(next);
  }, []);
  const receive = useCallback(async (next: SourceDocumentDto[]) => {
    for (const document of next) {
      const key = documentKey(document);
      if (seen.current.has(key) || skipped.current.has(key) || queue.getState().conflict) continue;
      try { await addPage(document, document.pages[0].pageIndex, true); }
      catch (error) { skipped.current.add(key); if (alive.current) setNotice(`${document.fileName}: ${errorText(error)}`); }
    }
  }, [addPage, queue]);
  const upload = useCallback((incoming: File[]) => serial(async () => {
    for (const file of incoming) {
      const mime = documentMime(file);
      if (!mime) { setNotice(textRef.current.unsupported); continue; }
      const original = file.type === mime ? file : new File([file], file.name, { type: mime });
      const document = await studio.uploadDocument(board.projectId, null, original);
      if (!alive.current) return;
      acceptDocuments([...documentsRef.current.filter((item) => documentKey(item) !== documentKey(document)), document]);
      await addPage(document, document.pages[0].pageIndex);
    }
  }), [acceptDocuments, addPage, board.projectId, serial]);

  useEffect(() => {
    alive.current = true;
    const warn = (event: BeforeUnloadEvent) => {
      const state = queue.getState();
      if (state.dirty || state.saving || busyRef.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      alive.current = false;
      // React's development effect rehearsal remounts immediately; real unmounts flush once.
      queueMicrotask(() => { if (!alive.current) queue.dispose(); });
      window.removeEventListener("beforeunload", warn);
    };
  }, [queue]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const changed = () => setSystemDark(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (!active || refreshing || document.hidden) return;
      refreshing = true;
      try {
        await serial(async () => {
          try {
            const list = await studio.documents();
            if (!active) return;
            if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
            acceptDocuments(list.documents); setSourceError("");
            await receive(list.documents);
          } catch (error) { if (active) setSourceError(errorText(error)); }
        }, false);
      } catch (error) { if (active) setSourceError(errorText(error)); }
      finally { refreshing = false; }
    };
    void serial(() => receive(documentsRef.current));
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    window.addEventListener("focus", refresh);
    return () => { active = false; window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [acceptDocuments, board.projectId, ready, receive, serial]);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      const api = canvas.current;
      if (!(event.target instanceof HTMLCanvasElement) || !api || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault(); event.stopPropagation();
      const state = api.getAppState();
      const point = viewportCoordsToSceneCoords({ clientX: event.clientX, clientY: event.clientY }, state);
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1);
      const zoom = Math.min(30, Math.max(0.1, state.zoom.value * Math.exp(-delta * 0.0015))) as AppState["zoom"]["value"];
      api.updateScene({ appState: { zoom: { value: zoom }, scrollX: (point.x + state.scrollX) * state.zoom.value / zoom - point.x, scrollY: (point.y + state.scrollY) * state.zoom.value / zoom - point.y }, captureUpdate: CaptureUpdateAction.NEVER });
    };
    element.addEventListener("wheel", wheel, { passive: false, capture: true });
    return () => element.removeEventListener("wheel", wheel, true);
  }, []);

  const onPasteCapture = (event: ReactClipboardEvent) => {
    if (event.target instanceof Element && event.target.closest("input,textarea,select,[contenteditable=true]")) return;
    const incoming = [...event.clipboardData.files];
    if (incoming.length) {
      event.preventDefault(); event.stopPropagation(); event.nativeEvent.stopImmediatePropagation();
      void upload(incoming); return;
    }
    if (/<(?:img|svg)\b/i.test(event.clipboardData.getData("text/html"))) {
      event.preventDefault(); event.stopPropagation(); event.nativeEvent.stopImmediatePropagation(); setNotice(text.unbound);
    }
  };
  const onDropCapture = (event: DragEvent) => {
    if (!event.dataTransfer.types.includes("Files")) return;
    event.preventDefault(); event.stopPropagation(); event.nativeEvent.stopImmediatePropagation();
    void upload([...event.dataTransfer.files]);
  };
  const retryVisuals = () => { void serial(async () => {
    const list = await studio.documents();
    if (!alive.current) return;
    if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
    acceptDocuments(list.documents); setSourceError(""); skipped.current.clear();
    const restored = await sceneFiles({ ...board, elements: records(canvas.current?.getSceneElementsIncludingDeleted() ?? []) }, list.documents, preview);
    if (!alive.current) return;
    canvas.current?.addFiles(Object.values(restored.files)); setPreviewFailed(restored.failures.length > 0);
    await receive(list.documents);
  }); };
  const source = selected && findSource(documents, selected);
  const resolvedTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  return <section className="monkeyboard" aria-label="MonkeyBoard">
    <header className="monkeyboard-topbar">
      <div className="monkeyboard-heading"><span className="monkeyboard-brand">MonkeyBoard</span><input aria-label={text.title} value={title} maxLength={200} onChange={(event) => { const value = event.target.value; setTitle(value); titleRef.current = value; capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} onBlur={() => { const value = titleRef.current.trim() || "MonkeyBoard"; titleRef.current = value; setTitle(value); capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} /></div>
      <span className={`monkeyboard-save-state${saveState.error ? " is-error" : ""}`} role="status">{saveState.error ? text.dirty : saveState.saving ? text.saving : saveState.dirty ? text.dirty : text.saved}</span>
      <button className="monkeyboard-primary" disabled={!ready || busy || saveState.conflict} onClick={() => input.current?.click()}>{text.upload}</button>
      <button disabled={!ready} onClick={() => canvas.current?.scrollToContent(undefined, { fitToContent: true, animate: false })}>{text.fit}</button>
      <input ref={input} type="file" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg" multiple hidden onChange={(event) => { void upload([...event.target.files ?? []]); event.target.value = ""; }} />
    </header>
    {saveState.error !== null && <div className="monkeyboard-alert" role="alert"><span>{saveState.conflict ? text.conflict : `${text.saveError} ${errorText(saveState.error)}`}</span>{saveState.conflict ? <a href={window.location.href} target="_blank" rel="noopener noreferrer">{text.compare}</a> : <button onClick={() => { void queue.retry().catch(() => {}); }}>{text.retry}</button>}</div>}
    {(previewFailed || sourceError) && <div className="monkeyboard-alert" role="alert"><span>{previewFailed ? text.previewError : `${text.sourceError} ${sourceError}`}</span><button onClick={retryVisuals} disabled={busy}>{text.refresh}</button></div>}
    {notice && <div className="monkeyboard-alert" role="alert"><span>{notice}</span><button onClick={() => setNotice("")} aria-label={text.dismiss}>×</button></div>}
    <div className="monkeyboard-body">
      <aside className="monkeyboard-sources" aria-label={text.sources}>
        <div className="monkeyboard-source-heading"><h2>{text.sources}</h2><span>{documents.length}</span></div>
        <p className="monkeyboard-auto"><span aria-hidden="true" />{text.auto}</p>
        {documents.length === 0 && <p className="monkeyboard-empty">{text.empty}</p>}
        <div className="monkeyboard-source-list">{documents.map((document) => {
          const key = documentKey(document);
          const page = pages[key] ?? document.pages[0].pageIndex;
          return <article className="monkeyboard-source" key={key}>
            <div className="monkeyboard-source-type">{document.mimeType === "application/pdf" ? "PDF" : document.mimeType === "image/png" ? "PNG" : "JPEG"}<span>{document.pageCount} {text.pages}</span></div>
            <h3 title={document.fileName}>{document.fileName}</h3>
            <p className="monkeyboard-received">{seen.current.has(key) ? text.received : text.pending}{document.generatedAt ? ` · ${new Date(document.generatedAt).toLocaleString(language, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : ""}</p>
            <div className="monkeyboard-page-row"><select aria-label={`${document.fileName} ${text.page}`} value={page} onChange={(event) => setPages((value) => ({ ...value, [key]: Number(event.target.value) }))}>{document.pages.map((item) => <option value={item.pageIndex} key={item.pageIndex}>{text.page} {item.pageIndex + 1} / {document.pageCount}</option>)}</select><button disabled={!ready || busy || saveState.conflict} onClick={() => { void serial(() => addPage(document, page)); }}>{text.add}</button></div>
            <a className="monkeyboard-source-link" href={documentUrl(window.location.href, pageSource(document, page))} target="_blank" rel="noopener noreferrer">{text.open} ↗</a>
          </article>;
        })}</div>
      </aside>
      <div className="monkeyboard-canvas" ref={root} onPasteCapture={onPasteCapture} onDropCapture={onDropCapture} onDragOverCapture={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.stopPropagation(); } }} onKeyDownCapture={(event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); event.stopPropagation(); void queue.flush().catch(() => {}); } }}>
        <Excalidraw initialData={initialData} excalidrawAPI={(api) => { canvas.current = api; }} langCode={language} theme={resolvedTheme} name={title} aiEnabled={false} validateEmbeddable={false} autoFocus handleKeyboardGlobally={false} UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false }, tools: { image: false } }}
          onPaste={(data) => {
            if (data.elements?.some((element) => element.type === "image" && (!imageSource(element as unknown as Record<string, unknown>) || !findSource(documentsRef.current, imageSource(element as unknown as Record<string, unknown>)!)))) { setNotice(text.unbound); return false; }
            return true;
          }}
          onChange={(elements, appState) => {
            if (!alive.current || appState.isLoading) return;
            if (appState.openSidebar?.name === "default") canvas.current?.updateScene({ appState: { openSidebar: null }, captureUpdate: CaptureUpdateAction.NEVER });
            if (!initialized.current) {
              const ids = new Set(elements.filter((element) => !element.isDeleted).map((element) => element.id));
              if (board.elements.some((element) => !element.isDeleted && !ids.has(String(element.id)))) return;
              initialized.current = true; setReady(true);
            }
            const selection = elements.find((element) => !element.isDeleted && appState.selectedElementIds[element.id] && element.type === "image");
            const selectedSource = selection ? imageSource(selection as unknown as Record<string, unknown>) : null;
            setSelected((previous) => JSON.stringify(previous) === JSON.stringify(selectedSource) ? previous : selectedSource);
            capture(elements);
          }}>
          <MainMenu><MainMenu.Item onSelect={() => input.current?.click()}>{text.upload}</MainMenu.Item><MainMenu.Item onSelect={() => { void queue.flush().catch(() => {}); }}>{text.save}</MainMenu.Item><MainMenu.DefaultItems.ClearCanvas /></MainMenu>
          <WelcomeScreen><WelcomeScreen.Center><WelcomeScreen.Center.Heading>{text.welcome}</WelcomeScreen.Center.Heading><div className="monkeyboard-welcome-body">{text.welcomeBody}</div><WelcomeScreen.Center.Menu><WelcomeScreen.Center.MenuItem onSelect={() => input.current?.click()}>{text.upload}</WelcomeScreen.Center.MenuItem></WelcomeScreen.Center.Menu></WelcomeScreen.Center></WelcomeScreen>
        </Excalidraw>
        {!ready && <div className="monkeyboard-initializing" role="status">{text.loading}</div>}
        {busy && <div className="monkeyboard-busy" role="status">{text.busy}</div>}
      </div>
    </div>
    <footer className="monkeyboard-footer"><span>{text.hint}</span>{source && selected ? <a href={documentUrl(window.location.href, selected)} target="_blank" rel="noopener noreferrer">{source.fileName} · {selected.pageIndex + 1}/{source.pageCount} · {text.open} ↗</a> : <span>{text.select}</span>}</footer>
  </section>;
}
