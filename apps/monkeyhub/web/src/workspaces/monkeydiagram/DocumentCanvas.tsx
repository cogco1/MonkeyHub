import { memo, useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { useStudio } from "../../api/ProjectRuntimeContext";
import { asStudioApiError, type StudioApiError } from "../../api/client";
import type { DocumentAnnotationRefDto, DocumentCommentDto, DocumentGestureDto, DocumentPageDto, DocumentTracingCalibrationDto, DocumentVisualInputDto, ModelSourceDto, SourceDocumentDto } from "../../api/generated";
import { ErrorPanel } from "../../app/ErrorPanel";
import { startClientTiming, type ClientTimingSpan } from "../../app/clientTiming";
import { useT } from "../../i18n/useT";
import { usePreferences } from "../../features/settings/preferences";
import { eraseAt, inkPath, toPagePoint, zoomPageAt, type PagePoint, type PageView } from "./documentInk";
import { useDocumentAnnotations, type createDocumentAnnotationsController } from "./useDocumentAnnotations";
import { DocumentTextLayer } from "./DocumentTextLayer";
import { renderDocumentVisual } from "./documentVisualInput";
import { DocumentTracingPanel } from "./DocumentTracingPanel";
import { DocumentStudyPanel } from "./DocumentStudyPanel";
import { useDocumentStudy } from "./useDocumentStudy";
import { studyEvidenceToGestures, studyEvidenceFromGestures } from "./documentStudy";
import "./DocumentCanvas.css";

type DrawingTool = "freehand" | "line" | "arrow" | "circle" | "polyline";
type VectorEditTool = "select";
type DocumentTool = DrawingTool | VectorEditTool | "eraser" | "pan" | "text";

function usesNativeTextEditing(event: ReactKeyboardEvent): boolean {
  return event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
    || (event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])") !== null);
}

