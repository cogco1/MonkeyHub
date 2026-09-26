import { useLayoutEffect, useSyncExternalStore, type ReactNode } from "react";

/**
 * #354: the desktop host's merged title row (Windows 11 only).
 *
 * The desktop host defines `window.__monkeyhubDesktop` before any page script runs, and
 * only in its main window. A browser and the host's popup windows never have it, so they
 * keep today's layout. The host owns a native child window over the three window buttons:
 * it gets their clicks (and offers Snap Layouts on maximize), and it reports hover, press,
 * maximized and active state back through the bridge. This row only draws them. The
 * geometry comes from the host, and the row uses fixed px, so the drawing cannot drift
 * from the native hit rectangle.
 */
export type DesktopCaptionButton = "minimize" | "maximize" | "close";
export type DesktopShellState = {
  readonly titleBar: boolean;
  readonly maximized: boolean;
  readonly active: boolean;
  readonly hover: DesktopCaptionButton | null;
  readonly pressed: DesktopCaptionButton | null;
  readonly hostStatus: "recovering" | null;
  /** Counts presses on the native caption (drag area); the page never sees them itself. */
  readonly captionPress: number;
};
type DesktopShell = {
  readonly version: number;
  readonly titleBarHeight: number;
  readonly captionButtonWidth: number;
  readonly captionButtons: number;
  readonly state: DesktopShellState;
  subscribe(listener: (state: DesktopShellState) => void): () => void;
};

declare global {
  interface Window { __monkeyhubDesktop?: DesktopShell }
}

const candidate = typeof window === "undefined" ? undefined : window.__monkeyhubDesktop;
const shell: DesktopShell | null = candidate && candidate.version === 1 && typeof candidate.subscribe === "function"
  && Number.isFinite(candidate.titleBarHeight) && Number.isFinite(candidate.captionButtonWidth)
  && Number.isFinite(candidate.captionButtons) ? candidate : null;
const subscribe = (listener: () => void) => shell ? shell.subscribe(listener) : () => undefined;
const snapshot = () => shell?.state ?? null;
const titleBarOn = () => Boolean(shell?.state.titleBar);
const hostStatusOf = () => shell?.state.hostStatus ?? null;
const captionPressOf = () => shell?.state.captionPress ?? 0;

/**
 * What the page around the row needs: whether the row is in use, the host's own status and
 * the caption-press counter that closes an open menu. Each part is a primitive, so hover,
 * press and focus changes re-render only the row, never its caller. Null in a browser, a
 * popup, or once the system title bar is back.
 */
export function useDesktopTitleBar(): { readonly hostStatus: DesktopShellState["hostStatus"]; readonly captionPress: number } | null {
  const on = useSyncExternalStore(subscribe, titleBarOn, () => false);
  const hostStatus = useSyncExternalStore(subscribe, hostStatusOf, () => null);
  const captionPress = useSyncExternalStore(subscribe, captionPressOf, () => 0);
  return on ? { hostStatus, captionPress } : null;
}

/** The app mark from the approved #337 proposal. */
function Mark() {
  return <svg viewBox="0 0 20 20" aria-hidden="true">
    <rect x="0.5" y="0.5" width="19" height="19" rx="4.5" fill="#12263f" stroke="#2e4a6e" />
    <path d="M5.5 17V9.5a4.5 4.5 0 0 1 9 0V17" fill="none" stroke="#f4efe6" strokeWidth="2.4" />
    <path d="M8.3 2.6h3.4l-0.6 4H8.9z" fill="#e8a33d" />
  </svg>;
}

const GLYPHS: Record<DesktopCaptionButton | "restore", ReactNode> = {
  minimize: <path d="M5 12h14" />,
  maximize: <path d="M6 6h12v12H6z" />,
  restore: <><path d="M8 8h10v10H8z" /><path d="M6 15V6h9" /></>,
  close: <path d="M6 6l12 12M18 6L6 18" />,
};

/**
 * One row: the app mark, the Hub's own menu row, a centred title between two drag areas,
 * and the pictures of the three window buttons. Only the mark, the title and the empty
 * fills carry `app-region: drag`, and none of them contains anything interactive.
 */
export function DesktopTitleBar({ menus, title }: { menus: ReactNode; title: string }) {
  // The row alone follows hover, press, maximized and active; `menus` is the caller's
  // element, so these re-renders do not reach the menu bar.
  const state = useSyncExternalStore(subscribe, snapshot, () => null);
  useLayoutEffect(() => {
    if (!shell) return;
    const root = document.documentElement;
    root.setAttribute("data-desktop-titlebar", "");
    root.style.setProperty("--desktop-titlebar-height", `${shell.titleBarHeight}px`);
    root.style.setProperty("--desktop-caption-button-width", `${shell.captionButtonWidth}px`);
    root.style.setProperty("--desktop-caption-width", `${shell.captionButtonWidth * shell.captionButtons}px`);
    return () => {
      root.removeAttribute("data-desktop-titlebar");
      for (const name of ["--desktop-titlebar-height", "--desktop-caption-button-width", "--desktop-caption-width"]) root.style.removeProperty(name);
    };
  }, []);
  if (!state) return null;
  return <div className="chat-menubar chat-titlebar" data-active={state.active} data-maximized={state.maximized}>
    <span className="chat-titlebar__mark" aria-hidden="true"><Mark /></span>
    {menus}
    <span className="chat-titlebar__fill" aria-hidden="true" />
    <span className="chat-titlebar__title">{title}</span>
    <span className="chat-titlebar__fill" aria-hidden="true" />
    {/* Pictures only: the host's caption child sits on top and owns these clicks. */}
    <span className="chat-titlebar__caption" aria-hidden="true">
      {(["minimize", "maximize", "close"] as const).map((button) => <span key={button} className="chat-titlebar__button"
        data-button={button} data-hover={state.hover === button} data-pressed={state.pressed === button}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {GLYPHS[button === "maximize" && state.maximized ? "restore" : button]}
        </svg>
      </span>)}
    </span>
  </div>;
}
