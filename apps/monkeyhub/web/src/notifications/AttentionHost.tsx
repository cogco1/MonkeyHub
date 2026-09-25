/**
 * The Hub's notices (#300, UX review FN-2), mounted once per window by main.tsx.
 *
 * At most three, bottom-right, clear of the rail and the composer. A waiting
 * permission stays until it is opened, dismissed or answered; the others fade
 * after about eight seconds on screen. One polite live region reads them out.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type FocusEvent } from "react";
import type { Language } from "../../../../shared-web/src/appearance.js";
import { attentionCopy, clearance, GAP, noticeText, type AttentionEvent, type AttentionWords, type Box } from "./attention";
import { useAttention } from "./useAttention";
import "./attention.css";

/** How long a notice that only informs stays on screen, and how long it takes to leave. */
const FADE_MS = 8000;
const LEAVE_MS = 200;

/** One host per window: a Hub page framed inside another (a tool) leaves notices to the page around it. */
function framed(): boolean {
  try { return window.self !== window.top; } catch { return true; }
}

export function AttentionHost({ language }: { language: Language }) {
  return framed() ? null : <AttentionStack language={language} />;
}

function AttentionStack({ language }: { language: Language }) {
  const { notices, visible, open, dismiss } = useAttention(language);
  const words = attentionCopy[language];
  const place = useClearance(notices.length > 0);
  const style = { "--attention-right": `${place.right}px`, "--attention-bottom": `${place.bottom}px` } as CSSProperties;
  return <section className="attention-toasts" aria-label={words.region} aria-live="polite" aria-relevant="additions" style={style}>
    {notices.map((notice) => <Notice key={notice.key} notice={notice} words={words} visible={visible} onOpen={open} onDismiss={dismiss} />)}
  </section>;
}

const reducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function Notice({ notice, words, visible, onOpen, onDismiss }: {
  notice: AttentionEvent; words: AttentionWords; visible: boolean;
  onOpen: (notice: AttentionEvent) => void; onDismiss: (notice: AttentionEvent) => void;
}) {
  // Pointer or keyboard focus on a notice holds its fade.
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const lasts = notice.kind === "permission";
  useEffect(() => {
    if (lasts || !visible || hovered || focused || leaving) return;
    const timer = window.setTimeout(() => setLeaving(true), FADE_MS);
    return () => window.clearTimeout(timer);
  }, [lasts, visible, hovered, focused, leaving]);
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => dismissRef.current(notice), reducedMotion() ? 0 : LEAVE_MS);
    return () => window.clearTimeout(timer);
  }, [leaving, notice]);
  const blur = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false);
  };
  // Each button keeps its short visible name and is described by the sentence it acts on.
  const sentence = useId();
  return <div className="attention-toast" data-kind={notice.kind} data-leaving={leaving || undefined}
    onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} onFocus={() => setFocused(true)} onBlur={blur}
    onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); onDismiss(notice); } }}>
    <span className="attention-toast__mark" aria-hidden="true" />
    <p className="attention-toast__text">
      <span id={sentence}>{noticeText(notice, words)}</span>
      <span className="attention-toast__dot" aria-hidden="true">·</span>
      <button type="button" className="attention-toast__view" aria-describedby={sentence} onClick={() => onOpen(notice)}>{words.view}</button>
    </p>
    <button type="button" className="attention-toast__close" aria-label={words.dismiss} aria-describedby={sentence} title={words.dismiss}
      onClick={() => onDismiss(notice)}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" aria-hidden="true"><path d="m7 7 10 10M7 17 17 7" /></svg>
    </button>
  </div>;
}

const box = (element: Element | null): Box | null => {
  if (!element) return null;
  const { left, top, right, bottom } = element.getBoundingClientRect();
  return { left, top, right, bottom };
};

/**
 * Where the stack sits while it shows anything: measured from the Hub shell's
 * rail and composer when this window has them, and again whenever either
 * changes size (the composer grows while typing) or the window does.
 */
function useClearance(active: boolean): { right: number; bottom: number } {
  const [place, setPlace] = useState({ right: GAP, bottom: GAP });
  useLayoutEffect(() => {
    if (!active) return;
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => measure()) : null;
    const watched = new Set<Element>();
    function measure() {
      // Read-only anchors in ChatShell; without them the stack keeps to the corner.
      const rail = document.querySelector(".chat-rail"), composer = document.querySelector(".chat-composer-wrap, .chat-composer");
      for (const element of [rail, composer]) {
        if (element && !watched.has(element)) { watched.add(element); observer?.observe(element); }
      }
      const root = document.documentElement;
      const next = clearance({ width: root.clientWidth, height: root.clientHeight }, box(rail), box(composer));
      setPlace((current) => current.right === next.right && current.bottom === next.bottom ? current : next);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
  }, [active]);
  return place;
}