function Icon({ name }: { name: DocumentTool | "undo" | "redo" | "fit" }) {
  const paths = {
    freehand: "M4 17l1-5L15 2l4 4L9 16l-5 1zm1-5 4 4M13 4l4 4",
    text: "M3 4h16M11 4v16M7 20h8M3 4v3m16-3v3",
    select: "M4 3l14 8-7 2-3 7L4 3zm7 10 5 6",
    line: "M4 17L18 3", arrow: "M4 17L18 3M9 3h9v9",
    polyline: "M3 18L5 4l14 3-3 12L3 18z",
    circle: "M19 10a9 7 0 1 1-18 0 9 7 0 1 1 18 0",
    eraser: "M3 12l9-9a2 2 0 0 1 3 0l4 4a2 2 0 0 1 0 3l-7 7H8l-5-5zm4-4 8 8M9 17h10",
    pan: "M7 10V5a2 2 0 0 1 3 0v5-7a2 2 0 0 1 3 0v7-5a2 2 0 0 1 3 0v6-3a2 2 0 0 1 3 0v6c0 5-3 7-6 7h-2c-2 0-3-1-4-3L3 12c-1-2 1-3 2-2l2 2",
    undo: "M8 4L3 9l5 5M3 9h9a6 6 0 0 1 0 12",
    redo: "M12 4l5 5-5 5M17 9H8a6 6 0 0 0 0 12",
    fit: "M8 3H3v5m10-5h5v5M3 13v5h5m10-5v5h-5",
  };
  return <svg viewBox="0 0 22 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

/** PDF.js applies the native CropBox and rotation; both canvas and ink use that visible page. */
export function DocumentSurface({ file, page, scale, onReady, timing }: {
  file: File; page: DocumentPageDto; scale: number; onReady(ready: boolean): void;
  timing?: ClientTimingSpan;
}) {
  const studio = useStudio();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [source, setSource] = useState<PDFDocumentProxy | HTMLImageElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activeTiming = useRef(timing);
  activeTiming.current = timing;
  const renderTiming = useRef<ClientTimingSpan | null>(null);
  const t = useT();
  useEffect(() => {
    let stopped = false;
    let pdf: ReturnType<typeof import("pdfjs-dist")["getDocument"]> | null = null;
    let imageUrl: string | null = null;
    const parent = activeTiming.current;
    renderTiming.current = parent && !parent.closed ? startClientTiming(studio, "document_render", parent.binding,
      parent.trace, { input_bytes: file.size }) : null;
    const measured = renderTiming.current;
    setSource(null); setError(null); onReady(false);
    void (async () => {
      if (file.type === "application/pdf") {
        const renderer = await import("pdfjs-dist");
        const data = await file.arrayBuffer();
        if (stopped) return;
        renderer.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        pdf = renderer.getDocument({ data });
        const loaded = await pdf.promise;
        if (!stopped) setSource(loaded);
      } else {
        const image = new Image();
        imageUrl = URL.createObjectURL(file);
        image.src = imageUrl;
        await image.decode();
        if (!stopped) setSource(image);
      }
    })().catch((cause: unknown) => { if (!stopped) {
      measured?.finish("failed"); parent?.finish("failed"); setError(String(cause));
    } });
    return () => {
      stopped = true;
      measured?.finish("cancelled");
      if (pdf) void pdf.destroy();
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
  }, [file, onReady]);

  useEffect(() => {
    if (!source) return;
    let stopped = false;
    let render: RenderTask | null = null;
    // Keep the last raster under the vector ink during a zoom, then replace it
    // atomically. A cancelled PDF render never reuses another render's canvas.
    const timeout = window.setTimeout(() => void (async () => {
      const rasterScale = Math.min(scale * (window.devicePixelRatio || 1),
        Math.sqrt(16_000_000 / (page.width * page.height)), 8192 / Math.max(page.width, page.height));
      const buffer = document.createElement("canvas");
      const context = buffer.getContext("2d");
      if (!context) throw new Error("Canvas 2D is unavailable");
      if ("getPage" in source) {
        const pdfPage = await source.getPage(page.pageIndex + 1);
        if (stopped) return;
        const viewport = pdfPage.getViewport({ scale: rasterScale, rotation: page.rotation });
        buffer.width = Math.ceil(viewport.width); buffer.height = Math.ceil(viewport.height);
        render = pdfPage.render({ canvas: buffer, canvasContext: context, viewport });
        await render.promise;
      } else {
        buffer.width = Math.ceil(page.width * rasterScale); buffer.height = Math.ceil(page.height * rasterScale);
        context.drawImage(source, 0, 0, buffer.width, buffer.height);
      }
      if (stopped || !canvasRef.current) return;
      const canvas = canvasRef.current;
      canvas.width = buffer.width; canvas.height = buffer.height;
      canvas.getContext("2d")?.drawImage(buffer, 0, 0);
      renderTiming.current?.finish("succeeded");
      onReady(true);
    })().catch((cause: unknown) => { if (!stopped) {
      renderTiming.current?.finish("failed"); activeTiming.current?.finish("failed"); setError(String(cause)); onReady(false);
    } }), 80);
    return () => { stopped = true; window.clearTimeout(timeout); render?.cancel(); };
  }, [source, page, scale, onReady]);
  return <><canvas ref={canvasRef} className="document-page__raster" aria-label={t("document.pageImage", { page: page.pageIndex + 1 })} />
    {error && <p className="document-render-error" role="alert">{t("document.renderFailed")} <span>{error}</span></p>}</>;
}

const SavedInk = memo(function SavedInk({ annotations, width, height }: {
  annotations: readonly DocumentGestureDto[]; width: number; height: number;
}) {
  return <g data-saved-ink="true">{annotations.filter((mark) => mark.kind !== "text").map((mark) => <path key={mark.id} data-stroke-id={mark.id}
    d={inkPath(mark, width, height)} fill="none" stroke={mark.color}
    strokeWidth={mark.lineWidth * Math.min(width, height)} strokeLinecap="round" strokeLinejoin="round" />)}</g>;
});

type Interaction = {
  pointerId: number; mode: DocumentTool; start: PagePoint; view: PageView;
  points: PagePoint[]; gesture: DocumentGestureDto; remaining: readonly DocumentGestureDto[];
  editingPoint?: number;
};

function isEditableVector(mark: DocumentGestureDto): boolean {
  return (mark.kind === "line" || mark.kind === "arrow" || mark.kind === "polyline") && mark.points.length >= 2;
}

function editableVectorHandle(annotations: readonly DocumentGestureDto[], point: PagePoint,
  width: number, height: number, scale: number): { mark: DocumentGestureDto; pointIndex: number } | null {
  const radius = 12;
  for (const mark of [...annotations].reverse()) {
    if (!isEditableVector(mark)) continue;
    for (const pointIndex of mark.points.map((_, index) => index)) {
      const handle = mark.points[pointIndex];
      if (Math.hypot((handle[0] - point[0]) * width * scale, (handle[1] - point[1]) * height * scale) <= radius) {
        return { mark, pointIndex };
      }
    }
  }
  return null;
}

/** Input stays in page coordinates. Only the current path is updated during a stroke. */
export function DocumentPageCanvas({ file, page, annotations, onChange, readOnly, canUndo, canRedo, onUndo, onRedo, timing, calibration = null, evidenceOnly = false, focusedEvidenceId = null }: {
  file: File; page: DocumentPageDto; annotations: readonly DocumentGestureDto[];
  onChange(marks: readonly DocumentGestureDto[]): void; readOnly: boolean;
  canUndo: boolean; canRedo: boolean; onUndo(): void; onRedo(): void;
  timing?: ClientTimingSpan;
  calibration?: DocumentTracingCalibrationDto | null;
  evidenceOnly?: boolean;
  focusedEvidenceId?: string | null;
}) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const ink = useRef<SVGSVGElement>(null);
  const live = useRef<SVGPathElement>(null);
  const active = useRef<Interaction | null>(null);
  const frame = useRef<number | null>(null);
  const space = useRef(false);
  const fitted = useRef(false);
  const [tool, setTool] = useState<DocumentTool>(evidenceOnly ? "polyline" : "freehand");
  useEffect(() => { setTool(evidenceOnly ? "polyline" : "freehand"); setOutline([]); setSelectedVectorId(null); }, [evidenceOnly]);
  useEffect(() => { if (focusedEvidenceId) { setTool("select"); setSelectedVectorId(focusedEvidenceId); } }, [focusedEvidenceId]);
  const [selectedVectorId, setSelectedVectorId] = useState<string | null>(null);
  const [outline, setOutline] = useState<PagePoint[]>([]);
  const [color, setColor] = useState("#2f80ed");
  const [lineWidth, setLineWidth] = useState(0.004);
  const [fontSize, setFontSize] = useState(0.024);
  const [view, setView] = useState<PageView>({ x: 24, y: 24, scale: 1 });
  const viewRef = useRef(view);
  const [isActive, setIsActive] = useState(false);
  const [temporaryPan, setTemporaryPan] = useState(false);
  const [renderReady, setRenderReady] = useState(false);
  const fallback = useRef<{ move(event: PointerEvent): void; up(event: PointerEvent): void; cancel(): void } | null>(null);
  const updateView = useCallback((next: PageView) => { viewRef.current = next; setView(next); }, []);
  const ready = useCallback((value: boolean) => setRenderReady(value), []);
  const selectedVector = annotations.find((mark) => mark.id === selectedVectorId && isEditableVector(mark)) ?? null;
  useEffect(() => { if (selectedVectorId !== null && selectedVector === null) setSelectedVectorId(null); }, [selectedVector, selectedVectorId]);
  useEffect(() => { if (renderReady) timing?.finish("succeeded"); }, [renderReady, timing]);
  const fit = useCallback(() => {
    const node = host.current;
    if (!node) return;
    const scale = Math.max(0.05, Math.min((node.clientWidth - 48) / page.width, (node.clientHeight - 48) / page.height));
    updateView({ x: (node.clientWidth - page.width * scale) / 2, y: (node.clientHeight - page.height * scale) / 2, scale });
  }, [page.width, page.height, updateView]);
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver(() => { if (!fitted.current && node.clientWidth && node.clientHeight) { fit(); fitted.current = true; } });
    observer.observe(node);
    return () => observer.disconnect();
  }, [fit]);

  const clearPreview = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null; live.current?.setAttribute("d", "");
    ink.current?.querySelectorAll<SVGPathElement>("[data-stroke-id]").forEach((path) => path.removeAttribute("visibility"));
  }, []);
  const cancel = useCallback(() => {
    const interaction = active.current;
    // A move and release can share one frame. Publish the last pan before
    // cancelling its scheduled paint, so input and the displayed page agree.
    if (interaction?.mode === "pan") updateView(viewRef.current);
    active.current = null; clearPreview(); setIsActive(false);
    if (!space.current) setTemporaryPan(false);
    if (interaction && host.current?.hasPointerCapture(interaction.pointerId)) host.current.releasePointerCapture(interaction.pointerId);
  }, [clearPreview, updateView]);
  useEffect(() => () => { if (frame.current !== null) cancelAnimationFrame(frame.current); }, []);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const node = host.current;
      if (!active.current || !node || node.hasPointerCapture?.(event.pointerId) || (event.target instanceof Node && node.contains(event.target))) return;
      if (event.type === "pointerup") fallback.current?.up(event);
      else if (event.type === "pointercancel") fallback.current?.cancel();
      else fallback.current?.move(event);
    };
    const blur = () => fallback.current?.cancel();
    window.addEventListener("pointermove", outside); window.addEventListener("pointerup", outside);
    window.addEventListener("pointercancel", outside); window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("pointermove", outside); window.removeEventListener("pointerup", outside);
      window.removeEventListener("pointercancel", outside); window.removeEventListener("blur", blur);
    };
  }, []);

  const zoom = useCallback((factor: number, anchor?: PagePoint) => {
    const node = host.current;
    if (!node || active.current) return;
    const base = Math.min(node.clientWidth / page.width, node.clientHeight / page.height);
    const scale = Math.max(base * 0.15, Math.min(base * 12, viewRef.current.scale * factor));
    updateView(zoomPageAt(viewRef.current, anchor ?? [node.clientWidth / 2, node.clientHeight / 2], scale));
  }, [page.width, page.height, updateView]);
  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      if (active.current) return;
      if (event.ctrlKey || event.metaKey) {
        const rect = node.getBoundingClientRect();
        zoom(Math.exp(-event.deltaY * 0.002), [event.clientX - rect.left, event.clientY - rect.top]);
      } else updateView({ ...viewRef.current, x: viewRef.current.x - event.deltaX, y: viewRef.current.y - event.deltaY });
    };
    node.addEventListener("wheel", wheel, { passive: false });
    return () => node.removeEventListener("wheel", wheel);
  }, [zoom, updateView]);

  const pagePoint = (event: { clientX: number; clientY: number }): PagePoint => toPagePoint(
    event.clientX, event.clientY, host.current!.getBoundingClientRect(), viewRef.current, page.width, page.height,
  );
  const closeOutline = () => {
    if (readOnly || outline.length < 3) return;
    const mark: DocumentGestureDto = { id: crypto.randomUUID(), kind: "polyline", closed: true,
      points: outline, color, lineWidth };
    onChange([...annotations, mark]); setOutline([]); setSelectedVectorId(mark.id); setTool("select");
  };
  const paint = () => {
    frame.current = null;
    const current = active.current;
    if (!current) return;
    if (current.mode === "pan") { setView(viewRef.current); return; }
    if (current.mode === "eraser") {
      const kept = new Set(current.remaining.map((mark) => mark.id));
      ink.current?.querySelectorAll<SVGPathElement>("[data-stroke-id]").forEach((path) => {
        path.setAttribute("visibility", kept.has(path.dataset.strokeId!) ? "visible" : "hidden");
      });
    } else {
      const points = current.mode === "freehand" ? current.points : [current.points[0], current.points[current.points.length - 1]];
      const gesture = current.mode === "select" ? current.gesture : { ...current.gesture, points };
      live.current?.setAttribute("d", inkPath(gesture, page.width, page.height));
      live.current?.setAttribute("stroke", current.gesture.color);
      live.current?.setAttribute("stroke-width", String(current.gesture.lineWidth * Math.min(page.width, page.height)));
    }
  };
  const schedulePaint = () => { if (frame.current === null) frame.current = requestAnimationFrame(paint); };
  const collect = (event: PointerEvent) => {
    const current = active.current;
    if (!current || event.pointerId !== current.pointerId) return;
    if (current.mode === "pan") {
      viewRef.current = { ...current.view, x: current.view.x + event.clientX - current.start[0], y: current.view.y + event.clientY - current.start[1] };
    } else {
      const point = pagePoint(event);
      if (current.mode === "select") {
        const index = current.editingPoint;
        if (index !== undefined) current.gesture = { ...current.gesture,
          points: current.gesture.points.map((value, pointIndex) => pointIndex === index ? point : value) };
        schedulePaint();
        return;
      }
      const previous = current.points[current.points.length - 1];
      if (current.mode === "eraser") current.remaining = eraseAt(current.remaining, previous, point,
        page.width * viewRef.current.scale, page.height * viewRef.current.scale, 9);
      if (point[0] !== previous[0] || point[1] !== previous[1]) current.points.push(point);
    }
    schedulePaint();
  };
  const down = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (active.current || (event.button !== 0 && event.button !== 1)) return;
    const mode = event.button === 1 || space.current ? "pan" : tool;
    if (mode === "text") return;
    if (mode !== "pan" && (readOnly || !renderReady)) return;
    const node = host.current!;
    const rect = node.getBoundingClientRect();
    const x = event.clientX - rect.left - viewRef.current.x, y = event.clientY - rect.top - viewRef.current.y;
    if (mode !== "pan" && (x < 0 || y < 0 || x > page.width * viewRef.current.scale || y > page.height * viewRef.current.scale)) return;
    event.preventDefault(); node.focus({ preventScroll: true });
    const point = pagePoint(event);
    if (mode === "polyline") {
      const first = outline[0];
      if (outline.length >= 3 && Math.hypot((point[0] - first[0]) * page.width * view.scale,
        (point[1] - first[1]) * page.height * view.scale) <= 12) closeOutline();
      else if (outline.length < 512) setOutline((current) => [...current, point]);
      return;
    }
    if (mode === "select") {
      const hit = editableVectorHandle(annotations, point, page.width, page.height, viewRef.current.scale);
      if (hit === null) {
        const selected = [...annotations].reverse().find(mark => isEditableVector(mark) &&
          eraseAt([mark], point, point, page.width * view.scale, page.height * view.scale, 8).length === 0);
        setSelectedVectorId(selected?.id ?? null); return;
      }
      setSelectedVectorId(hit.mark.id);
      active.current = { pointerId: event.pointerId, mode, start: [event.clientX, event.clientY], view: viewRef.current,
        points: [point], remaining: annotations, gesture: hit.mark, editingPoint: hit.pointIndex };
      ink.current?.querySelector<SVGPathElement>(`[data-stroke-id="${hit.mark.id}"]`)?.setAttribute("visibility", "hidden");
      if (typeof node.setPointerCapture === "function") node.setPointerCapture(event.pointerId);
      setIsActive(true); schedulePaint();
      return;
    }
    active.current = { pointerId: event.pointerId, mode, start: [event.clientX, event.clientY], view: viewRef.current,
      points: [point], remaining: annotations,
      gesture: { id: crypto.randomUUID(), kind: mode === "pan" || mode === "eraser" ? "freehand" : mode, points: [], color, lineWidth } };
    if (mode === "eraser") active.current.remaining = eraseAt(annotations, point, point,
      page.width * viewRef.current.scale, page.height * viewRef.current.scale, 9);
    if (typeof node.setPointerCapture === "function") node.setPointerCapture(event.pointerId);
    if (mode === "pan") setTemporaryPan(true);
    setIsActive(true); schedulePaint();
  };
  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const native = event.nativeEvent;
    const batch = native.getCoalescedEvents?.() ?? [];
    for (const sample of batch.length ? batch : [native]) collect(sample);
  };
  const up = (event: ReactPointerEvent<HTMLDivElement> | PointerEvent) => {
    if (!active.current || active.current.pointerId !== event.pointerId) return;
    collect("nativeEvent" in event ? event.nativeEvent : event);
    const current = active.current;
    if (current.mode === "eraser") {
      if (current.remaining.length !== annotations.length) onChange(current.remaining);
    } else if (current.mode === "select") {
      onChange(annotations.map((mark) => mark.id === current.gesture.id ? current.gesture : mark));
    } else if (current.mode !== "pan") {
      const points = current.mode === "freehand" ? current.points : [current.points[0], current.points[current.points.length - 1]];
      onChange([...annotations, { ...current.gesture, points }]);
    }
    cancel();
  };
  fallback.current = { move: collect, up, cancel };

  return <div className="document-canvas">
    <div className="document-tools" role="toolbar" aria-label={t("document.tools")}>
      {(["select", "freehand", "line", "polyline", "arrow", "circle", "text", "eraser", "pan"] as const).filter(kind => !evidenceOnly || ["select", "polyline", "eraser", "pan"].includes(kind)).map((kind) => <button key={kind} type="button"
        disabled={readOnly && kind !== "pan"} aria-pressed={tool === kind} aria-label={t(`document.tool.${kind}`)} title={t(`document.tool.${kind}`)}
        onClick={() => { cancel(); setOutline([]); setTool(kind); if (kind !== "select") setSelectedVectorId(null); }}><Icon name={kind} /><span>{t(`document.tool.${kind}`)}</span></button>)}
      {tool === "polyline" && <button type="button" disabled={readOnly || outline.length < 3} onClick={closeOutline}>{t("document.trace.close")}</button>}
      {selectedVector && <button type="button" disabled={readOnly} onClick={() => onChange(annotations.filter(mark => mark.id !== selectedVector.id))}>{t("document.trace.delete")}</button>}
      <span className="document-tools__separator" />
      <button type="button" disabled={!canUndo || readOnly} onClick={onUndo} title={t("document.undo")} aria-label={t("document.undo")}><Icon name="undo" /></button>
      <button type="button" disabled={!canRedo || readOnly} onClick={onRedo} title={t("document.redo")} aria-label={t("document.redo")}><Icon name="redo" /></button>
      {!evidenceOnly && <><label className="document-color" title={t("document.color")}><span className="visually-hidden">{t("document.color")}</span><input type="color" value={color} disabled={readOnly} onChange={(event) => setColor(event.target.value)} /></label>
      {tool === "text" ? <select aria-label={t("document.text.size")} value={fontSize} disabled={readOnly} onChange={(event) => setFontSize(Number(event.target.value))}>
        <option value={0.018}>{t("document.text.small")}</option><option value={0.024}>{t("document.medium")}</option><option value={0.032}>{t("document.text.large")}</option>
      </select> : <select aria-label={t("document.width")} value={lineWidth} disabled={readOnly} onChange={(event) => setLineWidth(Number(event.target.value))}>
        <option value={0.002}>{t("document.thin")}</option><option value={0.004}>{t("document.medium")}</option><option value={0.008}>{t("document.thick")}</option>
      </select>}</>}
      <span className="document-tools__spacer" />
      <button type="button" onClick={() => zoom(1 / 1.25)} aria-label={t("document.zoomOut")}>−</button>
      <span className="document-zoom" aria-live="polite">{Math.round(view.scale * 100)}%</span>
      <button type="button" onClick={() => zoom(1.25)} aria-label={t("document.zoomIn")}>+</button>
      <button type="button" onClick={fit} title={t("document.fit")} aria-label={t("document.fit")}><Icon name="fit" /></button>
    </div>
    <div ref={host} className="document-viewport" tabIndex={0} role="region" aria-label={t("document.pageCanvas", { page: page.pageIndex + 1 })}
      data-tool={temporaryPan ? "pan" : tool} data-active={isActive} data-ready={renderReady}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={cancel} onLostPointerCapture={() => { if (active.current) cancel(); }}
      onBlur={() => { space.current = false; setTemporaryPan(false); cancel(); }}
      onKeyDown={(event) => {
        if (event.defaultPrevented || usesNativeTextEditing(event)) return;
        if (event.code === "Space") { event.preventDefault(); space.current = true; setTemporaryPan(true); }
        if (event.key === "Escape") { event.preventDefault(); setOutline([]); if (active.current) cancel(); else setTool("pan"); }
        if (event.key === "Enter" && tool === "polyline") { event.preventDefault(); closeOutline(); }
        if ((event.key === "Delete" || event.key === "Backspace") && !readOnly && selectedVector) {
          event.preventDefault(); event.stopPropagation(); onChange(annotations.filter(mark => mark.id !== selectedVector.id));
        }
        // The workspace owns Undo/Redo for every document control; release any
        // unfinished ink here before that one history handles the bubbling key.
        if ((event.ctrlKey || event.metaKey) && !event.altKey && ["z", "y"].includes(event.key.toLowerCase())) cancel();
      }} onKeyUp={(event) => { if (!usesNativeTextEditing(event) && event.code === "Space") { event.preventDefault(); space.current = false; setTemporaryPan(active.current?.mode === "pan"); } }}>
      <div className="document-page" style={{ width: page.width * view.scale, height: page.height * view.scale, transform: `translate(${view.x}px, ${view.y}px)` }}>
        <DocumentSurface file={file} page={page} scale={view.scale} onReady={ready} timing={timing} />
        <svg ref={ink} className="document-page__ink" viewBox={`0 0 ${page.width} ${page.height}`} aria-hidden="true">
          <SavedInk annotations={annotations} width={page.width} height={page.height} />
          {outline.length > 0 && <path data-outline-draft="true" d={inkPath({ id: "draft", kind: "polyline", closed: false, points: outline, color, lineWidth }, page.width, page.height)} fill="none" stroke={color} strokeWidth={lineWidth * Math.min(page.width, page.height)} />}
          {calibration && <g className="document-calibration" stroke="#16804a" fill="#16804a">
            <path d={`M${calibration.origin[0] * page.width} ${calibration.origin[1] * page.height} L${calibration.axisPoint[0] * page.width} ${calibration.axisPoint[1] * page.height}`} strokeWidth={2 / view.scale} strokeDasharray={`${6 / view.scale} ${4 / view.scale}`} />
            {[calibration.origin, calibration.axisPoint].map((point, index) => <g key={index}>
              <circle cx={point[0] * page.width} cy={point[1] * page.height} r={4 / view.scale} />
              <text x={point[0] * page.width + 8 / view.scale} y={point[1] * page.height - 8 / view.scale} fontSize={14 / view.scale} stroke="none">{index === 0 ? "O" : "+X"}</text>
            </g>)}
          </g>}
          {selectedVector && <g className="document-vector-selection">{selectedVector.points.map((point, index) =>
            <circle key={index} cx={point[0] * page.width} cy={point[1] * page.height} r={7 / view.scale} />)}</g>}
          <path ref={live} data-live-ink="true" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <DocumentTextLayer annotations={annotations} width={page.width} height={page.height} scale={view.scale}
          color={color} fontSize={fontSize} lineWidth={lineWidth} active={tool === "text"}
          panning={temporaryPan} readOnly={readOnly || !renderReady} onChange={onChange} />
      </div>
      {!renderReady && <p className="document-page-loading" role="status">{t("document.loadingPage")}</p>}
    </div>
    <p className="document-input-hint">{t(readOnly ? "document.readOnly" : "document.inputHint")}</p>
  </div>;
}

