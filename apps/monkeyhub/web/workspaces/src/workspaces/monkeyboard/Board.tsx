import { useCallback, useEffect, useRef, useState, type ClipboardEvent as ReactClipboardEvent, type DragEvent } from "react";
import { CaptureUpdateAction, convertToExcalidrawElements, MainMenu, newElementWith, WelcomeScreen } from "@excalidraw/excalidraw";
import type { ExcalidrawElement, FileId } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles, DataURL, ExcalidrawImperativeAPI, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";

import { useStudio } from "../../api/ProjectRuntimeContext";
import type { StudioClient } from "../../api/client";
import type { BoardDto, FrameLevelDto, SourceDocumentDto } from "../../api/generated";
import { CANVAS_APP_STATE, PROJECT_CANVAS_CLASS, ProjectCanvas, useScenePointer, useWheelZoom } from "../../features/canvas/ProjectCanvas";
import { usePreferences } from "../../features/settings/preferences";
import { renderDocumentVisual } from "../monkeydiagram/documentVisualInput";
import { createBoardSaveQueue, type BoardSaveState } from "./boardSaveQueue";
import { BoardFeedbackError, prepareBoardDesignRequest, type BoardDesignRequest } from "./boardFeedback";
import { BoardFeedbackGeometryError, createBoardFeedback, type BoardFeedbackSelection } from "./boardFeedbackGeometry";
import { boardViewAppState, captureBoardView, pageSourceAt, type BoardDocumentOpen, type BoardViewState } from "./boardNavigation";
import { boardDocumentFrameName, documentKey, documentMime, findSource, imageSource, isTracingPaperReview, nextDocumentPosition, pageKey, pageReplacements, pageSource, selectedPageSource, type BoardDraft, type PageSource } from "./boardScene";
import { BoardSketchError, calibrateSketchFrame, insideSketchFrame, newSketchFrameData, sketchActionsFromFrame, sketchFrameData, sketchFrameIds, sketchSummary, type BoardSketchRequest, type SketchFrameData, type SketchSkipReason } from "./boardSketch";
import "./board.css";

const copy = {
  en: { loading: "Opening board…", loadFailed: "The board could not be opened.", retry: "Retry", sources: "Project documents", upload: "Upload PDF / image", title: "Board title", saved: "Saved", saving: "Saving…", dirty: "Unsaved changes", saveError: "Changes have not been saved.", conflict: "Another saved version exists. Your current canvas is preserved; these changes have not overwritten the saved board.", add: "Add page", open: "Open in MonkeyDiagram", openPage: "Edit this page", openHint: "Double-click a drawing to edit its page", fit: "Fit board", busy: "Receiving document…", clearAnnotations: "Clear annotations", clearAnnotationsHint: "Clear all drawn marks and text; keep drawings and frames. Ctrl+Z to undo.", crit: "Crit mode", critSubmit: "Submit", critExit: "Exit", export: "Export board pages", exportClean: "Clean originals · marks excluded", exportMerged: "Merged PDF", exportPages: "One PDF per page", exportPng: "PNG", exportJpeg: "JPEG", exportZip: "ZIP for transfer", exporting: "Preparing export…", exportDone: "Export ready.", exportEmpty: "Place at least one registered drawing page on the board before exporting.", empty: "Upload a PDF, PNG or JPEG to begin. New project drawings will appear here.", hint: "Wheel to zoom · Space or middle mouse to pan · Shift to select several", auto: "New documents arrive automatically", previewError: "Some page previews could not be loaded. The saved layout is retained.", unsupported: "Use PDF, PNG or JPEG files.", unbound: "This image has no registered project source. Upload its original file first.", page: "Page", pages: "pages", received: "Received", pending: "Pending", dismiss: "Dismiss", sourceError: "Project documents could not be refreshed.", select: "Select a drawing to open its original.", refresh: "Retry previews / receive", unknown: "Unknown error" },
  "zh-CN": { loading: "正在打开画布…", loadFailed: "画布暂时无法打开。", retry: "重试", sources: "项目资料", upload: "上传 PDF / 图片", title: "画布标题", saved: "已保存", saving: "正在保存…", dirty: "有未保存的修改", saveError: "修改尚未保存。", conflict: "已有另一份保存版本。当前画布已保留，这些修改没有覆盖已保存版本。", add: "添加此页", open: "在 MonkeyDiagram 中打开", openPage: "编辑此页", openHint: "双击图纸即可编辑该页", fit: "查看全部", busy: "正在接收资料…", clearAnnotations: "清除批注", clearAnnotationsHint: "清除全部圈线、箭头和文字，保留图纸与图框；可用 Ctrl+Z 撤销。", crit: "Crit 模式", critSubmit: "提交", critExit: "退出", export: "整理导出图墙图纸", exportClean: "清洁原图 · 不含批注", exportMerged: "合并 PDF", exportPages: "单页 PDF", exportPng: "PNG", exportJpeg: "JPEG", exportZip: "传输 ZIP", exporting: "正在整理导出…", exportDone: "导出已就绪。", exportEmpty: "请先在图墙中摆放至少一页已登记图纸。", empty: "上传 PDF、PNG 或 JPEG 开始。项目的新图纸也会自动出现在这里。", hint: "滚轮缩放 · 空格或鼠标中键平移 · Shift 多选", auto: "自动接收新资料", previewError: "部分页面预览未能载入，已保留原有布局。", unsupported: "请使用 PDF、PNG 或 JPEG 文件。", unbound: "这张图片没有项目来源，请先上传原始文件。", page: "第", pages: "页", received: "已接收", pending: "待接收", dismiss: "关闭提示", sourceError: "项目资料暂时无法刷新。", select: "选中图纸可打开原始页面。", refresh: "重试预览 / 接收", unknown: "未知错误" },
};
type Copy = typeof copy.en;

const selectionCopy = {
  en: { selected: "Selected drawing", linked: "Model linked", unlinked: "Drawing only" },
  "zh-CN": { selected: "选中图纸", linked: "已关联模型", unlinked: "尚未关联模型" },
};

const whiteboardCopy = {
  en: { more: "More board actions", hideSources: "Hide project documents", welcome: "Drop a drawing or image here", gestures: "Circle, draw an arrow, or type a note.", example: "Drawing + arrow + note", designHint: "Select a project drawing and your marks to discover design feedback.", oneSource: "Mark the drawing you want changed inside its own frame. Other selected drawings travel as reference only.", connectReference: "Draw an arrow between the edit drawing and each extra drawing, and select it too, to send them as reference.", referenceMarks: "Marks on a reference drawing cannot be sent. Select that reference's image on its own.", stale: "This drawing is no longer available. Select its current page from project documents.", outside: "Move the selected marks fully onto the drawing before sending.", unsupported: "This selection cannot be sent yet. Use solid outline marks on an uncropped drawing.", invalid: "A selected object has invalid geometry. Redraw it before sending.", linkModel: "Link model in MonkeyDiagram" },
  "zh-CN": { more: "更多画板操作", hideSources: "收起项目资料", welcome: "拖入图纸或图片开始", gestures: "圈画、画箭头，或直接写下想法。", example: "图纸 + 箭头 + 文字", designHint: "选中项目图纸与圈线，即可查看设计反馈入口。", oneSource: "请在要修改的那张图纸的图框内画出标记；同时选中的其他图纸只作参考。", connectReference: "在主改图纸与每张附加图纸之间画一根连线并一并选中，它们才会作为参考发送。", referenceMarks: "参考图纸上的标记无法一起发送，请只选中该参考图片本身。", stale: "这张图纸已不可用，请从项目资料重新选择当前图页。", outside: "请先将选中圈线完整移入图纸范围。", unsupported: "此选区暂时无法发送，请使用实线轮廓标记及未裁切的图纸。", invalid: "选中对象的几何无效，请重新绘制后发送。", linkModel: "在 MonkeyDiagram 中关联模型" },
};

