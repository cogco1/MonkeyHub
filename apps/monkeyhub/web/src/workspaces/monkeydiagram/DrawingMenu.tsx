import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import "./DrawingMenu.css";

/**
 * #337: a word in the Drawing's bar whose panel opens under it (Draw another version, Reuse drawing recipes, More). The
 * panel closes on Escape, and when a pointer goes down or focus lands outside it; a control inside it turning disabled
 * while it loads, or a file dialog it opened, leaves it open. Its owner closes it once its result shows elsewhere, and
 * focus then returns to the word.
 */
export function DrawingMenu({ label, className, panelClassName, open, onOpenChange, children }: {
  label: string; className: string; panelClassName: string; open: boolean; onOpenChange(open: boolean): void; children: ReactNode;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const change = useRef(onOpenChange);
  change.current = onOpenChange;
  // Closed because attention went elsewhere: focus stays where it went.
  const left = useRef(false);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      if (event.target instanceof Node && menu.current?.contains(event.target)) return;
      left.current = true; change.current(false);
    };
    // Escape also closes it while focus is nowhere, as after a command in it turned disabled while it ran.
    const escape = (event: KeyboardEvent) => {
      const focus = document.activeElement;
      if (event.key !== "Escape" || event.defaultPrevented || (focus && focus !== document.body && !menu.current?.contains(focus))) return;
      event.preventDefault(); change.current(false); menu.current?.querySelector("summary")?.focus();
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("focusin", outside, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true); document.removeEventListener("focusin", outside, true);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  useLayoutEffect(() => {
    const summary = menu.current?.querySelector("summary");
    if (!open && !left.current && summary && menu.current?.contains(document.activeElement) && document.activeElement !== summary) summary.focus();
    left.current = false;
  }, [open]);
  // The word opens and closes the menu through its owner, so what is on screen never runs ahead of it; a toggle from
  // elsewhere, such as find in page, is followed.
  return <details ref={menu} className={`drawing-menu ${className}`} open={open}
    onToggle={event => { if (event.currentTarget.open !== open) onOpenChange(event.currentTarget.open); }}
    onKeyDown={event => {
      if (event.key !== "Escape" || !open) return;
      event.preventDefault(); event.stopPropagation(); onOpenChange(false); menu.current?.querySelector("summary")?.focus();
    }}>
    <summary className="menu-command" onClick={event => { event.preventDefault(); onOpenChange(!open); }}>{label}</summary>
    <div className={`drawing-menu__panel ${panelClassName}`}>{children}</div>
  </details>;
}
