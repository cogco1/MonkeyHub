import { useCallback, useEffect, useRef, useState, type ClipboardEvent as ReactClipboardEvent, type DragEvent } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, Excalidraw, FONT_FAMILY, MainMenu, newElementWith, viewportCoordsToSceneCoords, WelcomeScreen } from "@excalidraw/excalidraw";
import type { ExcalidrawElement, FileId } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles, DataURL, ExcalidrawImperativeAPI, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

import { studio } from "../../api/client";
import type { BoardDto, SourceDocumentDto } from "../../api/generated";
import { usePreferences } from "../../features/settings/preferences";
import { renderDocumentVisual } from "../monkeydiagram/documentVisualInput";
import { createBoardSaveQueue, type BoardSaveState } from "./boardSaveQueue";
import { prepareBoardDesignRequest, type BoardDesignRequest } from "./boardFeedback";
import { BoardFeedbackGeometryError, createBoardFeedback, type BoardFeedbackSelection } from "./boardFeedbackGeometry";
import { documentKey, documentMime, documentUrl, findSource, imageSource, nextDocumentPosition, pageKey, pageReplacements, pageSource, type BoardDraft, type PageSource } from "./boardScene";
import "./board.css";

const copy = {
  en: { loading: "Opening board…", loadFailed: "The board could not be opened.", retry: "Retry", sources: "Project documents", upload: "Upload PDF / image", title: "Board title", saved: "Saved", saving: "Saving…", dirty: "Unsaved changes", saveError: "Changes have not been saved.", conflict: "Another saved version exists. Your current canvas is preserved; open the saved board separately to compare.", compare: "Open saved board", save: "Save now", add: "Add page", open: "Open in MonkeyDiagram", fit: "Fit board", busy: "Receiving document…", clearAnnotations: "Clear annotations", clearAnnotationsHint: "Clear all drawn marks and text; keep drawings and frames. Ctrl+Z to undo.", crit: "Crit mode", critSubmit: "Submit", critExit: "Exit", export: "Export board pages", exportClean: "Clean originals · marks excluded", exportMerged: "Merged PDF", exportPages: "One PDF per page", exportPng: "PNG", exportJpeg: "JPEG", exportZip: "ZIP for transfer", exporting: "Preparing export…", exportDone: "Export ready.", exportEmpty: "Place at least one registered drawing page on the board before exporting.", empty: "Upload a PDF, PNG or JPEG to begin. New project drawings will appear here.", hint: "Wheel to zoom · Space or middle mouse to pan · Shift to select several", auto: "New documents arrive automatically", previewError: "Some page previews could not be loaded. The saved layout is retained.", unsupported: "Use PDF, PNG or JPEG files.", unbound: "This image has no registered project source. Upload its original file first.", page: "Page", pages: "pages", received: "Received", pending: "Pending", dismiss: "Dismiss", sourceError: "Project documents could not be refreshed.", select: "Select a drawing to open its original.", refresh: "Retry previews / receive", unknown: "Unknown error" },
  "zh-CN": { loading: "正在打开画布…", loadFailed: "画布暂时无法打开。", retry: "重试", sources: "项目资料", upload: "上传 PDF / 图片", title: "画布标题", saved: "已保存", saving: "正在保存…", dirty: "有未保存的修改", saveError: "修改尚未保存。", conflict: "已有另一份保存版本。当前画布已保留，请另开已保存画布进行比较。", compare: "另开已保存画布", save: "立即保存", add: "添加此页", open: "在 MonkeyDiagram 中打开", fit: "查看全部", busy: "正在接收资料…", clearAnnotations: "清除批注", clearAnnotationsHint: "清除全部圈线、箭头和文字，保留图纸与图框；可用 Ctrl+Z 撤销。", crit: "Crit 模式", critSubmit: "提交", critExit: "退出", export: "整理导出图墙图纸", exportClean: "清洁原图 · 不含批注", exportMerged: "合并 PDF", exportPages: "单页 PDF", exportPng: "PNG", exportJpeg: "JPEG", exportZip: "传输 ZIP", exporting: "正在整理导出…", exportDone: "导出已就绪。", exportEmpty: "请先在图墙中摆放至少一页已登记图纸。", empty: "上传 PDF、PNG 或 JPEG 开始。项目的新图纸也会自动出现在这里。", hint: "滚轮缩放 · 空格或鼠标中键平移 · Shift 多选", auto: "自动接收新资料", previewError: "部分页面预览未能载入，已保留原有布局。", unsupported: "请使用 PDF、PNG 或 JPEG 文件。", unbound: "这张图片没有项目来源，请先上传原始文件。", page: "第", pages: "页", received: "已接收", pending: "待接收", dismiss: "关闭提示", sourceError: "项目资料暂时无法刷新。", select: "选中图纸可打开原始页面。", refresh: "重试预览 / 接收", unknown: "未知错误" },
};
type Copy = typeof copy.en;