type FeedbackContext = { source: PageSource | null; reason: "modelRequired" | "oneSource" | "connectReference" | "referenceMarks" | "stale" | "outside" | "unsupported" | "invalid" | null };

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
      case "BOARD_FEEDBACK_REFERENCE_UNLINKED": return { source: null, reason: "connectReference" };
      case "BOARD_FEEDBACK_REFERENCE_MARKED": return { source: null, reason: "referenceMarks" };
      case "BOARD_FEEDBACK_SOURCE_UNAVAILABLE": return { source: null, reason: "stale" };
      case "BOARD_FEEDBACK_OUTSIDE_PAGE": return { source: null, reason: "outside" };
      case "BOARD_FEEDBACK_INVALID_GEOMETRY": return { source: null, reason: "invalid" };
      default: return { source: null, reason: "unsupported" };
    }
  }
}

// A shape drawn inside a sketch frame is the architect's own geometry, not a
// mark on someone else's drawing, so clearing marks never reaches into one.
function isAnnotation(element: ExcalidrawElement, sketchFrames: ReadonlySet<string>): boolean {
  return !element.isDeleted && !insideSketchFrame(element, sketchFrames)
    && ["freedraw", "line", "arrow", "rectangle", "ellipse", "diamond", "text"].includes(element.type);
}

const replacementCopy = {
  en: { action: "Update this page", file: "Updated PDF / image", page: "Page number in the new file", hint: "Replace this page wherever it is placed on the board. Keep its position, scale and marks. The new page must have the same aspect ratio; the original remains in project documents.", cancel: "Cancel", submit: "Update in place", sending: "Updating page…",
    updated: "«{name}» updated", updatedMore: "«{name}» updated · {count} more updated", view: "View",
    workCopy: "Get editable copy", workCopyHint: "Editable copy" },
  "zh-CN": { action: "更新此页原图", file: "更新后的 PDF / 图片", page: "新文件中的页码", hint: "更新图墙中此页的所有副本，保留位置、缩放与批注。新页须保持相同宽高比；旧原图仍保存在项目资料中。", cancel: "取消", submit: "原位更新", sending: "正在更新…",
    updated: "«{name}» 已更新", updatedMore: "«{name}» 已更新 · 另有 {count} 页已更新", view: "查看",
    workCopy: "获取可编辑副本", workCopyHint: "可编辑副本" },
};

const sketchCopy = {
  en: { newFrame: "New sketch frame", frameName: "Sketch", panel: "Sketch frame", level: "Level", storeyHeight: "Storey height (m)",
    scale: "Scale", uncalibrated: "Not calibrated — select one straight line inside the frame and enter its real length.",
    calibrated: "1 m = {units} board units · frame {w} × {h} m", metres: "Known length (m)", calibrate: "Set scale",
    send: "Send to 3D", sending: "Sending sketch…", empty: "Draw at least one closed shape inside the frame.",
    tooMany: "Too many shapes in this frame to send at once. Split them across two sketch frames.",
    skipped: "{count} object(s) were not sent and stay on the board: {reasons}.", noLevels: "The project has no levels yet; the ground level will be created on first send." },
  "zh-CN": { newFrame: "新建草图框", frameName: "草图", panel: "草图框", level: "楼层", storeyHeight: "层高（米）",
    scale: "比例", uncalibrated: "尚未标定——选中框内一条直线，输入它的真实长度。",
    calibrated: "1 米 = {units} 画板单位 · 框 {w} × {h} 米", metres: "已知长度（米）", calibrate: "设定比例",
    send: "起模到 3D", sending: "正在发送草图…", empty: "请先在框内画至少一个闭合形状。",
    tooMany: "框内形状太多，无法一次发送；请分成两个草图框。",
    skipped: "{count} 个对象未发送，留在画板上：{reasons}。", noLevels: "项目还没有楼层；首次发送时会创建地面层。" },
};

// Why a shape stayed on the board, in the words that say what to do about it.
const sketchSkipCopy: Record<"en" | "zh-CN", Record<SketchSkipReason, string>> = {
  en: { open: "open strokes", unsupported: "arrows, text or images", degenerate: "shapes with no buildable area",
    tooManyPoints: "outlines with too many points", selfTouching: "outlines that touch themselves",
    selfIntersecting: "outlines that cross themselves", outsideFrame: "shapes no longer inside the frame",
    roundRotated: "rotated curved lines — set their edges to sharp" },
  "zh-CN": { open: "未闭合的线条", unsupported: "箭头、文字或图片", degenerate: "面积过小、无法起模的形状",
    tooManyPoints: "点数过多的轮廓", selfTouching: "自相接触的轮廓",
    selfIntersecting: "自相交叉的轮廓", outsideFrame: "已不在框内的形状",
    roundRotated: "旋转过的圆角线条——请将其边角改为直角" },
};

function skippedNotice(skipped: readonly { reason: SketchSkipReason }[], language: "en" | "zh-CN"): string {
  const reasons = [...new Set(skipped.map((row) => row.reason))].map((reason) => sketchSkipCopy[language][reason]);
  return sketchCopy[language].skipped.replace("{count}", String(skipped.length))
    .replace("{reasons}", reasons.join(language === "en" ? ", " : "、"));
}

/** The one sketch frame a panel can act on, and the dimension line selected with it. */
type SketchSelection = { frame: ExcalidrawElement; data: SketchFrameData; line: ExcalidrawElement | null };

// A frame is the panel's subject when it is selected itself or holds the selection.
function sketchSelectionOf(elements: readonly ExcalidrawElement[], selectedIds: AppState["selectedElementIds"]): SketchSelection | null {
  const selected = elements.filter((element) => !element.isDeleted && selectedIds[element.id]);
  if (selected.length === 0) return null;
  const frames = new Set<string>();
  for (const element of selected) {
    if (sketchFrameData(element) !== null) frames.add(element.id);
    else if (element.frameId) frames.add(element.frameId);
    else return null;
  }
  if (frames.size !== 1) return null;
  const frame = elements.find((element) => element.id === [...frames][0] && sketchFrameData(element) !== null);
  const data = frame && sketchFrameData(frame);
  if (!frame || !data) return null;
  const lines = selected.filter((element) => (element.type === "line" || element.type === "arrow")
    && element.frameId === frame.id && (element as { points?: readonly unknown[] }).points?.length === 2);
  return { frame, data, line: lines.length === 1 ? lines[0] : null };
}

/** One live update notice at a time: the newest page named, the rest counted. */
type BoardUpdateNotice = { fileName: string; others: number; sources: PageSource[] };
function updateNoticeText(notice: BoardUpdateNotice, language: "en" | "zh-CN"): string {
  const text = replacementCopy[language];
  return notice.others > 0
    ? text.updatedMore.replace("{name}", notice.fileName).replace("{count}", String(notice.others))
    : text.updated.replace("{name}", notice.fileName);
}

