import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";

import type { DocumentGestureDto } from "../../api/generated";
import { useT } from "../../i18n/useT";
import "./DocumentTextLayer.css";

type Point = [number, number];
type TextMark = DocumentGestureDto & { kind: "text"; label: string; fontSize: number };
type Editor = { mark: TextMark; existing: boolean };
type Drag = { pointerId: number; node: HTMLElement; mark: TextMark; start: Point; point: Point; moved: boolean };
type PanelPosition = { left: number; top: number; width: number; maxHeight: number };

function isText(mark: DocumentGestureDto): mark is TextMark {
  return mark.kind === "text" && typeof mark.label === "string" && typeof mark.fontSize === "number";
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));

/** Page-local text and one uncommitted native textarea; the parent owns history and storage. */
export function DocumentTextLayer({ annotations, width, height, scale, color, fontSize, lineWidth,
  active, panning, readOnly, onChange }: {
  annotations: readonly DocumentGestureDto[];
  width: number; height: number; scale: number; color: string; fontSize: number; lineWidth: number;
  active: boolean; panning: boolean; readOnly: boolean;
  onChange(next: readonly DocumentGestureDto[]): void;
}) {
  const t = useT();
  const layer = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const annotationsRef = useRef(annotations);
  annotationsRef.current = annotations;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editor, setEditorState] = useState<Editor | null>(null);
  const editorRef = useRef<Editor | null>(null);
  const composing = useRef(false);
  const drag = useRef<Drag | null>(null);
  const [dragPreview, setDragPreview] = useState<{ id: string; point: Point } | null>(null);
  const [panelPosition, setPanelPosition] = useState<PanelPosition>({ left: 8, top: 8, width: 280, maxHeight: 220 });
  const styleRef = useRef({ color, fontSize });
  const interactive = active && !readOnly;
  const selected = annotations.find((mark): mark is TextMark => mark.id === selectedId && isText(mark)) ?? null;

  const setEditor = (next: Editor | null) => {
    editorRef.current = next;
    setEditorState(next);
    if (next === null) composing.current = false;
  };
  const cancelDrag = () => {
    const current = drag.current;
    drag.current = null; setDragPreview(null);
    if (current?.node.hasPointerCapture?.(current.pointerId)) current.node.releasePointerCapture(current.pointerId);
  };
  const focusViewport = () => {
    const viewport = layer.current?.closest<HTMLElement>(".document-viewport");
    if (interactive && viewport && !viewport.closest("[inert]") && viewport.getClientRects().length > 0 &&
      getComputedStyle(viewport).visibility !== "hidden") viewport.focus({ preventScroll: true });
  };
  const cancelEditor = (restoreFocus = false) => {
    const hadEditor = editorRef.current !== null;
    setEditor(null);
    if (hadEditor && restoreFocus) focusViewport();
  };
  const commitEditor = (restoreFocus = true) => {
    const current = editorRef.current;
    if (!current || composing.current) return;
    // Clear synchronously so a repeated shortcut/click cannot submit twice.
    setEditor(null);
    if (restoreFocus) focusViewport();
    const old = annotationsRef.current.find((mark) => mark.id === current.mark.id);
    if (!current.mark.label.trim()) {
      if (current.existing && old) onChange(annotationsRef.current.filter((mark) => mark.id !== current.mark.id));
      setSelectedId(null);
      return;
    }
    if (current.existing) {
      if (!old) { setSelectedId(null); return; }
      if (old.label !== current.mark.label || old.color !== current.mark.color || old.fontSize !== current.mark.fontSize) {
        onChange(annotationsRef.current.map((mark) => mark.id === current.mark.id ? current.mark : mark));
      }
    } else onChange([...annotationsRef.current, current.mark]);
    setSelectedId(current.mark.id);
  };
  const edit = (mark: TextMark) => {
    cancelDrag(); setSelectedId(mark.id);
    setEditor({ mark: { ...mark, points: [[...mark.points[0]]] }, existing: true });
  };
  const remove = (id: string) => {
    cancelDrag(); setEditor(null); setSelectedId(null);
    focusViewport();
    const next = annotationsRef.current.filter((mark) => mark.id !== id);
    if (next.length !== annotationsRef.current.length) onChange(next);
  };

  useEffect(() => {
    if (!interactive) { cancelDrag(); cancelEditor(); setSelectedId(null); }
    else if (panning) cancelDrag();
  }, [interactive, panning]);
  useEffect(() => {
    if (selectedId && !annotations.some((mark) => mark.id === selectedId) && editorRef.current?.mark.id !== selectedId) setSelectedId(null);
    if (editorRef.current?.existing && !annotations.some((mark) => mark.id === editorRef.current!.mark.id)) cancelEditor();
  }, [annotations, selectedId]);
  useEffect(() => {
    const previous = styleRef.current;
    styleRef.current = { color, fontSize };
    // Opening an existing mark preserves its style. A subsequent toolbar change
    // updates this draft, and is committed with the text in one parent action.
    if (editorRef.current && (previous.color !== color || previous.fontSize !== fontSize)) {
      setEditor({ ...editorRef.current, mark: { ...editorRef.current.mark,
        ...(previous.color !== color ? { color } : {}), ...(previous.fontSize !== fontSize ? { fontSize } : {}),
      } });
    }
  }, [color, fontSize]);
  useLayoutEffect(() => {
    if (!editor) return;
    textarea.current?.focus({ preventScroll: true });
    textarea.current?.setSelectionRange(editor.mark.label.length, editor.mark.label.length);
  }, [editor?.mark.id]);

  const panelMark = editor?.mark ?? selected;
  const positionPanel = () => {
    const node = layer.current;
    if (!node || !panelMark) return;
    const pageRect = node.getBoundingClientRect();
    const viewport = node.closest(".document-viewport")?.getBoundingClientRect() ?? pageRect;
    const left = Math.max(8, viewport.left + 8), top = Math.max(8, viewport.top + 8);
    const right = Math.min(window.innerWidth - 8, viewport.right - 8), bottom = Math.min(window.innerHeight - 8, viewport.bottom - 8);
    const panelWidth = Math.max(120, Math.min(editor ? 320 : 180, right - left));
    const maxHeight = Math.max(80, bottom - top);
    const panelHeight = Math.min(panel.current?.getBoundingClientRect().height ?? (editor ? 190 : 38), maxHeight);
    const point = dragPreview?.id === panelMark.id ? dragPreview.point : panelMark.points[0];
    const anchorY = pageRect.top + point[1] * pageRect.height;
    let preferredTop = anchorY + 10;
    if (!editor) {
      const textRect = Array.from(node.querySelectorAll<HTMLElement>(".document-text-mark"))
        .find((mark) => mark.dataset.textId === panelMark.id)?.getBoundingClientRect();
      const above = (textRect?.top ?? anchorY) - panelHeight - 8;
      const textBottom = textRect?.bottom ?? anchorY + panelMark.fontSize * Math.min(width, height) * scale * 1.25 * panelMark.label.split("\n").length;
      preferredTop = above >= top ? above : textBottom + 8;
    }
    const next = {
      left: Math.max(left, Math.min(right - panelWidth, pageRect.left + point[0] * pageRect.width)),
      top: Math.max(top, Math.min(bottom - panelHeight, preferredTop)),
      width: panelWidth, maxHeight,
    };
    setPanelPosition((previous) => Object.keys(next).every((key) => Math.abs(next[key as keyof PanelPosition] - previous[key as keyof PanelPosition]) < 0.5) ? previous : next);
  };
  // Parent pan/zoom can change the page's rect without changing its dimensions.
  useLayoutEffect(positionPanel);
  useEffect(() => {
    if (!panelMark) return;
    window.addEventListener("resize", positionPanel);
    window.addEventListener("scroll", positionPanel, true);
    return () => { window.removeEventListener("resize", positionPanel); window.removeEventListener("scroll", positionPanel, true); };
  }, [panelMark, dragPreview, editor, scale]);

  const moveDrag = (event: PointerEvent) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId || !layer.current) return;
    const rect = layer.current.getBoundingClientRect();
    const dx = event.clientX - current.start[0], dy = event.clientY - current.start[1];
    if (Math.hypot(dx, dy) >= 3) current.moved = true;
    current.point = [clamp(current.mark.points[0][0] + dx / rect.width), clamp(current.mark.points[0][1] + dy / rect.height)];
    if (current.moved) setDragPreview({ id: current.mark.id, point: current.point });
  };
  const finishDrag = (event: PointerEvent) => {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    moveDrag(event);
    const point = current.point;
    const changed = current.moved && (point[0] !== current.mark.points[0][0] || point[1] !== current.mark.points[0][1]);
    cancelDrag();
    if (changed) onChange(annotationsRef.current.map((mark) => mark.id === current.mark.id ? { ...mark, points: [point] } : mark));
  };
  const dragHandlers = useRef({ move: moveDrag, up: finishDrag, cancel: cancelDrag });
  dragHandlers.current = { move: moveDrag, up: finishDrag, cancel: cancelDrag };
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId || current.node.hasPointerCapture?.(event.pointerId) ||
        (event.target instanceof Node && current.node.contains(event.target))) return;
      if (event.type === "pointerup") dragHandlers.current.up(event);
      else if (event.type === "pointercancel") dragHandlers.current.cancel();
      else dragHandlers.current.move(event);
    };
    const blur = () => dragHandlers.current.cancel();
    window.addEventListener("pointermove", outside); window.addEventListener("pointerup", outside);
    window.addEventListener("pointercancel", outside); window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("pointermove", outside); window.removeEventListener("pointerup", outside);
      window.removeEventListener("pointercancel", outside); window.removeEventListener("blur", blur);
      drag.current = null;
    };
  }, []);

  const select = (event: ReactPointerEvent<HTMLDivElement>, mark: TextMark) => {
    if (!interactive || panning || event.button !== 0) return;
    event.stopPropagation(); event.preventDefault();
    if (editorRef.current) {
      if (editorRef.current.mark.id === mark.id) { textarea.current?.focus(); return; }
      commitEditor(false);
    }
    event.currentTarget.focus({ preventScroll: true }); setSelectedId(mark.id);
    drag.current = { pointerId: event.pointerId, node: event.currentTarget, mark,
      start: [event.clientX, event.clientY], point: mark.points[0], moved: false };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const create = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive || panning || event.button !== 0 || !layer.current) return;
    event.stopPropagation(); event.preventDefault();
    if (editorRef.current) commitEditor(false);
    if (composing.current) return;
    const rect = layer.current.getBoundingClientRect();
    const mark: TextMark = { id: crypto.randomUUID(), kind: "text", label: "", color, fontSize, lineWidth,
      points: [[clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)]] };
    setSelectedId(null); setEditor({ mark, existing: false });
  };
  const marks = annotations.filter(isText).map((mark) => editor?.existing && editor.mark.id === mark.id ? editor.mark : mark);
  if (editor && !editor.existing) marks.push(editor.mark);

  return <div ref={layer} className="document-text-layer" data-document-text="true"
    data-text-active={interactive && !panning} style={{ pointerEvents: interactive && !panning ? "auto" : "none" }}
    onPointerDown={create}>
    <div className="document-text-display">
      {marks.map((mark) => {
        const point = dragPreview?.id === mark.id ? dragPreview.point : mark.points[0];
        const editing = editor?.mark.id === mark.id;
        return <div key={mark.id} className="document-text-mark" data-text-id={mark.id}
          data-selected={interactive && (selectedId === mark.id || editing)} data-text-draft={editing || undefined}
          tabIndex={interactive && !editing && !panning ? 0 : -1}
          aria-label={`${t("document.text.selected")}: ${mark.label}`}
          style={{ left: point[0] * width * scale, top: point[1] * height * scale,
            fontSize: mark.fontSize * Math.min(width, height) * scale, color: mark.color,
            pointerEvents: interactive && !panning ? "auto" : "none" }}
          onPointerDown={(event) => select(event, mark)}
          onPointerMove={(event) => { if (drag.current?.pointerId === event.pointerId) { event.stopPropagation(); moveDrag(event.nativeEvent); } }}
          onPointerUp={(event) => { if (drag.current?.pointerId === event.pointerId) { event.stopPropagation(); finishDrag(event.nativeEvent); } }}
          onPointerCancel={() => cancelDrag()} onLostPointerCapture={() => { if (drag.current) cancelDrag(); }}
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={(event) => { if (interactive && !panning) { event.stopPropagation(); edit(mark); } }}
          onFocus={() => { if (interactive) setSelectedId(mark.id); }}
          onKeyDown={(event) => {
            if (!interactive || editorRef.current || panning) return;
            if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); event.stopPropagation(); remove(mark.id); }
            else if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); edit(mark); }
            else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancelDrag(); setSelectedId(null); }
          }}>{mark.label}</div>;
      })}
    </div>
    {interactive && !panning && panelMark && createPortal(
      <div ref={panel} className={editor ? "document-text-editor" : "document-text-controls"} data-document-text="true"
        style={panelPosition} role="group" aria-label={t("document.text.edit")}
        onPointerDown={(event) => { if (event.button === 0) event.stopPropagation(); }}
        onClick={(event) => event.stopPropagation()} onDoubleClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && ["z", "y"].includes(event.key.toLowerCase()) &&
            !event.nativeEvent.isComposing && event.keyCode !== 229) return;
          event.stopPropagation();
        }}>
        {editor ? <>
          <textarea ref={textarea} aria-label={t("document.text.placeholder")} placeholder={t("document.text.placeholder")}
            value={editor.mark.label} maxLength={2000} wrap="off" spellCheck
            style={{ color: editor.mark.color, fontSize: Math.max(14, editor.mark.fontSize * Math.min(width, height) * scale) }}
            onChange={(event) => { if (editorRef.current) setEditor({ ...editorRef.current, mark: { ...editorRef.current.mark, label: event.target.value } }); }}
            onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={(event) => {
              event.stopPropagation();
              if (event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return;
              if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); commitEditor(); }
              else if (event.key === "Escape") { event.preventDefault(); cancelEditor(true); }
            }} />
          <div className="document-text-editor__actions">
            <span>Ctrl+Enter</span>
            <button type="button" onClick={() => cancelEditor(true)}>{t("document.text.cancel")}</button>
            <button type="button" onClick={() => commitEditor()}>{t("document.text.finish")}</button>
          </div>
        </> : <>
          <button type="button" onClick={() => selected && edit(selected)}>{t("document.text.edit")}</button>
          <button type="button" onClick={() => selected && remove(selected.id)}>{t("document.text.delete")}</button>
        </>}
      </div>, layer.current?.closest(".document-workspace") ?? document.body,
    )}
  </div>;
}