const whiteboardCopy = {
  en: { more: "More board actions", hideSources: "Hide project documents", welcome: "Drop a drawing or image here", gestures: "Circle, draw an arrow, or type a note.", example: "Drawing + arrow + note", designHint: "Select a project drawing and your marks to discover design feedback.", oneSource: "Select one drawing at a time to send feedback.", stale: "This drawing is no longer available. Select its current page from project documents.", outside: "Move the selected marks fully onto the drawing before sending.", unsupported: "This selection cannot be sent yet. Use solid outline marks on an uncropped drawing.", invalid: "A selected object has invalid geometry. Redraw it before sending.", linkModel: "Link model in MonkeyDiagram" },
  "zh-CN": { more: "更多画板操作", hideSources: "收起项目资料", welcome: "拖入图纸或图片开始", gestures: "圈画、画箭头，或直接写下想法。", example: "图纸 + 箭头 + 文字", designHint: "选中项目图纸与圈线，即可查看设计反馈入口。", oneSource: "每次选中一张图纸发送反馈。", stale: "这张图纸已不可用，请从项目资料重新选择当前图页。", outside: "请先将选中圈线完整移入图纸范围。", unsupported: "此选区暂时无法发送，请使用实线轮廓标记及未裁切的图纸。", invalid: "选中对象的几何无效，请重新绘制后发送。", linkModel: "在 MonkeyDiagram 中关联模型" },
};

type FeedbackContext = { source: PageSource | null; reason: "modelRequired" | "oneSource" | "stale" | "outside" | "unsupported" | "invalid" | null };

// The same strict, local conversion powers eligibility and the explicit action.
// Keep only the affordance in React state; the live Excalidraw scene remains its owner.
function feedbackContext(elements: readonly ExcalidrawElement[], selectedIds: AppState["selectedElementIds"], documents: SourceDocumentDto[]): FeedbackContext | null {
  try {
    const selection = createBoardFeedback(elements, selectedIds, documents);
    return { source: selection.source, reason: selection.document.modelSource ? null : "modelRequired" };
  } catch (error) {
    if (!(error instanceof BoardFeedbackGeometryError)) throw error;
    switch (error.code) {
      case "BOARD_FEEDBACK_SOURCE_REQUIRED": return null;
      case "BOARD_FEEDBACK_SOURCE_AMBIGUOUS": return { source: null, reason: "oneSource" };
      case "BOARD_FEEDBACK_SOURCE_UNAVAILABLE": return { source: null, reason: "stale" };
      case "BOARD_FEEDBACK_OUTSIDE_PAGE": return { source: null, reason: "outside" };
      case "BOARD_FEEDBACK_INVALID_GEOMETRY": return { source: null, reason: "invalid" };
      default: return { source: null, reason: "unsupported" };
    }
  }
}

function isAnnotation(element: ExcalidrawElement): boolean {
  return !element.isDeleted && ["freedraw", "line", "arrow", "rectangle", "ellipse", "diamond", "text"].includes(element.type);
}

const replacementCopy = {
  en: { action: "Update this page", file: "Updated PDF / image", page: "Page number in the new file", hint: "Replace this page wherever it is placed on the board. Keep its position, scale and marks. The new page must have the same aspect ratio; the original remains in project documents.", cancel: "Cancel", submit: "Update in place", sending: "Updating page…" },
  "zh-CN": { action: "更新此页原图", file: "更新后的 PDF / 图片", page: "新文件中的页码", hint: "更新图墙中此页的所有副本，保留位置、缩放与批注。新页须保持相同宽高比；旧原图仍保存在项目资料中。", cancel: "取消", submit: "原位更新", sending: "正在更新…" },
};