function ReplacementDialog({ target, language, returnFocus, onCancel, onSubmit, active }: {
  target: { document: SourceDocumentDto; pageIndex: number }; language: "en" | "zh-CN";
  returnFocus: HTMLElement | null;
  onCancel: () => void; onSubmit: (file: File, newPageIndex: number) => Promise<void>; active: boolean;
}) {
  const dialog = useRef<HTMLDialogElement | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [page, setPage] = useState(1);
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [error, setError] = useState("");
  const text = replacementCopy[language];
  useEffect(() => {
    const element = dialog.current;
    if (active) element?.showModal(); else element?.close();
    return () => { element?.close(); if (active && returnFocus?.isConnected) returnFocus.focus(); };
  }, [active]);
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
  en: { action: "Send design feedback", title: "Discuss this drawing", hint: "Select the drawing you want changed, or its frame, together with the marks and text to send.", description: "The selected marks will join this drawing page. Continue the discussion in MonkeyArch to review a proposal or answer a design question.", label: "What would you like to change?", placeholder: "Describe the change and what should stay as it is.", send: "Send to design", sending: "Preparing drawing…", cancel: "Cancel", marks: "selected marks", references: "Sent as visual reference only; this drawing stays the only page being changed:", textIncluded: "Selected text is included below. Send it as it is, or add what should stay unchanged.", modelRequired: "Link this drawing to its model in MonkeyDiagram before sending a design change.", sourceChanged: "This drawing's source changed. Close this dialog and select the drawing again.", referenceChanged: "A selected reference drawing changed. Close this dialog and select the references again.", modelChanged: "The drawing's linked model changed or cannot be opened for editing. Check its source in MonkeyDiagram.", empty: "Write the change you want to discuss.", failed: "The feedback could not be sent." },
  "zh-CN": { action: "发送设计反馈", title: "讨论这张图纸", hint: "选中要修改的那张图纸或它的图框，并同时选中要提交的圈线与文字。", description: "选中的圈线会加入对应图页。进入 MonkeyArch 后，可审阅修改提案或回答需要澄清的问题。", label: "希望怎样修改？", placeholder: "说明要调整的内容，以及需要保留的部分。", send: "发送到设计", sending: "正在准备图纸…", cancel: "取消", marks: "条选中标记", references: "以下图纸仅作视觉参考发送，被修改的仍只有上面这一页：", textIncluded: "已带入选中文字，可直接发送，或补充需要保留的内容。", modelRequired: "请先在 MonkeyDiagram 中关联这张图纸对应的模型，再提交设计修改。", sourceChanged: "这张图纸的来源已发生变化，请关闭此窗口并重新选择图纸。", referenceChanged: "选中的参考图纸已发生变化，请关闭此窗口并重新选择参考。", modelChanged: "图纸关联的模型已改变或暂时无法继续编辑，请在 MonkeyDiagram 中检查来源。", empty: "请写下希望讨论的修改。", failed: "意见暂时未能发送。" },
};

function feedbackError(error: unknown, language: "en" | "zh-CN"): string {
  const text = feedbackCopy[language];
  const code = error && typeof error === "object" && "code" in error ? error.code : null;
  if (code === "SOURCE_CHANGED") return text.sourceChanged;
  if (code === "REFERENCE_CHANGED") return text.referenceChanged;
  if (code === "MODEL_REQUIRED") return text.modelRequired;
  if (code === "MODEL_CHANGED") return text.modelChanged;
  if (code === "EMPTY_COMMENT") return text.empty;
  return errorText(error);
}

function FeedbackDialog({ selection, language, returnFocus, onCancel, onSubmit, onOpenDocument, active }: {
  onOpenDocument(source: PageSource): void; active: boolean;
  selection: BoardFeedbackSelection; language: "en" | "zh-CN";
  returnFocus: HTMLElement | null;
  onCancel: () => void; onSubmit: (comment: string) => Promise<void>;
}) {
  const dialog = useRef<HTMLDialogElement | null>(null);
  const [comment, setComment] = useState(selection.selectedText || (isTracingPaperReview(selection.document)
    ? language === "en" ? "Apply the changes shown in this Tracing Paper review." : "按这张 Tracing Paper review 中标出的意见修改。" : ""));
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [error, setError] = useState("");
  const text = feedbackCopy[language];
  useEffect(() => {
    const element = dialog.current;
    if (active) element?.showModal(); else element?.close();
    return () => { element?.close(); if (active && returnFocus?.isConnected) returnFocus.focus(); };
  }, [active]);
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
      {/* A group can exist only to retire earlier page ink, so count the ones carrying a mark. */}
      <p className="monkeyboard-feedback-source">{selection.document.fileName} · {selection.page.pageIndex + 1}/{selection.document.pageCount} · {
        selection.annotationGroups.filter((group) => selection.annotations.some((mark) => mark.id === group || mark.id.startsWith(`${group}:`))).length
      } {text.marks}</p>
      <p>{text.description}</p>
      {!!selection.references?.length && <p>{text.references}{" "}
        {selection.references.map((item) => `${item.document.fileName} · ${item.page.pageIndex + 1}/${item.document.pageCount}`).join(", ")}</p>}
      {selection.selectedText && <p>{text.textIncluded}</p>}
      <label htmlFor="monkeyboard-feedback-comment">{text.label}</label>
      <textarea id="monkeyboard-feedback-comment" value={comment} onChange={(event) => setComment(event.target.value)} placeholder={text.placeholder} autoFocus required rows={4} disabled={sending} />
      {!selection.document.modelSource && <p className="monkeyboard-feedback-error" role="alert">{text.modelRequired}</p>}
      {error && <p className="monkeyboard-feedback-error" role="alert">{text.failed} {error}</p>}
      <button type="button" disabled={sending} onClick={() => { onCancel(); onOpenDocument(selection.source); }}>{copy[language].open}</button>
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
  // A finished stroke's last committed point is live drawing state, which restore drops.
  return elements.map((element) => {
    const restored = element.boundElements === null ? { ...element, boundElements: [] } : element;
    return restored.type === "freedraw" && restored.lastCommittedPoint !== null
      ? { ...restored, lastCommittedPoint: null } : restored;
  }) as unknown as BoardDto["elements"];
}
function createPreviewLoader(studio: StudioClient) {
  const originals = new Map<string, Promise<File>>();
  const previews = new Map<string, Promise<Preview>>();
  const load = (document: SourceDocumentDto, pageIndex: number) => {
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
      // The page as its editor saved it. A page whose marks cannot be read is
      // still placed, showing the original rather than nothing. Both reads are
      // awaited together, so an original that fails before the marks arrive is
      // reported to this caller instead of escaping as an unhandled rejection.
      const [file, annotations] = await Promise.all([original, studio
        .documentAnnotations(document.runId, document.assetSha256, pageIndex, null, document.revisionRef ?? null)
        .then((saved) => saved.annotations, () => [])]);
      const visual = await renderDocumentVisual(file, page, annotations);
      return { dataURL: `data:image/png;base64,${visual.annotatedPngBase64 ?? visual.pagePngBase64}` as DataURL,
        width: visual.width, height: visual.height };
    })().catch((error) => { previews.delete(key); throw error; });
    previews.set(key, task);
    return task;
  };
  return Object.assign(load, { invalidate: () => previews.clear() });
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

export interface BoardPageRequest { source: PageSource; requestId: string }

export default function MonkeyBoard({ onSubmit, onSketch, onOpenDocument, onPublish, restoreView = null, active = true, refreshKey = 0, expectedProjectId, pageRequest = null }: {
  onSubmit: (request: BoardDesignRequest) => void;
  /** Hand one calibrated sketch frame to the App, which runs it as a sketch proposal. */
  onSketch: (request: BoardSketchRequest) => void;
  /** Hand one registered page to the existing document editor, with this place on the board. */
  onOpenDocument?: (open: BoardDocumentOpen) => void;
  onPublish?: (revision: string, ids: string[]) => void;
  /** The place a returning operator left, when this board is being reopened. */
  restoreView?: BoardViewState | null;
  active?: boolean;
  refreshKey?: number;
  expectedProjectId?: string;
  /** Explicitly place or focus exactly this page; ordinary discovery stays quiet. */
  pageRequest?: BoardPageRequest | null;
}) {
  const studio = useStudio();
  const { language } = usePreferences();
  const text = copy[language];
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ board: BoardDto; documents: SourceDocumentDto[]; files: BinaryFiles; failures: string[]; preview: PreviewLoader } | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    setError(null);
    const preview = createPreviewLoader(studio);
    void Promise.all([studio.board(), studio.documents()]).then(async ([board, list]) => {
      if (expectedProjectId !== undefined && board.projectId !== expectedProjectId) throw new Error("The board belongs to another project.");
      if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
      const visual = await sceneFiles(board, list.documents, preview);
      if (alive) setLoaded({ board, documents: list.documents, ...visual, preview });
    }).catch((cause) => { if (alive) setError(cause); });
    return () => { alive = false; };
  }, [attempt, expectedProjectId]);
  if (loaded) return <BoardCanvas {...loaded} onSubmit={onSubmit} onSketch={onSketch} onOpenDocument={onOpenDocument} onPublish={onPublish} restoreView={restoreView} active={active} refreshKey={refreshKey} pageRequest={pageRequest} />;
  return <section className="monkeyboard monkeyboard-loading" aria-live="polite">
    <strong>MonkeyBoard</strong>
    <p>{error === null ? text.loading : text.loadFailed}</p>
    {error !== null && <><p className="monkeyboard-error-detail">{errorText(error)}</p><button onClick={() => setAttempt((value) => value + 1)}>{text.retry}</button></>}
  </section>;
}