const modelSourceKey = (source: ModelSourceDto) => JSON.stringify([source.runId, source.stateDigest, source.assetSha256]);
const sameModelSource = (left: ModelSourceDto | null, right: ModelSourceDto | null) => left !== null && right !== null
  && left.runId === right.runId && left.stateDigest === right.stateDigest && left.assetSha256 === right.assetSha256;
type ReferencePage = Pick<DocumentAnnotationRefDto, "runId" | "assetSha256" | "pageIndex" | "drawingRevisionRef"> & { note: string; includeAnnotations: boolean };
const pageKey = (page: Pick<DocumentAnnotationRefDto, "runId" | "assetSha256" | "pageIndex" | "drawingRevisionRef">) =>
  JSON.stringify([page.runId, page.assetSha256, page.pageIndex, page.drawingRevisionRef ?? null]);

export interface DocumentViewContext {
  open: boolean;
  mounted: boolean;
  runId: string | null;
  sourceSha: string | null;
  revisionRef: string | null;
  pageIndex: number;
}

export function DocumentCanvas({ projectId, runId, controller, busy, onSubmit, modelSources, editingModelSource,
  onContinueModelSource, documentVisualInputAvailable, initialSourceSha = null, initialPageIndex = 0, onBeforeLeave,
  initialRevisionRef = null, sourceStageRef, timing, viewedModelSource = null, active = true, onOpenGeneratedDocument }: {
  projectId: string; runId: string | null; busy: boolean;
  documentVisualInputAvailable: boolean;
  modelSources: readonly { label: string; modelSource: ModelSourceDto }[];
  editingModelSource: ModelSourceDto | null;
  viewedModelSource?: ModelSourceDto | null;
  active?: boolean;
  onOpenGeneratedDocument?(document: SourceDocumentDto): void;
  onContinueModelSource(source: ModelSourceDto): Promise<void>;
  initialSourceSha?: string | null; initialPageIndex?: number;
  initialRevisionRef?: string | null;
  sourceStageRef?: string | null;
  timing?: ClientTimingSpan;
  onBeforeLeave?(save: (() => Promise<void>) | null): void;
  controller: ReturnType<typeof createDocumentAnnotationsController>;
  onSubmit(utterance: string, refs: readonly DocumentAnnotationRefDto[], modelSource: ModelSourceDto, visuals: DocumentVisualInputDto[]): Promise<void>;
}) {
  const studio = useStudio();
  const t = useT();
  const { language } = usePreferences();
  const input = useRef<HTMLInputElement>(null);
  const listRequest = useRef(0);
  const activeTiming = useRef(timing);
  activeTiming.current = timing;
  const [documents, setDocuments] = useState<SourceDocumentDto[]>([]);
  const [selectedSha, setSelectedSha] = useState<string | null>(initialSourceSha);
  const [selectedRevision, setSelectedRevision] = useState<string | null>(initialRevisionRef);
  const [selectedRun, setSelectedRun] = useState<string | null>(runId);
  const selectedDocumentRef = useRef({ runId: selectedRun, assetSha256: selectedSha, revisionRef: selectedRevision });
  selectedDocumentRef.current = { runId: selectedRun, assetSha256: selectedSha, revisionRef: selectedRevision };
  const [pageIndex, setPageIndex] = useState(initialPageIndex);
  useEffect(() => {
    if (initialSourceSha === null) return;
    setSelectedRun(runId);
    setSelectedSha(initialSourceSha);
    setSelectedRevision(initialRevisionRef);
    setPageIndex(initialPageIndex);
  }, [runId, initialSourceSha, initialRevisionRef, initialPageIndex]);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<StudioApiError | null>(null);
  const [submitted, setSubmitted] = useState<DocumentCommentDto[]>([]);
  const [review, setReview] = useState<{ ref: DocumentAnnotationRefDto; text: string } | null>(null);
  const [sending, setSending] = useState(false);
  const [modelChoice, setModelChoice] = useState("");
  const [bindingModel, setBindingModel] = useState(false);
  const [continuingModel, setContinuingModel] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [references, setReferences] = useState<ReferencePage[]>([]);
  const [drawingStyles, setDrawingStyles] = useState<Awaited<ReturnType<typeof studio.drawingStyles>>["styles"]>([]);
  const [styleId, setStyleId] = useState("arch400-white");
  const [scaleDenominator, setScaleDenominator] = useState(20);
  const [stylesLoading, setStylesLoading] = useState(true);
  const [stylesRetry, setStylesRetry] = useState(0);
  const [stylesError, setStylesError] = useState<StudioApiError | null>(null);
  const [sheetBusy, setSheetBusy] = useState(false);
  const [sheetError, setSheetError] = useState<StudioApiError | null>(null);
  const listRun = review?.ref.runId ?? (initialSourceSha === null ? null : runId);
  const selectionRun = listRun ?? selectedRun;
  const document = documents.find((item) => item.assetSha256 === selectedSha &&
    (selectionRun === null || item.runId === selectionRun) && (selectedRevision === null || item.revisionRef === selectedRevision)) ?? null;
  // Project-wide discovery has no model run. Page operations use the selected
  // document's retained storage run; the empty scope never reads or writes ink.
  const documentRun = review?.ref.runId ?? document?.runId ?? runId ?? "";
  const initializedSheet = useRef<string | null>(null);
  useEffect(() => {
    if (!document) { initializedSheet.current = null; return; }
    if (drawingStyles.length === 0) return;
    const key = JSON.stringify([projectId, document.runId, document.assetSha256, document.revisionRef ?? null]);
    if (initializedSheet.current === key) return;
    initializedSheet.current = key;
    const recipe = document.viewRecipe;
    if (recipe?.kind !== "review-sheet") return;
    const savedStyle = recipe.style;
    const registered = savedStyle && typeof savedStyle === "object" && "id" in savedStyle
      ? drawingStyles.find((style) => style.id === savedStyle.id) : undefined;
    if (registered) setStyleId(registered.id);
    const scale = recipe.scaleDenominator;
    if (typeof scale === "number" && Number.isInteger(scale) && scale >= 1 && scale <= 10000) setScaleDenominator(scale);
  }, [document, drawingStyles, projectId]);
  const optionKey = (item: SourceDocumentDto) => listRun === null
    ? JSON.stringify([item.runId, item.assetSha256, item.revisionRef ?? null]) : item.revisionRef ?? item.assetSha256;
  const page = document?.pages.find((item) => item.pageIndex === pageIndex) ?? null;
  const documentModelSource = document?.modelSource ?? null;
  const drawingModelSource = documentModelSource ?? viewedModelSource;
  const sheetScopeKey = JSON.stringify([projectId, runId, selectedSha, selectedRevision, active,
    drawingModelSource && modelSourceKey(drawingModelSource), viewedModelSource && modelSourceKey(viewedModelSource)]);
  const sheetScope = useRef({ key: sheetScopeKey });
  if (sheetScope.current.key !== sheetScopeKey) sheetScope.current = { key: sheetScopeKey };
  const hasSheetAction = onOpenGeneratedDocument !== undefined;
  useEffect(() => { setSheetError(null); }, [sheetScopeKey]);
  useEffect(() => {
    if (!hasSheetAction) return;
    let stopped = false;
    setStylesLoading(true); setStylesError(null);
    void studio.drawingStyles().then((result) => {
      if (stopped) return;
      setDrawingStyles(result.styles);
      setStyleId((current) => result.styles.some((style) => style.id === current) ? current : result.styles[0]?.id ?? "");
    }).catch((cause: unknown) => { if (!stopped) setStylesError(asStudioApiError(cause)); })
      .finally(() => { if (!stopped) setStylesLoading(false); });
    return () => { stopped = true; };
  }, [hasSheetAction, stylesRetry]);
  const modelMatches = sameModelSource(documentModelSource, editingModelSource);
  const submitScopeKey = JSON.stringify([projectId, documentRun, selectedSha, selectedRevision, pageIndex, review?.ref.revisionSha256,
    documentModelSource && modelSourceKey(documentModelSource), editingModelSource && modelSourceKey(editingModelSource)]);
  const submitScope = useRef({ key: submitScopeKey });
  if (submitScope.current.key !== submitScopeKey) submitScope.current = { key: submitScopeKey };
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setReferences([]); }, [projectId, documentRun]);
  useEffect(() => {
    setReferences((current) => current.filter((item) => item.assetSha256 !== selectedSha || item.pageIndex !== pageIndex ||
      (item.drawingRevisionRef ?? null) !== selectedRevision));
  }, [selectedSha, selectedRevision, pageIndex]);
  const referenceChoices = [...new Map(documents.flatMap((source) => source.pages
    .filter((item) => source.assetSha256 !== selectedSha || item.pageIndex !== pageIndex || (source.revisionRef ?? null) !== selectedRevision)
    .map((item) => ({ runId: source.runId, assetSha256: source.assetSha256, pageIndex: item.pageIndex,
      ...(source.revisionRef ? { drawingRevisionRef: source.revisionRef } : {}),
      label: t("document.referencePage", { file: source.fileName, page: item.pageIndex + 1 }) +
        (source.revisionRef ? ` · ${source.generatedAt ?? source.revisionRef.split("/").at(-1)?.slice(0, 8)}` : "") })))
    .map((choice) => [pageKey(choice), choice] as const)).values()];
  const modelLabel = documentModelSource === null ? null
    : modelSources.find((item) => sameModelSource(item.modelSource, documentModelSource))?.label ?? documentModelSource.runId;
  const missingPage = !loading && selectedSha !== null && !page;
  useEffect(() => {
    if (selectedSha !== initialSourceSha || selectedRevision !== initialRevisionRef || pageIndex !== initialPageIndex) timing?.finish("cancelled");
    else if (missingPage) timing?.finish("failed");
  }, [selectedSha, selectedRevision, pageIndex, initialSourceSha, initialRevisionRef, initialPageIndex, missingPage, timing]);
  const draft = useDocumentAnnotations({ projectId, runId: documentRun, assetSha256: page ? selectedSha : null, pageIndex,
    revisionSha256: review?.ref.revisionSha256 ?? null, drawingRevisionRef: review ? review.ref.drawingRevisionRef ?? null : selectedRevision }, controller);
  useEffect(() => {
    // A Board request may have saved new marks while this editor was hidden.
    // Never replace a local draft whose save is still pending or refused.
    if (active && draft.ready && !draft.dirty && !draft.saving) void draft.reload();
  }, [active, documentRun, selectedSha, selectedRevision, pageIndex, draft.reload]);
  const [studyMode, setStudyMode] = useState(false);
  const [focusedEvidenceId, setFocusedEvidenceId] = useState<string | null>(null);
  const studySource = page && selectedSha && documentRun ? { runId: documentRun, assetSha256: selectedSha,
    revisionRef: selectedRevision, pageIndex } : null;
  const study = useDocumentStudy(projectId, studyMode ? studySource : null);
  const studyBusy = study.busy || uploading || sending || busy || !active;
  const saveStudyIfDirty = async () => { if (study.ready && study.dirty) await study.save(); };
  const latestDraft = useRef(draft);
  latestDraft.current = draft;
  // Every mark saves itself; a comment after its typing pause. Leaving this
  // page, hiding the window or closing the editor saves a pending comment now.
  const flushDraft = draft.flush;
  useEffect(() => {
    const hidden = () => { if (window.document.hidden) flushDraft(); };
    window.document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", flushDraft);
    return () => {
      window.document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", flushDraft);
      flushDraft();
    };
  }, [flushDraft]);
  useEffect(() => {
    onBeforeLeave?.(async () => {
      if (study.busy || (studyMode && uploading)) throw new Error(language === "en" ? "Wait for the Study operation before leaving." : "请等待本次研究操作完成后再离开。");
      await saveStudyIfDirty();
      if (draft.ready && !draft.readOnly && (draft.dirty || draft.saving)) await draft.save();
    });
    return () => onBeforeLeave?.(null);
  }, [draft.ready, draft.readOnly, draft.dirty, draft.saving, draft.save, onBeforeLeave, study.ready, study.dirty, study.busy, study.save, studyMode, uploading]);
  useEffect(() => {
    let stopped = false;
    const request = ++listRequest.current;
    setLoading(true); setError(null);
    void studio.documents(listRun).then((result) => {
      if (stopped || request !== listRequest.current) return;
      const available = result.documents;
      const linked = available.filter((item) => sameModelSource(item.modelSource ?? null, editingModelSource));
      const generated = available.filter((item) => item.generatedAt && Number.isFinite(Date.parse(item.generatedAt)));
      const linkedGenerated = generated.filter((item) => sameModelSource(item.modelSource ?? null, editingModelSource));
      const reviewChoices = editingModelSource === null ? generated : linkedGenerated;
      const latestTime = Math.max(...reviewChoices.map((item) => Date.parse(item.generatedAt!)));
      const newest = reviewChoices.filter((item) => Date.parse(item.generatedAt!) === latestTime);
      const preferred = newest.length === 1 ? newest[0] : linked.length === 1 ? linked[0] : null;
      const selected = selectedDocumentRef.current;
      const keepSelection = selected.assetSha256 !== null && (selected.assetSha256 === initialSourceSha || available.some((item) =>
        item.assetSha256 === selected.assetSha256 && (listRun !== null || selected.runId === null || item.runId === selected.runId) &&
        (selected.revisionRef === null || item.revisionRef === selected.revisionRef)));
      setDocuments(available);
      if (!keepSelection) {
        setSelectedSha(preferred?.assetSha256 ?? null);
        setSelectedRevision(preferred?.revisionRef ?? null);
        setSelectedRun(preferred?.runId ?? null);
      }
    }).catch((cause: unknown) => { if (!stopped && request === listRequest.current) {
      activeTiming.current?.finish("failed"); setError(asStudioApiError(cause));
    } })
      .finally(() => { if (!stopped && request === listRequest.current) setLoading(false); });
    return () => { stopped = true; };
  }, [listRun, initialSourceSha, sourceStageRef, editingModelSource]);
  const fileSha = document?.assetSha256 ?? null;
  const fileName = document?.fileName ?? null;
  useEffect(() => {
    let stopped = false;
    setFile(null);
    const parent = activeTiming.current;
    const measured = parent && !parent.closed && fileSha && fileName ? startClientTiming(studio, "document_load",
      { ...parent.binding, runId: documentRun }, parent.trace,
      { asset_sha256: fileSha, request_kind: "document_bytes" }) : null;
    if (fileSha && fileName && documentRun) void studio.documentFile(documentRun, fileSha, fileName, selectedRevision, measured?.trace)
      .then((value) => { measured?.finish(stopped ? "cancelled" : "succeeded", { input_bytes: value.size }); if (!stopped) setFile(value); })
      .catch((cause: unknown) => { measured?.finish(stopped ? "cancelled" : "failed"); if (!stopped) { parent?.finish("failed"); setError(asStudioApiError(cause)); } });
    return () => { stopped = true; measured?.finish("cancelled"); };
  }, [fileSha, fileName, documentRun, selectedRevision]);
  useEffect(() => { setModelChoice(""); }, [selectedSha, documentRun]);
  const refreshComments = useCallback(async () => {
    const comments = documentRun ? (await studio.documentComments(documentRun)).comments : [];
    setSubmitted(comments); return comments;
  }, [documentRun]);
  useEffect(() => { void refreshComments().catch((cause: unknown) => setError(asStudioApiError(cause))); }, [refreshComments]);

  const upload = async (value: File) => {
    try { await saveStudyIfDirty(); } catch (cause) { setError(asStudioApiError(cause)); return; }
    setUploading(true); setError(null); setFeedback("");
    try {
      const result = await studio.uploadDocument(projectId, runId, value);
      // A list requested before this upload cannot replace the newly opened
      // source with its older contents, even if that response arrives last.
      const request = ++listRequest.current; setLoading(false); setError(null);
      setReview(null); setDocuments((current) => [...current.filter((item) => optionKey(item) !== optionKey(result)), result]);
      setSelectedSha(result.assetSha256); setSelectedRevision(result.revisionRef ?? null); setSelectedRun(result.runId); setPageIndex(0);
      // Refresh after the upload so an invalidated initial request cannot also
      // hide previously uploaded sources from the selector.
      const refreshed = await studio.documents(listRun);
      if (request === listRequest.current) {
        setDocuments([...refreshed.documents.filter((item) => optionKey(item) !== optionKey(result)), result]);
        setError(null);
      }
    } catch (cause) { setError(asStudioApiError(cause)); }
    finally { setUploading(false); }
  };
  const bindModel = async () => {
    const chosen = modelSources.find((item) => modelSourceKey(item.modelSource) === modelChoice);
    if (!document || documentModelSource || !chosen || bindingModel || uploading || busy || review !== null) return;
    setBindingModel(true); setError(null); setFeedback("");
    try {
      if (draft.ready && !draft.readOnly && (draft.dirty || draft.saving)) await draft.save();
      const bound = await studio.bindDocumentModelSource(projectId, documentRun, document.assetSha256, chosen.modelSource);
      setDocuments((current) => current.map((item) => optionKey(item) === optionKey(bound) ? bound : item));
      setModelChoice("");
    } catch (cause) { setError(asStudioApiError(cause)); }
    finally { setBindingModel(false); }
  };
  const switchDocumentPage = async (change: () => void) => {
    try {
      if (study.busy || uploading) return;
      await saveStudyIfDirty();
      if (draft.ready && !draft.readOnly && (draft.dirty || draft.saving)) await draft.save();
      change();
    } catch (cause) { setError(asStudioApiError(cause)); }
  };
  const generateSheet = async () => {
    const style = drawingStyles.find((item) => item.id === styleId);
    if (!drawingModelSource || !style || sheetBusy || busy || loading || !active || !onOpenGeneratedDocument) return;
    const scope = sheetScope.current;
    const isCurrent = () => mounted.current && sheetScope.current === scope;
    setSheetBusy(true); setSheetError(null);
    try {
      const recipe = sameModelSource(documentModelSource, drawingModelSource) ? document?.viewRecipe : null;
      const strings = (key: string): string[] | undefined => {
        const value = recipe?.[key];
        return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : undefined;
      };
      const result = await studio.drawingSheet({ projectId, modelSource: drawingModelSource, styleId: style.id, scaleDenominator,
        hiddenObjectIds: strings("hiddenObjectIds"), outlineObjectIds: strings("outlineObjectIds"), notes: strings("notes") });
      if (!isCurrent()) return;
      // Save marks added while the sheet was being generated before opening it.
      const currentDraft = latestDraft.current;
      if (currentDraft.ready && !currentDraft.readOnly && (currentDraft.dirty || currentDraft.saving)) await currentDraft.save();
      if (!isCurrent()) return;
      onOpenGeneratedDocument(result);
    } catch (cause) { if (isCurrent()) setSheetError(asStudioApiError(cause)); }
    finally { if (mounted.current) setSheetBusy(false); }
  };
  const continueModel = async () => {
    if (!documentModelSource || continuingModel || busy || review !== null) return;
    setContinuingModel(true); setError(null); setFeedback("");
    try { await onContinueModelSource(documentModelSource); }
    catch (cause) { setError(asStudioApiError(cause)); }
    finally { setContinuingModel(false); }
  };
  const submit = async () => {
    if (!documentVisualInputAvailable || !draft.comment.trim() || sending || busy || draft.readOnly || !document || !page || !file || !documentModelSource || !modelMatches) return;
    const sourceAtSubmit = documentModelSource;
    const scopeAtSubmit = submitScope.current;
    const isCurrent = () => mounted.current && submitScope.current === scopeAtSubmit;
    setSending(true); setFeedback(""); setError(null);
    try {
      const utterance = draft.comment.trim();
      const ref = await draft.save();
      if (!isCurrent()) return;
      const sourceFiles = new Map<string, Promise<File>>();
      const prepare = async (source: SourceDocumentDto, selectedPage: DocumentPageDto, role: "edit" | "reference",
        revision: string | null, referenceNote: string | null, includeAnnotations = true): Promise<DocumentVisualInputDto> => {
        const sourceKey = JSON.stringify([source.runId, source.assetSha256, source.revisionRef ?? null]);
        let sourceFile = sourceFiles.get(sourceKey);
        if (!sourceFile) {
          sourceFile = studio.documentFile(source.runId, source.assetSha256, source.fileName, source.revisionRef);
          sourceFiles.set(sourceKey, sourceFile);
        }
        const [saved, bytes] = await Promise.all([
          includeAnnotations ? studio.documentAnnotations(source.runId, source.assetSha256, selectedPage.pageIndex, revision, source.revisionRef) : null, sourceFile,
        ]);
        if (!isCurrent()) throw new Error(t("document.visualSourceChanged"));
        if (saved && (saved.projectId !== projectId || saved.runId !== source.runId || saved.assetSha256 !== source.assetSha256 ||
          saved.pageIndex !== selectedPage.pageIndex || (revision !== null && saved.revisionSha256 !== revision) ||
          (saved.drawingRevisionRef ?? null) !== (source.revisionRef ?? null) ||
          (saved.annotations.length > 0 && saved.revisionSha256 === null))) throw new Error(t("document.visualSourceChanged"));
        const image = await renderDocumentVisual(bytes, selectedPage, saved?.annotations ?? []);
        if (!isCurrent()) throw new Error(t("document.visualSourceChanged"));
        return { role, runId: source.runId, assetSha256: source.assetSha256, pageIndex: selectedPage.pageIndex,
          ...(source.revisionRef ? { drawingRevisionRef: source.revisionRef } : {}),
          revisionSha256: saved && (role === "edit" || saved.annotations.length > 0) ? saved.revisionSha256 : null,
          pagePngBase64: image.pagePngBase64, annotatedPngBase64: image.annotatedPngBase64, referenceNote };
      };
      if (ref.runId !== document.runId || ref.assetSha256 !== document.assetSha256 || ref.pageIndex !== page.pageIndex ||
          (ref.drawingRevisionRef ?? null) !== (document.revisionRef ?? null)) {
        throw new Error(t("document.visualSourceChanged"));
      }
      const visuals = [await prepare(document, page, "edit", ref.revisionSha256, null)];
      for (const reference of references) {
        if (!isCurrent()) return;
        const source = documents.find((item) => item.runId === reference.runId && item.assetSha256 === reference.assetSha256 &&
          (item.revisionRef ?? null) === (reference.drawingRevisionRef ?? null));
        const referencePage = source?.pages.find((item) => item.pageIndex === reference.pageIndex);
        if (!source || !referencePage) throw new Error(t("document.referenceUnavailable"));
        visuals.push(await prepare(source, referencePage, "reference", null, reference.note.trim() || null, reference.includeAnnotations));
      }
      if (!isCurrent()) return;
      const decodedBytes = visuals.flatMap((visual) => [visual.pagePngBase64, visual.annotatedPngBase64])
        .reduce((sum, value) => sum + (value ? value.length * 3 / 4 - (value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0) : 0), 0);
      if (decodedBytes > 16 * 1024 * 1024) throw new Error(t("document.visualTooLarge"));
      await onSubmit(utterance, [ref], sourceAtSubmit, visuals);
      if (!isCurrent()) return;
      const comments = await refreshComments();
      if (!isCurrent()) return;
      const retained = comments.some((comment) => comment.utterance === utterance && comment.documentAnnotations.some((item) =>
        item.runId === ref.runId && item.assetSha256 === ref.assetSha256 && item.pageIndex === ref.pageIndex && item.revisionSha256 === ref.revisionSha256 &&
        (item.drawingRevisionRef ?? null) === (ref.drawingRevisionRef ?? null)));
      setFeedback(t(retained ? "document.submitted" : "document.submitFailed"));
    } catch (cause) { if (isCurrent()) setError(asStudioApiError(cause)); }
    finally { if (mounted.current) setSending(false); }
  };
  return <div className="document-workspace" aria-label={t("document.workspace")} onKeyDown={(event) => {
    if (event.defaultPrevented || usesNativeTextEditing(event) || event.altKey || !(event.ctrlKey || event.metaKey)
      || event.currentTarget.closest("[inert], [aria-hidden='true']")
      || (studyMode ? !study.ready || studyBusy : !draft.ready || draft.readOnly || sending)) return;
    const key = event.key.toLowerCase();
    if (key !== "z" && key !== "y") return;
    event.preventDefault(); event.stopPropagation();
    if (event.shiftKey || key === "y") { if (studyMode) study.redo(); else draft.redo(); }
    else { if (studyMode) study.undo(); else draft.undo(); }
  }}>
    <div className="document-header">
      <button type="button" className="btn" disabled={uploading || sending || study.busy || review !== null} onClick={() => input.current?.click()}>{t(uploading ? "document.uploading" : "document.open")}</button>
      <input ref={input} className="visually-hidden" type="file" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg"
        aria-label={t("document.open")} onChange={(event) => { const selected = event.target.files?.[0]; event.target.value = ""; if (selected) void upload(selected); }} />
      {documents.length > 0 && <select aria-label={t("document.source")} value={document ? optionKey(document) : selectedRevision ?? selectedSha ?? ""} disabled={uploading || sending || study.busy || review !== null}
        onChange={(event) => { const selected = documents.find((item) => optionKey(item) === event.target.value);
          if (selected) void switchDocumentPage(() => { setSelectedSha(selected.assetSha256); setSelectedRevision(selected.revisionRef ?? null); setSelectedRun(selected.runId); setPageIndex(0); setFeedback(""); }); }}>
        {selectedSha === null && <option value="">选择图纸</option>}
        {!document && selectedSha !== null && <option value={selectedSha} disabled>{t("document.linkUnavailable")}</option>}
        {documents.map((item) => <option key={optionKey(item)} value={optionKey(item)}>{item.fileName}{item.revisionRef ? ` · ${item.revisionRef.split("/").at(-1)?.slice(0, 8)}` : ""}</option>)}
      </select>}
      {document && <div className="document-pages">
        <button type="button" aria-label={t("document.previousPage")} disabled={uploading || sending || study.busy || pageIndex <= 0 || review !== null} onClick={() => { void switchDocumentPage(() => setPageIndex((value) => value - 1)); }}>‹</button>
        <label><span className="visually-hidden">{t("document.page")}</span><select aria-label={t("document.page")} value={pageIndex} disabled={uploading || sending || study.busy || review !== null} onChange={(event) => { const next = Number(event.target.value); void switchDocumentPage(() => setPageIndex(next)); }}>
          {!page && <option value={pageIndex} disabled>{t("document.linkUnavailable")}</option>}
          {document.pages.map((item) => <option key={item.pageIndex} value={item.pageIndex}>{item.pageIndex + 1} / {document.pageCount}</option>)}
        </select></label>
        <button type="button" aria-label={t("document.nextPage")} disabled={uploading || sending || study.busy || pageIndex >= document.pageCount - 1 || review !== null} onClick={() => { void switchDocumentPage(() => setPageIndex((value) => value + 1)); }}>›</button>
      </div>}
      {studySource && <button type="button" aria-pressed={studyMode} disabled={studyBusy || review !== null}
        onClick={() => { void (async () => { try {
          if (studyMode) await saveStudyIfDirty();
          else if (draft.ready && !draft.readOnly && (draft.dirty || draft.saving)) await draft.save();
          setStudyMode(value => !value);
        } catch (cause) { setError(asStudioApiError(cause)); } })(); }}>
        {studyMode ? (language === "en" ? "Return to annotations" : "返回图页批注") : (language === "en" ? "Study this page" : "研究此图页")}
      </button>}
      <span className="document-save-state" role="status">{t(studyMode ? study.busy ? "document.saving" : study.dirty ? "document.unsaved" : study.ready ? "document.saved" : "document.loading"
        : draft.saving ? "document.saving" : draft.dirty ? "document.unsaved" : draft.ready ? "document.saved" : "document.loading")}</span>
    </div>
    {document && !studyMode && <div className="document-model-source" aria-label={t("document.modelSource.label")}
      data-model-source-status={documentModelSource === null ? "unknown" : modelMatches ? "ready" : "mismatch"}>
      <div className="document-model-source__description">
        <span>{t("document.modelSource.label")}</span>{modelLabel && <strong>{modelLabel}</strong>}
        <p>{t(documentModelSource === null ? "document.modelSource.unknown" : modelMatches ? "document.modelSource.ready" : "document.modelSource.mismatch")}</p>
      </div>
      {review === null && (documentModelSource === null ? <div className="document-model-source__actions">
        {modelSources.length > 0 ? <>
          <select aria-label={t("document.modelSource.select")} value={modelChoice} disabled={bindingModel || uploading || busy}
            onChange={(event) => setModelChoice(event.target.value)}>
            <option value="">{t("document.modelSource.select")}</option>
            {modelSources.map((item) => <option key={modelSourceKey(item.modelSource)} value={modelSourceKey(item.modelSource)}>{item.label}</option>)}
          </select>
          <button type="button" disabled={!modelChoice || bindingModel || uploading || busy} onClick={() => void bindModel()}>
            {t(bindingModel ? "document.modelSource.associating" : "document.modelSource.associate")}
          </button>
        </> : <span>{t("document.modelSource.noModels")}</span>}
      </div> : !modelMatches && <button type="button" disabled={continuingModel || busy} onClick={() => void continueModel()}>
        {t(continuingModel ? "document.modelSource.continuing" : "document.modelSource.continue")}
      </button>)}
    </div>}
    {(error || draft.error) && <div className="document-error"><ErrorPanel error={(error ?? draft.error)!} what={t("document.workspace")} />
      {draft.error && <><button type="button" onClick={() => void draft.save().then(() => setError(null)).catch((cause: unknown) => setError(asStudioApiError(cause)))}>{t("document.retrySave")}</button>
        <button type="button" onClick={() => void draft.reload().then(() => setError(null)).catch((cause: unknown) => setError(asStudioApiError(cause)))}>{t("document.reload")}</button>
        <span>{t("document.reloadHint")}</span></>}
    </div>}
    {review && <div className="document-review-banner"><span>{t("document.reviewVersion")}</span><button type="button" onClick={() => { setReview(null); setPageIndex(0); }}>{t("document.returnToEdit")}</button></div>}
    <div className="document-body">
      <div className="document-main">
        {file && page ? <DocumentPageCanvas key={`${documentRun}:${selectedSha}:${selectedRevision}:${pageIndex}`} file={file} page={page} timing={timing}
          annotations={studyMode ? studyEvidenceToGestures(study.draft.evidence) : draft.annotations}
          onChange={studyMode ? marks => study.change({ ...study.draft, evidence: studyEvidenceFromGestures(marks, study.draft.evidence) }) : draft.changeAnnotations}
          readOnly={studyMode ? !study.ready || studyBusy || review !== null : !draft.ready || draft.readOnly || sending}
          evidenceOnly={studyMode} focusedEvidenceId={studyMode ? focusedEvidenceId : null}
          calibration={studyMode ? null : draft.tracingCalibration}
          canUndo={studyMode ? !studyBusy && study.canUndo : draft.canUndo} canRedo={studyMode ? !studyBusy && study.canRedo : draft.canRedo}
          onUndo={studyMode ? study.undo : draft.undo} onRedo={studyMode ? study.redo : draft.redo} />
          : <div className="document-empty"><p role={missingPage ? "alert" : undefined}>{t(missingPage ? "document.linkUnavailable" : loading || selectedSha ? "document.loadingPage" : "document.empty")}</p><p>{t("document.formats")}</p></div>}
      </div>
      {studyMode && studySource ? <DocumentStudyPanel source={studySource} sourceName={document?.fileName ?? ""}
        draft={study.draft} study={study.view} dirty={study.dirty} onChange={study.change} busy={studyBusy} loading={!study.ready && !study.error}
        error={study.error} onSave={() => { void study.save().catch(() => undefined); }}
        onReopen={() => { void study.reload(); }} onSelectEvidence={setFocusedEvidenceId}
        onPropose={action => { void study.propose(action); }}
        comparisonTargets={study.choices.filter(row => row.studyId !== study.draft.studyId).map(row => ({
          studyId: row.studyId, ledgerRef: row.ledgerRef,
          label: `${documents.find(item => item.assetSha256 === row.source.assetSha256)?.fileName ?? row.studyId} · ${Number(row.source.pageIndex) + 1}`,
        }))} onCompare={target => { void study.compare(target); }} />
      : <aside className="document-notes" aria-label={t("document.notes")}>
        {file && page && <DocumentTracingPanel key={JSON.stringify([projectId, documentRun, selectedSha, selectedRevision, pageIndex, review?.ref.revisionSha256])} draft={draft}
          disabled={busy || sending || !active} onSendingChange={setSending} />}
        {hasSheetAction && <section className="document-drawing-styles" aria-label={t("document.drawingStyles")}>
          <fieldset disabled={stylesLoading || sheetBusy}>
            <legend>{t("document.drawingStyles")}</legend>
            <div className="document-drawing-styles__cards">{drawingStyles.map((style) => <label className="document-drawing-style" key={style.id}>
              <input type="radio" name="document-drawing-style" value={style.id} checked={styleId === style.id}
                onChange={() => setStyleId(style.id)} />
              <svg className="document-drawing-style__preview" viewBox={`0 0 ${56 * style.paperSizeMm[0] / style.paperSizeMm[1]} 56`} aria-hidden="true">
                <rect x="1" y="1" width={56 * style.paperSizeMm[0] / style.paperSizeMm[1] - 2} height="54" rx="1" />
                <path d="M9 12h20v15H9z M39 12h12v15H39z M9 34h20v9H9z" />
                {style.previewKind === "technical" ? <path className="document-drawing-style__detail" d="M5 5h69v46H5z M60 5v46 M60 18h14 M60 27h14 M60 40h14 M7 30h25 M7 28v4 M32 28v4 M55 10v19 M53 10h4 M53 29h4" />
                  : <path className="document-drawing-style__detail" d="M9 6h13 M39 37h10 M39 41h7 M9 49h10 M43 49h6" />}
              </svg>
              <span className="document-drawing-style__label"><strong>{language === "en" ? style.nameEn : style.name}</strong>
                <span>{style.paperSizeMm.join(" × ")} mm</span></span>
            </label>)}</div>
          </fieldset>
          {stylesLoading && <p role="status">{t("document.drawingStylesLoading")}</p>}
          {drawingStyles.find((style) => style.id === styleId) && <p>{language === "en"
            ? drawingStyles.find((style) => style.id === styleId)!.descriptionEn : drawingStyles.find((style) => style.id === styleId)!.description}</p>}
          <div className="document-drawing-styles__scale"><label htmlFor="document-drawing-scale">{t("document.drawingScale")}</label>
            <select id="document-drawing-scale" value={scaleDenominator} disabled={sheetBusy} onChange={(event) => setScaleDenominator(Number(event.target.value))}>
              {[...new Set([5, 10, 20, 50, 100, 200, 500, scaleDenominator])].sort((left, right) => left - right)
                .map((scale) => <option key={scale} value={scale}>1:{scale}</option>)}
            </select></div>
          <p id="document-drawing-source" className="document-drawing-styles__source">{t(!drawingModelSource ? "document.drawingNoSource"
            : documentModelSource ? "document.drawingFromDocument" : "document.drawingFromModel")}</p>
          <button type="button" className="btn btn--accent document-drawing-styles__generate" aria-describedby="document-drawing-source"
            disabled={stylesLoading || !drawingStyles.some((style) => style.id === styleId) || !drawingModelSource || sheetBusy || busy || loading || !active}
            onClick={() => void generateSheet()}>{t(sheetBusy ? "document.drawingGenerating" : "document.drawingGenerate")}</button>
          {sheetBusy && <p role="status">{t("document.drawingGeneratingHint")}</p>}
          {stylesError && <ErrorPanel error={stylesError} what={t("document.drawingGenerate")} />}
          {sheetError && <p className="document-drawing-styles__error" role="alert">{sheetError.detail}</p>}
          {stylesError && <button type="button" onClick={() => setStylesRetry((value) => value + 1)}>{t("document.drawingStylesRetry")}</button>}
        </section>}
        <label htmlFor="document-comment">{t("document.comment")}</label>
        <textarea id="document-comment" value={review?.text ?? draft.comment} readOnly={draft.readOnly} disabled={!draft.ready || sending}
          onChange={(event) => draft.setComment(event.target.value)} onBlur={draft.flush} placeholder={t("document.commentHint")} />
        <div className="document-note-actions">
          <button type="button" className="btn btn--accent" disabled={!documentVisualInputAvailable || !draft.ready || draft.readOnly || !draft.comment.trim() || !file || busy || sending || !modelMatches}
            onClick={() => void submit()}>{t(sending ? "document.submitting" : "document.submit")}</button></div>
        {!documentVisualInputAvailable && <p className="document-visual-unavailable" role="status">{t("document.visualUnavailable")}</p>}
        {review === null && <details className="document-references">
          <summary>{t("document.references", { count: references.length })}</summary>
          <p>{t("document.referenceHint")}</p>
          <div className="document-references__pages">{referenceChoices.map((choice) => {
            const selected = references.find((item) => pageKey(item) === pageKey(choice));
            return <div className="document-reference" key={pageKey(choice)}>
              <label><input type="checkbox" checked={selected !== undefined} disabled={sending || (!selected && references.length >= 3)}
                onChange={(event) => setReferences((current) => event.target.checked
                  ? [...current, { runId: choice.runId, assetSha256: choice.assetSha256, pageIndex: choice.pageIndex,
                    ...(choice.drawingRevisionRef ? { drawingRevisionRef: choice.drawingRevisionRef } : {}), note: "", includeAnnotations: false }]
                  : current.filter((item) => pageKey(item) !== pageKey(choice)))} /><span>{choice.label}</span></label>
              {selected && <><label className="document-reference__ink"><input type="checkbox" checked={selected.includeAnnotations} disabled={sending}
                aria-label={t("document.referenceInkLabel", { page: choice.label })}
                onChange={(event) => setReferences((current) => current.map((item) => pageKey(item) === pageKey(choice)
                  ? { ...item, includeAnnotations: event.target.checked } : item))} /><span>{t("document.referenceInk")}</span></label>
              <input type="text" value={selected.note} maxLength={500} disabled={sending}
                aria-label={t("document.referencePurpose", { page: choice.label })} placeholder={t("document.referencePurposeHint")}
                onChange={(event) => setReferences((current) => current.map((item) => pageKey(item) === pageKey(choice)
                  ? { ...item, note: event.target.value } : item))} /></>}
            </div>;
          })}</div>
          {referenceChoices.length === 0 && <p>{t("document.noReferencePages")}</p>}
        </details>}
        {feedback && <p role="status">{feedback}</p>}
        <details className="document-submitted"><summary>{t("document.submittedNotes", { count: submitted.length })}</summary>
          {submitted.map((comment) => <article key={comment.commentRef}><p>{comment.utterance}</p>
            {comment.documentAnnotations.map((ref) => <button key={`${pageKey(ref)}:${ref.revisionSha256}`} type="button" disabled={sending}
              onClick={() => { void switchDocumentPage(() => { setReview({ ref, text: comment.utterance }); setSelectedSha(ref.assetSha256); setSelectedRevision(ref.drawingRevisionRef ?? null); setSelectedRun(ref.runId); setPageIndex(ref.pageIndex); }); }}>
              {t("document.openSubmittedPage", { page: ref.pageIndex + 1 })}</button>)}
          </article>)}
        </details>
      </aside>}
    </div>
  </div>;
}