function ReplacementDialog({ target, language, onCancel, onSubmit }: {
  target: { document: SourceDocumentDto; pageIndex: number }; language: "en" | "zh-CN";
  onCancel: () => void; onSubmit: (file: File, newPageIndex: number) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [page, setPage] = useState(1);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [error, setError] = useState("");
  const text = replacementCopy[language];
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  const submit = async () => {
    if (!file || sendingRef.current) return;
    sendingRef.current = true; setSending(true); setError("");
    try { await onSubmit(file, page - 1); }
    catch (cause) { setError(errorText(cause)); }
    finally { sendingRef.current = false; setSending(false); }
  };
  return <dialog ref={dialog} className="monkeyboard-feedback" aria-labelledby="monkeyboard-replacement-title" onCancel={(event) => { event.preventDefault(); if (!sendingRef.current) onCancel(); }}>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }} aria-busy={sending}>
      <h2 id="monkeyboard-replacement-title">{text.action}</h2>
      <p className="monkeyboard-feedback-source">{target.document.fileName} · {target.pageIndex + 1}/{target.document.pageCount}</p>
      <p>{text.hint}</p>
      <label htmlFor="monkeyboard-replacement-file">{text.file}</label>
      <input id="monkeyboard-replacement-file" type="file" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg" required autoFocus disabled={sending} onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      <label htmlFor="monkeyboard-replacement-page">{text.page}</label>
      <input id="monkeyboard-replacement-page" type="number" min={1} step={1} required value={page} disabled={sending} onChange={(event) => setPage(event.target.valueAsNumber)} />
      {error && <p className="monkeyboard-feedback-error" role="alert">{error}</p>}
      <div className="monkeyboard-feedback-actions"><button type="button" onClick={onCancel} disabled={sending}>{text.cancel}</button><button className="monkeyboard-primary" type="submit" disabled={sending || !file}>{sending ? text.sending : text.submit}</button></div>
    </form>
  </dialog>;
}

const feedbackCopy = {
  en: { action: "Send design feedback", title: "Discuss this drawing", hint: "Select one drawing or its frame, together with the marks and text you want to send.", description: "The selected marks will join this drawing page. Continue the discussion in MonkeyArch to review a proposal or answer a design question.", label: "What would you like to change?", placeholder: "Describe the change and what should stay as it is.", send: "Send to design", sending: "Preparing drawing…", cancel: "Cancel", marks: "selected marks", textIncluded: "Selected text is included below. Send it as it is, or add what should stay unchanged.", modelRequired: "Link this drawing to its model in MonkeyDiagram before sending a design change.", sourceChanged: "This drawing's source changed. Close this dialog and select the drawing again.", modelChanged: "The drawing's linked model changed or cannot be opened for editing. Check its source in MonkeyDiagram.", empty: "Write the change you want to discuss.", failed: "The feedback could not be sent." },
  "zh-CN": { action: "发送设计反馈", title: "讨论这张图纸", hint: "选中一张图纸或它的图框，并同时选中要提交的圈线与文字。", description: "选中的圈线会加入对应图页。进入 MonkeyArch 后，可审阅修改提案或回答需要澄清的问题。", label: "希望怎样修改？", placeholder: "说明要调整的内容，以及需要保留的部分。", send: "发送到设计", sending: "正在准备图纸…", cancel: "取消", marks: "条选中标记", textIncluded: "已带入选中文字，可直接发送，或补充需要保留的内容。", modelRequired: "请先在 MonkeyDiagram 中关联这张图纸对应的模型，再提交设计修改。", sourceChanged: "这张图纸的来源已发生变化，请关闭此窗口并重新选择图纸。", modelChanged: "图纸关联的模型已改变或暂时无法继续编辑，请在 MonkeyDiagram 中检查来源。", empty: "请写下希望讨论的修改。", failed: "意见暂时未能发送。" },
};

function feedbackError(error: unknown, language: "en" | "zh-CN"): string {
  const text = feedbackCopy[language];
  const code = error && typeof error === "object" && "code" in error ? error.code : null;
  if (code === "SOURCE_CHANGED") return text.sourceChanged;
  if (code === "MODEL_REQUIRED") return text.modelRequired;
  if (code === "MODEL_CHANGED") return text.modelChanged;
  if (code === "EMPTY_COMMENT") return text.empty;
  return errorText(error);
}