function BoardCanvas({ board, documents: initialDocuments, files, failures, preview, onSubmit, onSketch, onOpenDocument, onPublish, restoreView, active, refreshKey, pageRequest }: {
  board: BoardDto; documents: SourceDocumentDto[]; files: BinaryFiles; failures: string[]; preview: PreviewLoader;
  onSubmit: (request: BoardDesignRequest) => void;
  onSketch: (request: BoardSketchRequest) => void;
  onOpenDocument?: (open: BoardDocumentOpen) => void;
  onPublish?: (revision: string, ids: string[]) => void;
  restoreView: BoardViewState | null;
  active: boolean;
  refreshKey: number;
  pageRequest: BoardPageRequest | null;
}) {
  const studio = useStudio();
  const { language } = usePreferences();
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
  const replacementReturnFocus = useRef<HTMLElement | null>(null);
  const replacementOpen = useRef(false);
  const feedbackQueued = useRef(false);
  const [feedbackWaiting, setFeedbackWaiting] = useState(false);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [exportFormat, setExportFormat] = useState<"merged-pdf" | "page-pdfs" | "png" | "jpeg">("merged-pdf");
  const [exportZip, setExportZip] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState("");
  // A replacement this tab uploaded itself is never announced back to its author.
  const originated = useRef(new Set<string>());
  const [update, setUpdate] = useState<BoardUpdateNotice | null>(null);
  const pendingFocus = useRef<PageSource[] | null>(null);
  const focusFrame = useRef<number | null>(null);
  const refreshedPreviews = useRef(new Set<string>());
  const [previewFailed, setPreviewFailed] = useState(failures.length > 0);
  const [sourceError, setSourceError] = useState("");
  const [sketchSelection, setSketchSelection] = useState<SketchSelection | null>(null);
  const sketchKey = useRef("");
  const [sketchLevels, setSketchLevels] = useState<FrameLevelDto[] | null>(null);
  const [sketchMetres, setSketchMetres] = useState("");
  // What the box shows while it is being typed in. It is written back only on
  // blur, and anything unusable there reverts to the height the frame carries,
  // so the panel never shows a number that would not be sent.
  const [sketchHeight, setSketchHeight] = useState("");
  const [sketchSending, setSketchSending] = useState(false);
  const announceUpdates = useCallback((sources: PageSource[], next: SourceDocumentDto[]) => {
    const arrived = [...new Map(sources.map((source) => [pageKey(source), source])).values()].flatMap((source) => {
      const document = findSource(next, source);
      return document && !originated.current.has(documentKey(document))
        ? [{ source, document, order: next.indexOf(document) }] : [];
    });
    if (arrived.length === 0 || !alive.current) return;
    const newest = arrived.reduce((best, item) => {
      const left = item.document.generatedAt ?? "", right = best.document.generatedAt ?? "";
      return left > right || (left === right && item.order > best.order) ? item : best;
    });
    const sourcesToShow = [newest.source, ...arrived.filter((item) => item !== newest).map((item) => item.source)];
    pendingFocus.current = sourcesToShow;
    setUpdate({ fileName: newest.document.fileName, others: arrived.length - 1, sources: sourcesToShow });
  }, []);
  const [saveState, setSaveState] = useState<BoardSaveState>({ dirty: false, saving: false, error: null, conflict: false, revisionSha256: board.revisionSha256 });
  const [queue] = useState(() => createBoardSaveQueue(board, studio.saveBoard, (state) => {
    if (alive.current) setSaveState(state);
  }, 700, {
    readLatest: async () => {
      const latest = await studio.board();
      if (latest.projectId !== board.projectId) throw new Error("The refreshed board belongs to another project.");
      if (latest.revisionSha256 !== queue.getState().revisionSha256) {
        const list = await studio.documents();
        if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
        const restored = await sceneFiles(latest, list.documents, preview);
        // A saved change to which page an image shows is adopted only once that
        // page can actually be drawn. While its bytes are unreadable the board
        // keeps the image, source and marks it already shows; the existing Retry
        // previews entry rebases it, and announces it, when the read succeeds.
        // A page whose source did not change, and a deleted historical copy,
        // never hold back an ordinary title or ink refresh.
        const api = canvas.current;
        const live = new Map(api?.getSceneElementsIncludingDeleted().map((element) => [String(element.id), element]));
        const unreadable = api !== null && latest.elements.some((element) => {
          if (element.type !== "image" || element.isDeleted) return false;
          const source = imageSource(element);
          const current = live.get(String(element.id));
          const shown = current && !current.isDeleted && imageSource(current);
          if (!source || (shown && pageKey(shown) === pageKey(source))) return false;
          return !restored.files[String(element.fileId)];
        });
        if (unreadable) {
          if (alive.current) setPreviewFailed(true);
          throw new Error(textRef.current.previewError);
        }
        if (alive.current) {
          refreshedPreviews.current = new Set(latest.elements.flatMap((element) => {
            const source = imageSource(element);
            return source && restored.files[String(element.fileId)] ? [pageKey(source)] : [];
          }));
          documentsRef.current = list.documents; setDocuments(list.documents);
          canvas.current?.addFiles(Object.values(restored.files));
          setPreviewFailed(restored.failures.length > 0);
        }
      }
      return latest;
    },
    onRebase: (draft) => {
      titleRef.current = draft.title; setTitle(draft.title);
      seen.current = new Set(draft.seenDocuments);
      const api = canvas.current;
      const live = new Map(api?.getSceneElementsIncludingDeleted().map((element) => [element.id, element]));
      // An in-progress stroke keeps mutating its live element. Replacing that
      // unchanged object with a clone would cut off the rest of the gesture.
      const elements = draft.elements.map((element) => {
        const current = live.get(String(element.id));
        return current && JSON.stringify(records([current])[0]) === JSON.stringify(element) ? current : element;
      });
      api?.updateScene({ elements: elements as ExcalidrawElement[], captureUpdate: CaptureUpdateAction.NEVER });
      // Another Board can already have saved the replacement. In that case
      // receive() has nothing left to swap, but this canvas still received new
      // reviewable bytes. Include remotely added pages, which may be far outside
      // the current viewport, only after their previews and safe merge succeed.
      announceUpdates(elements.flatMap((element) => {
        const previous = live.get(String(element.id));
        const before = previous && !previous.isDeleted && imageSource(previous);
        const after = !element.isDeleted && imageSource(element);
        return after && (!before || pageKey(before) !== pageKey(after)) && refreshedPreviews.current.has(pageKey(after))
          ? [after] : [];
      }), documentsRef.current);
    },
  }));
  const work = useRef(Promise.resolve());
  const [initialData] = useState<ExcalidrawInitialDataState>(() => {
    const restored = restoreView === null ? null : boardViewAppState(restoreView, board.elements);
    return {
      elements: board.elements as unknown as ExcalidrawElement[], files, scrollToContent: restored === null,
      appState: { ...CANVAS_APP_STATE,
        ...(restored === null ? {} : { ...restored, zoom: { value: restored.zoom.value as AppState["zoom"]["value"] } }) },
    };
  });
  const updateContext = (elements: readonly ExcalidrawElement[], appState: AppState) => {
    const next = appState.editingTextElement || appState.newElement || appState.selectionElement || appState.isResizing || appState.isRotating
      ? null : feedbackContext(elements, appState.selectedElementIds, documentsRef.current);
    setContext((previous) => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    // The panel follows the selected sketch frame; only what it shows is compared,
    // so dragging a shape inside the frame does not re-render it on every pointer move.
    const sketch = sketchSelectionOf(elements, appState.selectedElementIds);
    const key = sketch === null ? ""
      : `${sketch.frame.id}|${Math.round(sketch.frame.width)}x${Math.round(sketch.frame.height)}|${JSON.stringify(sketch.data)}|${sketch.line?.id ?? ""}`;
    if (key === sketchKey.current) return;
    sketchKey.current = key;
    setSketchSelection(sketch);
  };
  useEffect(() => {
    const api = canvas.current;
    if (api) updateContext(api.getSceneElements(), api.getAppState());
  }, [documents]);
  useEffect(() => {
    setSketchHeight(sketchSelection === null ? "" : String(sketchSelection.data.storeyHeight));
  }, [sketchSelection?.frame.id, sketchSelection?.data.storeyHeight]);
  useEffect(() => {
    if (sketchSelection === null || sketchLevels !== null) return;
    let live = true;
    // An unmodelled project has no frame yet; the panel then offers the frame's
    // own level alone and says the ground level is created on first send.
    void studio.frame().then((frame) => { if (live) setSketchLevels(frame.levels); }, () => { if (live) setSketchLevels([]); });
    return () => { live = false; };
  }, [sketchSelection, sketchLevels]);
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
      { type: "frame", children: [imageId], name: boardDocumentFrameName(document, pageIndex) },
    ], { regenerateIds: false });
    api.addFiles([{ id: fileId, dataURL: rendered.dataURL, mimeType: "image/png", created: Date.now() }]);
    seen.current.add(documentKey(document));
    const elements = [...api.getSceneElementsIncludingDeleted(), ...additions];
    api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    capture(elements);
    if (!automatic) api.scrollToContent(additions, { fitToContent: true, animate: false });
  }, [capture, preview, queue]);
  const acceptDocuments = useCallback((next: SourceDocumentDto[]) => {
    documentsRef.current = next;
    setDocuments(next);
  }, []);
  const receive = useCallback(async (next: SourceDocumentDto[]) => {
    const received: PageSource[] = [];
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
      const applied = new Set<string>();
      const elements = api.getSceneElementsIncludingDeleted().map((element) => {
        if (element.type !== "image" || element.isDeleted) return element;
        const source = imageSource(element);
        const target = source && replacements.get(pageKey(source));
        const ready = target && rendered.get(pageKey(target));
        if (!ready) return element;
        applied.add(pageKey(target));
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
      received.push(...[...applied].flatMap((key) => rendered.get(key)?.source ?? []));
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
        received.push(pageSource(latest, target?.pageIndex ?? document.pages[0].pageIndex));
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
    // One received batch owns one focus request; ordinary discovery stays quiet.
    announceUpdates(received, next);
  }, [addPage, announceUpdates, capture, preview, queue]);
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
      originated.current.add(documentKey(updated));
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
    if (!ready || !active) return;
    let live = true;
    let refreshing = false;
    const refresh = async () => {
      if (!live || refreshing || document.hidden || feedbackOpen.current || replacementOpen.current) return;
      refreshing = true;
      try {
        await serial(async () => {
          try {
            await queue.refresh();
            const list = await studio.documents();
            if (!live) return;
            if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
            acceptDocuments(list.documents); setSourceError("");
            await receive(list.documents);
          } catch (error) { if (live) setSourceError(errorText(error)); }
        }, false);
      } catch (error) { if (live) setSourceError(errorText(error)); }
      finally { refreshing = false; }
    };
    void serial(() => receive(documentsRef.current));
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    window.addEventListener("focus", refresh);
    return () => { live = false; window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [acceptDocuments, board.projectId, ready, receive, serial, active]);
  useWheelZoom(root, canvas);
  // Leaving for a page is still a board edit: the canvas is saved first, and a
  // refused save keeps the operator here with their marks rather than losing them.
  const openDocumentRef = useRef(onOpenDocument); openDocumentRef.current = onOpenDocument;
  const openDocument = useCallback((source: PageSource) => {
    const api = canvas.current;
    if (!api || busyRef.current) return;
    const view = captureBoardView(api.getAppState());
    busyRef.current = true; setBusy(true);
    void (async () => {
      try {
        await work.current;
        await queue.flush();
        if (alive.current) openDocumentRef.current?.({ source, view });
      } catch (error) { if (alive.current) setNotice(errorText(error)); }
      finally { busyRef.current = false; if (alive.current) setBusy(false); }
    })();
  }, [queue]);
  useScenePointer(root, canvas, {
    onDoubleClick: (point, api) => {
      if (queue.getState().conflict) return false;
      const state = api.getAppState();
      if (state.editingTextElement || state.newElement || state.isResizing || state.isRotating) return false;
      const source = pageSourceAt(records(api.getSceneElements()), point);
      if (!source || !findSource(documentsRef.current, source)) return false;
      openDocument(source);
      // Handled: the canvas would otherwise start cropping the page under the pointer.
      return true;
    },
  }, Boolean(onOpenDocument) && ready);

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
    // A page whose saved source could not be drawn is still showing its previous
    // one, so the retained board rebases first; the scene is then reread from the
    // sources it actually carries.
    await queue.refresh();
    const list = await studio.documents();
    if (!alive.current) return;
    if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
    acceptDocuments(list.documents); setSourceError(""); skipped.current.clear();
    const restored = await sceneFiles({ ...board, elements: records(canvas.current?.getSceneElementsIncludingDeleted() ?? []) }, list.documents, preview);
    if (!alive.current) return;
    const api = canvas.current;
    if (api) {
      const present = api.getFiles();
      const replacements = new Map<string, FileId>();
      const updated = Object.values(restored.files).flatMap((file) => {
        if (present[file.id]?.dataURL === file.dataURL) return [];
        const id = crypto.randomUUID() as FileId;
        replacements.set(file.id, id);
        return [{ ...file, id }];
      });
      // Excalidraw files are immutable by id. Refresh only changed preview ids,
      // keeping the live page elements, exact sources, selection and viewport.
      api.addFiles(updated);
      if (replacements.size) api.updateScene({
        elements: api.getSceneElementsIncludingDeleted().map((element) => element.type === "image" && element.fileId && replacements.has(element.fileId)
          ? newElementWith(element, { fileId: replacements.get(element.fileId)! }) : element),
        captureUpdate: CaptureUpdateAction.NEVER,
      });
    }
    setPreviewFailed(restored.failures.length > 0);
    await receive(list.documents);
  }); };
  const wasActive = useRef(active);
  const lastRefresh = useRef(refreshKey);
  useEffect(() => {
    const returning = active && !wasActive.current;
    wasActive.current = active;
    if (ready && (returning || lastRefresh.current !== refreshKey)) {
      lastRefresh.current = refreshKey; preview.invalidate(); retryVisuals();
    }
  }, [active, ready, refreshKey]);
  const sendToPublish = async () => {
    const api = canvas.current;
    if (!api || !onPublish || !ready || busy || saveState.conflict) return;
    const selection = api.getAppState().selectedElementIds;
    const ids = [...api.getSceneElements()].filter((element) => selection[element.id] && !element.isDeleted)
      .sort((left, right) => left.y - right.y || left.x - right.x || left.id.localeCompare(right.id)).map((element) => element.id);
    if (!ids.length) { setNotice(language === "zh-CN" ? "请先选中图纸或图片。" : "Select drawing pages or images first."); return; }
    setExporting(true);
    try { await queue.flush(); const revision = queue.getState().revisionSha256;
      if (!revision) throw new Error("The Board must be saved before sending its selection.");
      onPublish(revision, ids);
    } catch (cause) { setNotice(errorText(cause)); }
    finally { if (alive.current) setExporting(false); }
  };
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
  // Remote delivery becomes visible once, after any current gesture or dialog.
  // Camera changes never enter the saved scene or clear the user's selection.
  const focusSources = useCallback((sources: PageSource[], select: boolean) => {
    const api = canvas.current;
    if (!api || !alive.current || !active || !ready || document.hidden || queue.getState().conflict || feedbackOpen.current || replacementOpen.current) return false;
    const state = api.getAppState();
    if (state.cursorButton === "down" || state.selectionElement || state.selectedElementsAreBeingDragged || state.newElement || state.editingTextElement || state.isResizing || state.isRotating) return false;
    const keys = new Set(sources.map(pageKey));
    const targets = api.getSceneElements().filter((element) => {
      const source = imageSource(element);
      return !!source && keys.has(pageKey(source));
    });
    if (targets.length === 0) return true;
    if (select) api.updateScene({ appState: { selectedElementIds: Object.fromEntries(targets.map((element) => [element.id, true])), selectedGroupIds: {} }, captureUpdate: CaptureUpdateAction.NEVER });
    api.scrollToContent(targets, { fitToContent: true, animate: false });
    return true;
  }, [active, ready, queue]);
  const handledPageRequest = useRef<string | null>(null);
  useEffect(() => {
    if (!active || !ready || !pageRequest || handledPageRequest.current === pageRequest.requestId) return;
    handledPageRequest.current = pageRequest.requestId;
    void serial(async () => {
      if (queue.getState().conflict) throw new Error(language === "zh-CN" ? "请先解决画板保存冲突，再重新发送这张图。" : "Resolve the board save conflict, then send this image again.");
      const list = await studio.documents();
      if (list.projectId !== board.projectId) throw new Error("The document list belongs to another project.");
      const source = findSource(list.documents, pageRequest.source);
      if (!source) throw new Error(language === "zh-CN" ? "这张精确来源的图页已不可用。" : "This exact source page is unavailable.");
      acceptDocuments(list.documents);
      await receive(list.documents);
      const placed = () => canvas.current?.getSceneElements().some((element) => {
        const ref = imageSource(element); return ref && pageKey(ref) === pageKey(pageRequest.source);
      });
      // A user-deleted page stays seen. Only this explicitly requested page may
      // be placed again, through the same original-document insertion path.
      if (!placed()) await addPage(source, pageRequest.source.pageIndex);
      if (!placed()) throw new Error(language === "zh-CN" ? "图页未能放入画板，请检查画板状态后重试。" : "The page could not be placed. Check the board state and try again.");
      pendingFocus.current = null;
      if (focusSources([pageRequest.source], true)) root.current?.querySelector<HTMLElement>(".excalidraw")?.focus();
      else setNotice(language === "zh-CN" ? "图页已在画板中；结束当前操作后可从项目文档定位。" : "The page is on the board. Finish the current interaction to view it from project documents.");
    });
  }, [active, ready, pageRequest, serial, studio, board.projectId, queue, acceptDocuments, receive, addPage, focusSources, language]);
  const scheduleUpdateFocus = useCallback(() => {
    if (!pendingFocus.current || focusFrame.current !== null) return;
    focusFrame.current = window.requestAnimationFrame(() => {
      focusFrame.current = null;
      const sources = pendingFocus.current;
      // Clear before scrolling: its onChange must not queue the same focus again.
      pendingFocus.current = null;
      if (sources && !focusSources(sources, false)) pendingFocus.current = sources;
    });
  }, [focusSources]);
  useEffect(() => {
    scheduleUpdateFocus();
    const visible = () => scheduleUpdateFocus();
    document.addEventListener("visibilitychange", visible);
    return () => {
      document.removeEventListener("visibilitychange", visible);
      if (focusFrame.current !== null) window.cancelAnimationFrame(focusFrame.current);
      focusFrame.current = null;
    };
  }, [update, feedback, replacement, scheduleUpdateFocus]);
  const focusUpdate = () => {
    if (update && focusSources(update.sources, true)) pendingFocus.current = null;
  };
  // The entry point of the edit loop: an explicit editable copy of one registered
  // document, reported by its project-relative path. A document whose pages were
  // replaced individually has no single file to stand for it; that refusal
  // arrives from the document owner and is shown as it is.
  const requestWorkCopy = (document: SourceDocumentDto) => serial(async () => {
    const workCopy = await studio.createDocumentWorkCopy(board.projectId, document.runId, document.assetSha256, document.revisionRef ?? null);
    if (!alive.current) return;
    setNotice(`${replacementCopy[language].workCopyHint}: ${workCopy.relativePath}`);
  });
  const source = selected && findSource(documents, selected);
  const contextSource = context?.source ?? selected;
  const contextDocument = contextSource && findSource(documents, contextSource);
  const openReplacement = (document: SourceDocumentDto, pageIndex: number) => {
    replacementReturnFocus.current = window.document.activeElement instanceof HTMLElement ? window.document.activeElement : null;
    replacementOpen.current = true; setReplacement({ document, pageIndex });
  };
  const openSelectedReplacement = () => {
    const api = canvas.current;
    // Opening this picker is read-only. A silent source refresh must not
    // swallow an enabled click; upload still joins the serial work queue.
    if (!api || !ready || busy || queue.getState().conflict) return;
    const current = selectedPageSource(records(api.getSceneElements()), api.getAppState().selectedElementIds);
    const document = current && findSource(documentsRef.current, current);
    if (document && current) openReplacement(document, current.pageIndex);
  };
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
    const sketchFrames = sketchFrameIds(current);
    const removed = new Set(current.filter((element) => isAnnotation(element, sketchFrames)).map((element) => element.id));
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
  // One sketch frame carries the only scene-to-metres affine there is. It is an
  // ordinary Excalidraw frame, so it is saved, undone and versioned like the
  // rest of the board; nothing about it is kept outside the scene.
  const newSketchFrame = () => serial(async () => {
    closeActions();
    const api = canvas.current;
    if (!api || !ready) return;
    let levelId = "ground";
    try { const frame = await studio.frame(); levelId = frame.levels[0]?.levelId ?? "ground"; }
    catch { /* an unmodelled project: the App prepares it on first send */ }
    if (!alive.current || !canvas.current) return;
    const position = nextDocumentPosition(records(api.getSceneElements()));
    const count = api.getSceneElements().filter((element) => sketchFrameData(element) !== null).length + 1;
    const id = crypto.randomUUID();
    const additions = convertToExcalidrawElements([{ type: "frame", id, children: [], ...position, width: 800, height: 600,
      name: `${sketchCopy[language].frameName} ${count}`, customData: { sketch: newSketchFrameData(levelId) } }], { regenerateIds: false });
    const elements = [...api.getSceneElementsIncludingDeleted(), ...additions];
    api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    api.updateScene({ appState: { selectedElementIds: { [id]: true } }, captureUpdate: CaptureUpdateAction.NEVER });
    capture(elements);
    api.scrollToContent(additions, { fitToContent: true, animate: false });
  });
  // Every panel edit is written back through Excalidraw, so it is saved with the
  // board and undone with Ctrl+Z like any other change to the drawing.
  const updateSketchFrame = (frame: ExcalidrawElement, data: SketchFrameData) => {
    const api = canvas.current;
    if (!api || !ready || queue.getState().conflict) return;
    const elements = api.getSceneElementsIncludingDeleted().map((element) => element.id === frame.id
      ? newElementWith(element, { customData: { ...element.customData, sketch: data } }) : element);
    api.updateScene({ elements, captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    capture(elements);
  };
  const calibrateSketch = () => {
    const selection = sketchSelection;
    if (!selection?.line) return;
    try {
      updateSketchFrame(selection.frame, calibrateSketchFrame(selection.data, selection.line, Number(sketchMetres)));
      setSketchMetres("");
    } catch (cause) { setNotice(errorText(cause)); }
  };
  const sendSketch = async () => {
    const frame = sketchSelection?.frame;
    if (!frame || sketchSending) return;
    busyRef.current = true; setSketchSending(true);
    try {
      // The proposal cites the revision the board was saved at, so the marks it
      // was read from can always be found again.
      await queue.flush();
      const api = canvas.current;
      if (!api || !alive.current) return;
      const live = api.getSceneElements().find((element) => element.id === frame.id) ?? frame;
      const data = sketchFrameData(live);
      if (!data) throw new BoardSketchError("BOARD_SKETCH_FRAME_INVALID", sketchCopy[language].panel);
      const conversion = sketchActionsFromFrame(api.getSceneElements(), live);
      const revision = queue.getState().revisionSha256;
      const frameName = (live as { name?: string | null }).name ?? sketchCopy[language].frameName;
      if (conversion.skipped.length > 0) setNotice(skippedNotice(conversion.skipped, language));
      onSketch({ projectId: board.projectId, frameId: live.id, frameName, boardRevisionSha256: revision,
        levelId: data.levelId, sketches: conversion.sketches, skipped: conversion.skipped,
        summary: sketchSummary(frameName, conversion, data, revision) });
    } catch (error) {
      setNotice(error instanceof BoardSketchError
        ? (error.code === "BOARD_SKETCH_EMPTY" ? sketchCopy[language].empty
          : error.code === "BOARD_SKETCH_TOO_MANY" ? sketchCopy[language].tooMany : error.message)
        : errorText(error));
    } finally { busyRef.current = false; if (alive.current) setSketchSending(false); }
  };
  const exitCrit = useCallback(() => {
    const api = canvas.current;
    if (api && previousTool.current) api.updateScene({ appState: { activeTool: previousTool.current }, captureUpdate: CaptureUpdateAction.NEVER });
    previousTool.current = null;
    setCritMode(false);
  }, []);
  useEffect(() => {
    if (!critMode || !active) return;
    const leave = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !feedbackOpen.current) { event.preventDefault(); exitCrit(); }
    };
    window.addEventListener("keydown", leave);
    return () => window.removeEventListener("keydown", leave);
  }, [critMode, exitCrit, active]);
  const submitFeedback = async (comment: string) => {
    if (!feedback) return;
    // Finish any in-flight canvas save before leaving this workspace.
    busyRef.current = true;
    try {
      await queue.flush();
      const api = canvas.current;
      const current = api && createBoardFeedback(api.getSceneElements(), api.getAppState().selectedElementIds, documentsRef.current);
      if (!current || pageKey(current.source) !== pageKey(feedback.source)) {
        throw new BoardFeedbackError("SOURCE_CHANGED", "The drawing changed while saving its marks. Select its current page before sending feedback.");
      }
      const referenceKeys = (selection: BoardFeedbackSelection) => (selection.references ?? [])
        .map((reference) => `${reference.image.id}:${pageKey(reference.source)}`).sort();
      if (JSON.stringify(referenceKeys(current)) !== JSON.stringify(referenceKeys(feedback))) {
        throw new BoardFeedbackError("REFERENCE_CHANGED", "A selected reference changed while saving. Select its current page before sending feedback.");
      }
      if (JSON.stringify(current.annotations) !== JSON.stringify(feedback.annotations)
        || JSON.stringify(current.annotationGroups) !== JSON.stringify(feedback.annotationGroups)) {
        throw new Error(language === "en" ? "The selected marks changed. Close this dialog and review them before sending."
          : "选中的批注已发生变化，请关闭此窗口，核对后重新提交。");
      }
      const request = await prepareBoardDesignRequest(studio, feedback, board.projectId, comment);
      if (alive.current) onSubmit(request);
    } finally { busyRef.current = false; }
  };
  const boardText = whiteboardCopy[language];
  return <section className={`monkeyboard${critMode ? " monkeyboard--crit" : ""}`} aria-label="MonkeyBoard">
    <header className="monkeyboard-topbar">
      <div className="monkeyboard-heading"><input aria-label={text.title} value={title} maxLength={200} onChange={(event) => { const value = event.target.value; setTitle(value); titleRef.current = value; capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} onBlur={() => { const value = titleRef.current.trim() || "MonkeyBoard"; titleRef.current = value; setTitle(value); capture(canvas.current?.getSceneElementsIncludingDeleted() ?? []); }} /></div>
      <span className={`monkeyboard-save-state${saveState.error ? " is-error" : ""}`} role="status">{saveState.error ? text.dirty : saveState.saving ? text.saving : saveState.dirty ? text.dirty : text.saved}</span>
      <button aria-expanded={sourcesOpen} aria-controls="monkeyboard-project-documents" onClick={() => setSourcesOpen((open) => !open)}>{text.sources}</button>
      <button className="monkeyboard-primary" disabled={!ready || busy || saveState.conflict} onClick={() => input.current?.click()}>{text.upload}</button>
      <details ref={actions} className="monkeyboard-actions" onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) closeActions(); }} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeActions(); actions.current?.querySelector("summary")?.focus(); } }}>
        <summary>{boardText.more}</summary>
        <div className="monkeyboard-actions-panel" role="group" aria-label={boardText.more}>
          <button disabled={!ready} onClick={() => canvas.current?.scrollToContent(undefined, { fitToContent: true, animate: false })}>{text.fit}</button>
          <button disabled={!ready || busy || saveState.conflict || feedbackWaiting} onClick={openFeedback}>{feedbackWaiting ? text.busy : feedbackCopy[language].action}</button>
          <button disabled={!ready || busy || saveState.conflict} onClick={() => { void newSketchFrame(); }}>{sketchCopy[language].newFrame}</button>
          <button disabled={!ready} onClick={enterCrit}>{text.crit}</button>
          <button type="button" disabled={!ready || busy || saveState.conflict || !hasAnnotations} onClick={clearAnnotations} title={text.clearAnnotationsHint}>{text.clearAnnotations}</button>
          {onPublish && <button disabled={!ready || busy || exporting || saveState.conflict} onClick={() => void sendToPublish()}>{language === "zh-CN" ? "放入汇报" : "Add to Publish"}</button>}
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
    {saveState.error !== null && <div className="monkeyboard-alert" role="alert"><span>{saveState.conflict ? text.conflict : `${text.saveError} ${errorText(saveState.error)}`}</span>{!saveState.conflict && <button onClick={() => { void queue.retry().catch(() => {}); }}>{text.retry}</button>}</div>}
    {(previewFailed || sourceError) && <div className="monkeyboard-alert" role="alert"><span>{previewFailed ? text.previewError : `${text.sourceError} ${sourceError}`}</span><button onClick={retryVisuals} disabled={busy}>{text.refresh}</button></div>}
    {notice && <div className="monkeyboard-alert" role="alert"><span>{notice}</span><button onClick={() => setNotice("")} aria-label={text.dismiss}>×</button></div>}
    {update && <div className="monkeyboard-alert monkeyboard-update" role="status"><span>{updateNoticeText(update, language)}</span><span className="monkeyboard-update-actions"><button onClick={focusUpdate}>{replacementCopy[language].view}</button><button onClick={() => setUpdate(null)} aria-label={text.dismiss}>×</button></span></div>}
    {!critMode && sketchSelection && <div className="monkeyboard-sketch" role="group" aria-label={sketchCopy[language].panel}>
      <strong>{(sketchSelection.frame as { name?: string | null }).name ?? sketchCopy[language].panel}</strong>
      <label>{sketchCopy[language].level}<select value={sketchSelection.data.levelId} disabled={!ready || busy || sketchSending || saveState.conflict} onChange={(event) => updateSketchFrame(sketchSelection.frame, { ...sketchSelection.data, levelId: event.target.value })}>{[...new Set([...(sketchLevels ?? []).map((level) => level.levelId), sketchSelection.data.levelId])].map((levelId) => <option key={levelId} value={levelId}>{levelId}</option>)}</select></label>
      <label>{sketchCopy[language].storeyHeight}<input type="number" min={0.1} step={0.1} value={sketchHeight} disabled={!ready || busy || sketchSending || saveState.conflict} onChange={(event) => setSketchHeight(event.target.value)} onBlur={() => { const value = Number(sketchHeight); if (sketchHeight.trim() !== "" && Number.isFinite(value) && value > 0) updateSketchFrame(sketchSelection.frame, { ...sketchSelection.data, storeyHeight: value }); else setSketchHeight(String(sketchSelection.data.storeyHeight)); }} /></label>
      <label>{sketchCopy[language].metres}<input type="number" min={0} step={0.01} value={sketchMetres} disabled={!ready || busy || sketchSending || saveState.conflict || sketchSelection.line === null} onChange={(event) => setSketchMetres(event.target.value)} /></label>
      <button type="button" disabled={!ready || busy || sketchSending || saveState.conflict || sketchSelection.line === null || !(Number(sketchMetres) > 0)} onClick={calibrateSketch}>{sketchCopy[language].calibrate}</button>
      <p className="monkeyboard-sketch-scale">{sketchSelection.data.metresPerUnit === null ? sketchCopy[language].uncalibrated
        : sketchCopy[language].calibrated.replace("{units}", String(Math.round(1 / sketchSelection.data.metresPerUnit)))
          .replace("{w}", (sketchSelection.frame.width * sketchSelection.data.metresPerUnit).toFixed(1))
          .replace("{h}", (sketchSelection.frame.height * sketchSelection.data.metresPerUnit).toFixed(1))}{sketchLevels?.length === 0 ? ` · ${sketchCopy[language].noLevels}` : ""}</p>
      <button type="button" className="monkeyboard-primary" disabled={!ready || busy || sketchSending || saveState.conflict || sketchSelection.data.metresPerUnit === null} onClick={() => { void sendSketch(); }}>{sketchSending ? sketchCopy[language].sending : sketchCopy[language].send}</button>
    </div>}
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
            <button className="monkeyboard-source-link" type="button" disabled={!ready || busy || saveState.conflict} onClick={() => openDocument(pageSource(document, page))}>{text.open}</button>
            <button className="monkeyboard-source-update" disabled={!ready || busy || saveState.conflict} onClick={() => openReplacement(document, page)}>{replacementCopy[language].action}</button>
            <button className="monkeyboard-source-work-copy" disabled={!ready || busy || saveState.conflict} onClick={() => { void requestWorkCopy(document); }}>{replacementCopy[language].workCopy}</button>
          </article>;
        })}</div>
      </aside>
      <div className={`monkeyboard-canvas ${PROJECT_CANVAS_CLASS}`} ref={root} onPasteCapture={onPasteCapture} onDropCapture={onDropCapture} onDragOverCapture={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.stopPropagation(); } }} onKeyDownCapture={(event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); event.stopPropagation(); void queue.flush().catch(() => {}); } }}>
        <ProjectCanvas initialData={initialData} excalidrawAPI={(api) => { canvas.current = api; }} name={title} autoFocus={active} zenModeEnabled={critMode}
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
            const selectedSource = selectedPageSource(records(elements), appState.selectedElementIds);
            setSelected((previous) => JSON.stringify(previous) === JSON.stringify(selectedSource) ? previous : selectedSource);
            const sketchFrames = sketchFrameIds(elements);
            setHasAnnotations(elements.some((element) => isAnnotation(element, sketchFrames)));
            updateContext(elements, appState);
            capture(elements);
            scheduleUpdateFocus();
          }}>
          <MainMenu><MainMenu.Item onSelect={() => input.current?.click()}>{text.upload}</MainMenu.Item><MainMenu.DefaultItems.ClearCanvas /></MainMenu>
          <WelcomeScreen><WelcomeScreen.Center><WelcomeScreen.Center.Heading>{boardText.welcome}</WelcomeScreen.Center.Heading><div className="monkeyboard-welcome-body"><p>{boardText.gestures}</p><span className="monkeyboard-welcome-example">{boardText.example}</span><p>{boardText.designHint}</p></div><WelcomeScreen.Center.Menu><button type="button" className="monkeyboard-welcome-upload" onClick={() => input.current?.click()}>{text.upload}</button></WelcomeScreen.Center.Menu></WelcomeScreen.Center></WelcomeScreen>
        </ProjectCanvas>
        {!ready && <div className="monkeyboard-initializing" role="status">{text.loading}</div>}
        {busy && <div className="monkeyboard-busy" role="status">{text.busy}</div>}
        {!critMode && (context || source) && <div className="monkeyboard-context" role="group" aria-label={language === "en" ? "Selected drawing actions" : "选中图纸操作"}>
          {contextDocument && contextSource && <div className="monkeyboard-context-source">
            <div className="monkeyboard-context-identity">
              <span>{selectionCopy[language].selected} · {contextSource.pageIndex + 1}/{contextDocument.pageCount}</span>
              <strong title={contextDocument.fileName}>{contextDocument.fileName}</strong>
            </div>
            <span className={`monkeyboard-context-binding${contextDocument.modelSource ? " is-linked" : ""}`}>{contextDocument.modelSource ? selectionCopy[language].linked : selectionCopy[language].unlinked}</span>
          </div>}
          <div className="monkeyboard-context-actions">
            {source && selected && onOpenDocument && <button disabled={!ready || busy || saveState.conflict} onClick={() => openDocument(selected)}>{text.openPage}</button>}
            {source && <button disabled={!ready || busy || saveState.conflict} onClick={openSelectedReplacement}>{replacementCopy[language].action}</button>}
            {context && (context.reason === "modelRequired" && context.source
              ? <button type="button" className="monkeyboard-context-next" disabled={!ready || busy || saveState.conflict} onClick={() => openDocument(context.source!)}>{boardText.linkModel}</button>
              : <button className="monkeyboard-primary monkeyboard-context-next" disabled={!!context.reason || !ready || busy || saveState.conflict || feedbackWaiting} aria-describedby={context.reason ? "monkeyboard-context-hint" : undefined} onClick={openFeedback}>{feedbackWaiting ? text.busy : feedbackCopy[language].action}</button>)}
          </div>
          {context?.reason && <p id="monkeyboard-context-hint" role="status">{context.reason === "modelRequired" ? feedbackCopy[language].modelRequired : boardText[context.reason]}</p>}
        </div>}
        {critMode && <div className="monkeyboard-crit-actions" role="group" aria-label={text.crit}>
          <button type="button" className="monkeyboard-primary" disabled={!ready || saveState.conflict || feedbackWaiting} onClick={openFeedback}>{feedbackWaiting ? text.busy : text.critSubmit}</button>
          <button type="button" disabled={!ready || busy || saveState.conflict || !hasAnnotations} onClick={clearAnnotations} title={text.clearAnnotationsHint}>{text.clearAnnotations}</button>
          <button type="button" onClick={exitCrit}>{text.critExit}</button>
        </div>}
      </div>
    </div>
    <footer className="monkeyboard-footer"><span>{text.hint}{onOpenDocument ? ` · ${text.openHint}` : ""}</span>{source && selected ? <span title={source.fileName}>{source.fileName} · {selected.pageIndex + 1}/{source.pageCount}</span> : <span>{text.select}</span>}</footer>
    {feedback && <FeedbackDialog active={active} onOpenDocument={openDocument} selection={feedback} language={language} returnFocus={feedbackReturnFocus.current} onCancel={() => { feedbackOpen.current = false; setFeedback(null); }} onSubmit={submitFeedback} />}
    {replacement && <ReplacementDialog active={active} target={replacement} language={language} returnFocus={replacementReturnFocus.current} onCancel={() => { replacementOpen.current = false; setReplacement(null); }} onSubmit={replacePage} />}
  </section>;
}