function FeedbackDialog({ selection, language, returnFocus, onCancel, onSubmit }: {
  selection: BoardFeedbackSelection; language: "en" | "zh-CN";
  returnFocus: HTMLElement | null;
  onCancel: () => void; onSubmit: (comment: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement | null>(null);
  const [comment, setComment] = useState(selection.selectedText);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [error, setError] = useState("");
  const text = feedbackCopy[language];
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); if (returnFocus?.isConnected) returnFocus.focus(); };
  }, []);
  const submit = async () => {
    if (sendingRef.current || !comment.trim() || !selection.document.modelSource) return;
    sendingRef.current = true; setSending(true); setError("");
    try { await onSubmit(comment); }
    catch (cause) { setError(feedbackError(cause, language)); }
    finally { sendingRef.current = false; setSending(false); }
  };
  return <dialog ref={dialog} className="monkeyboard-feedback" aria-labelledby="monkeyboard-feedback-title" onCancel={(event) => { event.preventDefault(); if (!sendingRef.current) onCancel(); }}>
    <form onSubmit={(event) => { event.preventDefault(); void submit(); }} aria-busy={sending}>
      <h2 id="monkeyboard-feedback-title">{text.title}</h2>
      <p className="monkeyboard-feedback-source">{selection.document.fileName} · {selection.page.pageIndex + 1}/{selection.document.pageCount} · {selection.annotationGroups.length} {text.marks}</p>
      <p>{text.description}</p>
      {selection.selectedText && <p>{text.textIncluded}</p>}
      <label htmlFor="monkeyboard-feedback-comment">{text.label}</label>
      <textarea id="monkeyboard-feedback-comment" value={comment} onChange={(event) => setComment(event.target.value)} placeholder={text.placeholder} autoFocus required rows={4} disabled={sending} />
      {!selection.document.modelSource && <p className="monkeyboard-feedback-error" role="alert">{text.modelRequired}</p>}
      {error && <p className="monkeyboard-feedback-error" role="alert">{text.failed} {error}</p>}
      <a href={documentUrl(window.location.href, selection.source)} target="_blank" rel="noopener noreferrer">{copy[language].open} ↗</a>
      <div className="monkeyboard-feedback-actions"><button type="button" onClick={onCancel} disabled={sending}>{text.cancel}</button><button className="monkeyboard-primary" type="submit" disabled={sending || !comment.trim() || !selection.document.modelSource}>{sending ? text.sending : text.send}</button></div>
    </form>
  </dialog>;
}
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

export default function MonkeyBoard({ onSubmit }: { onSubmit: (request: BoardDesignRequest) => void }) {
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
  if (loaded) return <BoardCanvas {...loaded} onSubmit={onSubmit} />;
  return <section className="monkeyboard monkeyboard-loading" aria-live="polite">
    <strong>MonkeyBoard</strong>
    <p>{error === null ? text.loading : text.loadFailed}</p>
    {error !== null && <><p className="monkeyboard-error-detail">{errorText(error)}</p><button onClick={() => setAttempt((value) => value + 1)}>{text.retry}</button></>}
  </section>;
}

function BoardCanvas({ board, documents: initialDocuments, files, failures, preview, onSubmit }: {
  board: BoardDto; documents: SourceDocumentDto[]; files: BinaryFiles; failures: string[]; preview: PreviewLoader;
  onSubmit: (request: BoardDesignRequest) => void;
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
  const [hasAnnotations, setHasAnnotations] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [context, setContext] = useState<FeedbackContext | null>(null);
  const actions = useRef<HTMLDetailsElement | null>(null);
  const [critMode, setCritMode] = useState(false);
  const previousTool = useRef<AppState["activeTool"] | null>(null);
  const [selected, setSelected] = useState<PageSource | null>(null);
  const [feedback, setFeedback] = useState<BoardFeedbackSelection | null>(null);
  const feedbackReturnFocus = useRef<HTMLElement | null>(null);
  const feedbackOpen = useRef(false);
  const [replacement, setReplacement] = useState<{ document: SourceDocumentDto; pageIndex: number } | null>(null);
  const replacementOpen = useRef(false);
  const feedbackQueued = useRef(false);
  const [feedbackWaiting, setFeedbackWaiting] = useState(false);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [exportFormat, setExportFormat] = useState<"merged-pdf" | "page-pdfs" | "png" | "jpeg">("merged-pdf");
  const [exportZip, setExportZip] = useState(false);
  const [exporting, setExporting] = useState(false);
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
  const updateContext = (elements: readonly ExcalidrawElement[], appState: AppState) => {
    const next = appState.editingTextElement || appState.newElement || appState.selectionElement || appState.isResizing || appState.isRotating
      ? null : feedbackContext(elements, appState.selectedElementIds, documentsRef.current);
    setContext((previous) => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
  };
  useEffect(() => {
    const api = canvas.current;
    if (api) updateContext(api.getSceneElements(), api.getAppState());
  }, [documents]);
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
    const replacements = pageReplacements(next);
    const pending = next.filter((document) => !seen.current.has(documentKey(document)) && (document.replacesPages?.length ?? 0) > 0);
    if (pending.length > 0) {
      const rendered = new Map<string, { source: PageSource; preview: Preview; fileId: FileId }>();
      while (true) {
        const target = (canvas.current?.getSceneElements() ?? []).flatMap((element) => {
          const source = imageSource(element);
          const next = source && replacements.get(pageKey(source));
          return next && !rendered.has(pageKey(next)) ? [next] : [];
        })[0];
        if (!target || !alive.current) break;
        const document = findSource(next, target);
        if (!document) throw new Error("The replacement drawing page is unavailable.");
        rendered.set(pageKey(target), { source: target, preview: await preview(document, target.pageIndex), fileId: crypto.randomUUID() as FileId });
      }
      const api = canvas.current;
      if (!alive.current || !api || !initialized.current || queue.getState().conflict) return;
      // Rendering may take seconds. Read the live scene only after every await:
      // user moves, deletions, new marks and frame membership win over snapshots.
      const elements = api.getSceneElementsIncludingDeleted().map((element) => {
        if (element.type !== "image" || element.isDeleted) return element;
        const source = imageSource(element);
        const target = source && replacements.get(pageKey(source));
        const ready = target && rendered.get(pageKey(target));
        if (!ready) return element;
        const crop = element.crop;
        // Excalidraw crop coordinates use source pixels, not board units.
        const nextCrop = crop ? {
          x: crop.x / crop.naturalWidth * ready.preview.width,
          y: crop.y / crop.naturalHeight * ready.preview.height,
          width: crop.width / crop.naturalWidth * ready.preview.width,
          height: crop.height / crop.naturalHeight * ready.preview.height,
          naturalWidth: ready.preview.width, naturalHeight: ready.preview.height,
        } : null;
        return newElementWith(element, { fileId: ready.fileId, status: "saved", crop: nextCrop, customData: { ...element.customData, sourceDocument: ready.source } });
      });
      api.addFiles([...rendered.values()].map((item) => ({ id: item.fileId, dataURL: item.preview.dataURL, mimeType: "image/png", created: Date.now() })));
      api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
      capture(elements);
    }
    for (const document of next) {
      const key = documentKey(document);
      if (seen.current.has(key) || skipped.current.has(key) || queue.getState().conflict) continue;
      if ((document.replacesPages?.length ?? 0) > 0) continue;
      try {
        const target = replacements.get(pageKey(pageSource(document, document.pages[0].pageIndex)));
        const latest = target ? findSource(next, target) : document;
        if (!latest) throw new Error("The replacement drawing page is unavailable.");
        await addPage(latest, target?.pageIndex ?? document.pages[0].pageIndex, true);
        seen.current.add(key);
      }
      catch (error) { skipped.current.add(key); if (alive.current) setNotice(`${document.fileName}: ${errorText(error)}`); }
    }
    const waitingForPreview = new Set(next.flatMap((document) => {
      if (!skipped.current.has(documentKey(document))) return [];
      const target = replacements.get(pageKey(pageSource(document, document.pages[0].pageIndex)));
      return target ? [documentKey(target)] : [];
    }));
    for (const document of pending) {
      if (!waitingForPreview.has(documentKey(document))) seen.current.add(documentKey(document));
    }
    capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []);
  }, [addPage, capture, preview, queue]);
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
  const replacePage = async (file: File, newPageIndex: number) => {
    if (!replacement) return;
    const mime = documentMime(file);
    if (!mime) throw new Error(text.unsupported);
    await work.current;
    if (!alive.current || queue.getState().conflict) throw new Error(text.conflict);
    busyRef.current = true; setBusy(true);
    try {
      await queue.retry();
      const original = file.type === mime ? file : new File([file], file.name, { type: mime });
      const updated = await studio.uploadDocument(board.projectId, null, original, [{ ...pageSource(replacement.document, replacement.pageIndex), newPageIndex }]);
      if (!alive.current) return;
      const next = [...documentsRef.current.filter((item) => documentKey(item) !== documentKey(updated)), updated];
      acceptDocuments(next);
      await receive(next);
      await queue.flush();
      replacementOpen.current = false; setReplacement(null);
    } finally { busyRef.current = false; if (alive.current) setBusy(false); }
  };

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
      if (!active || refreshing || document.hidden || feedbackOpen.current || replacementOpen.current) return;
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
  const exportBoard = async () => {
    const placed = canvas.current?.getSceneElements().filter((element) => element.type === "image") ?? [];
    const ordered = [...placed].sort((left, right) => left.y - right.y || left.x - right.x);
    const seenPages = new Set<string>();
    const sources = ordered.flatMap((element) => {
      const source = imageSource(element as unknown as Record<string, unknown>);
      if (!source) return [];
      const key = `${source.runId}:${source.assetSha256}:${source.revisionRef ?? ""}:${source.pageIndex}`;
      if (seenPages.has(key)) return [];
      seenPages.add(key);
      return [source];
    });
    if (sources.length === 0) { setNotice(text.exportEmpty); return; }
    setExporting(true); setNotice("");
    try {
      await queue.flush();
      const blob = await studio.exportBoard({ projectId: board.projectId, pages: sources, format: exportFormat, zip: exportZip });
      const suffix = exportZip || (exportFormat !== "merged-pdf" && sources.length > 1) ? "zip" : exportFormat === "merged-pdf" || exportFormat === "page-pdfs" ? "pdf" : exportFormat;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = `monkeyboard-export.${suffix}`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice(text.exportDone);
    } catch (cause) { setNotice(errorText(cause)); }
    finally { if (alive.current) setExporting(false); }
  };
  const source = selected && findSource(documents, selected);
  const closeActions = () => {
    const element = actions.current;
    if (!element?.open) return;
    const restoreFocus = element.contains(document.activeElement);
    element.open = false;
    if (restoreFocus) element.querySelector("summary")?.focus();
  };
  const openFeedbackNow = () => {
    const api = canvas.current;
    if (!api || !ready) return;
    try {
      const next = createBoardFeedback(api.getSceneElements(), api.getAppState().selectedElementIds, documentsRef.current);
      feedbackOpen.current = true; setFeedback(next);
    }
    catch (cause) { setNotice(`${feedbackCopy[language].hint} ${feedbackError(cause, language)}`); }
  };
  const openFeedback = () => {
    const focused = document.activeElement;
    feedbackReturnFocus.current = actions.current?.contains(focused)
      ? actions.current.querySelector("summary") : focused instanceof HTMLElement ? focused : null;
    closeActions();
    if (!ready || !canvas.current) return;
    if (!busyRef.current) { openFeedbackNow(); return; }
    if (feedbackQueued.current) return;
    feedbackQueued.current = true; setFeedbackWaiting(true);
    void work.current.finally(() => {
      feedbackQueued.current = false;
      if (alive.current) { setFeedbackWaiting(false); openFeedbackNow(); }
    });
  };
  const enterCrit = () => {
    closeActions();
    const api = canvas.current;
    if (!api || !ready) return;
    const state = api.getAppState();
    previousTool.current = state.activeTool;
    api.updateScene({ appState: { activeTool: { type: "freedraw", customType: null, lastActiveTool: state.activeTool, locked: true } }, captureUpdate: CaptureUpdateAction.NEVER });
    setCritMode(true);
  };
  const clearAnnotations = () => {
    closeActions();
    const api = canvas.current;
    if (!api || !ready || busyRef.current || queue.getState().conflict) return;
    const current = api.getSceneElementsIncludingDeleted();
    const removed = new Set(current.filter(isAnnotation).map((element) => element.id));
    if (removed.size === 0) return;
    const elements = current.map((element) => {
      if (element.isDeleted) return element;
      if (removed.has(element.id)) return newElementWith(element, { isDeleted: true });
      const boundElements = element.boundElements?.filter((bound) => !removed.has(bound.id));
      return boundElements && boundElements.length !== element.boundElements?.length
        ? newElementWith(element, { boundElements }) : element;
    });
    api.updateScene({ elements, appState: { selectedElementIds: {}, selectedGroupIds: {} }, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    capture(elements);
    root.current?.querySelector<HTMLElement>(".excalidraw")?.focus();
  };
  const exitCrit = useCallback(() => {
    const api = canvas.current;
    if (api && previousTool.current) api.updateScene({ appState: { activeTool: previousTool.current }, captureUpdate: CaptureUpdateAction.NEVER });
    previousTool.current = null;
    setCritMode(false);
  }, []);
  useEffect(() => {
    if (!critMode) return;
    const leave = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !feedbackOpen.current) { event.preventDefault(); exitCrit(); }
    };
    window.addEventListener("keydown", leave);
    return () => window.removeEventListener("keydown", leave);
  }, [critMode, exitCrit]);
  const submitFeedback = async (comment: string) => {
    if (!feedback) return;
    // Finish any in-flight canvas save before leaving this workspace.
    busyRef.current = true;
    try {
      await queue.flush();
      const request = await prepareBoardDesignRequest(feedback, board.projectId, comment);
      if (alive.current) onSubmit(request);
    } finally { busyRef.current = false; }
  };
  const resolvedTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;
  const boardText = whiteboardCopy[language];
  return <section className={`monkeyboard${critMode ? " monkeyboard--crit" : ""}`} aria-label="MonkeyBoard">
    <header className="monkeyboard-topbar">
      <div className="monkeyboard-heading"><span className="monkeyboard-brand">MonkeyBoard</span><input aria-label={text.title} value={title} maxLength={200} onChange={(event) => { const value = event.target.value; setTitle(value); titleRef.current = value; capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} onBlur={() => { const value = titleRef.current.trim() || "MonkeyBoard"; titleRef.current = value; setTitle(value); capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} /></div>
      <span className={`monkeyboard-save-state${saveState.error ? " is-error" : ""}`} role="status">{saveState.error ? text.dirty : saveState.saving ? text.saving : saveState.dirty ? text.dirty : text.saved}</span>
      <button aria-expanded={sourcesOpen} aria-controls="monkeyboard-project-documents" onClick={() => setSourcesOpen((open) => !open)}>{text.sources}</button>
      <button className="monkeyboard-primary" disabled={!ready || busy || saveState.conflict} onClick={() => input.current?.click()}>{text.upload}</button>
      <details ref={actions} className="monkeyboard-actions" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) closeActions(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeActions(); actions.current?.querySelector("summary")?.focus(); } }}>
        <summary>{boardText.more}</summary>
        <div className="monkeyboard-actions-panel" role="group" aria-label={boardText.more}>
          <button disabled={!ready} onClick={() => canvas.current?.scrollToContent(undefined, { fitToContent: true, animate: false })}>{text.fit}</button>
          <button disabled={!ready || busy || saveState.conflict || feedbackWaiting} onClick={openFeedback}>{feedbackWaiting ? text.busy : feedbackCopy[language].action}</button>
          <button disabled={!ready} onClick={enterCrit}>{text.crit}</button>
          <button type="button" disabled={!ready || busy || saveState.conflict || !hasAnnotations} onClick={clearAnnotations} title={text.clearAnnotationsHint}>{text.clearAnnotations}</button>
          <button disabled={!ready || busy || saveState.conflict} onClick={() => { closeActions(); void queue.flush().catch(() => {}); }}>{text.save}</button>
          <div className="monkeyboard-export" aria-label={text.export}>
            <span>{text.exportClean}</span>
            <select aria-label={text.export} value={exportFormat} disabled={!ready || exporting} onChange={(event) => setExportFormat(event.target.value as typeof exportFormat)}>
              <option value="merged-pdf">{text.exportMerged}</option><option value="page-pdfs">{text.exportPages}</option><option value="png">{text.exportPng}</option><option value="jpeg">{text.exportJpeg}</option>
            </select>
            <label><input type="checkbox" checked={exportZip} disabled={!ready || exporting} onChange={(event) => setExportZip(event.target.checked)} /> {text.exportZip}</label>
            <button disabled={!ready || exporting} onClick={() => { void exportBoard(); }}>{exporting ? text.exporting : text.export}</button>
          </div>
        </div>
      </details>
      <input ref={input} type="file" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg" multiple hidden onChange={(event) => { void upload([...event.target.files ?? []]); event.target.value = ""; }} />
    </header>
    {saveState.error !== null && <div className="monkeyboard-alert" role="alert"><span>{saveState.conflict ? text.conflict : `${text.saveError} ${errorText(saveState.error)}`}</span>{saveState.conflict ? <a href={window.location.href} target="_blank" rel="noopener noreferrer">{text.compare}</a> : <button onClick={() => { void queue.retry().catch(() => {}); }}>{text.retry}</button>}</div>}
    {(previewFailed || sourceError) && <div className="monkeyboard-alert" role="alert"><span>{previewFailed ? text.previewError : `${text.sourceError} ${sourceError}`}</span><button onClick={retryVisuals} disabled={busy}>{text.refresh}</button></div>}
    {notice && <div className="monkeyboard-alert" role="alert"><span>{notice}</span><button onClick={() => setNotice("")} aria-label={text.dismiss}>×</button></div>}
    <div className="monkeyboard-body">
      <aside id="monkeyboard-project-documents" className="monkeyboard-sources" aria-label={text.sources} hidden={!sourcesOpen}>
        <div className="monkeyboard-source-heading"><h2>{text.sources} · {documents.length}</h2><button aria-label={boardText.hideSources} onClick={() => { setSourcesOpen(false); root.current?.closest(".monkeyboard")?.querySelector<HTMLButtonElement>("[aria-controls=monkeyboard-project-documents]")?.focus(); }}>×</button></div>
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
            <button className="monkeyboard-source-update" disabled={!ready || busy || saveState.conflict} onClick={() => { replacementOpen.current = true; setReplacement({ document, pageIndex: page }); }}>{replacementCopy[language].action}</button>
          </article>;
        })}</div>
      </aside>
      <div className="monkeyboard-canvas" ref={root} onPasteCapture={onPasteCapture} onDropCapture={onDropCapture} onDragOverCapture={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.stopPropagation(); } }} onKeyDownCapture={(event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); event.stopPropagation(); void queue.flush().catch(() => {}); } }}>
        <Excalidraw initialData={initialData} excalidrawAPI={(api) => { canvas.current = api; }} langCode={language} theme={resolvedTheme} name={title} aiEnabled={false} validateEmbeddable={false} autoFocus handleKeyboardGlobally={false} zenModeEnabled={critMode} UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: false }, tools: { image: false } }}
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
            setHasAnnotations(elements.some(isAnnotation));
            updateContext(elements, appState);
            capture(elements);
          }}>
          <MainMenu><MainMenu.Item onSelect={() => input.current?.click()}>{text.upload}</MainMenu.Item><MainMenu.Item onSelect={() => { void queue.flush().catch(() => {}); }}>{text.save}</MainMenu.Item><MainMenu.DefaultItems.ClearCanvas /></MainMenu>
          <WelcomeScreen><WelcomeScreen.Center><WelcomeScreen.Center.Heading>{boardText.welcome}</WelcomeScreen.Center.Heading><div className="monkeyboard-welcome-body"><p>{boardText.gestures}</p><span className="monkeyboard-welcome-example">{boardText.example}</span><p>{boardText.designHint}</p></div><WelcomeScreen.Center.Menu><WelcomeScreen.Center.MenuItem onSelect={() => input.current?.click()}>{text.upload}</WelcomeScreen.Center.MenuItem></WelcomeScreen.Center.Menu></WelcomeScreen.Center></WelcomeScreen>
        </Excalidraw>
        {!ready && <div className="monkeyboard-initializing" role="status">{text.loading}</div>}
        {busy && <div className="monkeyboard-busy" role="status">{text.busy}</div>}
        {!critMode && context && <div className="monkeyboard-context" role="group" aria-label={feedbackCopy[language].action}>
          {context.reason && <p id="monkeyboard-context-hint" role="status">{context.reason === "modelRequired" ? feedbackCopy[language].modelRequired : boardText[context.reason]}</p>}
          {context.reason === "modelRequired" && context.source
            ? <a href={documentUrl(window.location.href, context.source)} target="_blank" rel="noopener noreferrer">{boardText.linkModel} ↗</a>
            : <button className="monkeyboard-primary" disabled={!!context.reason || !ready || busy || saveState.conflict || feedbackWaiting} aria-describedby={context.reason ? "monkeyboard-context-hint" : undefined} onClick={openFeedback}>{feedbackWaiting ? text.busy : feedbackCopy[language].action}</button>}
        </div>}
        {critMode && <div className="monkeyboard-crit-actions" role="group" aria-label={text.crit}>
          <button type="button" className="monkeyboard-primary" disabled={!ready || saveState.conflict || feedbackWaiting} onClick={openFeedback}>{feedbackWaiting ? text.busy : text.critSubmit}</button>
          <button type="button" disabled={!ready || busy || saveState.conflict || !hasAnnotations} onClick={clearAnnotations} title={text.clearAnnotationsHint}>{text.clearAnnotations}</button>
          <button type="button" onClick={exitCrit}>{text.critExit}</button>
        </div>}
      </div>
    </div>
    <footer className="monkeyboard-footer"><span>{text.hint}</span>{source && selected ? <a href={documentUrl(window.location.href, selected)} target="_blank" rel="noopener noreferrer">{source.fileName} · {selected.pageIndex + 1}/{source.pageCount} · {text.open} ↗</a> : <span>{text.select}</span>}</footer>
    {feedback && <FeedbackDialog selection={feedback} language={language} returnFocus={feedbackReturnFocus.current} onCancel={() => { feedbackOpen.current = false; setFeedback(null); }} onSubmit={submitFeedback} />}
    {replacement && <ReplacementDialog target={replacement} language={language} onCancel={() => { replacementOpen.current = false; setReplacement(null); }} onSubmit={replacePage} />}
  </section>;
}
